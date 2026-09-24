# xixi-haha article data

This branch stores the machine-readable corpus as one canonical full record per
upstream article ID in [`articles/`](articles/). The schema is
[`schema.json`](schema.json).

## Contract

- `articles/<id>.json` is authoritative; `<id>` is a positive canonical decimal
  integer with no leading zeroes.
- Every record has exactly `id`, `title`, `date`, `author`, `editor`, `article`,
  and `text`. Existing historical values, including empty `text` arrays, are
  preserved.
- Automation is add-only. It must never edit or delete an existing article.
  Corrections require a separate explicit, reviewed process.
- Files are deterministic UTF-8 JSON with two-space indentation, LF endings,
  fixed field order, and one trailing newline.
- Generated aggregates and caches are not canonical and are not committed here.

## Use

Clone or download the data branch, then read `articles/*.json`. A branch archive
is available from GitHub:

<https://github.com/abusetelegram/xixi-haha/archive/refs/heads/data.zip>

Browse the repository data at:

<https://github.com/abusetelegram/xixi-haha/tree/data/articles>

The parser and validation tool remain on the code branch. From that checkout:

```sh
uv run --locked python parse/corpus.py validate --data-dir /path/to/data-worktree
```

GitHub Pages is optional; repository files and the branch archive are the core
distribution mechanism.
