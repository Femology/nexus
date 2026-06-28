import * as vscode from 'vscode';
import * as fs from 'fs';
import { ContextBundle, ActiveFile, Selection, OpenTab, Diagnostic, DiagnosticSeverity } from '../types/contracts';
import { getSettings } from './ExtensionConfig';

export class ContextAggregator {
  
  public async collectContext(): Promise<ContextBundle> {
    const settings = getSettings();
    
    // Run all 7 collectors in parallel
    const [
      active_file,
      selection,
      open_tabs,
      workspace_structure,
      git_diff,
      diagnostics,
      terminal_snapshot
    ] = await Promise.all([
      this.collectActiveFile(),
      this.collectSelection(),
      this.collectOpenTabs(settings.maxOpenTabsContext),
      this.collectWorkspaceStructure(),
      this.collectGitDiff(),
      this.collectDiagnostics(),
      this.collectTerminalSnapshot(settings.terminalContextEnabled)
    ]);

    const partialBundle = {
      active_file,
      selection,
      open_tabs,
      workspace_structure,
      git_diff,
      diagnostics,
      terminal_snapshot,
    };

    const totalChars = JSON.stringify(partialBundle).length;
    const pre_compression_token_estimate = Math.ceil(totalChars / 4);
    const heavy_context_flag = pre_compression_token_estimate > settings.heavyContextThreshold;

    return {
      ...partialBundle,
      pre_compression_token_estimate,
      heavy_context_flag,
    };
  }

  private async collectActiveFile(): Promise<ActiveFile> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      return {
        path: '',
        language_id: 'plaintext',
        content: '',
        cursor_position: { line: 0, column: 0 }
      };
    }
    return {
      path: editor.document.uri.fsPath,
      language_id: editor.document.languageId,
      content: editor.document.getText(),
      cursor_position: {
        line: editor.selection.active.line,
        column: editor.selection.active.character
      }
    };
  }

  private async collectSelection(): Promise<Selection | null> {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.selection.isEmpty) {
      return null;
    }
    const sel = editor.selection;
    return {
      text: editor.document.getText(sel),
      start_line: sel.start.line,
      end_line: sel.end.line,
      start_column: sel.start.character,
      end_column: sel.end.character
    };
  }

  private async collectOpenTabs(maxTabs: number): Promise<OpenTab[]> {
    if (maxTabs <= 0) return [];
    
    const activeUri = vscode.window.activeTextEditor?.document.uri.toString();
    const tabs = vscode.window.tabGroups.all
      .flatMap(group => group.tabs)
      .filter(tab => tab.input instanceof vscode.TabInputText)
      .map(tab => tab.input as vscode.TabInputText);

    const openTabs: OpenTab[] = [];
    
    for (const input of tabs) {
      if (input.uri.toString() === activeUri) continue;
      
      try {
        const doc = await vscode.workspace.openTextDocument(input.uri);
        let content = doc.getText();
        if (content.length > 50000) {
          content = content.substring(0, 50000) + '\n\n...[TRUNCATED: Exceeds 50,000 characters]';
        }
        openTabs.push({
          path: doc.uri.fsPath,
          language_id: doc.languageId,
          content
        });
        if (openTabs.length >= maxTabs) break;
      } catch (e) {
        // Skip failed documents
      }
    }
    return openTabs;
  }

  private async collectWorkspaceStructure(): Promise<Record<string, unknown>> {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) return {};

    // Depth-3 glob search
    const excludePattern = '{node_modules,.git,dist,build,__pycache__,.next,.cache,coverage,.nyc_output}';
    const files = await vscode.workspace.findFiles('**/*', excludePattern, 1000);
    
    const tree: Record<string, any> = {};
    for (const file of files) {
      const relative = vscode.workspace.asRelativePath(file, false);
      const parts = relative.split('/');
      if (parts.length > 3) continue; // max depth 3
      
      let current = tree;
      for (let i = 0; i < parts.length - 1; i++) {
        if (!current[parts[i]]) current[parts[i]] = {};
        current = current[parts[i]];
      }
      current[parts[parts.length - 1]] = null; // null means file
    }
    
    return tree;
  }

  private async collectGitDiff(): Promise<string> {
    const gitExtension = vscode.extensions.getExtension('vscode.git');
    if (!gitExtension) return '';

    try {
      const api = gitExtension.isActive ? gitExtension.exports.getAPI(1) : undefined;
      if (!api || !api.repositories || api.repositories.length === 0) return '';

      const repo = api.repositories[0];
      // Getting diffs usually requires running `git diff` natively, 
      // but repo.diff() returns unified diff strings in vscode.git API
      // We will fallback to a short diff summary if full diff is too large.
      const diff = await repo.diff(true); // staged
      const diffUnstaged = await repo.diff(false); // unstaged
      
      return [diff, diffUnstaged].filter(d => d).join('\n');
    } catch (e) {
      return '';
    }
  }

  private async collectDiagnostics(): Promise<Diagnostic[]> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return [];

    const rawDiagnostics = vscode.languages.getDiagnostics(editor.document.uri);
    return rawDiagnostics.slice(0, 50).map(d => ({
      message: d.message,
      severity: this.mapSeverity(d.severity),
      range: {
        start: { line: d.range.start.line, column: d.range.start.character },
        end: { line: d.range.end.line, column: d.range.end.character }
      },
      source: d.source || 'unknown'
    }));
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

  private async collectTerminalSnapshot(enabled: boolean): Promise<string | null> {
    if (!enabled) return null;
    
    // Note: VS Code API does not directly expose terminal text buffers.
    // There's a proposed API `vscode.window.activeTerminal?.buffer`, but
    // for standard APIs, we simulate it or return a generic message unless 
    // using terminal commands. We will return a placeholder for Phase 2 
    // unless there is a specific way to read it.
    // VS Code 1.85+ doesn't allow reading terminal buffer without accessibility API.
    return 'Terminal text extraction requires accessibility API or shell integration. Placeholder string for phase 2.';
  }
}
