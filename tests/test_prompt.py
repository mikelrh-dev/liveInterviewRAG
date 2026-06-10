"""Tests for candidate prompt builder."""

from backend.prompts.candidate import build_system_prompt, CANDIDATE_SYSTEM_PROMPT


def test_build_system_prompt_no_context():
    """System prompt without context is valid."""
    prompt = build_system_prompt()
    assert "Mikel" in prompt
    assert "Junior DAM Developer" in prompt
    assert "first-person" in prompt
    assert "Context from candidate's profile" not in prompt


def test_build_system_prompt_with_context():
    """System prompt includes retrieved context."""
    context = "I built InterviewTTS using Python and FastAPI."
    prompt = build_system_prompt(context)
    assert context in prompt
    assert "relevant information" in prompt.lower()


def test_system_prompt_template_has_placeholders():
    """Template has the expected placeholder."""
    assert "{context}" in CANDIDATE_SYSTEM_PROMPT
