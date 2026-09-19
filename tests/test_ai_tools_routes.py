import io

from app.models.app_user import AppUser, UserRole
from app.services.ai.base_transcription_service import BaseTranscriptionService
from tests.test_ai_services import FakeAIService


class FakeTranscriptionService(BaseTranscriptionService):
    provider_name = "fake"
    model_name = "fake-whisper"

    def __init__(self, text="This is the transcribed audio.", raise_error=None):
        self._text = text
        self._raise_error = raise_error

    def transcribe(self, audio_path, *, language=None, prompt=None):
        if self._raise_error:
            raise self._raise_error
        return self._text


def _make_ea(db, username="ea1"):
    user = AppUser(username=username, role=UserRole.EA)
    user.set_password("Password123")
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_index_defaults_to_voice_chat_tab(app, db, client):
    _login(client, _make_ea(db))
    resp = client.get("/ai-tools/")
    assert resp.status_code == 200
    assert b'id="tab-voice-chat"' in resp.data
    assert b"Voice Chat" in resp.data
    assert b"Text Extractor" in resp.data
    assert b"Voice Extractor" in resp.data


def test_index_tab_query_param_selects_tab(app, db, client):
    _login(client, _make_ea(db))
    resp = client.get("/ai-tools/?tab=voice-extract")
    assert resp.status_code == 200
    assert b'id="tab-voice-extract"' in resp.data


def test_summarize_reopens_text_tab(app, db, client, monkeypatch):
    _login(client, _make_ea(db))
    monkeypatch.setattr("app.services.text_extraction_service.factory.get_ai_service", lambda: FakeAIService())
    resp = client.post("/ai-tools/summarize", data={"pasted_text": "some text to summarize"})
    assert resp.status_code == 200
    assert b"Key Discussion Points" in resp.data


def test_voice_transcribe_requires_a_file(app, db, client):
    _login(client, _make_ea(db))
    resp = client.post("/ai-tools/voice/transcribe", data={}, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert b"No file was selected" in resp.data


def test_voice_transcribe_success_shows_transcript(app, db, client, monkeypatch):
    _login(client, _make_ea(db))
    monkeypatch.setattr(
        "app.services.text_extraction_service.factory.get_transcription_service",
        lambda: FakeTranscriptionService(text="We discussed the Q3 roadmap."),
    )
    data = {"audio_file": (io.BytesIO(b"fake audio bytes"), "memo.mp3")}
    resp = client.post("/ai-tools/voice/transcribe", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert b"We discussed the Q3 roadmap." in resp.data
    assert b'id="tab-voice-extract"' in resp.data


def test_voice_transcribe_rejects_bad_extension(app, db, client):
    _login(client, _make_ea(db))
    data = {"audio_file": (io.BytesIO(b"not audio"), "notes.txt")}
    resp = client.post("/ai-tools/voice/transcribe", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert b"Unsupported file type" in resp.data


def test_voice_summarize_requires_transcript_text(app, db, client):
    _login(client, _make_ea(db))
    resp = client.post("/ai-tools/voice/summarize", data={"voice_transcript_text": "  "})
    assert resp.status_code == 200
    assert b"Generate a transcript first" in resp.data


def test_voice_summarize_success(app, db, client, monkeypatch):
    _login(client, _make_ea(db))
    monkeypatch.setattr("app.services.text_extraction_service.factory.get_ai_service", lambda: FakeAIService())
    resp = client.post("/ai-tools/voice/summarize", data={"voice_transcript_text": "We discussed the roadmap."})
    assert resp.status_code == 200
    assert b"Key Discussion Points" in resp.data


def test_voice_transcribe_success_offers_playback(app, db, client, monkeypatch):
    _login(client, _make_ea(db))
    monkeypatch.setattr(
        "app.services.text_extraction_service.factory.get_transcription_service",
        lambda: FakeTranscriptionService(text="We discussed the Q3 roadmap."),
    )
    data = {"audio_file": (io.BytesIO(b"fake audio bytes"), "memo.mp3")}
    resp = client.post("/ai-tools/voice/transcribe", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert b"/ai-tools/voice/audio" in resp.data

    audio_resp = client.get("/ai-tools/voice/audio")
    assert audio_resp.status_code == 200
    assert audio_resp.data == b"fake audio bytes"


def test_voice_audio_404_when_nothing_transcribed_yet(app, db, client):
    _login(client, _make_ea(db))
    resp = client.get("/ai-tools/voice/audio")
    assert resp.status_code == 404


def test_voice_audio_cleared_on_fresh_page_load(app, db, client, monkeypatch):
    _login(client, _make_ea(db))
    monkeypatch.setattr(
        "app.services.text_extraction_service.factory.get_transcription_service",
        lambda: FakeTranscriptionService(text="We discussed the Q3 roadmap."),
    )
    data = {"audio_file": (io.BytesIO(b"fake audio bytes"), "memo.mp3")}
    client.post("/ai-tools/voice/transcribe", data=data, content_type="multipart/form-data")
    assert client.get("/ai-tools/voice/audio").status_code == 200

    # "Clear All" on the transcript step, and any other fresh visit to the
    # page, is a plain GET to index() — the same reset point the transcript
    # itself relies on (see ai_tools/routes.py's _cleanup_voice_audio_temp).
    resp = client.get("/ai-tools/?tab=voice-extract")
    assert resp.status_code == 200
    assert b"/ai-tools/voice/audio" not in resp.data
    assert client.get("/ai-tools/voice/audio").status_code == 404


def test_voice_audio_survives_summarize_step(app, db, client, monkeypatch):
    _login(client, _make_ea(db))
    monkeypatch.setattr(
        "app.services.text_extraction_service.factory.get_transcription_service",
        lambda: FakeTranscriptionService(text="We discussed the Q3 roadmap."),
    )
    data = {"audio_file": (io.BytesIO(b"fake audio bytes"), "memo.mp3")}
    client.post("/ai-tools/voice/transcribe", data=data, content_type="multipart/form-data")

    monkeypatch.setattr("app.services.text_extraction_service.factory.get_ai_service", lambda: FakeAIService())
    resp = client.post("/ai-tools/voice/summarize", data={"voice_transcript_text": "We discussed the Q3 roadmap."})
    assert resp.status_code == 200
    assert b"/ai-tools/voice/audio" in resp.data
    assert client.get("/ai-tools/voice/audio").status_code == 200


def test_clear_voice_audio_removes_player_but_keeps_transcript(app, db, client, monkeypatch):
    _login(client, _make_ea(db))
    monkeypatch.setattr(
        "app.services.text_extraction_service.factory.get_transcription_service",
        lambda: FakeTranscriptionService(text="We discussed the Q3 roadmap."),
    )
    data = {"audio_file": (io.BytesIO(b"fake audio bytes"), "memo.mp3")}
    client.post("/ai-tools/voice/transcribe", data=data, content_type="multipart/form-data")
    assert client.get("/ai-tools/voice/audio").status_code == 200

    resp = client.post("/ai-tools/voice/audio/clear", data={"voice_transcript_text": "We discussed the Q3 roadmap."})
    assert resp.status_code == 200
    assert b"We discussed the Q3 roadmap." in resp.data  # transcript kept
    assert b"/ai-tools/voice/audio" not in resp.data  # player gone
    assert client.get("/ai-tools/voice/audio").status_code == 404  # and actually cleared server-side


def test_create_actions_redirects_to_requested_tab(app, db, client):
    _login(client, _make_ea(db))
    resp = client.post("/ai-tools/create-actions", data={"suggestion_count": "0", "redirect_tab": "voice-extract"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/ai-tools/?tab=voice-extract")


def test_ai_tools_requires_ea_role(app, db, client):
    from app.models.app_user import UserRole as UR

    md = AppUser(username="md1", role=UR.MD)
    md.set_password("Password123")
    db.session.add(md)
    db.session.commit()
    _login(client, md)
    resp = client.get("/ai-tools/")
    assert resp.status_code == 403
