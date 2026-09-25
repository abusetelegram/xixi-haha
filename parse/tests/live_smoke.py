#!/usr/bin/env python3
"""Bounded opt-in smoke test: one live listing plus one live article."""

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus
import update


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    # Exactly one listing request and one article request, with no retries.
    client = update.Client(delay=0.5, timeout=10, retries=0)
    rows, total = update.parse_listing(client.listing(1), 1)
    require(rows, "live listing page 1 returned no articles")

    selected = rows[0]
    aid = selected["article_id"]
    article_url = update.BASE_URL + "/article/" + aid
    html = client.article(aid)
    parsed = update.parse_article(html, selected)
    require(parsed["id"] == aid, "parsed article ID does not match the listing")
    require(parsed["title"].strip(), "live article title is empty")
    require(parsed["text"] and all(line.strip() for line in parsed["text"]),
            "live article has no non-empty extracted paragraphs")

    # Exercise the fetch/cache and canonical create-only store contracts without
    # another network request or any repository-local output.
    class CachedResponse:
        def article(self, requested):
            require(requested == aid, "fetch requested a different article")
            return html

    with tempfile.TemporaryDirectory(prefix="xixi-haha-live-") as temporary:
        directory = Path(temporary)
        fetched = update.fetch_missing(
            directory, CachedResponse(), {aid: selected}, known=set())
        require(fetched["downloaded"] == 1 and len(fetched["records"]) == 1,
                "fetch contract did not return one downloaded record")
        added = corpus.create_articles(directory, fetched["records"], strict_content=True)
        require(added == 1, "canonical store did not add exactly one article")
        stored = corpus.load_articles(directory)
        require(stored == {aid: parsed}, "stored canonical record differs from live parse")
        require(update.html_cache_path(directory, aid).exists(),
                "validated HTML was not retained in the ignored cache")
        require((directory / "articles" / (aid + ".json")).exists(),
                "canonical article file is missing")

    print(json.dumps({
        "requests": 2,
        "listing_url": update.BASE_URL + "/testnew/result?page=1&source=2",
        "article_url": article_url,
        "listing_rows": len(rows),
        "listing_total": total,
        "article_id": aid,
        "text_paragraphs": len(parsed["text"]),
        "text_characters": sum(len(line) for line in parsed["text"]),
        "fetch_store_validated": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
