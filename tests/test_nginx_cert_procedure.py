"""The documented certificate step and the config that consumes it must name the
same file.

THE DEFECT
----------
The header of ``nginx/interview.conf`` told the reader to run::

    certbot certonly --standalone -d <your-domain>

and 30 lines later pointed ``ssl_certificate`` at::

    /etc/letsencrypt/live/interviewtts/fullchain.pem

Those are different paths. certbot derives its output directory from the first
``-d`` when ``--cert-name`` is absent, so the documented command writes a
certificate into ``live/<your-domain>/`` that this file never references -- and
nginx refuses to start with "cannot load certificate". A reader who followed the
document exactly produced a working certificate and a broken server, and the
error names a file they never asked for.

The order was the second half. ``--standalone`` binds port 80 itself, so it
fails if nginx is already listening there; step 2 of the same header started
nginx. As written the two steps could not both be true.

WHAT IS ASSERTED
----------------
Not "the word cert-name appears". The check extracts the directory certbot would
write to from the documented invocation, extracts the directory
``ssl_certificate`` points at, and requires them to be the same string. A
rewrite of the procedure that changes either side without the other fails here,
which is the only failure mode that matters.
"""

import re
from pathlib import Path

import pytest

NGINX_CONF = Path(__file__).resolve().parents[1] / "nginx" / "interview.conf"

#: The certificate directory this config's ``ssl_certificate`` names.
CONFIGURED_LIVE_DIR = "/etc/letsencrypt/live"

_CERTBOT_RE = re.compile(r"certbot\s+certonly\s+[^\n]*?(-d\s+\S+)")
_CERT_NAME_RE = re.compile(r"--cert-name\s+(\S+)")
_SSL_CERT_RE = re.compile(r"^\s*ssl_certificate\s+(\S+);", re.MULTILINE)


@pytest.fixture(scope="module")
def conf() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def _documented_certbot_invocations(conf: str) -> list[str]:
    """Every ``certbot certonly`` line in the file, comments included.

    Comments are included on purpose: this is a procedure written in prose, and
    an example nobody should follow is still an example a reader will follow.
    """
    return [
        line.strip().lstrip("#").strip()
        for line in conf.splitlines()
        if "certbot certonly" in line
    ]


def _live_dir_of(invocation: str) -> str | None:
    """The directory certbot writes to for this invocation, or None if unclear.

    Mirrors certbot's own rule: ``--cert-name`` wins, otherwise the directory is
    named after the first ``-d``. Returning None rather than asserting keeps the
    caller able to report "this invocation does not say" as its own failure.
    """
    named = _CERT_NAME_RE.search(invocation)
    if named:
        return f"{CONFIGURED_LIVE_DIR}/{named.group(1)}"

    domain = _CERTBOT_RE.search(invocation)
    if domain:
        return f"{CONFIGURED_LIVE_DIR}/{domain.group(1).lstrip('-').strip()}"
    return None


def _configured_live_dir(conf: str) -> str:
    match = _SSL_CERT_RE.search(conf)
    assert match is not None, (
        f"{NGINX_CONF.name} sets no ssl_certificate, so there is nothing for the "
        "documented procedure to agree with"
    )
    path = match.group(1)
    return path[: path.rindex("/")]


class TestTheDocumentedCommandWritesTheConfiguredFile:
    def test_there_is_a_certbot_step_to_check(self, conf):
        assert _documented_certbot_invocations(conf), (
            "the deployment header no longer shows how to obtain the "
            "certificate, so the procedure is incomplete rather than wrong"
        )

    @pytest.mark.parametrize(
        "invocation", _documented_certbot_invocations(Path(NGINX_CONF).read_text(encoding="utf-8"))
    )
    def test_the_documented_command_writes_where_the_config_reads(
        self, conf, invocation
    ):
        produced = _live_dir_of(invocation)
        assert produced is not None, (
            f"cannot tell what this invocation writes: {invocation!r}. It names "
            "neither --cert-name nor a -d domain, so its output path is a guess"
        )

        configured = _configured_live_dir(conf)
        assert produced == configured, (
            f"the documented command\n    {invocation}\nwrites to\n"
            f"    {produced}/\nbut ssl_certificate reads from\n"
            f"    {configured}/\nnginx will refuse to start: "
            "'cannot load certificate ... No such file or directory'. Add "
            f"--cert-name {configured.rsplit('/', 1)[-1]} to the invocation."
        )

    def test_the_procedure_explains_the_coupling_rather_than_asserting_it(self, conf):
        """A silent agreement is a coincidence that survives exactly one edit."""
        header = conf.split("server {", 1)[0]
        assert "--cert-name" in header, (
            "the header must name the flag that makes the two paths agree; "
            "without it the reader has to derive certbot's directory rule"
        )
        assert "renew" in header, (
            "the procedure must say that renewal re-issues to the same path, or "
            "a reader will assume it has to edit the config every 90 days"
        )


class TestTheOrderIsExecutableAsWritten:
    def test_the_standalone_precondition_is_stated(self, conf):
        """--standalone and a running nginx are mutually exclusive on port 80."""
        header = conf.split("server {", 1)[0]
        assert "--standalone" in header, "the standalone plugin is the one in use"
        assert re.search(r"port 80", header, re.IGNORECASE), (
            "the header must state the port-80 precondition. certbot's standalone "
            "plugin binds :80 to answer the challenge, so it fails with 'Address "
            "already in use' against a live nginx -- which is why step 2 used to "
            "contradict step 1"
        )

    def test_step_two_starts_nginx_rather_than_reloading_it(self, conf):
        """`systemctl reload` on a stopped unit does not start it.

        On a fresh VPS the certificate exists and nginx is installed but not
        running, which is the only state step 1 can be performed from. Telling
        the reader to `reload` at that point is a no-op that reads as success.
        """
        header = conf.split("server {", 1)[0]
        assert re.search(r"systemctl\s+enable\s+--now\s+nginx", header), (
            "step 2 must say `systemctl enable --now nginx`; `reload` cannot start "
            "a unit that was never started"
        )
        assert not re.search(r"^\s*#?\s*nginx -t && systemctl reload nginx", header), (
            "the old `nginx -t && systemctl reload nginx` step is still in the "
            "header and still describes a server that is already running"
        )
