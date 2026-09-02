"""Mirror resume.tex to Overleaf. Overleaf is a mirror, never a source of truth -
git is. This module's failures must always be treated as warnings by the caller,
not fatal errors: a broken Overleaf sync must never block a committed resume.tex."""

from __future__ import annotations

from pathlib import Path

BACKUP_DIR = Path(".backup")
OVERLEAF_DOMAIN = "overleaf.com"

# Tried in this order; the first one that yields a usable Overleaf session cookie wins.
# pyoverleaf's own login_from_browser() calls browser_cookie3.load(), which tries every
# supported browser (including ones like Arc that aren't installed on Linux) and crashes
# on a bad path lookup before it ever reaches an actually-installed browser - so we drive
# the per-browser loaders ourselves and skip whichever ones fail.
_BROWSER_LOADER_NAMES = ["chrome", "firefox", "brave", "edge", "chromium", "vivaldi", "opera"]


class OverleafSyncError(RuntimeError):
    """Raised when the Overleaf mirror step fails. Callers must treat this as a warning."""


def _load_overleaf_cookies():
    import browser_cookie3

    errors = []
    for name in _BROWSER_LOADER_NAMES:
        loader = getattr(browser_cookie3, name)
        try:
            cookie_jar = loader(domain_name=OVERLEAF_DOMAIN)
        except Exception as exc:  # noqa: BLE001 - browser not installed/usable, try the next one
            errors.append(f"{name}: {exc}")
            continue
        if any(True for _ in cookie_jar):
            return cookie_jar
        errors.append(f"{name}: no Overleaf cookies found (not logged in there?)")

    raise OverleafSyncError(
        "Could not find an Overleaf session cookie in any installed browser. "
        f"Make sure you're logged into overleaf.com somewhere. Tried: {'; '.join(errors)}"
    )


def sync_to_overleaf(resume_path: Path, project_id: str, target_filename: str) -> Path | None:
    """Back up the current Overleaf copy of `target_filename`, then overwrite it with
    the local resume_path's content. Returns the backup path written, or None if the
    file didn't exist yet in the Overleaf project. Raises OverleafSyncError on failure."""
    try:
        import pyoverleaf
    except ImportError as exc:
        raise OverleafSyncError("pyoverleaf is not installed. Install it with: pip install pyoverleaf") from exc

    try:
        cookies = _load_overleaf_cookies()
    except OverleafSyncError:
        raise
    except Exception as exc:
        raise OverleafSyncError(f"Could not read browser cookies: {exc}") from exc

    api = pyoverleaf.Api()
    try:
        api.login_from_cookies(cookies)
    except Exception as exc:
        raise OverleafSyncError(f"Overleaf rejected the browser session cookie: {exc}") from exc

    project_io = pyoverleaf.ProjectIO(api, project_id)

    backup_path: Path | None = None
    try:
        file_exists = project_io.exists(target_filename)
    except Exception as exc:
        raise OverleafSyncError(f"Could not read the Overleaf project's file list: {exc}") from exc

    if file_exists:
        try:
            with project_io.open(target_filename, "rb") as f:
                current_content = f.read()
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup_path = BACKUP_DIR / target_filename
            backup_path.write_bytes(current_content)
        except Exception as exc:
            raise OverleafSyncError(f"Failed to back up the current Overleaf copy: {exc}") from exc

    try:
        content = resume_path.read_bytes()
        with project_io.open(target_filename, "wb") as f:
            f.write(content)
    except Exception as exc:
        raise OverleafSyncError(f"Failed to upload {resume_path} to Overleaf: {exc}") from exc

    return backup_path
