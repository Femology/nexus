import * as vscode from 'vscode';

import * as fs from 'fs';
import { KeyVault } from './KeyVault';
import { getSettings, updateSetting } from './ExtensionConfig';
import { PayloadDispatcher } from './PayloadDispatcher';

export class WebviewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = 'nexus-code-chat';
  private view?: vscode.WebviewView;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly keyVault: KeyVault,
    private readonly dispatcher: PayloadDispatcher
  ) {}

  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this.view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.extensionUri],
    };

    webviewView.webview.html = this.getHtmlForWebview(webviewView.webview);

    webviewView.webview.onDidReceiveMessage(async (message) => {
      switch (message.type) {
        case 'webviewReady':
          await this.sendInitializationData();
          break;
        case 'sendMessage':
          try {
             // Let dispatcher handle the logic (calls aggregator and backend)
             // and pass callbacks to handle stream updates
             const sessionId = 'session-' + Date.now(); // Dummy session ID for Phase 2, in Phase 4 we use real UUIDs
             await this.dispatcher.dispatch(
               message.text,
               message.modelAlias,
               message.stream,
               sessionId,
               this.view!
             );
          } catch (e: any) {
             this.view?.webview.postMessage({
                type: 'error',
                requestId: 'internal',
                message: e.message || String(e),
                isRetryable: false
             });
          }
          break;
        case 'saveApiKey':
          await this.keyVault.storeKey(message.alias, message.key, message.provider);
          await this.sendInitializationData();
          vscode.window.showInformationMessage(`Nexus-Code: Key saved for alias "${message.alias}"`);
          break;
        case 'deleteApiKey':
          await this.keyVault.deleteKey(message.alias);
          await this.sendInitializationData();
          vscode.window.showInformationMessage(`Nexus-Code: Key deleted for alias "${message.alias}"`);
          break;
        case 'updateSetting':
          await updateSetting(message.key, message.value);
          break;
        case 'newChat':
          vscode.commands.executeCommand('nexus-code.newChat');
          break;
      }
    });
  }

  private async sendInitializationData() {
    if (!this.view) return;
    
    const settings = getSettings();
    const aliasesMeta = await this.keyVault.listAliases();
    const keyAliases = aliasesMeta.map(a => a.alias);
    
    // For Phase 2, we just hardcode the known models.
    // In Phase 4, we query GET /v1/models from Daemon.
    const models = ['gpt-4o', 'gpt-4o-mini', 'claude-sonnet-4-5', 'claude-haiku-3-5', 'gemini-2.0-flash', 'deepseek-chat'];
    
    this.view.webview.postMessage({
      type: 'initialize',
      models,
      selectedModel: settings.defaultModel,
      settings,
      keyAliases
    });
  }

  private getHtmlForWebview(webview: vscode.Webview): string {
    const indexPath = vscode.Uri.joinPath(this.extensionUri, 'src', 'webview', 'index.html');
    let html = '';
    try {
        html = fs.readFileSync(indexPath.fsPath, 'utf8');
    } catch (e: any) {
        return `<html><body><h1>Error loading webview</h1><p>${e.message}</p><p>Path: ${indexPath.fsPath}</p></body></html>`;
    }

    const nonce = this.getNonce();
    
    const webviewJsUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, 'out', 'webview', 'main.js')
    );

    // Replace template vars
    html = html.replace(/{{nonce}}/g, nonce);
    html = html.replace(/{{webviewJsUri}}/g, webviewJsUri.toString());

    return html;
  }

  private getNonce() {
    let text = '';
    const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    for (let i = 0; i < 32; i++) {
      text += possible.charAt(Math.floor(Math.random() * possible.length));
    }
    return text;
  }
}
