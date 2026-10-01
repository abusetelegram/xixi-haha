import { AssetValidationError, EmptyCorpusError } from "./errors";

const MAGIC = [0x58, 0x48, 0x50, 0x49] as const;
const HEADER_BYTES = 16;
const ENTRY_BYTES = 8;
const UINT32_SPACE = 0x1_0000_0000;

export interface ParagraphIndexEntry {
  articleId: number;
  cumulativeOffset: number;
}

export interface ParagraphIndex {
  formatVersion: number;
  articleCount: number;
  selectableParagraphCount: number;
  entries: readonly ParagraphIndexEntry[];
}

export interface SelectedRank extends ParagraphIndexEntry {
  entryIndex: number;
  localRank: number;
  previousOffset: number;
}

export function decodeParagraphIndex(bytes: Uint8Array): ParagraphIndex {
  if (bytes.byteLength < HEADER_BYTES || MAGIC.some((value, i) => bytes[i] !== value)) {
    throw new AssetValidationError("paragraph index magic or header is invalid");
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const formatVersion = view.getUint32(4, false);
  const articleCount = view.getUint32(8, false);
  const selectableParagraphCount = view.getUint32(12, false);
  if (formatVersion !== 1 || bytes.byteLength !== HEADER_BYTES + articleCount * ENTRY_BYTES) {
    throw new AssetValidationError("paragraph index version or length is invalid");
  }

  const entries: ParagraphIndexEntry[] = [];
  let previousId = 0;
  let previousOffset = 0;
  for (let i = 0; i < articleCount; i += 1) {
    const offset = HEADER_BYTES + i * ENTRY_BYTES;
    const articleId = view.getUint32(offset, false);
    const cumulativeOffset = view.getUint32(offset + 4, false);
    if (articleId <= previousId || cumulativeOffset < previousOffset || cumulativeOffset > selectableParagraphCount) {
      throw new AssetValidationError("paragraph index entries are not sorted and monotonic");
    }
    entries.push({ articleId, cumulativeOffset });
    previousId = articleId;
    previousOffset = cumulativeOffset;
  }
  if ((entries.at(-1)?.cumulativeOffset ?? 0) !== selectableParagraphCount) {
    throw new AssetValidationError("paragraph index final offset does not match total");
  }
  return { formatVersion, articleCount, selectableParagraphCount, entries };
}

export function locateGlobalRank(index: ParagraphIndex, rank: number): SelectedRank {
  if (!Number.isSafeInteger(rank) || rank < 0 || rank >= index.selectableParagraphCount) {
    throw new RangeError("global paragraph rank is out of range");
  }
  let low = 0;
  let high = index.entries.length;
  while (low < high) {
    const middle = low + Math.floor((high - low) / 2);
    if (index.entries[middle]!.cumulativeOffset > rank) high = middle;
    else low = middle + 1;
  }
  const entry = index.entries[low];
  if (entry === undefined) throw new AssetValidationError("paragraph index cannot resolve rank");
  const previousOffset = low === 0 ? 0 : index.entries[low - 1]!.cumulativeOffset;
  return { ...entry, entryIndex: low, localRank: rank - previousOffset, previousOffset };
}

export function selectableEntryIndexes(index: ParagraphIndex): number[] {
  const result: number[] = [];
  let previousOffset = 0;
  index.entries.forEach((entry, position) => {
    if (entry.cumulativeOffset > previousOffset) result.push(position);
    previousOffset = entry.cumulativeOffset;
  });
  return result;
}

export function locateArticleRank(index: ParagraphIndex, entryIndex: number, localRank: number): SelectedRank {
  const entry = index.entries[entryIndex];
  if (entry === undefined) throw new RangeError("article index is out of range");
  const previousOffset = entryIndex === 0 ? 0 : index.entries[entryIndex - 1]!.cumulativeOffset;
  const width = entry.cumulativeOffset - previousOffset;
  if (!Number.isSafeInteger(localRank) || localRank < 0 || localRank >= width) {
    throw new RangeError("article paragraph rank is out of range");
  }
  return { ...entry, entryIndex, localRank, previousOffset };
}

export interface RandomSource {
  fill(bytes: Uint8Array): void;
}

export const cryptoRandomSource: RandomSource = {
  fill(bytes) {
    crypto.getRandomValues(bytes);
  },
};

export function randomInteger(upperExclusive: number, random: RandomSource = cryptoRandomSource): number {
  if (!Number.isSafeInteger(upperExclusive) || upperExclusive <= 0 || upperExclusive > UINT32_SPACE) {
    throw new EmptyCorpusError("random range must be between 1 and 2^32");
  }
  const rejectionFloor = UINT32_SPACE % upperExclusive;
  const bytes = new Uint8Array(4);
  for (;;) {
    random.fill(bytes);
    const value = new DataView(bytes.buffer, bytes.byteOffset, 4).getUint32(0, false);
    if (value >= rejectionFloor) return value % upperExclusive;
  }
}
