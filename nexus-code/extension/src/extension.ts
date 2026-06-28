import * as vscode from 'vscode';
import { KeyVault } from './services/KeyVault';
import { ContextAggregator } from './services/ContextAggregator';
import { PayloadDispatcher } from './services/PayloadDispatcher';
import { WebviewProvider } from './services/WebviewProvider';
import { DaemonLifecycle } from './services/DaemonLifecycle';
import { DiffContentProvider } from './services/DiffContentProvider';
import { ToolExecutor } from './services/ToolExecutor';
import { ToolLoopOrchestrator } from './services/ToolLoopOrchestrator';

let daemonLifecycle: DaemonLifecycle;

export function activate(context: vscode.ExtensionContext): void {
  const outputChannel = vscode.window.createOutputChannel('Nexus-Code');
  outputChannel.appendLine('Nexus-Code extension activating...');

  // Initialize Services
  const keyVault = new KeyVault(context.secrets);
  const contextAggregator = new ContextAggregator();
  const payloadDispatcher = new PayloadDispatcher(keyVault, contextAggregator);
  
  // Phase 3 DI
  const diffProvider = new DiffContentProvider();
  const toolExecutor = new ToolExecutor(diffProvider);
  const toolOrchestrator = new ToolLoopOrchestrator(toolExecutor, payloadDispatcher);
  payloadDispatcher.setOrchestrator(toolOrchestrator);

  const webviewProvider = new WebviewProvider(context.extensionUri, keyVault, payloadDispatcher);
  daemonLifecycle = new DaemonLifecycle(context.extensionUri);

  // Register Document Provider for Diff
  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider(DiffContentProvider.scheme, diffProvider)
  );

  // Start Daemon
  daemonLifecycle.startDaemon().catch(err => {
    vscode.window.showErrorMessage(`Failed to start Nexus-Code Daemon: ${err.message}`);
  });

  // Register Webview Provider
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(WebviewProvider.viewType, webviewProvider)
  );

  // Register Commands
  const newChatCmd = vscode.commands.registerCommand(
    'nexus-code.newChat',
    () => {
      outputChannel.appendLine('Command: New Chat');
      vscode.commands.executeCommand('nexus-code-chat.focus');
    }
  );
  context.subscriptions.push(newChatCmd);

  const clearHistoryCmd = vscode.commands.registerCommand(
    'nexus-code.clearHistory',
    () => {
      outputChannel.appendLine('Command: Clear History');
      vscode.window.showInformationMessage('Nexus-Code: Chat history cleared.');
    }
  );
  context.subscriptions.push(clearHistoryCmd);

  const addApiKeyCmd = vscode.commands.registerCommand(
    'nexus-code.addApiKey',
    async () => {
      outputChannel.appendLine('Command: Add API Key');
      const alias = await vscode.window.showInputBox({
        prompt: 'Enter an alias for this API key (e.g., "openai-personal")',
        placeHolder: 'openai-personal',
      });
      if (!alias) return;

      const key = await vscode.window.showInputBox({
        prompt: `Enter the API key for "${alias}"`,
        password: true,
      });
      if (!key) return;

      const provider = await vscode.window.showQuickPick(['openai', 'anthropic', 'google', 'deepseek', 'ollama'], {
        placeHolder: 'Select Provider',
      });
      if (!provider) return;

      await keyVault.storeKey(alias, key, provider);
      vscode.window.showInformationMessage(`Nexus-Code: API key "${alias}" saved securely.`);
      outputChannel.appendLine(`API key alias "${alias}" registered for provider ${provider}.`);
    }
  );
  context.subscriptions.push(addApiKeyCmd);

  const restartDaemonCmd = vscode.commands.registerCommand(
    'nexus-code.restartDaemon',
    async () => {
      outputChannel.appendLine('Command: Restart Daemon');
      vscode.window.showInformationMessage('Nexus-Code: Daemon restart requested.');
      await daemonLifecycle.stopDaemon();
      await daemonLifecycle.startDaemon();
    }
  );
  context.subscriptions.push(restartDaemonCmd);

  context.subscriptions.push(outputChannel);
  outputChannel.appendLine('Nexus-Code extension activated successfully.');
}

export function deactivate(): void {
  if (daemonLifecycle) {
    daemonLifecycle.stopDaemon();
  }
}
