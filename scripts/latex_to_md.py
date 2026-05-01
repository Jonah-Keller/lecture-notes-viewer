#!/usr/bin/env python3
"""Convert penn-lecture-notes LaTeX section files into Markdown for the viewer.

Handles the subset of LaTeX actually used in ~/Documents/latex/school/repro:
sections, subsections, subsubsections, paragraph, textbf/textit/emph/texttt,
itemize / enumerate (with nesting), tabular inside a center environment,
the four custom tcolorbox environments (intuitionbox, questionbox,
supplemental, promptbox), and common escape sequences. Math spans (`$...$`
and `$$...$$`) are passed through untouched so KaTeX can render them.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


DEFAULT_SRC = Path.home() / "Documents/latex/school/repro/sections"
DEFAULT_DST = Path(__file__).resolve().parent.parent / "content/reproduction"
TOPIC_NAME = "Reproduction"

def _placeholder(i: int) -> str:
    return f"\x00MATH{i}\x00"


def protect_math(text: str) -> tuple[str, list[str]]:
    """Replace every math span with a sentinel so later rewrites leave it alone."""
    spans: list[str] = []

    def store(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return _placeholder(len(spans) - 1)

    # $$...$$ first, then $...$ (non-greedy, single-line for inline math).
    text = re.sub(r"\$\$.+?\$\$", store, text, flags=re.DOTALL)
    text = re.sub(r"(?<!\\)\$[^\n$]+?(?<!\\)\$", store, text)
    return text, spans


def restore_math(text: str, spans: list[str]) -> str:
    for i, span in enumerate(spans):
        text = text.replace(_placeholder(i), span)
    return text


def find_matching_end(text: str, start: int, name: str) -> int:
    """Return index of the matching \\end{name} for the \\begin{name} ending at `start`."""
    depth = 1
    pos = start
    begin_re = re.compile(r"\\begin\{" + re.escape(name) + r"\}")
    end_re = re.compile(r"\\end\{" + re.escape(name) + r"\}")
    while depth > 0:
        next_begin = begin_re.search(text, pos)
        next_end = end_re.search(text, pos)
        if next_end is None:
            raise ValueError(f"Unclosed environment: {name}")
        if next_begin is not None and next_begin.start() < next_end.start():
            depth += 1
            pos = next_begin.end()
        else:
            depth -= 1
            pos = next_end.end()
            if depth == 0:
                return next_end.start()
    raise ValueError(f"Unclosed environment: {name}")


def convert_list(body: str, ordered: bool) -> str:
    items = re.split(r"\\item\s*", body)
    # Anything before the first \item is whitespace/comments; drop it.
    items = [i.strip() for i in items[1:] if i.strip()]
    marker = "1." if ordered else "-"
    out_lines = []
    for item in items:
        lines = item.splitlines()
        first = lines[0].strip()
        out_lines.append(f"{marker} {first}")
        for cont in lines[1:]:
            stripped = cont.strip()
            if not stripped:
                continue
            out_lines.append(f"  {stripped}")
    return "\n".join(out_lines)


TABLE_SEP_TOKEN = "\x00TBLSEP\x00"


def convert_table(body: str) -> str:
    body = re.sub(r"\\hline", "", body)
    rows_raw = [r.strip() for r in re.split(r"\\\\", body) if r.strip()]
    rows = []
    for row in rows_raw:
        cells = [c.strip() for c in row.split("&")]
        rows.append(cells)
    if not rows:
        return ""
    header, *data = rows
    width = len(header)
    for r in data:
        while len(r) < width:
            r.append("")
    md = ["| " + " | ".join(header) + " |",
          "| " + " | ".join([TABLE_SEP_TOKEN] * width) + " |"]
    for r in data:
        md.append("| " + " | ".join(r) + " |")
    return "\n".join(md)


def convert_environments(text: str) -> str:
    """Replace begin/end blocks with Markdown equivalents, handling nesting."""
    # Process innermost environments first by repeatedly scanning.
    def strip_tabular_colspec(body: str) -> str:
        # The column spec like `{l l l}` or `{|l|l|}` follows \begin{tabular}.
        return re.sub(r"^\s*\{[^{}]*\}\s*", "", body, count=1)

    patterns = [
        ("itemize", lambda body, arg: convert_list(body, ordered=False)),
        ("enumerate", lambda body, arg: convert_list(body, ordered=True)),
        ("intuitionbox", lambda body, arg: f":::intuitionbox\n{body.strip()}\n:::"),
        ("supplemental", lambda body, arg: f":::supplemental\n{body.strip()}\n:::"),
        ("promptbox", lambda body, arg: f":::promptbox\n{body.strip()}\n:::"),
        ("questionbox", lambda body, arg: (
            f':::questionbox title="{arg}"\n{body.strip()}\n:::' if arg
            else f":::questionbox\n{body.strip()}\n:::"
        )),
        ("tabular", lambda body, arg: convert_table(strip_tabular_colspec(body))),
        # center and figure-like wrappers: just unwrap.
        ("center", lambda body, arg: body.strip()),
    ]

    changed = True
    while changed:
        changed = False
        for name, handler in patterns:
            pattern = re.compile(
                r"\\begin\{" + re.escape(name) + r"\}(\[[^\]]*\])?\s*",
                re.DOTALL,
            )
            match = pattern.search(text)
            while match:
                # Ensure this \begin has no inner \begin{<same>} before its matching \end.
                body_start = match.end()
                try:
                    body_end = find_matching_end(text, body_start, name)
                except ValueError:
                    break
                body = text[body_start:body_end]
                # If there's a nested same-name env, skip past this one for now; the
                # nested one will be resolved first on a later pass since we scan
                # after rewriting.
                if re.search(r"\\begin\{" + re.escape(name) + r"\}", body):
                    match = pattern.search(text, body_end)
                    continue
                arg = match.group(1)
                arg_val = arg[1:-1] if arg else ""
                replacement = handler(body, arg_val)
                end_close_end = body_end + len(f"\\end{{{name}}}")
                text = text[:match.start()] + replacement + text[end_close_end:]
                changed = True
                match = pattern.search(text)
    return text


def convert_inline(text: str) -> str:
    # \paragraph{Foo.} -> bold lead-in on next line.
    text = re.sub(r"\\paragraph\{([^}]*)\}\s*", r"**\1** ", text)

    # Headings: \section -> # (single H1), \subsection -> ##, \subsubsection -> ###.
    text = re.sub(r"\\section\*?\{([^}]*)\}", r"# \1", text)
    text = re.sub(r"\\subsection\*?\{([^}]*)\}", r"## \1", text)
    text = re.sub(r"\\subsubsection\*?\{([^}]*)\}", r"### \1", text)

    # Inline formatting. Use a loop for nested cases like \textbf{\textit{x}}.
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\\textbf\{([^{}]*)\}", r"**\1**", text)
        text = re.sub(r"\\textit\{([^{}]*)\}", r"*\1*", text)
        text = re.sub(r"\\emph\{([^{}]*)\}", r"*\1*", text)
        text = re.sub(r"\\texttt\{([^{}]*)\}", r"`\1`", text)

    # Escape sequences / typography.
    text = text.replace("---", "—").replace("--", "–")
    text = text.replace("``", '"').replace("''", '"')
    text = re.sub(r"\\ldots\b", "…", text)
    text = re.sub(r"\\textendash\b", "–", text)
    text = re.sub(r"\\textemdash\b", "—", text)
    # \% \& \$ \_ \# outside math (math is protected).
    text = re.sub(r"\\([%&$_#])", r"\1", text)
    # \ followed by a space -> a plain space (e.g. "vs.\ n").
    text = text.replace("\\ ", " ")
    # Drop remaining stray control symbols we don't care about.
    text = re.sub(r"\\newpage\b", "", text)
    text = re.sub(r"\\clearpage\b", "", text)
    text = re.sub(r"\\noindent\b", "", text)
    return text


def cleanup(text: str) -> str:
    # Restore table separator sentinels.
    text = text.replace(TABLE_SEP_TOKEN, "---")
    # Strip trailing whitespace per line.
    text = "\n".join(line.rstrip() for line in text.splitlines())
    # Collapse 3+ blank lines to 2.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def convert_file(src: Path) -> str:
    raw = src.read_text(encoding="utf-8")
    # Strip LaTeX comments (% to end of line, but not \%).
    raw = re.sub(r"(?<!\\)%.*", "", raw)

    protected, spans = protect_math(raw)
    protected = convert_environments(protected)
    protected = convert_inline(protected)
    restored = restore_math(protected, spans)
    return cleanup(restored)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--dst", type=Path, default=DEFAULT_DST)
    args = parser.parse_args()

    src: Path = args.src
    dst: Path = args.dst
    if not src.is_dir():
        raise SystemExit(f"source directory not found: {src}")
    dst.mkdir(parents=True, exist_ok=True)

    tex_files = sorted(src.glob("*.tex"))
    if not tex_files:
        raise SystemExit(f"no .tex files in {src}")

    chapters: list[str] = []
    for tex in tex_files:
        md = convert_file(tex)
        out_name = tex.stem + ".md"
        (dst / out_name).write_text(md, encoding="utf-8")
        chapters.append(out_name)

    meta_lines = [f"name: {TOPIC_NAME}", "chapters:"]
    meta_lines.extend(f"  - {c}" for c in chapters)
    (dst / "meta.yaml").write_text("\n".join(meta_lines) + "\n", encoding="utf-8")

    print(f"wrote {len(chapters)} files to {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
