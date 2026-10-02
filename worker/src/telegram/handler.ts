import { AssetLoadCapacityError, AssetUnavailableError, AssetValidationError, EmptyCorpusError } from "../errors";
import type { Quote } from "../types";
import { formatQuoteHtml, stableInlineResultId } from "./format";
import { callTelegram, TelegramTransportError, type TelegramTransportOptions } from "./transport";

export const TELEGRAM_WEBHOOK_PATH = "/telegram/webhook";
export const TELEGRAM_BODY_LIMIT = 64 * 1024;
const SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token";

export interface TelegramQuoteService {
  randomQuote(selection?: "paragraph" | "article"): Promise<Quote>;
}

export interface TelegramConfig {
  botToken: string | undefined;
  webhookSecret: string | undefined;
  botUsername: string | undefined;
}

export interface TelegramDependencies extends TelegramTransportOptions {
  quoteService: TelegramQuoteService;
}

type ChatId = number | string;

interface TelegramMessage {
  message_id: number;
  chat: { id: ChatId };
  text?: string;
}

interface TelegramInlineQuery {
  id: string;
}

interface TelegramUpdate {
  update_id: number;
  message?: TelegramMessage;
  inline_query?: TelegramInlineQuery;
}

function jsonError(status: number, error: string, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify({ error }), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...headers } });
}

interface ValidTelegramConfig {
  botToken: string;
  webhookSecret: string;
  botUsername: string;
}

function validConfig(config: TelegramConfig): config is ValidTelegramConfig {
  return typeof config.botToken === "string" && /^[0-9]+:[A-Za-z0-9_-]{20,}$/u.test(config.botToken)
    && typeof config.webhookSecret === "string" && /^[A-Za-z0-9_-]{32,256}$/u.test(config.webhookSecret)
    && typeof config.botUsername === "string" && /^[A-Za-z][A-Za-z0-9_]{4,31}$/u.test(config.botUsername);
}

async function secretsEqual(actual: string | null, expected: string): Promise<boolean> {
  if (actual === null) return false;
  const encoder = new TextEncoder();
  const [actualHash, expectedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(actual)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const left = new Uint8Array(actualHash);
  const right = new Uint8Array(expectedHash);
  let difference = 0;
  for (let i = 0; i < left.length; i += 1) difference |= left[i]! ^ right[i]!;
  return difference === 0;
}

async function readBoundedJson(request: Request): Promise<unknown> {
  const declaredLength = request.headers.get("Content-Length");
  if (declaredLength !== null && /^[0-9]+$/u.test(declaredLength) && Number(declaredLength) > TELEGRAM_BODY_LIMIT) {
    throw new RangeError("body_too_large");
  }
  if (request.body === null) throw new SyntaxError("missing body");

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > TELEGRAM_BODY_LIMIT) {
      await reader.cancel().catch(() => undefined);
      throw new RangeError("body_too_large");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

function asUpdate(value: unknown): TelegramUpdate | undefined {
  if (typeof value !== "object" || value === null || !("update_id" in value) || !Number.isSafeInteger(value.update_id)) return undefined;
  const update: TelegramUpdate = { update_id: value.update_id as number };
  if ("message" in value && value.message !== undefined) {
    const message = value.message;
    if (typeof message !== "object" || message === null || !("message_id" in message) || !Number.isSafeInteger(message.message_id)
      || !("chat" in message) || typeof message.chat !== "object" || message.chat === null || !("id" in message.chat)
      || (typeof message.chat.id !== "number" && typeof message.chat.id !== "string")
      || ("text" in message && typeof message.text !== "string")) return undefined;
    const parsedMessage: TelegramMessage = { message_id: message.message_id as number, chat: { id: message.chat.id } };
    if ("text" in message) parsedMessage.text = message.text as string;
    update.message = parsedMessage;
  }
  if ("inline_query" in value && value.inline_query !== undefined) {
    const inline = value.inline_query;
    if (typeof inline !== "object" || inline === null || !("id" in inline) || typeof inline.id !== "string" || inline.id.length === 0) return undefined;
    update.inline_query = { id: inline.id };
  }
  return update;
}

function addressedCommand(text: string, ownUsername: string): "start" | "yiyan" | undefined {
  const match = /^\/(start|yiyan)(?:@([A-Za-z0-9_]+))?(?:\s|$)/iu.exec(text);
  if (match === null) return undefined;
  const addressedUsername = match[2];
  if (addressedUsername !== undefined && addressedUsername.toLowerCase() !== ownUsername.toLowerCase()) return undefined;
  return match[1]!.toLowerCase() as "start" | "yiyan";
}

function inlineTitle(quote: Quote): string {
  const title = quote.article.title || `文章 ${quote.article.id}`;
  return Array.from(title).slice(0, 256).join("");
}

export function createTelegramHandler(dependencies: TelegramDependencies): (request: Request, config: TelegramConfig) => Promise<Response> {
  return async (request, config) => {
    if (request.method !== "POST") return jsonError(405, "method_not_allowed", { Allow: "POST" });
    if (!validConfig(config)) return jsonError(503, "telegram_not_configured");
    if (!(await secretsEqual(request.headers.get(SECRET_HEADER), config.webhookSecret))) return jsonError(401, "unauthorized");

    let rawUpdate: unknown;
    try {
      rawUpdate = await readBoundedJson(request);
    } catch (caught) {
      if (caught instanceof RangeError) return jsonError(413, "payload_too_large");
      return jsonError(400, "malformed_update");
    }
    const update = asUpdate(rawUpdate);
    if (update === undefined) return jsonError(400, "malformed_update");

    try {
      if (update.message !== undefined && typeof update.message.text === "string"
        && addressedCommand(update.message.text, config.botUsername) !== undefined) {
        const quote = await dependencies.quoteService.randomQuote("paragraph");
        await callTelegram(config.botToken, "sendMessage", {
          chat_id: update.message.chat.id,
          text: formatQuoteHtml(quote),
          parse_mode: "HTML",
          link_preview_options: { is_disabled: true },
          reply_parameters: { message_id: update.message.message_id, allow_sending_without_reply: true },
        }, dependencies);
      } else if (update.inline_query !== undefined) {
        const quote = await dependencies.quoteService.randomQuote("paragraph");
        const text = formatQuoteHtml(quote);
        await callTelegram(config.botToken, "answerInlineQuery", {
          inline_query_id: update.inline_query.id,
          is_personal: true,
          cache_time: 0,
          results: [{
            type: "article",
            id: stableInlineResultId(quote),
            title: inlineTitle(quote),
            input_message_content: { message_text: text, parse_mode: "HTML", link_preview_options: { is_disabled: true } },
          }],
        }, dependencies);
      }
      return new Response(null, { status: 200 });
    } catch (caught) {
      if (caught instanceof AssetValidationError || caught instanceof AssetUnavailableError
        || caught instanceof AssetLoadCapacityError || caught instanceof EmptyCorpusError) {
        return jsonError(503, "assets_unavailable");
      }
      if (caught instanceof TelegramTransportError) {
        const headers = caught.retryAfterSeconds === undefined ? {} : { "Retry-After": String(caught.retryAfterSeconds) };
        return jsonError(caught.status, caught.failure, headers);
      }
      return jsonError(500, "internal_error");
    }
  };
}
