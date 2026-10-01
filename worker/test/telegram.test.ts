import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { AssetLoadCapacityError } from "../src/errors";
import { formatQuoteHtml, logicalHtmlTextLengthForTest, stableInlineResultId, TELEGRAM_MESSAGE_LIMIT } from "../src/telegram/format";
import { createTelegramHandler, TELEGRAM_BODY_LIMIT, type TelegramConfig } from "../src/telegram/handler";
import type { Quote } from "../src/types";
import { createWorkerHandler } from "../src/worker";

const GENERATED_ASSETS = resolve(import.meta.dirname, "generated-assets");
const SECRET = "s".repeat(40);
const TOKEN = `12345:${"t".repeat(24)}`;
const CONFIG: TelegramConfig = { botToken: TOKEN, webhookSecret: SECRET, botUsername: "xixi_haha_bot" };
const QUOTE: Quote = {
  quote: "  <exact & words> 😀  ",
  paragraphIndex: 2,
  article: { id: "40140589", title: "A & <title>", date: "1966-01-01", author: "", editor: "" },
  sourceUrl: "http://jhsjk.people.cn/article/40140589",
  selection: "paragraph",
  corpus: { sourceSha: "a".repeat(40), dataSha: "b".repeat(40) },
};

function webhook(update: unknown, init: RequestInit = {}): Request {
  return new Request("https://worker.example/telegram/webhook", {
    method: "POST",
    headers: { "X-Telegram-Bot-Api-Secret-Token": SECRET, ...init.headers },
    body: JSON.stringify(update),
  });
}

function message(text: string, updateId = 1) {
  return { update_id: updateId, message: { message_id: 9, chat: { id: -1001 }, text } };
}

function localAssets(): Fetcher {
  return { fetch: async (input: RequestInfo | URL) => {
    const pathname = new URL(input instanceof Request ? input.url : input.toString()).pathname;
    try {
      return new Response(new Uint8Array(await readFile(resolve(GENERATED_ASSETS, `.${pathname}`))));
    } catch {
      return new Response("missing", { status: 404 });
    }
  } } as Fetcher;
}

function mockDependencies(response = new Response(JSON.stringify({ ok: true }))) {
  const randomQuote = vi.fn(async () => QUOTE);
  const telegramFetch = vi.fn<typeof fetch>(async (_input, _init) => response);
  return { randomQuote, telegramFetch, handler: createTelegramHandler({ quoteService: { randomQuote }, fetch: telegramFetch }) };
}

describe("Telegram webhook", () => {
  it("rejects methods, missing config, and bad auth before reading JSON", async () => {
    const { handler, randomQuote, telegramFetch } = mockDependencies();
    const wrongMethod = await handler(new Request("https://worker.example/telegram/webhook"), CONFIG);
    expect(wrongMethod.status).toBe(405);
    expect(wrongMethod.headers.get("allow")).toBe("POST");

    const missing = await handler(webhook({ nope: true }), { botToken: undefined, webhookSecret: undefined, botUsername: undefined });
    expect(missing.status).toBe(503);
    const unauthorized = await handler(new Request("https://worker.example/telegram/webhook", {
      method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": "wrong" }, body: "not json",
    }), CONFIG);
    expect(unauthorized.status).toBe(401);
    expect(randomQuote).not.toHaveBeenCalled();
    expect(telegramFetch).not.toHaveBeenCalled();
  });

  it.each([
    ["bot token", { botToken: "https://evil.example/token", webhookSecret: SECRET, botUsername: "xixi_haha_bot" }],
    ["short webhook secret", { botToken: TOKEN, webhookSecret: "short", botUsername: "xixi_haha_bot" }],
    ["unsafe webhook secret", { botToken: TOKEN, webhookSecret: `${"s".repeat(39)}!`, botUsername: "xixi_haha_bot" }],
    ["bot username", { botToken: TOKEN, webhookSecret: SECRET, botUsername: "@xixi_haha_bot" }],
  ] satisfies [string, TelegramConfig][])("rejects unsafe %s configuration before reading the body", async (_label, config) => {
    const { handler, randomQuote, telegramFetch } = mockDependencies();
    const response = await handler(new Request("https://worker.example/telegram/webhook", {
      method: "POST",
      headers: { "X-Telegram-Bot-Api-Secret-Token": SECRET },
      body: "not json",
    }), config);
    expect(response.status).toBe(503);
    expect(randomQuote).not.toHaveBeenCalled();
    expect(telegramFetch).not.toHaveBeenCalled();
  });

  it("returns 400 for malformed updates and enforces the streamed body cap", async () => {
    const { handler } = mockDependencies();
    expect((await handler(new Request("https://worker.example/telegram/webhook", {
      method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": SECRET }, body: "{",
    }), CONFIG)).status).toBe(400);
    const oversized = "x".repeat(TELEGRAM_BODY_LIMIT + 1);
    const response = await handler(new Request("https://worker.example/telegram/webhook", {
      method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": SECRET }, body: oversized,
    }), CONFIG);
    expect(response.status).toBe(413);
  });

  it.each(["/start", "/yiyan", "/YIYAN@XIXI_HAHA_BOT extra"])("handles addressed command %s with reply context", async (command) => {
    const { handler, randomQuote, telegramFetch } = mockDependencies();
    expect((await handler(webhook(message(command)), CONFIG)).status).toBe(200);
    expect(randomQuote).toHaveBeenCalledExactlyOnceWith("paragraph");
    expect(telegramFetch).toHaveBeenCalledTimes(1);
    const [url, init] = telegramFetch.mock.calls[0]!;
    expect(url).toBe(`https://api.telegram.org/bot${TOKEN}/sendMessage`);
    const payload = JSON.parse(String(init?.body));
    expect(payload).toMatchObject({
      chat_id: -1001,
      parse_mode: "HTML",
      reply_parameters: { message_id: 9, allow_sending_without_reply: true },
    });
    expect(payload.text).toContain("  &lt;exact &amp; words&gt; 😀  ");
    expect(payload.text).toContain("来源：");
  });

  it.each(["hello", "random keyword", "/yiyan@other_bot", "/unknown@xixi_haha_bot"])("ignores unsupported text %s", async (text) => {
    const { handler, randomQuote, telegramFetch } = mockDependencies();
    expect((await handler(webhook(message(text)), CONFIG)).status).toBe(200);
    expect(randomQuote).not.toHaveBeenCalled();
    expect(telegramFetch).not.toHaveBeenCalled();
  });

  it.each([
    ["photo", { update_id: 20, message: { message_id: 9, chat: { id: -1001 }, photo: [{ file_id: "photo", width: 1, height: 1 }] } }],
    ["sticker", { update_id: 21, message: { message_id: 9, chat: { id: -1001 }, sticker: { file_id: "sticker", width: 1, height: 1 } } }],
    ["location", { update_id: 22, message: { message_id: 9, chat: { id: -1001 }, location: { latitude: 1, longitude: 2 } } }],
    ["service", { update_id: 23, message: { message_id: 9, chat: { id: -1001 }, new_chat_title: "renamed" } }],
    ["callback root update", { update_id: 24, callback_query: { id: "callback" } }],
    ["poll root update", { update_id: 25, poll: { id: "poll" } }],
  ])("acknowledges unsupported valid %s updates without quote or transport calls", async (_label, update) => {
    const { handler, randomQuote, telegramFetch } = mockDependencies();
    expect((await handler(webhook(update), CONFIG)).status).toBe(200);
    expect(randomQuote).not.toHaveBeenCalled();
    expect(telegramFetch).not.toHaveBeenCalled();
  });

  it.each([
    ["update_id", { update_id: "1" }],
    ["message object", { update_id: 30, message: null }],
    ["message_id", { update_id: 31, message: { message_id: "9", chat: { id: -1001 } } }],
    ["chat", { update_id: 32, message: { message_id: 9, chat: null } }],
    ["chat id", { update_id: 33, message: { message_id: 9, chat: { id: null } } }],
    ["optional text", { update_id: 34, message: { message_id: 9, chat: { id: -1001 }, text: 123 } }],
    ["inline query id", { update_id: 35, inline_query: { id: 123 } }],
  ])("rejects malformed known %s fields", async (_label, update) => {
    const { handler, randomQuote, telegramFetch } = mockDependencies();
    expect((await handler(webhook(update), CONFIG)).status).toBe(400);
    expect(randomQuote).not.toHaveBeenCalled();
    expect(telegramFetch).not.toHaveBeenCalled();
  });

  it("answers valid inline queries with a stable result ID and formatted source", async () => {
    const first = mockDependencies();
    const update = { update_id: 7, inline_query: { id: "inline-1", from: { id: 3 }, query: "anything", offset: "" } };
    expect((await first.handler(webhook(update), CONFIG)).status).toBe(200);
    const [url, init] = first.telegramFetch.mock.calls[0]!;
    expect(url).toBe(`https://api.telegram.org/bot${TOKEN}/answerInlineQuery`);
    const payload = JSON.parse(String(init?.body));
    expect(payload.inline_query_id).toBe("inline-1");
    expect(payload.results[0].id).toBe(stableInlineResultId(QUOTE));
    expect(payload.results[0].input_message_content.message_text).toContain("来源：");
  });

  it("propagates rate limiting once with bounded Retry-After and maps other failures", async () => {
    const limited = mockDependencies(new Response(JSON.stringify({ parameters: { retry_after: 99999 } }), { status: 429 }));
    const limitedResponse = await limited.handler(webhook(message("/yiyan")), CONFIG);
    expect(limitedResponse.status).toBe(503);
    expect(limitedResponse.headers.get("retry-after")).toBe("3600");
    expect(limited.telegramFetch).toHaveBeenCalledTimes(1);

    const failed = mockDependencies(new Response("bad gateway", { status: 500 }));
    expect((await failed.handler(webhook(message("/start")), CONFIG)).status).toBe(502);
    expect(failed.telegramFetch).toHaveBeenCalledTimes(1);
  });

  it("times out one stalled Bot API call without retrying", async () => {
    const randomQuote = vi.fn(async () => QUOTE);
    const telegramFetch = vi.fn<typeof fetch>(async (_input, init) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    }));
    const handler = createTelegramHandler({ quoteService: { randomQuote }, fetch: telegramFetch, timeoutMs: 1 });
    expect((await handler(webhook(message("/yiyan")), CONFIG)).status).toBe(504);
    expect(telegramFetch).toHaveBeenCalledTimes(1);
  });

  it("maps core capacity to 503 without selecting a fallback quote", async () => {
    const randomQuote = vi.fn(async () => { throw new AssetLoadCapacityError("busy"); });
    const telegramFetch = vi.fn();
    const handler = createTelegramHandler({ quoteService: { randomQuote }, fetch: telegramFetch });
    expect((await handler(webhook(message("/yiyan")), CONFIG)).status).toBe(503);
    expect(randomQuote).toHaveBeenCalledTimes(1);
    expect(telegramFetch).not.toHaveBeenCalled();
  });

  it("wires a configured Worker to mocked Telegram and the shared quote service", async () => {
    const telegramFetch = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ ok: true })));
    const worker = createWorkerHandler({
      ASSETS: localAssets(), BOT_TOKEN: TOKEN, TELEGRAM_WEBHOOK_SECRET: SECRET, BOT_USERNAME: "xixi_haha_bot",
    }, telegramFetch);
    const response = await worker(webhook(message("/yiyan")));
    expect(response.status).toBe(200);
    expect(response.headers.get("access-control-allow-origin")).toBeNull();
    expect(telegramFetch).toHaveBeenCalledTimes(1);
    const payload = JSON.parse(String(telegramFetch.mock.calls[0]![1]?.body));
    expect(payload.text).toMatch(/\n\n来源：<a href=/u);
  });

  it("routes non-text messages through the real Worker webhook and acknowledges without loading assets or calling Telegram", async () => {
    const assetFetch = vi.fn(async () => new Response("unexpected"));
    const telegramFetch = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ ok: true })));
    const worker = createWorkerHandler({
      ASSETS: { fetch: assetFetch } as unknown as Fetcher,
      BOT_TOKEN: TOKEN,
      TELEGRAM_WEBHOOK_SECRET: SECRET,
      BOT_USERNAME: "xixi_haha_bot",
    }, telegramFetch);
    const response = await worker(webhook({
      update_id: 40,
      message: { message_id: 9, chat: { id: -1001 }, photo: [{ file_id: "photo", width: 1, height: 1 }] },
    }));
    expect(response.status).toBe(200);
    expect(response.headers.get("access-control-allow-origin")).toBeNull();
    expect(assetFetch).not.toHaveBeenCalled();
    expect(telegramFetch).not.toHaveBeenCalled();
  });

  it("is routed before the CORS API and fails closed when secrets are absent", async () => {
    const assets = { fetch: vi.fn(async () => new Response("asset")) } as unknown as Fetcher;
    const worker = createWorkerHandler({ ASSETS: assets, BOT_USERNAME: "xixi_haha_bot" }, vi.fn());
    const response = await worker(webhook(message("/start")));
    expect(response.status).toBe(503);
    expect(response.headers.get("access-control-allow-origin")).toBeNull();
    expect(assets.fetch).not.toHaveBeenCalled();
  });
});

describe("Telegram HTML formatting", () => {
  it("preserves an in-limit exact paragraph while escaping HTML", () => {
    const formatted = formatQuoteHtml(QUOTE);
    expect(formatted.startsWith("  &lt;exact &amp; words&gt; 😀  \n\n来源：")).toBe(true);
    expect(logicalHtmlTextLengthForTest(formatted)).toBeLessThanOrEqual(TELEGRAM_MESSAGE_LIMIT);
  });

  it("truncates by Unicode code points and reserves attribution within 4096", () => {
    const formatted = formatQuoteHtml({ ...QUOTE, quote: "😀".repeat(5_000) });
    expect(logicalHtmlTextLengthForTest(formatted)).toBe(TELEGRAM_MESSAGE_LIMIT);
    expect(formatted).toContain("…\n\n来源：");
    expect(formatted).not.toContain("�");
  });
});
