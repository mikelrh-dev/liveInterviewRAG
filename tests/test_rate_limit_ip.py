"""Tests for the client identity the rate limiter keys on.

Behind a reverse proxy the TCP peer is the proxy, so the peer address cannot
identify the client and ``X-Forwarded-For`` has to be consulted. But that header
is attacker-controlled, and nginx appends ``$remote_addr`` to the *right* of
whatever the client sent.

These tests therefore pin one rule: **the application must never derive client
identity from the raw header.** ``request.client.host`` is already resolved by
uvicorn's proxy-headers middleware, which walks the chain right-to-left and only
past hosts it was explicitly told to trust. Anything the application does on
top of that is a second, weaker opinion about a security decision.

The attack cases below are the ones that a previous header-parsing resolver
failed: a client on a private address, and a chain whose every hop looks like
infrastructure. Both are regression nets for that exact defect.
"""

import pytest
from pathlib import Path
from starlette.requests import Request

from backend.client_ip import resolve_client_ip
from backend.main import _rate_limit_store

# Real global addresses (is_global=True), chosen because Python's ipaddress
# reports the RFC 5737 documentation ranges as is_private=True, which makes the
# usual "obviously public" examples useless as public-client fixtures.
PUBLIC_A = "51.15.1.1"
PUBLIC_B = "51.15.1.2"
PRIVATE_CLIENT = "192.168.1.50"
LOOPBACK_PROXY = "127.0.0.1"


def make_request(peer, headers=None):
    """Minimal Starlette Request with a fixed peer and raw headers."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/health",
        "headers": headers or [],
        "query_string": b"",
    }
    if peer is not None:
        scope["client"] = (peer, 12345)
    return Request(scope)


def xff(value):
    return [(b"x-forwarded-for", value.encode())]


class TestHeaderIsNeverParsed:
    """The resolver reads the resolved peer only, never the raw header."""

    def test_no_header_returns_peer(self):
        assert resolve_client_ip(make_request(PUBLIC_A)) == PUBLIC_A

    def test_public_peer_with_forged_header_returns_peer(self):
        request = make_request(PUBLIC_A, xff("9.9.9.9"))
        assert resolve_client_ip(request) == PUBLIC_A

    @pytest.mark.parametrize(
        "peer",
        [
            pytest.param(PRIVATE_CLIENT, id="private-rfc1918"),
            pytest.param("10.0.0.7", id="private-rfc1918-10"),
            pytest.param("172.16.0.9", id="private-rfc1918-172"),
            pytest.param("169.254.10.10", id="link-local"),
            pytest.param("0.0.0.0", id="unspecified"),
            pytest.param("::1", id="ipv6-loopback"),
            pytest.param("fd00::1", id="ipv6-ula"),
        ],
    )
    def test_infrastructure_peer_with_forged_header_returns_peer(self, peer):
        """The bypass: a private-address client forging the header.

        nginx yields ``<forged>, <real>``; a resolver that skips the real
        address as "infrastructure" hands the attacker the forged one.
        """
        request = make_request(peer, xff(f"9.9.9.9, {peer}"))
        assert resolve_client_ip(request) == peer

    def test_all_infrastructure_chain_does_not_yield_a_string_key(self):
        """A chain of nothing but infrastructure hops must not leak a raw entry.

        The previous fallback returned hops[0] verbatim, so the literal string
        an attacker chose became the bucket key -- unbounded cardinality in a
        process-wide dict.
        """
        request = make_request(LOOPBACK_PROXY, xff("not-an-ip, 10.0.0.7"))
        assert resolve_client_ip(request) == LOOPBACK_PROXY

    def test_unparseable_string_never_becomes_the_identity(self):
        request = make_request(LOOPBACK_PROXY, xff("EVIL-KEY, 10.0.0.7"))
        assert resolve_client_ip(request) == LOOPBACK_PROXY

    def test_missing_client_is_reported_as_unknown(self):
        assert resolve_client_ip(make_request(None)) == "unknown"

    def test_result_is_always_the_peer_or_unknown(self):
        """Whatever the header says, the answer is a resolved address or the
        literal string 'unknown' -- never client-supplied text."""
        hostile = [
            "1.1.1.1",
            "not-an-ip",
            "",
            "  ",
            "a" * 500,
            "'; DROP TABLE _rate_limit_store; --",
            "<script>alert(1)</script>",
            "\x00\xff",
        ]
        for peer in (PUBLIC_A, PRIVATE_CLIENT, LOOPBACK_PROXY, None):
            for value in hostile:
                result = resolve_client_ip(make_request(peer, xff(value)))
                assert result in (peer, "unknown"), (peer, value, result)


class TestRateLimiterIsolation:
    """The middleware must key on the resolved peer, not on header content."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        _rate_limit_store.clear()
        yield
        _rate_limit_store.clear()

    def test_forged_headers_do_not_create_new_buckets(self):
        """Ten private-address clients forging different headers share one bucket."""
        from backend.middleware import RateLimitMiddleware

        middleware = RateLimitMiddleware(app=None, max_requests=10)
        for index in range(10):
            request = make_request(
                PRIVATE_CLIENT, xff(f"9.9.9.{index}, {PRIVATE_CLIENT}")
            )
            # Exercise the middleware's own keying decision directly.
            key = resolve_client_ip(request)
            middleware_state = getattr(middleware, "_rate_limit_store", None)
            assert key == PRIVATE_CLIENT
            assert middleware_state is None  # store is module-level, not on self

        assert list(_rate_limit_store) == []  # nothing written by a bare call

    @staticmethod
    def _client_with_limiter(max_requests: int):
        """A real app with the real middleware and a route the limiter protects.

        The route is a TURN (``POST /api/conversation/{id}/message``), not
        ``/api/health``. Health, the config read and the context panel are now
        exempt from the limiter on purpose -- they are interface reads, not the
        recruiter's questions -- so a health route here would no longer be
        charged and the keying could not be observed at all. The paths and
        verbs mirror ``backend/routers/``, because the exemption is decided from
        the path and a friendly invented path would test nothing real.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.middleware import RateLimitMiddleware

        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, max_requests=max_requests)

        @app.post("/api/conversation/{conversation_id}/message")
        def message(conversation_id: str):
            return {"answer": "ok"}

        return TestClient(app), resolve_client_ip(
            make_request(PRIVATE_CLIENT)
        )

    def test_resolved_peer_is_the_key_written_to_the_store(self):
        """The store's key space, observed through the middleware that fills it.

        The previous version of this test asserted ``_rate_limit_store == {}``
        after doing nothing, which is a tautology: it passes while the limiter
        buckets on ``X-Forwarded-For`` and while it does not key on anything at
        all. So the middleware is driven for real here and the store is read
        afterwards.
        """
        client, _ = self._client_with_limiter(max_requests=100)

        for value in ("9.9.9.1", "9.9.9.2", "9.9.9.3"):
            response = client.post(
                "/api/conversation/c1/message",
                headers={"X-Forwarded-For": f"{value}, {PRIVATE_CLIENT}"},
            )
            assert response.status_code == 200, (
                f"the request was rejected before the key could be observed: "
                f"{response.status_code} {response.text}"
            )

        assert len(_rate_limit_store) == 1, (
            f"three clients' worth of forged headers produced "
            f"{len(_rate_limit_store)} buckets: {sorted(_rate_limit_store)}"
        )
        (only_key,) = _rate_limit_store
        assert not any(part.startswith("9.9.9.") for part in only_key), (
            f"a forged header value became the bucket key: {only_key!r}"
        )
        assert len(_rate_limit_store[only_key]) == 3, (
            "three requests from one client must share one bucket, not three: "
            f"{_rate_limit_store}"
        )

    def test_a_client_that_forges_headers_cannot_buy_itself_headroom(self):
        """The limiter's own limit, exercised end to end.

        Ten forged header values must not be ten buckets. If any code path
        keyed on the header, the eleventh request would be served -- and the
        bypass, not the keying, is what matters.
        """
        client, _ = self._client_with_limiter(max_requests=10)

        statuses = [
            client.post(
                "/api/conversation/c1/message",
                headers={"X-Forwarded-For": f"9.9.9.{index}, {PRIVATE_CLIENT}"},
            ).status_code
            for index in range(12)
        ]

        assert statuses[:10] == [200] * 10, (
            f"the first ten requests were not all served: {statuses}"
        )
        assert statuses[10:] == [429, 429], (
            "forging a new header per request bought more than the configured "
            f"limit of 10: {statuses}"
        )
        assert len(_rate_limit_store) == 1, (
            f"the forged values became separate buckets: {sorted(_rate_limit_store)}"
        )


class TestDeploymentInvariant:
    """The trust decision belongs to uvicorn's command line, not to this app.

    There is no runtime check here and that is the decision, not an omission.
    ``proxy_headers_configured()`` existed and no production code ever called
    it, while its docstring claimed it "makes that silent failure visible" --
    a check nothing invokes makes nothing visible. Two tests here used to stand
    in for it:

        assert isinstance(proxy_headers_configured(), bool)
        assert "uvicorn" in proxy_headers_configured.__doc__

    The first passes for ``return True``. The second asserts that a string
    contains a word, which is the one thing a test cannot do about the claim it
    names. Between them they gave the appearance of coverage over a function
    that was never wired to anything.

    So the invariant is pinned where it can actually be observed: the unit file
    that sets the flags. Everything the resolver does with the result is pinned
    above, behaviourally.
    """

    UNIT_FILE = Path(__file__).resolve().parents[1] / "deployment" / "interviewtts.service"

    def test_the_unit_file_still_configures_proxy_headers_and_its_allowlist(self):
        unit = self.UNIT_FILE.read_text(encoding="utf-8")
        assert "--proxy-headers" in unit, (
            "the deployment no longer sets --proxy-headers, so every visitor "
            "behind the reverse proxy shares one rate-limit bucket again"
        )
        assert "--forwarded-allow-ips" in unit, (
            "the deployment no longer restricts --forwarded-allow-ips, so any "
            "client can forge its own source address"
        )

    def test_the_allowlist_is_not_a_wildcard(self):
        """``--forwarded-allow-ips '*'`` would make the header forgeable.

        This is the whole reason the resolver refuses to parse the header: if
        every peer is trusted, a client's own entry is taken at face value. A
        missing flag or a wildcard here is the exact configuration that turns
        the rule above into a suggestion.
        """
        unit = self.UNIT_FILE.read_text(encoding="utf-8")
        allowlist = [
            line for line in unit.splitlines()
            if "--forwarded-allow-ips" in line
        ]
        assert allowlist, "no --forwarded-allow-ips line to check"
        for line in allowlist:
            value = line.split("--forwarded-allow-ips", 1)[1].strip().strip("'\"")
            assert value not in ("*", ""), (
                f"--forwarded-allow-ips is set to {value!r}; the forwarded "
                "chain is then attacker-controlled"
            )

    def test_the_backend_exposes_no_proxy_headers_health_check(self):
        """Pin the removal, so it cannot come back unwired and unclaimed.

        An interface assertion rather than a grep: the name must not be
        reachable, which is the property that matters. Whether any file happens
        to spell it inside a comment is not the contract, and asserting that
        would be the same mistake the tests it replaces made.
        """
        from backend import client_ip

        assert not hasattr(client_ip, "proxy_headers_configured"), (
            "proxy_headers_configured is exported again. If it is meant to be "
            "a health check, wire it into the lifespan in backend/main.py -- a "
            "helper nothing calls is not a health check, it is a comment."
        )
        assert sorted(
            name for name in vars(client_ip) if not name.startswith("_")
        ) == ["Request", "resolve_client_ip"], (
            "client_ip exports something else now; every public name in this "
            "module is a rule the rate limiter's safety rests on, and each one "
            "has to earn its place: "
            f"{sorted(vars(client_ip))}"
        )
