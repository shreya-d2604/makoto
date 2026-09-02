"""Mirror resume.tex to Overleaf. Overleaf is a mirror, never a source of truth -
git is. This module's failures must always be treated as warnings by the caller,
not fatal errors: a broken Overleaf sync must never block a committed resume.tex."""

from __future__ import annotations

from pathlib import Path

BACKUP_DIR = Path(".backup")


class OverleafSyncError(RuntimeError):
    """Raised when the Overleaf mirror step fails. Callers must treat this as a warning."""


def sync_to_overleaf(resume_path: Path, project_id: str, target_filename: str) -> Path | None:
    """Back up the current Overleaf copy of `target_filename`, then overwrite it with
    the local resume_path's content. Returns the backup path written, or None if the
    file didn't exist yet in the Overleaf project. Raises OverleafSyncError on failure."""
    try:
        import pyoverleaf
    except ImportError as exc:
        raise OverleafSyncError("pyoverleaf is not installed. Install it with: pip install pyoverleaf") from exc

    api = pyoverleaf.Api()
    try:
        api.login_from_browser()
    except Exception as exc:
        raise OverleafSyncError(
            f"Could not authenticate with Overleaf from your browser's session cookie: {exc}. "
            "Make sure you're logged into Overleaf in your default browser."
        ) from exc

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
