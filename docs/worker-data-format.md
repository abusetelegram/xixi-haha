# Worker corpus asset format (version 1)

`parse/export_worker.py` transforms a separately checked-out, canonical DATA
commit into immutable static assets. It calls the existing canonical corpus
loader and projects only `id`, `title`, `date`, `author`, `editor`, and `text`,
plus paired `content_type`/`media` when present. Values are passed through; raw
`article` HTML is never emitted.

Run it from the exact source checkout. Both checkouts' `HEAD`s must equal the
explicit full SHAs:

```sh
python parse/export_worker.py \
  --data-dir "$DATA_CHECKOUT" --output-dir "$OUTPUT" \
  --source-sha "$SOURCE_SHA" --data-sha "$DATA_SHA"
```

The default is 256 shards. `--shards` accepts a positive power of two, allowing
512 or 1024 after measurement without a runtime constant change. Output is
built and fully re-read in a sibling staging directory before replacement. The
canonical checkout is read-only and output inside it is rejected. The build
fails if any asset exceeds 25 MiB or if the export exceeds 20,000 files; these
Cloudflare limits remain external service constraints and must be rechecked
before deployment.

At DATA `1b3fc6018ad2f6c13d61a8ca869b9bbf26fc152a` and source
`2b80c2820b8539b21844f35622666446fe14e38c`, a verified real export produced
15,111 articles (15,081 nonempty), 207,679 paragraphs, 259 files, and 66,606,320
bytes total. The binary index is 120,904 bytes; 256 shards range from 106,478 to
458,626 bytes with at most 81 records. The next Worker stage must benchmark a
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
integers. Every canonical record occurs in exactly one shard; empty-text records
remain available for article lookup.

## Paragraph index binary contract

All integers use **big-endian (network) byte order**. The file is:

| Offset | Type | Meaning |
|---:|---|---|
| 0 | 4 bytes | ASCII magic `XHPI` |
| 4 | uint32 | format version (`1`) |
| 8 | uint32 | article record count |
| 12 | uint32 | total nonempty paragraphs |
| 16 | repeated `(uint32,uint32)` | numeric article ID, cumulative paragraph offset |

There is one entry per article in globally sorted numeric-ID order. Offsets are
monotonic and the last equals the paragraph total. Empty articles therefore
have a zero-width range (the same offset as their predecessor) and cannot be
selected as quotes, while remaining indexed for consistency checks and lookup.
`parse/tests/fixtures/worker-index-v1.bin` and its adjacent JSON description are
a tiny language-neutral golden contract for the TypeScript decoder.
To map an unbiased random integer `k` in `[0, paragraphCount)`, binary-search for
the first cumulative offset strictly greater than `k`; the paragraph index is
`k - previousOffset`. The runtime must generate `k` without modulo bias.

Consumers must read paths and `shardCount` from the checksummed manifest, reject
unknown format versions or checksum/count mismatches, and never fall back to a
different DATA version. Static assets are public deployment material, not a
privacy boundary.
