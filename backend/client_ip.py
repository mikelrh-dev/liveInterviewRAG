"""Client identity for rate limiting, across a reverse proxy.

The peer address is not the client address once a proxy sits in front, and
``X-Forwarded-For`` is attacker-controlled. The rule implemented here -- trust
the header only from a loopback peer, then take the rightmost hop that is not
itself infrastructure -- is security-relevant, so it lives in its own module
instead of inside a middleware class.
"""

import ipaddress

from fastapi import Request

# Address classes that identify an infrastructure hop rather than a real
# client, so a forwarded-for chain walks past them to reach the visitor.
_TRUSTED_HOP_PROPERTIES = (
    "is_loopback",
    "is_private",
    "is_link_local",
    "is_unspecified",
)


def _is_trusted_hop(candidate: str) -> bool:
    """True if the address is unparseable or is an infrastructure address.

    Unparseable input is treated as trusted so that a malformed or hostile
    entry can never be picked as the client identity.
    """
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return True
    return any(getattr(address, prop) for prop in _TRUSTED_HOP_PROPERTIES)


def resolve_client_ip(request: Request) -> str:
    """Resolve the real client IP, trusting X-Forwarded-For only from loopback.

    Behind a reverse proxy the direct peer is the proxy itself, so the peer
    address cannot identify the client. But X-Forwarded-For is attacker-
    controlled: honouring it unconditionally would let anyone bypass the
    rate limit by sending a random header. We therefore only read the header
    when the immediate peer is loopback, and we take the RIGHTMOST entry that
    is not itself a trusted proxy hop.
    """
    peer = request.client.host if request.client else "unknown"
    if not _is_trusted_hop(peer) or peer == "unknown":
        # Not behind a local proxy — the peer is the client. Any
        # X-Forwarded-For present is attacker-controlled and must be ignored.
        return peer

    forwarded = request.headers.get("X-Forwarded-For", "")
    if not forwarded.strip():
        return peer

    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if not hops:
        return peer

    # Right to left: the first hop that is not an infrastructure address is
    # the closest thing to the real client that a hop we do not control did
    # not overwrite. The visitor can forge entries to the *left* of this one.
    for hop in reversed(hops):
        if not _is_trusted_hop(hop):
            return hop
    return hops[0]
