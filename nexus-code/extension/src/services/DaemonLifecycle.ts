import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import { getSettings } from './ExtensionConfig';

export class DaemonLifecycle {
  private childProcess?: cp.ChildProcess;
  private outputChannel: vscode.OutputChannel;
  private healthCheckInterval?: NodeJS.Timeout;
  private restartCount = 0;
  private isShuttingDown = false;

  constructor(private readonly extensionUri: vscode.Uri) {
    this.outputChannel = vscode.window.createOutputChannel('Nexus-Code Daemon');
  }

    public async startDaemon(): Promise<void> {
    this.isShuttingDown = false;
    const settings = getSettings();
    const port = settings.daemonPort;

    // 1. Check if already running
    if (await this.checkHealth(port)) {
      this.outputChannel.appendLine(`Daemon already running on port ${port}.`);
      this.startHealthCheckLoop();
      return;
    }

    this.outputChannel.appendLine(`Starting daemon on port ${port}...`);

    // 3. Resolve python executable
    const pythonExec = this.resolvePythonExecutable();
    
    // Check if python is actually available
    try {
        cp.execSync(`${pythonExec} --version`);
    } catch (e) {
        vscode.window.showErrorMessage(
            'Python not found. Nexus-Code requires Python to run its daemon.',
            'Download Python', 'Configure Path'
        ).then(selection => {
            if (selection === 'Download Python') {
                vscode.env.openExternal(vscode.Uri.parse('https://www.python.org/downloads/'));
            } else if (selection === 'Configure Path') {
                vscode.commands.executeCommand('workbench.action.openSettings', 'nexus-code.pythonPath');
            }
        });
        throw new Error('Python executable not found.');
    }

    // 4. Spawn child process
    const daemonDir = path.join(this.extensionUri.fsPath, '..', 'daemon');
    this.childProcess = cp.spawn(pythonExec, ['-m', 'uvicorn', 'app.main:app', '--port', port.toString(), '--host', '127.0.0.1'], {
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
      
      // Port in use check
      if (output.includes('error while attempting to bind on address') && output.includes('address already in use')) {
          vscode.window.showErrorMessage(
              `Port ${port} is already in use.`,
              'Change Port'
          ).then(selection => {
              if (selection === 'Change Port') {
                  vscode.commands.executeCommand('workbench.action.openSettings', 'nexus-code.daemonPort');
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

    // 6. Poll health for up to 30 seconds
    const success = await this.waitForHealth(port, 30000);
    if (success) {
      this.outputChannel.appendLine('Daemon started successfully.');
      this.restartCount = 0; // reset on success
      this.startHealthCheckLoop();
    } else {
      vscode.window.showErrorMessage(
          'Daemon failed to start within 30 seconds.',
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
      
      // Wait up to 5 seconds
      for (let i = 0; i < 10; i++) {
        if (this.childProcess.killed) break;
        await new Promise(r => setTimeout(r, 500));
      }
      
      if (!this.childProcess.killed) {
        this.outputChannel.appendLine('Daemon did not stop gracefully, sending SIGKILL...');
        this.childProcess.kill('SIGKILL');
      }
    }
    this.childProcess = undefined;
  }

  private resolvePythonExecutable(): string {
    // In a real production extension, you'd check for a bundled python 
    // environment or extension setting. For Phase 2, we fallback to system `python`.
    // Actually we can check if `venv` exists.
    const venvPath = path.join(this.extensionUri.fsPath, '..', 'daemon', 'venv', 'Scripts', 'python.exe');
    // We won't block on checking fs here for simplicity, assume 'python' is on path if no venv logic is complex.
    return process.platform === 'win32' ? 'python' : 'python3';
  }

  private async checkHealth(port: number): Promise<boolean> {
    try {
      const response = await fetch(`http://localhost:${port}/health`, {
        method: 'GET',
        // signal could be used for timeout, but fetch API doesn't support it natively without AbortController
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  private async waitForHealth(port: number, timeoutMs: number): Promise<boolean> {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await this.checkHealth(port)) {
        return true;
      }
      await new Promise(r => setTimeout(r, 500));
    }
    return false;
  }

  private startHealthCheckLoop() {
    this.stopHealthCheckLoop();
    this.healthCheckInterval = setInterval(async () => {
      if (this.isShuttingDown) return;
      const port = getSettings().daemonPort;
      const healthy = await this.checkHealth(port);
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
