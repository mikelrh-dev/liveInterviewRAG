"""Cache precedence: which source is allowed to answer a question.

FAQ literal cache, then the semantic paraphrase cache, then the LLM. That order
is the whole content of this module.

It used to be written out inline in both turn pipelines, and the two copies had
already drifted once — the streaming path grew a farewell branch the blocking
path never got. Keeping the decision here means the *policy* has one home even
while the two *executions* stay separate, so a future divergence is a visible
difference in these modules rather than a silent one between two long functions.

This is not a pure function: both lookups touch I/O (the FAQ is a dict scan, the
semantic cache embeds the question and runs a SELECT). What is centralised is
the rule, not the work.
"""

import logging

from backend import container
from backend.services.response_cache import get_cached_response

logger = logging.getLogger(__name__)

#: A pre-written answer in the candidate's own voice, matched literally.
FAQ = "faq"
#: An answer to a paraphrase of a question already answered this month.
SEMANTIC = "semantic"
#: Nothing cached; the LLM has to generate.
LLM = "llm"


def resolve_answer_source(
    user_text: str,
    *,
    is_first_substantive: bool,
    path_label: str = "",
) -> tuple[str, str | None]:
    """Return ``(source, answer_text)``; the text is ``None`` only for ``LLM``.

    ``is_first_substantive`` gates the semantic cache (design D10): only the
    recruiter's opening question is ever looked up or stored, so a later
    question can never be answered with a stale stand-in for it.

    ``path_label`` is appended to the two cache-hit log lines so an operator can
    tell which endpoint served the request, which is the only thing the two
    pipelines did differently here.
    """
    cached = get_cached_response(user_text)
    if cached is not None:
        logger.info("Cache hit%s for: %s", path_label, user_text)
        return FAQ, cached

    if is_first_substantive:
        semantic_hit = container.semantic_cache().lookup(user_text)
        if semantic_hit is not None:
            logger.info("Semantic cache hit%s for: %s", path_label, user_text)
            return SEMANTIC, semantic_hit

    return LLM, None
