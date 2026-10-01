import { AssetLoadCapacityError, AssetUnavailableError, AssetValidationError, EmptyCorpusError } from "../errors";
import type { CorpusInfo } from "../asset-repository";
import type { Article, Quote, SelectionMode } from "../types";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "If-None-Match",
  "Access-Control-Expose-Headers": "ETag",
} as const;
const JSON_HEADERS = { "Content-Type": "application/json; charset=utf-8" } as const;
const CANONICAL_ID = /^[1-9][0-9]*$/;

export interface ApiRepository {
  getArticle(id: number | string): Promise<Article | undefined>;
  provenance(): Promise<{ dataSha: string }>;
  corpusInfo(): Promise<CorpusInfo>;
}

export interface ApiQuoteService {
  randomQuote(selection?: SelectionMode): Promise<Quote>;
}

export interface ApiDependencies {
  repository: ApiRepository;
  quoteService: ApiQuoteService;
  staticAssets?: { fetch(request: Request): Promise<Response> };
}

function response(body: BodyInit | null, status: number, headers: HeadersInit = {}): Response {
  return new Response(body, { status, headers: { ...CORS_HEADERS, ...headers } });
}

function json(value: unknown, status = 200, headers: HeadersInit = {}): Response {
  return response(JSON.stringify(value), status, { ...JSON_HEADERS, ...headers });
}

function error(status: number, code: string): Response {
  return json({ error: code }, status);
}

function queryIsEmpty(url: URL): boolean {
  return url.search === "";
}

function quoteSelection(url: URL): SelectionMode | undefined {
  const keys = [...url.searchParams.keys()];
  if (keys.some((key) => key !== "selection") || url.searchParams.getAll("selection").length > 1) return undefined;
  const value = url.searchParams.get("selection") ?? "paragraph";
  return value === "paragraph" || value === "article" ? value : undefined;
}

function knownRoute(pathname: string): boolean {
  return pathname === "/" || pathname === "/api/quote" || pathname === "/healthz" || pathname.startsWith("/api/articles/");
}

function allowsStatic(pathname: string): boolean {
  return pathname === "/worker-data.json" || pathname.startsWith("/_data/");
}

function etagMatches(value: string | null, etag: string): boolean {
  if (value === null) return false;
  return value.split(",").some((candidate) => {
    const trimmed = candidate.trim();
    return trimmed === "*" || trimmed === etag || (trimmed.startsWith("W/") && trimmed.slice(2) === etag);
  });
}

export function createApiHandler(dependencies: ApiDependencies): (request: Request) => Promise<Response> {
  return async (request) => {
    const url = new URL(request.url);
    const { pathname } = url;

    if (allowsStatic(pathname)) {
      if (request.method !== "GET" && request.method !== "HEAD") return new Response(null, { status: 405, headers: { Allow: "GET, HEAD" } });
      return dependencies.staticAssets?.fetch(request) ?? new Response("Not Found", { status: 404 });
    }

    if (!knownRoute(pathname)) return error(404, "not_found");
    if (request.method === "OPTIONS") return response(null, 204);
    if (request.method !== "GET") return response(JSON.stringify({ error: "method_not_allowed" }), 405, { ...JSON_HEADERS, Allow: "GET, OPTIONS" });

    try {
      if (pathname === "/") {
        if (!queryIsEmpty(url)) return error(400, "bad_query");
        const quote = await dependencies.quoteService.randomQuote("paragraph");
        return response(quote.quote, 200, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" });
      }

      if (pathname === "/api/quote") {
        const selection = quoteSelection(url);
        if (selection === undefined) return error(400, "bad_query");
        return json(await dependencies.quoteService.randomQuote(selection), 200, { "Cache-Control": "no-store" });
      }

      if (pathname === "/healthz") {
        if (!queryIsEmpty(url)) return error(400, "bad_query");
        return json(await dependencies.repository.corpusInfo(), 200, { "Cache-Control": "no-store" });
      }

      if (!queryIsEmpty(url)) return error(400, "bad_query");
      const id = pathname.slice("/api/articles/".length);
      if (!CANONICAL_ID.test(id) || Number(id) > 0xffff_ffff) return error(400, "bad_article_id");
      const article = await dependencies.repository.getArticle(id);
      if (article === undefined) return error(404, "article_not_found");
      const { dataSha } = await dependencies.repository.provenance();
      const etag = `"${dataSha}-${id}"`;
      const cacheHeaders = { ETag: etag, "Cache-Control": "public, max-age=0, must-revalidate" };
      if (etagMatches(request.headers.get("If-None-Match"), etag)) return response(null, 304, cacheHeaders);
      return json(article, 200, cacheHeaders);
    } catch (caught) {
      if (caught instanceof AssetValidationError || caught instanceof AssetLoadCapacityError ||
          caught instanceof AssetUnavailableError || caught instanceof EmptyCorpusError) {
        return error(503, "assets_unavailable");
      }
      console.error("HTTP API request failed", caught);
      return error(500, "internal_error");
    }
  };
}
