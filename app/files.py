"""Upload handling: an allow-list of types, safe names, and thumbnails."""
from __future__ import annotations

import os
import re
import secrets

from PIL import Image, ImageOps

from . import db

UPLOAD_DIR = os.path.join(db.DATA_DIR, "uploads")
THUMB_DIR = os.path.join(db.DATA_DIR, "thumbs")

PHOTO_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "image/heif": ".heif",
}

DOC_TYPES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/zip": ".zip",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

ALLOWED = {**PHOTO_TYPES, **DOC_TYPES}
MAX_BYTES = 64 * 1024 * 1024
THUMB_MAX = 600

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class UploadError(Exception):
    pass


def ensure_dirs() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)


def clean_name(name: str) -> str:
    name = os.path.basename(name or "file")
    name = _SAFE_NAME.sub("-", name).strip("-._")
    return name[:120] or "file"


def stored_path(stored_name: str) -> str:
    """Resolve a stored name inside the uploads folder, refusing escapes."""
    safe = os.path.basename(stored_name)
    path = os.path.realpath(os.path.join(UPLOAD_DIR, safe))
    root = os.path.realpath(UPLOAD_DIR)
    if not path.startswith(root + os.sep):
        raise UploadError("Unsafe file path")
    return path


def thumb_path(stored_name: str) -> str:
    safe = os.path.basename(stored_name)
    base = os.path.splitext(safe)[0]
    return os.path.join(THUMB_DIR, base + ".jpg")


def is_photo(mime: str) -> bool:
    return mime in PHOTO_TYPES


def save_upload(
    data: bytes,
    original_name: str,
    mime: str,
    entry_id: int | None = None,
    caption: str = "",
    doc_type: str = "",
    kind: str | None = None,
) -> int:
    """Write an uploaded file to disk and record it. Returns the attachment id."""
    ensure_dirs()
    if not data:
        raise UploadError("The file was empty.")
    if len(data) > MAX_BYTES:
        raise UploadError("That file is larger than 64 MB.")
    mime = (mime or "").split(";")[0].strip().lower()
    original = clean_name(original_name)
    ext = os.path.splitext(original)[1].lower()
    if mime not in ALLOWED:
        # Fall back to the extension when the browser sends a vague type.
        by_ext = {v: k for k, v in ALLOWED.items()}
        if ext in by_ext:
            mime = by_ext[ext]
        else:
            raise UploadError(
                "That file type is not allowed. Photos (JPEG, PNG, WebP, HEIC) and "
                "documents (PDF, text, CSV, Office, ZIP) are accepted."
            )
    ext = ALLOWED[mime]
    stored_name = f"{db.utcnow():%Y%m%d}-{secrets.token_hex(8)}{ext}"
    path = os.path.join(UPLOAD_DIR, stored_name)
    with open(path, "wb") as fh:
        fh.write(data)

    width = height = 0
    if kind is None:
        kind = "photo" if is_photo(mime) else "document"
    if kind == "photo":
        try:
            width, height = make_thumb(path, stored_name)
        except Exception:
            kind = "photo"  # keep the file even if the thumbnail fails

    return db.execute(
        "INSERT INTO attachments (entry_id, kind, stored_name, original_name, mime,"
        " size_bytes, caption, doc_type, width, height, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry_id,
            kind,
            stored_name,
            original,
            mime,
            len(data),
            caption,
            doc_type,
            width,
            height,
            db.now(),
        ),
    )


def make_thumb(path: str, stored_name: str) -> tuple[int, int]:
    ensure_dirs()
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        width, height = img.size
        img = img.convert("RGB")
        img.thumbnail((THUMB_MAX, THUMB_MAX))
        img.save(thumb_path(stored_name), "JPEG", quality=82, optimize=True)
    return width, height


def delete_attachment(attachment_id: int) -> None:
    row = db.one("SELECT stored_name FROM attachments WHERE id = ?", (attachment_id,))
    if row is None:
        return
    for path in (stored_path(row["stored_name"]), thumb_path(row["stored_name"])):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass
    db.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))


def human_size(num: int | float) -> str:
    size = float(num or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def disk_usage() -> dict:
    ensure_dirs()
    total = 0
    count = 0
    for folder in (UPLOAD_DIR, THUMB_DIR):
        for name in os.listdir(folder):
            try:
                total += os.path.getsize(os.path.join(folder, name))
                if folder == UPLOAD_DIR:
                    count += 1
            except OSError:
                pass
    db_bytes = 0
    try:
        db_bytes = os.path.getsize(db.DB_PATH)
    except OSError:
        pass
    return {
        "files": count,
        "bytes": total + db_bytes,
        "human": human_size(total + db_bytes),
    }
