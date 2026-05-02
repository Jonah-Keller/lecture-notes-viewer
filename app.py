import os
import re
import shutil
import tempfile
import threading
import xml.etree.ElementTree as etree
from collections import defaultdict
from pathlib import Path
from typing import Optional

import markdown
import yaml
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from markdown.blockprocessors import BlockProcessor
from markdown.extensions import Extension

from aggregator import build_master, build_master_async
from generator import (
    JOB_STORE,
    next_lecture_number,
    slugify,
    start_generation,
)
from pdf_export import render_url_to_pdf

# Load .env before anything reads os.environ.
load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
CONTENT_DIR = BASE_DIR / "content"
UPLOADS_SUBDIR = "_uploads"
DRAFTS_SUBDIR = "_drafts"
MASTER_FILE = "_master.md"

CALLOUT_KINDS = {
    "intuitionbox": ("callout-intuition", "Intuition"),
    "questionbox": ("callout-question", ""),
    "supplemental": ("callout-supplemental", "Supplemental Information (not discussed in lecture)"),
    "promptbox": ("callout-prompt", "Self-test"),
    "synthesis": ("callout-synthesis", "Synthesis"),
    "case": ("callout-case", "Clinical case"),
}


class CalloutBlockProcessor(BlockProcessor):
    """Parse fenced `:::kind [title="..."] ... :::` blocks into styled divs."""

    # Accept three forms after the kind:
    #   :::questionbox
    #   :::questionbox title="Exam Note"
    #   :::questionbox[Exam Note]
    OPEN_RE = re.compile(
        r"^:::(?P<kind>intuitionbox|questionbox|supplemental|promptbox|synthesis|case)"
        r"(?:"
        r"\s+title=\"(?P<title_q>[^\"]*)\""
        r"|\s*\[(?P<title_b>[^\]]*)\]"
        r")?\s*$"
    )
    CLOSE_RE = re.compile(r"^:::\s*$")

    def test(self, parent, block):
        first_line = block.split("\n", 1)[0]
        return bool(self.OPEN_RE.match(first_line))

    def run(self, parent, blocks):
        first = blocks[0]
        lines = first.split("\n")
        open_match = self.OPEN_RE.match(lines[0])
        if not open_match:
            return False

        kind = open_match.group("kind")
        title_override = open_match.group("title_q") or open_match.group("title_b")
        css_class, default_title = CALLOUT_KINDS[kind]
        title = title_override if title_override is not None else default_title

        inner_blocks: list[str] = []
        rest = lines[1:]
        closed = False
        if rest and self.CLOSE_RE.match(rest[-1]):
            inner_blocks.append("\n".join(rest[:-1]))
            closed = True
            blocks.pop(0)
        else:
            if rest:
                inner_blocks.append("\n".join(rest))
            blocks.pop(0)
            while blocks and not closed:
                block = blocks.pop(0)
                block_lines = block.split("\n")
                if self.CLOSE_RE.match(block_lines[-1]):
                    inner_blocks.append("\n".join(block_lines[:-1]))
                    closed = True
                else:
                    inner_blocks.append(block)

        wrapper = etree.SubElement(parent, "div")
        wrapper.set("class", f"callout {css_class}")
        if title:
            header = etree.SubElement(wrapper, "div")
            header.set("class", "callout-header")
            header.text = title
        body = etree.SubElement(wrapper, "div")
        body.set("class", "callout-body")

        inner_md = "\n\n".join(b for b in inner_blocks if b.strip())
        self.parser.parseChunk(body, inner_md)
        return True


class CalloutExtension(Extension):
    def extendMarkdown(self, md):
        md.parser.blockprocessors.register(
            CalloutBlockProcessor(md.parser), "callout", 175
        )


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB upload cap


def list_topics():
    if not CONTENT_DIR.is_dir():
        return []
    topics = []
    for entry in sorted(CONTENT_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        slug = entry.name
        meta_path = entry / "meta.yaml"
        name = None
        chapters = None
        if meta_path.is_file():
            with meta_path.open() as f:
                meta = yaml.safe_load(f) or {}
            name = meta.get("name")
            chapters = meta.get("chapters")
        if not name:
            name = slug.replace("-", " ").replace("_", " ").title()
        if not chapters:
            chapters = sorted(
                p.name for p in entry.glob("*.md") if not p.name.startswith("_")
            )
        topics.append({"slug": slug, "name": name, "chapters": chapters})
    topics.sort(key=lambda t: t["name"].lower())
    return topics


def _list_published(course_slug: str) -> list[dict]:
    course_dir = CONTENT_DIR / course_slug
    if not course_dir.is_dir():
        return []
    out = []
    for p in sorted(course_dir.glob("*.md")):
        if p.name.startswith("_"):
            continue
        m = re.match(r"(\d+)-(.+)\.md$", p.name)
        if not m:
            continue
        out.append({
            "filename": p.name,
            "number": int(m.group(1)),
            "slug": m.group(2),
            "title": _first_heading(p) or p.stem,
        })
    out.sort(key=lambda c: c["number"])
    return out


def _list_drafts(course_slug: str) -> list[dict]:
    drafts_dir = CONTENT_DIR / course_slug / DRAFTS_SUBDIR
    if not drafts_dir.is_dir():
        return []
    out = []
    for p in sorted(drafts_dir.glob("*.md")):
        m = re.match(r"(\d+)-(.+)\.md$", p.name)
        if not m:
            continue
        out.append({
            "filename": p.name,
            "number": int(m.group(1)),
            "slug": m.group(2),
            "title": _first_heading(p) or p.stem,
        })
    return out


def _first_heading(md_path: Path) -> Optional[str]:
    try:
        with md_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        return None
    return None


def _render_markdown(source: str, course_slug: str) -> tuple[str, list[dict]]:
    md = markdown.Markdown(
        extensions=[
            "extra",
            "toc",
            "pymdownx.arithmatex",
            CalloutExtension(),
        ],
        extension_configs={
            "pymdownx.arithmatex": {"generic": True},
            "toc": {"slugify": lambda value, sep: _slugify(value, sep)},
        },
    )
    html = md.convert(source)
    html = re.sub(
        r'(<img[^>]+src=")images/',
        rf'\1/content/{course_slug}/images/',
        html,
    )
    anchors = []
    first = _first_h1(getattr(md, "toc_tokens", []))
    if first:
        anchors.append(first)
    return html, anchors


def render_topic(topic):
    topic_dir = CONTENT_DIR / topic["slug"]
    pieces = []
    anchors = []
    for chapter_file in topic["chapters"]:
        md_path = topic_dir / chapter_file
        if not md_path.is_file():
            continue
        source = md_path.read_text(encoding="utf-8")
        html, chapter_anchors = _render_markdown(source, topic["slug"])
        # Wrap every chapter in a section keyed on the file slug, so
        # sidebar `<a href="#<slug>">` always navigates to the right
        # lecture even if Claude's H1 wording differs from the slug.
        m = re.match(r"^\d+-(.+)\.md$", chapter_file)
        section_id = m.group(1) if m else chapter_file.removesuffix(".md")
        pieces.append(
            f'<section id="{section_id}" class="lecture-section">\n{html}\n</section>'
        )
        anchors.extend(chapter_anchors)
    return "\n".join(pieces), anchors


def _slugify(value, sep):
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[\s]+", sep, value)


def _first_h1(toc_tokens):
    for token in toc_tokens:
        if token.get("level") == 1:
            return {"id": token.get("id", ""), "title": token.get("name", "")}
    return None


@app.route("/")
def index():
    topics = list_topics()
    if not topics:
        return render_template(
            "topic.html",
            topics=[],
            current_slug=None,
            current_name=None,
            content="<p>No topics found in <code>content/</code>.</p>",
            anchors=[],
            drafts=[],
            master_exists=False,
            api_key_set=_api_key_set(),
            suggested_number=1,
        )
    return redirect(url_for("topic_view", topic_slug=topics[0]["slug"]))


@app.route("/topic/<topic_slug>")
def topic_view(topic_slug):
    topics = list_topics()
    current = next((t for t in topics if t["slug"] == topic_slug), None)
    if current is None:
        abort(404)
    content, anchors = render_topic(current)
    drafts = _list_drafts(topic_slug)
    published = _list_published(topic_slug)
    master_exists = (CONTENT_DIR / topic_slug / MASTER_FILE).is_file()
    return render_template(
        "topic.html",
        topics=topics,
        current_slug=topic_slug,
        current_name=current["name"],
        content=content,
        anchors=anchors,
        drafts=drafts,
        published=published,
        master_exists=master_exists,
        api_key_set=_api_key_set(),
        suggested_number=next_lecture_number(topic_slug),
    )


@app.route("/content/<topic_slug>/images/<path:filename>")
def topic_image(topic_slug, filename):
    images_dir = CONTENT_DIR / topic_slug / "images"
    if not images_dir.is_dir():
        abort(404)
    return send_from_directory(str(images_dir), filename)


# ---------- Upload + generation ----------


def _api_key_set() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# Per-course lock prevents two simultaneous uploads from claiming the same
# lecture number. The critical section: pick number -> reserve a placeholder
# draft file -> release. After that, even slow background work can't collide.
_NUMBER_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _course_number_lock(course: str) -> threading.Lock:
    return _NUMBER_LOCKS[course]


def _save_upload(file_storage, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    file_storage.save(str(dest))


@app.route("/courses/new", methods=["POST"])
def new_course():
    name = (request.form.get("name") or "").strip()
    if not name:
        return ("Course name required.", 400)
    slug_override = (request.form.get("slug") or "").strip()
    slug = slugify(slug_override) if slug_override else slugify(name)
    if not slug:
        return ("Could not derive a usable slug from that name.", 400)
    course_dir = CONTENT_DIR / slug
    if course_dir.exists():
        # Already exists — just navigate to it.
        return redirect(url_for("topic_view", topic_slug=slug))
    course_dir.mkdir(parents=True)
    (course_dir / "images").mkdir()
    meta = {"name": name, "chapters": []}
    (course_dir / "meta.yaml").write_text(
        yaml.safe_dump(meta, sort_keys=False), encoding="utf-8"
    )
    return redirect(url_for("topic_view", topic_slug=slug))


@app.route("/upload/<course_slug>", methods=["POST"])
def upload(course_slug):
    course_dir = CONTENT_DIR / course_slug
    if not course_dir.is_dir():
        abort(404, f"Unknown course: {course_slug}")
    if not _api_key_set():
        return jsonify({"error": "ANTHROPIC_API_KEY not set. Copy .env.example to .env."}), 400

    pdf = request.files.get("slides")
    transcript = request.files.get("transcript")
    if pdf is None or transcript is None:
        return jsonify({"error": "Upload both a PDF and a transcript file."}), 400
    if not pdf.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Slides must be a PDF."}), 400

    transcript_suffix = Path(transcript.filename).suffix.lower().lstrip(".")
    if transcript_suffix not in {"txt", "vtt", "srt"}:
        return jsonify({"error": "Transcript must be .txt, .vtt, or .srt."}), 400

    # Figure out title/slug/number.
    raw_title = (request.form.get("title") or "").strip()
    if not raw_title:
        raw_title = Path(pdf.filename).stem
    slug_override = (request.form.get("slug") or "").strip()
    slug = slugify(slug_override) if slug_override else slugify(raw_title)

    # Idempotency: if a job for this (course, slug) is already in flight
    # (typically caused by a double-click on Generate), return the existing
    # job rather than spawning a duplicate generation. Saves API spend and
    # keeps the UI sane.
    existing = JOB_STORE.find_active(course_slug, slug)
    if existing is not None:
        return jsonify({
            "job_id": existing.id,
            "slug": existing.slug,
            "number": existing.number,
            "title": existing.title,
            "deduplicated": True,
        })

    number_str = (request.form.get("number") or "").strip()
    drafts_dir = course_dir / DRAFTS_SUBDIR
    with _course_number_lock(course_slug):
        if number_str.isdigit():
            number = int(number_str)
        else:
            number = next_lecture_number(course_slug)
        # Reserve the (number, slug) pair on disk so a second concurrent
        # upload sees this as already taken. Stub gets overwritten when the
        # generation finishes.
        drafts_dir.mkdir(parents=True, exist_ok=True)
        stub_path = drafts_dir / f"{number:02d}-{slug}.md"
        if not stub_path.exists():
            stub_path.write_text("# (generating…)\n", encoding="utf-8")

    # Save uploads under _uploads/<slug>/ so they can be replayed if needed.
    uploads_dir = course_dir / UPLOADS_SUBDIR / slug
    if uploads_dir.is_dir():
        shutil.rmtree(uploads_dir)
    uploads_dir.mkdir(parents=True)
    pdf_path = uploads_dir / "slides.pdf"
    transcript_path = uploads_dir / f"transcript.{transcript_suffix}"
    _save_upload(pdf, pdf_path)
    _save_upload(transcript, transcript_path)

    job = start_generation(
        course=course_slug,
        slug=slug,
        number=number,
        title=raw_title,
        pdf_path=pdf_path,
        transcript_path=transcript_path,
        transcript_suffix=transcript_suffix,
    )
    return jsonify({"job_id": job.id, "slug": slug, "number": number, "title": raw_title})


@app.route("/jobs/active")
def jobs_active():
    jobs = JOB_STORE.list_recent()
    return jsonify({
        "jobs": [
            {
                "id": j.id,
                "course": j.course,
                "slug": j.slug,
                "number": j.number,
                "title": j.title,
                "state": j.state,
                "progress": j.progress,
                "error": j.error.split("\n")[0] if j.error else "",
                "slide_count": j.slide_count,
                "created_at": j.created_at,
                "updated_at": j.updated_at,
            }
            for j in jobs
        ]
    })


@app.route("/jobs/<job_id>")
def job_status(job_id):
    job = JOB_STORE.get(job_id)
    if job is None:
        abort(404)
    return jsonify({
        "id": job.id,
        "course": job.course,
        "slug": job.slug,
        "number": job.number,
        "title": job.title,
        "state": job.state,
        "progress": job.progress,
        "error": job.error,
        "draft_path": job.draft_path,
        "slide_count": job.slide_count,
    })


# ---------- Draft preview + publish ----------


@app.route("/drafts/<course_slug>/<draft_slug>")
def draft_view(course_slug, draft_slug):
    drafts_dir = CONTENT_DIR / course_slug / DRAFTS_SUBDIR
    # Accept either "NN-slug" or "slug"; match by suffix.
    candidates = sorted(drafts_dir.glob(f"*-{draft_slug}.md")) if drafts_dir.is_dir() else []
    if not candidates:
        abort(404)
    md_path = candidates[-1]
    source = md_path.read_text(encoding="utf-8")
    content, anchors = _render_markdown(source, course_slug)
    topics = list_topics()
    current = next((t for t in topics if t["slug"] == course_slug), None)
    return render_template(
        "draft.html",
        topics=topics,
        current_slug=course_slug,
        current_name=(current or {}).get("name", course_slug),
        content=content,
        anchors=anchors,
        draft_filename=md_path.name,
        draft_slug=draft_slug,
    )


@app.route("/publish/<course_slug>/<draft_slug>", methods=["POST"])
def publish(course_slug, draft_slug):
    course_dir = CONTENT_DIR / course_slug
    drafts_dir = course_dir / DRAFTS_SUBDIR
    candidates = sorted(drafts_dir.glob(f"*-{draft_slug}.md")) if drafts_dir.is_dir() else []
    if not candidates:
        abort(404)
    src = candidates[-1]
    dst = course_dir / src.name
    shutil.move(str(src), str(dst))

    # Append to meta.yaml chapters if not already present; keep sorted by NN prefix.
    meta_path = course_dir / "meta.yaml"
    if meta_path.is_file():
        data = yaml.safe_load(meta_path.read_text()) or {}
    else:
        data = {}
    if "name" not in data:
        data["name"] = course_slug.replace("-", " ").title()
    chapters = list(data.get("chapters") or [])
    if src.name not in chapters:
        chapters.append(src.name)
    chapters = sorted(
        set(chapters),
        key=lambda n: (int(re.match(r"(\d+)", n).group(1)) if re.match(r"(\d+)", n) else 9999, n),
    )
    data["chapters"] = chapters
    meta_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    build_master_async(course_slug)
    return redirect(url_for("topic_view", topic_slug=course_slug) + f"#{draft_slug}")


@app.route("/delete-lecture/<course_slug>/<lecture_slug>", methods=["POST"])
def delete_lecture(course_slug, lecture_slug):
    course_dir = CONTENT_DIR / course_slug
    if not course_dir.is_dir():
        abort(404)
    candidates = sorted(course_dir.glob(f"*-{lecture_slug}.md"))
    if not candidates:
        exact = course_dir / f"{lecture_slug}.md"
        if exact.is_file():
            candidates = [exact]
    if not candidates:
        abort(404)
    md = candidates[-1]
    md_name = md.name
    md.unlink()

    meta_path = course_dir / "meta.yaml"
    if meta_path.is_file():
        data = yaml.safe_load(meta_path.read_text()) or {}
        chapters = [c for c in (data.get("chapters") or []) if c != md_name]
        data["chapters"] = chapters
        meta_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    images = course_dir / "images" / lecture_slug
    if images.is_dir():
        shutil.rmtree(images)
    uploads = course_dir / UPLOADS_SUBDIR / lecture_slug
    if uploads.is_dir():
        shutil.rmtree(uploads)

    build_master_async(course_slug)
    return redirect(url_for("topic_view", topic_slug=course_slug))


@app.route("/discard/<course_slug>/<draft_slug>", methods=["POST"])
def discard_draft(course_slug, draft_slug):
    drafts_dir = CONTENT_DIR / course_slug / DRAFTS_SUBDIR
    if drafts_dir.is_dir():
        for p in drafts_dir.glob(f"*-{draft_slug}.md"):
            p.unlink()
    # Also remove uploads + rendered images for that slug.
    uploads = CONTENT_DIR / course_slug / UPLOADS_SUBDIR / draft_slug
    if uploads.is_dir():
        shutil.rmtree(uploads)
    images = CONTENT_DIR / course_slug / "images" / draft_slug
    if images.is_dir():
        shutil.rmtree(images)
    return redirect(url_for("topic_view", topic_slug=course_slug))


# ---------- Master document ----------


@app.route("/master/<course_slug>")
def master_view(course_slug):
    course_dir = CONTENT_DIR / course_slug
    master_path = course_dir / MASTER_FILE
    if not master_path.is_file():
        # Build on-demand if missing (e.g. first visit after publish).
        build_master(course_slug)
    if not master_path.is_file():
        abort(404)
    source = master_path.read_text(encoding="utf-8")
    content, anchors = _render_markdown(source, course_slug)
    topics = list_topics()
    current = next((t for t in topics if t["slug"] == course_slug), None)
    return render_template(
        "master.html",
        topics=topics,
        current_slug=course_slug,
        current_name=(current or {}).get("name", course_slug),
        content=content,
        anchors=anchors,
    )


@app.route("/master/<course_slug>/rebuild", methods=["POST"])
def master_rebuild(course_slug):
    build_master(course_slug)
    return redirect(url_for("master_view", course_slug=course_slug))


# ---------- Per-course prompt tuner ----------


PROMPT_TUNER_MODEL = os.environ.get("LECNOTES_TUNER_MODEL", "claude-sonnet-4-6")


def _course_addendum_path(course_slug: str) -> Path:
    return CONTENT_DIR / course_slug / "prompt.md"


def _base_prompt_text() -> str:
    return (BASE_DIR / "prompt.txt").read_text(encoding="utf-8")


@app.route("/prompt-tuner/<course_slug>")
def prompt_tuner_view(course_slug):
    course_dir = CONTENT_DIR / course_slug
    if not course_dir.is_dir():
        abort(404)
    addendum_path = _course_addendum_path(course_slug)
    addendum = addendum_path.read_text(encoding="utf-8") if addendum_path.is_file() else ""
    topics = list_topics()
    current = next((t for t in topics if t["slug"] == course_slug), None)
    return render_template(
        "prompt_tuner.html",
        topics=topics,
        current_slug=course_slug,
        current_name=(current or {}).get("name", course_slug),
        addendum=addendum,
        base_prompt=_base_prompt_text(),
        api_key_set=_api_key_set(),
    )


@app.route("/prompt-tuner/<course_slug>/chat", methods=["POST"])
def prompt_tuner_chat(course_slug):
    if not (CONTENT_DIR / course_slug).is_dir():
        abort(404)
    if not _api_key_set():
        return jsonify({"error": "ANTHROPIC_API_KEY not set."}), 400
    data = request.get_json(silent=True) or {}
    history = data.get("history") or []
    if not isinstance(history, list) or not history:
        return jsonify({"error": "Empty conversation."}), 400

    addendum_path = _course_addendum_path(course_slug)
    current_addendum = addendum_path.read_text(encoding="utf-8") if addendum_path.is_file() else ""

    system = (
        "You are helping a medical student craft per-course customizations "
        "for a lecture-notes generation prompt. The base prompt that runs "
        "for every lecture is shown below; the student wants to add "
        "course-specific emphasis WITHOUT breaking the base behavior. "
        "When proposing the new addendum, write the FULL proposed addendum "
        "text inside a fenced ```markdown ... ``` block — exactly the text "
        "that should be saved verbatim, no commentary inside the fence. "
        "The student's UI extracts the most recent ```markdown block from "
        "your messages and offers it as the saved addendum. Make additions "
        "concrete and scoped (e.g. \"for every disease, list 2–3 clinically "
        "confusable diagnoses with one-line distinguishing features\" — not "
        "vague exhortations like \"be more clinical\"). Keep the addendum "
        "tight; aim for a few short paragraphs of high-leverage rules. End "
        "each turn with a short follow-up question to drive the conversation "
        "forward.\n\n"
        "BASE PROMPT (unchanged for this course; addendum is appended after "
        "it under a 'Course-specific guidance' heading):\n---\n"
        + _base_prompt_text()
        + "\n---\n\nCURRENT COURSE ADDENDUM (may be empty):\n---\n"
        + (current_addendum.strip() or "(empty — no addendum yet)")
        + "\n---"
    )

    try:
        from anthropic import Anthropic
        client = Anthropic()
        resp = client.messages.create(
            model=PROMPT_TUNER_MODEL,
            max_tokens=2048,
            system=system,
            messages=history,
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )
    except Exception as exc:
        return jsonify({"error": f"Claude error: {exc}"}), 500

    return jsonify({"text": text})


@app.route("/prompt-tuner/<course_slug>/save", methods=["POST"])
def prompt_tuner_save(course_slug):
    if not (CONTENT_DIR / course_slug).is_dir():
        abort(404)
    data = request.get_json(silent=True) or {}
    addendum = (data.get("addendum") or "").strip()
    p = _course_addendum_path(course_slug)
    if addendum:
        p.write_text(addendum + "\n", encoding="utf-8")
    elif p.is_file():
        p.unlink()
    return jsonify({"saved": True, "exists": p.is_file(), "length": len(addendum)})


# ---------- PDF download ----------


def _pdf_response(pdf_bytes: bytes, filename: str) -> Response:
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _local_url(endpoint: str, **kwargs) -> str:
    """Build an absolute URL pointing back at this same Flask process. Uses
    the incoming request's host so it works whether you're on 127.0.0.1
    or a LAN IP."""
    path = url_for(endpoint, **kwargs)
    host = request.host  # includes :port
    scheme = request.scheme
    return f"{scheme}://{host}{path}"


@app.route("/topic/<topic_slug>.pdf")
def topic_pdf(topic_slug):
    if not (CONTENT_DIR / topic_slug).is_dir():
        abort(404)
    pdf = render_url_to_pdf(_local_url("topic_view", topic_slug=topic_slug))
    return _pdf_response(pdf, f"{topic_slug}.pdf")


@app.route("/master/<course_slug>.pdf")
def master_pdf(course_slug):
    course_dir = CONTENT_DIR / course_slug
    if not course_dir.is_dir():
        abort(404)
    if not (course_dir / MASTER_FILE).is_file():
        build_master(course_slug)
    pdf = render_url_to_pdf(_local_url("master_view", course_slug=course_slug))
    return _pdf_response(pdf, f"{course_slug}-master.pdf")


@app.route("/drafts/<course_slug>/<draft_slug>.pdf")
def draft_pdf(course_slug, draft_slug):
    drafts_dir = CONTENT_DIR / course_slug / DRAFTS_SUBDIR
    if not drafts_dir.is_dir() or not list(drafts_dir.glob(f"*-{draft_slug}.md")):
        abort(404)
    pdf = render_url_to_pdf(
        _local_url("draft_view", course_slug=course_slug, draft_slug=draft_slug)
    )
    return _pdf_response(pdf, f"{course_slug}-{draft_slug}-draft.pdf")


if __name__ == "__main__":
    # threaded=True so the PDF route can hit /topic/... on the same process.
    app.run(debug=True, port=8080, threaded=True)
