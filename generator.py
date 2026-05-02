"""Generate per-lecture Markdown notes from slides (PDF) + transcript via Claude.

Pipeline:
  1. Render uploaded PDF to images/<slug>/slide-NN.png (zero-padded, 1-indexed).
  2. Clean transcript (plain .txt, or .vtt/.srt with timestamp stripping).
  3. Call Claude Opus 4.7 with the prompt.txt as a cached system message plus
     all slide images and the transcript text in a single user message.
  4. Substitute the {LECTURE_SLUG} placeholder in the returned markdown so image
     paths resolve under the course's images directory.
  5. Write to content/<course>/_drafts/NN-<slug>.md.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

import fitz  # PyMuPDF
from PIL import Image

from md_cleanup import clean_markdown

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None  # type: ignore


BASE_DIR = Path(__file__).parent.resolve()
CONTENT_DIR = BASE_DIR / "content"
PROMPT_PATH = BASE_DIR / "prompt.txt"

DEFAULT_MODEL = os.environ.get("LECNOTES_MODEL", "claude-opus-4-7")
DEFAULT_MAX_EDGE = int(os.environ.get("LECNOTES_MAX_IMAGE_EDGE", "1568"))
DEFAULT_DPI = int(os.environ.get("LECNOTES_PDF_DPI", "150"))
MAX_OUTPUT_TOKENS = int(os.environ.get("LECNOTES_MAX_OUTPUT_TOKENS", "16000"))
DEFAULT_JPEG_QUALITY = int(os.environ.get("LECNOTES_JPEG_QUALITY", "85"))
# Soft cap on prior-lecture context size, in characters. Roughly 4 chars/token,
# so 400_000 ≈ 100K tokens. When exceeded, oldest chapters are dropped first.
PRIOR_CONTEXT_CHAR_CAP = int(os.environ.get("LECNOTES_PRIOR_CONTEXT_CAP", "400000"))


# ---------- Job state ----------


@dataclass
class Job:
    id: str
    course: str
    slug: str
    number: int
    title: str
    state: str = "queued"  # queued | rendering | generating | writing | done | error
    progress: str = ""
    error: str = ""
    draft_path: str = ""
    slide_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.updated_at = time.time()


class JobStore:
    """Thread-safe in-memory + on-disk job store. Survives reboots via JSON."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def _job_file(self, job: Job) -> Path:
        jobs_dir = CONTENT_DIR / job.course / "_jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        return jobs_dir / f"{job.id}.json"

    def create(self, course: str, slug: str, number: int, title: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], course=course, slug=slug,
                  number=number, title=title)
        with self._lock:
            self._jobs[job.id] = job
        self._persist(job)
        return job

    def update(self, job: Job, **kwargs) -> None:
        with self._lock:
            job.touch(**kwargs)
        self._persist(job)

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job:
            return job
        # Look on disk (survives reboot).
        for course_dir in CONTENT_DIR.iterdir():
            f = course_dir / "_jobs" / f"{job_id}.json"
            if f.is_file():
                data = json.loads(f.read_text())
                job = Job(**data)
                with self._lock:
                    self._jobs[job_id] = job
                return job
        return None

    def _persist(self, job: Job) -> None:
        try:
            self._job_file(job).write_text(json.dumps(asdict(job), indent=2))
        except OSError:
            pass  # non-fatal; state still held in memory

    def find_active(self, course: str, slug: str) -> Optional[Job]:
        """Return any in-flight job (queued/rendering/generating/writing) for
        this (course, slug). Used to make /upload idempotent against
        accidental double-submission."""
        active_states = {"queued", "rendering", "generating", "writing"}
        with self._lock:
            for job in self._jobs.values():
                if job.course == course and job.slug == slug and job.state in active_states:
                    return job
        return None

    def list_recent(self, *, max_done_age_s: float = 60,
                    max_error_age_s: float = 300) -> list[Job]:
        """Return all in-flight jobs across all courses, plus jobs that
        recently completed or errored. Used by the floating progress panel."""
        active_states = {"queued", "rendering", "generating", "writing"}
        now = time.time()
        out: list[Job] = []
        with self._lock:
            for job in self._jobs.values():
                if job.state in active_states:
                    out.append(job)
                elif job.state == "done" and (now - job.updated_at) <= max_done_age_s:
                    out.append(job)
                elif job.state == "error" and (now - job.updated_at) <= max_error_age_s:
                    out.append(job)
        out.sort(key=lambda j: j.created_at)
        return out


JOB_STORE = JobStore()


# ---------- Transcript cleaning ----------


_VTT_SRT_TS = re.compile(
    r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}.*$"
)
_SRT_CUE = re.compile(r"^\s*\d+\s*$")
_WEBVTT = re.compile(r"^\s*WEBVTT.*$", re.IGNORECASE)
_SPEAKER_TAG = re.compile(r"<v\s+[^>]+>(.*?)</v>", re.IGNORECASE)


def clean_transcript(raw: str, suffix: str) -> str:
    """Return plain text. .vtt/.srt timestamps and cue IDs are stripped."""
    suffix = suffix.lower().lstrip(".")
    if suffix not in {"vtt", "srt"}:
        return raw.strip()

    raw = _SPEAKER_TAG.sub(r"\1", raw)
    lines_out: list[str] = []
    for line in raw.splitlines():
        if _WEBVTT.match(line) or _VTT_SRT_TS.match(line) or _SRT_CUE.match(line):
            continue
        lines_out.append(line)

    # Collapse long runs of blank lines.
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines_out)).strip()
    return cleaned


# ---------- PDF rendering ----------


def render_pdf_to_slides(
    pdf_path: Path,
    images_dir: Path,
    *,
    dpi: int = DEFAULT_DPI,
    max_edge: int = DEFAULT_MAX_EDGE,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[Path]:
    """Render each PDF page to JPEG. 1-indexed, zero-padded filenames.
    JPEG (rather than PNG) keeps total payload under Anthropic's ~32 MB request
    limit even for 70+ slide decks; visually slide content is fine at q=85."""
    images_dir.mkdir(parents=True, exist_ok=True)
    # Clear any previous renders (PNG or JPEG) so stale extras don't linger.
    for f in list(images_dir.glob("slide-*.png")) + list(images_dir.glob("slide-*.jpg")):
        f.unlink()

    doc = fitz.open(pdf_path)
    try:
        total = doc.page_count
        width = max(2, len(str(total)))
        out_paths: list[Path] = []
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if max(img.size) > max_edge:
                ratio = max_edge / max(img.size)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            out_name = f"slide-{i + 1:0{width}d}.jpg"
            out_path = images_dir / out_name
            img.save(out_path, "JPEG", quality=jpeg_quality, optimize=True,
                     progressive=False)
            out_paths.append(out_path)
            if progress:
                progress(i + 1, total)
        return out_paths
    finally:
        doc.close()


# ---------- Claude API call ----------


_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _encode_image(path: Path) -> dict:
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "image/png")
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def _load_prompt(course: Optional[str] = None) -> str:
    """Return the base prompt, optionally augmented with the course-specific
    addendum at content/<course>/prompt.md. The addendum is appended under a
    "Course-specific guidance" heading so the model sees it after the global
    rules and treats it as a refinement rather than a replacement."""
    base = PROMPT_PATH.read_text(encoding="utf-8")
    if course:
        addendum_path = CONTENT_DIR / course / "prompt.md"
        if addendum_path.is_file():
            addendum = addendum_path.read_text(encoding="utf-8").strip()
            if addendum:
                base = (
                    base
                    + "\n\n# Course-specific guidance for this course\n\n"
                    + addendum
                    + "\n"
                )
    return base


def _load_prior_chapters(course: str) -> str:
    """Return concatenated prior chapter Markdown for the course (in
    meta.yaml order), or empty string if none. Trims oldest chapters when
    total size exceeds PRIOR_CONTEXT_CHAR_CAP so the model still gets the
    most recent narrative even on long courses."""
    import yaml

    course_dir = CONTENT_DIR / course
    meta_path = course_dir / "meta.yaml"
    if not meta_path.is_file():
        return ""
    meta = yaml.safe_load(meta_path.read_text()) or {}
    chapters = list(meta.get("chapters") or [])
    if not chapters:
        return ""

    pieces: list[tuple[str, str]] = []
    total = 0
    # Read in reverse (newest first) and prepend so we keep the most recent
    # chapters when trimming.
    for name in reversed(chapters):
        p = course_dir / name
        if not p.is_file():
            continue
        body = p.read_text(encoding="utf-8")
        chunk = f"### Prior lecture: {p.stem}\n\n{body.strip()}"
        if total + len(chunk) > PRIOR_CONTEXT_CHAR_CAP and pieces:
            break
        pieces.append((name, chunk))
        total += len(chunk) + 6  # separator overhead
    pieces.reverse()
    return "\n\n---\n\n".join(c for _, c in pieces)


def call_claude(
    *,
    transcript: str,
    slide_images: list[Path],
    lecture_title: str,
    course_name: str,
    course_slug: Optional[str] = None,
    lecture_slug: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    on_text: Optional[Callable[[str], None]] = None,
) -> str:
    """Stream a generation and return the full markdown string."""
    if Anthropic is None:
        raise RuntimeError("anthropic package not installed")

    client = Anthropic()
    system_prompt = _load_prompt(course_slug)
    prior_context = _load_prior_chapters(course_slug) if course_slug else ""

    # Build the user message: context header, transcript, then all slides in order.
    slug_for_paths = lecture_slug or "{LECTURE_SLUG}"
    pad_width = max(2, len(str(len(slide_images))))
    # Pick up actual on-disk extension (jpg vs png) so the path Claude is told
    # to write matches the rendered file.
    image_ext = slide_images[0].suffix.lstrip(".") if slide_images else "jpg"
    context_header = (
        f"Course: {course_name}\n"
        f"Lecture title (use this for the top-level # heading): {lecture_title}\n"
        f"Lecture slug for image paths (use this verbatim — DO NOT re-slugify "
        f"or derive from the title): {slug_for_paths}\n"
        f"Slide count: {len(slide_images)}\n\n"
        "TRANSCRIPT:\n"
        f"{transcript}\n\n"
        "SLIDES (in order, 1-indexed) follow as images. When you embed a slide "
        f"in the markdown, the image path MUST be exactly "
        f"`images/{slug_for_paths}/slide-NN.{image_ext}` where NN is zero-padded "
        f"to {pad_width} digits matching the slide's 1-indexed position. Use the "
        f"slug and extension above verbatim — do not invent a different slug "
        f"from the lecture title, and do not write `.png` if the extension is "
        f"`.{image_ext}`."
    )

    content: list[dict] = [{"type": "text", "text": context_header}]
    for path in slide_images:
        # Tag each image with its slide number so Claude knows the path to use.
        num = int(re.search(r"slide-(\d+)", path.stem).group(1))
        content.append({"type": "text", "text": f"Slide {num}:"})
        content.append(_encode_image(path))

    messages = [{"role": "user", "content": content}]

    # Cache the system prompt so repeated generations in a session are cheaper.
    system = [
        {"type": "text", "text": system_prompt,
         "cache_control": {"type": "ephemeral"}}
    ]
    # Second cached block: prior lectures in this course, so each new lecture
    # can be anchored explicitly in what's already been covered. Cached
    # separately so the model fee for it amortizes across regenerations.
    if prior_context:
        system.append({
            "type": "text",
            "text": (
                "PRIOR LECTURES IN THIS COURSE — these have already been "
                "delivered and the student has notes for them. Use this to "
                "anchor every new term, idea, or named structure introduced "
                "in the current lecture. When a concept builds on, refines, "
                "or contradicts something covered earlier, briefly say so "
                "(e.g. \"as introduced in the gametogenesis lecture, ...\") "
                "so the student can place the new material on top of the "
                "scaffolding they already have. Do NOT re-derive or re-explain "
                "prior material in depth — one phrase of cross-reference is "
                "usually enough.\n\n" + prior_context
            ),
            "cache_control": {"type": "ephemeral"},
        })

    chunks: list[str] = []
    with client.messages.stream(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
            if on_text:
                on_text(text)
    return "".join(chunks)


# ---------- High-level orchestration ----------


def _course_dir(course: str) -> Path:
    return CONTENT_DIR / course


def _course_name(course: str) -> str:
    import yaml
    meta_path = _course_dir(course) / "meta.yaml"
    if meta_path.is_file():
        meta = yaml.safe_load(meta_path.read_text()) or {}
        if meta.get("name"):
            return str(meta["name"])
    return course.replace("-", " ").title()


def next_lecture_number(course: str) -> int:
    """Return next NN based on existing chapters + drafts."""
    course_dir = _course_dir(course)
    nums: list[int] = []
    for p in list(course_dir.glob("*.md")) + list((course_dir / "_drafts").glob("*.md")):
        m = re.match(r"(\d+)-", p.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\s-]", "", value.strip().lower())
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "untitled"


def run_generation_job(
    job: Job,
    *,
    pdf_path: Path,
    transcript_path: Path,
    transcript_suffix: str,
) -> None:
    """Run end-to-end; updates job state at each phase."""
    try:
        course_dir = _course_dir(job.course)
        images_dir = course_dir / "images" / job.slug
        drafts_dir = course_dir / "_drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)

        JOB_STORE.update(job, state="rendering", progress="rendering slides")
        slide_paths = render_pdf_to_slides(pdf_path, images_dir)
        JOB_STORE.update(job, slide_count=len(slide_paths),
                         progress=f"rendered {len(slide_paths)} slides")

        transcript_raw = transcript_path.read_text(encoding="utf-8", errors="replace")
        transcript = clean_transcript(transcript_raw, transcript_suffix)

        JOB_STORE.update(job, state="generating", progress="calling Claude")
        md = call_claude(
            transcript=transcript,
            slide_images=slide_paths,
            lecture_title=job.title,
            course_name=_course_name(job.course),
            course_slug=job.course,
            lecture_slug=job.slug,
        )

        # Backward-compat: substitute placeholder if Claude emitted it.
        md = md.replace("{LECTURE_SLUG}", job.slug)

        # If Claude wrapped the output in a fenced ```markdown block, strip it.
        md = _strip_md_fence(md).strip() + "\n"

        # Safety net: rewrite every image path so it points at the actual
        # rendered file on disk — correct slug AND correct extension. Catches
        # Claude deriving its own slug from the title or insisting on `.png`
        # when files are JPEG.
        actual_ext = "jpg"
        for f in images_dir.iterdir():
            if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                actual_ext = f.suffix.lstrip(".").lower()
                break

        def _fix_image(match: re.Match) -> str:
            num = match.group(1)
            return f"(images/{job.slug}/slide-{num}.{actual_ext})"

        md = re.sub(
            r"\(images/[^/)]+/slide-(\d+)\.[a-zA-Z]+\)",
            _fix_image,
            md,
        )

        # Apply post-processing (LaTeX table cruft, em-dash separators,
        # blank-line-before-list normalization).
        md = clean_markdown(md)

        draft_name = f"{job.number:02d}-{job.slug}.md"
        draft_path = drafts_dir / draft_name
        draft_path.write_text(md, encoding="utf-8")

        JOB_STORE.update(job, state="done", progress="draft ready",
                         draft_path=str(draft_path.relative_to(BASE_DIR)))
    except Exception as exc:  # pragma: no cover
        JOB_STORE.update(job, state="error",
                         error=f"{exc}\n{traceback.format_exc()}")


def _strip_md_fence(text: str) -> str:
    """If Claude wrapped output in ```markdown ... ``` fences, remove them."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[0].lstrip("`").strip().lower() in {"", "markdown", "md"}:
            # Find closing fence.
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip() == "```":
                    return "\n".join(lines[1:i])
    return text


def start_generation(
    *,
    course: str,
    slug: str,
    number: int,
    title: str,
    pdf_path: Path,
    transcript_path: Path,
    transcript_suffix: str,
    on_done: Optional[Callable[[Job], None]] = None,
) -> Job:
    """Create a job and run it in a background thread."""
    job = JOB_STORE.create(course=course, slug=slug, number=number, title=title)

    def _target() -> None:
        run_generation_job(job, pdf_path=pdf_path,
                           transcript_path=transcript_path,
                           transcript_suffix=transcript_suffix)
        if on_done and job.state == "done":
            try:
                on_done(job)
            except Exception:
                pass

    threading.Thread(target=_target, daemon=True, name=f"gen-{job.id}").start()
    return job
