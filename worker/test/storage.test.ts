import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { basename, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { AssetRepository, AssetValidationError, QuoteService, type RandomSource } from "../src/main";

const ASSETS = resolve(import.meta.dirname, "generated-assets");
const encoder = new TextEncoder();

function walk(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = resolve(directory, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

function fixtureFiles(): Map<string, Uint8Array> {
  return new Map(walk(ASSETS).map((path) => [relative(ASSETS, path).split("\\").join("/"), new Uint8Array(readFileSync(path))]));
}

function sha(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function jsonBytes(value: unknown): Uint8Array {
  return encoder.encode(`${JSON.stringify(value)}\n`);
}

function resignManifest(files: Map<string, Uint8Array>, mutate: (manifest: any) => void): void {
  const metadata = JSON.parse(new TextDecoder().decode(files.get("worker-data.json")!));
  const manifest = JSON.parse(new TextDecoder().decode(files.get(metadata.manifestPath)!));
  mutate(manifest);
  const bytes = jsonBytes(manifest);
  files.set(metadata.manifestPath, bytes);
  metadata.manifestSha256 = sha(bytes);
  files.set("worker-data.json", jsonBytes(metadata));
}

function repository(files = fixtureFiles(), cacheSize = 2) {
  const calls = new Map<string, number>();
  const fetch = async (input: RequestInfo | URL): Promise<Response> => {
    const path = new URL(String(input)).pathname.slice(1);
    calls.set(path, (calls.get(path) ?? 0) + 1);
    const bytes = files.get(path);
    return bytes === undefined ? new Response("missing", { status: 404 }) : new Response(new Uint8Array(bytes).buffer as ArrayBuffer, {
      headers: { "content-length": String(bytes.byteLength) },
    });
  };
  return { store: new AssetRepository({ fetch, shardCacheSize: cacheSize }), calls, files };
}

class Sequence implements RandomSource {
  constructor(private readonly values: number[]) {}
  fill(bytes: Uint8Array): void {
    const value = this.values.shift();
    if (value === undefined) throw new Error("sequence exhausted");
    new DataView(bytes.buffer, bytes.byteOffset, 4).setUint32(0, value, false);
  }
}

describe("AssetRepository generated fixture", () => {
  it("cold-loads metadata, manifest and small index, then only the selected shard", async () => {
    const { store, calls } = repository();
    const article = await store.getArticle(2);
    expect(article).toMatchObject({ id: "2", text: [], content_type: "image", media: [{ alt: "" }] });
    expect([...calls.keys()].map((path) => basename(path)).sort()).toEqual(["manifest.json", "paragraph-index.bin", "worker-data.json", "002.json"].sort());
    expect(await store.getArticle(3)).toBeUndefined();
    expect(calls.size).toBe(4);
  });

  it("deduplicates concurrent shard loads and bounds the versioned LRU", async () => {
    const { store, calls } = repository(undefined, 1);
    await Promise.all([store.getArticle(1), store.getArticle(5)]);
    expect(calls.get("_data/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/shards/001.json")).toBe(1);
    await store.getArticle(2);
    await store.getArticle(1);
    expect(calls.get("_data/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/shards/001.json")).toBe(2);
  });

  it("does not cache failed fetches as correctness state", async () => {
    const { store, files } = repository(new Map());
    await expect(store.paragraphIndex()).rejects.toThrow(AssetValidationError);
    for (const [path, bytes] of fixtureFiles()) files.set(path, bytes);
    await expect(store.paragraphIndex()).resolves.toMatchObject({ articleCount: 3 });
  });

  it("fails closed for checksum, manifest path/count, and shard membership errors", async () => {
    const checksum = fixtureFiles();
    const shardPath = [...checksum.keys()].find((path) => path.endsWith("001.json"))!;
    const corruptShard = checksum.get(shardPath)!.slice();
    const lastByte = corruptShard.length - 1;
    corruptShard[lastByte] = corruptShard[lastByte]! ^ 1;
    checksum.set(shardPath, corruptShard);
    await expect(repository(checksum).store.getArticle(1)).rejects.toThrow(/checksum/);

    const unsafe = fixtureFiles();
    const metadata = JSON.parse(new TextDecoder().decode(unsafe.get("worker-data.json")!));
    metadata.manifestPath = "../manifest.json";
    unsafe.set("worker-data.json", jsonBytes(metadata));
    await expect(repository(unsafe).store.paragraphIndex()).rejects.toThrow(/unsafe/);

    const counts = fixtureFiles();
    resignManifest(counts, (manifest) => { manifest.counts.articles += 1; });
    await expect(repository(counts).store.paragraphIndex()).rejects.toThrow(/counts|record/);

    const membership = fixtureFiles();
    resignManifest(membership, (manifest) => {
      const descriptor = manifest.sharding.shards.find((item: any) => item.shard === 1);
      const rows = JSON.parse(new TextDecoder().decode(membership.get(descriptor.path)!));
      rows[1].id = "9";
      const bytes = jsonBytes(rows);
      membership.set(descriptor.path, bytes);
      descriptor.bytes = bytes.byteLength;
      descriptor.sha256 = sha(bytes);
    });
    await expect(repository(membership).store.getArticle(1)).rejects.toThrow(/membership/);

    const content = fixtureFiles();
    resignManifest(content, (manifest) => {
      const descriptor = manifest.sharding.shards.find((item: any) => item.shard === 1);
      const rows = JSON.parse(new TextDecoder().decode(content.get(descriptor.path)!));
      rows[0].text = ["", " "];
      const bytes = jsonBytes(rows);
      content.set(descriptor.path, bytes);
      descriptor.bytes = bytes.byteLength;
      descriptor.sha256 = sha(bytes);
    });
    await expect(repository(content).store.getArticle(1)).rejects.toThrow(/content/);

    const shardPathError = fixtureFiles();
    resignManifest(shardPathError, (manifest) => { manifest.sharding.shards[0].path = `_data/${"b".repeat(40)}/shards/../000.json`; });
    await expect(repository(shardPathError).store.paragraphIndex()).rejects.toThrow(/path|unsafe/);
  });
});

describe("QuoteService", () => {
  it("selects globally across unequal counts and returns original paragraph position", async () => {
    const { store } = repository();
    const quote = await new QuoteService(store, new Sequence([2])).randomQuote();
    expect(quote).toEqual({
      quote: "​",
      paragraphIndex: 1,
      article: { id: "5", title: "Last", date: "2020-01-03 00:00:00", author: "C", editor: "G" },
      sourceUrl: "http://jhsjk.people.cn/article/5",
      selection: "paragraph",
      corpus: { sourceSha: "a".repeat(40), dataSha: "b".repeat(40) },
    });
  });

  it("supports uniform selectable-article weighting without selecting empties", async () => {
    const { store } = repository();
    const quote = await new QuoteService(store, new Sequence([0, 1])).randomQuote("article");
    expect(quote.quote).toBe("second");
    expect(quote.paragraphIndex).toBe(3);
    expect(quote.article.id).toBe("1");
  });
});
