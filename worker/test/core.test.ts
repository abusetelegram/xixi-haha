import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  AssetValidationError,
  decodeParagraphIndex,
  locateGlobalRank,
  paragraphAtSelectableRank,
  randomInteger,
  isSelectableParagraph,
  type RandomSource,
} from "../src/main";

const ROOT = resolve(import.meta.dirname, "../..");

class Uint32Sequence implements RandomSource {
  constructor(private readonly values: number[]) {}
  fill(bytes: Uint8Array): void {
    const value = this.values.shift();
    if (value === undefined) throw new Error("random sequence exhausted");
    new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).setUint32(0, value, false);
  }
}

describe("paragraph index golden contract", () => {
  const binary = new Uint8Array(readFileSync(resolve(ROOT, "parse/tests/fixtures/worker-index-v1.bin")));
  const oracle = JSON.parse(readFileSync(resolve(ROOT, "parse/tests/fixtures/worker-index-v1.json"), "utf8"));

  it("decodes the Python big-endian golden and every cumulative boundary", () => {
    const decoded = decodeParagraphIndex(binary);
    expect(decoded.articleCount).toBe(oracle.articleCount);
    expect(decoded.selectableParagraphCount).toBe(oracle.selectableParagraphCount);
    expect(decoded.entries).toEqual(oracle.entries.map((entry: Record<string, number>) => ({
      articleId: entry.articleId,
      cumulativeOffset: entry.cumulativeSelectableParagraphOffset,
    })));
    expect([0, 1, 2, 3].map((rank) => locateGlobalRank(decoded, rank))).toMatchObject([
      { articleId: 1, localRank: 0 },
      { articleId: 1, localRank: 1 },
      { articleId: 1, localRank: 2 },
      { articleId: 5, localRank: 0 },
    ]);
    expect(() => locateGlobalRank(decoded, 4)).toThrow(RangeError);
  });

  it("rejects truncation, trailing bytes, bad magic, version, ordering, totals, and ranges", () => {
    const variants: Uint8Array[] = [binary.slice(0, 15), new Uint8Array([...binary, 0])];
    const badMagic = binary.slice();
    badMagic[0] = 0;
    variants.push(badMagic);
    for (const [offset, value] of [[4, 2], [24, 1], [36, 3], [20, 5]] as const) {
      const copy = binary.slice();
      new DataView(copy.buffer, copy.byteOffset, copy.byteLength).setUint32(offset, value, false);
      variants.push(copy);
    }
    for (const value of variants) expect(() => decodeParagraphIndex(value)).toThrow(AssetValidationError);
  });
});

describe("selection and unbiased random ranges", () => {
  const fixture = JSON.parse(readFileSync(resolve(ROOT, "parse/tests/fixtures/worker-selectable-v1.json"), "utf8"));

  it("matches every cross-language whitespace case and preserves exact payload/index", () => {
    for (const testCase of fixture.cases) expect(isSelectableParagraph(testCase.text), testCase.label).toBe(testCase.selectable);
    for (const expected of fixture.rankMappingExample.selectedRanks) {
      expect(paragraphAtSelectableRank(fixture.rankMappingExample.text, expected.rank)).toEqual({
        quote: expected.exactPayload,
        paragraphIndex: expected.originalTextIndex,
      });
    }
    expect(() => paragraphAtSelectableRank(["", " "], 0)).toThrow(AssetValidationError);
  });

  it("uses rejection sampling rather than biased modulo and accepts full uint32 range", () => {
    expect(randomInteger(10, new Uint32Sequence([5, 6]))).toBe(6);
    expect(randomInteger(0x1_0000_0000, new Uint32Sequence([0xffff_ffff]))).toBe(0xffff_ffff);
    expect(() => randomInteger(0, new Uint32Sequence([]))).toThrow();
  });
});
