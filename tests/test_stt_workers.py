"""Whisper gets more than one worker, and the number is a decision not a default.

THE DEFECT
----------
``STTService.load_model`` built ``WhisperModel(model, device, compute_type)``
and stopped. ``faster_whisper``'s own signature defaults ``num_workers`` to 1,
and its docstring says what that costs: several workers enable real parallelism
between concurrent ``transcribe`` calls, because each CTranslate2 worker gets
its own copy of the model and a single one serialises them.

This application is not single-turn. Two turns on the same server overlap their
transcription whenever a second candidate tab -- or a retried POST that the
rate limiter delayed past the first turn's Whisper call -- overlaps the first.
Measured in this project, two concurrent turns: **21.5 s** against **28.5 s**
run in series (1.32x). With ``num_workers=4`` the same measurement gives
**1.60x**.

So the value is worth setting. What must not happen is setting it to a literal
sitting next to the model name, because a number with no name and no reason is
the number an operator cannot change without reading the source.

WHY FOUR
--------
The deployed box is 4 cores (ARM64, ``deployment/``), and the service is
single-process: uvicorn without ``--workers``. CTranslate2 spawns
``num_workers`` *threads* inside the one process, so the useful ceiling is the
core count, and going past it buys oversubscription rather than throughput.
Four is therefore "every core, no more", which is a rule an operator can apply
to a different box: ``min(cores, 4)``. Local development machines have more
cores and do not need all of them -- transcription there is a developer's own
turn, and the extra threads are idle cost.

WHAT IS ASSERTED HERE
---------------------
Configuration, not performance. A test that measured a speedup would be a
flaky benchmark that passes on a fast machine and fails on a busy one, and it
would prove nothing about the thing that was broken. What is asserted is that
the value is present, named, configurable, and the one the service actually
hands to ``WhisperModel``.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from backend.config import config
from backend.services.stt import STTService

#: The value this deployment ships with, and the number the tests quote. Held
#: here so a change to the default is a visible edit rather than a silent
#: divergence between the code and the tests that describe it.
EXPECTED_WORKERS = 4

#: The deployed box. See the module docstring for why this is the ceiling.
DEPLOYED_CORES = 4


@pytest.fixture
def whisper_calls(monkeypatch):
    """Install a fake ``faster_whisper`` and collect how it was constructed."""
    calls: list[dict] = []

    def _WhisperModel(name, **kwargs):
        calls.append({"model_name": name, **kwargs})
        return MagicMock()

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = _WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return calls


class TestTheWorkerCountIsConfigured:
    def test_whisper_is_built_with_several_workers(self, whisper_calls):
        STTService(model_name="small", device="cpu", compute_type="int8").load_model()

        assert len(whisper_calls) == 1, f"WhisperModel was not built: {whisper_calls}"
        assert "num_workers" in whisper_calls[0], (
            "the WhisperModel was built without num_workers, so it took "
            f"faster-whisper's default of 1: {whisper_calls[0]}"
        )

    def test_it_is_not_the_default_of_one(self, whisper_calls):
        """The specific regression: a value present but equal to the default.

        Asserted on the service the composition root actually builds, not on
        ``STTService()``: a bare construction carries the CLASS default, which is
        faster-whisper's, and the class default is not what ships.
        """
        import backend.main as main_mod

        main_mod.stt_service.load_model()

        assert whisper_calls[0]["num_workers"] != 1, (
            "num_workers=1 is faster-whisper's default and buys no parallelism: "
            f"{whisper_calls[0]}"
        )

    def test_it_is_the_value_this_deployment_measured(self, whisper_calls):
        import backend.main as main_mod

        main_mod.stt_service.load_model()

        assert whisper_calls[0]["num_workers"] == EXPECTED_WORKERS, (
            f"the shipped value is {EXPECTED_WORKERS} and the service is "
            f"building with {whisper_calls[0]['num_workers']}"
        )

    def test_the_class_default_is_faster_whispers(self, whisper_calls):
        """Stated so the value above is not mistaken for the library's.

        ``STTService`` does not read configuration, so a bare construction gets
        the library default. That is the right separation and it is also why the
        composition root has to pass the value -- and why a test that builds
        ``STTService()`` and asserts 4 would be asserting something no
        deployment does.
        """
        STTService().load_model()
        assert whisper_calls[0]["num_workers"] == 1, whisper_calls[0]

    def test_a_value_given_to_the_service_wins(self, whisper_calls):
        """Configurable means configurable, not a constant with a test on it."""
        STTService(num_workers=2).load_model()
        assert whisper_calls[0]["num_workers"] == 2, whisper_calls[0]

    def test_the_other_arguments_are_untouched(self, whisper_calls):
        """A fix that changes the model is not this fix."""
        STTService(model_name="small", device="cpu", compute_type="int8").load_model()
        call = whisper_calls[0]
        assert call["model_name"] == "small"
        assert call["device"] == "cpu"
        assert call["compute_type"] == "int8"


class TestTheDefaultIsReadableAndJustified:
    def test_config_publishes_a_named_value(self):
        assert hasattr(config, "WHISPER_NUM_WORKERS"), (
            "the worker count is not a named setting, so an operator cannot "
            "change it without reading the service's source"
        )
        assert config.WHISPER_NUM_WORKERS == EXPECTED_WORKERS

    def test_the_composition_root_passes_it_in(self):
        """What the service builds is the question; this is who tells it."""
        import backend.main as main_mod

        service = main_mod.stt_service
        assert isinstance(service, STTService)
        assert service.num_workers == config.WHISPER_NUM_WORKERS, (
            "the root and the config disagree about the worker count, so the "
            "value an operator sets is not the one in use: "
            f"{service.num_workers} != {config.WHISPER_NUM_WORKERS}"
        )

    def test_it_does_not_oversubscribe_the_deployed_box(self):
        """One worker per core, and no more.

        CTranslate2 spawns threads inside a single process, so a count above the
        core count buys context switching rather than throughput. This is the
        check that would catch the value being raised to 8 on a 4-core VPS.
        """
        assert config.WHISPER_NUM_WORKERS <= DEPLOYED_CORES, (
            f"{config.WHISPER_NUM_WORKERS} workers on a {DEPLOYED_CORES}-core "
            "deployment oversubscribes it"
        )

    def test_a_non_positive_value_is_refused_at_construction(self):
        """Zero workers is not a "let the library decide" value.

        ``num_workers=0`` in CTranslate2 is either rejected or, worse, accepted
        as "no inference worker", and either way the model cannot transcribe.
        A configuration typo must not be able to produce that.
        """
        with pytest.raises(ValueError):
            STTService(num_workers=0)
        with pytest.raises(ValueError):
            STTService(num_workers=-1)

    def test_the_env_var_is_read_at_construction(self, monkeypatch):
        """So a test -- and an operator -- can build the service at a value."""
        from backend.config import Config

        monkeypatch.setenv("WHISPER_NUM_WORKERS", "2")
        assert Config().WHISPER_NUM_WORKERS == 2

    def test_a_non_numeric_env_var_fails_fast_by_name(self, monkeypatch):
        from backend.config import Config

        monkeypatch.setenv("WHISPER_NUM_WORKERS", "many")
        with pytest.raises(ValueError, match="WHISPER_NUM_WORKERS"):
            Config()
