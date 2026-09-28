"""Cache precedence: which source is allowed to answer a question.

FAQ literal cache, then the LLM. That order is the whole content of this
module.

It used to be written out inline in both turn pipelines, and the two copies had
already drifted once — the streaming path grew a farewell branch the blocking
path never got. Keeping the decision here means the *policy* has one home even
while the two *executions* stay separate, so a future divergence is a visible
difference in these modules rather than a silent one between two long functions.

This is not a pure function: the lookup touches I/O (the FAQ is a dict scan).
What is centralised is the rule, not the work.
"""

import logging

from backend.services.response_cache import get_cached_response

logger = logging.getLogger(__name__)

#: A pre-written answer in the candidate's own voice, matched literally.
FAQ = "faq"
#: Nothing cached; the LLM has to generate.
LLM = "llm"


def resolve_answer_source(
    user_text: str,
    *,
    path_label: str = "",
) -> tuple[str, str | None]:
    """Return ``(source, answer_text)``; the text is ``None`` only for ``LLM``.

    ``path_label`` is appended to the cache-hit log line so an operator can
    tell which endpoint served the request, which is the only thing the two
    pipelines did differently here.

    There used to be a third rung: a semantic cache that answered a paraphrase
    of an already-answered first question. It is gone, and nothing replaced
    it. Measured with the real embedder, the threshold it shipped with could
    not be reached by any paraphrase, and the lowest threshold that still
    rejected every different question served about 7% of paraphrases — the
    distributions overlap, so a hit would have been more likely to be a
    confidently wrong answer than a useful one. See
    ``tests/test_rag.py::TestSemanticAnswerCacheWasNotViable``. The signature
    lost its ``is_first_substantive`` flag with it: that flag existed only to
    gate the semantic lookup, and had no other reader.
    """
    cached = get_cached_response(user_text)
    if cached is not None:
        logger.info("Cache hit%s for: %s", path_label, user_text)
        return FAQ, cached

    return LLM, None
