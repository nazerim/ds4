#!/usr/bin/env python3
"""Shared, authoritative KV store-reason codes for the on-disk header.

Source of truth is ds4_kvstore.h (enum ds4_kvstore_reason), mirrored by the string
mapper ds4_kvstore_reason_code() in ds4_kvstore.c. Verify before trusting this file:

    grep -n "DS4_KVSTORE_REASON_" ds4_kvstore.h

Why this exists: four analysis tools each carried their own hand-written map, and
all four were shifted by one from value 3 upward. The result was not cosmetic -
"turn" is not a real reason and never was, so anything reading reason=="turn" read a
label that no object carries, while real evict/shutdown/agent stores were renamed
under it. One tool used reason in a *filter* (the P2.1 reclaimable set), so a shift
silently corrupted its numbers, not just its names.

Import this instead of writing a map.
"""

REASON = {
    0: "unknown",
    1: "cold",
    2: "continued",
    3: "evict",
    4: "shutdown",
    5: "agent-system",
    6: "agent-session",
}

# Names the server can actually write into a header, for validation.
VALID = set(REASON.values())


def import_here():
    """Let a tool in tests/ import this module whatever the caller's cwd is."""
    import os
    import sys
    d = os.path.dirname(os.path.abspath(__file__))
    if d not in sys.path:
        sys.path.insert(0, d)


def name(code):
    """Label a header reason byte, never inventing a name for an unknown code."""
    return REASON.get(code, "code%d" % code)


if __name__ == "__main__":
    for k in sorted(REASON):
        print("%d = %s" % (k, REASON[k]))
