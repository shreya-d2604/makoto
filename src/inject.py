"""Marker-based injection of resume bullets into resume.tex."""

from __future__ import annotations

import re
from pathlib import Path

from fetch import PRRecord

START_MARKER = "% PR-SECTION-START"
END_MARKER = "% PR-SECTION-END"

_PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")

# Order doesn't matter here: each source character is mapped independently
# against the *original* text, so a replacement's own backslash can never
# be rescanned and escaped again.
_LATEX_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


class InjectionError(RuntimeError):
    """Raised when resume.tex is malformed or the injection markers are missing."""


def escape_latex(text: str) -> str:
    return "".join(_LATEX_ESCAPE_MAP.get(ch, ch) for ch in text)


def format_resume_item(description: str, record: PRRecord) -> str:
    """Render one PR as a \\resumeItem{...} line, matching this resume's existing bullet style:
    a bolded short repo name and a hyperlinked PR number, e.g.

        \\resumeItem{Fixed race condition in shutdown path (\\textbf{wasmCloud}, \\href{...}{\\textcolor{blue}{PR \\#1204}})}

    Only the description and repo name are user-derived text and need escaping;
    the URL and surrounding macros are literal LaTeX structure.
    """
    repo_short = record.repo_full_name.split("/")[-1]
    escaped_description = escape_latex(description)
    escaped_repo = escape_latex(repo_short)
    return (
        f"    \\resumeItem{{{escaped_description} "
        f"(\\textbf{{{escaped_repo}}}, \\href{{{record.url}}}{{\\textcolor{{blue}}{{PR \\#{record.number}}}}})}}"
    )


def inject(original_text: str, lines: list[str]) -> str:
    """Replace everything strictly between the markers with `lines`; leave the rest of the file untouched.

    `lines` are already-rendered LaTeX (e.g. from format_resume_item), one per resume line -
    this function only splices them in; it has no opinion on bullet formatting.
    """
    newline = "\r\n" if "\r\n" in original_text else "\n"
    file_lines = original_text.splitlines(keepends=True)

    start_idx = next((i for i, line in enumerate(file_lines) if line.strip() == START_MARKER), None)
    end_idx = next((i for i, line in enumerate(file_lines) if line.strip() == END_MARKER), None)

    if start_idx is None or end_idx is None:
        raise InjectionError(
            f"Could not find both injection markers ({START_MARKER!r} / {END_MARKER!r}) in resume.tex."
        )
    if end_idx <= start_idx:
        raise InjectionError("PR-SECTION-END appears before PR-SECTION-START in resume.tex.")

    middle = [line + newline for line in lines]
    new_lines = file_lines[: start_idx + 1] + middle + file_lines[end_idx:]
    return "".join(new_lines)


def inject_file(path: Path, lines: list[str]) -> str:
    """Read resume.tex, inject, and return the new full text (caller decides whether to write it)."""
    original_text = path.read_text()
    return inject(original_text, lines)


def extract_marked_urls(text: str) -> set[str]:
    """Find every GitHub PR URL currently between the injection markers, for diffing on commit."""
    lines = text.splitlines()
    try:
        start_idx = next(i for i, line in enumerate(lines) if line.strip() == START_MARKER)
        end_idx = next(i for i, line in enumerate(lines) if line.strip() == END_MARKER)
    except StopIteration:
        return set()
    middle = "\n".join(lines[start_idx + 1 : end_idx])
    return set(_PR_URL_RE.findall(middle))
