#!/usr/bin/env python3
"""Generate tiny runtime assets through the production Python exporter."""

import json
from pathlib import Path
import shutil
import sys

WORKER = Path(__file__).resolve().parents[1]
ROOT = WORKER.parent
OUTPUT = WORKER / "test" / "generated-assets"
sys.path.insert(0, str(ROOT / "parse"))

from export_worker import _build_stage, _validate_stage  # noqa: E402


def main():
    rows = json.loads((WORKER / "test" / "fixtures" / "articles.json").read_text(encoding="utf-8"))
    records = {row["id"]: row for row in rows}
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    manifest = _build_stage(OUTPUT, records, "a" * 40, "b" * 40, 4)
    _validate_stage(OUTPUT, manifest, records)


if __name__ == "__main__":
    main()
