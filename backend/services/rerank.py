"""Cross-encoder re-ranking of the slots ``RAGPipeline.retrieve`` already chose.

QUÉ AFIRMA ESTE MÓDULO
---------------------
Reordenar el top-3 ya elegido por el pipeline NO cambia el conjunto de páginas
servidas, y por tanto no puede mover ``recall@3``: una permutación de tres
elementos deja el mismo conjunto. Lo único que mueve es el ORDEN, y el orden es
lo que decide qué lee primero el modelo. Medido sobre las 49 preguntas
etiquetadas del corpus real, a ``top_k`` = 3 y por la ruta pública
(``tests/retrieval_measurements.py``):

    población   recall@1   promotions   demotions   neto
    full        36 -> 40       7            3       +4
    reduced     31 -> 33       7            5       +2

``recall@3`` queda en 44/49 y 38/41 respectivamente, sin tocar: es la misma
garantía estructural de arriba, no una coincidencia favorable.

POR QUÉ EL UMBRAL 0.25 SIGUE SIENDO UN UMBRAL DE COSENO
------------------------------------------------------
Porque se aplica ANTES, sobre los cosenos, y el reranker sólo reordena lo que
ya lo pasó. Las dos implementaciones posibles eran:

  * Filtrar por el logit del cross-encoder. Un logit no es un coseno: no tiene
    rango conocido, su escala depende del modelo y no de este corpus, y 0.25 no
    significa nada en esa escala. Traducirlo sería inventar un número. Peor
    aún, cambiaría el CONJUNTO servido, que es la decisión que
    ``RAGPipeline.__init__`` mide y publica con nombre.
  * Reordenar sólo lo que el coseno ya aprobó. El umbral conserva su
    significado published —"el mínimo coseno para que un chunk sea candidato"—
    y el reranker se convierte en una decisión sobre el ORDEN, que es
    exactamente lo que se le pidió medir.

La segunda es la que está. El límite explícito: un reranker NO puede recuperar
una página que el filtro del coseno descartó. Cinco de las 49 preguntas siguen
fuera del top-3 por eso, y ninguna reordenación las alcanza; están nombradas en
``tests/retrieval_measurements.py`` (``PRECISION_FULL.absent``) y son el techo
real de esta técnica sobre este corpus.

POR QUÉ ``embedding_text`` Y NO ``chunk.content``
-------------------------------------------------
Medido, y la diferencia no es una Gordian knot sino unSpoiler: con
``chunk.content`` el neto es **-5** (6 promociones, 11 despromociones) y con
``embedding_text`` es **+4** (7 y 3). Nueve preguntas de diferencia, y el signo
se da la vuelta.

No es un ajuste. ``embedding_text`` (prefijo de identidad + cuerpo) es lo que el
embedder codificó, lo que BM25 puntúa (``Bm25Index`` se ajusta sobre esa misma
función) y lo que el rescate léxico ofrece. Los tres rankers de este pipeline
leen el mismo texto, y el cross-encoder rompía esa continuidad: darle un cuerpo
sin nombre de página le quita justo el dato que un modelo de 0.1B necesita para
saber qué página está leyendo. El H1 + resumen + sección ES la identidad
(``rag.py:424``), y aquí es la mitad de la señal.

CÓMO FALLA Y QUÉ PASA CUANDO FALLA
----------------------------------
Un fallo aquí NUNCA rompe el retrieval. Si la descarga falla, si el modelo no
cabe en RAM, si ``predict`` lanza, el estado pasa a ``failed`` y ``rerank()``
devuelve ``None``, que el caller lee como "sigue con tu orden". El pipeline
sigue sirviendo denso+BM25, que es una configuración medida y no un modo
degradado sin nombre: 36/49 y 31/31 en vez de 40/49 y 33/33.

``failed`` es PEGAJOSO a propósito. Reintentar en cada consulta convertiría un
fallo de arranque (sin red, disco lleno) en una espera de 20 s por pregunta,
que es peor que la degradación que evita. Un reintento por proceso es un
contrato; uno por consulta es una incidencia.

Lo que un operador NECESITA ver es que el reranker no está unloaded cuando cree
que sí, así que ``mode`` existe y ``/api/health`` lo publica igual que
``rag_mode`` — el mismo argumento que ``RAGPipeline.mode``: el resto del payload
de health es invariante bajo esta degradación, y una respuesta ``status: ok``
sobre un pipeline que perdió su reranker es una respuesta sobre otro servicio.

CACHÉ: QUÉ SE VERSIONA Y QUÉ NO, Y POR QUÉ
------------------------------------------
``RERANK_VERSION`` versiona ESTA decisión de orden, y sigue el precedente de
``LEXICAL_RESCUE_VERSION`` (``rag.py:110``): es la versión de una decisión de
RETRIEVAL, no de una semántica de chunking, así que NO va en la identidad de la
caché de embeddings. El motivo es el mismo que allí y conviene no perderlo de
vista: la caché guarda vectores y cuatro campos de texto
(``content``, ``h1``, ``summary``, ``section``), y el reranker no cambia ninguno
de los dos. Invalidarla costaría un re-embed de ~100 s para reconstruir números
que no se movieron.

Pero la pregunta de fondo —"¿una caché que no sabe del reranker sirve resultados
viejos como nuevos?"— sí tiene respuesta, y no por descarte: ``embedding_text``
es una función pura de esos cuatro campos persistidos, así que una run
restaurada de caché y una run que recalcula producen los MISMOS textos de rerank
y por tanto los mismos logits. Eso es un invariante, y lo afirma
``tests/test_rerank.py::TestTheRerankDoesNotDependOnTheCache`` exactamente como
``TestTheRescueDoesNotDependOnTheCache`` afirma el suyo.

Lo que no existe en este repositorio, y conviene decirlo claro porque la
pregunta lo da por hecho: no hay ninguna caché del ORDEN final. La única caché
de embeddings guarda vectores, y ``response_cache.py`` es una tabla literal
frase→respuesta que ni consulta el retrieval (salta el RAG entero,
``backend/turns/answer_source.py:50``), así que un reranker no la puede volver
obsoleta. Lo que sí hay es una versión de la decisión y un estado publicado, que
es lo que hace falta para que un operador vea qué configuración produjo el orden
que está sirviendo.

CARGUE
------
Perezoso, y ``ensure_loaded`` es idempotente. Se llama al final de
``RAGPipeline.ingest_documents`` para que el PRIMER ``retrieve()`` de un proceso
no pague los ~20 s de carga en una consulta con una persona esperando delante, y
se llama también desde ``rerank()``, de modo que un pipeline que nunca calienta
también funciona. La consecuencia de que el warm sea síncrono es que el arranque
paga esos ~20 s una vez, y el precio de no hacerlo sería pagarlos en el peor
momento posible.
"""

from __future__ import annotations

import logging
import threading
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Cuál reordenación produjo un conjunto de resultados. Se sube cuando cambia la
#: pasada, el modelo por defecto o la política de degradación — nunca por un
#: ajuste de runtime que no cambie el ORDEN que sale.
#:
#: "1": ``embedding_text`` como texto de passage, threshold del coseno aplicado
#: ANTES (decisión de ``RAGPipeline``) y este módulo limitándose a permutar.
RERANK_VERSION = "1"

#: El cross-encoder por defecto. 15 idiomas, 0.1B params, Apache 2.0, y se carga
#: con el ``sentence-transformers`` que el proyecto YA tiene — no añade una
#: dependencia, que era una de las condiciones de la fase.
#:
#: ~470 MB de descarga y ~1 GB de RSS. No es un modelo que se pueda meter en el
#: arranque sin decir nada: por eso el estado es observable y por eso el fallo
#: degrada en lugar de romper.
DEFAULT_RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

#: Techo de tokens del cross-encoder. 512 es el máximo de la familia MiniLM y el
#: passage de este corpus no llega: el chunk más largo del corpus real son 266
#: PALABRAS (``tests/real_wiki.py``), así que truncar aquí no recorta ningún
#: passage del producto. Se pone explícito para que subirlo sea una decisión y
#: no un accidente del default de la librería.
DEFAULT_MAX_LENGTH = 512


# There is deliberately no ``from_env`` helper here. ``RERANKER_MODEL`` and
# ``RERANK_ENABLED`` are read in ``backend/config.py`` and nowhere else, because
# two readers of one environment variable is the defect ``EMBEDDING_MODEL`` is
# kept honest against by a test
# (``tests/test_rag_cache_identity.py::TestTheModelNameIsASingleSourceOfTruth``).
# ``backend/main.py`` builds the instance from ``config`` explicitly.


class Reranker:
    """Un cross-encoder sobre los passages que el pipeline YA eligió.

    THE ASSERTION: ``rerank()`` devuelve una permutación de lo que recibe, o
    ``None``. Nunca añade, nunca quita, nunca lanza. La primera mitad es lo que
    hace que ``recall@3`` sea estructuralmente inalterable; la segunda es lo que
    hace que un fallo de descarga sea invisible para el retrieval.

    No sabe qué es un ``Chunk``, y esa es la forma de que el módulo no dependa de
    ``backend.services.rag`` (que lo importa): el caller le pasa TEXTO y recibe
    ÍNDICES, y el emparejamiento con los chunks ocurre en el caller, donde el
    tipo existe.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_RERANKER_MODEL,
        enabled: bool = True,
        max_length: int = DEFAULT_MAX_LENGTH,
    ):
        self._model_name = model_name
        self._enabled = enabled
        self._max_length = max_length
        self._model = None
        #: ``"disabled"`` / ``"uninitialized"`` / ``"loaded"`` / ``"failed"``
        self._mode = "disabled" if not enabled else "uninitialized"
        self._reason: Optional[str] = None
        self._load_count = 0
        self._calls = 0
        self._degraded_calls = 0
        # Un load y un predict pueden llegar de dos sitios a la vez (el warm del
        # ingest y una query). La descarga del modelo no es thread-safe y
        # hacerlo dos veces cuesta 20 s y ~1 GB, así que el load se serializa.
        self._lock = threading.Lock()

    # ── Estado observable ─────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        """``disabled`` / ``uninitialized`` / ``loaded`` / ``failed``.

        Los cuatro estados son distinguibles desde fuera a propósito, y los dos
        del medio son los que antes no existían como respuesta. ``disabled`` es
        una DECISIÓN del despliegue, no un fallo — el mismo criterio que
        ``_HEALTHY_STORE_STATES`` en ``backend/routers/system.py:46``.

        ``uninitialized`` existe para que preguntar antes del primer uso no
        pueda devolver ``loaded``: una affirmation sobre un modelo que no se ha
        cargado es la misma clase de mentira que ``RAGPipeline.mode`` existe
        para impedir, y sería la primera que nadie lee.
        """
        return self._mode

    @property
    def reason(self) -> Optional[str]:
        """Por qué está en ``failed``, o ``None``. Para logs y health, no para lógica."""
        return self._reason

    @property
    def model_name(self) -> str:
        """El modelo que este reranker AFLIRMA cargar. Una promesa, no un hecho.

        El mismo cuidado que ``RAGPipeline._embedder_identity`` existe para
        deshacer: lo que está loaded es ``mode``, y esto es sólo lo pedido.
        """
        return self._model_name

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def identity(self) -> str:
        """La decisión de reordenación, versionada. Para logs y health."""
        return f"{self._model_name}@rerank-v{RERANK_VERSION}"

    def stats(self) -> dict:
        """Contadores de por qué está sano. Diagnóstico, no control."""
        return {
            "loads": self._load_count,
            "reranked": self._calls,
            "degraded": self._degraded_calls,
        }

    # ── Carga ─────────────────────────────────────────────────────────────

    def ensure_loaded(self) -> bool:
        """Carga el modelo si hace falta. Idempotente, y nunca lanza.

        ``False`` significa "no hay modelo", sea porque está deshabilitado, ya
        falló, o falló ahora. El llamador degrada; nadie muere por esto.
        """
        if self._mode in {"loaded", "failed", "disabled"}:
            return self._mode == "loaded"

        with self._lock:
            if self._mode in {"loaded", "failed", "disabled"}:
                return self._mode == "loaded"
            try:
                from sentence_transformers import CrossEncoder

                logger.info(
                    "Loading cross-encoder %s (rerank decision v%s)...", self._model_name, RERANK_VERSION
                )
                self._model = CrossEncoder(self._model_name, max_length=self._max_length)
                self._load_count += 1
                self._mode = "loaded"
                self._reason = None
                logger.info("Cross-encoder %s loaded", self._model_name)
                return True
            except Exception as e:  # noqa: BLE001 — el punto es que NUNCA propaga
                # ERROR y nombrando la CONSECUENCIA, por el mismo motivo que
                # ``RAGPipeline.initialize`` lo hace: una advertencia que describe
                # una causa no dice qué tiene ahora el operador. Aquí la
                # consecuencia es que el top-3 se sirve en orden denso+BM25.
                self._model = None
                self._mode = "failed"
                self._reason = f"{type(e).__name__}: {e}"
                logger.error(
                    "Cross-encoder unavailable (%s); retrieval will serve the "
                    "dense+BM25 order unchanged. Ordering quality is reduced to "
                    "the pre-rerank measurement and the top-3 set is unaffected. "
                    "Rerank decision v%s.",
                    e, RERANK_VERSION,
                )
                return False

    # ── El rerank ─────────────────────────────────────────────────────────

    def rerank(self, query: str, passages: Sequence[str]) -> Optional[List[Tuple[int, float]]]:
        """Ordena ``passages`` para ``query``. ``[(índice original, logit), ...]``.

        ``None`` significa DEGRADADO, y es un valor de retorno de primera clase
        y no una excepción: el caller lo lee como "sigue con el orden que ya
        tenías", que es el comportamiento por defecto que hay que mantener cuando
        algo falla. Por eso la lista vacía NO es un ``None`` — ``[]`` es una
        permutación legítima de cero passages.

        Los logits NO son cosenos y no se comparan con el umbral de 0.25: el
        umbral es una decisión del pipeline sobre cosenos, y se aplica antes de
        llegar aquí (ver el docstring del módulo). Lo que se devuelve es el
        logit porque es la magnitud que decidió la posición — el mismo argumento
        que hace ``_lexical_rescue`` devolver el BM25 y no el coseno, y la misma
        consecuencia: la columna de scores del panel es heterogénea, lo cual es
        visible y por tanto preferible a un número plausible sin relación con
        por qué se eligió la página.
        """
        if not self._enabled:
            return None
        if not passages:
            return []
        if not self.ensure_loaded():
            self._degraded_calls += 1
            return None

        try:
            pairs = [(query, text) for text in passages]
            scores = self._model.predict(pairs, show_progress_bar=False)
            order = sorted(
                range(len(passages)),
                # Stable, para que un empate conserve el orden denso+BM25 de
                # entrada en vez de decidirlo el orden del set de nostalgia de
                # numpy. Un empate debe ser "no opinion", no una permutación.
                key=lambda i: -float(scores[i]),
            )
            self._calls += 1
            return [(i, float(scores[i])) for i in order]
        except Exception as e:  # noqa: BLE001 — degradar, no propagar
            # A diferencia de la carga, aquí SÍ se marca ``failed`` y no se
            # reintenta: un ``predict`` que lanzó es un modelo que no va a
            # funcionar en esta máquina, y reintentar en cada consulta convierte
            # un problema en una espera por pregunta.
            self._mode = "failed"
            self._reason = f"predict failed: {type(e).__name__}: {e}"
            self._degraded_calls += 1
            logger.error(
                "Cross-encoder predict failed (%s); serving the dense+BM25 order "
                "unchanged for this and every later query in this process.",
                e,
            )
            return None