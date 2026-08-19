# Lecture Notes Viewer

Slides (PDF) plus a transcript in, a written-up set of lecture notes out — figures
embedded, cross-referenced against everything already covered in the course.
Runs locally on your Mac, on your own Anthropic API key.

## Install

**[Download the installer →](https://jonah-keller.github.io/lecture-notes-viewer/)**

Double-click it. macOS blocks a downloaded script the first time — right-click
(Control-click) the file → **Open** → **Open**. Once only.

Prefer the terminal? Same installer, no Gatekeeper warning:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonah-Keller/lecture-notes-viewer/main/install.sh | bash
```

Either path will:

1. Install Xcode Command Line Tools if missing (a system popup — click Install)
2. Clone to `~/lecture-notes-viewer` and put **Launch Lecture Notes** on your Desktop
3. Build a virtualenv and download Chromium for PDF export (~150 MB, one time)
4. Open your browser on the **Setup** page

Every launch after that is just a double-click on the Desktop shortcut.

## Setup page

Two things to fill in, both at <http://localhost:8080/setup>:

**API key.** Create one at <https://console.anthropic.com/settings/keys> and add
a few dollars of credit. It's written to `.env` on your own machine and billed to
your own account.

**Model.** Sonnet 5 by default (~$0.25–0.60 a lecture) — good on dense slides at
about a fifth of Opus's price. Haiku 4.5 is a few cents a lecture and fine when
the lecture is carried by the transcript rather than the figures; Opus 5 is there
for the ones that are mostly pathway diagrams. Changing this takes effect
immediately, no restart.

**Library pack.** A `.zip` of already-written courses — notes and every slide
image. Download the one you were sent, then load it from the Setup page (it
auto-detects a pack sitting in `~/Downloads`). It's a straight copy onto your
machine: nothing regenerates, no API credit is spent, and reading it doesn't
need a key at all.

## Day-to-day use

- Double-click the Desktop launcher.
- **New lecture** → drop in the slide PDF and the transcript. Generation runs in
  the background; the job panel tracks it.
- Review the draft, publish it, export a PDF.
- **Prompt tuner** (per course) adjusts how notes are written for that course
  only, as an addendum to the base `prompt.txt`.

## Sharing your own courses

```bash
python3 make_library_pack.py                             # every course
python3 make_library_pack.py endocrinology reproduction  # just these
python3 make_library_pack.py --out ~/Desktop/pack.zip
```

Writes to `~/Downloads/psom-library.zip` by default. Notes, `meta.yaml` and slide
images go in; `_uploads` (the source PDFs and transcripts), `_drafts` and `_jobs`
stay out — they're working state, they're most of the bytes, and the original
decks aren't ours to hand around. Put the zip on Drive and share the link with
the people it's meant for.

## Troubleshooting

**"cannot be opened because it is from an unidentified developer"** — right-click
the installer → Open → Open. Or use the `curl | bash` line above.

**"git: command not found"** — `xcode-select --install`, click Install, wait, retry.

**"Python 3.9+ is required"** — install a newer Python from
<https://www.python.org/downloads/> and relaunch. Apple's bundled Python is usually fine.

**Launcher window closes immediately** — it's set to stay open on error. If it
still vanishes, run `~/lecture-notes-viewer/start.sh` from Terminal to see why.

**Port 8080 already in use** — quit whatever else is on that port.

**Generation button is greyed out** — no API key yet. Open Setup.

## Updating

The launcher runs `git pull` on each click, so fixes flow in automatically.
Manually: `cd ~/lecture-notes-viewer && git pull`.

## Layout

| | |
|---|---|
| `app.py` | Flask app, routes, markdown rendering |
| `setup_flow.py` | Setup page: API key, model, library pack import |
| `generator.py` | PDF → slide images → Claude → draft markdown |
| `aggregator.py` | Builds the per-course master document |
| `make_library_pack.py` | Builds a shareable course zip |
| `prompt.txt` | Base prompt for every lecture in every course |
| `docs/` | The download page (GitHub Pages) |
| `content/<course>/` | Notes, `images/`, `meta.yaml` — local only, never committed |
