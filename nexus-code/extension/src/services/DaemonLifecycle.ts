import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import { getSettings } from './ExtensionConfig';

export class DaemonLifecycle {
  private childProcess?: cp.ChildProcess;
  private outputChannel: vscode.OutputChannel;
  private healthCheckInterval?: NodeJS.Timeout;
  private restartCount = 0;
  private isShuttingDown = false;
  
  private daemonPort?: number;
  private daemonSecret?: string;

  constructor(private readonly extensionUri: vscode.Uri) {
    this.outputChannel = vscode.window.createOutputChannel('Nexus-Code Daemon');
  }

  public getPort(): number | undefined {
      return this.daemonPort;
  }

  public getSecret(): string | undefined {
      return this.daemonSecret;
  }

  private getLockfilePath(): string {
      return path.join(os.homedir(), '.nexus-code', 'daemon.lock');
  }

  private readLockfile(): { pid: number, port: number, secret: string } | null {
      try {
          const lockfilePath = this.getLockfilePath();
          if (fs.existsSync(lockfilePath)) {
              const content = fs.readFileSync(lockfilePath, 'utf8');
              return JSON.parse(content);
          }
      } catch (e) {
          // ignore parsing error or permission error
      }
      return null;
  }

    public async startDaemon(): Promise<void> {
    this.isShuttingDown = false;

    // 1. Check lockfile to reuse an existing running daemon
    const existingDaemon = this.readLockfile();
    if (existingDaemon) {
        if (await this.checkHealth(existingDaemon.port, existingDaemon.secret)) {
            this.outputChannel.appendLine(`Reconnected to existing daemon on port ${existingDaemon.port}.`);
            this.daemonPort = existingDaemon.port;
            this.daemonSecret = existingDaemon.secret;
            this.startHealthCheckLoop();
            return;
        } else {
            this.outputChannel.appendLine('Found stale lockfile or unresponsive daemon. Cleaning up...');
            try {
                fs.unlinkSync(this.getLockfilePath());
            } catch (e) {}
        }
    }

    this.outputChannel.appendLine(`Starting new daemon...`);

    // 3. Resolve python executable & Daemon Dir
    let daemonDir = path.join(this.extensionUri.fsPath, 'daemon');
    if (!fs.existsSync(daemonDir)) {
        // Fallback for local development (F5)
        daemonDir = path.join(this.extensionUri.fsPath, '..', 'daemon');
    }

    const isWin = process.platform === 'win32';
    const venvDir = path.join(daemonDir, 'venv');
    const venvPython = path.join(venvDir, isWin ? 'Scripts' : 'bin', isWin ? 'python.exe' : 'python');

    if (!fs.existsSync(venvDir)) {
        this.outputChannel.appendLine(`Creating Python virtual environment in ${venvDir}...`);
        try {
            cp.execSync(`python3 -m venv venv`, { cwd: daemonDir });
            this.outputChannel.appendLine(`Installing requirements...`);
            cp.execSync(`${venvPython} -m pip install -r requirements.txt`, { cwd: daemonDir });
        } catch (e: any) {
            this.outputChannel.appendLine(`Failed to setup venv: ${e.message}`);
            vscode.window.showErrorMessage("Failed to setup Python virtual environment for Nexus-Code Daemon.");
            throw e;
        }
    }

    const pythonExec = venvPython;
    
    try {
        cp.execSync(`${pythonExec} --version`);
    } catch (e) {
        vscode.window.showErrorMessage(
            'Python not found or venv is broken. Nexus-Code requires Python to run its daemon.',
            'View Logs'
        ).then(selection => {
            if (selection === 'View Logs') {
                this.outputChannel.show();
            }
        });
        throw new Error('Python executable not found.');
    }

    // 4. Spawn child process to run main.py which generates lockfile
    this.childProcess = cp.spawn(pythonExec, ['-m', 'app.main'], {
      cwd: daemonDir,
      env: process.env
    });

    let stderrBuffer = '';

    // 5. Pipe stdout/stderr
    this.childProcess.stdout?.on('data', (data) => {
      this.outputChannel.append(data.toString());
    });

    this.childProcess.stderr?.on('data', (data) => {
      const output = data.toString();
      this.outputChannel.append(output);
      stderrBuffer += output;
      
      if (output.includes('error while attempting to bind on address') && output.includes('address already in use')) {
          vscode.window.showErrorMessage(
              `Daemon port binding error.`,
              'View Logs'
          ).then(selection => {
              if (selection === 'View Logs') {
                  this.outputChannel.show();
              }
          });
      }
    });

    this.childProcess.on('exit', (code, signal) => {
      this.outputChannel.appendLine(`Daemon exited with code ${code} and signal ${signal}`);
      if (!this.isShuttingDown) {
        this.handleCrash();
      }
    });

    // Wait for the lockfile to be created by the new process (up to 5 seconds)
    const lockfileCreated = await this.waitForLockfile(5000);
    if (!lockfileCreated) {
        throw new Error(`Daemon failed to create lockfile. Stderr snippet: ${stderrBuffer.substring(0, 500)}`);
    }

    const newDaemon = this.readLockfile();
    if (!newDaemon) {
        throw new Error('Daemon created lockfile but failed to read it.');
    }

    this.daemonPort = newDaemon.port;
    this.daemonSecret = newDaemon.secret;

    // 6. Poll health for up to 30 seconds
    const success = await this.waitForHealth(this.daemonPort, this.daemonSecret, 30000);
    if (success) {
      this.outputChannel.appendLine(`Daemon started successfully on port ${this.daemonPort}.`);
      this.restartCount = 0; // reset on success
      this.startHealthCheckLoop();
    } else {
      vscode.window.showErrorMessage(
          'Daemon failed to pass health check within 30 seconds.',
          'View Logs'
      ).then(selection => {
          if (selection === 'View Logs') {
              this.outputChannel.show();
          }
      });
      throw new Error(`Daemon failed to start. Stderr snippet: ${stderrBuffer.substring(0, 500)}`);
    }
  }

  public async stopDaemon(): Promise<void> {
    this.isShuttingDown = true;
    this.stopHealthCheckLoop();

    if (this.childProcess && this.childProcess.pid) {
      this.outputChannel.appendLine('Stopping daemon...');
      this.childProcess.kill('SIGTERM');
      
      for (let i = 0; i < 10; i++) {
        if (this.childProcess.killed) break;
        await new Promise(r => setTimeout(r, 500));
      }
      
      if (!this.childProcess.killed) {
        this.outputChannel.appendLine('Daemon did not stop gracefully, sending SIGKILL...');
        this.childProcess.kill('SIGKILL');
      }
    }
    
    // Clean up the lockfile
    try {
        fs.unlinkSync(this.getLockfilePath());
    } catch (e) {}

    this.childProcess = undefined;
    this.daemonPort = undefined;
    this.daemonSecret = undefined;
  }



  private async checkHealth(port: number, secret: string): Promise<boolean> {
    try {
      const response = await fetch(`http://localhost:${port}/health`, {
        method: 'GET',
        headers: {
            'X-Nexus-Secret': secret
        }
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  private async waitForLockfile(timeoutMs: number): Promise<boolean> {
      const start = Date.now();
      while (Date.now() - start < timeoutMs) {
          if (this.readLockfile() !== null) {
              return true;
          }
          await new Promise(r => setTimeout(r, 200));
      }
      return false;
  }

  private async waitForHealth(port: number, secret: string, timeoutMs: number): Promise<boolean> {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await this.checkHealth(port, secret)) {
        return true;
      }
      await new Promise(r => setTimeout(r, 500));
    }
    return false;
  }

  private startHealthCheckLoop() {
    this.stopHealthCheckLoop();
    this.healthCheckInterval = setInterval(async () => {
      if (this.isShuttingDown || !this.daemonPort || !this.daemonSecret) return;
      const healthy = await this.checkHealth(this.daemonPort, this.daemonSecret);
      if (!healthy) {
        this.outputChannel.appendLine('Health check failed. Initiating restart...');
        this.handleCrash();
      }
    }, 30000); // 30 seconds
  }

  private stopHealthCheckLoop() {
    if (this.healthCheckInterval) {
      clearInterval(this.healthCheckInterval);
      this.healthCheckInterval = undefined;
    }
  }

  private async handleCrash() {
    this.stopHealthCheckLoop();
    const settings = getSettings();
    if (this.restartCount >= settings.daemonMaxRestarts) {
      vscode.window.showErrorMessage('Nexus-Code Daemon crashed repeatedly and will not restart.');
      return;
    }

    const backoff = Math.min(Math.pow(2, this.restartCount) * 1000, 30000);
    this.restartCount++;
    this.outputChannel.appendLine(`Restarting daemon in ${backoff}ms (attempt ${this.restartCount} of ${settings.daemonMaxRestarts})...`);

    setTimeout(() => {
      this.startDaemon().catch(e => {
        this.outputChannel.appendLine(`Restart failed: ${e}`);
        this.handleCrash();
      });
    }, backoff);
  }
}
