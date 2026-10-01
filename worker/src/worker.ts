import { AssetRepository } from "./asset-repository";
import { createApiHandler } from "./api/handler";
import { QuoteService } from "./quote-service";
import { createTelegramHandler, TELEGRAM_WEBHOOK_PATH, type TelegramConfig } from "./telegram/handler";

export interface Env {
  ASSETS: Fetcher;
  BOT_TOKEN?: string;
  TELEGRAM_WEBHOOK_SECRET?: string;
  BOT_USERNAME?: string;
}

export function createWorkerHandler(env: Env, telegramFetch: typeof fetch = fetch): (request: Request) => Promise<Response> {
  const repository = new AssetRepository({ fetch: (input, init) => env.ASSETS.fetch(input, init) });
  const quoteService = new QuoteService(repository);
  const api = createApiHandler({ repository, quoteService, staticAssets: env.ASSETS });
  const telegram = createTelegramHandler({ quoteService, fetch: telegramFetch });
  const telegramConfig: TelegramConfig = {
    botToken: env.BOT_TOKEN,
    webhookSecret: env.TELEGRAM_WEBHOOK_SECRET,
    botUsername: env.BOT_USERNAME,
  };

  return (request) => {
    if (new URL(request.url).pathname === TELEGRAM_WEBHOOK_PATH) return telegram(request, telegramConfig);
    return api(request);
  };
}

let currentEnv: Env | undefined;
let currentHandler: ((request: Request) => Promise<Response>) | undefined;

function handler(env: Env): (request: Request) => Promise<Response> {
  if (currentHandler === undefined || currentEnv?.ASSETS !== env.ASSETS || currentEnv.BOT_TOKEN !== env.BOT_TOKEN
    || currentEnv.TELEGRAM_WEBHOOK_SECRET !== env.TELEGRAM_WEBHOOK_SECRET || currentEnv.BOT_USERNAME !== env.BOT_USERNAME) {
    currentEnv = env;
    currentHandler = createWorkerHandler(env);
  }
  return currentHandler;
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return handler(env)(request);
  },
} satisfies ExportedHandler<Env>;
