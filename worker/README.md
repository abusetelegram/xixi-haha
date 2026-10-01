# Worker core

Framework-free TypeScript primitives for the future HTTP and Telegram adapters. This package performs no deployment and has no runtime npm dependencies.

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

`QuoteService.randomQuote()` returns exact quote whitespace, its original `text` array index, article metadata, the historical source URL, selection mode, and `{sourceSha,dataSha}` provenance. Both future adapters must share this service rather than implement selection or asset parsing.

`AssetRepository` strictly checks deployment metadata, manifest SHA/version/provenance, big-endian index size/counts/ranges, shard checksums/paths/membership, record schemas, and per-article selectable counts. It cold-loads only the small manifest/index and one selected shard. A version-keyed promise LRU (default two shards, configurable 1–8) deduplicates in-flight reads; eviction or failure only causes a validated refetch.

## Local validation

From this directory, after the repository Python environment and `npm ci` are available:

```sh
../.venv/bin/python scripts/generate-test-assets.py
npm run typecheck
npm test
```

The ignored tiny assets are generated through `parse/export_worker.py`; tests also consume the committed Python binary and whitespace golden fixtures directly. No generated corpus assets or index are committed.
