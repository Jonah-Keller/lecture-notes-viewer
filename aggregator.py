"""Build a single master document per course by concatenating published lectures.

Runs in a background thread after each new publish. Writes
`content/<course>/_master.md`. Image paths are left as-is (relative to the course
directory), so the master doc renders correctly when served through the viewer.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import yaml


BASE_DIR = Path(__file__).parent.resolve()
CONTENT_DIR = BASE_DIR / "content"


def _chapter_list(course_dir: Path) -> list[str]:
    meta = course_dir / "meta.yaml"
    if meta.is_file():
        data = yaml.safe_load(meta.read_text()) or {}
        chapters = data.get("chapters") or []
        if chapters:
            return list(chapters)
    return sorted(p.name for p in course_dir.glob("*.md"))


def _course_name(course_dir: Path) -> str:
    meta = course_dir / "meta.yaml"
    if meta.is_file():
        data = yaml.safe_load(meta.read_text()) or {}
        if data.get("name"):
            return str(data["name"])
    return course_dir.name.replace("-", " ").title()


def build_master(course: str) -> Optional[Path]:
    course_dir = CONTENT_DIR / course
    if not course_dir.is_dir():
        return None
    chapters = _chapter_list(course_dir)
    if not chapters:
        return None

    pieces: list[str] = [f"# {_course_name(course_dir)} — Master Notes\n"]
    for name in chapters:
        p = course_dir / name
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8").rstrip() + "\n"
        pieces.append(text)

    out = course_dir / "_master.md"
    out.write_text("\n\n".join(pieces), encoding="utf-8")
    return out


def build_master_async(course: str) -> None:
    threading.Thread(target=build_master, args=(course,),
                     daemon=True, name=f"aggregate-{course}").start()
