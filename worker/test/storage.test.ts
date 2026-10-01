import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { basename, relative, resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
  AssetLoadCapacityError,
  AssetRepository,
  AssetValidationError,
  QuoteService,
  type ContentHasher,
  type RandomSource,
} from "../src/main";

const ASSETS = resolve(import.meta.dirname, "generated-assets");
const MAX_MANIFEST_BYTES = 1024 * 1024;
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

function padManifestTo(files: Map<string, Uint8Array>, size: number): void {
  const metadata = JSON.parse(new TextDecoder().decode(files.get("worker-data.json")!));
  const manifest = files.get(metadata.manifestPath)!;
  if (manifest.byteLength > size) throw new Error("fixture manifest already exceeds requested size");
  const bytes = new Uint8Array(size);
  bytes.set(manifest);
  bytes.fill(0x20, manifest.byteLength);
  files.set(metadata.manifestPath, bytes);
  metadata.manifestSha256 = sha(bytes);
  files.set("worker-data.json", jsonBytes(metadata));
}

function assetResponse(bytes: Uint8Array): Response {
  return new Response(new Uint8Array(bytes).buffer as ArrayBuffer, {
    headers: { "content-length": String(bytes.byteLength) },
  });
}

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

function repository(files = fixtureFiles(), cacheSize = 2) {
  const calls = new Map<string, number>();
  const fetch = async (input: RequestInfo | URL): Promise<Response> => {
    const path = new URL(String(input)).pathname.slice(1);
    calls.set(path, (calls.get(path) ?? 0) + 1);
    const bytes = files.get(path);
    return bytes === undefined ? new Response("missing", { status: 404 }) : assetResponse(bytes);
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
    expect(await store.getArticle(4)).toBeUndefined();
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

  it("keeps a slow pending shard discoverable across fulfilled-LRU eviction", async () => {
    const files = fixtureFiles();
    const calls = new Map<string, number>();
    const hashCalls = new Map<string, number>();
    const gates = new Map([1, 2].map((number) => [
      `_data/${"b".repeat(40)}/shards/00${number}.json`, deferred(),
    ]));
    const hash: ContentHasher = async (bytes) => {
      const digest = sha(bytes);
      hashCalls.set(digest, (hashCalls.get(digest) ?? 0) + 1);
      return digest;
    };
    const fetch = async (input: RequestInfo | URL): Promise<Response> => {
      const path = new URL(String(input)).pathname.slice(1);
      calls.set(path, (calls.get(path) ?? 0) + 1);
      const gate = gates.get(path);
      if (gate !== undefined) await gate.promise;
      return assetResponse(files.get(path)!);
    };
    const store = new AssetRepository({ fetch, hash, shardCacheSize: 1, maxConcurrentShardLoads: 2 });
    await store.paragraphIndex();

    const firstA = store.getArticle(1);
    await vi.waitFor(() => expect(calls.get([...gates.keys()][0]!)).toBe(1));
    const pendingB = store.getArticle(2);
    await vi.waitFor(() => expect(calls.get([...gates.keys()][1]!)).toBe(1));
    const secondA = store.getArticle(1);
    expect(calls.get([...gates.keys()][0]!)).toBe(1);

    gates.get([...gates.keys()][0]!)!.resolve();
    const [articleA1, articleA2] = await Promise.all([firstA, secondA]);
    expect(articleA1).toBe(articleA2);
    expect(hashCalls.get(sha(files.get([...gates.keys()][0]!)!))).toBe(1);
    gates.get([...gates.keys()][1]!)!.resolve();
    await pendingB;

    await store.getArticle(1);
    expect(calls.get([...gates.keys()][0]!)).toBe(2);
    expect(hashCalls.get(sha(files.get([...gates.keys()][0]!)!))).toBe(2);
  });

  it("bounds unique pending loads, dedupes same-key requests at capacity, and admits retry", async () => {
    const files = fixtureFiles();
    const shardPaths = [1, 2, 3].map((number) => `_data/${"b".repeat(40)}/shards/00${number}.json`);
    const gates = new Map(shardPaths.map((path) => [path, deferred()]));
    const calls = new Map<string, number>();
    let activeShardLoads = 0;
    let peakShardLoads = 0;
    const fetch = async (input: RequestInfo | URL): Promise<Response> => {
      const path = new URL(String(input)).pathname.slice(1);
      calls.set(path, (calls.get(path) ?? 0) + 1);
      const gate = gates.get(path);
      if (gate !== undefined) {
        activeShardLoads += 1;
        peakShardLoads = Math.max(peakShardLoads, activeShardLoads);
        await gate.promise;
        activeShardLoads -= 1;
      }
      return assetResponse(files.get(path)!);
    };
    const store = new AssetRepository({ fetch, shardCacheSize: 2, maxConcurrentShardLoads: 2 });
    await store.paragraphIndex();

    const first = store.getArticle(1);
    const second = store.getArticle(2);
    await vi.waitFor(() => expect((calls.get(shardPaths[0]!) ?? 0) + (calls.get(shardPaths[1]!) ?? 0)).toBe(2));
    const sameKey = store.getArticle(1);
    await expect(store.getArticle(3)).rejects.toBeInstanceOf(AssetLoadCapacityError);
    expect(calls.get(shardPaths[2]!)).toBeUndefined();

    gates.get(shardPaths[0]!)!.resolve();
    const [firstResult, sameKeyResult] = await Promise.all([first, sameKey]);
    expect(firstResult).toBe(sameKeyResult);
    const third = store.getArticle(3);
    await vi.waitFor(() => expect(calls.get(shardPaths[2]!)).toBe(1));
    gates.get(shardPaths[2]!)!.resolve();
    gates.get(shardPaths[1]!)!.resolve();
    await Promise.all([second, third]);
    expect(Math.max(...shardPaths.map((path) => calls.get(path) ?? 0))).toBe(1);
    expect(peakShardLoads).toBe(2);
  });

  it("cleans up rejected pending shard loads so a later request retries", async () => {
    const files = fixtureFiles();
    const shardPath = `_data/${"b".repeat(40)}/shards/001.json`;
    let shardAttempts = 0;
    const fetch = async (input: RequestInfo | URL): Promise<Response> => {
      const path = new URL(String(input)).pathname.slice(1);
      if (path === shardPath && ++shardAttempts === 1) return new Response("busy", { status: 503 });
      return assetResponse(files.get(path)!);
    };
    const store = new AssetRepository({ fetch, shardCacheSize: 1, maxConcurrentShardLoads: 2 });
    await expect(store.getArticle(1)).rejects.toThrow(/503/);
    await expect(store.getArticle(1)).resolves.toMatchObject({ id: "1" });
    expect(shardAttempts).toBe(2);
  });

  it("does not cache failed fetches as correctness state", async () => {
    const { store, files } = repository(new Map());
    await expect(store.paragraphIndex()).rejects.toThrow(AssetValidationError);
    for (const [path, bytes] of fixtureFiles()) files.set(path, bytes);
    await expect(store.paragraphIndex()).resolves.toMatchObject({ articleCount: 4 });
  });

  it("accepts a valid manifest at the 1 MiB boundary and rejects one byte over", async () => {
    const bounded = fixtureFiles();
    padManifestTo(bounded, MAX_MANIFEST_BYTES);
    await expect(repository(bounded).store.paragraphIndex()).resolves.toMatchObject({ articleCount: 4 });

    const oversized = fixtureFiles();
    padManifestTo(oversized, MAX_MANIFEST_BYTES + 1);
    await expect(repository(oversized).store.paragraphIndex()).rejects.toThrow(/byte bound/);
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
    const quote = await new QuoteService(store, new Sequence([3])).randomQuote();
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
    const quote = await new QuoteService(store, new Sequence([3, 1])).randomQuote("article");
    expect(quote.quote).toBe("second");
    expect(quote.paragraphIndex).toBe(3);
    expect(quote.article.id).toBe("1");
  });
});
