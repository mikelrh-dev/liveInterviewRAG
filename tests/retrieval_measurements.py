"""precision@k y latencia del retrieval sobre el set etiquetado, por población.

QUÉ HUECO CIERRA ESTE MÓDULO
----------------------------
El harness medía recall@1, recall@3 y MRR@5, y nada más. Dos huecos concretos:

    * Ninguna medida de PRECISIÓN, en ningún sitio. Recall dice si la respuesta
      está en el top-k; no dice cuántas de las páginas que el modelo lee SON la
      respuesta. Con ``RAG_TOP_K`` en 3 (``backend/config.py:181``) el modelo
      lee tres páginas y sólo una lo contesta, así que la pregunta de la fase 2
      ("¿el top-3 trae ruido junto al acierto?") no tenía ni una cifra que la
      respondiera. Era una decisión de arquitectura tomándose a ciegas.
    * Ninguna medida de LATENCIA sobre el corpus real. El único test de
      rendimiento era ``tests/test_rag.py:206-227``, y media diez documentos
      inventados con un techo de 500 ms de MEDIA. No dice nada de los 124 chunks
      que el producto sirve, ni de la mediana, que es lo que nota quien pregunta.

POR QUÉ ES UN MÓDULO Y NO UN COMENTARIO
---------------------------------------
Por el mismo motivo que ``tests/lexical_rescue_losses.py``: una cifra que sólo
existe en prosa no la puede comprobar nadie. Lo que un reranker necesita no es
"la precisión es alta", es un número por población y una lista con nombre de las
preguntas que lo hunden. Las dos cosas viven aquí, en un sitio que un guard puede
leer, y las dos son MEDIDAS: volver a derivarlas es el trabajo, no editarlas.

QUÉ ES PRECISIÓN AQUÍ, Y POR QUÉ NO ES LA DE UN LIBRO DE TEXTO
-------------------------------------------------------------
``precision@k`` es una fracción de SLOTS, no de preguntas: de los ``3 x n``
resultados que ``retrieve()`` devuelve, cuántos son la página que responde a ESA
pregunta. Relevante es ``primary`` junto a ``also``
(``tests/real_wiki.py:167-178``), que es el conjunto que la propia etiqueta
declara aceptable.

Eso tiene dos consecuencias que hay que decir antes de mirar ninguna cifra:

    * Una pregunta con una sola página aceptable NO PUEDE puntuar más de 1/3 en
      ``precision@3``. Con 37 páginas y una respuesta por pregunta el techo
      teórico son 50 de 147 slots, no 147. Por eso el módulo publica
      ``possible_slots`` y la fracción ``share_of_ceiling``: leída sin ese
      denominador, ``precision@3 = 0.3061`` parece una tercera parte de lo que
      es, cuando es nueve décimos.
    * ``precision@1`` sale numéricamente IGUAL a ``recall@1`` en las dos
      poblaciones, y no por casualidad: 48 de las 49 etiquetas no tienen página
      ``also``, y la única que sí la tiene no la tenía en el rank 1. No es una
      coincidencia que un guard pueda dar por buena sin más; el guard afirma la
      premisa.

Y lo que la métrica NO mide, que es lo que la hace legible: en el corpus real
las 147 páginas servidas por las 49 preguntas son TODAS la respuesta de alguna
pregunta. No hay páginas inútiles circulando; lo que hay son páginas útiles para
otra pregunta. Así que "precisión" aquí significa "relevante PARA ESTA
PREGUNTA", y ninguna reformulación de la métrica lo cambia: es una medida
condicional por pregunta, y por eso los nombres de las preguntas hunden la cifra
más que cualquier agregado.

EL TECHO DE LATENCIA, Y POR QUÉ MAX NO TIENE NINGUNO
-----------------------------------------------------
Las tres estadísticas salen de UNA pasada por las preguntas sin calentar nada,
que es la forma de producción: el primer ``retrieve()`` tras el ingest paga el
ajuste perezoso del índice BM25 y el primer ``encode()`` del proceso. Medido en
la máquina donde se calibró esto, sobre el corpus de 124 chunks:

    mediana 21.9-23.9 ms    p95 24.2-33.8 ms    peor llamada 59.8 ms

(Esas son las cifras de ANTES del cross-encoder. Con el reranker de la fase 2 la
mediana es ~196 ms y el p95 ~413 ms; ver ``LATENCY_FULL`` y el comentario que
tienen encima, que explica el coste y por qué se publica en vez de tragarse.)

La mediana es ESTABLE (cuatro pasadas seguidas: 22.18 / 22.10 / 22.61 / 22.29
ms) y el máximo no lo es: sobre unas 1000 llamadas el peor máximo observado fue
172.7 ms, casi ocho veces la mediana, por preempción del planificador y no por
trabajo de retrieval. Un techo sobre el máximo sería o inútil (500 ms no
dispara nunca) o intermitente (una máquina tres veces más lenta se acerca y
falla por ruido). Así que el máximo se PUBLICA y no se afirma, y las dos
estadísticas que sostienen el guard son la mediana y el p95, con techos derivados
de la medición por un multiplicador con nombre.

Las filas guardan la MÁS LENTA de las pasadas observadas, no la más rápida: un
techo elegido sobre una ejecución afortunada es un techo que se dispara solo.

LAS DOS POBLACIONES, Y POR QUÉ CADA FIGURA SE MIDE EN SU CORPUS
--------------------------------------------------------------
Igual que en ``tests/real_wiki.py``: ``full`` ES el working tree tal como está
(37 páginas, 49 preguntas, 124 chunks) y ``reduced`` es lo que produce
``git show HEAD:``, o sea lo que le llega a cualquiera que clonee. Desde el
commit ``efda998`` las dos tienen las MISMAS 37 páginas, las mismas 49 preguntas
y los mismos 124 chunks, y siguen siendo dos corpus distintos porque quince
``wiki/*.md`` están modificados en el working tree y sin commitear: el texto no
es el mismo, y por tanto el ranking tampoco. Lo medido sobre los dos, mismo día
y mismo arnés: la fila ``reduced`` da 46 slots relevantes en el top-3 donde la
``full`` da 45, entierra 5 preguntas donde la otra entierra 4, y el rescate
léxico no le cuesta ninguna pregunta donde le cuesta una.

La fila ``reduced`` se lee SIEMPRE de ``git show HEAD:`` y por tanto es medible
en cualquier checkout. La fila ``full`` sólo se mide cuando ESTE checkout es el
working tree que la midió, y eso ya no se deduce de cuántas etiquetas resuelve:
``precision_for`` y ``latency_for`` la buscan por el DIGEST del corpus servido
(``tests/real_wiki.py::CORPUS_DIGESTS``), porque con las dos poblaciones en 49
preguntas un contador no las distingue y devolvía ``full`` para el corpus
commiteado sin decir nada. Un ``None`` aquí es un RECHAZO y no un respaldo: ver
``documents_for``, ``precision_for`` y ``latency_for``.

QUÉ SE FIJA Y QUÉ NO
--------------------
Fijado: los contadores de slots y de preguntas medidos en cada población, la
identidad con nombre de las preguntas cuya página dorada está en rank 2-3 (el
margen real de un reranker) y de las que no están en el top-3 (las que un
reranker sobre los MISMOS tres slots no puede recuperar), y los techos de
latencia.

No fijado: que la precisión sea alta. No lo es, y publicarlo como si lo fuera
sería el mismo defecto que este repositorio ya cometió dos veces con las
unidades. Lo que sí se fija es el suelo: ``TOLERATED_SLOTS`` por debajo de la
medición. Y ``served_pages`` y ``relevant_pages`` son definiciones, no entradas,
para que un cambio en el corpus no pueda cambiar la métrica sin que se note.

Medido el 2026-10-04 sobre la rama con el rescate léxico BM25
(``backend/services/rag.py``, ``LEXICAL_RESCUE_VERSION = "1"``), producción
(``cache_dir=None``, ver más abajo), ``top_k`` = 3, embedder real
``paraphrase-multilingual-MiniLM-L12-v2``.

POR QUÉ ``cache_dir`` SIGUE SIENDO ``None``
-------------------------------------------
``tests/real_wiki.py:150-161`` decidió que este harness no sea un segundo
escritor de ``backend/.rag_cache/``, que la app reescribe al arrancar, y esa
decisión no se toca aquí. La consecuencia es que cada pasada vuelve a embeber
los ~120 chunks (9-17 s) y no se puede reutilizar el cache de vectores entre
ejecuciones: el coste de este módulo es el precio de no escribir en producción.
Lo que sí se reutiliza entre ejecuciones es el checkpoint del modelo, que vive
en el cache de HuggingFace, y el cache de páginas del sistema operativo; por eso
el ingest son 9-17 s y no el minuto largo del primer download.
"""

from __future__ import annotations

import math
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from tests.real_wiki import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBEDDING_MODEL,
    LABELLED_CASES,
    REPO_ROOT,
    Case,
    corpus_for,
    load_documents,
    resolved_cases,
)

#: El ``top_k`` que la producción pide (``backend/config.py``, ``RAG_TOP_K``, que
#: embarca 3). Todo lo de este módulo se mide a este ancho y no al ancho de la
#: población, porque el ancho de la población responde a "¿está la página en el
#: ranking?" y esta fase necesita la pregunta de "¿qué lee el modelo?".
SHIPPED_TOP_K = 3

#: La única etiqueta con una segunda página aceptable, y la razón por la que
#: ``precision@1`` sale igual a ``recall@1`` en las dos poblaciones.
#:
#: Dos páginas responden de verdad a por qué dejó retail para DAM: la FAQ y el
#: registro de decisión. El conjunto ``also`` de las otras 48 etiquetas está
#: vacío, así que "relevante" y "la página dorada" son el mismo conjunto en 48 de
#: 49 casos, y en el 49 la página ``also`` no está en el rank 1. Por eso
#: ``precision@1`` coincide con el ``recall@1`` publicado: no es que las dos
#: métricas sean la misma, es que la etiqueta no las distingue.
DUPLICATED_QUESTION = "por que dejaste los supermercados para estudiar dam"


def norm(source: str) -> str:
    """Las claves de documento son ``str(Path.relative_to(...))``, y el separador varía."""
    return source.replace("\\", "/")


def relevant_pages(case: Case) -> FrozenSet[str]:
    """Las páginas que la etiqueta dice que contestan ``case``: ``primary`` y ``also``.

    ``primary`` sola es la regla ESTRICTA que usa el resto del harness
    (``tests/test_recall_claims.py``, ``tests/test_lexical_rescue.py``) y sirve
    para un recall: está la página que el autor quería. Aquí hace falta la regla
    LENIENTE, porque una pregunta con dos páginas aceptables no debe contar como
    error si el retrieval devuelve la otra. La diferencia es de una pregunta en
    49, y esa una está nombrada en ``DUPLICATED_QUESTION`` en lugar de quedar
    implícita.
    """
    return frozenset({case.primary}) | case.also


def duplicated_cases() -> Tuple[Case, ...]:
    """Las etiquetas con más de una página aceptable, en el orden del set."""
    return tuple(case for case in LABELLED_CASES if case.also)


def possible_relevant_slots(cases: Sequence[Case], top_k: int = SHIPPED_TOP_K) -> int:
    """Cuántos slots relevantes podría servir un retrieval PERFECTO, desde las etiquetas.

    Derivado de las etiquetas y NO medido, que es lo que lo hace útil como
    denominador: ``sum(min(top_k, |relevante|))``. En la población ``full`` son
    50 (48 preguntas con una página aceptable y una con dos) sobre 147 slots
    servidos. Así que ``precision@3 = 0.3061`` no es un tercio del techo: es 45 de
    los 50 slots que un retrieval perfecto habría ocupado.
    """
    return sum(min(top_k, len(relevant_pages(case))) for case in cases)


# ── El instrumento ───────────────────────────────────────────────────────────


def served_pages(
    pipeline, question: str, top_k: int = SHIPPED_TOP_K, rescue: bool = True
) -> List[str]:
    """Las páginas que ``retrieve()`` devuelve, con el rescate léxico on/off.

    Se apaga reemplazando el método en la CLASE, que es el único mango que un
    test tiene sobre él, y se restaura en un ``finally`` porque un reemplazo
    filtrado haría que todo test posterior del proceso midiera un pipeline sin
    rescate y pasara por el motivo equivocado. Copiado de
    ``tests/test_lexical_rescue.py:162-176`` en vez de importado de ahí, con el
    mismo argumento que ese archivo da para no importar un nombre privado.
    """
    from backend.services.rag import RAGPipeline

    original = RAGPipeline.__dict__["_lexical_rescue"]
    if not rescue:
        RAGPipeline._lexical_rescue = lambda self, q, d, k: d
    try:
        return [
            norm(chunk.source) for chunk, _ in pipeline.retrieve(question, top_k=top_k)
        ]
    finally:
        RAGPipeline._lexical_rescue = original


def best_rank(sources: Sequence[str], gold: FrozenSet[str]) -> Optional[int]:
    """El rank 1-based de la PRIMERA página relevante, o ``None`` si no hay ninguna.

    ``None`` y no 0 porque 0 no es un rank: ``None`` ordena peor que cualquier
    rank real, que es justo lo que significa "no la trajo".
    """
    ranks = [index + 1 for index, source in enumerate(sources) if source in gold]
    return min(ranks) if ranks else None


def percentile(values: Sequence[float], fraction: float = 0.95) -> float:
    """El percentil ``fraction`` por RANGO MÁS CERCANO: ``sorted(v)[ceil(f*n)-1]``.

    La misma definición que ``tests/real_wiki.py:662`` usa para la forma de las
    palabras, y por el mismo motivo de estar nombrada: la interpolación lineal es
    la otra convención común y las dos no coinciden, así que un p95 derivado con
    la convención equivocada no es un p95 un poco distinto sino otra métrica.
    """
    ordered = sorted(values)
    assert ordered, "un percentil sobre un conjunto vacío no es una medición"
    return float(ordered[math.ceil(fraction * len(ordered)) - 1])


def build_pipeline(documents: Dict[str, str], rerank: bool = True, **kwargs):
    """``RAGPipeline`` sobre un corpus dado, sin escribir cache en ninguna parte.

    ``cache_dir=None`` no es una omisión: es ``tests/real_wiki.py:150-161``, que
    lo fijó para que este harness no sea un segundo escritor de
    ``backend/.rag_cache/``. El guard lo afirma, porque un ``cache_dir`` pasado
    por aquí convertiría una medición en una escritura sobre el artefacto que la
    app usa en producción.

    ``rerank=True`` POR DEFECTO, y ése es el punto. Un harness que mide la
    configuración de producción tiene que medir la configuración de producción:
    ``RAGPipeline`` sin reranker construye uno DESHABILITADO (ver su
    ``__init__``), así que medir por aquí sin pedirlo explícitamente publicaba
    las cifras de un pipeline que la app ya no arranca -- la forma exacta del
    error que este módulo y ``tests/real_wiki.py`` existen para impedir, y que
    sólo se ve cuando alguien lee la fila y la compara con ``backend/main.py``.

    ``rerank=False`` existe para medir el otro lado, que es lo que hace
    ``tests/test_rerank.py`` para el antes y el después.
    """
    from backend.services.rag import RAGPipeline
    from backend.services.rerank import Reranker

    if "cache_dir" in kwargs:
        raise AssertionError(
            "build_pipeline() fija cache_dir=None porque este harness no debe "
            "escribir en backend/.rag_cache/. Si necesitas una caché real, "
            "construye el RAGPipeline tú mismo (ver test_rerank.py)."
        )
    if "reranker" in kwargs:
        raise AssertionError(
            "pasa rerank=True/False, no un Reranker: este harness tiene que saber "
            "qué configuración está midiendo para poder publicarla con nombre."
        )

    rag = RAGPipeline(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        cache_dir=None,
        embedding_model=EMBEDDING_MODEL,
        reranker=Reranker(enabled=rerank),
        **kwargs,
    )
    rag.ingest_documents(documents)
    return rag


# ── El corpus de cada población ──────────────────────────────────────────────


def _git(*args: str) -> bytes:
    """``git`` en este repositorio, para el índice y para los blobs commiteados."""
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout


def write_committed_wiki(root: Path) -> Path:
    """Escribe bajo ``root/wiki`` lo que ``git clone`` serviría, y devuelve ``root``.

    Dos comandos, y la diferencia entre ellos es el punto:

    ``git ls-files -- wiki``
        El ÍNDICE. Las cincuenta páginas del wiki salen todas, incluidas las
        cuatro FAQ que antes eran la definición de la población ``reduced`` y que
        ``efda998`` commiteó; hoy el índice ya no es lo que distingue un corpus
        del otro.
    ``git show HEAD:<path>``
        El blob COMMITEADO. Los 15 ``wiki/*.md`` modificados sin commitear se leen
        aquí en su contenido commiteado, para que un rewrite local del corpus no
        pueda mover una cifra publicada. ESTE comando es el que sigue definiendo
        la población ``reduced``: mismo conjunto de páginas, otro texto.

    Mismo mecanismo y mismo motivo que ``tests/test_committed_corpus_figures.py``,
    reimplementado aquí en vez de importado porque un fixture de otro módulo de
    test no se comparte entre módulos, y una copia de doce líneas se puede leer.
    """
    for raw in _git("ls-files", "-z", "--", "wiki").split(b"\0"):
        if not raw.endswith(b".md"):
            continue
        relative = raw.decode("utf-8")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_git("show", f"HEAD:{relative}"))
    return root


def _load_with_production_loader(root: Path) -> Dict[str, str]:
    """El corpus de ``root`` leído por el loader de PRODUCCIÓN.

    No un ``rglob`` hecho a mano: ``_SKIP_FILES`` y ``_SKIP_DIRS`` son los del
    loader real, así que ``index.md``, ``README.md``, ``CONVENCIONES.md`` y
    ``templates/`` caen aquí por la misma razón que caen en producción.
    """
    from backend.services.candidate import CandidateProfile

    profile = CandidateProfile(root, wiki_dir=root / "wiki")
    profile.load()
    assert profile.documents, (
        f"el wiki commiteado reconstruido en {root} no cargó nada. Si esto falla, "
        "`git ls-files wiki` está vacío aquí, no es que el corpus se haya borrado."
    )
    return profile.documents


def documents_for(population: str, root: Path) -> Optional[Dict[str, str]]:
    """El corpus que la población ``population`` NOMBRA, o ``None`` si no está aquí.

    ``None`` es un rechazo, y el criterio del rechazo es el DIGEST del corpus
    servido, no su número de etiquetas. Se usó el número hasta que ``efda998``
    dejó las dos poblaciones en 49 preguntas, y a partir de ahí cualquier fila
    ``full`` habría podido medirse sobre el corpus commiteado sin que nada lo
    dijera: el nombre de una población puesto a la medición de la otra, que es
    el defecto que ``tests/real_wiki.py`` existe para impedir.
    """
    if population == "full":
        documents = load_documents()
        return documents if corpus_for(documents) == "full" else None
    if population == "reduced":
        documents = _load_with_production_loader(write_committed_wiki(root))
        return documents if corpus_for(documents) == "reduced" else None
    raise AssertionError(
        f"población desconocida {population!r}. Las calibradas están en "
        "PRECISION_FIGURES y LATENCY_FIGURES; una tercera exige volver a medirla "
        "y añadir su fila, no apuntar una fila existente a otra población."
    )


# ── Las mediciones ───────────────────────────────────────────────────────────


def measure_precision(
    pipeline, cases: Sequence[Case], top_k: int = SHIPPED_TOP_K
) -> Dict[str, object]:
    """Los contadores de ``precision@k`` a través de ``retrieve()``, on y off.

    Se lee por la ruta pública y no por un producto punto local: un guard que
    reimplementa el scoring se está testando a sí mismo. Dos configuraciones
    porque la comparación ES el dato: la producción corre con el rescate, y lo
    que la fase 2 necesita saber es si el rescate léxico mete ruido en el
    contexto además de traer la respuesta.
    """
    at_1 = 0
    slots_on = slots_off = 0
    buried: List[str] = []
    absent: List[str] = []
    buried_off: List[str] = []
    absent_off: List[str] = []

    for case in cases:
        gold = relevant_pages(case)
        on = served_pages(pipeline, case.question, top_k, rescue=True)
        off = served_pages(pipeline, case.question, top_k, rescue=False)

        at_1 += bool(on) and on[0] in gold
        slots_on += sum(1 for source in on if source in gold)
        slots_off += sum(1 for source in off if source in gold)

        rank = best_rank(on, gold)
        if rank is None:
            absent.append(case.question)
        elif rank > 1:
            buried.append(case.question)

        rank_off = best_rank(off, gold)
        if rank_off is None:
            absent_off.append(case.question)
        elif rank_off > 1:
            buried_off.append(case.question)

    return {
        "relevant_at_1": at_1,
        "relevant_slots_at_3": slots_on,
        "relevant_slots_at_3_dense": slots_off,
        "buried": frozenset(buried),
        "absent": frozenset(absent),
        "buried_dense": frozenset(buried_off),
        "absent_dense": frozenset(absent_off),
    }


def measure_latency(
    pipeline, cases: Sequence[Case], top_k: int = SHIPPED_TOP_K
) -> List[float]:
    """El coste por llamada de ``retrieve()``, en ms, UNA pasada y sin calentar.

    Sin warming deliberado: la producción tampoco calienta, así que el primer
    ``retrieve()`` tras el ingest paga el ajuste perezoso del índice BM25 y el
    primer ``encode()`` del proceso. Excluirlo daría una mediana más bonita y
    falsa; incluirlo es lo que hace que el ``max`` sea el ``max`` de producción.

    ``perf_counter`` y no ``time.time``: es monótono, así que un ajuste del reloj
    del sistema durante la pasada no puede fabricar una latencia negativa.
    """
    timings: List[float] = []
    for case in cases:
        started = time.perf_counter()
        pipeline.retrieve(case.question, top_k=top_k)
        timings.append((time.perf_counter() - started) * 1000)
    return timings


def latency_summary(timings: Sequence[float]) -> Dict[str, float]:
    """mediana / p95 / max de una pasada, con el p95 por RANGO MÁS CERCANO."""
    return {
        "median_ms": statistics.median(timings),
        "p95_ms": percentile(timings),
        "max_ms": max(timings),
    }


# ── La tolerancia, y por qué no es un número redondo ─────────────────────────

#: Cuántos slots de precisión tolera un suelo, y POR QUÉ ese número.
#:
#: Un slot es 1/147 = 0.0068 de ``precision@3`` en la población ``full``. La
#: unidad mínima de movimiento es UNA pregunta: si su página dorada sale del
#: top-3 o entra en él, el contador se mueve un slot. Dos preguntas son dos
#: slots, y el barrido de chunk size ya registró que UNA pregunta movía el recall
#: 0.034 sobre un set de 29, así que por debajo de tres slots lo que hay es
#: temblor, no resultado.
#:
#: Tres slots son 0.0204, la misma escala ABSOLUTA que el 2/49 = 0.0408 del suelo
#: de recall pero con la mitad de ancho, y con la misma idea: el suelo se escribe
#: como expresión para que nadie pueda subir una medición sin decidir antes qué
#: tolerancia está aceptando.
TOLERATED_SLOTS = 3

#: Multiplicador del techo de mediana. Medido: la mediana de la pasada se movió
#: entre 21.9 y 23.9 ms en cuatro pasadas seguidas (una banda del 9%), y es una
#: embedding corta en CPU. 4x absorbe un runner hasta cuatro veces más lento que
#: la máquina donde se calibró, que es de lo que CI suele estar; 2x no lo
#: absorbería y 10x dejaría de ser un techo.
MEDIAN_HEADROOM = 4.0

#: Multiplicador del techo de p95. Es el percentil 47 de 49, o sea que está a un
#: outlier del máximo: el mismo pico que produjo un máximo de 172.7 ms lo traería
#: aquí arriba. Medido: 24.2 a 33.8 ms entre pasadas (una banda del 40%), así que
#: 4x, el de la mediana, quedaría por debajo del doble del peor caso observado.
#: 6x deja esa margen y sigue siendo la mitad de lo que costaría afirmar sobre el
#: máximo.
P95_HEADROOM = 6.0


# ── Las filas ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PrecisionFigures:
    """La precisión de una población, medida y con su suelo derivado.

    ``*_at_3`` son CONTADORES de slots y ``served_slots`` es el denominador que
    los convierte en fracción: son cosas distintas y confundirlas es la clase de
    error que este repositorio ya cometió una vez con el filtro del coseno
    ("956 de 6125 pares": un numerador deduplicado sobre un denominador de
    celdas crudas, un ratio que no era un porcentaje de nada).
    ``possible_slots`` es el techo, y sale de las etiquetas, no de una ejecución.
    """

    population: str
    pages: int
    chunks: int
    questions: int
    relevant_at_1: int
    relevant_slots_at_3: int
    relevant_slots_at_3_dense: int
    possible_slots: int
    buried: FrozenSet[str]
    absent: FrozenSet[str]
    corpus: str

    @property
    def served_slots(self) -> int:
        """Los slots que ``retrieve()`` devolvió: ``top_k`` por preguntas."""
        return SHIPPED_TOP_K * self.questions

    @property
    def precision1(self) -> float:
        return self.relevant_at_1 / self.questions

    @property
    def precision3(self) -> float:
        return self.relevant_slots_at_3 / self.served_slots

    @property
    def precision3_dense(self) -> float:
        """Lo mismo con el rescate léxico forzado a declinar (preproducción)."""
        return self.relevant_slots_at_3_dense / self.served_slots

    @property
    def precision1_floor(self) -> float:
        return (self.relevant_at_1 - TOLERATED_SLOTS) / self.questions

    @property
    def precision3_floor(self) -> float:
        return (self.relevant_slots_at_3 - TOLERATED_SLOTS) / self.served_slots

    @property
    def precision3_dense_floor(self) -> float:
        return (
            self.relevant_slots_at_3_dense - TOLERATED_SLOTS
        ) / self.served_slots

    @property
    def share_of_ceiling(self) -> float:
        """``precision@3`` contra el techo que las etiquetas permiten.

        La cifra que hace legible la anterior. 45 de 50 slots posibles es 0.90;
        ``0.3061`` sin este denominador se lee como una tercera parte y no lo es.
        """
        return self.relevant_slots_at_3 / self.possible_slots


PRECISION_FULL = PrecisionFigures(
    population="full",
    pages=37,
    chunks=124,
    questions=49,
    # 36 -> 40 with the cross-encoder re-ranking the top-3. `relevant_slots_at_3`
    # below is UNCHANGED at 45, and that is the whole safety argument: a re-rank
    # is a permutation of the same three slots, so it cannot change the set and
    # cannot move a slot counter. What it moves is WHICH of those three the model
    # reads first, and that is `relevant_at_1`. The seven promotions and three
    # demotions are named in `tests/test_rerank.py::RERANK_FULL`, and the net
    # (> 0) is the phase-2 acceptance condition, asserted there.
    relevant_at_1=40,
    relevant_slots_at_3=45,
    relevant_slots_at_3_dense=41,
    possible_slots=50,
    buried=frozenset({
        # Four, down from eight. The composition changed and it is worth being
        # exact about how, because "fewer buried" is not the same as "better":
        #   * SEVEN of the previous eight were PROMOTED to rank 1 by the
        #     cross-encoder -- the margin a re-ranker exists to exploit.
        #   * THREE questions that were at rank 1 were DEMOTED into this set.
        #     Those three are the price, they are named in `tests/test_rerank.py`,
        #     and they are why the net (+4) is smaller than the promotions (7).
        # ``cuentame tu perfil profesional`` is the one that was buried before
        # and still is: the re-ranker did not rescue it either way.
        "como testias tu codigo",
        "con que stack hiciste el detector de fraude",
        "cuentame tu perfil profesional",
        "empezaste como frutero en mercadona no",
    }),
    absent=frozenset({
        # UNCHANGED, five questions, and that is the ceiling of this technique on
        # this corpus. There is no gold page in the three slots at all, so no
        # REORDERING of those three can reach it -- the page is not in the list.
        # Three of the five are below the cosine threshold and are not even
        # candidates (skills/backend.md scores 0.1703 against 0.25), which makes
        # them a CANDIDATE-SET problem and not an ordering one. Closing this gap
        # needs a different lever, not a better ranker.
        "cuando podrias incorporarte al puesto",
        "que planes tienes para los proximos años",
        "que estabas haciendo en mercadona los ultimos años",
        "para que sirven los tests hoy en dia con ia",
        "que hiciste con fastapi docker y asincronia en dam",
    }),
    corpus="working tree del autor -- 37 páginas, 124 chunks",
)

PRECISION_REDUCED = PrecisionFigures(
    population="reduced",
    pages=37,
    chunks=124,
    questions=49,
    # 37 -> 40 with the cross-encoder, and `relevant_slots_at_3` stays at 46.
    # The net here is +3 against +4 on the full population, over eight promotions
    # and five demotions instead of seven and three: the committed corpus ranks
    # the same 124 chunks differently because fifteen of the pages are not the
    # fifteen the author has modified. Two populations measured separately is the
    # reason this module exists; copying the full row's numbers here would be
    # inventing the reduced one.
    relevant_at_1=40,
    relevant_slots_at_3=46,
    relevant_slots_at_3_dense=41,
    possible_slots=50,
    buried=frozenset({
        # Five, and NOT the full population's four. Same page count, same chunk
        # count, same question count, one more buried question -- which is the
        # whole argument for keying these rows on a corpus digest and not on how
        # many labels resolve. A named set cannot be copied between populations
        # and it cannot be derived from the other population's either.
        "como testias tu codigo",
        "con que stack hiciste el detector de fraude",
        "cuentame tu perfil profesional",
        "cuentame tu trabajo como encargado en bm",
        "empezaste como frutero en mercadona no",
    }),
    absent=frozenset({
        # Four, one fewer than ``full``, and the difference is
        # ``para que sirven los tests hoy en dia con ia``: its page reaches the
        # top-3 from the committed text. A set of four against a set of five, on
        # the same 49 questions, is the difference a count cannot see.
        "cuando podrias incorporarte al puesto",
        "que estabas haciendo en mercadona los ultimos años",
        "que planes tienes para los proximos años",
        "que hiciste con fastapi docker y asincronia en dam",
    }),
    corpus="git show HEAD: -- 37 páginas, 124 chunks",
)

PRECISION_FIGURES: Tuple[PrecisionFigures, ...] = (PRECISION_FULL, PRECISION_REDUCED)


@dataclass(frozen=True)
class LatencyFigures:
    """Latencia de ``retrieve()`` sobre el corpus real, y el techo derivado.

    Las tres estadísticas son de UNA pasada por las preguntas con la producción
    encendida (rescate incluido) y sin calentar. Los valores grabados son la MÁS
    LENTA de las pasadas observadas, no la más rápida; ver el docstring del
    módulo.
    """

    population: str
    chunks: int
    questions: int
    median_ms: float
    p95_ms: float
    max_ms: float
    corpus: str

    @property
    def median_ceiling_ms(self) -> float:
        return float(math.ceil(self.median_ms * MEDIAN_HEADROOM))

    @property
    def p95_ceiling_ms(self) -> float:
        return float(math.ceil(self.p95_ms * P95_HEADROOM))


#: EL COSTE DEL CROSS-ENCODER, Y POR QUÉ ESTA FILA SE REESCRIBE ENTERA
#: ------------------------------------------------------------------
#: These two rows were 23.89 ms and 23.36 ms of median, and they are now 195.88
#: ms and 195.78 ms. That is roughly EIGHT TIMES the previous retrieval, and it
#: is the price of the +4 / +2 net gain in ``PRECISION_FULL`` /
#: ``PRECISION_REDUCED``. It is published here rather than absorbed because the
#: alternative is a latency figure that describes a pipeline this project no
#: longer starts -- ``backend/main.py`` constructs the reranker explicitly.
#:
#: Two things make the number what it is, and neither is the embedding:
#:
#:   * The re-ranker scores THREE short passages, not ten long ones. Measured in
#:     isolation its median cost is 145-156 ms on this machine, so the 20-40x
#:     that a ten-passage re-ranker would have cost does not apply.
#:   * The remaining cost is the CPU. There is no GPU in this deployment and
#:     torch runs on whatever threads it finds, which is also why the spread
#:     between the fastest and slowest observed pass is wide (114-196 ms of
#:     median on the full population). The SLOWEST observed pass is recorded, per
#:     the convention in this module's docstring, precisely because that spread is
#:     real: a row picked on the lucky pass would be a row that trips its own
#:     ceiling on the next quiet morning.
#:
#: In the context of a turn this is not the dominant cost. The response cache's
#: own documentation puts an LLM turn at 4-8 s, so ~196 ms of retrieval is a
#: small single-digit percentage of one -- but it IS eight times what this
#: repository published for retrieval, and ``RERANK_ENABLED=false`` in the
#: environment is the switch back (``_HEALTHY_RERANK_STATES`` treats ``disabled``
#: as healthy, because it is a decision and not a fault).
LATENCY_FULL = LatencyFigures(
    population="full",
    chunks=124,
    questions=49,
    median_ms=195.88,
    p95_ms=413.19,
    max_ms=627.67,
    corpus="working tree del autor -- 124 chunks, embedder real, cross-encoder, CPU",
)

LATENCY_REDUCED = LatencyFigures(
    population="reduced",
    chunks=124,
    questions=49,
    median_ms=174.75,
    p95_ms=351.37,
    max_ms=484.38,
    corpus="git show HEAD: -- 124 chunks, embedder real, cross-encoder, CPU",
)

LATENCY_FIGURES: Tuple[LatencyFigures, ...] = (LATENCY_FULL, LATENCY_REDUCED)


# ── Resolver la fila de ESTE checkout ────────────────────────────────────────


def precision_for(documents: Dict[str, str]) -> Optional[PrecisionFigures]:
    """La fila de precisión calibrada para EXACTAMENTE este corpus, o ``None``.

    ``None`` es un rechazo y no un respaldo, por el mismo motivo que
    ``tests/real_wiki.py::measurement_for``. Lo que decide es el digest del
    corpus servido y no cuántas etiquetas resuelve: con las dos poblaciones en 49
    preguntas, el contador devolvía la fila ``full`` para el corpus commiteado
    sin avisar, y los tests de ese parámetro pasaban porque el techo de latencia
    tenía holgura, no porque la fila fuera la correcta.
    """
    population = corpus_for(documents)
    for figures in PRECISION_FIGURES:
        if figures.population == population:
            return figures
    return None


def latency_for(documents: Dict[str, str]) -> Optional[LatencyFigures]:
    """La fila de latencia de este corpus, o ``None``. Rechazo, no respaldo.

    Mismo criterio que ``precision_for``, y por el mismo motivo: la cifra es una
    afirmación sobre un corpus, no sobre un número de preguntas.
    """
    population = corpus_for(documents)
    for figures in LATENCY_FIGURES:
        if figures.population == population:
            return figures
    return None