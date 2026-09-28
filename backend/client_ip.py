"""Client identity for rate limiting, across a reverse proxy.

The peer address is not the client address once a proxy sits in front, and
``X-Forwarded-For`` is attacker-controlled. The rule is security-relevant, so it
lives in its own module instead of inside a middleware class.

**This module does not parse ``X-Forwarded-For`` itself, and must not.**

A previous version walked the header here, trusting any peer that looked like
infrastructure (loopback, RFC1918, link-local, unspecified, or unparseable).
That was bypassable in production: nginx appends ``$remote_addr`` to the right
of the client-supplied value, so a client on a private address could prepend
an arbitrary entry, have its own real address skipped as "infrastructure", and
be handed a fresh rate-limit bucket per forged header. An all-infrastructure
chain made the literal string of the attacker's choosing the bucket key, which
is also unbounded in cardinality.

The trust decision is not ours to make in the application layer. uvicorn's
``--proxy-headers`` middleware already resolves ``scope['client']`` from the
forwarded chain, walking right-to-left and only past hosts it was explicitly
told to trust via ``--forwarded-allow-ips``. Its parser is stricter than a
hand-rolled one: an unparseable host is compared against the trusted literals
and therefore never treated as a client identity.

So: ``request.client.host`` is already the real client when uvicorn is
configured correctly, and the raw header must be ignored entirely.

That is a deployment invariant, not a preference. It holds only while
``--proxy-headers`` and a loopback-restricted ``--forwarded-allow-ips`` are both
present (see ``deployment/interviewtts.service``). Without ``--proxy-headers``
every visitor shares the proxy's bucket again; without the allowlist anyone can
forge their own source address.

There is deliberately NO runtime check for it here, and the reason is worth
stating rather than leaving to be discovered. A check existed --
``proxy_headers_configured()``, reading uvicorn's own middleware configuration --
and no production code ever called it. Its docstring claimed it "makes that
silent failure visible", which was false by construction: a check nothing
invokes makes nothing visible. It was removed rather than kept, because a
helper whose whole value is being run somewhere is a promise the code does not
keep.

The invariant is enforced where it is actually decided: the systemd unit's
command line. A check in the application can only ever report the flags uvicorn
already has, so it cannot fail in any way the unit file could not.
"""

from fastapi import Request


def resolve_client_ip(request: Request) -> str:
    """Return the client address the rate limiter should key on.

    Reads ``request.client.host``, which uvicorn's proxy-headers middleware has
    already rewritten from ``X-Forwarded-For`` when the request arrived through
    a trusted proxy. The raw header is deliberately never read here.
    """
    return request.client.host if request.client else "unknown"
