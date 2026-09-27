"""Server-Sent Event formatting for the interview stream.

The envelope is defined in exactly one function so that the wire format has one
owner. Every event the backend emits travels through ``sse_format``, which is
also why ``tests/test_sse_contract.py`` can scan for a single call shape: a new
event type cannot be introduced without appearing in that scan.
"""

import json


def sse_format(event: str, data: dict) -> str:
    """Format as Server-Sent Event data line.

    Produces::

        data: {"event": "<event>", "data": <json>}\n\n
    """
    payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
    return f"data: {payload}\n\n"
