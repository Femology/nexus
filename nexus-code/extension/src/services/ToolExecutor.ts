import * as vscode from 'vscode';
import * as path from 'path';
import * as os from 'os';
import * as fs from 'fs';
import { ToolCall, ToolResult, DiagnosticSeverity } from '../types/contracts';
import { DiffContentProvider } from './DiffContentProvider';

export class ToolExecutor {
  private terminal?: vscode.Terminal;

  constructor(private readonly diffProvider: DiffContentProvider) {}

  public async executeTool(toolCall: ToolCall): Promise<ToolResult> {
    const outputChannel = vscode.window.createOutputChannel('Nexus-Code');
    outputChannel.appendLine(`Executing tool: ${toolCall.tool_name}`);

    try {
      switch (toolCall.tool_name) {
        case 'read_file':
          return await this.readFile(toolCall);
        case 'write_file':
          return await this.writeFile(toolCall);
        case 'create_file':
          return await this.createFile(toolCall);
        case 'run_terminal':
          return await this.runTerminal(toolCall);
        case 'list_directory':
          return await this.listDirectory(toolCall);
        case 'get_diagnostics':
          return await this.getDiagnostics(toolCall);
        case 'show_diff':
          return await this.showDiff(toolCall);
        default:
          throw new Error(`Unknown tool: ${toolCall.tool_name}`);
      }
    } catch (e: any) {
      outputChannel.appendLine(`Tool ${toolCall.tool_name} failed: ${e.message}`);
      return {
        tool_call_id: toolCall.id,
        tool_name: toolCall.tool_name,
        output: e.message || String(e),
        is_error: true
      };
    }
  }

  private validateWorkspacePath(targetPath: string): void {
    if (!vscode.workspace.workspaceFolders) {
      throw new Error('No open workspace folders');
    }
    const uri = vscode.Uri.file(targetPath);
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(uri);
    if (!workspaceFolder) {
      throw new Error(`Path is outside workspace root: ${targetPath}`);
    }
  }

  private async readFile(toolCall: ToolCall): Promise<ToolResult> {
    const filePath = toolCall.arguments.path as string;
    const uri = vscode.Uri.file(filePath);
    const contentBuffer = await vscode.workspace.fs.readFile(uri);
    const content = Buffer.from(contentBuffer).toString('utf-8');
    
    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: content,
      is_error: false
    };
  }

  private async writeFile(toolCall: ToolCall): Promise<ToolResult> {
    const filePath = toolCall.arguments.path as string;
    const content = toolCall.arguments.content as string;
    this.validateWorkspacePath(filePath);

    const uri = vscode.Uri.file(filePath);
    // Ensure file exists before writing
    try {
      await vscode.workspace.fs.stat(uri);
    } catch {
      throw new Error(`File does not exist: ${filePath}. Use create_file instead.`);
    }

    const edit = new vscode.WorkspaceEdit();
    const document = await vscode.workspace.openTextDocument(uri);
    const fullRange = new vscode.Range(
      document.positionAt(0),
      document.positionAt(document.getText().length)
    );
    edit.replace(uri, fullRange, content);
    
    const success = await vscode.workspace.applyEdit(edit);
    if (!success) {
      throw new Error(`Failed to apply edit to ${filePath}`);
    }
    
    await document.save();

    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: `Successfully wrote to ${filePath}`,
      is_error: false
    };
  }

  private async createFile(toolCall: ToolCall): Promise<ToolResult> {
    const filePath = toolCall.arguments.path as string;
    const content = toolCall.arguments.content as string;
    this.validateWorkspacePath(filePath);

    const uri = vscode.Uri.file(filePath);
    await vscode.workspace.fs.writeFile(uri, Buffer.from(content, 'utf-8'));
    
    const doc = await vscode.workspace.openTextDocument(uri);
    await vscode.window.showTextDocument(doc);

    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: `Successfully created file ${filePath}`,
      is_error: false
    };
  }

  private async runTerminal(toolCall: ToolCall): Promise<ToolResult> {
    const command = toolCall.arguments.command as string;
    const timeoutMs = (toolCall.arguments.timeout_ms as number) || 30000;

    if (!this.terminal || this.terminal.exitStatus) {
      this.terminal = vscode.window.createTerminal('Nexus-Code');
    }
    this.terminal.show(true);

    const tmpFile = path.join(os.tmpdir(), `nexus-term-${Date.now()}.txt`);
    const marker = `NEXUS_TERM_DONE_${Date.now()}`;
    const isWin = process.platform === 'win32';

    // Wrap the command to capture output and signal completion
    // Uses generic bash/powershell syntax. PowerShell handles parens and Tees nicely.
    const wrappedCmd = isWin
      ? `(& { ${command} } | Tee-Object -FilePath "${tmpFile}"); Write-Output "${marker}" >> "${tmpFile}"`
      : `(${command}) | tee "${tmpFile}"; echo "${marker}" >> "${tmpFile}"`;

    this.terminal.sendText(wrappedCmd);

    const start = Date.now();
    let output = '';
    
    while (Date.now() - start < timeoutMs) {
      if (fs.existsSync(tmpFile)) {
        output = fs.readFileSync(tmpFile, 'utf-8');
        if (output.includes(marker)) {
          // Finished
          output = output.replace(marker, '').trim();
          try { fs.unlinkSync(tmpFile); } catch (e) {}
          return {
            tool_call_id: toolCall.id,
            tool_name: toolCall.tool_name,
            output: output || 'Command completed successfully with no output.',
            is_error: false
          };
        }
      }
      await new Promise(r => setTimeout(r, 500));
    }

    // Timeout
    if (fs.existsSync(tmpFile)) {
       output = fs.readFileSync(tmpFile, 'utf-8').trim();
       try { fs.unlinkSync(tmpFile); } catch (e) {}
    }
    
    throw new Error(`Command timed out after ${timeoutMs}ms. Partial output:\n${output}`);
  }

  private async listDirectory(toolCall: ToolCall): Promise<ToolResult> {
    const dirPath = toolCall.arguments.path as string;
    const uri = vscode.Uri.file(dirPath);
    
    const entries = await vscode.workspace.fs.readDirectory(uri);
    const formatted = entries.map(([name, type]) => {
      const typeStr = type === vscode.FileType.Directory ? 'directory' : 'file';
      return `${typeStr}: ${name}`;
    }).join('\n');

    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: formatted || '(empty directory)',
      is_error: false
    };
  }

  private async getDiagnostics(toolCall: ToolCall): Promise<ToolResult> {
    const filePath = toolCall.arguments.path as string;
    const uri = vscode.Uri.file(filePath);
    const diags = vscode.languages.getDiagnostics(uri);
    
    if (diags.length === 0) {
       return {
         tool_call_id: toolCall.id,
         tool_name: toolCall.tool_name,
         output: 'No diagnostics found.',
         is_error: false
       };
    }

    const formatted = diags.map(d => ({
      message: d.message,
      severity: this.mapSeverity(d.severity),
      range: {
        start: { line: d.range.start.line, column: d.range.start.character },
        end: { line: d.range.end.line, column: d.range.end.character }
      },
      source: d.source || 'unknown'
    }));

    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: JSON.stringify(formatted, null, 2),
      is_error: false
    };
  }

  private mapSeverity(severity: vscode.DiagnosticSeverity): DiagnosticSeverity {
    switch (severity) {
      case vscode.DiagnosticSeverity.Error: return 'error';
      case vscode.DiagnosticSeverity.Warning: return 'warning';
      case vscode.DiagnosticSeverity.Information: return 'information';
      case vscode.DiagnosticSeverity.Hint: return 'hint';
      default: return 'information';
    }
  }

  private async showDiff(toolCall: ToolCall): Promise<ToolResult> {
    const originalContent = toolCall.arguments.original_content as string;
    const modifiedContent = toolCall.arguments.modified_content as string;
    const filePath = toolCall.arguments.file_path as string;
    
    const baseUri = vscode.Uri.file(filePath);
    const originalUri = baseUri.with({ scheme: DiffContentProvider.scheme, query: 'original' });
    const modifiedUri = baseUri.with({ scheme: DiffContentProvider.scheme, query: 'modified' });

    this.diffProvider.setContent(originalUri, originalContent);
    this.diffProvider.setContent(modifiedUri, modifiedContent);

    const title = `Diff: ${path.basename(filePath)}`;
    await vscode.commands.executeCommand('vscode.diff', originalUri, modifiedUri, title);

    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: "Diff view opened successfully.",
      is_error: false
    };
  }
}
