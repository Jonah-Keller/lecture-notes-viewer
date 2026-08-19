"""First-run setup: API key, model choice, and importing a shared library pack.

Split out of app.py as a blueprint so the whole onboarding path — the thing a
new user sees before they have a key — lives in one file. app.py only has to
register it.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

BASE_DIR = Path(__file__).parent.resolve()
CONTENT_DIR = BASE_DIR / "content"
ENV_PATH = BASE_DIR / ".env"

# Rough per-lecture cost is for a 40-slide deck plus transcript, so people can
# see the tradeoff before they pick. Opus stays on the list because a dense
# pathway lecture is sometimes worth the money.
MODELS = [
    ("claude-sonnet-5", "Sonnet 5",
     "Default. Handles dense slides well at roughly a fifth of Opus's price. "
     "About $0.25–0.60 per lecture."),
    ("claude-haiku-4-5-20251001", "Haiku 4.5",
     "Cheapest. Good on transcript-heavy lectures, weaker on crowded figures "
     "and pathway diagrams. About $0.07–0.15 per lecture."),
    ("claude-opus-5", "Opus 5",
     "Most capable and most expensive — worth it for a lecture that is mostly "
     "diagrams. About $1.50–3.00 per lecture."),
]
MODEL_IDS = {m[0] for m in MODELS}

# Directories inside a course that are working state, never shipped in a pack.
PACK_EXCLUDE = {"_uploads", "_jobs", "_drafts"}

setup_bp = Blueprint("setup", __name__)


# ---------- .env read/write ----------


def read_env() -> dict[str, str]:
    if not ENV_PATH.is_file():
        return {}
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def write_env(updates: dict[str, str]) -> None:
    """Rewrite .env in place, preserving unrelated keys, comments and order."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.is_file() else []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    # Take effect without a restart.
    for key, value in updates.items():
        os.environ[key] = value


def api_key_set() -> bool:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return bool(key) and key != "sk-ant-..."


def masked_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key_set():
        return ""
    return f"{key[:11]}…{key[-4:]}" if len(key) > 18 else "set"


# ---------- Library pack ----------


def find_library_pack() -> Path | None:
    """Look where a downloaded pack actually lands, newest first."""
    candidates: list[Path] = []
    for folder in (Path.home() / "Downloads", Path.home() / "Desktop", BASE_DIR):
        if not folder.is_dir():
            continue
        for p in folder.glob("*.zip"):
            name = p.name.lower()
            if "library" in name or "lecture" in name or "psom" in name:
                candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Reject absolute paths and ../ traversal before extracting anything."""
    safe = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"Unsafe path in archive: {info.filename}")
        if name.startswith("__MACOSX/") or Path(name).name == ".DS_Store":
            continue
        safe.append(info)
    return safe


def import_pack(zip_path: Path, overwrite: bool = False) -> dict:
    """Extract a pack's course folders into content/.

    Extracts to a temp dir first so a corrupt archive can't leave content/ half
    written. A pack is a zip of course directories, either at the top level or
    one level down under a wrapper folder.
    """
    with tempfile.TemporaryDirectory(dir=str(BASE_DIR)) as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_dir, members=_safe_members(zf))

        roots = [p for p in tmp_dir.iterdir() if p.is_dir()]
        # Unwrap a single containing folder (what macOS "Compress" produces).
        if len(roots) == 1 and not (roots[0] / "meta.yaml").is_file():
            inner = [p for p in roots[0].iterdir() if p.is_dir()]
            if any((p / "meta.yaml").is_file() for p in inner):
                roots = inner

        courses = [p for p in roots if (p / "meta.yaml").is_file()]
        if not courses:
            raise ValueError(
                "No courses found in that zip. A library pack contains one "
                "folder per course, each with a meta.yaml inside."
            )

        CONTENT_DIR.mkdir(exist_ok=True)
        imported, skipped = [], []
        for src in courses:
            dest = CONTENT_DIR / src.name
            if dest.exists():
                if not overwrite:
                    skipped.append(src.name)
                    continue
                shutil.rmtree(dest)
            shutil.move(str(src), str(dest))
            imported.append(src.name)

    return {"imported": imported, "skipped": skipped}


# ---------- Routes ----------


@setup_bp.route("/setup")
def setup_view():
    pack = find_library_pack()
    return render_template(
        "setup.html",
        models=MODELS,
        current_model=os.environ.get("LECNOTES_MODEL") or MODELS[0][0],
        api_key_set=api_key_set(),
        masked_key=masked_key(),
        detected_pack=str(pack) if pack else "",
        detected_pack_name=pack.name if pack else "",
        detected_pack_size=f"{pack.stat().st_size / 1e6:,.0f} MB" if pack else "",
        courses=sorted(p.name for p in CONTENT_DIR.glob("*") if (p / "meta.yaml").is_file())
        if CONTENT_DIR.is_dir() else [],
    )


@setup_bp.route("/setup", methods=["POST"])
def setup_save():
    updates: dict[str, str] = {}
    key = (request.form.get("api_key") or "").strip()
    if key and not key.startswith("sk-ant-"):
        return render_setup_error("That doesn't look like an Anthropic key — they start with sk-ant-.")
    if key:
        updates["ANTHROPIC_API_KEY"] = key

    model = (request.form.get("model") or "").strip()
    if model:
        if model not in MODEL_IDS:
            return render_setup_error("Unknown model.")
        updates["LECNOTES_MODEL"] = model

    if updates:
        write_env(updates)
    return redirect(url_for("setup.setup_view", saved=1))


def render_setup_error(message: str):
    pack = find_library_pack()
    return render_template(
        "setup.html",
        models=MODELS,
        current_model=os.environ.get("LECNOTES_MODEL") or MODELS[0][0],
        api_key_set=api_key_set(),
        masked_key=masked_key(),
        detected_pack=str(pack) if pack else "",
        detected_pack_name=pack.name if pack else "",
        detected_pack_size=f"{pack.stat().st_size / 1e6:,.0f} MB" if pack else "",
        courses=sorted(p.name for p in CONTENT_DIR.glob("*") if (p / "meta.yaml").is_file())
        if CONTENT_DIR.is_dir() else [],
        error=message,
    ), 400


@setup_bp.route("/setup/library", methods=["POST"])
def setup_library():
    """Import a library pack, either the one we detected on disk or an upload."""
    overwrite = request.form.get("overwrite") == "1"
    upload = request.files.get("pack")
    tmp_upload: Path | None = None
    try:
        if upload is not None and upload.filename:
            if not upload.filename.lower().endswith(".zip"):
                return jsonify({"error": "Library packs are .zip files."}), 400
            fd, tmp_name = tempfile.mkstemp(suffix=".zip", dir=str(BASE_DIR))
            os.close(fd)
            tmp_upload = Path(tmp_name)
            upload.save(str(tmp_upload))
            zip_path = tmp_upload
        else:
            given = (request.form.get("path") or "").strip()
            zip_path = Path(given).expanduser() if given else (find_library_pack() or Path())
            if not zip_path.is_file():
                return jsonify({"error": "No library pack found. Choose the .zip file."}), 400

        result = import_pack(zip_path, overwrite=overwrite)
    except (zipfile.BadZipFile, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        if tmp_upload is not None and tmp_upload.exists():
            tmp_upload.unlink()

    return jsonify(result)
