"""File upload validation and storage (master spec sections 25, 42).

Never trusts the client-supplied filename for storage — a random
server-side name is generated and the original name is kept only as
display metadata, which also rules out path traversal outright.
"""
from __future__ import annotations

import os
import secrets
import tempfile

from flask import current_app
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models.attachment import Attachment

_ALLOWED_AUDIO_MIME_PREFIXES = ("audio/", "video/webm")  # some browsers report webm audio as video/webm


class AttachmentValidationError(Exception):
    pass


def _entity_upload_dir(entity_type: str, entity_id: str) -> str:
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], entity_type.lower(), str(entity_id))
    os.makedirs(path, exist_ok=True)
    return path


def _validate_audio_file(file: FileStorage) -> int:
    """Shared by save_audio_attachment (persisted) and save_audio_to_temp
    (ad-hoc, no entity to attach to) — same rules either way. Returns the
    file size in bytes.
    """
    if not file or not file.filename:
        raise AttachmentValidationError("No file was selected.")

    ext = os.path.splitext(file.filename)[1].lower()
    allowed = current_app.config["ALLOWED_AUDIO_EXTENSIONS"]
    if ext not in allowed:
        raise AttachmentValidationError(f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed))}.")

    mime_type = file.mimetype or ""
    if mime_type and not any(mime_type.startswith(p) for p in _ALLOWED_AUDIO_MIME_PREFIXES):
        raise AttachmentValidationError(f"File does not look like audio (detected type: {mime_type}).")

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    max_size = current_app.config["MAX_AUDIO_SIZE_MB"] * 1024 * 1024
    if size > max_size:
        raise AttachmentValidationError(f"File is too large (max {current_app.config['MAX_AUDIO_SIZE_MB']} MB).")
    if size == 0:
        raise AttachmentValidationError("The uploaded file is empty.")
    return size


def save_audio_attachment(file: FileStorage, *, entity_type: str, entity_id: str, uploaded_by: str) -> Attachment:
    size = _validate_audio_file(file)
    ext = os.path.splitext(file.filename)[1].lower()

    stored_filename = f"{secrets.token_hex(16)}{ext}"
    dest_dir = _entity_upload_dir(entity_type, entity_id)
    file.save(os.path.join(dest_dir, stored_filename))

    attachment = Attachment(
        entity_type=entity_type,
        entity_id=str(entity_id),
        original_filename=file.filename,
        stored_filename=stored_filename,
        mime_type=file.mimetype or None,
        file_size=size,
        uploaded_by=uploaded_by,
    )
    db.session.add(attachment)
    db.session.commit()
    return attachment


def save_audio_to_temp(file: FileStorage) -> str:
    """Validates an uploaded audio file and writes it to a temp path for a
    one-off transcription with no meeting/entity to attach it to (the
    standalone Voice Extractor in AI Tools). The caller deletes the temp
    file once done — nothing is persisted to the database or upload folder.
    """
    _validate_audio_file(file)
    ext = os.path.splitext(file.filename)[1].lower()
    fd, tmp_path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    file.save(tmp_path)
    return tmp_path


def attachment_path(attachment: Attachment) -> str:
    return os.path.join(
        current_app.config["UPLOAD_FOLDER"], attachment.entity_type.lower(), attachment.entity_id, attachment.stored_filename
    )


def delete_attachment(attachment: Attachment) -> None:
    path = attachment_path(attachment)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    db.session.delete(attachment)
    db.session.commit()
