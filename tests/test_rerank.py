"""The reranker's acceptance criterion, pinned so a future model change goes red.

QUÉ CIERRA ESTE MÓDULO
----------------------
La condición de aceptación de la fase 2, escrita como test y no como prosa: la
ganancia neta del reranker (promociones menos despromociones) tiene que ser
MAYOR QUE CERO, y el ``recall@3`` tiene que quedarse donde estaba. Un reranker
que empata o que pierde no entra, y esta es la parte del contrato que sobrevive a
que yo deje de mirar.

Medido por la ruta de producción (``retrieve()``, con el filtro del coseno, el
corte de una chunk por página y el rescate léxico encendidos), sobre las
poblaciones que este checkout puede resolver:

    población   recall@1   promotions   demotions   neto
    full        36 -> 40       7            3        +4
    reduced     31 -> 33       7            5        +2

Y ``recall@3`` NO SE MUEVE en ninguna de las dos -- 44/49 y 38/41 -- porque un
rerank es una permutación de los MISMOS slots y una permutación no cambia el
conjunto. Eso no es una buena noticia afortunada: es la razón por la que este
stage puede existir sin poner en riesgo ninguna afirmación sobre conjuntos que el
repositorio ya publica.

POR QUÉ EL NETO Y NO EL ``recall@1``
-----------------------------------
Porque el ``recall@1`` puede subir por dos razones distintas y sólo una es un
reranker funcionando. Si el conjunto servido creciera, subiría ``recall@1`` sin
que el orden hubiera mejorado nada. El desglose promociones/despromociones no
puede moverse por un cambio de conjunto: cuenta las preguntas cuya POSICIÓN
cambió. Es la pregunta que la fase 2 hacía.

Y POR QUÉ LA MARGEN ES ESTRECHA, DICHO DE ENTRADA
-------------------------------------------------
``+4`` sobre 49 es una unidad de 0.0816, y la tolerancia de este repositorio para
el ruido de medición es de dos o tres preguntas. La medición es DETERMINISTA --
misma entrada, mismo orden, tres pasadas seguidas dan el mismo conjunto de
promociones y despromociones -- así que el riesgo no es ruido: es
generalización. Siete promociones y tres despromociones sobre este corpus y este
modelo no son una promesa sobre otro corpus. El invariante que de verdad importa
a largo plazo no es el neto: es que ``MAX_RERANK_TOP_K`` deje el ``recall@3``
intacto para siempre, porque ése no se puede perder por accidente.

LO QUE ESTE MÓDULO AFIRMA, Y LO QUE NO
--------------------------------------
Afirma: la ganancia neta por población; el ``recall@3`` intacto; que la
permutación conserva el mismo conjunto de páginas; que un reranker que falla
degrada al orden denso+BM25 sin lanzar; y que el passage es ``embedding_text``.

No afirma: que la ganancia sea grande, ni que sobreviva a otro corpus, ni que el
cross-encoder entienda algo que no sea este español. Esas tres cosas son
mediciones nuevas, no tierra conocida, y este archivo no las viste de verde.
"""

from __future__ import annotations

import contextlib
import inspect
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

import pytest

from backend.services.rag import CHUNK_FILTER_VERSION, RAGPipeline, embedding_text
from backend.services.rerank import RERANK_VERSION, Reranker
from tests.real_wiki import load_documents, resolved_cases
from tests.retrieval_measurements import (
    SHIPPED_TOP_K,
    best_rank,
    build_pipeline,
    norm,
    relevant_pages,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Las filas ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RerankFigures:
    """La ganancia neta de una población, medida y con nombre.

    ``promotions`` y ``demotions`` son IDENTIDADES CON NOMBRE y no contadores,
    por el mismo motivo que ``PRECISION_FIGURES.buried`` lo es: un agregado que
    se mueve de 7 a 5 no dice qué se perdió, y una cifra que no puede decir qué
    se perdió es la que hace imposible diagnosticar una regresión.
    """

    population: str
    questions: int
    at_1_before: int
    at_1_after: int
    promotions: FrozenSet[str]
    demotions: FrozenSet[str]
    corpus: str

    @property
    def net(self) -> int:
        return len(self.promotions) - len(self.demotions)


RERANK_FULL = RerankFigures(
    population="full",
    questions=49,
    at_1_before=36,
    at_1_after=40,
    promotions=frozenset({
        "puedes empezar a trabajar ya estas disponible",
        "prefieres backend o frontend",
        "que opinas de la ia en el desarrollo de software",
        "que es interviewtts",
        "que resultados dio el proyecto de la pagina web de velneo",
        "en que consistian tus practicas en ceesa",
        "que sabes de backend y java",
    }),
    demotions=frozenset({
        "empezaste como frutero en mercadona no",
        "con que stack hiciste el detector de fraude",
        "como testias tu codigo",
    }),
    corpus="working tree del autor -- 37 paginas, 124 chunks",
)

RERANK_REDUCED = RerankFigures(
    population="reduced",
    questions=41,
    at_1_before=31,
    at_1_after=33,
    promotions=frozenset({
        "prefieres backend o frontend",
        "para que sirven los tests hoy en dia con ia",
        "que opinas de la ia en el desarrollo de software",
        "que es interviewtts",
        "que resultados dio el proyecto de la pagina web de velneo",
        "en que consistian tus practicas en ceesa",
        "que sabes de backend y java",
    }),
    demotions=frozenset({
        "cuentame tu trabajo como encargado en bm",
        "empezaste como frutero en mercadona no",
        "cuentame tu perfil profesional",
        "con que stack hiciste el detector de fraude",
        "como testias tu codigo",
    }),
    corpus="git show HEAD: -- 33 paginas, 116 chunks",
)

RERANK_FIGURES: Tuple[RerankFigures, ...] = (RERANK_FULL, RERANK_REDUCED)


def rerank_for(documents: Dict[str, str]) -> Optional[RerankFigures]:
    """La fila calibrada para EXACTAMENTE esta población, o ``None``.

    ``None`` es un rechazo y no un respaldo, por el mismo motivo que
    ``tests/real_wiki.py::measurement_for``: puntuar una población sin calibrar
    contra una fila existente es el error que ese módulo existe para detener.
    """
    resolved = len(resolved_cases(documents))
    for figures in RERANK_FIGURES:
        if figures.questions == resolved:
            return figures
    return None


# ── El instrumento ───────────────────────────────────────────────────────────


@dataclass
class Measured:
    """Lo que produce una medición: los rangos del gold antes y después."""

    cases: List = field(default_factory=list)
    before: List[Optional[int]] = field(default_factory=list)
    after: List[Optional[int]] = field(default_factory=list)
    sets_identical: bool = True
    hit3_before: int = 0
    hit3_after: int = 0


def _pipeline(documents: Dict[str, str]) -> RAGPipeline:
    """Pipeline con el reranker ENCENDIDO sobre un corpus dado.

    ``build_pipeline(rerank=True)`` es la vía normal, y lleva ``cache_dir=None``
    por la razón que ``tests/retrieval_measurements.py`` da: esta medición no
    escribe en ``backend/.rag_cache/``.
    """
    return build_pipeline(documents, rerank=True)


def _pipeline_with(documents: Dict[str, str], reranker: Reranker) -> RAGPipeline:
    """Pipeline con un ``Reranker`` CONCRETO, para los caminos de fallo.

    El harness rechaza un ``Reranker`` pasado por ``build_pipeline`` a propósito --
    tiene que saber qué configuración está midiendo para poder publicarla con
    nombre -- así que un doble de prueba se inyecta construyendo el pipeline
    aquí, con el mismo ``cache_dir=None`` y el mismo chunker.
    """
    from tests.real_wiki import CHUNK_OVERLAP, CHUNK_SIZE, EMBEDDING_MODEL

    pipeline = RAGPipeline(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        cache_dir=None,
        embedding_model=EMBEDDING_MODEL,
        reranker=reranker,
    )
    pipeline.ingest_documents(documents)
    return pipeline


@contextlib.contextmanager
def _no_rerank():
    """Neutraliza el reranker en la CLASE, y lo restaura pase lo que pase.

    Es el mismo mecanismo que ``tests/retrieval_measurements.served_pages`` usa
    para apagar el rescate léxico, y por el mismo motivo: es el único mango que
    un test tiene sobre un stage privado, y un reemplazo filtrado haría que todo
    test posterior del proceso midiera un pipeline sin reranker y pasara por el
    motivo equivocado.

    Hace falta porque ``retrieve()`` YA incluye el reranker. Comparar el orden
    devuelto por ``retrieve()`` contra el devuelto por ``retrieve()`` con el
    reranker apagado es la única A/B honesta: pedirle a ``_rerank`` que reordene
    una lista que ``retrieve`` ya reordenó mediría el stage aplicado dos veces, y
    un cross-encoder no es idempotente.
    """
    original = RAGPipeline.__dict__["_rerank"]
    RAGPipeline._rerank = lambda self, query, ordered, top_k: ordered
    try:
        yield
    finally:
        RAGPipeline._rerank = original


def _measure(pipeline: RAGPipeline, cases: Sequence) -> Measured:
    """Recorre las preguntas con el reranker apagado y luego encendido.

    Un solo ingest: el reranker no toca chunks ni vectores, así que el corpus es
    idéntico en las dos pasadas y la única variable es el stage. El estado del
    cross-encoder se lee al final para que un fallo de carga sea visible en el
    guard en vez de ser un ``net`` de cero sin explicación.
    """
    def ranks() -> List[Tuple[List[str], Optional[int]]]:
        out = []
        for case in cases:
            ordered = pipeline.retrieve(case.question, top_k=SHIPPED_TOP_K)
            sources = [norm(c.source) for c, _ in ordered]
            out.append((sources, best_rank(sources, relevant_pages(case))))
        return out

    with _no_rerank():
        baseline = ranks()
    reranked = ranks()

    result = Measured(cases=list(cases))
    for (before_sources, before_rank), (after_sources, after_rank) in zip(baseline, reranked):
        result.before.append(before_rank)
        result.after.append(after_rank)
        result.hit3_before += before_rank is not None
        result.hit3_after += after_rank is not None
        if set(before_sources) != set(after_sources) or len(before_sources) != len(after_sources):
            result.sets_identical = False
    return result


@pytest.fixture(scope="class")
def documents() -> Dict[str, str]:
    return load_documents()


@pytest.fixture(scope="class")
def measured(documents: Dict[str, str]) -> Measured:
    return _measure(_pipeline(documents), resolved_cases(documents))


@pytest.fixture(scope="class")
def row(documents: Dict[str, str]) -> RerankFigures:
    found = rerank_for(documents)
    assert found is not None, (
        "esta poblacion no esta calibrada para el reranker; re-meidir y anadir su "
        "fila en RERANK_FIGURES en vez de puntuar contra otra"
    )
    return found


def _split(measured: Measured) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """Las dos frozensets con nombre que definen la ganancia neta."""
    promotions = frozenset(
        case.question
        for index, case in enumerate(measured.cases)
        if measured.after[index] == 1
        and measured.before[index] is not None
        and measured.before[index] > 1
    )
    demotions = frozenset(
        case.question
        for index, case in enumerate(measured.cases)
        if measured.before[index] == 1 and measured.after[index] != 1
    )
    return promotions, demotions


# ─── La condición de aceptación ──────────────────────────────────────────────


class TestTheNetGainIsPositive:
    """La condición de aceptación de la fase 2, como aserción."""

    def test_promotions_outnumber_demotions(self, measured: Measured, row: RerankFigures):
        promotions, demotions = _split(measured)
        assert len(promotions) - len(demotions) > 0, (
            f"ganancia neta {len(promotions) - len(demotions)} "
            f"({len(promotions)} promociones, {len(demotions)} despromociones). "
            "La condicion de aceptacion de la fase 2 es neta > 0: un reranker que "
            "empata o pierde no entra.\n"
            f"  promociones: {sorted(promotions)}\n"
            f"  despromociones: {sorted(demotions)}"
        )

    def test_the_named_sets_are_the_recorded_ones(self, measured: Measured, row: RerankFigures):
        promotions, demotions = _split(measured)
        assert promotions == row.promotions, (
            "el set de promociones no es el grabado:\n"
            f"  medido:  {sorted(promotions)}\n"
            f"  grabado: {sorted(row.promotions)}"
        )
        assert demotions == row.demotions, (
            "el set de despromociones no es el grabado:\n"
            f"  medido:  {sorted(demotions)}\n"
            f"  grabado: {sorted(row.demotions)}"
        )

    def test_the_recorded_arithmetic_is_the_recorded_net(self, row: RerankFigures):
        """El número del docstring y el de los contadores no pueden divergir."""
        assert row.net == len(row.promotions) - len(row.demotions)
        assert row.at_1_after - row.at_1_before == row.net, (
            f"la fila dice net={row.net} pero recall@1 se mueve "
            f"{row.at_1_before} -> {row.at_1_after} "
            f"({row.at_1_after - row.at_1_before:+d}). Una promocion y una "
            "despromocion se compensan, asi que la diferencia de recall@1 tiene "
            "que ser IGUAL al neto, no menor."
        )

    def test_the_headline_figures_are_the_measured_ones(
        self, measured: Measured, row: RerankFigures
    ):
        assert len(measured.cases) == row.questions, (
            f"la fila describe {row.questions} preguntas y se resolvieron "
            f"{len(measured.cases)}"
        )
        assert measured.before.count(1) == row.at_1_before, (
            f"recall@1 antes: medido {measured.before.count(1)}, "
            f"grabado {row.at_1_before}"
        )
        assert measured.after.count(1) == row.at_1_after, (
            f"recall@1 despues: medido {measured.after.count(1)}, "
            f"grabado {row.at_1_after}"
        )


# ─── La garantía estructural: un rerank no cambia el conjunto ────────────────


class TestTheSetOfServedPagesIsUntouched:
    """Lo que hace que este stage sea seguro, y que ningún otro guard afirma.

    No es que ``recall@3`` no haya bajado en esta medición: es que no PUEDE bajar
    mientras el stage sea una permutación. Un guard que sólo midiera el
    ``recall@3`` de hoy dejaría pasar un stage que mañana empezara a añadir
    páginas, así que este archivo afirma la invariante y no sólo el número.
    """

    def test_the_rerank_returns_the_same_chunks_in_a_different_order(
        self, measured: Measured
    ):
        assert measured.sets_identical, (
            "el reranker devolvio un conjunto de paginas distinto del que recibio. "
            "Eso convertiria un stage de orden en uno de seleccion, y "
            "MAX_RERANK_TOP_K dejaria de ser la garantia que es."
        )

    def test_recall3_is_identical_before_and_after(self, measured: Measured):
        assert measured.hit3_before == measured.hit3_after, (
            f"recall@3 se movio con el reranker: {measured.hit3_before} -> "
            f"{measured.hit3_after}. Una permutacion no cambia un conjunto, asi que "
            "esto significa que el reranker dejo de ser una permutacion."
        )

    def test_recall3_is_still_the_published_figure(self, measured: Measured):
        """El conjunto servido no cambia, asi que la cifra publicada no puede."""
        from tests.retrieval_measurements import PRECISION_FULL

        assert measured.hit3_after == PRECISION_FULL.relevant_at_1 + len(
            PRECISION_FULL.buried
        ), (
            f"recall@3 es {measured.hit3_after} y la fila de precision publicada "
            f"implica {PRECISION_FULL.relevant_at_1 + len(PRECISION_FULL.buried)}. "
            "Un cambio aqui es un cambio de lo que el modelo LEE, no una "
            "reordenacion, y hay que publicarlo como tal."
        )


# ─── El passage: la decisión que vale nueve preguntas ─────────────────────────


class TestThePassageIsTheEmbeddedText:
    """``embedding_text`` y no ``chunk.content``, y la diferencia está medida.

    Es la decisión más fácil de revertir de todo el stage y la que más cuesta: con
    ``content`` el neto medido es **-5** (6 promociones, 11 despromociones) y con
    ``embedding_text`` es **+4** (7 y 3). Nueve preguntas de diferencia, y el signo
    se da la vuelta. Los tres módulos de este pipeline -- el embedder, BM25 y el
    cross-encoder -- leen el mismo texto, y darle al cross-encoder un cuerpo sin
    nombre de página le quita el dato que un modelo de 0.1B necesita para saber
    qué página está leyendo.
    """

    def test_the_pipeline_builds_its_passages_with_embedding_text(self):
        source = inspect.getsource(RAGPipeline._rerank)
        assert "embedding_text(chunk)" in source, (
            "RAGPipeline._rerank ya no construye el passage con embedding_text. "
            "La variante con chunk.content mide un neto de -5; con "
            "embedding_text mide +4. Re-deriva las dos antes de tocar esto."
        )

    def test_a_passage_carries_the_page_identity(self):
        """La identidad es la mitad de la señal, y se comprueba sin un modelo.

        Si ``embedding_text`` devolviera el cuerpo desnudo, el cross-encoder
        recibiría un passage sin nombre de página -- exactamente la variante que
        mide -5. Esta aserción no necesita el modelo, y por eso vale como red el
        día que el modelo no se pueda cargar.
        """
        from backend.services.rag import Chunk

        chunk = Chunk(
            id="x",
            content="Reduje la latencia un 40% con un pool de conexiones.",
            source="projects/fraud-detector.md",
            section="Resultados",
            summary="Detector de fraude en tres capas",
            h1="Detector de fraude",
        )
        text = embedding_text(chunk)
        assert text.startswith(
            "Detector de fraude. Detector de fraude en tres capas. Resultados."
        ), text
        assert "Reduje la latencia un 40%" in text


# ─── El fallo degrada, no rompe ──────────────────────────────────────────────


class TestAFailedRerankerNeverBreaksRetrieval:
    """Requisito 2 de la fase 2, y la razón por la que el stage se puede activar."""

    def test_a_reranker_that_cannot_load_degrades_to_the_dense_order(
        self, documents: Dict[str, str]
    ):
        class Exploding(Reranker):
            def ensure_loaded(self) -> bool:
                self._mode = "failed"
                self._reason = "RuntimeError: no network"
                return False

        broken = _pipeline_with(documents, Exploding(enabled=True))
        reference = build_pipeline(documents, rerank=False)
        for case in resolved_cases(documents):
            got = [norm(c.source) for c, _ in broken.retrieve(case.question, top_k=SHIPPED_TOP_K)]
            want = [norm(c.source) for c, _ in reference.retrieve(case.question, top_k=SHIPPED_TOP_K)]
            assert got == want, (
                f"un reranker que fallo cambio el orden servido de {case.question!r}. "
                "La degradacion tiene que ser exactamente el orden denso+BM25."
            )
        assert broken.rerank_mode == "failed"

    def test_a_reranker_that_raises_cannot_break_retrieval(
        self, documents: Dict[str, str]
    ):
        """Defensa en profundidad, y la razón por la que está.

        ``Reranker.rerank`` ya se traga sus propios fallos, así que esto sólo lo
        alcanza una subclase. Está porque la garantía "un fallo del reranker nunca
        rompe el retrieval" tiene que ser una propiedad de ESTE punto de llamada
        y no de la disciplina interna de otro módulo: un componente que cuesta
        90 ms, 470 MB y 1 GB de RSS puede fallar de formas que este archivo no
        puede enumerar.
        """

        class Raiser(Reranker):
            def ensure_loaded(self) -> bool:
                return True

            def rerank(self, query, passages):
                raise RuntimeError("CUDA out of memory")

        broken = _pipeline_with(documents, Raiser(enabled=True))
        reference = build_pipeline(documents, rerank=False)
        for case in resolved_cases(documents):
            got = [norm(c.source) for c, _ in broken.retrieve(case.question, top_k=SHIPPED_TOP_K)]
            want = [norm(c.source) for c, _ in reference.retrieve(case.question, top_k=SHIPPED_TOP_K)]
            assert got == want, (
                f"una excepcion del reranker rompio el retrieval de {case.question!r}"
            )

    def test_a_predict_failure_is_recorded_and_not_retried(self, documents: Dict[str, str]):
        """Un ``predict`` que lanza deja el reranker en ``failed`` y no reintenta.

        Reintentar en cada consulta convertiría un modelo que no cabe en esta
        máquina en una espera de 90 ms por pregunta, que es peor que la
        degradación que evita.
        """
        reranker = Reranker(enabled=True)
        pipeline = _pipeline_with(documents, reranker)

        # Force the failure path without a 470 MB model: stub the loaded model.
        reranker._model = object()
        pipeline.retrieve(resolved_cases(documents)[0].question, top_k=SHIPPED_TOP_K)
        assert reranker.mode == "failed", reranker.mode
        assert reranker.reason and "predict failed" in reranker.reason, reranker.reason
        assert reranker.stats()["degraded"] >= 1

    def test_a_disabled_reranker_downloads_nothing(self, documents: Dict[str, str]):
        pipeline = build_pipeline(documents, rerank=False)
        assert pipeline.rerank_mode == "disabled"
        assert pipeline._reranker.stats()["loads"] == 0, (
            "un despliegue con RERANK_ENABLED=false no debe descargar 470 MB"
        )


# ─── El gate de anchura ──────────────────────────────────────────────────────


class TestTheWidthGate:
    """``MAX_RERANK_TOP_K`` existe para no reordenar lo que nadie lee."""

    def test_population_width_is_not_reranked(self, documents: Dict[str, str]):
        """A ``top_k`` de población no actúa, y las cifras publicadas no se mueven.

        Las figuras de ``tests/real_wiki.py`` se miden a ``top_k`` = tamaño de la
        población. Si el stage las afectara, tres cifras publicadas describirían una
        reconfiguración que el producto no sirve. Afirmado para que subir el gate
        sea una decisión visible y no un efecto colateral.
        """
        pipeline = _pipeline(documents)
        wide = len(resolved_cases(documents))
        for case in resolved_cases(documents):
            ordered = pipeline.retrieve(case.question, top_k=wide)
            again = pipeline._rerank(case.question, ordered, wide)
            assert [norm(c.source) for c, _ in again] == [
                norm(c.source) for c, _ in ordered
            ], f"el reranker reordeno una lista de {wide} slots"

    def test_the_gate_covers_every_production_caller(self):
        """El panel de contexto pide 2 y la respuesta pide 3: los dos caben."""
        from backend.config import config

        assert RAGPipeline.MAX_RERANK_TOP_K >= config.RAG_TOP_K, (
            f"el gate es {RAGPipeline.MAX_RERANK_TOP_K} y la respuesta pide "
            f"{config.RAG_TOP_K}: el ancho de produccion quedaria fuera del rerank"
        )
        assert RAGPipeline.MAX_RERANK_TOP_K >= 2, "el panel de contexto pide 2 slots"


# ─── La caché ────────────────────────────────────────────────────────────────


class TestTheRerankDoesNotDependOnTheCache:
    """El invariante que responde a "¿una caché que no sabe del reranker sirve viejo?".

    Lo que esta versión hace es lo que hace ``LEXICAL_RESCUE_VERSION``: versionar
    la DECISIÓN (``RERANK_VERSION``) sin tocar la identidad de la caché de
    embeddings, porque el reranker no cambia ni un vector ni un chunk. Lo que hay
    que poder afirmar entonces es que una run restaurada de caché y una run que
    recalcula dan el MISMO texto de passage -- y por tanto los mismos logits --
    para el mismo corpus.
    """

    def test_the_passage_text_is_persisted_by_the_embedding_cache(self):
        source = inspect.getsource(RAGPipeline._save_embeddings_cache)
        for name in ("contents", "h1s", "summaries", "sections"):
            assert name in source, (
                f"la cache de embeddings ya no persiste {name}, y embedding_text() "
                "es una funcion de el. Sin ese campo, una run restaurada de cache "
                "daria passages distintos de los de una run que recalcula, y el "
                "reranker no seria una funcion del corpus."
            )

    def test_the_decision_is_versioned_and_separate_from_the_chunking(self):
        from backend.services.rag import LEXICAL_RESCUE_VERSION

        assert RERANK_VERSION, "una decision de retrieval sin version no se audita"
        assert RERANK_VERSION != CHUNK_FILTER_VERSION, (
            "RERANK_VERSION no puede ser CHUNK_FILTER_VERSION: uno versiona una "
            "permutacion y el otro una semantica de chunking, y confundirlos "
            "haria que un cambio de orden invalidara una cache de vectores "
            "perfectamente correcta."
        )
        assert LEXICAL_RESCUE_VERSION, (
            "el precedente que este modulo sigue: una decision de retrieval se "
            "versiona aparte"
        )

    def test_a_cached_run_and_a_recomputed_run_produce_the_same_passage(
        self, documents: Dict[str, str]
    ):
        """El invariante, medido: dos ingest, un caché y otro sin él.

        Se escribe una cache real a un temporal, se restaura en un pipeline nuevo
        y se compara el texto de passage chunk a chunk con el de un pipeline que
        recalcula desde cero. Si difieren, la cache le estaria dando al reranker
        otro corpus que el que se esta recoveriendo.
        """
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            writer = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir)
            writer.ingest_documents(documents)
            assert (cache_dir / "embeddings.npz").exists(), (
                "no se escribio cache; el invariante no se puede comprobar"
            )

            restored = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir)
            restored.ingest_documents(documents)
            recomputed = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=None)
            recomputed.ingest_documents(documents)

            assert len(restored.chunks) == len(recomputed.chunks)
            for cached, fresh in zip(restored.chunks, recomputed.chunks):
                assert embedding_text(cached) == embedding_text(fresh), (
                    f"el passage difiere entre una run restaurada de cache y una "
                    f"que recalcula, para el chunk {cached.id}"
                )


# ─── La versión se publica ───────────────────────────────────────────────────


class TestTheDecisionIsObservable:
    def test_the_identity_names_the_model_and_the_version(self):
        reranker = Reranker(enabled=True, model_name="cross-encoder/anything")
        assert "cross-encoder/anything" in reranker.identity
        assert f"v{RERANK_VERSION}" in reranker.identity

    def test_the_four_states_are_distinguishable(self):
        assert Reranker(enabled=False).mode == "disabled"
        assert Reranker(enabled=True).mode == "uninitialized", (
            "uninitialized tiene que ser la respuesta ANTES de cargar, para que "
            "preguntar antes no pueda producir 'loaded' -- una afirmacion sobre "
            "un modelo que no se ha cargado."
        )

    def test_the_pipeline_reports_the_reranker_separately_from_the_embedder(
        self, documents: Dict[str, str]
    ):
        """Dos fallos independientes necesitan dos propiedades, no una cadena.

        Un TF-IDF con el cross-encoder cargado y un embedder multilingue con el
        cross-encoder muerto son despliegues distintos con arreglo distinto, y una
        sola cadena no puede decir cual es cual.
        """
        pipeline = _pipeline(documents)
        assert pipeline.mode == "embeddings"
        assert pipeline.rerank_mode in {"loaded", "uninitialized", "failed"}