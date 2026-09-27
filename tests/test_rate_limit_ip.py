"""Tests for trusted-proxy client IP resolution used by the rate limiter.

Behind a reverse proxy the TCP peer is the proxy itself, so the peer address
cannot identify the client. But ``X-Forwarded-For`` is attacker-controlled:
honouring it unconditionally would let anyone bypass the rate limit by sending
a random header. The resolver therefore only reads the header when the
immediate peer is loopback, and takes the rightmost entry that is not itself a
trusted proxy hop.

Gotcha worth knowing before editing these tests: Python's ``ipaddress``
reports the RFC 5737 documentation ranges (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24) as ``is_private == True``. The "obviously public" values in
most proxy examples are therefore classified as *trusted hops* by this
resolver, so they are useless as public-client fixtures. The public addresses
used below are real global-space addresses.
"""

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

from backend.main import _rate_limit_store, resolve_client_ip

# Real global addresses (verified is_private=False, is_global=True).
PUBLIC_A = "51.15.1.1"
PUBLIC_B = "51.15.1.2"
PUBLIC_C = "70.41.3.18"


def make_request(peer, headers=None):
    """Build a minimal Starlette Request with a fixed peer and raw headers."""
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


class TestResolveClientIP:
    """Unit tests for the pure resolver function."""

    def test_direct_connection_without_forwarded_header_returns_peer(self):
        """No header at all — the peer is the only thing we can trust."""
        request = make_request(PUBLIC_A)
        assert resolve_client_ip(request) == PUBLIC_A

    def test_loopback_peer_with_single_public_forwarded_header(self):
        """Normal proxied case: loopback peer, one public client IP."""
        request = make_request(
            "127.0.0.1",
            [(b"x-forwarded-for", PUBLIC_A.encode())],
        )
        assert resolve_client_ip(request) == PUBLIC_A

    def test_non_loopback_peer_with_spoofed_header_returns_peer_not_header(self):
        """Anti-spoofing: a direct client cannot forge its own identity.

        A non-loopback peer means the request did NOT arrive through a local
        proxy, so any X-Forwarded-For on it is attacker-controlled. It must be
        ignored entirely, otherwise the rate limit is trivially bypassed by
        rotating a fake header.
        """
        request = make_request(
            PUBLIC_C,
            [(b"x-forwarded-for", PUBLIC_B.encode())],
        )
        resolved = resolve_client_ip(request)
        assert resolved == PUBLIC_C
        assert resolved != PUBLIC_B

    def test_loopback_peer_with_chain_returns_rightmost_non_trusted(self):
        """Walk the chain right-to-left and stop at the first real client."""
        request = make_request(
            "127.0.0.1",
            [(b"x-forwarded-for", f"{PUBLIC_A}, {PUBLIC_C}, 10.0.0.1".encode())],
        )
        assert resolve_client_ip(request) == PUBLIC_C

    def test_loopback_peer_with_public_client_behind_private_hop(self):
        """The canonical nginx shape: client, then a private proxy hop."""
        request = make_request(
            "127.0.0.1",
            [(b"x-forwarded-for", f"{PUBLIC_A}, 10.0.0.1".encode())],
        )
        assert resolve_client_ip(request) == PUBLIC_A

    def test_spec_example_chain_also_steps_over_rfc5737_doc_ranges(self):
        """Documents the RFC 5737 gotcha, in case the example is reused.

        The chain "203.0.113.5, 70.41.3.18, 10.0.0.1" looks like it should
        resolve to 203.0.113.5, but 203.0.113.0/24 is a documentation range
        that ``ipaddress`` marks private, so the walk correctly steps over it
        and lands on the genuinely global 70.41.3.18.
        """
        request = make_request(
            "127.0.0.1",
            [(b"x-forwarded-for", b"203.0.113.5, 70.41.3.18, 10.0.0.1")],
        )
        assert resolve_client_ip(request) == PUBLIC_C

    def test_loopback_peer_with_only_private_addresses_falls_back_leftmost(self):
        """All-trusted chain: return the leftmost entry, never None or ''."""
        request = make_request(
            "127.0.0.1",
            [(b"x-forwarded-for", b"10.0.0.7, 192.168.1.4, 127.0.0.1")],
        )
        resolved = resolve_client_ip(request)
        assert resolved == "10.0.0.7"
        assert resolved is not None
        assert resolved != ""

    def test_missing_client_returns_unknown_without_raising(self):
        """No peer and no header: degrade to 'unknown', never raise."""
        request = make_request(None)
        assert resolve_client_ip(request) == "unknown"

    def test_loopback_peer_with_empty_forwarded_header_returns_peer(self):
        """Empty header string is treated as absent."""
        request = make_request(
            "127.0.0.1",
            [(b"x-forwarded-for", b"")],
        )
        assert resolve_client_ip(request) == "127.0.0.1"

    def test_garbage_hop_is_never_selected_as_client(self):
        """An unparseable hop is treated as trusted, never as an identity."""
        request = make_request(
            "127.0.0.1",
            [(b"x-forwarded-for", f"{PUBLIC_A}, not-an-ip".encode())],
        )
        assert resolve_client_ip(request) == PUBLIC_A


class TestRateLimitBucketsPerForwardedIP:
    """Behavioural tests through the real middleware and the real store."""

    @pytest.fixture(autouse=True)
    def clear_rate_limits(self):
        """Clear the real rate limit store before each test."""
        _rate_limit_store.clear()
        yield
        _rate_limit_store.clear()

    @pytest.fixture
    def proxied_client(self):
        """TestClient presenting a loopback peer, i.e. the real nginx shape.

        The default TestClient peer is the literal string "testclient", which
        is not a parseable loopback address. Passing client=("127.0.0.1", ...)
        makes the test exercise the real proxied path where the header is
        actually honoured.
        """
        from fastapi.testclient import TestClient

        from backend.main import app

        return TestClient(app, client=("127.0.0.1", 50000))

    def test_different_forwarded_ips_get_separate_buckets(self, proxied_client):
        """Two forwarded IPs must not share one bucket."""
        for _ in range(10):
            response = proxied_client.get(
                "/api/health", headers={"X-Forwarded-For": PUBLIC_A}
            )
            assert response.status_code == 200

        # The first IP is now exhausted.
        blocked = proxied_client.get(
            "/api/health", headers={"X-Forwarded-For": PUBLIC_A}
        )
        assert blocked.status_code == 429

        # The second IP still has its full budget.
        allowed = proxied_client.get(
            "/api/health", headers={"X-Forwarded-For": PUBLIC_B}
        )
        assert allowed.status_code == 200

    def test_spoofed_header_does_not_create_a_new_bucket(self):
        """Anti-spoofing end-to-end: a direct client cannot rotate identity.

        The peer is a non-loopback address, so no local proxy is involved and
        every X-Forwarded-For on these requests is attacker-controlled. The
        limiter must key on the peer and ignore the header, so burning the
        budget with rotating fake values still exhausts the single peer bucket.
        """
        from fastapi.testclient import TestClient

        from backend.main import app

        direct = TestClient(app, client=(PUBLIC_C, 50000))

        for i in range(10):
            response = direct.get(
                "/api/health", headers={"X-Forwarded-For": f"{PUBLIC_A}{i}"}
            )
            assert response.status_code == 200

        blocked = direct.get(
            "/api/health", headers={"X-Forwarded-For": f"{PUBLIC_B}9"}
        )
        assert blocked.status_code == 429

        # Every rotated header was ignored: no fake bucket was ever created.
        assert list(_rate_limit_store.keys()) == [PUBLIC_C]

    def test_rate_limit_store_is_keyed_by_forwarded_ip(self, proxied_client):
        """The store contains the forwarded IP, not the loopback peer."""
        proxied_client.get("/api/health", headers={"X-Forwarded-For": PUBLIC_A})
        assert PUBLIC_A in _rate_limit_store
        assert "127.0.0.1" not in _rate_limit_store

    def test_uppercase_header_name_is_honoured_on_the_wire(self, proxied_client):
        """Header names are case-insensitive on the wire.

        Checked through the real ASGI stack rather than a hand-built scope:
        Starlette does not normalise names inside a raw header list, so only a
        real request proves the lookup works for a client that capitalises the
        header differently.
        """
        proxied_client.get("/api/health", headers={"X-FORWARDED-FOR": PUBLIC_C})
        assert PUBLIC_C in _rate_limit_store
