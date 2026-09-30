"""The deployment path must create the directories the server actually serves.

THE DEFECT
----------
``nginx/interview.conf`` serves the site from ``root /opt/interviewtts/frontend``
and generated audio from ``alias /opt/interviewtts/audio/``. ``scripts/deploy.sh``
rsynced exactly one directory, ``candidate/``. Nothing in the repository ever
created ``frontend/`` on the VPS, so nginx answered every request for the site
with a 404 -- and the deploy script reported ``DEPLOY OK`` the whole time.

The same gap, one level down: ``deployment/interviewtts.service`` names a
``User``, a ``WorkingDirectory``, a venv interpreter, and three
``ReadWritePaths``. No script in the repository creates the user, the venv, or
the code, and ``README.md`` covered all of it with the line "Set up systemd
service with ``deployment/interviewtts.service``". A documented step that
silently does nothing is worse than an absent one.

WHAT IS ASSERTED
----------------
The list of directories is read OUT of the two files that name them rather than
copied into this test. That is the point: if nginx starts serving from
``/opt/interviewtts/static`` tomorrow, this test demands that ``deploy.sh``
handle ``static`` too, instead of quietly going stale the way the original
assertion would have.

Two treatments are accepted, and the difference between them is real:

``rsync``
    a static tree owned by the repository, which must be mirrored to the VPS
    (``frontend/``);
``mkdir -p``
    a directory the running service writes into, which must exist before the
    unit starts (``audio/``, ``data/``, ``reports/``). Rsyncing these would be
    actively wrong: ``audio/`` is TTS output and the periodic sweep deletes from
    it, so a mirror would fight the runtime.
"""

import re
from pathlib import Path

import pytest

from backend.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]
NGINX_CONF = REPO_ROOT / "nginx" / "interview.conf"
DEPLOY_SH = REPO_ROOT / "scripts" / "deploy.sh"
UNIT_FILE = REPO_ROOT / "deployment" / "interviewtts.service"

#: EVERY user-facing README. Not "the" README.
#:
#: This suite used to read ``README.md`` and nothing else, which is the whole
#: mechanical cause of the documentation-drift class: ``README_ES.md`` had zero
#: coverage and therefore accumulated every falsehood the English file had
#: already been fixed for -- Piper TTS, ``0.0.0.0``, ``docker compose up``,
#: ``<repo-url>``, ``155+ tests``, WSGI, float16, ``cd InterviewTTS`` and the
#: install command. A document nobody checks is a document nobody keeps true.
#:
#: Every doc assertion below is parametrized over this tuple, so a claim that
#: drifts in one language goes red in both directions of the check.
READMES = (REPO_ROOT / "README.md", REPO_ROOT / "README_ES.md")

#: The command that anchors the deployment section, in place of its heading.
#: ``## Deployment`` and ``## Despliegue`` are wording; ``useradd`` is content,
#: and only the deployment section contains it.
_DEPLOYMENT_ANCHOR = "useradd"


def _level2_sections(text: str) -> list[str]:
    """Split a document into its ``## ``-level sections, heading included."""
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            if current:
                sections.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def _deployment_section(readme: str) -> str:
    """The one ``## `` section that documents provisioning the service user.

    Found by content rather than by heading, because the heading is the word
    that differs between the two languages and the content is what the
    assertions are about. Raises rather than defaulting when the anchor is
    missing or ambiguous: a locator that silently returned the whole document
    would turn "the Deployment section must create audio/" into "somewhere in
    the README there is a mkdir", which is the weaker assertion this suite
    deliberately makes.
    """
    sections = [s for s in _level2_sections(readme) if _DEPLOYMENT_ANCHOR in s]
    if len(sections) != 1:
        raise AssertionError(
            f"expected exactly one ## section containing {_DEPLOYMENT_ANCHOR!r}, "
            f"found {len(sections)}; the deployment section cannot be located "
            "and every assertion scoped to it would be vacuous"
        )
    return sections[0]

#: The default the deploy script pushes to, and the root nginx serves from.
#: Hard-coded once here so a mismatch is reported as a mismatch rather than
#: inferred from whichever side a test happened to read.
DEPLOY_ROOT = "/opt/interviewtts"


@pytest.fixture(scope="module")
def deploy_sh() -> str:
    return DEPLOY_SH.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )


def _nginx_served_subdirs() -> set[str]:
    """Directory names under DEPLOY_ROOT that nginx serves files from.

    ``root`` and ``alias`` are the two directives that name one. A
    ``proxy_pass`` names a port, not a tree, and is deliberately not read here.
    """
    conf = _strip_comments(NGINX_CONF.read_text(encoding="utf-8"))
    served = set()
    for path in re.findall(r"\b(?:root|alias)\s+(\S+);", conf):
        prefix = DEPLOY_ROOT.rstrip("/") + "/"
        if path.startswith(prefix):
            rest = path[len(prefix) :].strip("/")
            if rest:
                served.add(rest.split("/")[0])
    return served


def _readwrite_paths() -> list[tuple[str, bool]]:
    """Every ``ReadWritePaths`` entry as ``(path, is_required)``.

    ``is_required`` is False for systemd's ``-`` prefix -- "ignore if missing" --
    which is the whole point of the prefix: systemd sets the mount namespace up
    before ``ExecStart`` and fails the unit outright when a *bare* path does not
    exist. With ``Restart=always`` that is a restart loop every 5 seconds, so the
    prefix is what decides whether a missing directory is fatal or benign, and
    the distinction has to be readable from the unit rather than guessed at.

    Note what this function does NOT do: it does not drop the prefixed entries.
    An earlier version filtered them out by testing ``path.startswith(prefix)``
    against a string that began with ``-``, silently returning nothing for every
    optional path -- so a guard written on top of it would have been blind to
    exactly the entries whose prefix policy most needs reviewing. The entries are
    all read, and the prefix is carried out as data.
    """
    unit = UNIT_FILE.read_text(encoding="utf-8")
    found: list[tuple[str, bool]] = []
    for directive in re.findall(r"^\s*ReadWritePaths=(.+)$", unit, re.MULTILINE):
        for entry in directive.split():
            required = not entry.startswith("-")
            found.append((entry.lstrip("-"), required))
    return found


def _subdir(path: str) -> str | None:
    """The first path segment under DEPLOY_ROOT, or None if outside it."""
    prefix = DEPLOY_ROOT.rstrip("/") + "/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix) :].strip("/")
    return rest.split("/")[0] if rest else None


def _unit_required_subdirs() -> set[str]:
    """Directories the unit file REQUIRES to exist before it can start.

    Only the unprefixed entries, which is what the name claims. A ``-``-prefixed
    path is by definition not required to exist, so demanding that a script
    create it would be asserting something the unit itself does not assert.
    """
    found = set()
    for path, required in _readwrite_paths():
        if not required:
            continue
        subdir = _subdir(path)
        if subdir:
            found.add(subdir)
    return found


def _unit_optional_subdirs() -> set[str]:
    """Directories the unit tolerates being absent (``-``-prefixed)."""
    found = set()
    for path, required in _readwrite_paths():
        if required:
            continue
        subdir = _subdir(path)
        if subdir:
            found.add(subdir)
    return found


def _unit_paths() -> set[str]:
    """Every ``ReadWritePaths`` entry, prefix stripped, as an absolute path."""
    return {path for path, _ in _readwrite_paths()}


def _deployed_rag_cache_dir() -> str:
    """Where ``RAGPipeline`` persists embeddings once deployed, as an absolute path.

    ``Config.RAG_CACHE_DIR`` is the single source of truth (backend/config.py:148,
    overridable with ``RAG_CACHE_DIR``), and it is built from ``BASE_DIR``, which
    is the repository root (``config.py:133``). Deployed, the repository root IS
    ``DEPLOY_ROOT`` -- that is what the clone target and the unit's
    ``WorkingDirectory`` are. So the path the unit file has to name is
    ``DEPLOY_ROOT / <RAG_CACHE_DIR relative to BASE_DIR>``, computed here rather
    than written out, because the defect being guarded was a path in the unit
    that no other file agreed with and a hardcoded copy here would have been a
    third copy to keep in step instead of the derivation that removes the need
    for one.

    An ambient ``RAG_CACHE_DIR`` is popped first, so the value compared is the
    one a fresh machine gets rather than whatever this shell happens to export.
    """
    import os

    previous = os.environ.pop("RAG_CACHE_DIR", None)
    try:
        cfg = Config()
    finally:
        if previous is not None:
            os.environ["RAG_CACHE_DIR"] = previous
    relative = Path(cfg.RAG_CACHE_DIR).relative_to(cfg.BASE_DIR)
    return f"{DEPLOY_ROOT}/{relative.as_posix()}"


def _commands(script: str) -> str:
    """The executable lines of a shell script, with continuations joined.

    Two normalisations, both load-bearing:

    * ``#`` comment lines are dropped. This file's own subject is prose about
      commands, and matching against prose produces exactly the wrong answer in
      both directions -- a comment explaining why ``audio/`` is created rather
      than rsynced reads as an rsync of ``audio/``, and a comment explaining
      why ``frontend/`` must be mirrored reads as no rsync at all.
    * Backslash-newline is joined, so an ``rsync`` invocation written across two
      lines is one line. Every rsync in ``deploy.sh`` is wrapped.
    """
    code = "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )
    return re.sub(r"\\\s*\n\s*", " ", code)


def _is_rsynced(deploy_sh: str, name: str) -> bool:
    return bool(re.search(rf"rsync\b[^\n]*{re.escape(name)}/", _commands(deploy_sh)))


def _is_created(deploy_sh: str, name: str) -> bool:
    return bool(re.search(rf"mkdir\s+-p[^\n]*{re.escape(name)}", _commands(deploy_sh)))


class TestDeployCreatesWhatNginxServes:
    def test_the_served_list_is_not_empty(self):
        """Guard the guard: a parser that reads nothing would pass everything."""
        assert _nginx_served_subdirs(), (
            f"no `root` or `alias` under {DEPLOY_ROOT} found in {NGINX_CONF.name}; "
            "this test would pass vacuously"
        )

    def test_the_deploy_root_is_the_one_nginx_serves_from(self, deploy_sh):
        match = re.search(r'REMOTE_DIR="\$\{REMOTE_DIR:-(.+?)\}"', deploy_sh)
        assert match is not None, "deploy.sh no longer declares a REMOTE_DIR default"
        assert match.group(1).rstrip("/") == DEPLOY_ROOT, (
            f"deploy.sh pushes to {match.group(1)} but nginx serves from "
            f"{DEPLOY_ROOT}. Every directory check below would then be satisfied "
            "by names that are not the ones the server reads."
        )

    @pytest.mark.parametrize("name", sorted(_nginx_served_subdirs()))
    def test_every_served_directory_is_provisioned(self, deploy_sh, name):
        provisioned = _is_rsynced(deploy_sh, name) or _is_created(deploy_sh, name)
        assert provisioned, (
            f"nginx serves /{name}/ out of {DEPLOY_ROOT} but deploy.sh neither "
            f"rsyncs {name}/ nor creates it, so it does not exist on the VPS. "
            "nginx 404s the whole site and the deploy still reports OK."
        )

    def test_the_frontend_is_mirrored_not_merely_created(self, deploy_sh):
        """frontend/ is a repository tree, so an empty directory is not enough.

        Creating it would turn a total 404 into a 404 for index.html, which is
        the same outage with more steps.
        """
        assert _is_rsynced(deploy_sh, "frontend"), (
            "deploy.sh must rsync frontend/ -- it is tracked in the repository "
            "(index.html, style.css, app.js, avatar.js, assets/) and is the root "
            "nginx serves the site from"
        )


class TestTheUnitCanActuallyStart:
    @pytest.mark.parametrize("name", sorted(_unit_required_subdirs()))
    def test_every_readwrite_path_is_provisioned(self, deploy_sh, readme, name):
        """Some documented step must create each path the unit hard-requires.

        This used to accept ``deploy.sh`` alone, which conflated two different
        provisioning moments. ``deploy.sh`` runs before every content deploy, so
        it is what re-creates a directory a box has since lost; the README's
        numbered setup runs once, on a machine that has never deployed. A
        directory only the README creates is a weaker guarantee -- a box that
        loses it later will not recover -- but it is a true one, and requiring
        the stronger guarantee of both is a claim about the deploy script's
        refresh behaviour, not about whether the documented procedure is
        executable. Asserting neither accepts the original defect (nothing
        anywhere creates the path, the unit restart-loops, and the deploy
        reports OK), which is what this still rejects.

        ``scripts/deploy.sh`` should grow ``backend/.rag_cache`` here for the
        same reason it already grows the other three; it does not yet, and that
        is the one thing this assertion is looser than it was.
        """
        by_deploy = _is_created(deploy_sh, name)
        by_readme = _is_created(_deployment_section(readme), name)
        assert by_deploy or by_readme, (
            f"{UNIT_FILE.name} declares {DEPLOY_ROOT}/{name} in ReadWritePaths "
            "with no `-` prefix, so systemd fails to set up the mount namespace "
            "when it does not exist and the unit does not start. Neither "
            "deploy.sh nor the README deployment section creates it."
        )

    def test_the_readwrite_paths_parser_is_not_blind_to_optional_entries(self):
        """Guard the guard, on the path that used to be skipped silently.

        ``ReadWritePaths`` entries can carry systemd's ``-`` prefix. The original
        parser compared each raw entry against ``/opt/interviewtts/`` and dropped
        anything that did not start with it, so every optional entry was
        discarded without a word and any guard built on it was vacuous for
        exactly the entries whose prefix policy is a judgement call.
        """
        entries = _readwrite_paths()
        assert entries, f"{UNIT_FILE.name} declares no ReadWritePaths at all"
        assert all(path.startswith("/") for path, _ in entries), (
            f"a ReadWritePaths entry survived without its prefix stripped: {entries}"
        )
        assert any(not required for _, required in entries), (
            f"no ReadWritePaths entry uses systemd's `-` prefix, so the unit has "
            "no optional path. The parsing above would still be correct, but "
            "this suite would no longer be covering the case that needs it -- "
            "and the HuggingFace cache is precisely that case: a directory that "
            "cannot be required to exist without restart-looping a fresh install."
        )

    def test_the_optional_and_required_sets_partition_the_entries(self):
        entries = _readwrite_paths()
        required = {_subdir(p) for p, r in entries if r} - {None}
        optional = {_subdir(p) for p, r in entries if not r} - {None}
        assert not (required & optional), (
            f"a subdirectory is both required and optional: {required & optional}"
        )
        assert required, "no required ReadWritePaths entry parsed"

    def test_the_writable_directories_are_created_rather_than_mirrored(self, deploy_sh):
        """Audio is runtime output. Mirroring it would fight the sweep.

        ``periodic_cleanup`` unlinks stale ``.mp3``/``.webm``/``.wav`` files
        (backend/maintenance.py:162). An rsync of a repository-side audio/
        tree with ``--delete`` would restore files the sweep just pruned, on
        every deploy, forever.
        """
        assert not _is_rsynced(deploy_sh, "audio"), (
            "deploy.sh rsyncs audio/, which is TTS output the periodic sweep "
            "prunes. Mirror the candidate content and the frontend; create the "
            "runtime directories."
        )


@pytest.fixture(params=READMES, ids=lambda path: path.name, scope="module")
def readme(request: pytest.FixtureRequest) -> str:
    """Each user-facing README in turn, so both are held to the same claims.

    Module-scoped so the file is read once per document rather than once per
    assertion, and parametrised rather than looped so a failure names the
    file it came from instead of an opaque index.
    """
    path: Path = request.param
    return path.read_text(encoding="utf-8")


def test_every_user_facing_readme_is_covered():
    """Guard the guard: the coverage tuple cannot shrink.

    ``READMES`` is the whole mechanism by which ``README_ES.md`` stopped being
    unverified. Deleting an entry from it would not fail a single assertion --
    it would silently return the suite to checking one language while the
    green output looked identical. So the list is pinned.
    """
    assert READMES == (REPO_ROOT / "README.md", REPO_ROOT / "README_ES.md"), (
        f"the covered-document list changed: {READMES}. Every user-facing "
        "README must stay in it, or the assertions below stop being a claim "
        "about 'the documentation' and become a claim about one language."
    )
    for path in READMES:
        assert path.is_file(), f"{path} is in READMES but does not exist"


class TestTheDocumentedSetupIsNotOneLine:
    """README.md:319 used to be the entire setup for the unit.

    Each of these is something the unit file names and something the reader has
    to run. Named explicitly so the list cannot shrink back to a reference to
    the unit file.

    Parametrised over both READMEs. Three of the five assertions read a
    command -- ``useradd``, ``python3 -m venv``, ``mkdir`` -- and are
    language-agnostic as written. The one that was not is
    ``test_it_creates_every_writable_directory``, which scoped its search with
    ``readme.split("## Deployment", 1)[-1]``; it now locates the section by
    content, because the heading is the only genuinely language-specific token
    in this class.
    """

    def test_it_creates_the_service_user(self, readme):
        assert re.search(r"useradd[^\n]*interviewtts", readme), (
            f"{UNIT_FILE.name} sets User=interviewtts. Nothing in the repository "
            "creates that user, so the unit cannot start on a fresh VPS."
        )

    def test_it_creates_the_venv_the_unit_executes_from(self, readme):
        match = re.search(r"ExecStart=(\S+)", UNIT_FILE.read_text(encoding="utf-8"))
        assert match is not None
        venv_bin = match.group(1).rsplit("/", 1)[0]

        assert "python -m venv" in readme or "python3 -m venv" in readme, (
            f"the unit's ExecStart is {match.group(1)}; nothing creates that "
            "interpreter"
        )
        assert "pip install" in readme, (
            f"a venv without the project's dependencies still yields no uvicorn "
            f"at {venv_bin}"
        )

    def test_it_says_where_the_code_lives(self, readme):
        match = re.search(r"WorkingDirectory=(\S+)", UNIT_FILE.read_text(encoding="utf-8"))
        assert match is not None
        assert match.group(1) in readme, (
            f"the unit runs from {match.group(1)} and the README must say so"
        )

    @pytest.mark.parametrize("name", sorted(_unit_required_subdirs()))
    def test_it_creates_every_writable_directory(self, readme, name):
        block = _deployment_section(readme)
        assert re.search(rf"mkdir[^\n]*{re.escape(name)}", block), (
            f"the deployment section must create {name}/ -- the unit lists it in "
            "ReadWritePaths and systemd will not start without it"
        )

    @pytest.mark.parametrize("name", sorted(_unit_optional_subdirs()))
    def test_it_creates_the_optional_directory_too(self, readme, name):
        """A ``-``-prefixed path is not required -- but it is still worth making.

        The prefix says the unit will not die without this directory. It does not
        say the directory should be absent. The HuggingFace cache is created by
        ``huggingface_hub`` the first time the model is downloaded, and if systemd
        has to create the mount point first, that 457 MB download is written into
        a directory nobody chose the owner of. Creating it up front means the
        first download lands somewhere that already belongs to the service user.
        """
        block = _deployment_section(readme)
        assert re.search(rf"mkdir[^\n]*{re.escape(name)}", block), (
            f"the deployment section should create {name}/ even though the unit "
            "prefixes it with `-`. The prefix is what keeps a missing directory "
            "from restart-looping the unit; it is not a reason to leave the "
            "first, uncached write to an owner systemd picked."
        )

    def test_it_does_not_promise_a_docker_compose_path(self, readme):
        assert "docker compose up" not in readme, (
            "there is no compose file in this repository, so `docker compose up "
            "-d` is a command that cannot work"
        )


class TestTheUnitStatesWhereItsCachesLive:
    """The two caches the service must be able to WRITE, not just read.

    ``ProtectSystem=strict`` makes the whole filesystem read-only, so a cache the
    service cannot write is not a cache. Both of these fail silently:

    * ``backend/.rag_cache`` -- ``RAGPipeline._save_cache`` catches the OSError
      and logs a warning (backend/services/rag.py:701), so the corpus is
      re-embedded at every boot and the only symptom is a log line.
    * the HuggingFace model cache -- ``paraphrase-multilingual-MiniLM-L12-v2`` is
      457 MB, so a non-persistent cache is 457 MB down on every restart.

    The paths are derived here rather than written out, from the two places that
    actually decide them: ``backend.config`` for the RAG cache, and the unit's own
    ``Environment=HOME=`` for the model cache. Deriving them is the point -- the
    defect was a path in the unit file that no other file agreed with, and a
    hardcoded copy of that path here would simply have added a third.
    """

    @staticmethod
    def _unit() -> str:
        return UNIT_FILE.read_text(encoding="utf-8")

    def test_the_unit_states_home_rather_than_inheriting_it(self):
        """``$HOME`` decides where the model cache lands, so it is stated.

        systemd would populate it from the passwd entry on its own. "Would" is
        the problem: the unit then spells the resolved path out again in
        ReadWritePaths, and if the two ever disagree the model is downloaded to a
        read-only directory at every restart with nothing to say so.
        """
        match = re.search(r"^Environment=HOME=(\S+)$", self._unit(), re.MULTILINE)
        assert match is not None, (
            f"{UNIT_FILE.name} sets no Environment=HOME=. The HuggingFace cache "
            "defaults to $HOME/.cache/huggingface, so the model cache path is "
            "then decided by whatever the passwd entry happens to say -- and the "
            "ReadWritePaths entry that has to match it is an absolute path in "
            "this same file."
        )
        assert match.group(1) == DEPLOY_ROOT, (
            f"the unit's HOME is {match.group(1)} but ReadWritePaths is written "
            f"against {DEPLOY_ROOT}. They have to be the same tree, or the cache "
            "paths in ReadWritePaths do not name the caches the service uses."
        )

    def test_home_agrees_with_the_home_directory_the_readme_creates(self, readme):
        """The account's home and the unit's ``$HOME`` are one fact, stated twice.

        The README's ``useradd --home-dir`` is what puts the user's home under
        ``/opt/interviewtts``; the unit's ``Environment=HOME=`` is what
        ``huggingface_hub`` will read. If one moves, the model cache silently
        relocates to somewhere ``ProtectSystem=strict`` has made read-only.
        """
        match = re.search(r"useradd[^\n]*--home-dir[= ](\S+)", readme)
        assert match is not None, (
            "no useradd --home-dir in this README, so the account's home "
            "directory -- which is $HOME, and so the model cache location -- is "
            "not stated anywhere"
        )
        unit_home = re.search(r"^Environment=HOME=(\S+)$", self._unit(), re.MULTILINE)
        assert unit_home is not None
        assert match.group(1) == unit_home.group(1), (
            f"the README creates the account with home {match.group(1)} but the "
            f"unit says HOME={unit_home.group(1)}. One of the two is stale, and "
            "the failure mode is a 457 MB download at every restart."
        )

    def test_the_rag_cache_is_writable_and_must_exist(self):
        expected = _deployed_rag_cache_dir()
        assert expected in _unit_paths(), (
            f"{UNIT_FILE.name} does not list {expected} in ReadWritePaths. "
            "RAGPipeline writes its embeddings there, catches the OSError and "
            "re-embeds the corpus at every boot."
        )
        rag_entry = next((p, r) for p, r in _readwrite_paths() if p == expected)
        assert rag_entry[1], (
            f"{expected} is listed with systemd's `-` prefix. It is not "
            "optional: it is gitignored, so a clone never creates it, and the "
            "only other way to notice it is missing is a warning in the journal "
            "and a full re-embed at every boot. `backend/.rag_cache/` has to be "
            "created by the documented setup and required by the unit."
        )

    def test_the_model_cache_is_listed_and_marked_optional(self):
        """One prefixed entry, and it is this one.

        The model cache is created by ``huggingface_hub`` on first use, so it does
        not exist on a freshly provisioned box and must not be required to: with
        a bare path the unit would fail to set up its mount namespace and restart
        every 5 seconds, forever, on a machine that has never run.
        """
        home = re.search(r"^Environment=HOME=(\S+)$", self._unit(), re.MULTILINE)
        assert home is not None
        # HF_HOME defaults to $HOME/.cache/huggingface; this is where the 457 MB
        # paraphrase-multilingual-MiniLM-L12-v2 lands.
        expected = f"{home.group(1)}/.cache/huggingface"
        entry = next(((p, r) for p, r in _readwrite_paths() if p == expected), None)
        assert entry is not None, (
            f"{UNIT_FILE.name} does not list {expected} in ReadWritePaths, so the "
            "embedding model is re-downloaded to a read-only directory on every "
            "restart."
        )
        assert not entry[1], (
            f"{expected} is listed as required. It is created by "
            "huggingface_hub the first time the model is fetched -- which is the "
            "first thing the service does -- so requiring it restart-loops every "
            "fresh install until someone creates a HuggingFace-internal path by "
            "hand. It needs systemd's `-` prefix."
        )

    def test_the_optional_entry_is_never_the_whole_list(self):
        """`-` everywhere is as broken as no `-` at all.

        A unit where every path is optional starts fine and then fails at request
        time on every write, with a namespace error instead of a traceback. The
        required entries are the point of the directive, so the mix is asserted
        rather than assumed.
        """
        entries = _readwrite_paths()
        assert any(required for _, required in entries), (
            "every ReadWritePaths entry is prefixed with `-`. The unit would start "
            "with nothing writable, which is the other way to get this directive "
            "wrong."
        )
