#!/usr/bin/env python3
"""Static call-path audit (v2): brace-depth state machine attribution."""
import re

FILES = ["/Users/naz/Projects/ds4/ds4_metal.m", "/Users/naz/Projects/ds4/ds4.c"]
BOOKKEEP = re.compile(r"(stream_expert_cache_note_selected_hotness|stream_expert_cache_note_frequency_hotness|stream_expert_cache_note_route_hotness|stream_expert_cache_note_token|moe_selected_hotlist_record|cache_seed_selected)\s*\(")
FNNAME = re.compile(r"([A-Za-z_][A-Za-z0-9_]{3,})\s*\(")

def regions(path):
    """Return list of (fnname, start, end) for depth 0->1 transitions preceded by a call-looking header."""
    src = open(path, errors="ignore").read().splitlines()
    out, depth, cur, lasthdr = [], 0, None, ""
    for num, raw in enumerate(src, 1):
        line = raw.strip()
        if depth == 0:
            if "{" in raw:
                hdr = raw if line != "{" else lasthdr
                m = None
                for cand in ([raw] + ([lasthdr] if line == "{" else [])):
                    m = FNNAME.search(cand.split("{")[0])
                    if m: break
                if m and not line.startswith(("=", "for", "if", "while", "switch")) and ";" not in raw.split("{")[0]:
                    name = m.group(1)
                    # static bool-ish definitions we care about are functions; accept and filter by name later
                    cur = [name, num, num]
                depth += raw.count("{") - raw.count("}")
                if cur and depth > 0: pass
            else:
                if line and not line.startswith(("//", "/*", "*", "#")):
                    lasthdr = raw
                depth += raw.count("{") - raw.count("}")  # keep balanced-struct noise at 0
            if cur and depth == 0:
                cur[2] = num; out.append(tuple(cur)); cur = None
            elif cur:
                cur[2] = num
        else:
            depth += raw.count("{") - raw.count("}")
            if depth == 0 and cur:
                cur[2] = num; out.append(tuple(cur)); cur = None
    return src, out

report = []
for path in FILES:
    src, regs = regions(path)
    tag = path.split("/")[-1]
    report.append(f"=== {tag}: {len(regs)} regions parsed ===")
    def enclosing(lineno):
        best = None
        for name, s, e in regs:
            if s <= lineno <= e:
                best = name
        return best
    # known function name filters
    SKIP = {"fprintf", "snprintf", "free", "malloc", "memset", "memcpy", "getenv", "strcmp", "strlen", "fclose", "fopen", "qsort"}
    for num, line in enumerate(src, 1):
        if BOOKKEEP.search(line):
            fn = enclosing(num)
            if fn in SKIP or fn is None:
                report.append(f"{tag}:{num} UNATTRIB/short: {line.strip()[:80]}")
            else:
                # skip the definition lines of the note functions themselves (self-attribution)
                if BOOKKEEP.search(line) and re.search(r"^(?:static\s+)?[A-Za-z_0-9 \t\*]*\b" + BOOKKEEP.search(line).group(1).rstrip(r"\\(").replace("(?",""), line):
                    pass
                report.append(f"{tag}:{num} in [{fn}] : {line.strip()[:85]}")
    for pat in ["ds4_gpu_routed_moe_batch_owned_tensor", "ds4_gpu_routed_moe_batch_tensor", "ds4_gpu_routed_moe_one_tensor"]:
        rx = re.compile(r"\b" + pat + r"\s*\(")
        for num, line in enumerate(src, 1):
            if rx.search(line):
                fn = enclosing(num)
                is_def = fn and fn == pat
                report.append(f"{tag}:{num} {'DEF' if is_def else 'call['+str(fn)+']'} :: {pat}")
open("/tmp/ds41_static_audit.txt", "w").write("\n".join(report) + "\n")
print(f"lines: {len(report)}")
for l in report[:14]: print(l)
