#!/usr/bin/env python3
"""Build a shareable library pack from the courses in content/.

A pack is a zip of finished courses — published .md notes, meta.yaml, and every
slide image. It deliberately omits _uploads (the source PDFs and transcripts),
_drafts and _jobs: those are working state, they are the bulk of the bytes, and
the original slide decks are not mine to hand out.

    python3 make_library_pack.py                  # every course
    python3 make_library_pack.py endocrinology reproduction
    python3 make_library_pack.py --out ~/Desktop/psom-library.zip

A course can be narrowed to specific lectures by their leading number, which is
how you build a small sampler someone can download in seconds:

    python3 make_library_pack.py brain-and-behavior endocrinology:3,4

A narrowed course ships a rewritten meta.yaml listing only the lectures that
are actually in it, and only the images those lectures reference — otherwise
the sidebar points at files that aren't there.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
CONTENT_DIR = BASE_DIR / "content"

EXCLUDE_DIRS = {"_uploads", "_jobs", "_drafts", "__pycache__", ".git"}
EXCLUDE_NAMES = {".DS_Store", ".Rhistory"}


def parse_spec(spec: str) -> tuple[str, set[int] | None]:
    """"endocrinology:3,4" -> ("endocrinology", {3, 4}); bare name -> (name, None)."""
    if ":" not in spec:
        return spec, None
    name, nums = spec.split(":", 1)
    return name, {int(n) for n in nums.split(",") if n.strip()}


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


def subset_files(course_dir: Path, keep: set[int]):
    """Files for just the numbered lectures, plus the images they reference.

    The images a lecture uses are read out of its markdown rather than guessed
    from the slug, because the two don't always match.
    """
    notes = []
    for md in sorted(course_dir.glob("*.md")):
        m = re.match(r"(\d+)-", md.name)
        if m and int(m.group(1)) in keep:
            notes.append(md)
    if not notes:
        raise SystemExit(f"No lectures numbered {sorted(keep)} in {course_dir.name}")

    wanted: list[tuple[Path, Path]] = []
    for md in notes:
        wanted.append((md, Path(md.name)))
        text = md.read_text(encoding="utf-8", errors="replace")
        for ref in sorted(set(re.findall(r"\((images/[^)\s]+)\)", text))):
            img = course_dir / ref
            if img.is_file():
                wanted.append((img, Path(ref)))
    return notes, wanted


def subset_meta(course_dir: Path, notes: list[Path]) -> str:
    """meta.yaml naming only the lectures actually in the pack."""
    import yaml
    meta = yaml.safe_load((course_dir / "meta.yaml").read_text()) or {}
    meta["chapters"] = [n.name for n in notes]
    return yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("courses", nargs="*", help="course slugs (default: all)")
    ap.add_argument("--out", default=str(Path.home() / "Downloads" / "psom-library.zip"))
    args = ap.parse_args()

    available = sorted(p.name for p in CONTENT_DIR.glob("*") if (p / "meta.yaml").is_file())
    specs = [parse_spec(c) for c in args.courses] or [(c, None) for c in available]
    unknown = [c for c, _ in specs if c not in available]
    if unknown:
        print(f"Unknown course(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"Available: {', '.join(available)}", file=sys.stderr)
        return 1

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    # Slide images are already JPEG, so stored (no deflate) is both faster to
    # build and essentially the same size.
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for course, keep in specs:
            course_dir = CONTENT_DIR / course
            if keep is None:
                n = 0
                for path, rel in course_files(course_dir):
                    zf.write(path, str(Path(course) / rel))
                    n += 1
                notes = len([p for p in course_dir.glob("*.md") if p.name != "_master.md"])
            else:
                # A trimmed course needs a trimmed meta.yaml, and _master.md is
                # derived from the full set so it is left out — the app rebuilds it.
                note_paths, files = subset_files(course_dir, keep)
                zf.writestr(str(Path(course) / "meta.yaml"), subset_meta(course_dir, note_paths))
                for path, rel in files:
                    zf.write(path, str(Path(course) / rel))
                n = len(files) + 1
                notes = len(note_paths)
            print(f"  {course:<24} {notes:>3} notes, {n:>5} files")

    size = out.stat().st_size
    print(f"\nWrote {out}  ({size / 1e6:,.0f} MB)")
    print("Upload it to Google Drive, share the link, and tell people to download it")
    print("and load it from the app's Setup screen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
