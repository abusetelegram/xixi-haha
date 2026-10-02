# Cloudflare Worker API

Framework-free TypeScript core and HTTP adapter for a Cloudflare Worker. The Worker has no runtime npm dependencies and reads only validated, bounded Static Assets through the `ASSETS` binding.

## Adapter API

```ts
import { AssetRepository, QuoteService } from "./src/main";

const repository = new AssetRepository({
  // Pass the Cloudflare Static Assets binding's fetch method here.
  fetch: (input, init) => env.ASSETS.fetch(input, init),
});
const quotes = new QuoteService(repository);

const quote = await quotes.randomQuote();          // uniform selectable paragraph
const legacyWeight = await quotes.randomQuote("article"); // uniform nonempty article, then rank
const article = await repository.getArticle("40140589");  // full projection, including media/empty text
```

`QuoteService.randomQuote()` returns exact quote whitespace, its original `text` array index, article metadata, the historical source URL, selection mode, and `{sourceSha,dataSha}` provenance. Both the HTTP and future Telegram adapters share this service rather than implement selection or asset parsing.

`AssetRepository` strictly checks deployment metadata, manifest SHA/version/provenance, big-endian index size/counts/ranges, shard checksums/paths/membership, record schemas, and per-article selectable counts. It cold-loads only the small manifest/index and one selected shard. A version-keyed fulfilled-value LRU (default two shards, configurable 1–8) is separate from the bounded in-flight registry (default four unique shard loads, configurable 2–8). Same-key work always deduplicates, including across LRU eviction; failure is cleaned up for retry. Excess different-key concurrency throws retryable `AssetLoadCapacityError` before fetching. HTTP and Telegram adapters must map that overload to a retryable `503` rather than silently selecting another asset/version.

## HTTP routes

- `GET /` returns a uniformly selected paragraph as UTF-8 plain text.
- `GET /api/quote?selection=paragraph|article` returns the quote, original paragraph index, article metadata, source URL, selection mode, and corpus provenance.
- `GET /api/articles/<canonical-positive-uint32>` returns the full projection (including media), with a DATA-SHA/id ETag.
- `GET /healthz` returns format/provenance versions and corpus counts.
- `OPTIONS` on those public routes returns `204` with public `GET, OPTIONS` CORS.

Random responses are `no-store`. Unknown routes are strict `404`s; only `/worker-data.json` and `/_data/*` delegate to Static Assets, with no SPA fallback. Internal missing, corrupt, or capacity-limited assets fail closed as `503`.

## Local validation

From this directory, after the repository Python environment and `npm ci` are available:

```sh
../.venv/bin/python scripts/generate-test-assets.py
npm run typecheck
npm test
WRANGLER_SEND_METRICS=false npm run test:runtime
WRANGLER_SEND_METRICS=false npm run package:dry-run
```

`test:runtime` starts local workerd and exercises the real Static Assets binding; `package:dry-run` packages without authenticating or deploying. The ignored tiny assets are generated through `parse/export_worker.py`; tests also consume the committed Python binary and whitespace golden fixtures directly. No generated corpus assets or index are committed.

For local screening against a separately generated real corpus, bundle and run `scripts/benchmark-assets.ts <asset-directory>`. Its cold/warm timings are not a Cloudflare edge CPU guarantee.
