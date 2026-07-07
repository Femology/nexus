import * as vscode from 'vscode';
import { KeyVault } from './KeyVault';
import { ContextAggregator } from './ContextAggregator';
import { getSettings } from './ExtensionConfig';
import { NexusPayload, NexusResponse, ToolResult } from '../types/contracts';
import type { ToolLoopOrchestrator } from './ToolLoopOrchestrator';
import type { DaemonLifecycle } from './DaemonLifecycle';

export class PayloadDispatcher {
  private orchestrator?: ToolLoopOrchestrator;

  constructor(
    private readonly keyVault: KeyVault,
    private readonly contextAggregator: ContextAggregator,
    private readonly daemonLifecycle: DaemonLifecycle
  ) {}

  public setOrchestrator(orchestrator: ToolLoopOrchestrator) {
    this.orchestrator = orchestrator;
  }

    public async dispatch(
    userMessage: string,
    modelAlias: string,
    stream: boolean,
    sessionId: string,
    webviewView: vscode.WebviewView,
    toolResults?: ToolResult[],
    attempt: number = 1
  ): Promise<void> {
    try {
      const aliases = await this.keyVault.listAliases();
      if (aliases.length === 0) {
        throw new Error('No API keys configured. Please add an API key in Settings.');
      }
      const providerKeyAlias = aliases[0].alias;
      const apiKey = await this.keyVault.getKey(providerKeyAlias);
      
      if (!apiKey) {
        throw new Error(`API key not found for alias: ${providerKeyAlias}`);
      }

      let context_bundle;
      if (toolResults && toolResults.length > 0) {
         const editor = vscode.window.activeTextEditor;
         context_bundle = {
            active_file: {
               path: editor ? editor.document.uri.fsPath : '',
               language_id: 'plaintext',
               content: '',
               cursor_position: { line: 0, column: 0 }
            },
            selection: null,
            open_tabs: [],
            workspace_structure: {},
            git_diff: '',
            diagnostics: [],
            terminal_snapshot: null,
            pre_compression_token_estimate: 0,
            heavy_context_flag: false
         };
      } else {
         context_bundle = await this.contextAggregator.collectContext();
      }

      const payload: NexusPayload = {
        session_id: sessionId,
        request_id: 'req-' + Date.now(),
        timestamp: new Date().toISOString(),
        model_alias: modelAlias,
        stream,
        user_message: userMessage,
        provider_key_alias: providerKeyAlias,
        context_bundle,
        history_ref: sessionId,
        tool_results: toolResults || null,
      };

      const port = this.daemonLifecycle.getPort();
      const secret = this.daemonLifecycle.getSecret();
      
      if (!port || !secret) {
          throw new Error('Daemon is not running or lockfile is missing.');
      }

      const url = `http://localhost:${port}/v1/chat`;
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 15000);

      let response: Response;
      try {
        response = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${apiKey}`,
            'X-Nexus-Secret': secret
          },
          body: JSON.stringify(payload),
          signal: controller.signal
        });
        clearTimeout(timeoutId);
      } catch (e: any) {
        clearTimeout(timeoutId);
        if (e.name === 'AbortError') {
           throw new Error('Daemon is slow to respond (>15s timeout). Please ensure the daemon is healthy.');
        }
        if ((e.cause?.code === 'ECONNREFUSED' || e.message.includes('fetch failed')) && attempt < 3) {
            console.log(`Connection refused, retrying ${attempt}/3...`);
            webviewView.webview.postMessage({
              type: 'streamDelta',
              requestId: payload.request_id,
              delta: `\n*[Starting daemon... retry ${attempt}/3]*\n`
            });
            await new Promise(r => setTimeout(r, 2000));
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
        const reader = (response.body as any).getReader();
        const decoder = new TextDecoder('utf-8');
        let done = false;
        let finalResponse: NexusResponse | undefined = undefined;

        try {
            while (!done) {
              const { value, done: readerDone } = await reader.read();
              done = readerDone;
              if (value) {
                const chunk = decoder.decode(value, { stream: true });
                const lines = chunk.split('\n');
                
                for (const line of lines) {
                  if (line.startsWith('data: ')) {
                    const data = line.slice(6).trim();
                    if (data === '[DONE]') {
                       // handled via is_final
                    } else if (data) {
                      try {
                        const parsed = JSON.parse(data);
                        if (parsed.response_text || parsed.delta) {
                           webviewView.webview.postMessage({
                              type: 'streamDelta',
                              requestId: payload.request_id,
                              delta: parsed.delta || parsed.response_text || ''
                           });
                        }
                        if (parsed.is_final || (parsed.tool_calls && parsed.tool_calls.length > 0)) {
                           finalResponse = parsed;
                        }
                      } catch (e) {
                        console.error('Failed to parse SSE data', e);
                      }
                    }
                  }
                }
              }
            }
        } catch (streamError) {
            console.error('Stream reading error', streamError);
            webviewView.webview.postMessage({
                type: 'streamDelta',
                requestId: payload.request_id,
                delta: '\n\n**[Connection lost mid-stream]**\n'
            });
            return;
        }
        
        if (finalResponse && this.orchestrator) {
           await this.orchestrator.handleResponse(finalResponse, sessionId, modelAlias, stream, webviewView);
        }
        
      } else {
        const jsonResponse = await response.json() as NexusResponse;
        if (this.orchestrator) {
           await this.orchestrator.handleResponse(jsonResponse, sessionId, modelAlias, stream, webviewView);
        } else {
           webviewView.webview.postMessage({
             type: 'responseComplete',
             response: jsonResponse
           });
        }
      }

    } catch (e: any) {
      console.error('PayloadDispatcher error', e);
      let message = e.message;
      if (e.cause?.code === 'ECONNREFUSED' || message.includes('fetch failed')) {
        message = 'Daemon connection refused. Is the Nexus-Code optimization daemon running?';
      }
      webviewView.webview.postMessage({
        type: 'error',
        requestId: 'internal',
        message,
        isRetryable: true
      });
    }
  }
}
