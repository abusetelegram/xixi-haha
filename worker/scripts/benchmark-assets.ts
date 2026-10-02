import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { performance } from "node:perf_hooks";
import { AssetRepository } from "../src/asset-repository";

const root = process.argv[2];
if (root === undefined) throw new Error("usage: benchmark-assets.ts <generated-assets-directory>");

const metadata = JSON.parse(await readFile(resolve(root, "worker-data.json"), "utf8"));
const manifest = JSON.parse(await readFile(resolve(root, metadata.manifestPath), "utf8"));
const worst = manifest.sharding.shards.reduce((largest: { bytes: number }, shard: { bytes: number }) => shard.bytes > largest.bytes ? shard : largest);
const rows = JSON.parse(await readFile(resolve(root, worst.path), "utf8"));
if (rows.length === 0) throw new Error("largest shard unexpectedly empty");
const id = rows[0].id as string;
let bytesRead = 0;
const fetchAsset = async (input: RequestInfo | URL) => {
  const pathname = new URL(input instanceof Request ? input.url : input.toString()).pathname;
  try {
    const bytes = new Uint8Array(await readFile(resolve(root, `.${pathname}`)));
    bytesRead += bytes.byteLength;
    return new Response(bytes);
  } catch {
    return new Response("missing", { status: 404 });
  }
};
const repository = new AssetRepository({ fetch: fetchAsset });

const startIndex = performance.now();
await repository.paragraphIndex();
const indexMs = performance.now() - startIndex;
const beforeShardBytes = bytesRead;
const startCold = performance.now();
await repository.getArticle(id);
const coldShardMs = performance.now() - startCold;
const shardBytesRead = bytesRead - beforeShardBytes;
const startWarm = performance.now();
await repository.getArticle(id);
const warmShardMs = performance.now() - startWarm;

console.log(JSON.stringify({
  screeningOnly: true,
  articles: manifest.counts.articles,
  indexBytes: manifest.paragraphIndex.bytes,
  worstShard: worst.shard,
  worstShardBytes: worst.bytes,
  selectedId: id,
  measuredShardBytes: shardBytesRead,
  indexColdMs: Number(indexMs.toFixed(3)),
  worstShardColdMs: Number(coldShardMs.toFixed(3)),
  worstShardWarmMs: Number(warmShardMs.toFixed(3)),
  note: "Local Node timings are screening data, not a Cloudflare edge CPU guarantee.",
}));
