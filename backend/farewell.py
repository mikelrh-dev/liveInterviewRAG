"""Farewell detection and the closing line.

Both turn pipelines can end an interview, but only the streaming one detects
the farewell and speaks it; the two endpoints still diverge here, which is
deliberately left as-is until they are unified. See the module docstring of
``backend.turns.streaming`` for what each path does.

DETECTING A FAREWELL IS NOT FINDING A SUBSTRING
-----------------------------------------------
This module used to ask one question -- "does any pattern appear anywhere in
the transcript?" -- and the answer is almost always yes, because a candidate
talks about wrapping up in the middle of an answer constantly. Six ordinary
sentences ended a live interview:

    "en cuanto a base de datos no tengo dudas uso postgres"
    "no tengo preguntas pero sí te quiero preguntar por el Reto de DAM"
    "vale ya está perfecto nos vemos en la siguiente ronda"
    "el equipo ya estamos bien organicé yo las reuniones"
    "no tengo más dudas sobre el proyecto de fraude pero tengo otra pregunta"
    "sobre el despliegue ya está todo claro"

The second contradicts itself in one sentence. "ya estamos bien" describes a
team, not the interview. And because ``detect_farewell`` is what makes the
pipeline speak ``FAREWELL_TEXT`` and emit ``interview_end``, each of those was
a session terminated by the candidate's own words, mid-answer.

So a closing phrase has to DOMINATE its sentence. Two checks on the text that
follows the match:

  1. A contradicting connector immediately after -- "pero", "aunque", "y", or an
     opening parenthesis. A negative in the forward direction is the minimum:
     the candidate just said they are continuing.
  2. Nothing substantive after it. "no tengo dudas" followed by "uso postgres"
     is a mention, not a sign-off; "nos vemos" followed by "en la siguiente
     ronda" defers the goodbye rather than saying it.

The two kinds of pattern are declared, not inferred:

  * ``_DOMINANT_PATTERNS`` -- the sign-off must be the last thing said. These
    are the phrases a candidate can drop mid-answer without meaning it.
  * ``_SPANNING_PATTERNS`` -- spanning clauses IS the point ("gracias ...
    nos vemos"). They are exempt from (2) and subject to (1) only, because
    their own tail is what makes them a farewell.
"""

import re
import unicodedata


def _fold(text: str) -> str:
    """Drop the diacritics, so a sign-off is recognised however it was spelled.

    The transcript is Whisper's output on this exact corpus, and its accents
    are not reliable -- ``tests/real_wiki.py`` says exactly that about these
    questions. Folding both sides of the comparison makes that a non-issue:
    "adiós" and "adios" are the same sign-off to this detector, and neither
    spelling can be the one that silently fails to end a live interview.
    """
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


#: Patterns that only count when nothing substantive follows them. Spelled
#: folded on purpose: see ``_fold`` and the guard in
#: ``tests/test_farewell.py::TestFarewellDetectionIgnoresDiacritics``.
_DOMINANT_PATTERNS = [
    r"\b(eso es todo|nada mas|no tengo mas preguntas)\b",
    r"\bno (tengo|hay) (mas |ninguna )?(preguntas|dudas|cosas)\b",
    r"\bya (esta|termine|acabe|estamos)\b",
    r"\b(terminamos|finalizamos|cerramos) (la entrevista|por hoy|aqui|aca)\b",
    r"\bfue un placer\b",
    r"\b(adios|chao|nos vemos|hasta luego)\b",
]

#: Patterns whose tail is part of the farewell itself, so only (1) applies.
_SPANNING_PATTERNS = [
    r"\bgracias\b.*\b(eso es todo|terminamos|finalizamos|nos vemos|adios|chao)\b",
    r"\b(gracias|muchas gracias).*(por tu tiempo|por la entrevista|ha sido un placer)\b",
]

_FAREWELL_PATTERNS = [
    *[(re.compile(_fold(pattern)), True) for pattern in _DOMINANT_PATTERNS],
    *[(re.compile(_fold(pattern)), False) for pattern in _SPANNING_PATTERNS],
]

#: A connector right after the phrase means the sentence turned around. Leading
#: punctuation is allowed because the phrase usually ends mid-clause:
#: "nos vemos, pero..." is the same contradiction as "nos vemos pero...".
_CONTRADICTED_RE = re.compile(r"^[\s,.;:!?—–-]*(?:pero|aunque|y)\b|^\s*\(")

#: Words a sign-off is allowed to trail off with. Token-exact, not a substring
#: test: "uso postgres" shares no token with this set, "y ya" is entirely made
#: of it. Kept small on purpose -- every word here is a hole through which a
#: false positive could return, and "gracias" plus "por hoy" are the two the
#: pattern list genuinely needs ("eso es todo, gracias", "terminamos la
#: entrevista por hoy").
#:
#: EVERY SPELLING HERE IS UNACCENTED, and that is load-bearing rather than
#: careless. ``_is_signoff_tail`` is reached only with text that has already
#: been through ``_fold`` -- ``detect_farewell`` folds the input once, at line
#: 148, and slices the remainder out of the folded string -- so a token
#: carrying a diacritic can never be produced by ``_TOKEN_RE`` on that
#: remainder. "más", "aquí" and "acá" sat in this set as reachable entries until
#: ``_fold`` was introduced; since then they have been half-dead: the unaccented
#: "mas", "aqui" and "aca" carry every real match, and the accented twins match
#: nothing at all. They are removed rather than documented-as-intentional
#: because there is no intentionality to document -- they are the residue of a
#: refactor, and a reader who found them would reasonably assume some caller
#: passes unfolded text.
#:
#: If a future caller ever needs to match raw text, the fix is to fold THAT
#: text before tokenizing, not to restore the accented entries: an entry here
#: that the folding makes unreachable is indistinguishable from a typo.
_SIGNOFF_TAIL_WORDS = frozenset(
    {
        "gracias",
        "muchas",
        "de",
        "verdad",
        "y",
        "ya",
        "nada",
        "mas",
        "por",
        "hoy",
        "aqui",
        "aca",
        "listo",
        "ok",
        "okay",
    }
)

_TOKEN_RE = re.compile(r"[\wáéíóúüñ]+", re.UNICODE)


def _is_signoff_tail(remainder: str) -> bool:
    """Whether what follows the match is only sign-off padding.

    Empty, or nothing but courtesy words: "eso es todo, gracias" and
    "terminamos la entrevista por hoy" are farewells, and the words after the
    match are part of the sign-off rather than a new thought.
    """
    return all(token in _SIGNOFF_TAIL_WORDS for token in _TOKEN_RE.findall(remainder))


#: Spoken when the recruiter signs off. Kept here so the copy and the detection
#: that triggers it sit together.
FAREWELL_TEXT = (
    "¡Gracias a ti! Ha sido un placer. Si tenés más preguntas en el futuro, "
    "acá estoy. ¡Éxito en tu búsqueda!"
)


def detect_farewell(text: str) -> bool:
    """Check if the user is indicating the interview should end.

    A pattern is a candidate, not a verdict: the text after the match decides.
    Every occurrence is tried, so a phrase that appears once mid-answer and
    again at the end still ends the interview.
    """
    lower = _fold(text.lower().strip())
    for pattern, must_dominate in _FAREWELL_PATTERNS:
        for match in pattern.finditer(lower):
            remainder = lower[match.end() :]

            # Checked first, because "nos vemos y ya" is a farewell and "y" is
            # also the connector that cancels one. Padding that belongs to the
            # sign-off cannot be the contradiction.
            if _is_signoff_tail(remainder):
                return True

            if _CONTRADICTED_RE.match(remainder):
                continue

            if not must_dominate:
                return True

            # A dominant phrase with the candidate still talking after it was a
            # mention, not a goodbye. Keep looking.
    return False
