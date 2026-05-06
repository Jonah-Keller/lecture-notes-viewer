"""One-shot normalizer.

Each lecture .md file is named `NN-<slug>.md`. The canonical image dir for
that lecture is `<course>/images/<slug>/` (no number prefix) — that's what
the generator emits today. Historically some dirs were renamed to include
prefixes, and some .md files were generated when the lecture had a different
number, so dir names and in-markdown image paths drifted out of sync.

This script makes both consistent:
  1. For each .md, compute target_slug = filename minus leading `NN-` prefix
     and `.md`.
  2. Inspect every `images/<X>/slide-NN.<ext>` reference in the .md. If the
     dir `<X>` exists on disk, rename it to `target_slug` (idempotent — skip
     if already named that). Rewrite the reference to use `target_slug`.

Run:
    python scripts/normalize_image_paths.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"

IMG_RE = re.compile(r"images/([^/)\s]+)/slide-(\d+)\.([a-zA-Z]+)")


def canonical_slug(filename: str) -> str:
    """`02-endocrine-anatomy-and-histology.md` → `endocrine-anatomy-and-histology`."""
    m = re.match(r"^\d+-(.+)\.md$", filename)
    return m.group(1) if m else filename.removesuffix(".md")


def normalize_course(course_dir: Path) -> None:
    images_root = course_dir / "images"
    if not images_root.is_dir():
        return
    for md_path in sorted(course_dir.glob("*.md")):
        if md_path.name.startswith("_"):
            continue
        target_slug = canonical_slug(md_path.name)
        target_dir = images_root / target_slug
        text = md_path.read_text(encoding="utf-8")

        referenced_slugs = {m.group(1) for m in IMG_RE.finditer(text)}
        for slug in referenced_slugs:
            if slug == target_slug:
                continue
            src = images_root / slug
            if src.is_dir() and not target_dir.exists():
                print(f"  rename {course_dir.name}/images/{slug} → {target_slug}")
                src.rename(target_dir)

        # Fallback: if the target dir still doesn't exist, look for an
        # `NN-target_slug` dir on disk that nothing references and rename it.
        # Catches old prefixed dirs whose .md was already rewritten elsewhere.
        if not target_dir.exists():
            candidates = [
                d for d in images_root.iterdir()
                if d.is_dir() and re.match(rf"^\d+-{re.escape(target_slug)}$", d.name)
            ]
            if len(candidates) == 1:
                print(f"  rename {course_dir.name}/images/{candidates[0].name} → {target_slug}")
                candidates[0].rename(target_dir)

        new_text = IMG_RE.sub(
            lambda m: f"images/{target_slug}/slide-{m.group(2)}.{m.group(3)}",
            text,
        )
        if new_text != text:
            print(f"  rewrite {md_path.relative_to(ROOT)}")
            md_path.write_text(new_text, encoding="utf-8")


def main() -> int:
    if not CONTENT.is_dir():
        print(f"error: {CONTENT} not found", file=sys.stderr)
        return 1
    for course in sorted(CONTENT.iterdir()):
        if not course.is_dir() or course.name.startswith("."):
            continue
        print(f"course: {course.name}")
        normalize_course(course)
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
