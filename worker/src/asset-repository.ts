import { AssetValidationError } from "./errors";
import { decodeParagraphIndex, type ParagraphIndex } from "./index";
import { isSelectableParagraph } from "./selection";
import type { Article, CorpusProvenance, MediaItem } from "./types";

const FORMAT_VERSION = 1;
const UINT32_MAX = 0xffff_ffff;
const MAX_ASSET_BYTES = 25 * 1024 * 1024;
const MAX_METADATA_BYTES = 64 * 1024;
const MAX_MANIFEST_BYTES = 1024 * 1024;
const SHA_PATTERN = /^[0-9a-f]{64}$/;
const COMMIT_PATTERN = /^[0-9a-f]{40}$/;
const ID_PATTERN = /^[1-9][0-9]*$/;

export type AssetFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
export type ContentHasher = (bytes: Uint8Array) => Promise<string>;

interface FileDescriptor {
  path: string;
  bytes: number;
  sha256: string;
}

interface ShardDescriptor extends FileDescriptor {
  shard: number;
  recordCount: number;
}

interface WorkerMetadata {
  formatVersion: number;
  sourceSha: string;
  dataSha: string;
  manifestPath: string;
  manifestSha256: string;
}

interface Manifest {
  formatVersion: number;
  sourceSha: string;
  dataSha: string;
  counts: {
    articles: number;
    selectableArticles: number;
    sourceParagraphs: number;
    selectableParagraphs: number;
  };
  sharding: {
    algorithm: string;
    shardCount: number;
    mask: number;
    recordsSortedBy: string;
    shards: ShardDescriptor[];
  };
  paragraphIndex: FileDescriptor & { byteOrder: string; layout: string };
}

interface LoadedState {
  metadata: WorkerMetadata;
  manifest: Manifest;
  index: ParagraphIndex;
  indexIds: Set<number>;
  indexPositions: Map<number, number>;
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new AssetValidationError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function integer(value: unknown, label: string, maximum = UINT32_MAX): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0 || (value as number) > maximum) {
    throw new AssetValidationError(`${label} must be a bounded non-negative integer`);
  }
  return value as number;
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string") throw new AssetValidationError(`${label} must be a string`);
  return value;
}

function exactKeys(record: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): void {
  const allowed = new Set([...required, ...optional]);
  if (required.some((key) => !(key in record)) || Object.keys(record).some((key) => !allowed.has(key))) {
    throw new AssetValidationError("asset object fields do not match the format");
  }
}

function safeVersionPath(path: string, dataSha: string): string {
  if (path.startsWith("/") || path.includes("\\") || path.split("/").some((part) => part === "" || part === "." || part === "..")) {
    throw new AssetValidationError("asset path is unsafe");
  }
  if (!path.startsWith(`_data/${dataSha}/`)) throw new AssetValidationError("asset path is outside its DATA version");
  return path;
}

async function defaultHasher(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(bytes).buffer as ArrayBuffer);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function boundedBody(response: Response, maximum: number): Promise<Uint8Array> {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > maximum)) {
    throw new AssetValidationError("asset response exceeds its byte bound");
  }
  if (response.body === null) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximum) throw new AssetValidationError("asset response exceeds its byte bound");
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const result = new Uint8Array(total);
  let position = 0;
  for (const chunk of chunks) {
    result.set(chunk, position);
    position += chunk.byteLength;
  }
  return result;
}

function parseJson(bytes: Uint8Array, label: string): unknown {
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch (error) {
    throw new AssetValidationError(`${label} is not valid UTF-8 JSON: ${String(error)}`);
  }
}

function fileDescriptor(value: unknown, label: string, dataSha: string): FileDescriptor {
  const record = object(value, label);
  const path = safeVersionPath(string(record.path, `${label}.path`), dataSha);
  const bytes = integer(record.bytes, `${label}.bytes`, MAX_ASSET_BYTES);
  const sha256 = string(record.sha256, `${label}.sha256`);
  if (!SHA_PATTERN.test(sha256)) throw new AssetValidationError(`${label} checksum is invalid`);
  return { path, bytes, sha256 };
}

function parseMetadata(value: unknown): WorkerMetadata {
  const record = object(value, "worker metadata");
  exactKeys(record, ["formatVersion", "sourceSha", "dataSha", "manifestPath", "manifestSha256"]);
  const metadata = {
    formatVersion: integer(record.formatVersion, "metadata formatVersion"),
    sourceSha: string(record.sourceSha, "metadata sourceSha"),
    dataSha: string(record.dataSha, "metadata dataSha"),
    manifestPath: string(record.manifestPath, "metadata manifestPath"),
    manifestSha256: string(record.manifestSha256, "metadata manifestSha256"),
  };
  if (metadata.formatVersion !== FORMAT_VERSION || !COMMIT_PATTERN.test(metadata.sourceSha) ||
      !COMMIT_PATTERN.test(metadata.dataSha) || !SHA_PATTERN.test(metadata.manifestSha256)) {
    throw new AssetValidationError("worker metadata version or provenance is invalid");
  }
  safeVersionPath(metadata.manifestPath, metadata.dataSha);
  if (metadata.manifestPath !== `_data/${metadata.dataSha}/manifest.json`) {
    throw new AssetValidationError("manifest path is not canonical");
  }
  return metadata;
}

function parseManifest(value: unknown, metadata: WorkerMetadata): Manifest {
  const record = object(value, "manifest");
  exactKeys(record, ["formatVersion", "sourceSha", "dataSha", "counts", "sharding", "paragraphIndex"]);
  const countsValue = object(record.counts, "manifest counts");
  exactKeys(countsValue, ["articles", "selectableArticles", "sourceParagraphs", "selectableParagraphs"]);
  const shardingValue = object(record.sharding, "manifest sharding");
  exactKeys(shardingValue, ["algorithm", "shardCount", "mask", "recordsSortedBy", "shards"]);
  if (!Array.isArray(shardingValue.shards)) throw new AssetValidationError("manifest shards must be an array");
  const shardCount = integer(shardingValue.shardCount, "shardCount");
  if (shardCount === 0 || (shardCount & (shardCount - 1)) !== 0 || shardCount > 16_384 ||
      integer(shardingValue.mask, "shard mask") !== shardCount - 1 || shardingValue.shards.length !== shardCount ||
      shardingValue.algorithm !== "uint32-id-bitmask" || shardingValue.recordsSortedBy !== "numeric-id") {
    throw new AssetValidationError("manifest sharding contract is invalid");
  }
  const seen = new Set<number>();
  const width = Math.max(3, String(shardCount - 1).length);
  const shards = shardingValue.shards.map((entry, position): ShardDescriptor => {
    const descriptorValue = object(entry, `shard ${position}`);
    exactKeys(descriptorValue, ["shard", "path", "recordCount", "bytes", "sha256"]);
    const descriptor = {
      ...fileDescriptor(descriptorValue, `shard ${position}`, metadata.dataSha),
      shard: integer(descriptorValue.shard, "shard number"),
      recordCount: integer(descriptorValue.recordCount, "shard recordCount"),
    };
    if (descriptor.shard !== position || descriptor.shard >= shardCount || seen.has(descriptor.shard) ||
        descriptor.path !== `_data/${metadata.dataSha}/shards/${String(descriptor.shard).padStart(width, "0")}.json`) {
      throw new AssetValidationError("shard path or membership is invalid");
    }
    seen.add(descriptor.shard);
    return descriptor;
  });
  const paragraphValue = object(record.paragraphIndex, "paragraph index descriptor");
  exactKeys(paragraphValue, ["path", "byteOrder", "layout", "bytes", "sha256"]);
  const paragraphIndex = {
    ...fileDescriptor(paragraphValue, "paragraph index", metadata.dataSha),
    byteOrder: string(paragraphValue.byteOrder, "paragraph index byteOrder"),
    layout: string(paragraphValue.layout, "paragraph index layout"),
  };
  const counts = {
    articles: integer(countsValue.articles, "article count"),
    selectableArticles: integer(countsValue.selectableArticles, "selectable article count"),
    sourceParagraphs: integer(countsValue.sourceParagraphs, "source paragraph count"),
    selectableParagraphs: integer(countsValue.selectableParagraphs, "selectable paragraph count"),
  };
  const formatVersion = integer(record.formatVersion, "manifest formatVersion");
  const sourceSha = string(record.sourceSha, "manifest sourceSha");
  const dataSha = string(record.dataSha, "manifest dataSha");
  if (formatVersion !== metadata.formatVersion || sourceSha !== metadata.sourceSha || dataSha !== metadata.dataSha ||
      paragraphIndex.byteOrder !== "big-endian" || paragraphIndex.path !== `_data/${metadata.dataSha}/paragraph-index.bin` ||
      paragraphIndex.bytes !== 16 + counts.articles * 8 || counts.selectableArticles > counts.articles ||
      counts.selectableParagraphs > counts.sourceParagraphs || shards.reduce((sum, shard) => sum + shard.recordCount, 0) !== counts.articles) {
    throw new AssetValidationError("manifest provenance or counts do not match metadata");
  }
  return { formatVersion, sourceSha, dataSha, counts, sharding: {
    algorithm: "uint32-id-bitmask", shardCount, mask: shardCount - 1,
    recordsSortedBy: "numeric-id", shards,
  }, paragraphIndex };
}

function parseMedia(value: unknown): MediaItem[] {
  if (!Array.isArray(value)) throw new AssetValidationError("article media must be an array");
  return value.map((entry) => {
    const record = object(entry, "media item");
    exactKeys(record, ["type", "url", "alt"]);
    return { type: string(record.type, "media type"), url: string(record.url, "media URL"), alt: string(record.alt, "media alt") };
  });
}

function parseArticle(value: unknown): Article {
  const record = object(value, "article");
  exactKeys(record, ["id", "title", "date", "author", "editor", "text"], ["content_type", "media"]);
  const id = string(record.id, "article id");
  const numeric = Number(id);
  if (!ID_PATTERN.test(id) || !Number.isSafeInteger(numeric) || numeric > UINT32_MAX || String(numeric) !== id) {
    throw new AssetValidationError("article id is not a canonical positive uint32");
  }
  if (!Array.isArray(record.text) || record.text.some((paragraph) => typeof paragraph !== "string")) {
    throw new AssetValidationError("article text must be a string array");
  }
  const hasContentType = "content_type" in record;
  const hasMedia = "media" in record;
  if (hasContentType !== hasMedia) throw new AssetValidationError("article media fields must be paired");
  const article: Article = {
    id,
    title: string(record.title, "article title"),
    date: string(record.date, "article date"),
    author: string(record.author, "article author"),
    editor: string(record.editor, "article editor"),
    text: [...record.text] as string[],
  };
  if (hasContentType) {
    article.content_type = string(record.content_type, "article content type");
    article.media = parseMedia(record.media);
  }
  return article;
}

class PromiseLru<K, V> {
  private readonly values = new Map<K, Promise<V>>();
  constructor(private readonly maximum: number) {}

  get(key: K, load: () => Promise<V>): Promise<V> {
    const present = this.values.get(key);
    if (present !== undefined) {
      this.values.delete(key);
      this.values.set(key, present);
      return present;
    }
    const pending = load();
    this.values.set(key, pending);
    while (this.values.size > this.maximum) this.values.delete(this.values.keys().next().value!);
    void pending.catch(() => {
      if (this.values.get(key) === pending) this.values.delete(key);
    });
    return pending;
  }
}

export interface AssetRepositoryOptions {
  fetch: AssetFetch;
  origin?: string;
  hash?: ContentHasher;
  shardCacheSize?: number;
}

export class AssetRepository {
  private readonly fetchAsset: AssetFetch;
  private readonly origin: string;
  private readonly hash: ContentHasher;
  private statePromise: Promise<LoadedState> | undefined;
  private readonly shards: PromiseLru<string, Article[]>;

  constructor(options: AssetRepositoryOptions) {
    this.fetchAsset = options.fetch;
    this.origin = options.origin ?? "https://static-assets.invalid";
    this.hash = options.hash ?? defaultHasher;
    const cacheSize = options.shardCacheSize ?? 2;
    if (!Number.isSafeInteger(cacheSize) || cacheSize < 1 || cacheSize > 8) throw new RangeError("shard cache size must be 1..8");
    this.shards = new PromiseLru(cacheSize);
  }

  async provenance(): Promise<CorpusProvenance> {
    const { metadata } = await this.state();
    return { sourceSha: metadata.sourceSha, dataSha: metadata.dataSha };
  }

  async paragraphIndex(): Promise<ParagraphIndex> {
    return (await this.state()).index;
  }

  async getArticle(id: number | string): Promise<Article | undefined> {
    const numeric = typeof id === "number" ? id : Number(id);
    if (!Number.isSafeInteger(numeric) || numeric <= 0 || numeric > UINT32_MAX || String(numeric) !== String(id)) {
      throw new RangeError("article ID must be a canonical positive uint32");
    }
    const state = await this.state();
    if (!state.indexIds.has(numeric)) return undefined;
    const shardNumber = numeric & state.manifest.sharding.mask;
    const articles = await this.loadShard(state, shardNumber);
    return articles.find((article) => Number(article.id) === numeric);
  }

  private state(): Promise<LoadedState> {
    this.statePromise ??= this.loadState();
    void this.statePromise.catch(() => { this.statePromise = undefined; });
    return this.statePromise;
  }

  private async read(path: string, maximum: number): Promise<Uint8Array> {
    const response = await this.fetchAsset(new URL(`/${path}`, this.origin));
    if (!response.ok) throw new AssetValidationError(`asset fetch failed for ${path}: ${response.status}`);
    return boundedBody(response, maximum);
  }

  private async checkedRead(descriptor: FileDescriptor): Promise<Uint8Array> {
    const bytes = await this.read(descriptor.path, descriptor.bytes);
    if (bytes.byteLength !== descriptor.bytes || await this.hash(bytes) !== descriptor.sha256) {
      throw new AssetValidationError(`asset size or checksum mismatch: ${descriptor.path}`);
    }
    return bytes;
  }

  private async loadState(): Promise<LoadedState> {
    const metadataBytes = await this.read("worker-data.json", MAX_METADATA_BYTES);
    const metadata = parseMetadata(parseJson(metadataBytes, "worker metadata"));
    const manifestBytes = await this.read(metadata.manifestPath, MAX_MANIFEST_BYTES);
    if (await this.hash(manifestBytes) !== metadata.manifestSha256) throw new AssetValidationError("manifest checksum mismatch");
    const manifest = parseManifest(parseJson(manifestBytes, "manifest"), metadata);
    const index = decodeParagraphIndex(await this.checkedRead(manifest.paragraphIndex));
    if (index.articleCount !== manifest.counts.articles || index.selectableParagraphCount !== manifest.counts.selectableParagraphs) {
      throw new AssetValidationError("paragraph index counts do not match manifest");
    }
    let selectableArticles = 0;
    let previous = 0;
    for (const entry of index.entries) {
      if (entry.cumulativeOffset > previous) selectableArticles += 1;
      previous = entry.cumulativeOffset;
    }
    if (selectableArticles !== manifest.counts.selectableArticles) throw new AssetValidationError("selectable article count does not match index");
    return {
      metadata,
      manifest,
      index,
      indexIds: new Set(index.entries.map((entry) => entry.articleId)),
      indexPositions: new Map(index.entries.map((entry, position) => [entry.articleId, position])),
    };
  }

  private loadShard(state: LoadedState, shardNumber: number): Promise<Article[]> {
    const key = `${state.metadata.dataSha}:${shardNumber}`;
    return this.shards.get(key, async () => {
      const descriptor = state.manifest.sharding.shards.find((entry) => entry.shard === shardNumber);
      if (descriptor === undefined) throw new AssetValidationError("selected shard is absent from manifest");
      const value = parseJson(await this.checkedRead(descriptor), `shard ${shardNumber}`);
      if (!Array.isArray(value) || value.length !== descriptor.recordCount) throw new AssetValidationError("shard record count mismatch");
      const articles = value.map(parseArticle);
      const expectedEntries = state.index.entries.filter((entry) => (entry.articleId & state.manifest.sharding.mask) === shardNumber);
      const expectedIds = expectedEntries.map((entry) => entry.articleId);
      const actualIds = articles.map((article) => Number(article.id));
      if (actualIds.some((id, position) => id !== expectedIds[position]) || actualIds.length !== expectedIds.length) {
        throw new AssetValidationError("shard ordering or index membership mismatch");
      }
      for (let position = 0; position < articles.length; position += 1) {
        const indexPosition = state.indexPositions.get(expectedEntries[position]!.articleId)!;
        const previousOffset = indexPosition === 0 ? 0 : state.index.entries[indexPosition - 1]!.cumulativeOffset;
        const indexedCount = state.index.entries[indexPosition]!.cumulativeOffset - previousOffset;
        const contentCount = articles[position]!.text.reduce((count, paragraph) => count + (isSelectableParagraph(paragraph) ? 1 : 0), 0);
        if (contentCount !== indexedCount) throw new AssetValidationError("shard paragraph content does not match index offsets");
      }
      return articles;
    });
  }
}
