import * as vscode from 'vscode';
import * as crypto from 'crypto';
import { NexusResponse, ToolResult } from '../types/contracts';
import { ToolExecutor } from './ToolExecutor';
import { PayloadDispatcher } from './PayloadDispatcher';

export class ToolLoopOrchestrator {
  private iterationCount = new Map<string, number>();

  constructor(
    private readonly toolExecutor: ToolExecutor,
    private readonly payloadDispatcher: PayloadDispatcher
  ) {}

  public async handleResponse(
    response: NexusResponse,
    sessionId: string,
    modelAlias: string,
    stream: boolean,
    webviewView: vscode.WebviewView
  ): Promise<void> {
    
    // 1. If final, send to webview and stop.
    if (response.is_final) {
      webviewView.webview.postMessage({
        type: 'responseComplete',
        response
      });
      // Reset counter on successful completion
      this.iterationCount.set(sessionId, 0);
      return;
    }

    // 2. If it has tool calls, process them sequentially
    if (response.tool_calls && response.tool_calls.length > 0) {
      const currentIterations = this.iterationCount.get(sessionId) || 0;
      if (currentIterations >= 25) {
        webviewView.webview.postMessage({
          type: 'error',
          requestId: response.request_id,
          message: 'Safety limit reached: maximum tool loop iterations (25) exceeded.',
          isRetryable: false
        });
        return;
      }
      this.iterationCount.set(sessionId, currentIterations + 1);

      // Notify UI
      const toolStatusList = response.tool_calls.map(tc => ({
        name: tc.tool_name,
        status: 'running'
      }));
      webviewView.webview.postMessage({
        type: 'toolExecution',
        tools: toolStatusList
      });

      const toolResults: ToolResult[] = [];

      for (let i = 0; i < response.tool_calls.length; i++) {
        const tc = response.tool_calls[i];
        
        // Execute tool sequentially
        const result = await this.toolExecutor.executeTool(tc);
        toolResults.push(result);

        // Update UI for this tool
        toolStatusList[i].status = result.is_error ? 'error' : 'completed';
        webviewView.webview.postMessage({
          type: 'toolExecution',
          tools: toolStatusList,
          lastResult: result // Let UI optionally preview output
        });
      }

      // Re-dispatch with new Payload
      // Note: Dispatcher will fetch the active file path to construct the minimal context
      // when toolResults are passed, to satisfy schema requirements without re-reading huge context.
      await this.payloadDispatcher.dispatch(
        '', // empty user message
        modelAlias,
        stream,
        sessionId,
        webviewView,
        toolResults
      );
    }
  }
}
