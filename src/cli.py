"""Entrypoint for resume-pr-sync."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from describe import get_bullet_text
from fetch import GitHubError, PRRecord, fetch_all_prs, load_cache, save_cache
from inject import InjectionError, extract_marked_urls, format_resume_item, inject
from rank import RankedPR, RankingError, rank_prs, select_top
from sync import OverleafSyncError, sync_to_overleaf

CONFIG_PATH = Path("config.yaml")
RESUME_PATH = Path("resume.tex")


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        raise RuntimeError(f"Config file not found: {path}")
    return yaml.safe_load(path.read_text())


def get_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable is not set.")
    return token


def print_pr_table(records: list[PRRecord]) -> None:
    if not records:
        print("No merged PRs found.")
        return

    headers = ["Repo", "#", "Title", "Stars", "Merged", "+/-", "Files", "Reviews"]
    rows = []
    for r in records:
        title = r.title if len(r.title) <= 50 else r.title[:47] + "..."
        rows.append(
            [
                r.repo_full_name,
                str(r.number),
                title,
                str(r.repo_stars),
                r.merged_at[:10],
                f"+{r.additions}/-{r.deletions}",
                str(r.changed_files),
                str(r.review_comments),
            ]
        )

    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("-" * len(line))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    print(f"\n{len(records)} merged PR(s) found.")


def cmd_fetch(args: argparse.Namespace) -> None:
    config = load_config()
    username = config["github"]["username"]
    token = get_github_token()

    try:
        records = fetch_all_prs(username, token)
    except GitHubError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    save_cache(records)
    print_pr_table(records)


def _require_pr_cache() -> list[PRRecord]:
    records = load_cache()
    if records is None:
        print("Error: no cached PRs found. Run 'fetch' first.", file=sys.stderr)
        sys.exit(1)
    return records


def _short_title(record: PRRecord, width: int = 70) -> str:
    return record.title if len(record.title) <= width else record.title[: width - 3] + "..."


def _rank_and_select(
    records: list[PRRecord], config: dict, *, rejudge: bool
) -> tuple[list[RankedPR], bool, list[RankedPR]]:
    """Rank + select the top PRs, exiting with a clear error on failure."""
    try:
        rankings, used_llm = rank_prs(records, config, rejudge=rejudge)
    except RankingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    pinned = config.get("ranking", {}).get("pinned", [])
    n = min(4, len(rankings))
    selected = select_top(rankings, pinned, n)
    return rankings, used_llm, selected


def cmd_rank(args: argparse.Namespace) -> None:
    config = load_config()
    records = _require_pr_cache()
    excluded = set(config.get("ranking", {}).get("excluded", []))
    pinned = config.get("ranking", {}).get("pinned", [])

    rankings, used_llm, selected = _rank_and_select(records, config, rejudge=args.rejudge)

    by_url: dict[str, PRRecord] = {r.url: r for r in records}
    selected_urls = {r.url for r in selected}

    mode = "LLM judgment" if used_llm else "deterministic fallback"
    print(f"Ranking mode: {mode}\n")

    if args.all:
        rankings_by_url: dict[str, RankedPR] = {r.url: r for r in rankings}
        for record in sorted(records, key=lambda r: r.merged_at, reverse=True):
            if record.url in excluded:
                status = "EXCLUDED"
            elif record.url in selected_urls:
                status = "SELECTED"
            else:
                status = "-"
            ranked = rankings_by_url.get(record.url)
            rank_str = f"#{ranked.rank}" if ranked else "-"
            print(f"[{status:8}] {rank_str:4} {record.repo_full_name} #{record.number} {_short_title(record)}")
        return

    if args.explain:
        for r in sorted(rankings, key=lambda r: r.rank):
            record = by_url[r.url]
            marker = "PINNED " if r.url in pinned else ("SELECTED" if r.url in selected_urls else "        ")
            print(f"[{marker}] #{r.rank:<3} {record.repo_full_name} #{record.number} {_short_title(record)}")
            print(f"           reason: {r.reason}")
        return

    for r in selected:
        record = by_url[r.url]
        print(f"#{r.rank} {record.repo_full_name} #{record.number} {_short_title(record)}")
        print(f"   reason: {r.reason}")


def cmd_describe(args: argparse.Namespace) -> None:
    config = load_config()
    records = _require_pr_cache()

    rankings, used_llm, selected = _rank_and_select(records, config, rejudge=args.rejudge)

    by_url: dict[str, PRRecord] = {r.url: r for r in records}
    rankings_by_url: dict[str, RankedPR] = {r.url: r for r in rankings}

    mode = "LLM judgment" if used_llm else "deterministic fallback"
    desc_mode = config.get("descriptions", {}).get("mode", "llm")
    print(f"Ranking mode: {mode} | Description mode: {desc_mode}\n")

    for r in selected:
        record = by_url[r.url]
        text = get_bullet_text(record, rankings_by_url.get(r.url), config)
        print(f"{text} ({record.repo_full_name} #{record.number})")


def _find_latex_compiler() -> tuple[str, list[str]] | None:
    if shutil.which("tectonic"):
        return "tectonic", ["tectonic"]
    if shutil.which("pdflatex"):
        return "pdflatex", ["pdflatex", "-interaction=nonstopmode", "-halt-on-error"]
    return None


def _compile_resume(path: Path) -> tuple[bool, str]:
    """Compile resume.tex into a temp dir. Returns (success, error_output)."""
    compiler = _find_latex_compiler()
    if compiler is None:
        return False, "Neither tectonic nor pdflatex is installed; cannot verify the resume compiles."

    name, base_cmd = compiler
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir_flag = "--outdir" if name == "tectonic" else "-output-directory"
        cmd = [*base_cmd, outdir_flag, tmpdir, str(path)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        except subprocess.TimeoutExpired:
            return False, f"{name} timed out after 180s."

        if result.returncode != 0:
            return False, (result.stdout + result.stderr)[-4000:]
        return True, ""


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def _is_git_repo() -> bool:
    return _run_git(["rev-parse", "--is-inside-work-tree"]).returncode == 0


def _commit_resume(added: set[str], removed: set[str]) -> None:
    """Commit resume.tex, which the caller must have already `git add`-ed."""
    message_lines = ["Update resume PR bullets"]
    if added:
        message_lines.append("")
        message_lines.append("Added:")
        message_lines.extend(f"  + {url}" for url in sorted(added))
    if removed:
        message_lines.append("")
        message_lines.append("Removed:")
        message_lines.extend(f"  - {url}" for url in sorted(removed))

    commit_result = _run_git(["commit", "-m", "\n".join(message_lines)])
    if commit_result.returncode != 0:
        raise RuntimeError(f"git commit failed: {commit_result.stderr.strip()}")


def cmd_inject(args: argparse.Namespace) -> None:
    config = load_config()
    records = _require_pr_cache()

    _rankings, _used_llm, selected = _rank_and_select(records, config, rejudge=args.rejudge)

    by_url: dict[str, PRRecord] = {r.url: r for r in records}
    rankings_by_url: dict[str, RankedPR] = {r.url: r for r in _rankings}
    lines = []
    for r in selected:
        record = by_url[r.url]
        text = get_bullet_text(record, rankings_by_url.get(r.url), config)
        lines.append(format_resume_item(text, record))

    if not RESUME_PATH.exists():
        print(f"Error: {RESUME_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    original_text = RESUME_PATH.read_text()
    try:
        new_text = inject(original_text, lines)
    except InjectionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(new_text)
        return

    if not _is_git_repo():
        print(
            f"Error: {Path.cwd()} is not a git repository. Run 'git init' before injecting for real.",
            file=sys.stderr,
        )
        sys.exit(1)

    old_urls = extract_marked_urls(original_text)
    new_urls = {r.url for r in selected}

    RESUME_PATH.write_text(new_text)

    success, output = _compile_resume(RESUME_PATH)
    if not success:
        RESUME_PATH.write_text(original_text)
        print("Error: resume.tex failed to compile. Restored the previous version.", file=sys.stderr)
        print(output, file=sys.stderr)
        sys.exit(1)

    print(f"Wrote {len(lines)} bullet(s) to {RESUME_PATH} -- compiles cleanly.")

    add_result = _run_git(["add", "resume.tex"])
    if add_result.returncode != 0:
        print(f"Error: git add failed: {add_result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    # Nothing staged relative to HEAD (e.g. resume.tex already matched the last commit).
    if _run_git(["diff", "--cached", "--quiet", "--", "resume.tex"]).returncode == 0:
        print("resume.tex already matches the last commit; nothing to commit.")
    else:
        try:
            _commit_resume(added=new_urls - old_urls, removed=old_urls - new_urls)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print("Committed resume.tex.")

    if args.sync_overleaf:
        _sync_overleaf(config)


def _sync_overleaf(config: dict) -> None:
    overleaf_cfg = config.get("overleaf", {})
    project_id = overleaf_cfg.get("project_id", "")
    target_filename = overleaf_cfg.get("target_filename", "resume.tex")

    if not project_id:
        print(
            "Warning: --sync-overleaf was passed but overleaf.project_id is not set in config.yaml. Skipping.",
            file=sys.stderr,
        )
        return

    try:
        backup_path = sync_to_overleaf(RESUME_PATH, project_id, target_filename)
    except OverleafSyncError as exc:
        # Overleaf is a mirror, not a source of truth - a failed sync is a warning, never fatal.
        print(f"Warning: Overleaf sync failed: {exc}", file=sys.stderr)
        return

    if backup_path:
        print(f"Backed up the previous Overleaf copy to {backup_path}")
    print(f"Synced {RESUME_PATH} to Overleaf project {project_id} ({target_filename}).")


def main() -> None:
    parser = argparse.ArgumentParser(prog="resume-pr-sync")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch merged PRs from GitHub and cache them")
    fetch_parser.set_defaults(func=cmd_fetch)

    rank_parser = subparsers.add_parser("rank", help="Rank cached PRs and select the top ones for the resume")
    rank_parser.add_argument("--explain", action="store_true", help="Print the full ranking with reasons")
    rank_parser.add_argument("--all", action="store_true", help="Show every merged PR found, chosen or not")
    rank_parser.add_argument("--rejudge", action="store_true", help="Force a fresh ranking, ignoring the cache")
    rank_parser.set_defaults(func=cmd_rank)

    describe_parser = subparsers.add_parser(
        "describe", help="Print the resume bullets for the selected top PRs"
    )
    describe_parser.add_argument("--rejudge", action="store_true", help="Force a fresh ranking, ignoring the cache")
    describe_parser.set_defaults(func=cmd_describe)

    inject_parser = subparsers.add_parser("inject", help="Inject the selected PR bullets into resume.tex")
    inject_parser.add_argument("--rejudge", action="store_true", help="Force a fresh ranking, ignoring the cache")
    inject_parser.add_argument(
        "--dry-run", action="store_true", help="Print the LaTeX that would be written, change nothing on disk"
    )
    inject_parser.add_argument(
        "--sync-overleaf",
        action="store_true",
        help="After a successful commit, mirror resume.tex to Overleaf (off by default)",
    )
    inject_parser.set_defaults(func=cmd_inject)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
