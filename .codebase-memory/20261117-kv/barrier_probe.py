#!/usr/bin/env python3
"""TEMPORARY instrumentation for the upstream tree's barrier -- NOT part of the patch.

Why this exists: the fork build proved the barrier executes in production (6/6
stores, `sync=` mean 4.05 ms), but it reported that through `kv_debug_enabled()`,
which does not exist in origin/main. So the ported barrier had never visibly run.
This prints each call's return value, errno and duration so a real store on the
upstream tree can be observed directly. Reverted immediately afterwards; the only
thing that must survive is the transcript of what it printed.

The rewrite is done by brace-matching the `if (ok) {` block that contains the
fsync call, because the compile-time `#error` guard sits between fsync and
F_FULLFSYNC -- a naive text splice around those two lines would drop it.
Semantics are preserved: fcntl is still only issued when ok is still true.

Usage (from the upstream worktree):
    python3 .codebase-memory/20261117-kv/barrier_probe.py dry   # show, write nothing
    python3 .codebase-memory/20261117-kv/barrier_probe.py apply
    python3 .codebase-memory/20261117-kv/barrier_probe.py revert
"""
import subprocess
import sys

PATH = "ds4_kvstore.c"


def head_source():
    return subprocess.run(["git", "show", f"HEAD:{PATH}"],
                          capture_output=True, text=True, check=True).stdout


def block_span(src):
    """(start, end) of the `if (ok) { ... }` that holds the fsync call."""
    i = src.index("if (fsync(fileno(fp))")
    start = src.rindex("if (ok) {", 0, i)
    depth, j = 0, start
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return start, j + 1
        j += 1
    raise AssertionError("unbalanced block")


def instrument(src):
    start, end = block_span(src)
    block = src[start:end]
    out = block
    out = out.replace(
        "if (ok) {\n",
        "if (ok) {\n"
        "        double probe_t0 = kv_now_sec();\n"
        "        int probe_r1 = -1, probe_e1 = 0, probe_r2 = -1, probe_e2 = 0;\n", 1)
    out = out.replace(
        "if (fsync(fileno(fp)) != 0) ok = false;",
        "probe_r1 = fsync(fileno(fp));\n        probe_e1 = errno;\n"
        "        if (probe_r1 != 0) ok = false;", 1)
    # Capture the ioctl result inside whatever condition wraps it, rather than
    # rewriting the condition: the surrounding text changes when the patch is
    # reviewed, and a probe that only matches one revision is useless the next day.
    call = "fcntl(fileno(fp), F_FULLFSYNC)"
    assert out.count(call) == 1, "F_FULLFSYNC call site not unique"
    out = out.replace(
        call,
        "(probe_r2 = fcntl(fileno(fp), F_FULLFSYNC), probe_e2 = errno, probe_r2)", 1)
    out = out[:-1].rstrip() + ("\n        fprintf(stderr, \"BARRIER-PROBE fsync=%d/%d "
                               "fullsync=%d/%d ms=%.2f\\n\", probe_r1, probe_e1, probe_r2, "
                               "probe_e2, (kv_now_sec() - probe_t0) * 1000.0);\n    }")
    assert "#error" in out, "the compile-time guard must survive instrumentation"
    return src[:start] + out + src[end:]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "dry"
    src = head_source()
    if mode == "revert":
        open(PATH, "w").write(src)
        print("reverted to HEAD")
        return 0
    new = instrument(src)
    if mode == "dry":
        start, end = block_span(src)
        print("--- resulting block ---")
        print(instrument(src)[start:start + (end - start) + 700])
        print("--- net lines added:", new.count("\n") - src.count("\n"), "---")
        return 0
    open(PATH, "w").write(new)
    print("instrumented (temporary; revert before committing anything)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
