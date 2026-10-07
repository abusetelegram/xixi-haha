#!/usr/bin/env node
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { createServer } from "node:net";

async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => server.listen(0, "127.0.0.1", resolve).once("error", reject));
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("could not allocate test port");
  await new Promise((resolve) => server.close(resolve));
  return address.port;
}

const port = await availablePort();
const configArgs = process.env.WRANGLER_CONFIG === undefined ? [] : ["--config", process.env.WRANGLER_CONFIG];
const deploymentAssets = process.env.WRANGLER_CONFIG === "wrangler.deploy.jsonc";
let expectedDataSha = "b".repeat(40);
let expectedArticles = 4;
let articleId = "2";
if (deploymentAssets) {
  const metadata = JSON.parse(await readFile(new URL("../generated-assets/worker-data.json", import.meta.url), "utf8"));
  const manifest = JSON.parse(await readFile(new URL(`../generated-assets/${metadata.manifestPath}`, import.meta.url), "utf8"));
  expectedDataSha = metadata.dataSha;
  expectedArticles = manifest.counts.articles;
  const populated = manifest.sharding.shards.find((shard) => shard.recordCount > 0);
  if (populated === undefined) throw new Error("deployment corpus has no article shard");
  const rows = JSON.parse(await readFile(new URL(`../generated-assets/${populated.path}`, import.meta.url), "utf8"));
  articleId = rows[0].id;
}
const child = spawn(process.execPath, ["node_modules/wrangler/bin/wrangler.js", "dev", ...configArgs, "--local", "--port", String(port), "--ip", "127.0.0.1"], {
  cwd: new URL("..", import.meta.url),
  env: { ...process.env, WRANGLER_SEND_METRICS: "false", NO_COLOR: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
let logs = "";
child.stdout.on("data", (chunk) => { logs += chunk; });
child.stderr.on("data", (chunk) => { logs += chunk; });

async function fetchReady(path, init) {
  const deadline = Date.now() + 20_000;
  let last;
  while (Date.now() < deadline) {
    try { return await fetch(`http://127.0.0.1:${port}${path}`, init); }
    catch (error) { last = error; await new Promise((resolve) => setTimeout(resolve, 100)); }
  }
  throw new Error(`local Worker did not become ready: ${String(last)}\n${logs}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

try {
  const root = await fetchReady("/");
  assert(root.status === 200, `root status ${root.status}`);
  assert(root.headers.get("cache-control") === "no-store", "root cache policy");
  const rootText = await root.text();
  assert(rootText.length > 0 && (deploymentAssets || ["  first exact  ", "second", "third", "​"].includes(rootText)), "root quote payload");

  const quote = await fetchReady("/api/quote");
  const quoteBody = await quote.json();
  assert(quote.status === 200 && typeof quoteBody.paragraphIndex === "number", "quote JSON contract");

  const article = await fetchReady(`/api/articles/${articleId}`);
  const articleBody = await article.json();
  assert(article.status === 200 && articleBody.id === articleId, "article projection");
  if (!deploymentAssets) assert(articleBody.media?.[0]?.type === "image", "fixture article media projection");
  const etag = article.headers.get("etag");
  const cached = await fetchReady(`/api/articles/${articleId}`, { headers: { "If-None-Match": etag } });
  assert(cached.status === 304, `conditional article status ${cached.status}`);

  const health = await fetchReady("/healthz");
  const healthBody = await health.json();
  assert(healthBody.counts.articles === expectedArticles && healthBody.counts.selectableParagraphs > 0, "health counts");

  assert((await fetchReady("/worker-data.json")).status === 200, "static metadata route");
  assert((await fetchReady(`/_data/${expectedDataSha}/manifest.json`)).status === 200, "static version route");
  assert((await fetchReady("/not-a-static-route")).status === 404, "strict unknown route");
  console.log("local workerd: root, API, ETag/304, health, static binding, and strict 404 passed");
} finally {
  child.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => child.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 3_000)),
  ]);
  if (child.exitCode === null) child.kill("SIGKILL");
}
