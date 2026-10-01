import { AssetRepository } from "./asset-repository";
import { createApiHandler } from "./api/handler";
import { QuoteService } from "./quote-service";

interface Env {
  ASSETS: Fetcher;
}

let currentAssets: Fetcher | undefined;
let currentHandler: ((request: Request) => Promise<Response>) | undefined;

function handler(env: Env): (request: Request) => Promise<Response> {
  if (currentHandler === undefined || currentAssets !== env.ASSETS) {
    const repository = new AssetRepository({ fetch: (input, init) => env.ASSETS.fetch(input, init) });
    const quoteService = new QuoteService(repository);
    currentAssets = env.ASSETS;
    currentHandler = createApiHandler({ repository, quoteService, staticAssets: env.ASSETS });
  }
  return currentHandler;
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return handler(env)(request);
  },
} satisfies ExportedHandler<Env>;
