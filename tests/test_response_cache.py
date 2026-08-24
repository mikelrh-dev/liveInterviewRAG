"""Tests for the response cache service (backend/services/response_cache.py)."""

from backend.services.response_cache import get_cached_response, normalize_text

# ─── normalize_text ───────────────────────────────────────


def test_normalize_lowercases_and_strips_accents():
    """Normalization removes accents, case and question marks."""
    assert normalize_text("¿Cuáles son tus FORTALEZAS?") == "cuales son tus fortalezas"


def test_normalize_removes_punctuation_and_collapses_spaces():
    """Punctuation becomes spaces and whitespace is collapsed."""
    assert normalize_text("Háblame de ti... ¡por favor!") == "hablame de ti por favor"


def test_normalize_empty_input():
    """Empty input normalizes to an empty string."""
    assert normalize_text("") == ""
    assert normalize_text("   ") == ""


# ─── get_cached_response: hit variants ────────────────────


def test_pitch_question_cuentame():
    """'Cuéntame sobre ti' returns the 30-second pitch."""
    answer = get_cached_response("Cuéntame sobre ti")
    assert answer is not None
    assert "Mikel" in answer


def test_pitch_question_hablame_de_ti():
    """'Háblame de ti' returns the pitch too."""
    answer = get_cached_response("Háblame de ti")
    assert answer is not None
    assert "Mikel" in answer


def test_pitch_question_quien_eres():
    """'¿Quién eres?' returns the pitch too."""
    answer = get_cached_response("¿Quién eres?")
    assert answer is not None
    assert "Mikel" in answer


def test_interviewtts_project_question():
    """'¿Qué es InterviewTTS?' returns the project description."""
    answer = get_cached_response("¿Qué es InterviewTTS?")
    assert answer is not None
    assert "InterviewTTS" in answer


def test_mercadona_career_change_question():
    """'¿Por qué dejaste Mercadona?' returns the career-change answer."""
    answer = get_cached_response("¿Por qué dejaste Mercadona?")
    assert answer is not None
    assert "supermercado" in answer.lower()


def test_fortalezas_question():
    """'¿Cuáles son tus fortalezas?' returns strengths."""
    answer = get_cached_response("¿Cuáles son tus fortalezas?")
    assert answer is not None
    assert "disciplina" in answer.lower()


def test_fortalezas_y_debilidades_combined_question():
    """Combined strengths+weaknesses question answers both."""
    answer = get_cached_response("¿Cuáles son tus fortalezas y debilidades?")
    assert answer is not None
    assert "disciplina" in answer.lower()
    assert "desordenado" in answer.lower()


def test_debilidades_question():
    """'¿Cuáles son tus debilidades?' returns the honest weakness."""
    answer = get_cached_response("¿Cuáles son tus debilidades?")
    assert answer is not None
    assert "desordenado" in answer.lower()


def test_por_que_trabajar_aqui_question():
    """'¿Por qué quieres trabajar aquí?' returns the why-company answer."""
    answer = get_cached_response("¿Por qué quieres trabajar aquí?")
    assert answer is not None
    assert "aprender" in answer.lower()


def test_haces_tests_question():
    """'¿Haces tests?' returns the testing approach."""
    answer = get_cached_response("¿Haces tests?")
    assert answer is not None
    assert "test" in answer.lower()


def test_opinion_ia_question():
    """'¿Qué opinas de la IA?' returns the AI opinion."""
    answer = get_cached_response("¿Qué opinas de la IA?")
    assert answer is not None
    assert "ia" in answer.lower()


def test_como_aprendes_question():
    """'¿Cómo aprendes algo nuevo?' returns the learning methodology."""
    answer = get_cached_response("¿Cómo aprendes algo nuevo?")
    assert answer is not None
    assert "autodidacta" in answer.lower()


def test_python_question():
    """'¿Qué experiencia tienes con Python?' returns the Python answer."""
    answer = get_cached_response("¿Qué experiencia tienes con Python?")
    assert answer is not None
    assert "python" in answer.lower()


def test_docker_question():
    """'¿Qué experiencia tienes con Docker?' returns the Docker answer."""
    answer = get_cached_response("¿Qué experiencia tienes con Docker?")
    assert answer is not None
    assert "docker" in answer.lower()


def test_donde_te_ves_question():
    """'¿Dónde te ves en 5 años?' returns the future vision."""
    answer = get_cached_response("¿Dónde te ves en 5 años?")
    assert answer is not None
    assert "backend" in answer.lower() or "desarrollador" in answer.lower()


def test_area_preferida_question():
    """'¿Qué área del desarrollo te gusta más?' returns the preferred area."""
    answer = get_cached_response("¿Qué área del desarrollo te gusta más?")
    assert answer is not None
    assert "backend" in answer.lower() or "datos" in answer.lower()


def test_bases_de_datos_question():
    """'¿Qué sabes de bases de datos?' returns the database answer."""
    answer = get_cached_response("¿Qué sabes de bases de datos?")
    assert answer is not None
    assert "sql" in answer.lower() or "mysql" in answer.lower()


def test_trabajo_equipo_question():
    """'¿Has trabajado en equipo?' returns the teamwork answer."""
    answer = get_cached_response("¿Has trabajado en equipo?")
    assert answer is not None
    assert "equipo" in answer.lower()


def test_mayor_logro_question():
    """'¿Cuál es tu mayor logro?' returns the biggest achievement."""
    answer = get_cached_response("¿Cuál es tu mayor logro?")
    assert answer is not None
    assert "autodidacta" in answer.lower()


def test_apis_rest_question():
    """'¿Qué sabes de APIs REST?' returns the REST API answer."""
    answer = get_cached_response("¿Qué sabes de APIs REST?")
    assert answer is not None
    assert "api" in answer.lower()


def test_rag_question():
    """'¿Qué es RAG?' returns the RAG explanation."""
    answer = get_cached_response("¿Qué es RAG?")
    assert answer is not None
    assert "rag" in answer.lower()


def test_dam_question():
    """'¿Por qué elegiste DAM?' returns the DAM answer."""
    answer = get_cached_response("¿Por qué elegiste DAM?")
    assert answer is not None
    assert "tecnología" in answer.lower()


# ─── get_cached_response: miss behavior ───────────────────


def test_unknown_question_returns_none():
    """A non-cached question returns None so the caller falls back to the LLM."""
    assert get_cached_response("¿Qué stack usas en tus proyectos?") is None


def test_empty_question_returns_none():
    """Empty input returns None."""
    assert get_cached_response("") is None


def test_whitespace_only_question_returns_none():
    """Whitespace-only input returns None."""
    assert get_cached_response("   ") is None


def test_punctuation_only_question_returns_none():
    """Punctuation-only input returns None."""
    assert get_cached_response("¿?!...") is None


# ─── get_cached_response: word-boundary keyword matching ──


def test_keyword_api_inside_word_misses():
    """Keyword 'api' inside 'rápidamente' must NOT trigger the APIs answer."""
    assert get_cached_response("Necesitas responder rápidamente") is None


def test_keyword_rest_inside_word_misses():
    """Keyword 'rest' inside 'restaurante' must NOT trigger the APIs answer."""
    assert get_cached_response("¿Conoces un buen restaurante cerca de aquí?") is None


def test_keyword_presenta_inside_word_misses():
    """Keyword 'presenta' inside 'representa' must NOT trigger the pitch."""
    assert get_cached_response("Este proyecto representa mucho para mí") is None


def test_keyword_exact_word_hits():
    """Keyword 'api' as a standalone word DOES trigger the APIs answer."""
    answer = get_cached_response("¿Sabes trabajar con API?")
    assert answer is not None
    assert "APIs REST" in answer or "endpoints" in answer.lower()


def test_multiword_keyword_hits():
    """Multi-word keyword 'ia generativa' still triggers the AI answer."""
    answer = get_cached_response("¿Has usado IA generativa en tus proyectos?")
    assert answer is not None
    assert "IA" in answer
