import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { AssetRepository } from "../src/asset-repository";
import { createApiHandler, type ApiDependencies } from "../src/api/handler";
import { AssetLoadCapacityError, AssetValidationError } from "../src/errors";
import { QuoteService } from "../src/quote-service";
import type { RandomSource } from "../src/index";

const ASSETS = resolve(import.meta.dirname, "generated-assets");

class ZeroRandom implements RandomSource {
  fill(bytes: Uint8Array): void { bytes.fill(0); }
}

function fileAssets(): (input: RequestInfo | URL) => Promise<Response> {
  return async (input) => {
    const pathname = new URL(input instanceof Request ? input.url : input.toString()).pathname;
    try {
      const bytes = await readFile(resolve(ASSETS, `.${pathname}`));
      return new Response(new Uint8Array(bytes));
    } catch {
      return new Response("missing", { status: 404 });
    }
  };
}

function realHandler() {
  const fetch = fileAssets();
  const repository = new AssetRepository({ fetch });
  return createApiHandler({
    repository,
    quoteService: new QuoteService(repository, new ZeroRandom()),
    staticAssets: { fetch: async (request) => fetch(request) },
  });
}

function request(path: string, init?: RequestInit): Request {
  return new Request(`https://api.example${path}`, init);
}

function throwingDependencies(error: Error): ApiDependencies {
  return {
    repository: {
      getArticle: async () => { throw error; },
      provenance: async () => { throw error; },
      corpusInfo: async () => { throw error; },
    },
    quoteService: { randomQuote: async () => { throw error; } },
  };
}

describe("HTTP API contract", () => {
  it("returns plain and JSON random quotes with golden original paragraph indexes", async () => {
    const api = realHandler();
    const root = await api(request("/"));
    expect(root.status).toBe(200);
    expect(root.headers.get("content-type")).toBe("text/plain; charset=utf-8");
    expect(root.headers.get("cache-control")).toBe("no-store");
    expect(await root.text()).toBe("  first exact  ");

    const quote = await api(request("/api/quote?selection=paragraph"));
    expect(quote.status).toBe(200);
    expect(quote.headers.get("access-control-allow-origin")).toBe("*");
    expect(await quote.json()).toMatchObject({ quote: "  first exact  ", paragraphIndex: 2, selection: "paragraph", article: { id: "1" } });
  });

  it("returns full article media, health counts, ETags, and a correct 304", async () => {
    const api = realHandler();
    const article = await api(request("/api/articles/2"));
    expect(article.status).toBe(200);
    expect(article.headers.get("cache-control")).toBe("public, max-age=0, must-revalidate");
    expect(article.headers.get("access-control-expose-headers")).toBe("ETag");
    expect(await article.json()).toMatchObject({ id: "2", text: [], content_type: "image", media: [{ type: "image" }] });
    const etag = article.headers.get("etag");
    expect(etag).toBe(`"${"b".repeat(40)}-2"`);

    const notModified = await api(request("/api/articles/2", { headers: { "If-None-Match": `"other", W/${etag}` } }));
    expect(notModified.status).toBe(304);
    expect(notModified.headers.get("etag")).toBe(etag);
    expect(notModified.headers.get("access-control-expose-headers")).toBe("ETag");
    expect(await notModified.text()).toBe("");

    const health = await api(request("/healthz"));
    expect(await health.json()).toEqual({
      formatVersion: 1,
      sourceSha: "a".repeat(40),
      dataSha: "b".repeat(40),
      counts: { articles: 4, selectableArticles: 3, sourceParagraphs: 7, selectableParagraphs: 4 },
    });
  });

  it("enforces narrow routes, canonical queries and methods with CORS", async () => {
    const api = realHandler();
    for (const path of ["/api/quote?selection=nope", "/api/quote?x=1", "/api/articles/01", "/api/articles/0", "/healthz?x=1"]) {
      const result = await api(request(path));
      expect(result.status, path).toBe(400);
      expect(result.headers.get("access-control-allow-origin"), path).toBe("*");
    }
    expect((await api(request("/api/articles/4"))).status).toBe(404);
    expect((await api(request("/unknown"))).status).toBe(404);

    const wrongMethod = await api(request("/api/quote", { method: "POST" }));
    expect(wrongMethod.status).toBe(405);
    expect(wrongMethod.headers.get("allow")).toBe("GET, OPTIONS");
    const options = await api(request("/api/quote", {
      method: "OPTIONS",
      headers: {
        Origin: "https://client.example",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "If-None-Match",
      },
    }));
    expect(options.status).toBe(204);
    expect(options.headers.get("access-control-allow-origin")).toBe("*");
    expect(options.headers.get("access-control-allow-methods")).toBe("GET, OPTIONS");
    expect(options.headers.get("access-control-allow-headers")).toBe("If-None-Match");
  });

  it("maps corrupt, missing, and capacity-limited internal assets to 503", async () => {
    for (const failure of [new AssetValidationError("bad asset"), new AssetLoadCapacityError("busy")]) {
      const result = await createApiHandler(throwingDependencies(failure))(request("/api/quote"));
      expect(result.status).toBe(503);
      expect(await result.json()).toEqual({ error: "assets_unavailable" });
    }

    const repository = new AssetRepository({ fetch: async () => new Response("missing", { status: 404 }) });
    const result = await createApiHandler({ repository, quoteService: new QuoteService(repository) })(request("/healthz"));
    expect(result.status).toBe(503);
  });

  it("maps provider fetch and body stream failures to 503 without masking programmer errors", async () => {
    const fetchFailure = new AssetRepository({ fetch: async () => { throw new TypeError("provider unavailable"); } });
    const failedFetch = await createApiHandler({
      repository: fetchFailure,
      quoteService: new QuoteService(fetchFailure),
    })(request("/healthz"));
    expect(failedFetch.status).toBe(503);
    expect(await failedFetch.json()).toEqual({ error: "assets_unavailable" });

    const bodyFailure = new AssetRepository({
      fetch: async () => new Response(new ReadableStream<Uint8Array>({
        pull() { throw new Error("stream interrupted"); },
      })),
    });
    const failedBody = await createApiHandler({
      repository: bodyFailure,
      quoteService: new QuoteService(bodyFailure),
    })(request("/healthz"));
    expect(failedBody.status).toBe(503);
    expect(await failedBody.json()).toEqual({ error: "assets_unavailable" });

    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const programmerFailure = await createApiHandler(throwingDependencies(new Error("bug")))(request("/healthz"));
      expect(programmerFailure.status).toBe(500);
      expect(await programmerFailure.json()).toEqual({ error: "internal_error" });
      expect(consoleError).toHaveBeenCalledOnce();
    } finally {
      consoleError.mockRestore();
    }
  });

  it("delegates only generated asset paths and does not SPA-fallback unknown paths", async () => {
    const api = realHandler();
    expect((await api(request("/worker-data.json"))).status).toBe(200);
    expect((await api(request(`/_data/${"b".repeat(40)}/manifest.json`))).status).toBe(200);
    expect((await api(request("/_data/nope"))).status).toBe(404);
    expect((await api(request("/index.html"))).status).toBe(404);
  });
});
