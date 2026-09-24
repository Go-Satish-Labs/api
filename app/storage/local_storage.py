"""
Local disk storage, standing in for Supabase Storage / S3.

Kept behind a tiny interface (save/read/delete/path) so swapping in real
object storage later means changing this one file, not the routes that use it.
Files are stored under a per-workspace folder — never in a public path, and
never resolved from a client-supplied path (prevents path traversal).
"""
import os
import uuid

from ..config import settings


def _workspace_dir(workspace_id: str) -> str:
    path = os.path.join(settings.STORAGE_DIR, workspace_id)
    os.makedirs(path, exist_ok=True)
    return path


def save_upload(workspace_id: str, original_filename: str, content: bytes) -> tuple[str, int]:
    """Saves bytes under a server-generated filename (never the client's raw
    filename) to avoid path traversal / overwrite issues. Returns (stored_path, size)."""
    ext = os.path.splitext(original_filename)[1].lower()
    safe_name = f"{uuid.uuid4().hex}{ext}"
    full_path = os.path.join(_workspace_dir(workspace_id), safe_name)
    with open(full_path, "wb") as f:
        f.write(content)
    return full_path, len(content)


def delete_file(stored_path: str) -> None:
    if os.path.exists(stored_path):
        os.remove(stored_path)


def workspace_storage_used_bytes(workspace_id: str) -> int:
    path = _workspace_dir(workspace_id)
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            total += os.path.getsize(os.path.join(dirpath, name))
    return total
