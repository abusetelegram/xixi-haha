# xixi-haha article data

This branch stores the machine-readable corpus as one canonical full record per
upstream article ID in [`articles/`](articles/). The schema is
[`schema.json`](schema.json).

## Contract

- `articles/<id>.json` is authoritative; `<id>` is a positive canonical decimal
  integer with no leading zeroes.
- Every ordinary record has exactly `id`, `title`, `date`, `author`, `editor`,
  `article`, and `text`. Existing historical values, including empty `text`
  arrays, are preserved. A validated image-only record additionally has
  `content_type: "image"` and a nonempty `media` array of
  `{type: "image", url: <absolute HTTP(S) URL>, alt: <string>}` objects in
  article-body order; its `text` array is empty. Supported sources normalize to
  HTTP(S) URLs ending in `jpg`, `jpeg`, `png`, `gif`, `webp`, or `bmp` and must
  not be self/fragment/extensionless, hidden, zero-sized, or one-pixel evidence.
  The URL and original body HTML are stored, not the remote image bytes; static
  validation does not fetch or authenticate those bytes.
- Automation is add-only. It must never edit or delete an existing article.
  Corrections require a separate explicit, reviewed process.
- Files are deterministic UTF-8 JSON with two-space indentation, LF endings,
  fixed field order, and one trailing newline.
- Generated aggregates and caches are not canonical and are not committed here.

## Image-only records and migration order

The image extension is intentionally narrow: it is emitted only when the
recognized upstream article body has no readable text and contains a supported
substantive raster image. Site chrome, scripts, short denial/challenge shells,
tracking pixels, hidden/placeholder sources, unsafe URLs, and metadata-only
pages do not qualify. Ordinary records retain their original seven-field
serialization, and historical empty-text records remain unchanged.

Deploy the reader/parser code that understands these optional fields first.
Then update the data branch's `schema.json` in an explicit reviewed
metadata-only commit using this template. Only after both steps should
acquisition resume and publish the first image-only record. This avoids placing
a record on the data branch that old code or the old schema rejects.

## Use

Clone or download the data branch, then read `articles/*.json`. A branch archive
is available from GitHub:

<https://github.com/abusetelegram/xixi-haha/archive/refs/heads/data.zip>

Browse the repository data at:

<https://github.com/abusetelegram/xixi-haha/tree/data/articles>

Deterministic minimal/full JSON, TGZ, and provenance exports are attached to
successful update workflow runs:

<https://github.com/abusetelegram/xixi-haha/actions/workflows/update-data.yml>

The parser and validation tool remain on the code branch. From that checkout:

```sh
uv run --locked python parse/corpus.py validate --data-dir /path/to/data-worktree
```

GitHub Pages is optional; repository files and the branch archive are the core
distribution mechanism.
