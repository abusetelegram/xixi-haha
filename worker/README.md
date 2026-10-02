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

`QuoteService.randomQuote()` returns exact quote whitespace, its original `text` array index, article metadata, the historical source URL, selection mode, and `{sourceSha,dataSha}` provenance. Both the HTTP and Telegram adapters share this service rather than implement selection or asset parsing.

`AssetRepository` strictly checks deployment metadata, manifest SHA/version/provenance, big-endian index size/counts/ranges, shard checksums/paths/membership, record schemas, and per-article selectable counts. It cold-loads only the small manifest/index and one selected shard. A version-keyed fulfilled-value LRU (default two shards, configurable 1–8) is separate from the bounded in-flight registry (default four unique shard loads, configurable 2–8). Same-key work always deduplicates, including across LRU eviction; failure is cleaned up for retry. Excess different-key concurrency throws retryable `AssetLoadCapacityError` before fetching. HTTP and Telegram adapters must map that overload to a retryable `503` rather than silently selecting another asset/version.

## Telegram webhook

`POST /telegram/webhook` is a native, framework-free Telegram adapter over the same `QuoteService` used by the HTTP API. It supports `/start`, `/yiyan`, and inline queries. Commands explicitly addressed to another bot and all ambient text are ignored with `200`; the adapter does not implement keyword or novelty replies. Message commands retain reply context, and every quote includes linked source attribution. HTML is escaped, and overlong text is truncated by Unicode code point while reserving attribution inside Telegram's 4096-character limit.

The webhook requires all three bindings below. `BOT_USERNAME` is non-secret public configuration and is pinned to the repository's documented bot name in `wrangler.jsonc`. The other two values are secrets and must never be placed in source, Static Assets, logs, or plain Wrangler variables:

- `BOT_TOKEN`: Telegram Bot API token.
- `TELEGRAM_WEBHOOK_SECRET`: a fresh high-entropy 32–256 character value using letters, digits, `_`, or `-`.
- `BOT_USERNAME`: `xixi_haha_bot` (without `@`).

Configuration and registration are deliberate operator actions; this repository does not perform them. After an authorized deployment, an operator must provision both secrets through the approved Cloudflare secret mechanism and explicitly call Telegram's `setWebhook` for the exact public `https://<worker-host>/telegram/webhook` URL, supplying the same value as `secret_token`. Do not put either secret in a command transcript; use protected environment/input handling. Registration should request only `message` and `inline_query` updates. The endpoint returns `503` until all configuration is present.

Webhook authentication is checked before the body is read. Bodies are stream-limited to 64 KiB. Outbound calls are restricted to HTTPS `api.telegram.org` `sendMessage` and `answerInlineQuery`, have a five-second timeout, and are attempted once: Telegram `429` becomes retryable `503` with bounded `Retry-After`, timeout becomes `504`, and other Bot API failures become `502`. There is no arbitrary method proxy, sleep, or internal retry loop.

Processing is stateless and at-least-once. Telegram may redeliver an update after a timeout or non-2xx response, so duplicate bot replies are possible. No durable deduplication store is claimed or configured in this slice.

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

## Build and deployment

`.github/workflows/deploy-worker.yml` is the only application publisher. A push to the actual default branch pins the published `data` head once; a successful changed-data update calls the same reusable workflow explicitly because its `GITHUB_TOKEN` push cannot trigger another workflow. Pull requests, forks, non-default refs, arbitrary manual callers, and mismatched source SHAs cannot enter the trusted job.

Every run canonically validates DATA, deterministically exports `generated-assets`, enforces fewer than 20,000 files and less than 25 MiB per file, verifies metadata/manifest checksums and exact `{sourceSha,dataSha}` provenance, then runs typecheck, unit tests, local workerd against those actual assets, and a Wrangler dry-run package. The resulting artifact contains code packaging and intentionally public corpus assets; it contains neither `BOT_TOKEN` nor `TELEGRAM_WEBHOOK_SECRET`.

Deployment is **inactive until operator setup**. The build/test/artifact path always runs without Cloudflare credentials. The deploy job runs only when repository variable `CLOUDFLARE_DEPLOY_ENABLED` is exactly `true`, is serialized, rechecks that live `master` and `data` still equal the tested generation, revalidates provenance, and deploys code and matching Static Assets together. Configure only named `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets in the protected `cloudflare-production` environment; do not use broad secret inheritance. Scope the token to the target account and minimum Worker/Static Assets edit permissions.

Cloudflare free-plan request, CPU, build, and Static Assets quotas can change and are operator constraints, not guarantees made by this repository. Check current Cloudflare limits before enabling production. A quota or stale-generation failure must stop rather than deploy only code or only assets.

Rollback means deploying a reviewed default-branch source commit together with a freshly validated export for an exact reachable DATA commit. Never edit or upload `worker-data.json`, the manifest, or shards independently: the root metadata is the atomic version pointer and runtime provenance must match the deployed code/assets generation.

Telegram activation remains separate and manual after deployment: provision `BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` with Cloudflare's secret mechanism, then register the exact webhook URL manually with Telegram as described above. Neither build nor deploy performs `getMe`, `setWebhook`, or sends a bot message.
