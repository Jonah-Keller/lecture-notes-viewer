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


# Per-course lock so two concurrent publishes (or a publish racing a delete)
# don't both rewrite _master.md at the same time and leave a half-written file.
# Same guarded-setdefault pattern as app._NUMBER_LOCKS.
_BUILD_LOCKS_MUTEX = threading.Lock()
_BUILD_LOCKS: dict[str, threading.Lock] = {}


def _build_lock(course: str) -> threading.Lock:
    with _BUILD_LOCKS_MUTEX:
        lock = _BUILD_LOCKS.get(course)
        if lock is None:
            lock = threading.Lock()
            _BUILD_LOCKS[course] = lock
        return lock


def build_master(course: str) -> Optional[Path]:
    with _build_lock(course):
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

        # Atomic-ish write: tmpfile + rename, so a reader never catches a
        # half-written master while we're rebuilding.
        out = course_dir / "_master.md"
        tmp = course_dir / "_master.md.tmp"
        tmp.write_text("\n\n".join(pieces), encoding="utf-8")
        tmp.replace(out)
        return out


def build_master_async(course: str) -> None:
    threading.Thread(target=build_master, args=(course,),
                     daemon=True, name=f"aggregate-{course}").start()
