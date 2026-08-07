#!/usr/bin/env python3
"""Build a self-contained GitHub Pages copy of the trace viewer."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from server import STATIC_DIR, read_report, report_index, safe_report_path
import server


ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    reports = (ROOT / "reports").resolve()
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(STATIC_DIR, output, dirs_exist_ok=True)

    server.REPORTS_DIR = reports
    items = report_index()
    data_dir = output / "data"
    traces_dir = data_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "index.json").write_text(
        json.dumps({"items": items, "reports": "Bundled reports"}, ensure_ascii=False),
        encoding="utf-8",
    )
    for item in items:
        path = safe_report_path(item["id"])
        if path is None:
            continue
        payload = read_report(path)
        payload["_meta"] = item
        target = traces_dir / item["id"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    print(f"Built {len(items)} traces in {output}")


if __name__ == "__main__":
    main()
