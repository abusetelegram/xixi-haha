const TELEGRAM_API_ORIGIN = "https://api.telegram.org";
const MAX_RETRY_AFTER_SECONDS = 3600;
export const TELEGRAM_RESPONSE_BODY_LIMIT = 64 * 1024;

export type TelegramMethod = "sendMessage" | "answerInlineQuery";
export type TelegramTransportFailure = "rate_limited" | "upstream" | "timeout";

export class TelegramTransportError extends Error {
  constructor(
    readonly failure: TelegramTransportFailure,
    readonly status: number,
    readonly retryAfterSeconds?: number,
  ) {
    super(`Telegram transport failed: ${failure}`);
    this.name = "TelegramTransportError";
  }
}

export interface TelegramTransportOptions {
  fetch?: typeof fetch;
  timeoutMs?: number;
}

function retryAfterFromHeader(value: string | null): number | undefined {
  if (value === null || !/^[0-9]+$/u.test(value)) return undefined;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 1) return undefined;
  return Math.min(parsed, MAX_RETRY_AFTER_SECONDS);
}

function responseObject(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function retryAfterFromEnvelope(envelope: Record<string, unknown> | undefined): number | undefined {
  const parameters = responseObject(envelope?.parameters);
  const value = parameters?.retry_after;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) return undefined;
  return Math.min(value, MAX_RETRY_AFTER_SECONDS);
}

async function readBoundedResponse(response: Response, signal: AbortSignal): Promise<unknown> {
  if (response.body === null) return undefined;

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let rejectAborted: ((reason: DOMException) => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    rejectAborted = reject;
  });
  const onAbort = () => rejectAborted?.(new DOMException("aborted", "AbortError"));
  signal.addEventListener("abort", onAbort, { once: true });

  try {
    if (signal.aborted) onAbort();
    while (true) {
      const { done, value } = await Promise.race([reader.read(), aborted]);
      if (done) break;
      total += value.byteLength;
      if (total > TELEGRAM_RESPONSE_BODY_LIMIT) throw new RangeError("Telegram response body too large");
      chunks.push(value);
    }
  } finally {
    signal.removeEventListener("abort", onAbort);
    if (signal.aborted || total > TELEGRAM_RESPONSE_BODY_LIMIT) {
      void reader.cancel().catch(() => undefined);
    } else {
      reader.releaseLock();
    }
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  if (bytes.byteLength === 0) return undefined;
  try {
    return JSON.parse(new TextDecoder().decode(bytes)) as unknown;
  } catch {
    return undefined;
  }
}

/** Makes one bounded request to one of the two allowlisted Bot API methods. It never retries. */
export async function callTelegram(
  botToken: string,
  method: TelegramMethod,
  payload: unknown,
  options: TelegramTransportOptions = {},
): Promise<void> {
  const fetcher = options.fetch ?? fetch;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 5_000);
  try {
    const response = await fetcher(`${TELEGRAM_API_ORIGIN}/bot${botToken}/${method}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });

    let envelope: Record<string, unknown> | undefined;
    try {
      envelope = responseObject(await readBoundedResponse(response, controller.signal));
    } catch (caught) {
      if (controller.signal.aborted || (caught instanceof DOMException && caught.name === "AbortError")) throw caught;
      if (response.status !== 429) throw new TelegramTransportError("upstream", 502);
    }

    const retryAfter = retryAfterFromHeader(response.headers.get("Retry-After")) ?? retryAfterFromEnvelope(envelope);
    if (response.status === 429 || (envelope?.ok === false && envelope.error_code === 429)) {
      throw new TelegramTransportError("rate_limited", 503, retryAfter);
    }
    if (!response.ok || envelope?.ok !== true) throw new TelegramTransportError("upstream", 502);
  } catch (caught) {
    if (caught instanceof TelegramTransportError) throw caught;
    if (controller.signal.aborted || (caught instanceof DOMException && caught.name === "AbortError")) {
      throw new TelegramTransportError("timeout", 504);
    }
    throw new TelegramTransportError("upstream", 502);
  } finally {
    clearTimeout(timeout);
  }
}
