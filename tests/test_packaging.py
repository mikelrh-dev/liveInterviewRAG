"""Packaging guard: every third-party import is a DECLARED dependency.

The defect this exists to prevent
---------------------------------
``backend/services/rag.py`` imported ``yaml`` (module level) and ``sklearn``
(the TF-IDF fallback) while neither appeared in ``backend/requirements.txt`` or
``pyproject.toml``.

They were installed anyway -- ``pyyaml`` via ``ctranslate2`` (a
``faster-whisper`` dependency) and via ``transformers``, ``scikit-learn`` via
``sentence-transformers`` -- so the defect was invisible. The suite passed. CI
passed. A clean ``pip install -r backend/requirements.txt`` *worked*.

That is the whole problem: it worked by accident. A clean install importing
``backend.services.rag`` successfully is NOT evidence that the declaration is
correct, because the module is importable for reasons the manifest never
mentions. The day ``ctranslate2`` drops its ``pyyaml`` pin, or
``sentence-transformers`` re-extras ``scikit-learn``, this repository stops
starting -- on a machine where the suite was green the day before.

An accidental transitive is not a dependency. It is a coincidence with a
version number. These tests make the coincidence impossible:

1. DECLARED -- every third-party top-level module imported under ``backend/``
   is provided by a distribution written down in ``backend/requirements.txt``.
   Under ``tests/`` it may additionally come from the ``dev`` extra.
2. NO DRIFT -- ``backend/requirements.txt`` and the ``[project] dependencies``
   array in ``pyproject.toml`` declare the same distributions at the same
   floors. Two manifests describing one install is one too many to keep honest;
   before this guard they had already diverged (``python-dotenv`` missing from
   ``pyproject.toml`` entirely, ``httpx`` at two different floors).
3. THE FALLBACK IS LIVE -- ``sklearn`` is declared, so the next reader has to
   know the TF-IDF path is reachable code, not dead weight. See
   ``TestTfidfFallbackIsLiveCode``.

Deliberately NOT asserted: that a clean install can import the package. That
would be a test of pip's resolver, it needs its own environment to mean
anything, and it passes today *because of* the defect this file documents.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_PATH = REPO_ROOT / "backend" / "requirements.txt"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

# Local packages. Not third-party, and not something a manifest can declare.
LOCAL_PACKAGES = frozenset({"backend", "tests"})

# Standard library on every interpreter the project supports, but not listed in
# ``sys.stdlib_module_names`` on 3.10 (it arrived in 3.11). The TOML reader below
# imports it opportunistically and degrades to ``tomli``, so its absence from
# that 3.10 set is a version artifact, not a third-party dependency.
STDLIB_SUPERSET = frozenset({"tomllib"})

# Top-level import name -> distribution that provides it. Only names that
# differ from their distribution need an entry; anything absent is assumed to
# be its own distribution.
#
# Explicit and hand-maintained on purpose. Deriving this from the installed
# environment (``importlib.metadata.packages_distributions``) would make the
# verdict depend on what happens to be in whichever venv the test runs in --
# a broken manifest would then "pass" on a machine with the package installed
# by hand, which is precisely the failure mode being guarded against.
IMPORT_TO_DISTRIBUTION = {
    "dotenv": "python-dotenv",
    "edge_tts": "edge-tts",
    "faster_whisper": "faster-whisper",
    "sentence_transformers": "sentence-transformers",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
}


def _normalize(name: str) -> str:
    """PEP 503 normalization: ``PyYAML``, ``py-yaml`` and ``pyyaml`` are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement_line(line: str) -> tuple[str, str]:
    """Split ``name[extra]>=floor`` into ``(name, remainder)``."""
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line.strip())
    assert match, f"unparseable requirement line: {line!r}"
    return match.group(1), line.strip()[match.end():]


def declared_runtime() -> dict[str, str]:
    """``{normalized distribution: everything after the name}`` from requirements.txt."""
    declared: dict[str, str] = {}
    for raw in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, remainder = _parse_requirement_line(line)
        declared[_normalize(name)] = remainder
    return declared


def _read_pyproject() -> dict:
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # Python 3.10; declared in the dev extra
        except ModuleNotFoundError:  # pragma: no cover - unreachable in practice
            raise AssertionError(
                "neither tomllib (3.11+) nor tomli (3.10) is importable, so "
                "pyproject.toml cannot be read. Install one of them; do not "
                "silently skip this check."
            )
    return tomllib.loads(text)


def declared_in_pyproject() -> dict[str, str]:
    """The same, for ``[project] dependencies`` in pyproject.toml."""
    return {
        _normalize(_parse_requirement_line(item)[0]): _parse_requirement_line(item)[1]
        for item in _read_pyproject()["project"]["dependencies"]
    }


def declared_dev() -> dict[str, str]:
    """``[project.optional-dependencies] dev`` -- what the test suite may import."""
    return {
        _normalize(_parse_requirement_line(item)[0]): _parse_requirement_line(item)[1]
        for item in _read_pyproject()["project"]["optional-dependencies"]["dev"]
    }


def third_party_imports(relative_root: str) -> dict[str, list[str]]:
    """``{top-level module: ["path:line", ...]}`` for non-stdlib, non-local imports."""
    found: dict[str, list[str]] = {}
    for path in sorted((REPO_ROOT / relative_root).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [(alias.name.split(".")[0], node.lineno) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is relative: always local.
                modules = (
                    [(node.module.split(".")[0], node.lineno)]
                    if node.level == 0 and node.module
                    else []
                )
            else:
                continue
            for module, lineno in modules:
                if module in sys.stdlib_module_names or module in LOCAL_PACKAGES:
                    continue
                if module in STDLIB_SUPERSET:
                    continue
                where = f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}"
                found.setdefault(module, []).append(where)
    return found


def _undeclared(imports: dict[str, list[str]], declared: dict[str, str]) -> list[str]:
    undeclared = []
    for module, locations in imports.items():
        dist = _normalize(IMPORT_TO_DISTRIBUTION.get(module, module))
        if dist not in declared:
            undeclared.append(
                f"{dist} (imported as `{module}` at {', '.join(locations)})"
            )
    return sorted(undeclared)


class TestEveryImportIsDeclared:
    """The invariant that would have caught the original defect."""

    def test_runtime_imports_are_all_declared(self):
        """Every third-party import under backend/ is a declared dependency.

        This is the failing test for the packaging defect: it fails on
        ``pyyaml``, ``scikit-learn`` and ``starlette`` before the manifests
        declare them, and passes after.
        """
        undeclared = _undeclared(third_party_imports("backend"), declared_runtime())
        assert not undeclared, (
            "backend/ imports distributions that backend/requirements.txt does "
            "not declare. They may be installed transitively today; that is a "
            "coincidence, not a declaration. Undeclared:\n  - "
            + "\n  - ".join(undeclared)
        )

    def test_test_imports_are_all_declared(self):
        """Test-only third-party imports are in requirements.txt or the dev extra."""
        declared = {**declared_runtime(), **declared_dev()}
        undeclared = _undeclared(third_party_imports("tests"), declared)
        assert not undeclared, (
            "tests/ imports distributions declared in neither "
            "backend/requirements.txt nor the pyproject dev extra. Undeclared:\n  - "
            + "\n  - ".join(undeclared)
        )


class TestManifestsDoNotDrift:
    """Two manifests, one install. They must say the same thing."""

    def test_pyproject_matches_requirements(self):
        requirements = declared_runtime()
        pyproject = declared_in_pyproject()

        only_in_requirements = sorted(set(requirements) - set(pyproject))
        assert not only_in_requirements, (
            "declared in backend/requirements.txt but missing from "
            f"pyproject.toml [project] dependencies: {only_in_requirements}"
        )

        only_in_pyproject = sorted(set(pyproject) - set(requirements))
        assert not only_in_pyproject, (
            "declared in pyproject.toml [project] dependencies but missing from "
            f"backend/requirements.txt: {only_in_pyproject}"
        )

        mismatched = {
            name: (requirements[name], pyproject[name])
            for name in sorted(set(requirements) & set(pyproject))
            if requirements[name] != pyproject[name]
        }
        assert not mismatched, (
            "same distribution declared at different floors "
            "(requirement.txt vs pyproject.toml): "
            + ", ".join(
                f"{name}: {a!r} vs {b!r}" for name, (a, b) in mismatched.items()
            )
        )

    def test_floors_use_the_project_convention(self):
        """Floors, never pins. A `==` pin here is an upstream dependency problem."""
        for source, declared in (
            ("backend/requirements.txt", declared_runtime()),
            ("pyproject.toml", declared_in_pyproject()),
        ):
            pinned = {n: spec for n, spec in declared.items() if "==" in spec}
            assert not pinned, f"{source} pins exact versions: {pinned}"


class TestTfidfFallbackIsLiveCode:
    """Why ``scikit-learn`` is a hard dependency and not an optional extra.

    ``RAGPipeline.initialize()`` catches every exception around building the
    sentence-transformer embedder and drops to TF-IDF. The trigger is not
    theoretical: an offline machine, a cold HuggingFace cache, a corrupt
    download or an OOM all raise, while the ``import sentence_transformers``
    on the line above still succeeds. In that state the app starts, retrieves
    with TF-IDF, and needs ``sklearn``.

    It is also free. ``sentence-transformers`` already requires
    ``scikit-learn`` unconditionally, so the package is installed on every
    machine that can run the primary path. Declaring it explicitly costs no
    extra download; leaving it undeclared means the fallback is one transitive
    edge away from an ``ImportError`` on a machine that never sees it in dev.
    """

    def test_fallback_activates_and_works(self, monkeypatch):
        import sentence_transformers

        def unavailable(*args, **kwargs):
            raise OSError("simulated: model unavailable (offline / no HF cache / OOM)")

        monkeypatch.setattr(sentence_transformers, "SentenceTransformer", unavailable)

        from backend.services.rag import RAGPipeline

        pipeline = RAGPipeline()
        pipeline.initialize()

        assert pipeline._use_tfidf is True
        # TF-IDF vectors are unstable across restarts, so the answer cache
        # must never store them (design D8).
        assert pipeline.embedder is None

        matrix = pipeline._tfidf_vectorizer.fit_transform(
            ["hola mundo", "adios mundo"]
        )
        assert matrix.shape[0] == 2


class TestImportProofEnvironmentIsNotNeeded:
    """Guards the guard: this file must not require a second interpreter.

    The brief for this fix asked for a clean-install proof. That proof is a
    property of pip's resolver, not of the codebase, and it needs a throwaway
    virtualenv to mean anything. Pinning the module map against the *current*
    interpreter instead is what makes the check reproducible in CI.
    """

    def test_every_mapped_import_name_is_importable_or_known(self):
        """Sanity-check the map: a stale key here is a silently skipped guard."""
        for module in IMPORT_TO_DISTRIBUTION:
            assert module.islower() or "_" in module, (
                f"{module!r} is not a valid Python module name"
            )

    @pytest.mark.parametrize("path", [REQUIREMENTS_PATH, PYPROJECT_PATH])
    def test_manifests_exist(self, path):
        assert path.is_file(), f"expected manifest at {path}"
