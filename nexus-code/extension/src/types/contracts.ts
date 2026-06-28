/**
 * Nexus-Code shared data contracts.
 *
 * These TypeScript interfaces mirror the shared JSON Schema at
 * `shared/nexus_schema.json` and the Pydantic models in the daemon.
 * They are the single TypeScript source of truth for all data crossing
 * the localhost:8000 boundary.
 */

// ---------------------------------------------------------------------------
// Shared sub-types
// ---------------------------------------------------------------------------

/** Line/column position inside a text document. */
export interface CursorPosition {
  /** Zero-based line number. */
  line: number;
  /** Zero-based column number. */
  column: number;
}

/** Full snapshot of the currently focused editor tab. */
export interface ActiveFile {
  /** Absolute file path. */
  path: string;
  /** VS Code language identifier (e.g., "typescript", "python"). */
  language_id: string;
  /** Full text content of the file. */
  content: string;
  /** Cursor position at the moment the message was sent. */
  cursor_position: CursorPosition;
}

/**
 * Highlighted text range in the active file.
 * `null` when no text is selected.
 */
export interface Selection {
  /** The exact selected text. */
  text: string;
  /** Selection start line (zero-based). */
  start_line: number;
  /** Selection end line (zero-based). */
  end_line: number;
  /** Selection start column (zero-based). */
  start_column: number;
  /** Selection end column (zero-based). */
  end_column: number;
}

/** Snapshot of a single open editor tab (excluding the active file). */
export interface OpenTab {
  /** Absolute file path. */
  path: string;
  /** VS Code language identifier. */
  language_id: string;
  /** Full or truncated text content. */
  content: string;
}

/** Start/end positions for a diagnostic annotation. */
export interface DiagnosticRange {
  /** Range start position. */
  start: CursorPosition;
  /** Range end position. */
  end: CursorPosition;
}

/** Severity levels matching VS Code DiagnosticSeverity. */
export type DiagnosticSeverity = 'error' | 'warning' | 'information' | 'hint';

/** A single VS Code diagnostic entry for the active file. */
export interface Diagnostic {
  /** Human-readable diagnostic message. */
  message: string;
  /** Severity level. */
  severity: DiagnosticSeverity;
  /** Location range in the file. */
  range: DiagnosticRange;
  /** The language server or linter that produced this diagnostic. */
  source: string;
}

/**
 * All editor state captured at the moment the user sent their message.
 * Collected by the Context Aggregator in the Extension Host.
 */
export interface ContextBundle {
  /** Snapshot of the currently focused file. */
  active_file: ActiveFile;
  /** Highlighted text selection, or null if nothing is selected. */
  selection: Selection | null;
  /** Snapshots of open editor tabs (max 10, excluding active file). */
  open_tabs: OpenTab[];
  /** Workspace file tree to depth 3 (nested object). */
  workspace_structure: Record<string, unknown>;
  /** Unified diff of staged + unstaged changes, or empty string. */
  git_diff: string;
  /** Diagnostics for the active file (max 50). */
  diagnostics: Diagnostic[];
  /** Last 200 lines of the most recent terminal, or null. */
  terminal_snapshot: string | null;
  /** Approximate token count of the entire context bundle. */
  pre_compression_token_estimate: number;
  /** True when pre_compression_token_estimate exceeds the heavy threshold. */
  heavy_context_flag: boolean;
}

/** Result of a single tool execution returned to the daemon. */
export interface ToolResult {
  /** ID echoed from the model's tool call request. */
  tool_call_id: string;
  /** Name of the tool that was executed. */
  tool_name: string;
  /** Raw text output of the tool execution. */
  output: string;
  /** True if the tool execution failed. */
  is_error: boolean;
}

// ---------------------------------------------------------------------------
// NexusPayload — Request contract (Extension Host → Daemon)
// ---------------------------------------------------------------------------

/**
 * Master request contract sent from the VS Code Extension Host to the
 * local Python daemon on every user interaction.
 *
 * This is the only data structure permitted to cross the localhost:8000
 * boundary in the request direction.
 */
export interface NexusPayload {
  /** UUID for the chat tab session. Primary key for all session state. */
  session_id: string;
  /** UUID for this individual message. Used for deduplication and correlation. */
  request_id: string;
  /** ISO 8601 datetime of dispatch. Used for cache TTL and memory ordering. */
  timestamp: string;
  /** Model alias string passed to LiteLLM (e.g., "gpt-4o"). Opaque to TS. */
  model_alias: string;
  /** Whether to use SSE streaming for the response. */
  stream: boolean;
  /** Exact user input text. Never modified by any pipeline layer. */
  user_message: string;
  /** SecretStorage alias for the API key. NOT the key itself. */
  provider_key_alias: string;
  /** All captured editor state. */
  context_bundle: ContextBundle;
  /** Session ID reference for conversation history lookup. NOT the history. */
  history_ref: string;
  /** Tool execution results (null on first turn, populated on tool loop). */
  tool_results: ToolResult[] | null;
}

// ---------------------------------------------------------------------------
// NexusResponse — Response contract (Daemon → Extension Host)
// ---------------------------------------------------------------------------

/** A single tool invocation requested by the LLM. */
export interface ToolCall {
  /** Unique tool call ID to echo back in the next request's tool_results. */
  id: string;
  /** Name of the function the model wants to call. */
  tool_name: string;
  /** Structured input parameters for the tool. */
  arguments: Record<string, unknown>;
}

/** Token usage as reported by the LLM provider. */
export interface UsageStats {
  /** Tokens in the prompt sent to the provider. */
  prompt_tokens: number;
  /** Tokens in the model's completion. */
  completion_tokens: number;
  /** Total tokens (prompt + completion). */
  total_tokens: number;
}

/** Structured error from any layer of the daemon pipeline. */
export interface ResponseError {
  /** Machine-readable error code (e.g., "AUTH_FAILURE"). */
  code: string;
  /** Human-readable error description. */
  message: string;
  /** Which pipeline layer failed. */
  layer: 'cache' | 'memory' | 'compression' | 'router' | 'provider' | 'tool_loop';
  /** Whether the Extension Host should offer a retry option. */
  is_retryable: boolean;
}

/**
 * Master response contract returned from the daemon to the Extension Host.
 *
 * For streaming: the final SSE chunk carries this full schema.
 * For non-streaming: the entire HTTP body is this schema.
 */
export interface NexusResponse {
  /** Echo of the request's request_id for correlation. */
  request_id: string;
  /** Echo of the request's session_id. */
  session_id: string;
  /** Final LLM text output. Null during streaming or when tool_calls present. */
  response_text: string | null;
  /** Tool call requests from the model, or null for final text responses. */
  tool_calls: ToolCall[] | null;
  /** True when this response carries a final answer (no pending tools). */
  is_final: boolean;
  /** True if served from the semantic cache. */
  cache_hit: boolean;
  /** "L1" (session) or "L2" (workspace) cache tier, null on miss. */
  cache_tier: 'L1' | 'L2' | null;
  /** Count of MRAgent memory graph nodes used. Zero on cache hits. */
  memory_nodes_retrieved: number;
  /** Estimated token count of the prompt before compression. */
  pre_compression_tokens: number;
  /** Actual token count of the prompt after compression. */
  post_compression_tokens: number;
  /** 1 − (post / pre). 0.70 means 70% of tokens were eliminated. */
  compression_ratio: number;
  /** Token usage as reported by the LLM provider. */
  usage: UsageStats;
  /** Actual model identifier string after fallback resolution. */
  model_used: string;
  /** Estimated cost of this request in USD. */
  cost_estimate_usd: number;
  /** Structured error if any pipeline layer failed, null on success. */
  error: ResponseError | null;
}

// ---------------------------------------------------------------------------
// Webview ↔ Extension Host message types
// ---------------------------------------------------------------------------

/** Messages sent from the Webview to the Extension Host. */
export type WebviewToHostMessage =
  | { type: 'sendMessage'; text: string; modelAlias: string; stream: boolean }
  | { type: 'saveApiKey'; alias: string; key: string; provider: string }
  | { type: 'deleteApiKey'; alias: string }
  | { type: 'updateSetting'; key: string; value: unknown }
  | { type: 'newChat' }
  | { type: 'webviewReady' };

/** Messages sent from the Extension Host to the Webview. */
export type HostToWebviewMessage =
  | {
      type: 'initialize';
      models: string[];
      selectedModel: string;
      settings: Record<string, unknown>;
      keyAliases: string[];
    }
  | { type: 'streamDelta'; requestId: string; delta: string }
  | { type: 'responseComplete'; response: NexusResponse }
  | { type: 'error'; requestId: string; message: string; isRetryable: boolean }
  | { type: 'statusUpdate'; status: string };
