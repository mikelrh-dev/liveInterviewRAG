"""What the candidate is told, and nothing else.

``openspec/specs/conversation-engine/spec.md`` (TTS Error Resilience) is
explicit: error payloads "SHALL NOT include internal paths, stack traces, or
sensitive details". Every ``error`` emission used to interpolate ``str(e)`` into
that payload, so a real ``sentence-transformers``, ``httpx`` or provider failure
shipped a model cache path, a local directory and sometimes an upstream
response body -- a fragment of which can be an API key -- to a public endpoint.

This module is the whole of the user-facing wording, in one place, so it can be
reviewed as a set. Three properties hold for every message here:

* It names **the stage that failed**, so the candidate knows what did not
  happen. A silent failure is the one thing a candidate cannot distinguish from
  the app simply not listening.
* It says **whether asking again is worth it**, which is the only action
  available to them. Fatal messages say so; the recoverable per-sentence
  message does not, because that turn finishes without them.
* It contains **no diagnostic detail whatsoever**. Not a path, not a provider,
  not a status code.

The detail is not discarded, only withheld. Every site below logs the original
exception with ``exc_info=True`` before emitting, and the services chain their
cause with ``raise ... from e``, so the text remains reachable in the traceback
and the ``__cause__`` chain even where the message was cleaned.
"""

#: Transcription failed. Distinct from the "no speech detected" message, which is
#: a silent recording rather than a broken stage.
STT_FAILED = (
    "No se pudo transcribir el audio. Repite la pregunta para intentarlo de nuevo."
)

#: The answer could not be generated, so nothing was stored for the turn.
LLM_FAILED = (
    "No se pudo generar la respuesta. Repite la pregunta para intentarlo de nuevo."
)

#: The answer is written but could not be spoken. Fatal for the turn: the
#: candidate hears nothing, so the retry advice matters.
TTS_FAILED = (
    "No se pudo generar el audio de la respuesta. "
    "Repite la pregunta para intentarlo de nuevo."
)

#: One sentence could not be spoken. RECOVERABLE: it is emitted with a chunk
#: ``id``, which is the frontend's "skip this one and keep going" signal, and it
#: is only honest while more audio is still coming. No retry advice here --
#: asking again would discard a turn that is about to complete on its own.
#:
#: A provider failure has three outcomes, not two, and this message is the one
#: the candidate reads for two of them.
#:
#: Some sentences spoken: the turn is stored, MARKED ``incomplete`` -- on the
#: message, and on the terminal event -- because the text on disk is every
#: sentence the model produced and the candidate heard a fraction of them. This
#: notice is what the page has live, and it is the only thing that can name WHICH
#: sentence was lost; the mark is what the recruiter gets later.
#:
#: Nothing at all spoken -- every sentence failed, so the answer was written and
#: never heard -- the turn is dead: the per-chunk errors above are already on
#: the wire, the stream then emits fatal ``TTS_FAILED`` with no ``id``, and
#: nothing is stored. ``TTS_CHUNK_FAILED`` is the "there is a gap in this
#: answer" message; ``TTS_FAILED`` is the "you heard none of it" one, and the
#: difference is the whole of what the page can honestly show.
TTS_CHUNK_FAILED = "No se pudo generar el audio de una parte de la respuesta."

#: The sign-off could not be spoken. The interview still ends: the farewell text
#: is already on screen, so the goodbye simply has no voice.
FAREWELL_TTS_FAILED = "No se pudo generar el audio de la despedida."

#: Anything unclassified. Says the turn did not complete without claiming to
#: know which part broke, which the server log does know.
UNEXPECTED_ERROR = (
    "Error inesperado al procesar la pregunta. "
    "Repite la pregunta para intentarlo de nuevo."
)
