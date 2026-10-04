"""What the lexical rescue costs, recorded per population.

WHY THIS IS A MODULE AND NOT A COMMENT
--------------------------------------
``RAGPipeline._lexical_rescue`` is not a purely additive change and the
difference matters, so the cost is recorded where a guard can read it. The
alternative -- describing the trade in prose beside the code -- is how
"the rescue only adds" came to be believed: it is a sentence nobody can check,
and it was wrong. One question on the working tree has its gold page at exactly
dense rank 3, which is the slot the rescue surrenders.

THE TWO SETS NO LONGER LOOK ALIKE, AND THAT IS THE POINT
---------------------------------------------------------
On 2026-10-04, with the four FAQ pages committed (``efda998``) and both
populations at 37 pages / 49 questions / 124 chunks, the rescue COSTS ONE
question on the author's working tree and COSTS NONE on ``git show HEAD:``. The
question it costs on the tree, ``para que sirven los tests hoy en dia con ia``,
is served from the committed text of that page either way, and
``que estabas haciendo en mercadona los ultimos años`` -- the cost the committed
corpus used to carry, back when it was a 41-question population -- is absent from
the top-3 on both sides now, so it is not a trade at all.

An empty cost set is a RESULT, not a missing record, and it is recorded as one:
``frozenset()`` next to the page map ``{}``. Before this re-measurement the two
rows differed in shape because one population had eight fewer questions to score;
now they differ in shape because the same 49 questions rank differently on
different text, which is the harder and more useful difference.

The gains are the SAME five questions on both populations, which is not
suspicious: the rescue is lexical over the same chunk texts, and the five
questions it recovers are decided by BM25 scores far from the 0.25 cosine
threshold. What is not the same is which question it pays for.

Re-measuring these sets is expected when the corpus or the model changes, and
the guard that reads them says so at the point of failure. What is NOT expected
is editing them to hide a regression -- the net is +4 on ``full`` and +5 on
``reduced``, so a shrinking gain set is never the cheaper option.

WHY NO THRESHOLDS INSTEAD
-------------------------
Both sets were tried as exclusions and neither works, which is a result rather
than an omission. See ``RAGPipeline.RESCUE_MAX_SURRENDERED_COSINE`` for the
sweep: on the committed corpus the surrendered slot of the lost question scored
0.3097 against a cheapest available gain of 0.3264, so the loss sat below every
gain and no cosine threshold separated them. That sweep is a HISTORICAL record
of the working tree's single lost slot, which ``reduced`` does not have; the
conclusion it supports -- that the only thresholds losing nothing are the ones
rescuing nothing -- still stands on ``full``.
"""

from typing import Dict, FrozenSet, Tuple

#: ``population -> questions whose gold page the rescue costs``.
#:
#: ``full`` is ``wiki/`` as it stands in the checkout running the suite;
#: ``reduced`` is ``git show HEAD:``, which is what a clone serves. Both are
#: measured at the shipped ``top_k`` = 3, on the population each name describes,
#: and both populations are 37 pages / 49 questions / 124 chunks since
#: ``efda998``.
RESCUE_COSTS: Dict[str, FrozenSet[str]] = {
    "full": frozenset({
        "para que sirven los tests hoy en dia con ia",
    }),
    # Measured, and it is EMPTY: no question loses its gold page at top_k=3 on
    # the committed corpus. Recorded as ``frozenset()`` on purpose -- an empty set
    # that a guard compares against a live measurement is a claim; a missing key
    # is a hole the guard would have to special-case.
    "reduced": frozenset(),
}

#: The gold page each cost question loses, so a failure can name the page and
#: not only the question. Empty for ``reduced`` because its cost set is: the
#: question the working tree loses, ``para que sirven los tests hoy en dia con
#: ia``, is served from ``opinions/importancia-tests.md`` with the rescue ON and
#: OFF once that page is read at its committed content.
RESCUE_COST_PAGES: Dict[str, Dict[str, str]] = {
    "full": {
        "para que sirven los tests hoy en dia con ia": "opinions/importancia-tests.md",
    },
    "reduced": {},
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
    # The SAME five as ``full``, and the reason is the shape of the mechanism: the
    # rescue ranks with BM25 over the whole chunk text, so whether a question is
    # recovered is decided by lexical scores that sit far from the 0.25 cosine
    # gate, not by where the dense side happens to put the page. It used to be
    # four here, missing ``puedes empezar a trabajar ya estas disponible``, whose
    # gold page ``faq/disponibilidad.md`` was one of the four a clone did not have;
    # that page is committed now.
    "reduced": frozenset({
        "puedes empezar a trabajar ya estas disponible",
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