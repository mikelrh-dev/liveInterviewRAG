"""Late-bound access to the process-wide service singletons.

``backend.main`` is the composition root: it constructs every service once, at
import time, and those module-level names *are* the live instances. This module
is the one place the rest of the backend reads them from.

Late binding is the point, not an accident. The singletons are swappable at
runtime — the test suite replaces ``backend.main.stt_service`` and friends with
doubles — so a consumer that captured the object at import time would keep
talking to the real service while the test believed it had stubbed it. Reading
the attribute on every call is what makes the swap observable, exactly as it was
when the handlers lived beside the construction site.

The mutable stores (``conversations``, ``_rate_limit_store``) are deliberately
NOT accessed this way. Tests mutate those in place and never rebind them, so
modules import the objects directly; a function call per lookup would buy
nothing.

One consequence to keep in mind: a module that wants to be swappable must reach
its dependency through here rather than importing the object. Binding
``from backend.services.tts import TTSService`` is fine (a class); binding a
constructed instance at import time is not.
"""


def _root():
    """Return the composition root module, resolved on demand.

    The import sits inside the function deliberately. At module level it would
    make this module's load order depend on ``backend.main``'s, and the cycle
    would only resolve when ``main`` happened to be imported first: starting from
    ``backend.conversation`` instead left it partially initialized and every
    consumer failed with a circular-import error. Resolving lazily makes the
    dependency a runtime one, so the import graph stays acyclic from any entry
    point.
    """
    import backend.main as root

    return root


def stt_service():
    """Whisper transcription service."""
    return _root().stt_service


def llm_service():
    """LLM generation service (blocking and streaming)."""
    return _root().llm_service


def tts_service():
    """Speech synthesis service."""
    return _root().tts_service


def rag_pipeline():
    """Document retrieval pipeline, and the owner of the only embedder."""
    return _root().rag_pipeline


def candidate_profile():
    """The candidate's wiki-backed profile."""
    return _root().candidate_profile


def report_service():
    """Interview report writer."""
    return _root().report_service


def persistence():
    """Write-through SQLite store."""
    return _root().persistence


def cleanup_stale_audio():
    """Remove audio files left over from previous runs.

    Reached through this module because it is swappable: tests replace
    ``backend.main.cleanup_stale_audio`` to stop the periodic sweep from
    touching the real audio directory.
    """
    return _root().cleanup_stale_audio
