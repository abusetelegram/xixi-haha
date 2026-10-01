# Worker corpus asset format (version 1)

`parse/export_worker.py` transforms a separately checked-out, canonical DATA
commit into immutable static assets. It calls the existing canonical corpus
loader and projects only `id`, `title`, `date`, `author`, `editor`, and `text`,
plus paired `content_type`/`media` when present. Values are passed through; raw
`article` HTML is never emitted.

Run it from the exact source checkout. Both paths must be repository roots at the
explicit full SHAs, with no tracked changes. Every file consumed from
`DATA/articles` must be present in that pinned commit; extra untracked or ignored
corpus files are rejected. The CLI exports from a private `git archive` snapshot
and rechecks both worktrees before publication, so mutable worktree bytes are
never labeled as committed data. Library callers may explicitly disable these
Git checks only for synthetic fixtures (`verify_refs=False`):

```sh
python parse/export_worker.py \
  --data-dir "$DATA_CHECKOUT" --output-dir "$OUTPUT" \
  --source-sha "$SOURCE_SHA" --data-sha "$DATA_SHA"
```

The default is 256 shards. `--shards` accepts a positive power of two up to
4096. The shard cap and fixed-file count are checked before shard-list
allocation. Output is built and fully re-read in a sibling staging directory
before replacement. The canonical checkout is read-only, and output paths that
equal, contain, or are contained by it are rejected before staging. Existing
regular files and live or dangling symlinks are also rejected without replacement
or backup. Each shard may be at most 25 MiB, while the encoded manifest has a
stricter 1 MiB format limit; the export may contain at most 20,000 files. These
Cloudflare limits remain external service constraints and must be rechecked
before deployment.

At DATA `1b3fc6018ad2f6c13d61a8ca869b9bbf26fc152a`, a verified real export
produced 15,111 articles (15,081 selectable), 207,679 source paragraphs and
207,679 selectable paragraphs, 259 files, and 66,606,378 bytes total. The exact
snapshot has zero blank/whitespace-only paragraph strings. The binary index is
120,904 bytes; 256 shards range from 106,478 to 458,626 bytes with at most 81
records. The next Worker stage must benchmark a
cold parse of the 458,626-byte worst shard and treat the current Free-plan 10 ms
CPU allowance as a screening target; this exporter measurement is not a claim
about Cloudflare runtime performance.

## Layout

```text
worker-data.json
_data/<dataSha>/manifest.json
_data/<dataSha>/paragraph-index.bin
_data/<dataSha>/shards/000.json
...
```

`worker-data.json` is minimal deployment metadata: format version, both pinned
SHAs, manifest path, and manifest SHA-256. The manifest is authoritative for
counts, shard count/algorithm, every artifact path, byte length, and SHA-256.
Shard arrays are compact UTF-8 JSON with records in numeric-ID order. Assignment
is deterministic: `uint32(articleId) & (shardCount - 1)`.

All IDs, counts, and cumulative offsets are checked to fit unsigned 32-bit
integers. Every canonical record occurs in exactly one shard; its original
`text` array and string payloads are unchanged, including blank entries and
padding. Manifest counts distinguish `sourceParagraphs` (all lookup array
entries) from `selectableParagraphs` and `selectableArticles`.

## Quote selectability and original indexes

A paragraph is selectable iff at least one Unicode code point is **not** in the
ECMAScript WhiteSpace + LineTerminator set used by JavaScript `trim`:
`U+0009..U+000D`, `U+0020`, `U+00A0`, `U+1680`, `U+2000..U+200A`,
`U+2028`, `U+2029`, `U+202F`, `U+205F`, `U+3000`, or `U+FEFF`. No text is
trimmed or normalized. Notably `U+200B` ZERO WIDTH SPACE and `U+180E` MONGOLIAN
VOWEL SEPARATOR are non-whitespace boundaries and are selectable.

The index stores selectable **ranks**, not raw `text` indexes. After index
selection identifies an article and local selectable rank, the runtime scans
only that selected article's original `text` array, applies the same code-point
rule, and returns the original array index and exact string at that rank. It
must not filter or renumber the lookup projection. The language-neutral
`worker-selectable-v1.json` fixture defines whitespace boundaries, preserved
payloads, and rank-to-original-index examples for the TypeScript implementation.

## Paragraph index binary contract

All integers use **big-endian (network) byte order**. The file is:

| Offset | Type | Meaning |
|---:|---|---|
| 0 | 4 bytes | ASCII magic `XHPI` |
| 4 | uint32 | format version (`1`) |
| 8 | uint32 | article record count |
| 12 | uint32 | total selectable paragraph ranks |
| 16 | repeated `(uint32,uint32)` | numeric article ID, cumulative selectable-paragraph offset |

There is one entry per article in globally sorted numeric-ID order. Offsets are
monotonic and the last equals the selectable-paragraph total. Articles without
selectable paragraphs therefore have a zero-width range (the same offset as
their predecessor) and cannot be selected as quotes, while remaining available
for lookup. `parse/tests/fixtures/worker-index-v1.bin` and its adjacent JSON
description are a tiny language-neutral golden contract for the TypeScript
decoder. To map an unbiased random integer `k` in
`[0, selectableParagraphCount)`, binary-search for the first cumulative offset
strictly greater than `k`; the local selectable rank is
`k - previousOffset`. The runtime must generate `k`
without modulo bias, then map that rank as specified above.

Consumers must read paths and `shardCount` from the checksummed manifest, reject
unknown format versions or checksum/count mismatches, and never fall back to a
different DATA version. Static assets are public deployment material, not a
privacy boundary.
