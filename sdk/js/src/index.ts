/**
 * HlAB official JavaScript/TypeScript SDK — a single fetch-based client for
 * the Headless AI Agent Platform API. Zero runtime dependencies.
 *
 * Endpoint contract (mirrors sdk/python/hlab_client/client.py and
 * server/api/invoke.py, sessions.py, files.py):
 *   POST   {prefix}/invoke                 -> full response JSON
 *   POST   {prefix}/invoke/stream          -> SSE (answer_delta/status/answer/done/error)
 *   GET    {prefix}/sessions/               -> {total, offset, limit, items: [...]}
 *   GET    {prefix}/sessions/{id}/messages  -> {total, offset, limit, items: [...]}
 *   DELETE {prefix}/sessions/{id}           -> 204
 *   POST   {prefix}/files/upload            -> {file_id, filename, size, content_type, reference}
 *
 * Auth: X-API-Key header (an API key scoped for "invoke" — see docs/integration.md).
 *
 * Unlike the Python SDK there is only one client class here: JavaScript's
 * fetch API is inherently async/non-blocking, so there is no separate
 * "sync" variant to mirror HlabClient/AsyncHlabClient.
 */

const DEFAULT_API_PREFIX = "/api/v1";
const DEFAULT_TIMEOUT_MS = 120_000;

/** Thrown when the HlAB API returns a non-2xx response. */
export class HlabAPIError extends Error {
  readonly statusCode: number;
  readonly detail: unknown;

  constructor(statusCode: number, detail: unknown) {
    super(`HlAB API error ${statusCode}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    this.name = "HlabAPIError";
    this.statusCode = statusCode;
    this.detail = detail;
  }
}

/** A single parsed Server-Sent Event. */
export interface SSEEvent {
  event: string;
  data: unknown;
}

export interface InvokeOptions {
  sessionId?: string;
  userId?: string;
  formData?: Record<string, unknown>;
  /** Any other InvokeRequest field (intent, expand, clientMeta as client_meta, etc.) passed through verbatim. */
  [extra: string]: unknown;
}

export interface ListSessionsOptions {
  agentId?: string;
  userId?: string;
  limit?: number;
  offset?: number;
}

export interface GetMessagesOptions {
  limit?: number;
  offset?: number;
}

export interface HlabClientOptions {
  apiKey?: string;
  timeoutMs?: number;
  apiPrefix?: string;
  /** Override fetch (e.g. for tests, or non-browser/non-node environments). */
  fetchImpl?: typeof fetch;
}

function buildInvokePayload(
  agentId: string,
  message: string,
  options: InvokeOptions,
): Record<string, unknown> {
  const { sessionId, userId, formData, ...extra } = options;
  const payload: Record<string, unknown> = { agent_id: agentId, message };
  if (sessionId !== undefined) payload.session_id = sessionId;
  if (userId !== undefined) payload.user_id = userId;
  if (formData !== undefined) payload.form_data = formData;
  Object.assign(payload, extra);
  return payload;
}

// ---------------------------------------------------------------------------
// SSE parsing — a small incremental state machine shared by the canned-array
// parser (parseSSELines, unit-testable without any network) and the live
// ReadableStream parser (parseSSEStream). Mirrors _SSEAccumulator in the
// Python SDK so both SDKs implement identical framing rules:
//   - a blank line terminates the current event
//   - "event: <type>" sets the event type (default "message")
//   - one or more "data: <chunk>" lines join with "\n" and are JSON-decoded
//     when possible, else kept as the raw string
//   - lines starting with ":" are comments and are ignored
// ---------------------------------------------------------------------------

class SSEAccumulator {
  private eventType = "message";
  private dataLines: string[] = [];

  /** Feed one raw line; returns a completed event, or null if none yet. */
  feed(rawLine: string): SSEEvent | null {
    const line = rawLine.replace(/\r$/, "");
    if (line === "") {
      return this.flush();
    }
    if (line.startsWith(":")) {
      return null;
    }
    if (line.startsWith("event:")) {
      this.eventType = line.slice("event:".length).trim();
      return null;
    }
    if (line.startsWith("data:")) {
      this.dataLines.push(line.slice("data:".length).replace(/^ /, ""));
      return null;
    }
    return null;
  }

  /** Flush a trailing event that never received a final blank-line terminator. */
  flushFinal(): SSEEvent | null {
    return this.flush();
  }

  private flush(): SSEEvent | null {
    if (this.dataLines.length === 0) {
      this.eventType = "message";
      return null;
    }
    const raw = this.dataLines.join("\n");
    let data: unknown;
    try {
      data = JSON.parse(raw);
    } catch {
      data = raw;
    }
    const event: SSEEvent = { event: this.eventType, data };
    this.eventType = "message";
    this.dataLines = [];
    return event;
  }
}

/**
 * Parse a canned array (or any iterable) of raw SSE text lines into events.
 * Useful for unit tests with no live server / network involved.
 */
export function parseSSELines(lines: Iterable<string>): SSEEvent[] {
  const accumulator = new SSEAccumulator();
  const events: SSEEvent[] = [];
  for (const rawLine of lines) {
    const event = accumulator.feed(rawLine);
    if (event) events.push(event);
  }
  const trailing = accumulator.flushFinal();
  if (trailing) events.push(trailing);
  return events;
}

/** Parse a live fetch response body (ReadableStream<Uint8Array>) as SSE events. */
async function* parseSSEStream(body: ReadableStream<Uint8Array>): AsyncGenerator<SSEEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  const accumulator = new SSEAccumulator();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newlineIndex = buffer.indexOf("\n");
      while (newlineIndex !== -1) {
        const line = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 1);
        const event = accumulator.feed(line);
        if (event) yield event;
        newlineIndex = buffer.indexOf("\n");
      }
    }
    if (buffer.length > 0) {
      const event = accumulator.feed(buffer);
      if (event) yield event;
    }
    const trailing = accumulator.flushFinal();
    if (trailing) yield trailing;
  } finally {
    reader.releaseLock();
  }
}

/**
 * HlAB Headless Agent API client.
 *
 * @example
 * const client = new HlabClient("https://your-hlab-host", { apiKey: "..." });
 * const result = await client.invoke("sales_agent", "Hello");
 *
 * @example streaming
 * for await (const evt of client.invokeStream("sales_agent", "Hello")) {
 *   if (evt.event === "answer_delta") process.stdout.write((evt.data as any).text);
 * }
 */
export class HlabClient {
  private readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(baseUrl: string, options: HlabClientOptions = {}) {
    const prefix = options.apiPrefix ?? DEFAULT_API_PREFIX;
    this.baseUrl = baseUrl.replace(/\/+$/, "") + prefix;
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  private buildHeaders(extra?: Record<string, string>): Record<string, string> {
    const headers: Record<string, string> = { ...extra };
    if (this.apiKey) headers["X-API-Key"] = this.apiKey;
    return headers;
  }

  private async request(path: string, init: RequestInit = {}): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      return await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        headers: this.buildHeaders(init.headers as Record<string, string> | undefined),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  private async raiseForStatus(response: Response): Promise<void> {
    if (response.ok) return;
    let detail: unknown;
    try {
      const body: unknown = await response.json();
      detail = body && typeof body === "object" && "detail" in (body as Record<string, unknown>)
        ? (body as Record<string, unknown>).detail
        : body;
    } catch {
      detail = await response.text().catch(() => response.statusText);
    }
    throw new HlabAPIError(response.status, detail);
  }

  /** Send a message to an agent and get the full response (POST /invoke). */
  async invoke(agentId: string, message: string, options: InvokeOptions = {}): Promise<any> {
    const payload = buildInvokePayload(agentId, message, options);
    const response = await this.request("/invoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await this.raiseForStatus(response);
    return response.json();
  }

  /**
   * Stream a message to an agent (POST /invoke/stream). Yields
   * `{event, data}` pairs as SSE events arrive (answer_delta / status /
   * answer / done / error — see docs/integration.md for the full reference).
   */
  async *invokeStream(
    agentId: string,
    message: string,
    options: InvokeOptions = {},
  ): AsyncGenerator<SSEEvent> {
    const payload = buildInvokePayload(agentId, message, options);
    const response = await this.request("/invoke/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      await this.raiseForStatus(response);
      return;
    }
    if (!response.body) {
      throw new Error("Streaming response has no readable body in this environment");
    }
    yield* parseSSEStream(response.body);
  }

  /** List conversation sessions for the caller's tenant (GET /sessions/). */
  async listSessions(options: ListSessionsOptions = {}): Promise<any> {
    const params = new URLSearchParams();
    params.set("limit", String(options.limit ?? 50));
    params.set("offset", String(options.offset ?? 0));
    if (options.agentId) params.set("agent_id", options.agentId);
    if (options.userId) params.set("user_id", options.userId);
    const response = await this.request(`/sessions/?${params.toString()}`);
    await this.raiseForStatus(response);
    return response.json();
  }

  /** Chronological message history for a session (GET /sessions/{id}/messages). */
  async getMessages(sessionId: string, options: GetMessagesOptions = {}): Promise<any> {
    const params = new URLSearchParams();
    params.set("limit", String(options.limit ?? 100));
    params.set("offset", String(options.offset ?? 0));
    const response = await this.request(
      `/sessions/${encodeURIComponent(sessionId)}/messages?${params.toString()}`,
    );
    await this.raiseForStatus(response);
    return response.json();
  }

  /** Delete a session and its messages (DELETE /sessions/{id}). */
  async deleteSession(sessionId: string): Promise<void> {
    const response = await this.request(`/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    await this.raiseForStatus(response);
  }

  /** Upload a file for use as a workflow file-field value (POST /files/upload). */
  async uploadFile(file: Blob, filename?: string): Promise<any> {
    const form = new FormData();
    form.append("file", file, filename);
    const response = await this.request("/files/upload", { method: "POST", body: form });
    await this.raiseForStatus(response);
    return response.json();
  }
}
