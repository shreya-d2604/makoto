from pathlib import Path

import pytest

from fetch import PRRecord
from inject import (
    InjectionError,
    escape_latex,
    extract_marked_urls,
    format_resume_item,
    inject,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "sample_resume.tex").read_text()


def make_record(**overrides) -> PRRecord:
    defaults = {
        "repo_full_name": "wasmCloud/wasmCloud",
        "repo_stars": 2427,
        "repo_description": "A universal, secure distributed application runtime.",
        "number": 1204,
        "title": "Fix race condition in host actor shutdown path",
        "body": "",
        "merged_at": "2026-04-17T00:00:00Z",
        "changed_files": 1,
        "additions": 4,
        "deletions": 1,
        "review_comments": 0,
        "url": "https://github.com/wasmCloud/wasmCloud/pull/1204",
    }
    defaults.update(overrides)
    return PRRecord(**defaults)


# --- escape_latex -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("&", r"\&"),
        ("_", r"\_"),
        ("%", r"\%"),
        ("#", r"\#"),
        ("$", r"\$"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
        ("\\", r"\textbackslash{}"),
    ],
)
def test_escape_single_special_char(raw: str, expected: str) -> None:
    assert escape_latex(raw) == expected


def test_escape_plain_text_unchanged() -> None:
    assert escape_latex("Fixed a bug in the parser") == "Fixed a bug in the parser"


def test_escape_pr_title_with_underscore() -> None:
    # The exact bug the spec calls out: an unescaped underscore breaks the document.
    assert escape_latex("Fix host_actor shutdown") == r"Fix host\_actor shutdown"


def test_escape_does_not_double_escape() -> None:
    # A naive sequential str.replace() implementation would re-escape the backslash
    # introduced by an earlier replacement (e.g. "&" -> "\&" -> "\\&" on a second pass).
    text = "A & B \\ C"
    result = escape_latex(text)
    assert result == r"A \& B \textbackslash{} C"
    assert r"\textbackslash{}\&" not in result


def test_escape_all_special_chars_together() -> None:
    raw = r"100% & foo_bar #1 ${cost} ~tilde ^caret \end"
    result = escape_latex(raw)
    assert result == (
        r"100\% \& foo\_bar \#1 \$\{cost\} \textasciitilde{}tilde "
        r"\textasciicircum{}caret \textbackslash{}end"
    )


# --- format_resume_item --------------------------------------------------------


def test_format_resume_item_basic_shape() -> None:
    record = make_record()
    line = format_resume_item("Fixed race condition in host actor shutdown path", record)

    assert line.strip().startswith(r"\resumeItem{")
    assert line.strip().endswith("}")
    assert r"\textbf{wasmCloud}" in line
    assert r"\href{https://github.com/wasmCloud/wasmCloud/pull/1204}" in line
    assert r"\textcolor{blue}{PR \#1204}" in line
    assert "Fixed race condition in host actor shutdown path" in line


def test_format_resume_item_uses_short_repo_name() -> None:
    record = make_record(repo_full_name="wasmCloud/wasmcloud.com")
    line = format_resume_item("Did something", record)
    assert r"\textbf{wasmcloud.com}" in line
    assert "wasmCloud/wasmcloud.com" not in line


def test_format_resume_item_escapes_description() -> None:
    record = make_record()
    line = format_resume_item("Fixed host_actor & 100% shutdown_path", record)
    assert r"host\_actor \& 100\% shutdown\_path" in line
    assert "host_actor & 100% shutdown_path" not in line


def test_format_resume_item_escapes_repo_name() -> None:
    record = make_record(repo_full_name="some_org/weird_repo")
    line = format_resume_item("Did something", record)
    assert r"\textbf{weird\_repo}" in line


def test_format_resume_item_does_not_escape_url() -> None:
    # \href's URL argument must stay a literal, unescaped URL.
    record = make_record(url="https://github.com/wasmCloud/wasmCloud/pull/1204")
    line = format_resume_item("Did something", record)
    assert r"\href{https://github.com/wasmCloud/wasmCloud/pull/1204}" in line


def test_format_resume_item_pr_number_uses_escaped_hash() -> None:
    record = make_record(number=42)
    line = format_resume_item("Did something", record)
    assert r"PR \#42" in line
    assert "PR #42" not in line


# --- inject -------------------------------------------------------------------


def test_inject_replaces_only_between_markers() -> None:
    lines = [format_resume_item("Fixed a bug", make_record(number=1))]
    result = inject(FIXTURE, lines)

    assert "Some experience content that must never be touched." in result
    assert "Some education content that must never be touched." in result
    assert r"\resumeItem{Fixed a bug" in result
    assert "% PR-SECTION-START" in result
    assert "% PR-SECTION-END" in result


def test_inject_preserves_lines_outside_markers_verbatim() -> None:
    lines = [format_resume_item("Fixed a bug", make_record(number=1))]
    result = inject(FIXTURE, lines)

    original_lines = FIXTURE.splitlines()
    result_lines = result.splitlines()

    start_idx = original_lines.index("% PR-SECTION-START")
    end_idx = original_lines.index("% PR-SECTION-END")

    assert result_lines[:start_idx] == original_lines[:start_idx]
    assert result_lines[-(len(original_lines) - end_idx - 1) :] == original_lines[end_idx + 1 :]


def test_inject_multiple_lines() -> None:
    lines = [
        format_resume_item("First bullet", make_record(number=1)),
        format_resume_item("Second bullet", make_record(number=2)),
    ]
    result = inject(FIXTURE, lines)

    assert r"\resumeItem{First bullet" in result
    assert r"\resumeItem{Second bullet" in result


def test_inject_empty_lines_leaves_only_markers() -> None:
    result = inject(FIXTURE, [])
    start = result.index("% PR-SECTION-START")
    end = result.index("% PR-SECTION-END")
    assert result[start + len("% PR-SECTION-START") : end].strip() == ""


def test_inject_is_idempotent() -> None:
    lines = [
        format_resume_item("Fixed a bug", make_record(number=1)),
        format_resume_item("Added a feature", make_record(number=2)),
    ]
    first_pass = inject(FIXTURE, lines)
    second_pass = inject(first_pass, lines)
    assert first_pass == second_pass


def test_inject_rerun_with_different_lines_fully_replaces_old_content() -> None:
    first_pass = inject(FIXTURE, [format_resume_item("Old bullet", make_record(number=1))])
    second_pass = inject(first_pass, [format_resume_item("New bullet", make_record(number=2))])

    assert "Old bullet" not in second_pass
    assert r"\resumeItem{New bullet" in second_pass


def test_inject_missing_start_marker_raises() -> None:
    broken = FIXTURE.replace("% PR-SECTION-START\n", "")
    with pytest.raises(InjectionError):
        inject(broken, [format_resume_item("Fixed a bug", make_record(number=1))])


def test_inject_missing_end_marker_raises() -> None:
    broken = FIXTURE.replace("% PR-SECTION-END\n", "")
    with pytest.raises(InjectionError):
        inject(broken, [format_resume_item("Fixed a bug", make_record(number=1))])


def test_inject_missing_both_markers_raises() -> None:
    broken = FIXTURE.replace("% PR-SECTION-START\n", "").replace("% PR-SECTION-END\n", "")
    with pytest.raises(InjectionError):
        inject(broken, [format_resume_item("Fixed a bug", make_record(number=1))])


def test_inject_end_before_start_raises() -> None:
    swapped = FIXTURE.replace(
        "% PR-SECTION-START\n% PR-SECTION-END\n",
        "% PR-SECTION-END\n% PR-SECTION-START\n",
    )
    with pytest.raises(InjectionError):
        inject(swapped, [format_resume_item("Fixed a bug", make_record(number=1))])


# --- extract_marked_urls -------------------------------------------------------


def test_extract_marked_urls_finds_urls_between_markers() -> None:
    lines = [
        format_resume_item("First", make_record(number=1, url="https://github.com/org/repo/pull/1")),
        format_resume_item("Second", make_record(number=2, url="https://github.com/org/repo/pull/2")),
    ]
    result = inject(FIXTURE, lines)

    assert extract_marked_urls(result) == {
        "https://github.com/org/repo/pull/1",
        "https://github.com/org/repo/pull/2",
    }


def test_extract_marked_urls_ignores_urls_outside_markers() -> None:
    text = FIXTURE.replace(
        "Some experience content that must never be touched.",
        "See https://github.com/other/repo/pull/99 for details.",
    )
    assert extract_marked_urls(text) == set()


def test_extract_marked_urls_empty_when_no_markers() -> None:
    assert extract_marked_urls("no markers here") == set()
