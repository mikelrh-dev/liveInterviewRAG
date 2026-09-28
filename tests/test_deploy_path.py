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

REPO_ROOT = Path(__file__).resolve().parents[1]
NGINX_CONF = REPO_ROOT / "nginx" / "interview.conf"
DEPLOY_SH = REPO_ROOT / "scripts" / "deploy.sh"
UNIT_FILE = REPO_ROOT / "deployment" / "interviewtts.service"
README = REPO_ROOT / "README.md"

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


def _unit_required_subdirs() -> set[str]:
    """Directories the unit file needs to exist before it can start."""
    unit = UNIT_FILE.read_text(encoding="utf-8")
    prefix = DEPLOY_ROOT.rstrip("/") + "/"
    found = set()
    for directive in re.findall(r"^\s*ReadWritePaths=(.+)$", unit, re.MULTILINE):
        for path in directive.split():
            if path.startswith(prefix):
                rest = path[len(prefix) :].strip("/")
                if rest:
                    found.add(rest.split("/")[0])
    return found


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
    def test_every_readwrite_path_is_provisioned(self, deploy_sh, name):
        assert _is_created(deploy_sh, name), (
            f"{UNIT_FILE.name} declares {DEPLOY_ROOT}/{name} in ReadWritePaths but "
            f"deploy.sh never creates it. systemd fails to set up the namespace "
            "when a ReadWritePaths entry does not exist, so the unit does not "
            "start -- and the path is created by the app, not before it."
        )

    def test_the_writable_directories_are_created_rather_than_mirrored(self, deploy_sh):
        """Audio is runtime output. Mirroring it would fight the sweep.

        ``periodic_cleanup`` unlinks stale ``.mp3``/``.webm``/``.wav`` files
        (backend/maintenance.py:24-32). An rsync of a repository-side audio/
        tree with ``--delete`` would restore files the sweep just pruned, on
        every deploy, forever.
        """
        assert not _is_rsynced(deploy_sh, "audio"), (
            "deploy.sh rsyncs audio/, which is TTS output the periodic sweep "
            "prunes. Mirror the candidate content and the frontend; create the "
            "runtime directories."
        )


class TestTheDocumentedSetupIsNotOneLine:
    """README.md:319 used to be the entire setup for the unit.

    Each of these is something the unit file names and something the reader has
    to run. Named explicitly so the list cannot shrink back to a reference to
    the unit file.
    """

    @pytest.fixture(scope="class")
    def readme(self) -> str:
        return README.read_text(encoding="utf-8")

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
        block = readme.split("## Deployment", 1)[-1]
        assert re.search(rf"mkdir[^\n]*{re.escape(name)}", block), (
            f"the Deployment section must create {name}/ -- the unit lists it in "
            "ReadWritePaths and systemd will not start without it"
        )

    def test_it_does_not_promise_a_docker_compose_path(self, readme):
        assert "docker compose up" not in readme, (
            "there is no compose file in this repository, so `docker compose up "
            "-d` is a command that cannot work"
        )
