const TELEGRAM_API_ORIGIN = "https://api.telegram.org";
const MAX_RETRY_AFTER_SECONDS = 3600;

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

async function retryAfter(response: Response): Promise<number | undefined> {
  const fromHeader = retryAfterFromHeader(response.headers.get("Retry-After"));
  if (fromHeader !== undefined) return fromHeader;
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "parameters" in body) {
      const parameters = body.parameters;
      if (typeof parameters === "object" && parameters !== null && "retry_after" in parameters) {
        const value = parameters.retry_after;
        if (typeof value === "number" && Number.isSafeInteger(value) && value >= 1) return Math.min(value, MAX_RETRY_AFTER_SECONDS);
      }
    }
  } catch {
    // A malformed Telegram error body does not change the bounded failure policy.
  }
  return undefined;
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
    if (response.ok) return;
    if (response.status === 429) throw new TelegramTransportError("rate_limited", 503, await retryAfter(response));
    throw new TelegramTransportError("upstream", 502);
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
