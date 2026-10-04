"""What the lexical rescue costs, recorded per population.

WHY THIS IS A MODULE AND NOT A COMMENT
--------------------------------------
``RAGPipeline._lexical_rescue`` is not a purely additive change and the
difference matters, so the cost is recorded where a guard can read it. The
alternative -- describing the trade in prose beside the code -- is how
"the rescue only adds" came to be believed: it is a sentence nobody can check,
and it was wrong. One question per population has its gold page at exactly
dense rank 3, which is the slot the rescue surrenders.

It is ONE question, and it is not the same question on each population. That
asymmetry is the reason the sets live here keyed by population instead of being
written once in a docstring: a corpus the author never has would silently carry
a loss nobody named.

Re-measuring these sets is expected when the corpus or the model changes, and
the guard that reads them says so at the point of failure. What is NOT expected
is editing them to hide a regression -- the net is +4 on both populations, so a
set that grows is never the cheaper option.

WHY NO THRESHOLDS INSTEAD
-------------------------
Both sets were tried as exclusions and neither works, which is a result rather
than an omission. See ``RAGPipeline.RESCUE_MAX_SURRENDERED_COSINE`` for the
sweep: on the committed corpus the surrendered slot of the lost question scores
0.3097 and the cheapest available gain scores 0.3264, so the loss sits below
every gain and no cosine threshold separates them. The only thresholds that lose
nothing are the ones that rescue nothing.
"""

from typing import Dict, FrozenSet, Tuple

#: ``population -> questions whose gold page the rescue costs``.
#:
#: ``full`` is the author's working tree (37 pages, 49 questions); ``reduced`` is
#: what ``git clone`` serves (33 pages, 41 questions). Both are measured at the
#: shipped ``top_k`` = 3, on the population each name describes.
RESCUE_COSTS: Dict[str, FrozenSet[str]] = {
    "full": frozenset({
        "para que sirven los tests hoy en dia con ia",
    }),
    "reduced": frozenset({
        "que estabas haciendo en mercadona los ultimos años",
    }),
}

#: The gold page each cost question loses, so a failure can name the page and
#: not only the question.
RESCUE_COST_PAGES: Dict[str, Dict[str, str]] = {
    "full": {
        "para que sirven los tests hoy en dia con ia": "opinions/importancia-tests.md",
    },
    "reduced": {
        "que estabas haciendo en mercadona los ultimos años":
            "experience/gerente-mercadona-2019-2025.md",
    },
}

#: The questions the rescue gains, per population. Published for the same reason
#: as the costs: a gain that nobody recorded is indistinguishable from a
#: coincidence the next person has to re-derive.
RESCUE_GAINS: Dict[str, FrozenSet[str]] = {
    "full": frozenset({
        "puedes empezar a trabajar ya estas disponible",
        "que opinas de la ia en el desarrollo de software",
        "que resultados dio el proyecto de la pagina web de velneo",
        "en que consistian tus practicas en ceesa",
        "que sabes de backend y java",
    }),
    # One fewer than ``full``, and the missing one is
    # ``puedes empezar a trabajar ya estas disponible``: its gold page is
    # ``faq/disponibilidad.md``, one of the four FAQ pages that exist on disk and
    # are not in the index, so a clone cannot score it. This is the clearest
    # single illustration of why these sets are per population.
    "reduced": frozenset({
        "que opinas de la ia en el desarrollo de software",
        "que resultados dio el proyecto de la pagina web de velneo",
        "en que consistian tus practicas en ceesa",
        "que sabes de backend y java",
    }),
}

#: The three questions the rescue was built for, with the gold page each one
#: names and the best cosine that gold page reaches against the 0.25 threshold.
#:
#: Two are recovered at top_k=3. The third is NOT, and the reason is worth more
#: than the number: its gold page's best cosine is 0.1703, below the threshold,
#: so it is FILTERED OUT of the candidate set entirely rather than ranked low --
#: and one lexical slot cannot reach a page the dense side never proposed. BM25
#: ALONE ranks it first (measured), so the terms are in the corpus; what does not
#: exist is a slot for them. Recovering it needs a different decision (a second
#: lexical slot, or a fusion), not a better threshold, which is why no threshold
#: is configured: there is nothing here for one to select.
TARGET_QUESTIONS: Tuple[Tuple[str, str, float], ...] = (
    (
        "en que consistian tus practicas en ceesa",
        "projects/pagina-web-practicas.md",
        0.3895,
    ),
    (
        "que sabes de backend y java",
        "skills/backend.md",
        0.2301,
    ),
    (
        "que hiciste con fastapi docker y asincronia en dam",
        "stories/autodidacta-fastapi-docker-async.md",
        0.1703,
    ),
)

#: The subset of ``TARGET_QUESTIONS`` the rescue actually recovers at top_k=3.
#:
#: Published so that "the rescue works" is a checkable claim rather than a
#: remembered one: a change that recovers fewer of these has regressed, and a
#: change that recovers the third has improved on what was measured.
RECOVERED_TARGETS: FrozenSet[str] = frozenset({
    "en que consistian tus practicas en ceesa",
    "que sabes de backend y java",
})