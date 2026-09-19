import base64
import os
import tempfile
from datetime import date

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required

from app.ai_tools import ai_tools_bp
from app.models.task import TaskPriority
from app.services import assistant_service, attachment_service, task_service, text_extraction_service
from app.services.ai import factory as ai_factory
from app.utils.rbac import roles_required


def _cleanup_voice_audio_temp():
    """Removes the temp file (if any) left behind by a previous voice-
    extract transcription — called whenever the page loads fresh (see
    index() below), since that's exactly when the transcript itself also
    resets (it only ever survives via the hidden form field the transcript-
    step forms round-trip, never server-side state).
    """
    tmp_path = session.pop("voice_audio_tmp_path", None)
    session.pop("voice_audio_mime", None)
    if tmp_path and os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _render(*, active_tab="voice-chat", pasted_text="", summary=None, suggestions=None,
            voice_transcript="", voice_filename=None, voice_summary=None, voice_suggestions=None):
    """Every AI Tools sub-flow (voice chat / text extractor / voice
    extractor) re-renders this one page with its own tab active — mirrors
    how the Meeting workspace's Transcript & AI tab stays open across its
    own multi-step flow (see build_workspace_context there).
    """
    voice_audio_tmp_path = session.get("voice_audio_tmp_path")
    voice_audio_available = bool(voice_audio_tmp_path and os.path.exists(voice_audio_tmp_path))
    return render_template(
        "ai_tools/index.html",
        active_tab=active_tab,
        pasted_text=pasted_text, summary=summary, suggestions=suggestions,
        voice_transcript=voice_transcript, voice_filename=voice_filename,
        voice_summary=voice_summary, voice_suggestions=voice_suggestions,
        voice_audio_available=voice_audio_available,
        ai_configured=ai_factory.is_configured(),
    )


@ai_tools_bp.route("/")
@login_required
@roles_required("EA")
def index():
    _cleanup_voice_audio_temp()
    return _render(active_tab=request.args.get("tab", "voice-chat"))


@ai_tools_bp.route("/summarize", methods=["POST"])
@login_required
@roles_required("EA")
def summarize():
    pasted_text = request.form.get("pasted_text", "")
    if not ai_factory.is_configured():
        flash("AI features are not configured yet. An administrator needs to set OPENAI_API_KEY in .env.", "warning")
        return _render(active_tab="text", pasted_text=pasted_text)

    if not pasted_text.strip():
        flash("Paste some text first.", "danger")
        return _render(active_tab="text", pasted_text=pasted_text)

    try:
        summary = text_extraction_service.summarize_text(pasted_text, requested_by=current_user.username)
    except text_extraction_service.TextExtractionError as exc:
        flash(f"Summarization failed: {exc}", "danger")
        summary = None

    return _render(active_tab="text", pasted_text=pasted_text, summary=summary)


@ai_tools_bp.route("/extract-actions", methods=["POST"])
@login_required
@roles_required("EA")
def extract_actions():
    pasted_text = request.form.get("pasted_text", "")
    if not ai_factory.is_configured():
        flash("AI features are not configured yet. An administrator needs to set OPENAI_API_KEY in .env.", "warning")
        return _render(active_tab="text", pasted_text=pasted_text)

    if not pasted_text.strip():
        flash("Paste some text first.", "danger")
        return _render(active_tab="text", pasted_text=pasted_text)

    try:
        suggestions = text_extraction_service.extract_actions_from_text(pasted_text, requested_by=current_user.username)
    except text_extraction_service.TextExtractionError as exc:
        flash(f"Action extraction failed: {exc}", "danger")
        suggestions = None

    if suggestions is not None and not suggestions:
        flash("The AI did not find any clear action items in this text.", "info")

    return _render(active_tab="text", pasted_text=pasted_text, suggestions=suggestions)


@ai_tools_bp.route("/voice/transcribe", methods=["POST"])
@login_required
@roles_required("EA")
def transcribe_voice():
    """Voice Extractor, step 1: the same Whisper transcription the Meeting
    workspace's "Transcript & AI" tab uses, just with no Meeting/Attachment
    to save the audio or the result against — everything here is ad hoc
    and lives only in this one page render (see text_extraction_service).
    """
    if not ai_factory.is_configured():
        flash("AI features are not configured yet. An administrator needs to set OPENAI_API_KEY in .env.", "warning")
        return _render(active_tab="voice-extract")

    _cleanup_voice_audio_temp()

    audio_file = request.files.get("audio_file")
    tmp_path = None
    try:
        tmp_path = attachment_service.save_audio_to_temp(audio_file)
        transcript = text_extraction_service.transcribe_audio(tmp_path, requested_by=current_user.username)
    except attachment_service.AttachmentValidationError as exc:
        flash(str(exc), "danger")
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return _render(active_tab="voice-extract")
    except text_extraction_service.TextExtractionError as exc:
        flash(f"Transcription failed: {exc}", "danger")
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return _render(active_tab="voice-extract")

    if not transcript.strip():
        flash("The AI couldn't make out any speech in that recording.", "warning")
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return _render(active_tab="voice-extract")

    # Kept (not deleted) on success so the Transcript step can offer a
    # play-back player alongside the text — see voice_audio() below and
    # _cleanup_voice_audio_temp() for when it eventually gets removed.
    session["voice_audio_tmp_path"] = tmp_path
    session["voice_audio_mime"] = audio_file.mimetype or "audio/mpeg"

    return _render(active_tab="voice-extract", voice_transcript=transcript, voice_filename=audio_file.filename)


@ai_tools_bp.route("/voice/audio")
@login_required
@roles_required("EA")
def voice_audio():
    """Streams back the audio just transcribed in the Voice Extractor tab
    (see transcribe_voice() above) so it can be played alongside the
    transcript to check the AI got it right — nothing here is persisted
    beyond this ad hoc session-tracked temp file.
    """
    tmp_path = session.get("voice_audio_tmp_path")
    if not tmp_path or not os.path.exists(tmp_path):
        abort(404)
    return send_file(tmp_path, mimetype=session.get("voice_audio_mime") or "audio/mpeg")


@ai_tools_bp.route("/voice/audio/clear", methods=["POST"])
@login_required
@roles_required("EA")
def clear_voice_audio():
    """Removes just the audio behind the play-back player, keeping whatever
    is currently in the transcript textarea (submitted via the shared
    voiceTranscriptForm — see the template) so clearing the recording
    doesn't also throw away edits made to the transcript.
    """
    _cleanup_voice_audio_temp()
    transcript = request.form.get("voice_transcript_text", "")
    flash("Audio removed.", "success")
    return _render(active_tab="voice-extract", voice_transcript=transcript)


@ai_tools_bp.route("/voice/summarize", methods=["POST"])
@login_required
@roles_required("EA")
def summarize_voice():
    transcript = request.form.get("voice_transcript_text", "")
    if not ai_factory.is_configured():
        flash("AI features are not configured yet. An administrator needs to set OPENAI_API_KEY in .env.", "warning")
        return _render(active_tab="voice-extract", voice_transcript=transcript)

    if not transcript.strip():
        flash("Generate a transcript first.", "danger")
        return _render(active_tab="voice-extract", voice_transcript=transcript)

    try:
        voice_summary = text_extraction_service.summarize_text(transcript, requested_by=current_user.username)
    except text_extraction_service.TextExtractionError as exc:
        flash(f"Summarization failed: {exc}", "danger")
        voice_summary = None

    return _render(active_tab="voice-extract", voice_transcript=transcript, voice_summary=voice_summary)


@ai_tools_bp.route("/voice/extract-actions", methods=["POST"])
@login_required
@roles_required("EA")
def extract_actions_voice():
    transcript = request.form.get("voice_transcript_text", "")
    if not ai_factory.is_configured():
        flash("AI features are not configured yet. An administrator needs to set OPENAI_API_KEY in .env.", "warning")
        return _render(active_tab="voice-extract", voice_transcript=transcript)

    if not transcript.strip():
        flash("Generate a transcript first.", "danger")
        return _render(active_tab="voice-extract", voice_transcript=transcript)

    try:
        voice_suggestions = text_extraction_service.extract_actions_from_text(transcript, requested_by=current_user.username)
    except text_extraction_service.TextExtractionError as exc:
        flash(f"Action extraction failed: {exc}", "danger")
        voice_suggestions = None

    if voice_suggestions is not None and not voice_suggestions:
        flash("The AI did not find any clear action items in this transcript.", "info")

    return _render(active_tab="voice-extract", voice_transcript=transcript, voice_suggestions=voice_suggestions)


@ai_tools_bp.route("/create-actions", methods=["POST"])
@login_required
@roles_required("EA")
def create_actions():
    count = request.form.get("suggestion_count", type=int) or 0
    created = 0

    for i in range(count):
        if request.form.get(f"action_{i}_selected") != "1":
            continue
        title = request.form.get(f"action_{i}_title", "").strip()
        employee_codes = [c for c in request.form.getlist(f"action_{i}_employee_codes") if c.strip()]
        if not title or not employee_codes:
            continue

        raw_priority = request.form.get(f"action_{i}_priority", "MEDIUM")
        try:
            priority = TaskPriority(raw_priority)
        except ValueError:
            priority = TaskPriority.MEDIUM

        due_date_raw = request.form.get(f"action_{i}_due_date", "")
        due_date = date.fromisoformat(due_date_raw) if due_date_raw else None

        try:
            task_service.create_general_followup(
                title=title,
                description=request.form.get(f"action_{i}_description", "").strip() or None,
                employee_codes=employee_codes,
                priority=priority,
                due_date=due_date,
                created_by=current_user.username,
            )
            created += 1
        except task_service.TaskValidationError:
            continue

    if created:
        flash(f"Created {created} follow-up task(s) from the reviewed suggestions.", "success")
    else:
        flash("No tasks were created — select at least one suggestion and assign an employee to it.", "warning")

    redirect_tab = request.form.get("redirect_tab", "text")
    return redirect(url_for("ai_tools.index", tab=redirect_tab))


@ai_tools_bp.route("/assistant/transcribe", methods=["POST"])
@login_required
@roles_required("EA")
def assistant_transcribe():
    """Voice assistant, step 1 of 3: transcribe the EA's recorded question
    only. Split from answering (see assistant_answer) so the UI can show
    the EA their own question the moment it's heard, with a "thinking"
    indicator for the slower LLM step, instead of one long silent wait
    before anything at all appears on screen.
    """
    if not ai_factory.is_configured():
        return jsonify({"error": "AI features are not configured yet. An administrator needs to set OPENAI_API_KEY in .env."}), 400

    audio_file = request.files.get("audio")
    if not audio_file or not audio_file.filename:
        return jsonify({"error": "No audio was recorded."}), 400

    tmp_path = None
    try:
        suffix = os.path.splitext(audio_file.filename)[1] or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            audio_file.save(tmp.name)
            tmp_path = tmp.name

        question_text = assistant_service.transcribe_question(tmp_path).strip()
        if not question_text:
            return jsonify({"error": "I couldn't make out a question in that recording — please try again."}), 400

        return jsonify({"question": question_text})
    except assistant_service.AssistantError as exc:
        return jsonify({"error": str(exc)}), 502
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@ai_tools_bp.route("/assistant/answer", methods=["POST"])
@login_required
@roles_required("EA")
def assistant_answer():
    """Voice assistant, step 2 of 3: answer an already-transcribed question,
    grounded in a live data snapshot. Voice synthesis is step 3 (see
    assistant_speak) — kept separate so the text answer can appear the
    moment it's ready instead of waiting for a full speech render too.
    """
    if not ai_factory.is_configured():
        return jsonify({"error": "AI features are not configured yet. An administrator needs to set OPENAI_API_KEY in .env."}), 400

    question = (request.get_json(silent=True) or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "No question to answer."}), 400
    if len(question) > 2000:
        question = question[:2000]

    try:
        employee_repository = current_app.extensions["employee_repository"]
        answer_text = assistant_service.ask(
            question, requested_by=current_user.username, employee_repository=employee_repository
        )
        return jsonify({"answer": answer_text})
    except assistant_service.AssistantError as exc:
        return jsonify({"error": str(exc)}), 502


@ai_tools_bp.route("/assistant/greeting")
@login_required
@roles_required("EA")
def assistant_greeting():
    """AURA's fixed self-introduction, spoken once per browser session (see
    the front-end prefetch-on-load). Cached server-side after the first
    call so this never re-pays TTS latency/cost.
    """
    if not ai_factory.is_configured():
        return jsonify({"error": "AI features are not configured yet."}), 400
    try:
        text, audio_bytes = assistant_service.get_greeting()
        return jsonify({"text": text, "audio_base64": base64.b64encode(audio_bytes).decode("ascii")})
    except assistant_service.AssistantError as exc:
        return jsonify({"error": str(exc)}), 502


@ai_tools_bp.route("/assistant/speak", methods=["POST"])
@login_required
@roles_required("EA")
def assistant_speak():
    """Synthesize speech for an already-generated assistant answer. Split
    out from assistant_ask so the text answer can appear immediately while
    this (slower, and not needed if the EA is happy just reading it) call
    runs separately.
    """
    if not ai_factory.is_configured():
        return jsonify({"error": "AI features are not configured yet. An administrator needs to set OPENAI_API_KEY in .env."}), 400

    text = (request.get_json(silent=True) or {}).get("text", "").strip()
    if not text:
        return jsonify({"error": "No text to speak."}), 400
    if len(text) > 2000:
        text = text[:2000]

    try:
        audio_bytes = assistant_service.synthesize_answer(text)
        return jsonify({"audio_base64": base64.b64encode(audio_bytes).decode("ascii")})
    except assistant_service.AssistantError as exc:
        return jsonify({"error": str(exc)}), 502
