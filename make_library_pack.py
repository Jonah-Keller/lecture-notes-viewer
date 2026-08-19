#!/usr/bin/env python3
"""Build a shareable library pack from the courses in content/.

A pack is a zip of finished courses — published .md notes, meta.yaml, and every
slide image. It deliberately omits _uploads (the source PDFs and transcripts),
_drafts and _jobs: those are working state, they are the bulk of the bytes, and
the original slide decks are not mine to hand out.

    python3 make_library_pack.py                  # every course
    python3 make_library_pack.py endocrinology reproduction
    python3 make_library_pack.py --out ~/Desktop/psom-library.zip
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
CONTENT_DIR = BASE_DIR / "content"

EXCLUDE_DIRS = {"_uploads", "_jobs", "_drafts", "__pycache__", ".git"}
EXCLUDE_NAMES = {".DS_Store", ".Rhistory"}


def course_files(course_dir: Path):
    for path in sorted(course_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(course_dir)
        if EXCLUDE_DIRS & set(rel.parts):
            continue
        if path.name in EXCLUDE_NAMES or path.name.startswith("._"):
            continue
        yield path, rel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("courses", nargs="*", help="course slugs (default: all)")
    ap.add_argument("--out", default=str(Path.home() / "Downloads" / "psom-library.zip"))
    args = ap.parse_args()

    available = sorted(p.name for p in CONTENT_DIR.glob("*") if (p / "meta.yaml").is_file())
    wanted = args.courses or available
    unknown = [c for c in wanted if c not in available]
    if unknown:
        print(f"Unknown course(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"Available: {', '.join(available)}", file=sys.stderr)
        return 1

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    # Slide images are already JPEG, so stored (no deflate) is both faster to
    # build and essentially the same size.
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for course in wanted:
            course_dir = CONTENT_DIR / course
            n = 0
            for path, rel in course_files(course_dir):
                zf.write(path, str(Path(course) / rel))
                n += 1
            notes = len(list(course_dir.glob("*.md")))
            print(f"  {course:<24} {notes:>3} notes, {n:>5} files")

    size = out.stat().st_size
    print(f"\nWrote {out}  ({size / 1e6:,.0f} MB)")
    print("Upload it to Google Drive, share the link, and tell people to download it")
    print("and load it from the app's Setup screen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
