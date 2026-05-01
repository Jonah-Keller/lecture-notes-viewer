#!/usr/bin/env python3
"""Run md_cleanup over every Markdown file under content/.

Fixes the LaTeX table cruft and em-dash separator rows left behind by the
legacy LaTeX→Markdown converter, plus normalizes list spacing. Idempotent.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from md_cleanup import clean_markdown


def main() -> int:
    content = ROOT / "content"
    if not content.is_dir():
        print(f"no content directory at {content}", file=sys.stderr)
        return 1

    changed = 0
    scanned = 0
    for md in content.rglob("*.md"):
        scanned += 1
        original = md.read_text(encoding="utf-8")
        cleaned = clean_markdown(original)
        if cleaned != original:
            md.write_text(cleaned, encoding="utf-8")
            print(f"  fixed {md.relative_to(ROOT)}")
            changed += 1
    print(f"scanned {scanned}, changed {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
