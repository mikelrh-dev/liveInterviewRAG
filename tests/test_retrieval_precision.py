"""precision@k del retrieval sobre el set etiquetado, y las preguntas que la hunden.

EL HUECO
--------
``tests/real_wiki.py`` mide recall@1, recall@3 y MRR@5, y
``tests/test_lexical_rescue.py`` mide lo mismo al ``top_k`` que la producción
embarca. Ninguno de los dos dice cuántas de las páginas que el modelo LEE SON la
respuesta, y esa es exactamente la pregunta que hay que responder antes de meter
un reranker: si el top-3 ya viene limpio, el reranker es trabajo sin premio; si
trae ruido junto al acierto, tiene margen.

La respuesta medida está en ``tests/retrieval_measurements.py``, y este archivo
es lo que la ata a una medición viva. Publicada por población:

    full    (37 páginas, 49 preguntas, 124 chunks)
            precision@1 0.7347   precision@3 0.3061   (45 de 147 slots)
            contra el techo que las etiquetas permiten: 45 de 50 = 0.90
    reduced (37 páginas, 49 preguntas, 124 chunks)
            precision@1 0.7551   precision@3 0.3129   (46 de 147 slots)
            contra el techo: 46 de 50 = 0.92

Las dos filas tienen las MISMAS cifras de páginas, preguntas y chunks desde el
commit ``efda998``, que commiteó las cuatro páginas FAQ que antes el clon no
tenía; lo que las sigue distinguiendo es el TEXTO (el ``digest`` de
``tests/real_wiki.py::CORPUS_DIGESTS``). La diferencia de precisión es de UN slot
relevante y de una pregunta hundida de más, y sale de quince páginas
commiteadas que no son las quince modificadas sin commitear en el working tree.

Y el reparto, que es la parte accionable:

    49 preguntas -> 36 con la respuesta en rank 1
                    8 con la respuesta en rank 2-3   (el margen de un reranker)
                    5 sin la respuesta en el top-3  (no lo arregla reordenar)

LA PREMISA QUE HACE LEGIBLE EL 0.3061
--------------------------------------
Una pregunta con una sola página aceptable no puede puntuar más de 1/3 en
``precision@3``, así que el techo de la población ``full`` son 50 slots
relevantes sobre 147 servidos. Leído sin ese denominador, 0.3061 parece una
tercera parte del contexto y no lo es: es nueve décimos de lo que un retrieval
perfecto habría ocupado con los mismos tres huecos. Las dos poblaciones están por
encima de 0.85 de su techo, y el hueco que queda NO es ruido en el top-3: son
cinco preguntas cuya página dorada no está en la lista.

Lo que eso significa para un reranker, dicho con el precaution que merece: un
reranker que reordene LOS MISMOS tres slots tiene 8 preguntas que ganar y 36 que
no puede perder. Cinco NO las puede recuperar, porque la página no está en la
candidatura; una rerankización sobre un conjunto más ancho sí podría, y eso es
otro diseño con otro coste, no el mismo. Este archivo no mide el conjunto ancho
a propósito: no es lo que se pidió y es otra cifra con su propia línea.

QUÉ SE FIJA Y QUÉ NO
--------------------
Fijado: los contadores medidos, el suelo de ``TOLERATED_SLOTS`` por debajo, el
nombre de las 8 + 5 preguntas de cada población, y el hecho de que el registro del
rescate (``tests/lexical_rescue_losses.py``) y el de precisión describan los
MISMOS movimientos.

No fijado: que la precisión sea buena. No lo es y no se finge. Lo que se fija es
que no empeore sin que alguien lo vuelva a medir y lo escriba aquí.

CADA POBLACIÓN EN SU CORPUS
---------------------------
``reduced`` se lee siempre de ``git show HEAD:``, así que este archivo mide esa
población en cualquier checkout. ``full`` sólo se mide si ESTE checkout es el
working tree que la midió, y desde el 2026-10-04 eso se decide por el digest del
corpus servido y no por el número de etiquetas: las dos poblaciones resuelven las
49, así que un contador devolvería la fila ``full`` para el corpus commiteado sin
avisar y estos tests pasarían por la holgura del techo, no porque la fila fuera
la correcta. En una clone limpia el parámetro ``full`` se salta, y el salto lo
dice el motivo, no un verde.
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Sequence

import pytest

from backend.services.rag import RAGPipeline
from tests.lexical_rescue_losses import RESCUE_COSTS, RESCUE_GAINS
from tests.real_wiki import (
    CORPUS_DIGESTS,
    Case,
    corpus_digest,
    corpus_for,
    load_documents,
    resolved_cases,
    wiki_is_present,
)
from tests.retrieval_measurements import (
    SHIPPED_TOP_K,
    DUPLICATED_QUESTION,
    PrecisionFigures,
    build_pipeline,
    documents_for,
    duplicated_cases,
    measure_precision,
    possible_relevant_slots,
    precision_for,
    served_pages,
)

_WIKI_PRESENT = wiki_is_present()

#: Cuántas etiquetas resuelve ESTE checkout, resuelto sin embedder: la carga del
#: corpus es una lectura de 50 ficheros y no cuesta lo que cuesta una medición.
_CHECKOUT_QUESTIONS = len(resolved_cases(load_documents())) if _WIKI_PRESENT else 0

#: Y qué corpus ES este checkout, que desde 2026-10-04 no es lo mismo que lo
#: anterior: las dos poblaciones resuelven las 49 etiquetas, así que la pregunta
#: que decide si la fila ``full`` es medible aquí es de qué TEXTO se trata.
_CHECKOUT_CORPUS = corpus_for(load_documents()) if _WIKI_PRESENT else None


#: Por qué esta población no se mide en este checkout. Va en el ``reason`` del
#: ``skipif`` y no en un comentario, porque pytest imprime el motivo en el
#: resumen: un skip cuyo motivo nombra la fila que falta es un skip que se lee, y
#: uno sin motivo es un verde que se cree.
_WIKI_ABSENT = (
    "wiki/ no está en este checkout, y son 50 ficheros TRACKED, así que esto sólo "
    "pasa donde se borró el corpus. Allí está fallando "
    "TestTheRetrievalGuardActuallyRan en tests/test_rag.py, que es la señal. No "
    "leas este skip como un pass."
)

_FULL_ABSENT = (
    "este checkout sirve el corpus {_corpus!r}, no el working tree que la fila "
    "'full' nombra (digest {_digest} en tests/real_wiki.py::CORPUS_DIGESTS). "
    "Resuelve {_questions} etiquetas y eso ya no distingue nada: desde efda998 "
    "las dos poblaciones resuelven las 49. La fila 'full' ES el working tree del "
    "autor con sus quince wiki/*.md sin commitear, así que medirla con el texto "
    "de HEAD sería medir una población con el nombre de otra. La fila 'reduced' "
    "de este mismo archivo sí se mide, y es la que corresponde a este checkout."
)


def _skip_reason(population: str) -> str:
    """El motivo del skip de ``population``, con lo que este checkout ES dentro."""
    if population == "reduced" or not _WIKI_PRESENT:
        return _WIKI_ABSENT
    return _FULL_ABSENT.format(
        _corpus=_CHECKOUT_CORPUS,
        _digest=CORPUS_DIGESTS["full"][:12],
        _questions=_CHECKOUT_QUESTIONS,
    )


#: Las dos filas, cada una contra su corpus. Los ``marks`` van en el parámetro y
#: no en la clase para que el motivo del skip nombre la fila que falta.
POPULATIONS = (
    pytest.param(
        "full",
        marks=pytest.mark.skipif(
            _CHECKOUT_CORPUS != "full", reason=_skip_reason("full")
        ),
    ),
    pytest.param(
        "reduced",
        marks=pytest.mark.skipif(
            not _WIKI_PRESENT, reason=_skip_reason("reduced")
        ),
    ),
)


@dataclass(frozen=True)
class Corpus:
    """Una población, su fila, y la medición hecha sobre SU corpus."""

    population: str
    row: PrecisionFigures
    documents: Dict[str, str]
    cases: Sequence[Case]
    pipeline: RAGPipeline
    measured: Dict[str, object]
    #: The same counters with the cross-encoder OFF. Needed because
    #: ``tests/lexical_rescue_losses.py`` records the lexical rescue's behaviour,
    #: and that record describes the ranking BEFORE any re-ranker reorders it.
    #: See ``TestTheRescueRecordAndThePrecisionRecordAgree``.
    measured_without_rerank: Dict[str, object]


@pytest.fixture(scope="module", params=POPULATIONS)
def corpus(request, tmp_path_factory) -> Corpus:
    """La fila de esta población, y todo lo medido para comprobarla.

    Module-scoped porque embeber el corpus cuesta 9-17 s con el modelo real, y
    ``precision@k`` son cuatro contadores sobre las mismas 49 preguntas: sin
    warms, un guard que mide cuatro veces para afirmar cuatro veces sobre la
    misma pregunta no es más riguroso, es más lento.

    La fila se resuelve con ``precision_for``, que RECHAZA un corpus no
    calibrado en lugar de devolver otro, y lo hace por su DIGEST. Un corpus
    distinto es una población nueva aunque resuelva las mismas 49 etiquetas, y
    puntuarla contra una fila que no es suya es el defecto que
    ``tests/real_wiki.py`` existe para impedir.
    """
    population = request.param
    root = tmp_path_factory.mktemp(f"precision_{population}")
    documents = documents_for(population, root)
    assert documents is not None, (
        f"documents_for({population!r}) devolvió None en un checkout que debería "
        f"tener esa población. El skip de este parámetro no está funcionando."
    )

    row = precision_for(documents)
    assert row is not None, (
        f"el corpus de la población {population!r} digiere a "
        f"{corpus_digest(documents)[:12]} y no hay fila de precisión para él "
        f"({sorted(CORPUS_DIGESTS)} en tests/real_wiki.py::CORPUS_DIGESTS). "
        "Vuelve a medir sobre el corpus que queda y añade su fila en "
        "tests/retrieval_measurements.py; no apuntes esta fila a otro."
    )

    cases = resolved_cases(documents)
    pipeline = build_pipeline(documents)
    # A second pipeline over the SAME documents, with the cross-encoder off. Not
    # a second population and not a second corpus: the chunks and the vectors are
    # identical, and the only difference is the re-ranking stage, which is what
    # makes the pair a controlled comparison rather than two measurements.
    without_rerank = build_pipeline(documents, rerank=False)
    return Corpus(
        population=population,
        row=row,
        documents=documents,
        cases=cases,
        pipeline=pipeline,
        measured=measure_precision(pipeline, cases),
        measured_without_rerank=measure_precision(without_rerank, cases),
    )


class TestThePremises:
    """Lo que tiene que ser cierto para que lo de abajo signifique algo."""

    def test_the_corpus_is_the_one_this_row_describes(self, corpus: Corpus):
        """Páginas, chunks y preguntas, los tres, contra la fila de esta población.

        Sin esto, todo lo de abajo sería un guard que compara una medición con
        una cifra y no dice de qué corpus habla. Y "124 chunks" agreeing por
        casualidad no es corroboración: es la razón por la que un corpus
        distinto con la misma forma pasó inadvertido antes
        (``tests/test_committed_corpus_figures.py``, docstring del módulo).
        """
        assert len(corpus.documents) == corpus.row.pages, (
            f"la población {corpus.population!r} carga {len(corpus.documents)} "
            f"páginas; su fila dice {corpus.row.pages}."
        )
        assert len(corpus.pipeline.chunks) == corpus.row.chunks, (
            f"la población {corpus.population!r} chunkea a "
            f"{len(corpus.pipeline.chunks)}; su fila dice {corpus.row.chunks}."
        )
        assert len(corpus.cases) == corpus.row.questions, (
            f"la población {corpus.population!r} resuelve {len(corpus.cases)} "
            f"etiquetas; su fila dice {corpus.row.questions}."
        )

    def test_retrieval_ran_in_the_space_the_figures_are_about(self, corpus: Corpus):
        """``mode == "embeddings"``, o nada de esto midió lo que dice medir.

        ``RAGPipeline.initialize()`` cae a TF-IDF si sentence-transformers no
        carga, y un guard que midiera ese espacio publicaría cifras de otra
        relación con el mundo sin decir nada: el chunk count es el mismo y
        ``/api/health`` sigue reportando ``ok``. Es la razón por la que
        ``RAGPipeline.mode`` existe, y aquí es la premisa de todo el archivo.
        """
        assert corpus.pipeline.mode == "embeddings", (
            f"la población {corpus.population!r} recuperó en modo "
            f"{corpus.pipeline.mode!r}, no en el espacio de embeddings. Las cifras "
            "de este archivo son sobre paraphrase-multilingual-MiniLM-L12-v2; "
            "midiendo el fallback publicarían una relación distinta con el mismo "
            "nombre. Instala sentence-transformers y vuelve a medir."
        )

    def test_the_measurement_writes_no_cache_anywhere(self, corpus: Corpus):
        """``cache_dir`` sigue siendo ``None``, por decisión de ``real_wiki.py``.

        No es una preferencia de este archivo: ``tests/real_wiki.py:150-161`` lo
        fijó para que el harness no sea un segundo escritor de
        ``backend/.rag_cache/``, que la app reescribe al arrancar. Un
        ``cache_dir`` pasado aquí convertiría la medición en una escritura sobre
        el artefacto de producción, y además haría que este guard dependiera del
        estado en disco de otra ejecución.
        """
        assert corpus.pipeline._cache_dir is None, (
            f"el pipeline de la población {corpus.population!r} fue construido con "
            f"cache_dir={corpus.pipeline._cache_dir!r}. tests/real_wiki.py:150-161 "
            "exige que este harness no escriba en backend/.rag_cache/."
        )

    def test_every_question_fills_the_slots_the_denominator_assumes(
        self, corpus: Corpus
    ):
        """Cada pregunta devuelve 3 páginas distintas, sin excepción.

        ``served_slots`` es ``3 x preguntas`` y ese es el DENOMINADOR de
        ``precision@3``. No es una cuenta atrás: es la afirmación de que el
        pipeline llenó los tres huecos en las 49 preguntas. Si una devolviera dos,
        el denominador sobraría un slot y la fracción bajaría sin que ninguna
        medición de ranking hubiera cambiado, que es exactamente la clase de
        defecto que este repositorio ya tuvo que deshacer dos veces con otras
        unidades.
        """
        short = []
        for case in corpus.cases:
            served = served_pages(corpus.pipeline, case.question)
            if len(served) != SHIPPED_TOP_K or len(set(served)) != SHIPPED_TOP_K:
                short.append((case.question, served))
        assert not short, (
            f"en la población {corpus.population!r} estas preguntas no llenan los "
            f"tres slots del top-3: {short}. El denominador de precision@3 asume "
            f"{SHIPPED_TOP_K} páginas por pregunta, así que hay que re-derivar la "
            "cifra o el denominador, no ajustarlos para que cuadren."
        )

    def test_the_metric_still_has_something_to_measure(self, corpus: Corpus):
        """Ninguna pregunta con los tres slots relevantes.

        Si algún día el corpus fuera tan pequeño que cada top-3 fuera la
        respuesta entera, ``precision@3`` valdría 1.0 en todas partes y este
        archivo no estaría midiendo nada sin que nada lo dijera.
        """
        assert corpus.row.precision3 < 1.0, (
            f"la población {corpus.population!r} sirve sólo páginas relevantes: "
            "la métrica no tiene nada que separar y las aserciones de abajo "
            "pasan por la razón equivocada."
        )


class TestTheLabelSetCannotManufacturePrecision:
    """La premisa que explica por qué ``precision@1`` iguala a ``recall@1``.

    Fuera de la parametrización porque es una propiedad del SET DE ETIQUETAS, no
    de un corpus: si esto cambia, las dos cifras publicadas dejan de coincidir por
    la razón que dice el módulo y empiezan a coincidir por casualidad, que es
    peor.
    """

    def test_exactly_one_label_names_a_second_acceptable_page(self):
        """``also`` no está vacío en una sola de las 49, y es la que se nombra.

        Sin esto, ``precision@1`` sería una métrica distinta de la que se
        publicaría como si fuera la misma, y nadie podría ver por qué dan el
        mismo número.
        """
        duplicated = duplicated_cases()
        assert [case.question for case in duplicated] == [DUPLICATED_QUESTION], (
            "las etiquetas con más de una página aceptable han cambiado: "
            f"{[case.question for case in duplicated]}. "
            "tests/retrieval_measurements.py::DUPLICATED_QUESTION nombra la que "
            "existe y el módulo explica por qué precision@1 coincide con "
            "recall@1; si el set cambia, esa explicación cambia con él."
        )

    def test_the_relevant_set_is_the_union_of_primary_and_also(self):
        """La definición de relevante, comprobada contra la de la fila.

        ``relevant_pages`` es la FUNCIÓN de la que salen los contadores, así que
        un cambio ahí no se vería en ninguna cifra: sólo cambiaría lo que mide
        este archivo. Se afirma contra el ``primary`` y el ``also`` de la
        etiqueta, que es donde el significado vive.
        """
        from tests.retrieval_measurements import relevant_pages

        for case in duplicated_cases():
            assert relevant_pages(case) == frozenset({case.primary}) | case.also
            assert len(relevant_pages(case)) == 2


class TestTheFiguresAreTheMeasuredOnes:
    """Cada contador de la fila, contra una medición viva a través de ``retrieve()``."""

    def test_relevant_at_1_matches_the_row(self, corpus: Corpus):
        measured = corpus.measured["relevant_at_1"]
        assert measured == corpus.row.relevant_at_1, (
            f"la población {corpus.population!r} sirve la página relevante en el "
            f"rank 1 para {measured} de {len(corpus.cases)} preguntas; su fila "
            f"dice {corpus.row.relevant_at_1}."
        )
        assert corpus.row.precision1 == pytest.approx(
            measured / len(corpus.cases), abs=0.0005
        )

    def test_relevant_slots_at_3_match_the_row(self, corpus: Corpus):
        """El denominador es ``top_k`` POR PREGUNTAS, y esto lo comprueba.

        45 de 147, no 45 de 49. La confusión entre esas dos divisiones es la
        misma clase de error que el filtro del coseno cometió con "956 de 6125
        pares", y por eso el denominador se deriva como propiedad y se afirma
        contra el número de slots que la población dice servir.
        """
        measured = corpus.measured["relevant_slots_at_3"]
        assert corpus.row.served_slots == SHIPPED_TOP_K * corpus.row.questions
        assert measured == corpus.row.relevant_slots_at_3, (
            f"la población {corpus.population!r} sirve {measured} slots relevantes "
            f"de {corpus.row.served_slots}; su fila dice "
            f"{corpus.row.relevant_slots_at_3}."
        )
        assert corpus.row.precision3 == pytest.approx(
            measured / corpus.row.served_slots, abs=0.0005
        )

    def test_the_dense_only_comparison_matches_the_row(self, corpus: Corpus):
        """Los mismos contadores con el rescate léxico forzado a declinar.

        Es la comparación que necesita la fase 2: si el rescate trajera la
        respuesta Y cuesta un slot de ruido, es un trato distinto del que
        publicaba hasta ahora, que sólo hablaba de recall.
        """
        measured = corpus.measured["relevant_slots_at_3_dense"]
        assert measured == corpus.row.relevant_slots_at_3_dense, (
            f"sin rescate, la población {corpus.population!r} sirve {measured} slots "
            f"relevantes; su fila dice {corpus.row.relevant_slots_at_3_dense}."
        )
        assert corpus.row.precision3_dense == pytest.approx(
            measured / corpus.row.served_slots, abs=0.0005
        )

    def test_the_recorded_ceiling_is_the_one_the_labels_allow(self, corpus: Corpus):
        """``possible_slots`` sale de las etiquetas, no de una ejecución.

        Es el denominador que hace legible el 0.3061, así que si se derivara de
        la medición dejaría de ser un techo: bajaría con ella y el 0.90 publicable
        no significaría nada. Afirmado contra ``possible_relevant_slots`` para que
        el número guardado y el derivado no puedan separarse.
        """
        assert corpus.row.possible_slots == possible_relevant_slots(corpus.cases), (
            f"la fila declara {corpus.row.possible_slots} slots posibles y las "
            f"etiquetas de la población {corpus.population!r} permiten "
            f"{possible_relevant_slots(corpus.cases)}."
        )


class TestPrecisionIsAboveItsFloor:
    """El suelo, que es donde vive la capacidad de detectar una regresión."""

    def test_precision1_is_above_its_floor(self, corpus: Corpus):
        measured = corpus.measured["relevant_at_1"] / len(corpus.cases)
        assert measured >= corpus.row.precision1_floor, (
            f"precision@1 de la población {corpus.population!r} es {measured:.4f}, "
            f"por debajo de su suelo {corpus.row.precision1_floor:.4f} "
            f"(medición - TOLERATED_SLOTS). Vuelve a medir y a escribir la cifra "
            "en tests/retrieval_measurements.py, o explica qué se rompió."
        )

    def test_precision3_is_above_its_floor(self, corpus: Corpus):
        measured = corpus.measured["relevant_slots_at_3"] / corpus.row.served_slots
        assert measured >= corpus.row.precision3_floor, (
            f"precision@3 de la población {corpus.population!r} es {measured:.4f}, "
            f"por debajo de su suelo {corpus.row.precision3_floor:.4f} "
            f"(medición - TOLERATED_SLOTS). Un slot es 1/"
            f"{corpus.row.served_slots}, así que esto son al menos "
            f"{SHIPPED_TOP_K} preguntas cuyo acierto salió del top-3."
        )

    def test_the_dense_only_precision3_is_above_its_floor(self, corpus: Corpus):
        measured = (
            corpus.measured["relevant_slots_at_3_dense"] / corpus.row.served_slots
        )
        assert measured >= corpus.row.precision3_dense_floor, (
            f"sin rescate, precision@3 de la población {corpus.population!r} es "
            f"{measured:.4f}, por debajo de su suelo "
            f"{corpus.row.precision3_dense_floor:.4f}."
        )


class TestTheQuestionsThatSinkItAreTheRecordedOnes:
    """Un agregado no dice qué arreglar. Estas dos listas sí."""

    def test_the_buried_set_is_exactly_what_is_recorded(self, corpus: Corpus):
        """La página dorada en rank 2-3: el margen real de un reranker.

        Publicada con nombre porque es una decisión que se puede revisar: las
        preguntas que hoy se leen en el orden equivocado, y una lista que cambia
        cuando cambia el corpus. Si crece o mengua, se re-deriva aquí y se dice
        qué pasó; no se ensancha la aserción hasta que encaje.
        """
        measured: FrozenSet[str] = corpus.measured["buried"]
        assert measured == corpus.row.buried, (
            f"en la población {corpus.population!r} la respuesta está en rank 2-3 "
            f"para {sorted(measured)}; tests/retrieval_measurements.py registra "
            f"{sorted(corpus.row.buried)}.\n"
            "Si la lista cambió porque cambió el retriever, re-derívala y explica "
            "el movimiento: es la lista que dice si un reranker tiene trabajo."
        )

    def test_the_absent_set_is_exactly_what_is_recorded(self, corpus: Corpus):
        """La respuesta en NONE de los tres slots: lo que un reranker no arregla.

        Publicada por la misma razón y con el aviso adjunto, porque la tentación
        de leer las enterradas y las ausentes como el mismo problema es justo la
        que lleva a prometer un reranker que no puede cumplir.
        """
        measured: FrozenSet[str] = corpus.measured["absent"]
        assert measured == corpus.row.absent, (
            f"en la población {corpus.population!r} la respuesta no está en el "
            f"top-3 para {sorted(measured)}; la fila registra "
            f"{sorted(corpus.row.absent)}.\n"
            "Reordenar los mismos tres slots no puede recuperar ninguna de estas: "
            "la página no está en la lista."
        )

    def test_the_buckets_are_exhaustive_and_the_slot_counter_is_not_a_question_counter(
        self, corpus: Corpus
    ):
        """rank 1 + enterradas + ausentes = todas, Y los slots no son preguntas.

        La segunda parte es la que importa: 45 slots relevantes sobre 44
        preguntas acertadas es la diferencia de UNA página, la segunda aceptable
de la etiqueta duplicada. Si los dos contadores coincidieran, el número sería
        indistinguible de un recuento de preguntas, y ésa es la ambigüedad que ya
        rompió una cifra publicada en este repositorio.
        """
        at_1 = corpus.measured["relevant_at_1"]
        buried: FrozenSet[str] = corpus.measured["buried"]
        absent: FrozenSet[str] = corpus.measured["absent"]

        assert at_1 + len(buried) + len(absent) == len(corpus.cases), (
            f"los tres cubos suman {at_1 + len(buried) + len(absent)} sobre "
            f"{len(corpus.cases)} preguntas. O hay una pregunta en dos cubos, o "
            "falta un cubo, y el otro conjunto de este archivo deja de ser la "
            "partición que dice ser."
        )
        assert corpus.measured["relevant_slots_at_3"] - (at_1 + len(buried)) == 1, (
            "los slots relevantes deberían exceder en UNO a las preguntas "
            "acertadas, y es la segunda página aceptable de la etiqueta "
            f"duplicada ({DUPLICATED_QUESTION!r}). Si no lo hacen, o la etiqueta "
            "duplicada dejó de estar duplicada, o el contador de slots se está "
            "midiendo en preguntas."
        )

    def test_the_slots_lost_are_exactly_the_questions_with_no_gold_page(
        self, corpus: Corpus
    ):
        """Techo - slots servidos = número de preguntas sin respuesta.

        Una identidad aritmética entre un número derivado de las etiquetas y dos
        números medidos, que es el tipo de comprobación que sobrevive a un
        cambio en el corpus y avisa antes de que nadie mire una cifra.
        """
        lost = corpus.row.possible_slots - corpus.measured["relevant_slots_at_3"]
        assert lost == len(corpus.measured["absent"]), (
            f"en la población {corpus.population!r} sobran {lost} slots posibles "
            f"y hay {len(corpus.measured['absent'])} preguntas sin página dorada. "
            "La identidad se rompe si una pregunta con DOS páginas aceptables "
            f"pierde una de ellas; mira {DUPLICATED_QUESTION!r} antes de "
            "reasignar un número."
        )


class TestTheRescueRecordAndThePrecisionRecordAgree:
    """Dos artefactos medidos por separado que tienen que describir lo mismo.

    ``tests/lexical_rescue_losses.py`` registra, por población, qué preguntas
    gana y qué preguntas pierde el rescate léxico. Este archivo acaba de medir,
    por separado, qué preguntas tienen su respuesta en el top-3 con y sin él. Las
    dos cosas no pueden ser ciertas a la vez si alguno de los dos registros se ha
    quedado viejo, y ese es el motivo de esta clase.
    """

    def test_every_question_the_rescue_gains_is_now_buried(self, corpus: Corpus):
        """El rescate sólo añade en el ÚLTIMO slot, así que gana = enterrada.

        Afirmado como inclusión y no como igualdad a propósito: hay preguntas
        enterradas que el rescate no ganó (ya estaban en rank 2 o 3 sin él), y
        esa asimetría es información, no ruido.

        SE MIDE SIN EL CROSS-ENCODER, y no es un detalle. La premisa de este
        invariante -- "ganada" implica "en el último slot, luego enterrada" -- la
        cumple el RANKING, y el cross-encoder reordena el ranking: una pregunta
        ganada por el rescate puede acabar en rank 1 y ya no está enterrada. Le
       ída con el reranker encendido, la aserción estaría midiendo la promesa
        del rescate contra la configuración que el rescate no describe, y fallaría
        sin que ninguna de las dos partes esté equivocada.

        Así que el registro del rescate se comprueba contra el ranking que
       produced -- el mismo sobre el que se grabó -- y la configuración de
        producción se comprueba aparte, en la aserción siguiente.
        """
        gains = RESCUE_GAINS[corpus.population]
        buried: FrozenSet[str] = corpus.measured_without_rerank["buried"]
        assert gains <= buried, (
            f"el rescate declara ganar {sorted(gains - buried)} en la población "
            f"{corpus.population!r}, pero esas preguntas no tienen su respuesta en "
            "rank 2-3. Como el rescate sólo puede añadir en el último slot, una "
            "pregunta ganada tiene que estar enterrada por definición: o el "
            "registro del rescate está viejo, o la medición de precisión lo está."
        )

    def test_every_question_the_rescue_gains_is_still_served_after_reranking(
        self, corpus: Corpus
    ):
        """Y en la configuración que SÍ se embarca, la ganancia del rescate sigue ahí.

        Esta es la versión débil del invariante de arriba, y la que protege al
        producto: el cross-encoder puede mover una pregunta ganada del rank 3 al
        1, pero no puede SACARLA del top-3, porque reorderar no cambia el conjunto.
        Lo que se afirma es exactamente eso -- y si algún día el re-ranker
        empezara a selecting en vez de permutar, esta aserción caería antes que la
        de `recall@3`, que es donde de verdad se nota.
        """
        gains = RESCUE_GAINS[corpus.population]
        absent: FrozenSet[str] = corpus.measured["absent"]
        assert not (gains & absent), (
            f"el cross-encoder dejó de servir {sorted(gains & absent)} en la "
            f"población {corpus.population!r}, y el rescate las declaraba ganadas. "
            "Un re-ranker que reordena no puede sacar una página del top-3; si "
            "esto se dispara, el stage está seleccionando y no permutando."
        )
        at_1_before = corpus.measured_without_rerank["relevant_at_1"]
        at_1_after = corpus.measured["relevant_at_1"]
        assert at_1_after >= at_1_before, (
            f"el cross-encoder bajó recall@1 de {at_1_before} a {at_1_after} en la "
            f"población {corpus.population!r}. La aceptacion de la fase 2 es neta "
            "positiva (tests/test_rerank.py) y aquí no puede perderse: el conjunto "
            "servido es idéntico con y sin reranker."
        )

    def test_every_question_the_rescue_costs_is_absent(self, corpus: Corpus):
        """Y el otro lado: una pregunta que el rescate cuesta ya no está en el top-3.

        Ésta es la contraparte de la anterior, y por eso las dos importan: juntas
        dicen que la delta de slots relevantes entre con y sin rescate es
        exactamente ganado menos perdido.
        """
        costs = RESCUE_COSTS[corpus.population]
        absent: FrozenSet[str] = corpus.measured["absent"]
        assert costs <= absent, (
            f"el rescate declara perder {sorted(costs - absent)} en la población "
            f"{corpus.population!r}, pero esas preguntas todavía tienen su "
            "respuesta en el top-3, lo que es imposible si el rescate les quita "
            "el último slot."
        )

    def test_the_slot_delta_is_the_rescue_record_arithmetic(
        self, corpus: Corpus
    ):
        """La diferencia de slots ES ``ganados - perdidos``, y por eso la anterior.

        Aritmética, con los dos registros ya medidos por separado. Si el rescate
        metiera ruido además de traer respuestas, esta suma no cerraría y el
        registro de precisión estaría diciendo algo que el registro del rescate
        contradice.
        """
        delta = (
            corpus.measured["relevant_slots_at_3"]
            - corpus.measured["relevant_slots_at_3_dense"]
        )
        expected = len(RESCUE_GAINS[corpus.population]) - len(
            RESCUE_COSTS[corpus.population]
        )
        assert delta == expected, (
            f"en la población {corpus.population!r} el rescate aporta {delta} slots "
            f"relevantes y el registro dice {expected} "
            f"({len(RESCUE_GAINS[corpus.population])} ganados menos "
            f"{len(RESCUE_COSTS[corpus.population])} perdidos). Cada pregunta ganada "
            "mete un slot relevante y cada perdida lo saca; si no cuadra, mira si "
            f"la etiqueta duplicada ({DUPLICATED_QUESTION!r}) ha cambiado de "
            "posición, que es lo único que puede romper la cuenta."
        )