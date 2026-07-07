"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/extension.ts
var extension_exports = {};
__export(extension_exports, {
  activate: () => activate,
  deactivate: () => deactivate
});
module.exports = __toCommonJS(extension_exports);
var vscode8 = __toESM(require("vscode"));

// src/services/KeyVault.ts
var KEY_PREFIX = "nexuscode-key-";
var ALIAS_LIST_KEY = "nexuscode-key-aliases";
var KeyVault = class {
  secrets;
  constructor(secretStorage) {
    this.secrets = secretStorage;
  }
  /**
   * Store an API key under the given alias.
   *
   * The provider string is stored separately as informational metadata so
   * the settings UI can display which provider each alias belongs to. The
   * key value itself is never recorded in the metadata.
   */
  async storeKey(alias, key, provider) {
    if (!alias) {
      throw new Error("KeyVault.storeKey: alias must be a non-empty string");
    }
    if (!key) {
      throw new Error("KeyVault.storeKey: key must be a non-empty string");
    }
    await this.secrets.store(KEY_PREFIX + alias, key);
    await this.registerAlias(alias, provider);
  }
  /**
   * Retrieve the raw API key for an alias.
   *
   * IMPORTANT: The caller must use this value immediately (inject into an
   * Authorization header) and must not store it anywhere. This method does
   * not cache the value.
   *
   * @returns The raw key, or undefined if the alias is unknown.
   */
  async getKey(alias) {
    if (!alias) {
      return void 0;
    }
    return this.secrets.get(KEY_PREFIX + alias);
  }
  /**
   * Delete the key and metadata for an alias.
   */
  async deleteKey(alias) {
    await this.secrets.delete(KEY_PREFIX + alias);
    await this.unregisterAlias(alias);
  }
  /**
   * Rotate (overwrite) an existing alias's key with a new value.
   *
   * Because SecretStorage.store overwrites atomically, there is never a
   * window during which both old and new keys coexist in the clear.
   */
  async rotateKey(alias, newKey) {
    if (!newKey) {
      throw new Error("KeyVault.rotateKey: newKey must be a non-empty string");
    }
    await this.secrets.store(KEY_PREFIX + alias, newKey);
  }
  /**
   * List all registered key aliases (no key material returned).
   */
  async listAliases() {
    const raw = await this.secrets.get(ALIAS_LIST_KEY);
    if (!raw) {
      return [];
    }
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed.filter((item) => {
        return typeof item === "object" && item !== null && typeof item.alias === "string";
      }).map((item) => ({
        alias: item.alias,
        provider: typeof item.provider === "string" ? item.provider : "unknown"
      }));
    } catch {
      return [];
    }
  }
  // ---------------------------------------------------------------------------
  // Private alias-list metadata maintenance
  // ---------------------------------------------------------------------------
  async registerAlias(alias, provider) {
    const aliases = await this.listAliases();
    const existingIndex = aliases.findIndex((a) => a.alias === alias);
    if (existingIndex >= 0) {
      aliases[existingIndex] = { alias, provider };
    } else {
      aliases.push({ alias, provider });
    }
    await this.secrets.store(ALIAS_LIST_KEY, JSON.stringify(aliases));
  }
  async unregisterAlias(alias) {
    const aliases = await this.listAliases();
    const filtered = aliases.filter((a) => a.alias !== alias);
    await this.secrets.store(ALIAS_LIST_KEY, JSON.stringify(filtered));
  }
};

// src/services/ContextAggregator.ts
var vscode2 = __toESM(require("vscode"));

// src/services/ExtensionConfig.ts
var vscode = __toESM(require("vscode"));
var SECTION = "nexuscode";
function getSettings() {
  const cfg = vscode.workspace.getConfiguration(SECTION);
  return {
    defaultModel: cfg.get("defaultModel", "gpt-4o"),
    streamingEnabled: cfg.get("streamingEnabled", true),
    terminalContextEnabled: cfg.get("terminalContextEnabled", true),
    maxOpenTabsContext: cfg.get("maxOpenTabsContext", 10),
    heavyContextThreshold: cfg.get("heavyContextThreshold", 8e3),
    daemonPort: cfg.get("daemonPort", 8e3),
    daemonMaxRestarts: cfg.get("daemonMaxRestarts", 5)
  };
}
async function updateSetting(key, value) {
  const cfg = vscode.workspace.getConfiguration(SECTION);
  await cfg.update(key, value, vscode.ConfigurationTarget.Global);
}

// src/services/ContextAggregator.ts
var ContextAggregator = class {
  async collectContext() {
    const settings = getSettings();
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
      terminal_snapshot
    };
    const totalChars = JSON.stringify(partialBundle).length;
    const pre_compression_token_estimate = Math.ceil(totalChars / 4);
    const heavy_context_flag = pre_compression_token_estimate > settings.heavyContextThreshold;
    return {
      ...partialBundle,
      pre_compression_token_estimate,
      heavy_context_flag
    };
  }
  async collectActiveFile() {
    const editor = vscode2.window.activeTextEditor;
    if (!editor) {
      return {
        path: "",
        language_id: "plaintext",
        content: "",
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
  async collectSelection() {
    const editor = vscode2.window.activeTextEditor;
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
  async collectOpenTabs(maxTabs) {
    if (maxTabs <= 0) return [];
    const activeUri = vscode2.window.activeTextEditor?.document.uri.toString();
    const tabs = vscode2.window.tabGroups.all.flatMap((group) => group.tabs).filter((tab) => tab.input instanceof vscode2.TabInputText).map((tab) => tab.input);
    const openTabs = [];
    for (const input of tabs) {
      if (input.uri.toString() === activeUri) continue;
      try {
        const doc = await vscode2.workspace.openTextDocument(input.uri);
        let content = doc.getText();
        if (content.length > 5e4) {
          content = content.substring(0, 5e4) + "\n\n...[TRUNCATED: Exceeds 50,000 characters]";
        }
        openTabs.push({
          path: doc.uri.fsPath,
          language_id: doc.languageId,
          content
        });
        if (openTabs.length >= maxTabs) break;
      } catch (e) {
      }
    }
    return openTabs;
  }
  async collectWorkspaceStructure() {
    const folders = vscode2.workspace.workspaceFolders;
    if (!folders || folders.length === 0) return {};
    const excludePattern = "{node_modules,.git,dist,build,__pycache__,.next,.cache,coverage,.nyc_output}";
    const files = await vscode2.workspace.findFiles("**/*", excludePattern, 1e3);
    const tree = {};
    for (const file of files) {
      const relative = vscode2.workspace.asRelativePath(file, false);
      const parts = relative.split("/");
      if (parts.length > 3) continue;
      let current = tree;
      for (let i = 0; i < parts.length - 1; i++) {
        if (!current[parts[i]]) current[parts[i]] = {};
        current = current[parts[i]];
      }
      current[parts[parts.length - 1]] = null;
    }
    return tree;
  }
  async collectGitDiff() {
    const gitExtension = vscode2.extensions.getExtension("vscode.git");
    if (!gitExtension) return "";
    try {
      const api = gitExtension.isActive ? gitExtension.exports.getAPI(1) : void 0;
      if (!api || !api.repositories || api.repositories.length === 0) return "";
      const repo = api.repositories[0];
      const diff = await repo.diff(true);
      const diffUnstaged = await repo.diff(false);
      return [diff, diffUnstaged].filter((d) => d).join("\n");
    } catch (e) {
      return "";
    }
  }
  async collectDiagnostics() {
    const editor = vscode2.window.activeTextEditor;
    if (!editor) return [];
    const rawDiagnostics = vscode2.languages.getDiagnostics(editor.document.uri);
    return rawDiagnostics.slice(0, 50).map((d) => ({
      message: d.message,
      severity: this.mapSeverity(d.severity),
      range: {
        start: { line: d.range.start.line, column: d.range.start.character },
        end: { line: d.range.end.line, column: d.range.end.character }
      },
      source: d.source || "unknown"
    }));
  }
  mapSeverity(severity) {
    switch (severity) {
      case vscode2.DiagnosticSeverity.Error:
        return "error";
      case vscode2.DiagnosticSeverity.Warning:
        return "warning";
      case vscode2.DiagnosticSeverity.Information:
        return "information";
      case vscode2.DiagnosticSeverity.Hint:
        return "hint";
      default:
        return "information";
    }
  }
  async collectTerminalSnapshot(enabled) {
    if (!enabled) return null;
    return "Terminal text extraction requires accessibility API or shell integration. Placeholder string for phase 2.";
  }
};

// src/services/PayloadDispatcher.ts
var vscode3 = __toESM(require("vscode"));
var PayloadDispatcher = class {
  constructor(keyVault, contextAggregator, daemonLifecycle2) {
    this.keyVault = keyVault;
    this.contextAggregator = contextAggregator;
    this.daemonLifecycle = daemonLifecycle2;
  }
  orchestrator;
  setOrchestrator(orchestrator) {
    this.orchestrator = orchestrator;
  }
  async dispatch(userMessage, modelAlias, stream, sessionId, webviewView, toolResults, attempt = 1) {
    try {
      const aliases = await this.keyVault.listAliases();
      if (aliases.length === 0) {
        throw new Error("No API keys configured. Please add an API key in Settings.");
      }
      const providerKeyAlias = aliases[0].alias;
      const apiKey = await this.keyVault.getKey(providerKeyAlias);
      if (!apiKey) {
        throw new Error(`API key not found for alias: ${providerKeyAlias}`);
      }
      let context_bundle;
      if (toolResults && toolResults.length > 0) {
        const editor = vscode3.window.activeTextEditor;
        context_bundle = {
          active_file: {
            path: editor ? editor.document.uri.fsPath : "",
            language_id: "plaintext",
            content: "",
            cursor_position: { line: 0, column: 0 }
          },
          selection: null,
          open_tabs: [],
          workspace_structure: {},
          git_diff: "",
          diagnostics: [],
          terminal_snapshot: null,
          pre_compression_token_estimate: 0,
          heavy_context_flag: false
        };
      } else {
        context_bundle = await this.contextAggregator.collectContext();
      }
      const payload = {
        session_id: sessionId,
        request_id: "req-" + Date.now(),
        timestamp: (/* @__PURE__ */ new Date()).toISOString(),
        model_alias: modelAlias,
        stream,
        user_message: userMessage,
        provider_key_alias: providerKeyAlias,
        context_bundle,
        history_ref: sessionId,
        tool_results: toolResults || null
      };
      const port = this.daemonLifecycle.getPort();
      const secret = this.daemonLifecycle.getSecret();
      if (!port || !secret) {
        throw new Error("Daemon is not running or lockfile is missing.");
      }
      const url = `http://localhost:${port}/v1/chat`;
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 15e3);
      let response;
      try {
        response = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${apiKey}`,
            "X-Nexus-Secret": secret
          },
          body: JSON.stringify(payload),
          signal: controller.signal
        });
        clearTimeout(timeoutId);
      } catch (e) {
        clearTimeout(timeoutId);
        if (e.name === "AbortError") {
          throw new Error("Daemon is slow to respond (>15s timeout). Please ensure the daemon is healthy.");
        }
        if ((e.cause?.code === "ECONNREFUSED" || e.message.includes("fetch failed")) && attempt < 3) {
          console.log(`Connection refused, retrying ${attempt}/3...`);
          webviewView.webview.postMessage({
            type: "streamDelta",
            requestId: payload.request_id,
            delta: `
*[Starting daemon... retry ${attempt}/3]*
`
          });
          await new Promise((r) => setTimeout(r, 2e3));
          return this.dispatch(userMessage, modelAlias, stream, sessionId, webviewView, toolResults, attempt + 1);
        }
        throw e;
      }
      if (!response.ok) {
        if (response.status === 422) {
          let errDetail = await response.text();
          throw new Error(`Internal error: invalid request format. Details: ${errDetail}`);
        } else if (response.status >= 500) {
          throw new Error(`Daemon error: HTTP ${response.status} ${response.statusText}`);
        }
        throw new Error(`HTTP Error ${response.status} ${response.statusText}`);
      }
      if (stream && response.body) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let done = false;
        let finalResponse = void 0;
        try {
          while (!done) {
            const { value, done: readerDone } = await reader.read();
            done = readerDone;
            if (value) {
              const chunk = decoder.decode(value, { stream: true });
              const lines = chunk.split("\n");
              for (const line of lines) {
                if (line.startsWith("data: ")) {
                  const data = line.slice(6).trim();
                  if (data === "[DONE]") {
                  } else if (data) {
                    try {
                      const parsed = JSON.parse(data);
                      if (parsed.response_text || parsed.delta) {
                        webviewView.webview.postMessage({
                          type: "streamDelta",
                          requestId: payload.request_id,
                          delta: parsed.delta || parsed.response_text || ""
                        });
                      }
                      if (parsed.is_final || parsed.tool_calls && parsed.tool_calls.length > 0) {
                        finalResponse = parsed;
                      }
                    } catch (e) {
                      console.error("Failed to parse SSE data", e);
                    }
                  }
                }
              }
            }
          }
        } catch (streamError) {
          console.error("Stream reading error", streamError);
          webviewView.webview.postMessage({
            type: "streamDelta",
            requestId: payload.request_id,
            delta: "\n\n**[Connection lost mid-stream]**\n"
          });
          return;
        }
        if (finalResponse && this.orchestrator) {
          await this.orchestrator.handleResponse(finalResponse, sessionId, modelAlias, stream, webviewView);
        }
      } else {
        const jsonResponse = await response.json();
        if (this.orchestrator) {
          await this.orchestrator.handleResponse(jsonResponse, sessionId, modelAlias, stream, webviewView);
        } else {
          webviewView.webview.postMessage({
            type: "responseComplete",
            response: jsonResponse
          });
        }
      }
    } catch (e) {
      console.error("PayloadDispatcher error", e);
      let message = e.message;
      if (e.cause?.code === "ECONNREFUSED" || message.includes("fetch failed")) {
        message = "Daemon connection refused. Is the Nexus-Code optimization daemon running?";
      }
      webviewView.webview.postMessage({
        type: "error",
        requestId: "internal",
        message,
        isRetryable: true
      });
    }
  }
};

// src/services/WebviewProvider.ts
var vscode4 = __toESM(require("vscode"));
var fs = __toESM(require("fs"));
var WebviewProvider = class {
  constructor(extensionUri, keyVault, dispatcher) {
    this.extensionUri = extensionUri;
    this.keyVault = keyVault;
    this.dispatcher = dispatcher;
  }
  static viewType = "nexus-code-chat";
  view;
  resolveWebviewView(webviewView, _context, _token) {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.extensionUri]
    };
    webviewView.webview.html = this.getHtmlForWebview(webviewView.webview);
    webviewView.webview.onDidReceiveMessage(async (message) => {
      switch (message.type) {
        case "webviewReady":
          await this.sendInitializationData();
          break;
        case "sendMessage":
          try {
            const sessionId = "session-" + Date.now();
            await this.dispatcher.dispatch(
              message.text,
              message.modelAlias,
              message.stream,
              sessionId,
              this.view
            );
          } catch (e) {
            this.view?.webview.postMessage({
              type: "error",
              requestId: "internal",
              message: e.message || String(e),
              isRetryable: false
            });
          }
          break;
        case "saveApiKey":
          await this.keyVault.storeKey(message.alias, message.key, message.provider);
          await this.sendInitializationData();
          vscode4.window.showInformationMessage(`Nexus-Code: API key saved for ${message.provider}.`);
          break;
        case "deleteApiKey":
          await this.keyVault.deleteKey(message.alias);
          await this.sendInitializationData();
          vscode4.window.showInformationMessage(`Nexus-Code: API key removed for ${message.alias}.`);
          break;
        case "updateSetting":
          await updateSetting(message.key, message.value);
          break;
        case "newChat":
          vscode4.commands.executeCommand("nexus-code.newChat");
          break;
        case "openLink":
          vscode4.env.openExternal(vscode4.Uri.parse(message.url));
          break;
        case "applyEdit":
          const editor = vscode4.window.activeTextEditor;
          if (editor) {
            editor.edit((editBuilder) => {
              editBuilder.replace(editor.selection, message.code);
            });
          } else {
            vscode4.window.showWarningMessage("Nexus-Code: No active editor to apply code to.");
          }
          break;
      }
    });
  }
  async sendInitializationData() {
    if (!this.view) return;
    const settings = getSettings();
    const aliasesMeta = await this.keyVault.listAliases();
    this.view.webview.postMessage({
      type: "initialize",
      keyAliases: aliasesMeta,
      // Full metadata: [{ alias, provider }]
      selectedModel: settings.defaultModel,
      settings
    });
  }
  getHtmlForWebview(webview) {
    const indexPath = vscode4.Uri.joinPath(this.extensionUri, "src", "webview", "index.html");
    let html = "";
    try {
      html = fs.readFileSync(indexPath.fsPath, "utf8");
    } catch (e) {
      return `<html><body><h1>Error loading webview</h1><p>${e.message}</p><p>Path: ${indexPath.fsPath}</p></body></html>`;
    }
    const nonce = this.getNonce();
    const webviewJsUri = webview.asWebviewUri(
      vscode4.Uri.joinPath(this.extensionUri, "out", "webview", "main.js")
    );
    html = html.replace(/{{nonce}}/g, nonce);
    html = html.replace(/{{webviewJsUri}}/g, webviewJsUri.toString());
    return html;
  }
  getNonce() {
    let text = "";
    const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    for (let i = 0; i < 32; i++) {
      text += possible.charAt(Math.floor(Math.random() * possible.length));
    }
    return text;
  }
};

// src/services/DaemonLifecycle.ts
var vscode5 = __toESM(require("vscode"));
var cp = __toESM(require("child_process"));
var path = __toESM(require("path"));
var fs2 = __toESM(require("fs"));
var os = __toESM(require("os"));
var DaemonLifecycle = class {
  constructor(extensionUri) {
    this.extensionUri = extensionUri;
    this.outputChannel = vscode5.window.createOutputChannel("Nexus-Code Daemon");
  }
  childProcess;
  outputChannel;
  healthCheckInterval;
  restartCount = 0;
  isShuttingDown = false;
  daemonPort;
  daemonSecret;
  getPort() {
    return this.daemonPort;
  }
  getSecret() {
    return this.daemonSecret;
  }
  getLockfilePath() {
    return path.join(os.homedir(), ".nexus-code", "daemon.lock");
  }
  readLockfile() {
    try {
      const lockfilePath = this.getLockfilePath();
      if (fs2.existsSync(lockfilePath)) {
        const content = fs2.readFileSync(lockfilePath, "utf8");
        return JSON.parse(content);
      }
    } catch (e) {
    }
    return null;
  }
  async startDaemon() {
    this.isShuttingDown = false;
    const existingDaemon = this.readLockfile();
    if (existingDaemon) {
      if (await this.checkHealth(existingDaemon.port, existingDaemon.secret)) {
        this.outputChannel.appendLine(`Reconnected to existing daemon on port ${existingDaemon.port}.`);
        this.daemonPort = existingDaemon.port;
        this.daemonSecret = existingDaemon.secret;
        this.startHealthCheckLoop();
        return;
      } else {
        this.outputChannel.appendLine("Found stale lockfile or unresponsive daemon. Cleaning up...");
        try {
          fs2.unlinkSync(this.getLockfilePath());
        } catch (e) {
        }
      }
    }
    this.outputChannel.appendLine(`Starting new daemon...`);
    let daemonDir = path.join(this.extensionUri.fsPath, "daemon");
    if (!fs2.existsSync(daemonDir)) {
      daemonDir = path.join(this.extensionUri.fsPath, "..", "daemon");
    }
    const isWin = process.platform === "win32";
    const venvDir = path.join(daemonDir, "venv");
    const venvPython = path.join(venvDir, isWin ? "Scripts" : "bin", isWin ? "python.exe" : "python");
    if (!fs2.existsSync(venvDir)) {
      this.outputChannel.appendLine(`Creating Python virtual environment in ${venvDir}...`);
      try {
        cp.execSync(`python3 -m venv venv`, { cwd: daemonDir });
        this.outputChannel.appendLine(`Installing requirements...`);
        cp.execSync(`${venvPython} -m pip install -r requirements.txt`, { cwd: daemonDir });
      } catch (e) {
        this.outputChannel.appendLine(`Failed to setup venv: ${e.message}`);
        vscode5.window.showErrorMessage("Failed to setup Python virtual environment for Nexus-Code Daemon.");
        throw e;
      }
    }
    const pythonExec = venvPython;
    try {
      cp.execSync(`${pythonExec} --version`);
    } catch (e) {
      vscode5.window.showErrorMessage(
        "Python not found or venv is broken. Nexus-Code requires Python to run its daemon.",
        "View Logs"
      ).then((selection) => {
        if (selection === "View Logs") {
          this.outputChannel.show();
        }
      });
      throw new Error("Python executable not found.");
    }
    this.childProcess = cp.spawn(pythonExec, ["-m", "app.main"], {
      cwd: daemonDir,
      env: process.env
    });
    let stderrBuffer = "";
    this.childProcess.stdout?.on("data", (data) => {
      this.outputChannel.append(data.toString());
    });
    this.childProcess.stderr?.on("data", (data) => {
      const output = data.toString();
      this.outputChannel.append(output);
      stderrBuffer += output;
      if (output.includes("error while attempting to bind on address") && output.includes("address already in use")) {
        vscode5.window.showErrorMessage(
          `Daemon port binding error.`,
          "View Logs"
        ).then((selection) => {
          if (selection === "View Logs") {
            this.outputChannel.show();
          }
        });
      }
    });
    this.childProcess.on("exit", (code, signal) => {
      this.outputChannel.appendLine(`Daemon exited with code ${code} and signal ${signal}`);
      if (!this.isShuttingDown) {
        this.handleCrash();
      }
    });
    const lockfileCreated = await this.waitForLockfile(5e3);
    if (!lockfileCreated) {
      throw new Error(`Daemon failed to create lockfile. Stderr snippet: ${stderrBuffer.substring(0, 500)}`);
    }
    const newDaemon = this.readLockfile();
    if (!newDaemon) {
      throw new Error("Daemon created lockfile but failed to read it.");
    }
    this.daemonPort = newDaemon.port;
    this.daemonSecret = newDaemon.secret;
    const success = await this.waitForHealth(this.daemonPort, this.daemonSecret, 3e4);
    if (success) {
      this.outputChannel.appendLine(`Daemon started successfully on port ${this.daemonPort}.`);
      this.restartCount = 0;
      this.startHealthCheckLoop();
    } else {
      vscode5.window.showErrorMessage(
        "Daemon failed to pass health check within 30 seconds.",
        "View Logs"
      ).then((selection) => {
        if (selection === "View Logs") {
          this.outputChannel.show();
        }
      });
      throw new Error(`Daemon failed to start. Stderr snippet: ${stderrBuffer.substring(0, 500)}`);
    }
  }
  async stopDaemon() {
    this.isShuttingDown = true;
    this.stopHealthCheckLoop();
    if (this.childProcess && this.childProcess.pid) {
      this.outputChannel.appendLine("Stopping daemon...");
      this.childProcess.kill("SIGTERM");
      for (let i = 0; i < 10; i++) {
        if (this.childProcess.killed) break;
        await new Promise((r) => setTimeout(r, 500));
      }
      if (!this.childProcess.killed) {
        this.outputChannel.appendLine("Daemon did not stop gracefully, sending SIGKILL...");
        this.childProcess.kill("SIGKILL");
      }
    }
    try {
      fs2.unlinkSync(this.getLockfilePath());
    } catch (e) {
    }
    this.childProcess = void 0;
    this.daemonPort = void 0;
    this.daemonSecret = void 0;
  }
  async checkHealth(port, secret) {
    try {
      const response = await fetch(`http://localhost:${port}/health`, {
        method: "GET",
        headers: {
          "X-Nexus-Secret": secret
        }
      });
      return response.ok;
    } catch {
      return false;
    }
  }
  async waitForLockfile(timeoutMs) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (this.readLockfile() !== null) {
        return true;
      }
      await new Promise((r) => setTimeout(r, 200));
    }
    return false;
  }
  async waitForHealth(port, secret, timeoutMs) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await this.checkHealth(port, secret)) {
        return true;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    return false;
  }
  startHealthCheckLoop() {
    this.stopHealthCheckLoop();
    this.healthCheckInterval = setInterval(async () => {
      if (this.isShuttingDown || !this.daemonPort || !this.daemonSecret) return;
      const healthy = await this.checkHealth(this.daemonPort, this.daemonSecret);
      if (!healthy) {
        this.outputChannel.appendLine("Health check failed. Initiating restart...");
        this.handleCrash();
      }
    }, 3e4);
  }
  stopHealthCheckLoop() {
    if (this.healthCheckInterval) {
      clearInterval(this.healthCheckInterval);
      this.healthCheckInterval = void 0;
    }
  }
  async handleCrash() {
    this.stopHealthCheckLoop();
    const settings = getSettings();
    if (this.restartCount >= settings.daemonMaxRestarts) {
      vscode5.window.showErrorMessage("Nexus-Code Daemon crashed repeatedly and will not restart.");
      return;
    }
    const backoff = Math.min(Math.pow(2, this.restartCount) * 1e3, 3e4);
    this.restartCount++;
    this.outputChannel.appendLine(`Restarting daemon in ${backoff}ms (attempt ${this.restartCount} of ${settings.daemonMaxRestarts})...`);
    setTimeout(() => {
      this.startDaemon().catch((e) => {
        this.outputChannel.appendLine(`Restart failed: ${e}`);
        this.handleCrash();
      });
    }, backoff);
  }
};

// src/services/DiffContentProvider.ts
var vscode6 = __toESM(require("vscode"));
var DiffContentProvider = class {
  static scheme = "nexus-diff";
  contentMap = /* @__PURE__ */ new Map();
  onDidChangeEmitter = new vscode6.EventEmitter();
  get onDidChange() {
    return this.onDidChangeEmitter.event;
  }
  provideTextDocumentContent(uri) {
    return this.contentMap.get(uri.toString()) || "";
  }
  setContent(uri, content) {
    this.contentMap.set(uri.toString(), content);
    this.onDidChangeEmitter.fire(uri);
  }
};

// src/services/ToolExecutor.ts
var vscode7 = __toESM(require("vscode"));
var path2 = __toESM(require("path"));
var os2 = __toESM(require("os"));
var fs3 = __toESM(require("fs"));
var ToolExecutor = class {
  constructor(diffProvider) {
    this.diffProvider = diffProvider;
  }
  terminal;
  async executeTool(toolCall) {
    const outputChannel = vscode7.window.createOutputChannel("Nexus-Code");
    outputChannel.appendLine(`Executing tool: ${toolCall.tool_name}`);
    try {
      switch (toolCall.tool_name) {
        case "read_file":
          return await this.readFile(toolCall);
        case "write_file":
          return await this.writeFile(toolCall);
        case "create_file":
          return await this.createFile(toolCall);
        case "run_terminal":
          return await this.runTerminal(toolCall);
        case "list_directory":
          return await this.listDirectory(toolCall);
        case "get_diagnostics":
          return await this.getDiagnostics(toolCall);
        case "show_diff":
          return await this.showDiff(toolCall);
        case "apply_edit":
          return await this.applyEdit(toolCall);
        default:
          throw new Error(`Unknown tool: ${toolCall.tool_name}`);
      }
    } catch (e) {
      outputChannel.appendLine(`Tool ${toolCall.tool_name} failed: ${e.message}`);
      return {
        tool_call_id: toolCall.id,
        tool_name: toolCall.tool_name,
        output: e.message || String(e),
        is_error: true
      };
    }
  }
  validateWorkspacePath(targetPath) {
    if (!vscode7.workspace.workspaceFolders) {
      throw new Error("No open workspace folders");
    }
    const uri = vscode7.Uri.file(targetPath);
    const workspaceFolder = vscode7.workspace.getWorkspaceFolder(uri);
    if (!workspaceFolder) {
      throw new Error(`Path is outside workspace root: ${targetPath}`);
    }
  }
  async readFile(toolCall) {
    const filePath = toolCall.arguments.path;
    const uri = vscode7.Uri.file(filePath);
    const contentBuffer = await vscode7.workspace.fs.readFile(uri);
    const content = Buffer.from(contentBuffer).toString("utf-8");
    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: content,
      is_error: false
    };
  }
  async writeFile(toolCall) {
    const filePath = toolCall.arguments.path;
    const content = toolCall.arguments.content;
    this.validateWorkspacePath(filePath);
    const uri = vscode7.Uri.file(filePath);
    try {
      await vscode7.workspace.fs.stat(uri);
    } catch {
      throw new Error(`File does not exist: ${filePath}. Use create_file instead.`);
    }
    const edit = new vscode7.WorkspaceEdit();
    const document = await vscode7.workspace.openTextDocument(uri);
    const fullRange = new vscode7.Range(
      document.positionAt(0),
      document.positionAt(document.getText().length)
    );
    edit.replace(uri, fullRange, content);
    const success = await vscode7.workspace.applyEdit(edit);
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
  async createFile(toolCall) {
    const filePath = toolCall.arguments.path;
    const content = toolCall.arguments.content;
    this.validateWorkspacePath(filePath);
    const uri = vscode7.Uri.file(filePath);
    await vscode7.workspace.fs.writeFile(uri, Buffer.from(content, "utf-8"));
    const doc = await vscode7.workspace.openTextDocument(uri);
    await vscode7.window.showTextDocument(doc);
    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: `Successfully created file ${filePath}`,
      is_error: false
    };
  }
  async runTerminal(toolCall) {
    const command = toolCall.arguments.command;
    const timeoutMs = toolCall.arguments.timeout_ms || 3e4;
    if (!this.terminal || this.terminal.exitStatus) {
      this.terminal = vscode7.window.createTerminal("Nexus-Code");
    }
    this.terminal.show(true);
    const tmpFile = path2.join(os2.tmpdir(), `nexus-term-${Date.now()}.txt`);
    const marker = `NEXUS_TERM_DONE_${Date.now()}`;
    const isWin = process.platform === "win32";
    const wrappedCmd = isWin ? `(& { ${command} } | Tee-Object -FilePath "${tmpFile}"); Write-Output "${marker}" >> "${tmpFile}"` : `(${command}) | tee "${tmpFile}"; echo "${marker}" >> "${tmpFile}"`;
    this.terminal.sendText(wrappedCmd);
    const start = Date.now();
    let output = "";
    while (Date.now() - start < timeoutMs) {
      if (fs3.existsSync(tmpFile)) {
        output = fs3.readFileSync(tmpFile, "utf-8");
        if (output.includes(marker)) {
          output = output.replace(marker, "").trim();
          try {
            fs3.unlinkSync(tmpFile);
          } catch (e) {
          }
          return {
            tool_call_id: toolCall.id,
            tool_name: toolCall.tool_name,
            output: output || "Command completed successfully with no output.",
            is_error: false
          };
        }
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    if (fs3.existsSync(tmpFile)) {
      output = fs3.readFileSync(tmpFile, "utf-8").trim();
      try {
        fs3.unlinkSync(tmpFile);
      } catch (e) {
      }
    }
    throw new Error(`Command timed out after ${timeoutMs}ms. Partial output:
${output}`);
  }
  async listDirectory(toolCall) {
    const dirPath = toolCall.arguments.path;
    const uri = vscode7.Uri.file(dirPath);
    const entries = await vscode7.workspace.fs.readDirectory(uri);
    const formatted = entries.map(([name, type]) => {
      const typeStr = type === vscode7.FileType.Directory ? "directory" : "file";
      return `${typeStr}: ${name}`;
    }).join("\n");
    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: formatted || "(empty directory)",
      is_error: false
    };
  }
  async getDiagnostics(toolCall) {
    const filePath = toolCall.arguments.path;
    const uri = vscode7.Uri.file(filePath);
    const diags = vscode7.languages.getDiagnostics(uri);
    if (diags.length === 0) {
      return {
        tool_call_id: toolCall.id,
        tool_name: toolCall.tool_name,
        output: "No diagnostics found.",
        is_error: false
      };
    }
    const formatted = diags.map((d) => ({
      message: d.message,
      severity: this.mapSeverity(d.severity),
      range: {
        start: { line: d.range.start.line, column: d.range.start.character },
        end: { line: d.range.end.line, column: d.range.end.character }
      },
      source: d.source || "unknown"
    }));
    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: JSON.stringify(formatted, null, 2),
      is_error: false
    };
  }
  mapSeverity(severity) {
    switch (severity) {
      case vscode7.DiagnosticSeverity.Error:
        return "error";
      case vscode7.DiagnosticSeverity.Warning:
        return "warning";
      case vscode7.DiagnosticSeverity.Information:
        return "information";
      case vscode7.DiagnosticSeverity.Hint:
        return "hint";
      default:
        return "information";
    }
  }
  async showDiff(toolCall) {
    const originalContent = toolCall.arguments.original_content;
    const modifiedContent = toolCall.arguments.modified_content;
    const filePath = toolCall.arguments.file_path;
    const baseUri = vscode7.Uri.file(filePath);
    const originalUri = baseUri.with({ scheme: DiffContentProvider.scheme, query: "original" });
    const modifiedUri = baseUri.with({ scheme: DiffContentProvider.scheme, query: "modified" });
    this.diffProvider.setContent(originalUri, originalContent);
    this.diffProvider.setContent(modifiedUri, modifiedContent);
    const title = `Diff: ${path2.basename(filePath)}`;
    await vscode7.commands.executeCommand("vscode.diff", originalUri, modifiedUri, title);
    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: "Diff view opened successfully.",
      is_error: false
    };
  }
  async applyEdit(toolCall) {
    const filePath = toolCall.arguments.path;
    const editText = toolCall.arguments.edit_text;
    this.validateWorkspacePath(filePath);
    const uri = vscode7.Uri.file(filePath);
    const document = await vscode7.workspace.openTextDocument(uri);
    let content = document.getText();
    const blockRegex = /<<<<\n([\s\S]*?)\n====\n([\s\S]*?)\n>>>>/g;
    let match;
    let anyMatches = false;
    let failedMatches = 0;
    while ((match = blockRegex.exec(editText)) !== null) {
      anyMatches = true;
      const oldCode = match[1];
      const newCode = match[2];
      if (content.includes(oldCode)) {
        content = content.replace(oldCode, newCode);
      } else {
        const oldTrimmed = oldCode.trim();
        if (content.includes(oldTrimmed)) {
          content = content.replace(oldTrimmed, newCode.trim());
        } else {
          failedMatches++;
        }
      }
    }
    if (!anyMatches) {
      throw new Error("No valid search/replace blocks (<<<<...====...>>>>) found in edit_text.");
    }
    if (failedMatches > 0) {
      throw new Error(`Failed to apply ${failedMatches} edit blocks. The old code did not strictly match the file contents.`);
    }
    const edit = new vscode7.WorkspaceEdit();
    const fullRange = new vscode7.Range(
      document.positionAt(0),
      document.positionAt(document.getText().length)
    );
    edit.replace(uri, fullRange, content);
    const success = await vscode7.workspace.applyEdit(edit);
    if (!success) {
      throw new Error(`Failed to save edit to ${filePath}`);
    }
    await document.save();
    return {
      tool_call_id: toolCall.id,
      tool_name: toolCall.tool_name,
      output: `Successfully applied edits to ${filePath}`,
      is_error: false
    };
  }
};

// src/services/ToolLoopOrchestrator.ts
var ToolLoopOrchestrator = class {
  constructor(toolExecutor, payloadDispatcher) {
    this.toolExecutor = toolExecutor;
    this.payloadDispatcher = payloadDispatcher;
  }
  iterationCount = /* @__PURE__ */ new Map();
  async handleResponse(response, sessionId, modelAlias, stream, webviewView) {
    if (response.is_final) {
      webviewView.webview.postMessage({
        type: "responseComplete",
        response
      });
      this.iterationCount.set(sessionId, 0);
      return;
    }
    if (response.tool_calls && response.tool_calls.length > 0) {
      const currentIterations = this.iterationCount.get(sessionId) || 0;
      if (currentIterations >= 25) {
        webviewView.webview.postMessage({
          type: "error",
          requestId: response.request_id,
          message: "Safety limit reached: maximum tool loop iterations (25) exceeded.",
          isRetryable: false
        });
        return;
      }
      this.iterationCount.set(sessionId, currentIterations + 1);
      const toolStatusList = response.tool_calls.map((tc) => ({
        name: tc.tool_name,
        status: "running"
      }));
      webviewView.webview.postMessage({
        type: "toolExecution",
        tools: toolStatusList
      });
      const toolResults = [];
      for (let i = 0; i < response.tool_calls.length; i++) {
        const tc = response.tool_calls[i];
        const result = await this.toolExecutor.executeTool(tc);
        toolResults.push(result);
        toolStatusList[i].status = result.is_error ? "error" : "completed";
        webviewView.webview.postMessage({
          type: "toolExecution",
          tools: toolStatusList,
          lastResult: result
          // Let UI optionally preview output
        });
      }
      await this.payloadDispatcher.dispatch(
        "",
        // empty user message
        modelAlias,
        stream,
        sessionId,
        webviewView,
        toolResults
      );
    }
  }
};

// src/extension.ts
var daemonLifecycle;
function activate(context) {
  const outputChannel = vscode8.window.createOutputChannel("Nexus-Code");
  outputChannel.appendLine("Nexus-Code extension activating...");
  const keyVault = new KeyVault(context.secrets);
  const contextAggregator = new ContextAggregator();
  daemonLifecycle = new DaemonLifecycle(context.extensionUri);
  const payloadDispatcher = new PayloadDispatcher(keyVault, contextAggregator, daemonLifecycle);
  const diffProvider = new DiffContentProvider();
  const toolExecutor = new ToolExecutor(diffProvider);
  const toolOrchestrator = new ToolLoopOrchestrator(toolExecutor, payloadDispatcher);
  payloadDispatcher.setOrchestrator(toolOrchestrator);
  const webviewProvider = new WebviewProvider(context.extensionUri, keyVault, payloadDispatcher);
  context.subscriptions.push(
    vscode8.workspace.registerTextDocumentContentProvider(DiffContentProvider.scheme, diffProvider)
  );
  daemonLifecycle.startDaemon().catch((err) => {
    vscode8.window.showErrorMessage(`Failed to start Nexus-Code Daemon: ${err.message}`);
  });
  context.subscriptions.push(
    vscode8.window.registerWebviewViewProvider(WebviewProvider.viewType, webviewProvider)
  );
  const newChatCmd = vscode8.commands.registerCommand(
    "nexus-code.newChat",
    () => {
      outputChannel.appendLine("Command: New Chat");
      vscode8.commands.executeCommand("nexus-code-chat.focus");
    }
  );
  context.subscriptions.push(newChatCmd);
  const clearHistoryCmd = vscode8.commands.registerCommand(
    "nexus-code.clearHistory",
    () => {
      outputChannel.appendLine("Command: Clear History");
      vscode8.window.showInformationMessage("Nexus-Code: Chat history cleared.");
    }
  );
  context.subscriptions.push(clearHistoryCmd);
  const addApiKeyCmd = vscode8.commands.registerCommand(
    "nexus-code.addApiKey",
    async () => {
      outputChannel.appendLine("Command: Add API Key");
      const alias = await vscode8.window.showInputBox({
        prompt: 'Enter an alias for this API key (e.g., "openai-personal")',
        placeHolder: "openai-personal"
      });
      if (!alias) return;
      const key = await vscode8.window.showInputBox({
        prompt: `Enter the API key for "${alias}"`,
        password: true
      });
      if (!key) return;
      const provider = await vscode8.window.showQuickPick(["openai", "anthropic", "google", "deepseek", "ollama"], {
        placeHolder: "Select Provider"
      });
      if (!provider) return;
      await keyVault.storeKey(alias, key, provider);
      vscode8.window.showInformationMessage(`Nexus-Code: API key "${alias}" saved securely.`);
      outputChannel.appendLine(`API key alias "${alias}" registered for provider ${provider}.`);
    }
  );
  context.subscriptions.push(addApiKeyCmd);
  const restartDaemonCmd = vscode8.commands.registerCommand(
    "nexus-code.restartDaemon",
    async () => {
      outputChannel.appendLine("Command: Restart Daemon");
      vscode8.window.showInformationMessage("Nexus-Code: Daemon restart requested.");
      await daemonLifecycle.stopDaemon();
      await daemonLifecycle.startDaemon();
    }
  );
  context.subscriptions.push(restartDaemonCmd);
  context.subscriptions.push(outputChannel);
  outputChannel.appendLine("Nexus-Code extension activated successfully.");
}
function deactivate() {
  if (daemonLifecycle) {
    daemonLifecycle.stopDaemon();
  }
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  activate,
  deactivate
});
//# sourceMappingURL=extension.js.map
