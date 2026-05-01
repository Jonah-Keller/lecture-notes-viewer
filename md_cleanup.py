"""Markdown post-processing shared by the one-shot cleaner and the generator.

Fixes the rendering issues that have shown up in practice:

0. LaTeX list environments (`\\begin{itemize}[nosep] \\item ... \\end{itemize}`,
   plus `enumerate` and nested variants). Converted to Markdown `-`/`1.`
   bullets with 4-space indentation per nesting level.

1. LaTeX table cruft left behind by `scripts/latex_to_md.py` and occasionally
   produced by Claude when it imitates LaTeX habits — `\\resizebox{...}{...}{`,
   `\\toprule`, `\\midrule`, `\\bottomrule`, `\\hline`, and colspec lines like
   `| {lll}`. All stripped.

2. Em-dash separator rows (`| — | — |`). The legacy converter ran a typography
   pass that replaced `---` with `—` on separator rows too, which made
   python-markdown stop recognizing the row as a table separator. Normalize
   any all-em-dash separator row back to hyphens.

3. Duplicate separator rows in a single contiguous table block. An earlier
   pass over-inserted `| --- | --- |` between every data row; collapse so
   each contiguous table block has at most one separator row (the first one).

4. Lazy bullet lists. python-markdown won't parse a `- item` line as a list
   item if it directly follows a non-empty paragraph line with no blank line
   between. Insert a blank line before list starts.
"""

from __future__ import annotations

import re


_RESIZEBOX_OPEN = re.compile(r"^\s*\\resizebox\{[^{}]*\}\{[^{}]*\}\{\s*$")
_COLSPEC_ROW = re.compile(r"^\s*\|\s*\{[a-zA-Z|*]+\}\s*\|?\s*$")
_BARE_RULE = re.compile(r"^\s*\\(top|mid|bottom)rule\b.*$")
_PIPED_RULE = re.compile(r"^\s*\|\s*\\(top|mid|bottom)rule\b.*$")
_HLINE = re.compile(r"^\s*\\hline\s*$")
_EMDASH_SEP_ROW = re.compile(r"^\s*\|(\s*—\s*\|)+\s*$")
_LIST_START = re.compile(r"^(\s*)([-*+]|\d+\.)\s+\S")
_HEADER_LIKE_ROW = re.compile(r"^\s*\|.*\|\s*$|^[^|]+(\s*\|\s*[^|]+)+\s*\|?\s*$")
_HYPHEN_SEP_ROW = re.compile(r"^\s*\|?(\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
_LATEX_LIST_BEGIN = re.compile(r"^(\s*)\\begin\{(itemize|enumerate)\}(\[[^\]]*\])?\s*$")
_LATEX_LIST_END = re.compile(r"^(\s*)\\end\{(itemize|enumerate)\}\s*$")
_LATEX_ITEM = re.compile(r"^(\s*)\\item\s+(.*)$")


def _convert_latex_lists(lines: list[str]) -> list[str]:
    """Convert `\\begin{itemize|enumerate}[opts] ... \\item ... \\end{...}` blocks
    into Markdown bullet/numbered lists. Handles nesting via a stack so inner
    lists get 4-space indentation under their parent."""
    out: list[str] = []
    stack: list[str] = []  # list of "itemize" | "enumerate" for each open list
    for line in lines:
        m_begin = _LATEX_LIST_BEGIN.match(line)
        if m_begin:
            stack.append(m_begin.group(2))
            continue
        m_end = _LATEX_LIST_END.match(line)
        if m_end and stack:
            stack.pop()
            continue
        m_item = _LATEX_ITEM.match(line)
        if m_item and stack:
            depth = max(0, len(stack) - 1)
            indent = "    " * depth
            marker = "-" if stack[-1] == "itemize" else "1."
            content = m_item.group(2)
            out.append(f"{indent}{marker} {content}")
            continue
        out.append(line)
    return out


def _strip_latex_table_cruft(lines: list[str]) -> list[str]:
    out: list[str] = []
    in_resizebox = False
    for line in lines:
        if _RESIZEBOX_OPEN.match(line):
            in_resizebox = True
            continue
        if in_resizebox and line.strip() == "}":
            in_resizebox = False
            continue
        if _COLSPEC_ROW.match(line):
            continue
        if _BARE_RULE.match(line) or _PIPED_RULE.match(line):
            continue
        if _HLINE.match(line):
            continue
        out.append(line)
    return out


def _normalize_emdash_separators(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if _EMDASH_SEP_ROW.match(line):
            # Replace each em-dash cell content with --- so python-markdown
            # recognizes the table separator.
            normalized = re.sub(r"—", "---", line)
            out.append(normalized)
        else:
            out.append(line)
    return out


def _looks_like_table_row(line: str) -> bool:
    """Heuristic: at least two cells (one or more `|` between non-empty content)."""
    s = line.strip()
    if not s.startswith("|") and "|" not in s:
        return False
    # Count interior pipes (excluding leading/trailing)
    inner = s.strip("|")
    return inner.count("|") >= 1


def _dedupe_separator_rows(lines: list[str]) -> list[str]:
    """In each contiguous table block (no blank lines), keep only the first
    `| --- | --- |` separator row; drop the rest. Fixes earlier over-insertion
    bug and is safe — a well-formed Markdown table never needs more than one
    separator row."""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # Identify start of a contiguous pipe-row block.
        if _looks_like_table_row(line):
            block_start = i
            block_end = i
            while block_end < n and _looks_like_table_row(lines[block_end]):
                block_end += 1
            # block: lines[block_start:block_end]
            seen_separator = False
            for blk_line in lines[block_start:block_end]:
                if _HYPHEN_SEP_ROW.match(blk_line):
                    if seen_separator:
                        continue  # drop redundant
                    seen_separator = True
                out.append(blk_line)
            i = block_end
        else:
            out.append(line)
            i += 1
    return out


def _ensure_blank_line_before_lists(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if _LIST_START.match(line):
            if out and out[-1].strip() != "" and not _LIST_START.match(out[-1]):
                out.append("")
        out.append(line)
    return out


def _ensure_blank_line_after_lists(lines: list[str]) -> list[str]:
    """If a non-indented, non-list paragraph line directly follows a list item,
    insert a blank line so it's parsed as a separate paragraph instead of being
    pulled into the previous bullet via lazy continuation. Indented continuations
    (>=2 leading spaces) are preserved."""
    out: list[str] = []
    for line in lines:
        if (
            out
            and _LIST_START.match(out[-1])
            and line.strip() != ""
            and not _LIST_START.match(line)
            and not line.startswith(" ")
            and not line.startswith("\t")
        ):
            out.append("")
        out.append(line)
    return out


def clean_markdown(text: str) -> str:
    """Apply all cleanups. Idempotent."""
    lines = text.splitlines()
    lines = _convert_latex_lists(lines)
    lines = _strip_latex_table_cruft(lines)
    lines = _normalize_emdash_separators(lines)
    lines = _dedupe_separator_rows(lines)
    lines = _ensure_blank_line_before_lists(lines)
    lines = _ensure_blank_line_after_lists(lines)
    cleaned = "\n".join(lines)
    # Collapse 3+ blank lines.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.rstrip() + "\n"
