"""Seis preguntas de HISTORIAL LABORAL, su diagnóstico de fallo y el par con/sin tilde.

QUÉ HUECO CIERRA
----------------
Las 49 etiquetas de ``tests/real_wiki.py`` tienen TRES preguntas de experiencia
(``real_wiki.py:225-229``), y las tres nombran un employer's concrete:
``mercadona``, ``encargado``, ``frutero``. Ninguna pregunta "de dónde vengo" con
palabras que no son un nombre de empresa. Ese hueco es el que Midió este módulo:
medido sobre el harness oficial, ``¿Dónde has trabajado?`` devuelve
``faq/por-que-esta-empresa.md`` (0.489), ``faq/donde-veo-en-3-5-anos.md`` (0.410)
y ``faq/por-que-contratarte.md`` (0.377), y NINGUNA ``experience/*.md`` en el
top-3 -- tres páginas que son la respuesta de otra pregunta.

La distancia no es que las páginas estén mal escritas: es que la pregunta no
contiene ningún token que sea un nombre de empresa, y el coseno premia la
superficie léxica compartida ("¿dónde has trabajado?" y "aquí"/
"empresa"/"por qué" son las palabras que más aparecen en las páginas de opinión).
Es un mecanismo CONOCIDO y ya instrumentado en otro sitio del retrieval --
el prefijo de identidad en ``embedding_text`` (``backend/services/rag.py:424``) --
pero que este conjunto de preguntas midió contra el subconjunto equivocado.

LA FRAGILIDAD A LA TILDE, POR QUÉ ESTÁ SEPARADA DE LOS OTROS CINCO
-------------------------------------------------------------------
Whisper transcribe ``dónde`` y ``donde`` indistintamente según el modelo, el
micrófono y el ruido, y las reglas de faro de ``tests/real_wiki.py:44-47`` ya
dicen que la pregunta llega "lowercase, unpunctuated and with unreliable
accents". Una pregunta de historial laboral escrita sin tilde tiene que
funcionar igual que con ella, y la diferencia entre las dos tiene que ser un
NÚMERO, no una impresión.

Por eso el módulo nombra el par en ``ACCENT_PAIR`` y ``accent_delta()`` lo
presenta por separado: si las dos punctuated versiones divergen, el
normalizador de query (``Phase B``) es la palanca, y si divergen igual la
causa es el ranking y no la ortografía. Sin ese par, una mejora de 6/6 puede
significar "funciona cuando escribes bien".

POR QUÉ ``primary`` NO ES SUFICIENTE Y AQUÍ SÍ SE USA ``also``
--------------------------------------------------------------
``real_wiki.py`` puntúa en ESTRICTO (sólo ``primary``), y con razón para un
recall: pregunta si la página que el autor quería es la que salió. Pero
"¿en qué empresas has trabajado?" tiene tres páginas que la contestan de
verdad -- las tres ``experience/*.md``, y el ``## Career timeline (corrected)``
de ``profile/mikel.md`` que las lista -- y medirla en estricto produce un
fallo que no es un fallo: la página que salió SÍ respondía.

Así que este módulo puntúa las DOS vistas y publica las dos, y ``RANKED`` de
la tabla es la ESTRICTA para que siga siendo comparable con las 49 oficiales.
``as_primary`` es lo que dice "no salió la que yo quería"; ``as_relevant`` es
lo que dice "no salió nada que contestara". Confundirlas es el error que
``tests/retrieval_measurements.py`` ya nombra con ``DUPLICATED_QUESTION``.

LOS DIAGNÓSTICOS, Y POR QUÉ SON CUATRO Y NO UN "PASS/FAIL"
-------------------------------------------------------------
``classify`` no devuelve un booleano porque el ask es "¿por qué NO LA
RECUPERÓ?", y la respuesta cambia el fix por completo:

``sin_chunks``
    La página dorada no produjo ningún chunk (``backend/services/rag.py``
    ``_chunk_document`` la filtró). Ningún cambio en la query la va a
    recuperar, porque no existe sobre lo que puntuar.
``bajo_umbral``
    Hay chunks, pero su mejor coseno queda por debajo de ``pipeline.threshold``
    (0.25) y el filtro los descartó ANTES de ordenar. No es un problema de
    ranking: la página no es candidata. Subir el umbral global es la palanca
    que este repositorio ya-calibró una vez y Payne, así que es
    explícitamente la ÚLTIMA.
``fuera_de_top_k``
    La página es candidata y puntúa, pero el corte por página o el orden la
    dejan fuera de los slots. Palanca: ranking o expansión de la query.
``en_top_k``
    Llegó. ``rank`` dice en qué slot, porque llegar al 3º y llegar al 1º no es
    lo mismo cuando ``RAG_TOP_K`` es 3.

``rank`` es 1-based y ``None`` significa "no llegó", que ordena peor que
cualquier rank real (mismo convenio que ``retrieval_measurements.best_rank``).

LÍMITES
-------
    * NO es un guard. No hay ``assert`` sobre ninguna cifra de aquí y no está
      nombrado ``test_*``: es un instrumento, y su salida es una TABLA que hay
      que leer, no un veredicto. Los guards son
      ``tests/test_recall_claims.py`` y ``tests/test_committed_corpus_figures.py``,
      que miden las 49 oficiales.
    * ``cosine_by_page`` reimita el cálculo de coseno de ``retrieve()``
      (``rag.py:1511-1530``: ``expand_query`` → embed → normalizar → producto
      punto) reading internos, igual que hace
      ``tests/test_lexical_rescue.py:162-176``. Si cambia ese cálculo, este
      módulo miente y hay que actualizarlo; es un precio aceptado por no tener
      que re-embeber el corpus para mirar un coseno.
    * No toca el umbral. Sólo lo LEE y lo nombra en el diagnóstico, para que
      quien lo cambie tenga que decidir antes qué tolera
      (``tests/real_wiki.py:341-351``).
    * 6 preguntas NO son un recall. La unidad de movimiento aquí es UNA
      pregunta, y el propio repositorio ya fijó tres como mínimo credible
      (``real_wiki.py:347-349``). Con 6, "de 4 a 6" es un resultado; "de 6 a
      6" es dos.

Uso::

    python -m tests.work_history_cases              # tabla top_k=3 y 10
    python -m tests.work_history_cases --all       # + las 49 oficiales
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from tests.real_wiki import EMBEDDING_MODEL, LABELLED_CASES, Case, NARRATIVE, load_documents, resolved_cases
from tests.retrieval_measurements import build_pipeline, served_pages

#: Los dos anchos que se miden. 3 es lo que la producción pide
#: (``backend/config.py`` ``RAG_TOP_K``) y 10 separa "no es candidata" de
#: "es candidata pero el corte la enterró", que son dos fallos con dos fixes.
SHIPPED_TOP_K = 3
DIAGNOSTIC_TOP_K = 10


def norm(source: str) -> str:
    """Las claves de documento son ``str(Path.relative_to(...))``; el separador varía."""
    return source.replace("\\", "/")


def _c(
    question: str, primary: str, also: Sequence[str] = (), doc_class: str = NARRATIVE
) -> Case:
    """``Case`` con ``also`` como tupla en vez de ``frozenset``.

    Se reimplementa en vez de importarse porque ``real_wiki._c`` es privado y
    el módulo que lo contiene es explícito sobre no cruzar fixtures entre
    módulos de test; cuatro líneas que se leen enteras son más baratas de
    mantener que una importación de un nombre con guion bajo.
    """
    return Case(question, primary, frozenset(also), doc_class)


#: Las seis preguntas de historial laboral, en el formato de
#: ``tests/real_wiki.py:167-181`` (``question``, ``primary``, ``also``,
#: ``doc_class``). Las cuatro dimensiones que el ask nombra -- lugar,
#: empleador, fecha, rol -- están las cuatro, y el presupuesto de 6 se gasta en
#: un PAR con/sin tilde (``ACCOUNT_PAIR``) sobre la pregunta que la medición
#: rompe, no en una segunda versión de cada una.
#:
#: El ``also`` de la primera y la tercera es amplio a propósito: tres páginas
#: ``experience/*.md`` contestan de verdad "donde has trabajado", y medir eso
#: en estricto sería fabricar el fallo que este módulo viene a medir. Por eso
#: ambos puntos de vista se publican; ver el docstring del módulo.
WORK_HISTORY_CASES: Tuple[Case, ...] = (
    # 1. LUGAR, SIN tilde. La variante que la mediciónfficial sí recupera
    #    (gerente-mercadona entra 3ª con 0.418) y la que por tanto cualquier
    #    normalización de query tiene que PRESERVAR, no mejorar.
    _c(
        "donde has trabajado",
        "experience/gerente-mercadona-2019-2025.md",
        (
            "experience/encargado-bm-2016-2019.md",
            "experience/frutero-bm-2015-2016.md",
            "profile/mikel.md",
        ),
    ),
    # 2. LUGAR, CON tilde. Idéntica necesidad de información, un carácter de
    #    diferencia. El par es ``ACCOUNT_PAIR``.
    _c(
        "dónde has trabajado",
        "experience/gerente-mercadona-2019-2025.md",
        (
            "experience/encargado-bm-2016-2019.md",
            "experience/frutero-bm-2015-2016.md",
            "profile/mikel.md",
        ),
    ),
    # 3. EMPLEADOR. La gold primaria es ``profile/mikel.md`` porque su
    #    ``## Career timeline (corrected)`` (``wiki/profile/mikel.md:26-32``) es
    #    la única página que lista las TRES empresas en un solo sitio; las tres
    #    ``experience/*.md`` son aceptables pero respondieron una.
    _c(
        "en qué empresas has trabajado",
        "profile/mikel.md",
        (
            "experience/gerente-mercadona-2019-2025.md",
            "experience/encargado-bm-2016-2019.md",
            "experience/frutero-bm-2015-2016.md",
        ),
    ),
    # 4. FECHA de inicio del puesto más reciente.
    _c(
        "cuándo empezaste en mercadona",
        "experience/gerente-mercadona-2019-2025.md",
        ("profile/mikel.md",),
    ),
    # 5. FECHA del primer empleo. La gold es la página que dice literalmente
    #    "primer trabajo" (``wiki/experience/frutero-bm-2015-2016.md:17``).
    _c(
        "en qué año empezaste a trabajar",
        "experience/frutero-bm-2015-2016.md",
        ("profile/mikel.md",),
    ),
    # 6. ROL. ``summary_1line`` de la página de Mercadona es
    #    "Gerente B (Encargado) at Mercadona, 2019-Nov 2025".
    _c(
        "qué puesto tenías en mercadona",
        "experience/gerente-mercadona-2019-2025.md",
        ("profile/mikel.md",),
    ),
)

#: El par con/sin tilde, como PREGUNTAS, para que la comparación sea de
#: pregunta a pregunta y no "las que tienen tilde" contra "las que no".
#:
#: Se nombran por pregunta y no por índice porque la diferencia de interest es
#: justo lo que hay que publicar: si sólo se publicara el agregado, un
#: ``2/6`` con tilde contra ``3/6`` sin tilde se leería como un problema de
#: redacción del set cuando lo que importa es si LAS MISMAS DOS preguntas
#: divergen.
ACCOUNT_PAIR: Tuple[str, str] = ("donde has trabajado", "dónde has trabajado")

#: Qué etiqueta usa cada diagnóstico. Se publican como strings y no como un
#: enum porque el informe los imprime y un ``Diagnosis.OK`` en una tabla es
#: ruido en lugar de información.
MECHANISMS: Tuple[str, ...] = (
    "en_top_k",
    "fuera_de_top_k",
    "bajo_umbral",
    "sin_chunks",
)


# ── La sonda ─────────────────────────────────────────────────────────────────


def relevant(case: Case) -> FrozenSet[str]:
    """Las páginas que contestan ``case``: ``primary`` junto a ``also``."""
    return frozenset({case.primary}) | case.also


@dataclass(frozen=True)
class Probe:
    """Una pregunta medida: qué salió, en qué slot, y el mejor coseno de la gold.

    ``best_cosine`` es el máximo sobre TODOS los chunks de la página dorada, sin
    filtro de umbral, y es lo que separa ``bajo_umbral`` de ``fuera_de_top_k``.
    Sin él los dos fallos son indistinguibles desde el ranking, que es
    exactamente el punto en el que se elige la palanca equivocada.
    """

    question: str
    primary: str
    served: Tuple[str, ...]
    rank_primary: Optional[int]
    rank_relevant: Optional[int]
    best_cosine: Optional[float]

    def mechanism(self, threshold: float, top_k: int) -> str:
        """El diagnóstico, con la precedencia que hace útil el orden.

        ``sin_chunks`` antes que nada (no hay nada que recuperar), luego
        ``bajo_umbral`` (la página existe y no es candidata), luego
        ``fuera_de_top_k`` (es candidata y el corte la enterró). Se evalúa en
        ESTRICTO a propósito: una pregunta que saca una de sus páginas
        aceptables y falla en la primaria no se está quejando del mecanismo, se
        está quejando de la etiqueta.
        """
        if self.best_cosine is None:
            return "sin_chunks"
        if self.rank_primary is None and self.best_cosine < threshold:
            return "bajo_umbral"
        if self.rank_primary is None:
            return "fuera_de_top_k"
        return "en_top_k"


def cosine_by_page(pipeline, question: str) -> Dict[str, float]:
    """El mejor coseno por página, SIN filtro de umbral, para ``question``.

    Reimita ``RAGPipeline.retrieve`` (``backend/services/rag.py:1503-1530``)
    menos el filtro y el corte: ``expand_query`` → embed → normalizar → producto
    punto contra cada chunk, y luego el máximo por página. Se lee el embedder
    del pipeline en vez de re-embeber el corpus porque el corpus ya está
    emebido y la pregunta es una sola.

    LÍMITE: si ``retrieve()`` cambia su forma de calcular el coseno, este
    diccionario deja de ser el coseno que el retrieval usa y hay que
    actualizarlo. Es el precio de poder mirar un score sin re-ingestar 124
    chunks, y está anotado también en el docstring del módulo.
    """
    import numpy as np

    from backend.services.rag import expand_query

    expanded = expand_query(question)
    if pipeline._use_tfidf:
        vector = pipeline._tfidf_vectorizer.transform([expanded]).toarray().astype(np.float32)[0]
    else:
        vector = pipeline._embedder.encode([expanded])[0].astype(np.float32)
    norm_value = float(np.linalg.norm(vector))
    if norm_value > 0:
        vector = vector / norm_value

    best: Dict[str, float] = {}
    for chunk in pipeline.chunks:
        if chunk.embedding is None:
            continue
        source = norm(chunk.source)
        score = float(np.dot(vector, chunk.embedding))
        if source not in best or score > best[source]:
            best[source] = score
    return best


def probe(pipeline, case: Case, top_k: int = SHIPPED_TOP_K) -> Probe:
    """Mide una pregunta a través de ``retrieve()`` y con el coseno crudo.

    Los ranks salen de ``served_pages``, o sea de la ruta pública y con el
    rescate léxico y el rerank encendidos como los lleva producción; el coseno
    sale de ``cosine_by_page``, que es una pregunta aparte sobre el corpus.
    """
    served = tuple(served_pages(pipeline, case.question, top_k=top_k))
    cosines = cosine_by_page(pipeline, case.question)

    def rank_of(gold: FrozenSet[str]) -> Optional[int]:
        for index, source in enumerate(served):
            if source in gold:
                return index + 1
        return None

    return Probe(
        question=case.question,
        primary=case.primary,
        served=served,
        rank_primary=rank_of({case.primary}),
        rank_relevant=rank_of(relevant(case)),
        best_cosine=cosines.get(case.primary),
    )


# ── La tabla ─────────────────────────────────────────────────────────────────


def measure(
    pipeline, cases: Sequence[Case], top_k: int = SHIPPED_TOP_K
) -> List[Probe]:
    """Las sondas de ``cases`` a ``top_k``, en el orden en que se declararon."""
    return [probe(pipeline, case, top_k) for case in cases]


def strict_hits(probes: Sequence[Probe]) -> int:
    """Cuántas sondas sacaron su ``primary``, la vista comparable con las 49."""
    return sum(1 for item in probes if item.rank_primary is not None)


def relevant_hits(probes: Sequence[Probe]) -> int:
    """Cuántas sondas sacaron ALGO que conteste, la vista de personaInterview."""
    return sum(1 for item in probes if item.rank_relevant is not None)


def mechanism_counts(probes: Sequence[Probe], threshold: float, top_k: int) -> Dict[str, int]:
    """Un contador por diagnóstico, con los cuatro ceros puestos.

    Los ceros importan: un diagnóstico que nunca ocurre en una ejecución es un
    ``KeyError`` esperando, y la ausencia de un fallo es la mitad del informe.
    """
    counts = {name: 0 for name in MECHANISMS}
    for item in probes:
        counts[item.mechanism(threshold, top_k)] += 1
    return counts


def accent_delta(
    probes: Sequence[Probe], threshold: float, top_k: int
) -> Dict[str, object]:
    """La diferencia entre las dos mitades del ``ACCOUNT_PAIR``, por pregunta.

    Publica el rank de cada mitad y el mecanismo de cada mitad, y sólo después
    el veredicto, porque el veredicto solo ("la tilde no importa") es
    indistinguible de "importa y la tabla de arriba lo dice".
    """
    by_question = {item.question: item for item in probes}
    flat, accented = (by_question.get(question) for question in ACCOUNT_PAIR)
    missing = [
        question
        for question, item in zip(ACCOUNT_PAIR, (flat, accented))
        if item is None
    ]
    if missing:
        raise AssertionError(
            f"ACCOUNT_PAIR nombra preguntas que no se midieron: {missing}. "
            "Las seis de WORK_HISTORY_CASES tienen que estar todas en la "
            "pasada; si no, el par no es un par."
        )
    assert flat is not None and accented is not None

    return {
        "flat": flat.question,
        "flat_rank": flat.rank_primary,
        "flat_mechanism": flat.mechanism(threshold, top_k),
        "accented": accented.question,
        "accented_rank": accented.rank_primary,
        "accented_mechanism": accented.mechanism(threshold, top_k),
        "agrees": flat.rank_primary == accented.rank_primary
        and flat.mechanism(threshold, top_k) == accented.mechanism(threshold, top_k),
    }


def render(probes: Sequence[Probe], threshold: float, top_k: int, title: str) -> str:
    """La tabla de una pasada, como Markdown, lista para pegar en un informe."""
    lines = [
        f"### {title} — top_k={top_k}, umbral={threshold}",
        "",
        "| # | pregunta | primary | rank primario | rank relevante | mejor coseno | mecanismo | servido |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(probes, start=1):
        cosine = "n/d" if item.best_cosine is None else f"{item.best_cosine:.4f}"
        lines.append(
            f"| {index} | {item.question} | `{item.primary}` | "
            f"{item.rank_primary if item.rank_primary is not None else '—'} | "
            f"{item.rank_relevant if item.rank_relevant is not None else '—'} | "
            f"{cosine} | {item.mechanism(threshold, top_k)} | "
            f"{', '.join(item.served) if item.served else '(vacío)'} |"
        )
    counts = mechanism_counts(probes, threshold, top_k)
    lines += [
        "",
        f"estrictas {strict_hits(probes)}/{len(probes)} · "
        f"relevantes {relevant_hits(probes)}/{len(probes)} · "
        + " · ".join(f"{name} {count}" for name, count in counts.items()),
    ]
    delta = accent_delta(probes, threshold, top_k)
    lines += [
        f"par sin/con tilde: `{delta['flat']}` rank {delta['flat_rank']} "
        f"({delta['flat_mechanism']}) vs `{delta['accented']}` rank "
        f"{delta['accented_rank']} ({delta['accented_mechanism']}) → "
        f"{'coinciden' if delta['agrees'] else 'DIVERGEN'}",
    ]
    return "\n".join(lines)


# ── La pasada ────────────────────────────────────────────────────────────────


def run(
    cases: Sequence[Case] = WORK_HISTORY_CASES,
    rerank: bool = True,
    rescue: bool = True,
) -> Tuple[List[Probe], List[Probe], object]:
    """Las seis sondadas a ``top_k=3`` y a ``top_k=10``, sobre UN corpus.

    Un solo ingest para las dos pasadas porque el corpus no cambia entre
    anchos y re-embeberlo cuesta 9-17 s
    (``tests/retrieval_measurements.py:111-121``).

    ``rescue`` se apaga reemplazando el método en la clase, el mismo mango que
    usa ``tests/test_lexical_measurements``; con un ``finally`` porque un
    reemplazo filtrado haría que todo lo posterior del proceso midiera un
    pipeline distinto y pasara por el motivo equivocado.
    """
    from backend.services.rag import RAGPipeline

    pipeline = build_pipeline(load_documents(), rerank=rerank)
    original = RAGPipeline.__dict__["_lexical_rescue"]
    if not rescue:
        RAGPipeline._lexical_rescue = lambda self, q, d, k: d
    try:
        narrow = measure(pipeline, cases, SHIPPED_TOP_K)
        wide = measure(pipeline, cases, DIAGNOSTIC_TOP_K)
    finally:
        RAGPipeline._lexical_rescue = original
    return narrow, wide, pipeline


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mide las seis preguntas de historial laboral y diagnostica cada fallo."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="añade la tabla de las 49 etiquetas oficiales para la regresión",
    )
    parser.add_argument(
        "--dense",
        action="store_true",
        help="sin rerank ni rescate léxico: el orden denso solo",
    )
    args = parser.parse_args(argv)

    cases: Sequence[Case] = WORK_HISTORY_CASES
    narrow, wide, pipeline = run(
        cases, rerank=not args.dense, rescue=not args.dense
    )
    threshold = pipeline.threshold
    label = "PRODUCCIÓN (rerank + rescate)" if not args.dense else "DENSO (sin rerank ni rescate)"

    print(f"embedder {EMBEDDING_MODEL} · {len(pipeline.chunks)} chunks · {label}")
    print()
    print(render(narrow, threshold, SHIPPED_TOP_K, "Historial laboral"))
    print()
    print(render(wide, threshold, DIAGNOSTIC_TOP_K, "Historial laboral (ancho diagnóstico)"))

    if args.all:
        documents = load_documents()
        official = resolved_cases(documents)
        official_narrow = measure(pipeline, official, SHIPPED_TOP_K)
        official_wide = measure(pipeline, official, SHIPPED_TOP_K)
        print()
        print(
            f"### Las {len(official)} oficiales — recall@{SHIPPED_TOP_K} "
            f"estrictas {strict_hits(official_narrow)}/{len(official)} = "
            f"{strict_hits(official_narrow) / len(official):.4f}"
        )
        absent = [
            item.question
            for item in official_narrow
            if item.rank_primary is None
        ]
        print(f"ausentes ({len(absent)}): " + "; ".join(sorted(absent)))
        del official_wide

    return 0


if __name__ == "__main__":
    raise SystemExit(main())