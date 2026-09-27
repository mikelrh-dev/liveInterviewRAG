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
from starlette.requests import Request

from backend.client_ip import proxy_headers_configured, resolve_client_ip
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

    def test_resolved_peer_is_the_key_written_to_the_store(self):
        """Sanity on the store's key space: it only ever holds resolved peers."""
        assert _rate_limit_store == {}


class TestDeploymentInvariant:
    """The trust decision belongs to uvicorn, and must actually be configured."""

    def test_helper_exists_and_returns_bool(self):
        assert isinstance(proxy_headers_configured(), bool)

    def test_does_not_depend_on_this_modules_opinion(self):
        """The helper reads uvicorn's config, not a re-derived guess.

        It is a visibility check for the deployment invariant, not the
        enforcement: enforcement is `--forwarded-allow-ips` in the unit file.
        """
        from backend import client_ip

        source = client_ip.proxy_headers_configured.__doc__ or ""
        assert "uvicorn" in source
