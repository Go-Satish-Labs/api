"""
Supabase Storage integration.

Files are stored under a per-workspace folder in the Supabase bucket.
"""
import os
import uuid

from ..config import settings

_supabase = None


def _get_client():
    """Lazily create the Supabase client so the server doesn't crash at
    startup if the package is momentarily unavailable.

    Storage writes use the service_role key so they bypass Storage RLS;
    the anon key would be blocked by the default "new row violates
    row-level security policy" policy on every upload.
    """
    global _supabase
    if _supabase is None:
        from supabase import create_client  # noqa: PLC0415
        key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY
        _supabase = create_client(settings.SUPABASE_URL, key)
    return _supabase


def save_upload(workspace_id: str, original_filename: str, content: bytes) -> tuple[str, int]:
    """Saves bytes to Supabase Storage. Returns (stored_path, size_bytes)."""
    ext = os.path.splitext(original_filename)[1].lower()
    safe_name = f"{workspace_id}/{uuid.uuid4().hex}{ext}"
    _get_client().storage.from_(settings.STORAGE_DIR).upload(safe_name, content)
    return safe_name, len(content)


def get_file_url(stored_path: str) -> str:
    """Returns a signed URL (60 s) so pandas can download the file from Supabase."""
    try:
        res = _get_client().storage.from_(settings.STORAGE_DIR).create_signed_url(stored_path, 60)
        return res["signedURL"] if isinstance(res, dict) else res.get("signedURL")
    except Exception:
        return _get_client().storage.from_(settings.STORAGE_DIR).get_public_url(stored_path)


def delete_file(stored_path: str) -> None:
    try:
        _get_client().storage.from_(settings.STORAGE_DIR).remove([stored_path])
    except Exception:
        pass


def save_profile_pic(user_id: str, original_filename: str, content: bytes) -> str:
    """Save a profile picture for a user. Returns the public URL.

    Stored under a dedicated profile-pics bucket so it never collides with
    dataset files, and the path is scoped to the user.
    """
    import imghdr

    ext = os.path.splitext(original_filename)[1].lower() or ".jpg"
    safe_name = f"{user_id}/{uuid.uuid4().hex}{ext}"
    bucket = settings.PROFILE_PIC_BUCKET
    _get_client().storage.from_(bucket).upload(safe_name, content)
    try:
        return _get_client().storage.from_(bucket).get_public_url(safe_name)
    except Exception:
        return safe_name


def delete_profile_pic(stored_path: str) -> None:
    bucket = settings.PROFILE_PIC_BUCKET
    try:
        _get_client().storage.from_(bucket).remove([stored_path])
    except Exception:
        pass


def workspace_storage_used_bytes(workspace_id: str) -> int:
    try:
        files = _get_client().storage.from_(settings.STORAGE_DIR).list(workspace_id)
        return sum(
            f.get("metadata", {}).get("size", 0)
            for f in files
            if f.get("name") != ".emptyFolderPlaceholder"
        )
    except Exception:
        return 0

