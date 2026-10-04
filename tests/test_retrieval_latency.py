"""Latencia real de ``retrieve()`` sobre el corpus real, con techo derivado.

EL HUECO
--------
El único test de rendimiento que tenía este repositorio era
``tests/test_rag.py:206-227``, y medía esto:

    10 documentos inventados, ~50 chunks, una consulta repetida 10 veces
    assert avg_ms < 500

Tres cosas lo hacen inservible como medida de lo que embarca el producto, y
ninguna es culpa del número: es el objeto medido. Un corpus de 50 chunks de
texto inventado es 2.5 veces más pequeño que el real; una MEDIA de diez llamadas
repetidas sobre la misma consulta no tiene p95 ni máximo, que es donde aparece
el problema cuando alguien está esperando; y un techo de 500 ms de media es un
techo que no puede fallar en una máquina donde la consulta tarda 40 ms, que es lo
que mide. Lo que no había era NINGUNA cifra de latencia sobre los 124 chunks que
la app sirve, y sin ella "el retrieval es rápido" es una Sinatra.

LA MEDICIÓN, Y CADA COSA QUE SE DECIDIÓ EN EL CAMINO
----------------------------------------------------
Una pasada por las preguntas del set etiquetado, con la producción encendida
(rescate léxico incluido), sin calentar nada y sin escribir cache. Medido:

    full    (37 páginas, 124 chunks, 49 preguntas)
            mediana 23.89 ms   p95 33.84 ms   max 59.81 ms
    reduced (33 páginas, 116 chunks, 41 preguntas)
            mediana 23.36 ms   p95 28.60 ms   max 39.31 ms

Cuatro decisiones, y las cuatro son mediciones y no preferencias:

    * SIN CALENTAR. El primer ``retrieve()`` tras el ingest paga el ajuste
      perezoso del índice BM25 y el primer ``encode()`` del proceso. La
      producción tampoco calienta, así que excluirlo daría una mediana más
      bonita y falsa.
    * LAS FILAS GUARDAN LA PAS MÁS LENTA de las observadas (mediana 21.9-23.9 ms
      y p95 24.2-33.8 ms entre cuatro pasadas seguidas). Un techo elegido sobre
      una ejecución afortunada es un techo que se dispara solo en la segunda.
    * EL TECHO ES UN MÚLTIPLO DE LA MEDICIÓN, no un número tecleado: 4x para la
      mediana, 6x para el p95. Con 4x sobra para un runner cuatro veces más
      lento que la máquina donde se calibró; para el p95 hace falta más porque
      es el percentil 47 de 49 y está a un outlier del máximo. La fórmula está
      en ``tests/retrieval_measurements.py`` para que subir una cifra sin decidir
      la tolerancia no pueda pasar inadvertido.
    * EL MÁXIMO SE PUBLICA Y NO SE CEILEA. Sobre unas 1000 llamadas el peor
      máximo observado fue 172.7 ms, casi ocho veces la mediana, por preemptción
      del planificador. Un techo sobre el máximo sería inútil (500 ms no dispara
      nunca) o intermitente (una máquina tres veces más lenta se acerca y falla
      por ruido), así que el guard se apoya en las dos estadísticas que sí se
      comportan y publica la tercera. Es una decisión, y por eso está escrita
      aquí y no sólo en la cabeza de quien la tomó.

POR QUÉ ESTE ES EL GUARD MÁS LENTO DE LA SUITE
----------------------------------------------
Porque carga el embedder real dos veces: una por población, porque cada fila se
mide en SU corpus (la ``full`` es el working tree del autor y la ``reduced`` se
lee de ``git show HEAD:``). El ingest son 9-17 s por población.

Y por qué NO usa el caché de embeddings, siendo lo más caro que hace:
``tests/real_wiki.py:150-161`` decidió que este harness no sea un segundo
escritor de ``backend/.rag_cache/``, que la app reescribe al arrancar, y esa
decisión se respeta aquí sin excepciones. El precio es medir sin escribir en
producción y volver a embeber en cada pasada; el beneficio es que nadie puede
leer estas cifras como una afirmación sobre el artefacto que el producto
sobrescribe al arrancar. Lo que sí se reutiliza entre ejecuciones es el
checkpoint del modelo, en el caché de HuggingFace, y por eso son 9-17 s y no el
minuto del primer download. El guard lo afirma, porque un ``cache_dir`` pasado
por aquí convertiría la medición en una escritura sobre producción.

QUÉ SE FIJA Y QUÉ NO
--------------------
Fijado: que la mediana y el p95 están bajo un techo derivado de la medición, que
el techo se deriva y no se teclea, que el p95 es por rango más cercano, y que las
tres estadísticas son coherentes entre sí (mediana <= p95 <= max) tanto en la fila
como en lo medido. Ese último es el que detecta un percentile mal conectado.

No fijado: el máximo, por lo de arriba. Y ninguna cota por DEBAJO, porque una
medición más rápida no es un defecto. Lo que sí hay es un suelo de sentido
(un octavo de la cifra grabada), que no es una cota de rendimiento sino la
comprobación de que se está midiendo lo mismo: un stub o un corpus vacío
devolverían milisegundos, pasarían cualquier techo, y esta es la única aserción
del archivo que los distingue.
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence

import pytest

from backend.services.rag import RAGPipeline
from tests.real_wiki import Case, load_documents, resolved_cases, wiki_is_present
from tests.retrieval_measurements import (
    LATENCY_FIGURES,
    MEDIAN_HEADROOM,
    P95_HEADROOM,
    LatencyFigures,
    build_pipeline,
    documents_for,
    latency_for,
    latency_summary,
    measure_latency,
    percentile,
)

_WIKI_PRESENT = wiki_is_present()
_CHECKOUT_QUESTIONS = len(resolved_cases(load_documents())) if _WIKI_PRESENT else 0

#: Cuánto más lento puede ser el ``max`` de una pasada sin que la cifra publicada
#: esté contando otra cosa. Amplo a propósito: el máximo es ruido (172.7 ms
#: observados contra una mediana de 23), así que este factor mide si sigue
#: siendo el mismo orden de magnitud, no si ha empeorado.
MAX_NOISE_FACTOR = 8.0

POPULATIONS = (
    pytest.param(
        "full",
        marks=pytest.mark.skipif(
            _CHECKOUT_QUESTIONS != 49,
            reason="sin las 4 páginas FAQ sin commitear: la fila 'full' ES el "
            "working tree del autor",
        ),
    ),
    pytest.param(
        "reduced",
        marks=pytest.mark.skipif(
            not _WIKI_PRESENT, reason="wiki/ ausente: el corpus se borró"
        ),
    ),
)


@dataclass(frozen=True)
class TimedCorpus:
    """Una población, su fila de latencia, y la pasada medida sobre SU corpus."""

    population: str
    row: LatencyFigures
    documents: Dict[str, str]
    cases: Sequence[Case]
    pipeline: RAGPipeline
    timings: List[float]
    measured: Dict[str, float]


@pytest.fixture(scope="module", params=POPULATIONS)
def timed(request, tmp_path_factory) -> TimedCorpus:
    """La fila de esta población y UNA pasada de latencia por ``retrieve()``.

    Module-scoped por el mismo motivo que en el guard de precisión: embeber el
    corpus cuesta 9-17 s con el modelo real, y una mediana de 49 llamadas no se
    hace más sólida repitiéndola dentro del mismo proceso.

    La fila se resuelve con ``latency_for``, que RECHAZA una población no
    calibrada en lugar de devolver otra: un corpus que resuelve un tercer número
    de etiquetas es una población nueva, y una cifra de latencia es una
    afirmación sobre un corpus, no sobre un número de preguntas.
    """
    population = request.param
    root = tmp_path_factory.mktemp(f"latency_{population}")
    documents = documents_for(population, root)
    assert documents is not None, (
        f"documents_for({population!r}) devolvió None en un checkout que debería "
        f"tener esa población. El skip de este parámetro no está funcionando."
    )

    row = latency_for(documents)
    assert row is not None, (
        f"el corpus de la población {population!r} resuelve "
        f"{len(resolved_cases(documents))} etiquetas y no hay fila de latencia "
        "para ese número. Vuelve a medir sobre la población que queda y añade la "
        "suya en tests/retrieval_measurements.py."
    )

    cases = resolved_cases(documents)
    pipeline = build_pipeline(documents)
    timings = measure_latency(pipeline, cases)
    return TimedCorpus(
        population=population,
        row=row,
        documents=documents,
        cases=cases,
        pipeline=pipeline,
        timings=timings,
        measured=latency_summary(timings),
    )


class TestThePremises:
    def test_the_corpus_is_the_one_this_row_describes(self, timed: TimedCorpus):
        """Chunks y preguntas, los dos, contra la fila de esta población.

        Una cifra de latencia sin corpus es un número de máquina. Y "116 chunks"
        agreeing por casualidad no es corroboración: es la razón por la que un
        corpus distinto con la misma forma pasó inadvertido antes
        (``tests/test_committed_corpus_figures.py``, docstring del módulo).
        """
        assert len(timed.pipeline.chunks) == timed.row.chunks, (
            f"la población {timed.population!r} chunkea a "
            f"{len(timed.pipeline.chunks)}; su fila dice {timed.row.chunks}."
        )
        assert len(timed.cases) == timed.row.questions == len(timed.timings), (
            f"la población {timed.population!r} tiene {len(timed.cases)} "
            f"preguntas, su fila dice {timed.row.questions} y se midieron "
            f"{len(timed.timings)} llamadas. Las tres tienen que ser el mismo "
            "número: una llamada por pregunta, y ninguna de más."
        )

    def test_retrieval_ran_in_the_space_the_figures_are_about(self, timed: TimedCorpus):
        """``mode == "embeddings"``, o se midió la caída a TF-IDF.

        Es la premisa más importante de este archivo, porque el fallback es MÁS
        RÁPIDO: mediría un ``transform`` de un vectorizador en vez de una
        embedding de MiniLM, daría una mediana de un milisegundo y pasaría
        cualquier techo. Por eso además hay un suelo más abajo: los dos juntos
        son lo que impide que este guard sea verde midiendo otra cosa.
        """
        assert timed.pipeline.mode == "embeddings", (
            f"la población {timed.population!r} recuperó en modo "
            f"{timed.pipeline.mode!r}. Las cifras de este archivo son sobre "
            "paraphrase-multilingual-MiniLM-L12-v2; el fallback TF-IDF es más "
            "rápido y pasaría cualquier techo mientras mide otra cosa."
        )

    def test_the_measurement_writes_no_cache_anywhere(self, timed: TimedCorpus):
        """``cache_dir`` sigue siendo ``None``, por decisión de ``real_wiki.py``.

        Aquí el coste de esa decisión es el más caro de la suite (re-embeber los
        124 chunks en cada pasada), y aun así no se toca: este harness no va a
        ser un segundo escritor de ``backend/.rag_cache/``, que la app reescribe
        al arrancar.
        """
        assert timed.pipeline._cache_dir is None, (
            f"el pipeline de la población {timed.population!r} fue construido con "
            f"cache_dir={timed.pipeline._cache_dir!r}. tests/real_wiki.py:150-161 "
            "exige que este harness no escriba en backend/.rag_cache/."
        )


class TestTheCeilings:
    def test_the_median_is_under_its_ceiling(self, timed: TimedCorpus):
        ceiling = timed.row.median_ceiling_ms
        assert timed.measured["median_ms"] <= ceiling, (
            f"la mediana de retrieve() en la población {timed.population!r} es "
            f"{timed.measured['median_ms']:.2f} ms y su techo es {ceiling:.0f} ms "
            f"({timed.row.median_ms:.2f} x {MEDIAN_HEADROOM:g}).\n"
            "El techo viene de la medición, así que esto significa que el "
            "retrieval se ha vuelto más lento de lo que se midió: o hay una "
            "regresión, o la máquina es otra, o el embedder ha cambiado. "
            "Vuelve a medir y sube la fila CON SU TECHO, y di cuál de las tres "
            "fue."
        )

    def test_the_p95_is_under_its_ceiling(self, timed: TimedCorpus):
        ceiling = timed.row.p95_ceiling_ms
        assert timed.measured["p95_ms"] <= ceiling, (
            f"el p95 de retrieve() en la población {timed.population!r} es "
            f"{timed.measured['p95_ms']:.2f} ms y su techo es {ceiling:.0f} ms "
            f"({timed.row.p95_ms:.2f} x {P95_HEADROOM:g}). El p95 es el percentil "
            f"{math.ceil(0.95 * len(timed.timings))} de {len(timed.timings)}, así "
            "que un techo que salta aquí es una cola, no una mediana que se ha "
            "degradado: mira también el máximo antes de concluir nada."
        )

    def test_the_ceilings_are_derived_from_the_measurement_not_typed(
        self, timed: TimedCorpus
    ):
        """El techo es una expresión de la cifra guardada, y por eso no se puede subir.

        Es la misma forma que el suelo de recall de ``tests/real_wiki.py``, que
        está clavado por
        ``tests/test_rag.py::test_the_floor_is_a_function_of_the_measurement_not_a_literal``:
        un techo tecleado puede ajustarse a una regresión en la misma línea que
        la regresión, y un techo derivado obliga a decidir el multiplicador
        explícitamente.
        """
        assert timed.row.median_ceiling_ms == math.ceil(
            timed.row.median_ms * MEDIAN_HEADROOM
        )
        assert timed.row.p95_ceiling_ms == math.ceil(
            timed.row.p95_ms * P95_HEADROOM
        )
        assert timed.row.median_ceiling_ms > timed.row.median_ms, (
            "un techo por debajo de su propia medición no es un techo: la "
            "derivación está rota o el multiplicador es menor que 1."
        )

    def test_the_measurement_is_the_same_measurement(self, timed: TimedCorpus):
        """La mediana medida no puede ser un orden de magnitud más rápida.

        NO es una cota de rendimiento y no pretende serlo: una máquina más
        rápida tiene que pasar. Lo que no puede pasar es una medición contra un
        stub, un corpus vacío o un embedder que no se cargó, porque eso es más
        rápido en tres órdenes de magnitud y pasaría todos los techos de
        arriba sin haber medido el retrieval.
        """
        floor_ms = timed.row.median_ms / MAX_NOISE_FACTOR
        assert timed.measured["median_ms"] >= floor_ms, (
            f"la mediana medida en la población {timed.population!r} es "
            f"{timed.measured['median_ms']:.3f} ms, más de {MAX_NOISE_FACTOR:g}x "
            f"más rápida que la grabada ({timed.row.median_ms:.2f} ms). Eso no es "
            "una mejora: es otra medición. Comprueba que el embedder se cargó "
            "realmente y que el corpus tiene los chunks que dice la fila."
        )


class TestTheStatisticsAreWhatTheyClaimToBe:
    def test_the_three_statistics_are_ordered_in_the_row_and_in_the_measurement(
        self, timed: TimedCorpus
    ):
        """``mediana <= p95 <= max``, en la fila grabada y en lo medido ahora.

        Es la aserción que detecta un percentile mal conectado o una fila
        escrita al revés, y por eso compara las dos: una fila coherente con una
        medición incoherente también es un defecto.
        """
        for label, values in (
            ("fila", (timed.row.median_ms, timed.row.p95_ms, timed.row.max_ms)),
            (
                "medición",
                (
                    timed.measured["median_ms"],
                    timed.measured["p95_ms"],
                    timed.measured["max_ms"],
                ),
            ),
        ):
            median, p95, maximum = values
            assert median <= p95 <= maximum, (
                f"en la población {timed.population!r} la {label} da "
                f"mediana {median:.2f}, p95 {p95:.2f} y max {maximum:.2f}. "
                "Una de las tres está mal conectada o escrita al revés."
            )

    def test_the_maximum_is_published_and_only_drifted_against(self, timed: TimedCorpus):
        """El máximo se publica, y se compara con margen, no con un techo.

        La ausencia de techo es deliberada y está explicada en el docstring del
        módulo: el máximo de esta máquina es ruido preemptivo (172.7 ms
        observados contra una mediana de 23 ms), así que un techo aquí o no
        dispara o falla por el planificador. Lo que sí se afirma es que la cifra
        publicada sigue siendo del mismo orden de magnitud que lo que se mide,
        porque un ``max_ms`` grabado que ya no describe nada es una cifra
        publicada que miente sin disclaimer.
        """
        assert timed.row.max_ms > timed.row.p95_ms, (
            "la fila declara un máximo menor o igual que su p95, así que el "
            "máximo no está midiendo la cola de la distribución."
        )
        assert timed.measured["max_ms"] <= MAX_NOISE_FACTOR * timed.row.max_ms, (
            f"el máximo medido en la población {timed.population!r} es "
            f"{timed.measured['max_ms']:.1f} ms contra un máximo grabado de "
            f"{timed.row.max_ms:.1f} ms. Un salto de este tamaño no es ruido de "
            "planificador: vuelve a medir y actualiza la fila."
        )

    def test_the_percentile_is_nearest_rank_and_not_interpolated(self):
        """``sorted(v)[ceil(0.95*n)-1]``, y la otra convención daría otro número.

        Sin pipeline: es una propiedad de la FUNCIÓN, y se afirma sobre una
        muestra pequeña y construida a mano donde las dos definiciones separan.
        Dos percentiles son de uso común en el mismo repositorio
        (``tests/real_wiki.py:662`` para la forma de las palabras, y éste para la
        latencia), y el nombre de la convención es lo que impide que un guard
        los derive de formas distintas sin que nadie lo note.
        """
        sample = [float(value) for value in range(1, 21)]  # 1..20, n = 20
        assert percentile(sample) == 19.0, (
            f"percentile dio {percentile(sample)} sobre 1..20; por rango más "
            "cercano es 19.0 (el índice ceil(0.95*20)-1 = 18)."
        )
        interpolated = 18.0 + 0.95 * (19.0 - 18.0)
        assert percentile(sample) != interpolated, (
            "la interpolación lineal daría 18.95 sobre la misma muestra. Si el "
            "resultado coincidiera, esta muestra ya no separaría las dos "
            "convenciones y el test pasaría sin comprobar nada."
        )

    def test_a_percentile_over_an_empty_sample_is_not_a_measurement(self):
        """El fallo que el aserto de ``percentile`` produce, exigido a propósito.

        Una función de percentil que devuelve 0.0 sobre una lista vacía es una
        función que devolvería un techo de 0 en un corpus que no cargó nada.
        """
        with pytest.raises(AssertionError):
            percentile([])


def test_both_populations_are_published():
    """Las dos filas existen, con chunks distintos, y ninguna es la otra.

    Trivial a propósito, y por eso está: una sola fila es una cifra que en el
    otro checkout nadie puede comprobar, que es exactamente lo que pasó con el
    recall cuando sólo se publicaba la del working tree.
    """
    assert [figures.population for figures in LATENCY_FIGURES] == ["full", "reduced"]
    assert len({figures.chunks for figures in LATENCY_FIGURES}) == 2, (
        "las dos filas declaran el mismo número de chunks, así que o son la "
        "misma población con dos nombres o una se copió de la otra."
    )
    for figures in LATENCY_FIGURES:
        assert figures.median_ceiling_ms > figures.median_ms, (
            f"la fila {figures.population!r} tiene el techo por debajo de su "
            "propia mediana, así que la derivación está rota."
        )
        assert figures.corpus, (
            f"la fila {figures.population!r} no dice de qué corpus es. Una "
            "cifra de latencia sin corpus es un número de máquina."
        )