"""The ``/audio`` mount must declare its own directory, not inherit one.

``TTSService.__init__`` performs an implicit ``mkdir`` on its output directory.
``app.mount("/audio", StaticFiles(directory=...))`` runs at *import* time, so the
mount's directory only existed because a service constructor had created it as a
side effect, several hundred lines earlier in the same module. That dependency
is invisible: make the service lazy, or reorder two module-level statements, and
the application dies at import with an opaque ``StaticFiles`` error about a
directory nobody remembers asking for.

These tests pin the two things the mount actually needs -- the directory on disk
and the route registration -- and the subprocess case proves the coupling to the
service constructor is gone rather than merely unexercised.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from starlette.routing import Mount

REPO_ROOT = Path(__file__).resolve().parents[1]

# Imports ``backend.main`` in a fresh interpreter with the audio directory
# pointed at a path that does not exist and with the TTS constructor's ``mkdir``
# removed. If the mount still comes up, the directory is created deliberately
# rather than as a side effect of a service.
_PROBE = """
import sys
from pathlib import Path

sys.path.insert(0, {repo!r})

import backend.config as cfg
cfg.config.AUDIO_DIR = Path({audio_dir!r})

import backend.services.tts as tts


def _no_side_effect_init(self, voice="x", output_dir="y"):
    # The real constructor mkdirs its output_dir. Dropping that line is the
    # whole point: the mount must not depend on it.
    self.voice = voice
    self.output_dir = Path(output_dir)


tts.TTSService.__init__ = _no_side_effect_init

import backend.main as main
from starlette.routing import Mount

assert not {audio_dir!r} or True
assert cfg.config.AUDIO_DIR.is_dir(), "audio dir missing after import"
mounts = [r for r in main.app.routes if isinstance(r, Mount) and r.name == "audio"]
assert len(mounts) == 1, "no /audio mount registered"
assert mounts[0].path == "/audio", "audio mount serves the wrong path"
print("PROBE_OK")
"""


def _run_probe(audio_dir: Path) -> subprocess.CompletedProcess:
    script = textwrap.dedent(_PROBE).format(
        repo=str(REPO_ROOT), audio_dir=str(audio_dir)
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
    )


class TestAudioDirectory:
    def test_audio_dir_exists_after_import(self):
        from backend.config import config

        assert config.AUDIO_DIR.is_dir(), (
            f"audio directory {config.AUDIO_DIR} does not exist; the /audio "
            f"mount would have failed at import"
        )

    def test_bootstrap_is_idempotent(self):
        """The bootstrap may be called again (startup re-runs it) safely."""
        from backend.main import ensure_audio_dir

        first = ensure_audio_dir()
        second = ensure_audio_dir()

        assert first.is_dir()
        assert first == second


class TestAudioMount:
    def test_audio_mount_is_registered(self):
        from backend.main import app

        mounts = [
            r for r in app.routes if isinstance(r, Mount) and r.name == "audio"
        ]

        assert len(mounts) == 1, f"expected one /audio mount, found {len(mounts)}"
        assert mounts[0].path == "/audio"

    def test_mount_survives_without_the_tts_constructor_side_effect(self, tmp_path):
        """The regression this whole module exists for.

        Runs a fresh interpreter whose audio directory does not exist and whose
        TTS service no longer creates it. Before the fix this raised
        ``RuntimeError: Directory 'audio' does not exist`` out of the mount.
        """
        audio_dir = tmp_path / "audio-does-not-exist-yet"
        assert not audio_dir.exists()

        result = _run_probe(audio_dir)

        assert "PROBE_OK" in result.stdout, (
            "importing backend.main without the TTS constructor's mkdir failed:\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
        assert audio_dir.is_dir(), "the mount did not create the directory itself"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__]))
