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

There is exactly one publisher: an operator-managed **Cloudflare native Git connection**. GitHub Actions never deploys. `.github/workflows/build-worker.yml` only pins trusted source/DATA commits, canonically validates DATA, deterministically exports and re-exports `generated-assets`, runs typecheck/unit/local-workerd checks, dry-run packages with `wrangler.deploy.jsonc`, and uploads the public artifact. A direct default-branch push resolves the published `data` head once. A successful changed-DATA update calls that reusable build explicitly because its `GITHUB_TOKEN` push cannot trigger another workflow. The source/ref/caller/reachability checks reject pull requests, forks, feature refs, arbitrary callers, and mismatched source SHAs.

Configure the Cloudflare native Git build from the repository root (`/`) with these values:

- Build command: `./worker/scripts/build-cloudflare.sh`
- Production deploy command: `cd worker && node node_modules/wrangler/bin/wrangler.js deploy --config wrangler.deploy.jsonc`
- Production branch: `master`
- Build variables: `NODE_VERSION=22.20.0`, `PYTHON_VERSION=3.13.3`, and `SKIP_DEPENDENCY_INSTALL=1`
- Do not set or rely on `UV_VERSION`; the build script selects an already-installed exact `uv 0.12.18` or bootstraps that pinned package itself.
- Preview deployments: disabled unless an operator deliberately defines them. Any future preview command must also pass `--config wrangler.deploy.jsonc`; the default `wrangler.jsonc` points at test fixtures and must never package a production or preview deployment.

The build image must provide Git, Python 3.13, and Node `22.20.0`; the script fails closed on those prerequisites. It reuses an already-installed exact `uv 0.12.18` read-only when available. If `uv` is missing or reports another version, the script creates its own external temporary Python 3.13 virtual environment, installs only pinned `uv==0.12.18` from the explicit public PyPI index with isolated pip configuration and bounded retries/timeouts, verifies the installed version, routes every uv command through that absolute executable, and removes the temporary environment on exit. It never replaces an image-provided uv or globally installs a package. The selected uv performs the locked sync; npm uses `npm ci`. Dependencies stay in ignored roots. The remainder of the build checks a clean exact source HEAD, resolves the public `data` branch once, checks out the selected reachable DATA commit in an isolated temporary repository, validates and exports to ignored `worker/generated-assets`, verifies descriptor hashes/provenance and deterministic re-export, then performs tests and a deployment-config dry-run. It contains no publish command or deployment credentials. `DATA_SHA=<exact reachable lowercase 40-hex>` may be set for an intentional historical rebuild; otherwise each new build uses the DATA head resolved at that build.

A DATA-only GitHub update does **not** trigger or feed Cloudflare automatically. After reviewing a successful changed-data Actions run, an operator must manually request a fresh Cloudflare rebuild. That new build resolves the freshest published DATA head at build time; the GitHub artifact is validation evidence only and is not automatically consumed by Cloudflare. Do not add a webhook, API trigger, workflow deploy job, or GitHub Cloudflare credentials as a substitute.

The native deploy step must publish code and `worker/generated-assets` from the same successful build. The repository's default `worker/wrangler.jsonc` remains test-only; production always selects `worker/wrangler.deploy.jsonc`. Cloudflare plan quotas and the availability of the documented tool versions in the selected build image are operator constraints to verify before enabling the connection; this repository does not claim a particular Cloudflare image supplies them.

Rollback means selecting a reviewed source commit in the native Git connection and starting a manual build, optionally with an exact reachable historical `DATA_SHA`. Never edit or upload `worker-data.json`, the manifest, or shards independently: root metadata is the atomic version pointer and runtime provenance must match the deployed code/assets generation.

Telegram activation remains separate and manual after deployment: provision `BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` with Cloudflare's secret mechanism, then register the exact webhook URL manually with Telegram as described above. Neither build nor deploy performs `getMe`, `setWebhook`, or sends a bot message.
