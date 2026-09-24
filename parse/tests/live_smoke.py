#!/usr/bin/env python3
"""Bounded opt-in smoke test against the live jhsjk listing and one article."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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

    # Exercise the canonical CLI and its output contract in an isolated data path.
    with tempfile.TemporaryDirectory(prefix="xixi-haha-live-") as temporary:
        directory = Path(temporary)
        update.write_json(directory / "entries.json", [selected])
        update.atomic_write(directory / "articles" / aid, html)
        command = [
            sys.executable,
            str(Path(update.__file__).resolve()),
            "extract",
            "--data-dir",
            str(directory),
            "--timeout",
            "10",
            "--retries",
            "0",
            "--delay",
            "0.5",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        require(result.returncode == 0,
                "parser CLI failed: {}".format(result.stderr.strip() or result.stdout.strip()))
        output = update.read_json(directory / "result-min.json", None)
        require(isinstance(output, dict) and set(output) == {aid},
                "parser CLI did not write exactly the selected live article")
        expected = {key: parsed[key] for key in ("title", "date", "author", "editor", "text")}
        require(output[aid] == expected, "parser CLI output differs from validated live content")

    print(json.dumps({
        "requests": 2,
        "listing_url": update.BASE_URL + "/testnew/result?page=1&source=2",
        "article_url": article_url,
        "listing_rows": len(rows),
        "listing_total": total,
        "article_id": aid,
        "text_paragraphs": len(parsed["text"]),
        "text_characters": sum(len(line) for line in parsed["text"]),
        "cli_output_validated": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
