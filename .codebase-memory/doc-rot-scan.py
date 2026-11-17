#!/usr/bin/env python3
"""Scan workstream docs for references that no longer resolve.

Reports every backticked filename in the project's documentation that cannot be
found in the repo or in the session temp dir. Output feeds
.codebase-memory/DOC-ROT-20261117.md -- regenerate that index with this script
rather than trusting its counts, which age as files are added and removed.

Usage:  python3 .codebase-memory/doc-rot-scan.py [--json]
Exit status is always 0; this is a report, not a gate.
"""
import glob
import json
import os
import re
import sys

# The session temp dir on this machine. Resolution roots are probed for existence,
# so a stale or absent path here is harmless -- it only stops temp files counting
# as "found".
TMP = "/private/var/folders/gp/hjkf5cnx3vg4pxxv0zbd1c8h0000gn/T/opencode"

ROOTS = [
    ".", ".codebase-memory", ".codebase-memory/20261117-kv",
    "docs", "tests", "trainer", "trainer/dspark", "log",
    "tools", "metal", "gguf", "scripts", TMP,
]

# References known to be intentional rather than rot.
ALLOWED_DEAD = {
    # This index's own trust table cites the two vanished snapshot files as
    # *examples* of doc rot; those hits are the point of the document.
    ".codebase-memory/20261117-kv/INDEX.md": {
        "ds4-live-argv.txt", "ds4-live-env.snapshot.txt",
    },
}

# The generated index quotes every dead reference it catalogues, so scanning it
# would report the catalogue as rot. Skip the output file itself.
SELF_EXCLUDE = ".codebase-memory/DOC-ROT-20261117.md"

IN_BACKTICKS = re.compile(r"`([^`\n]{2,90}?)`")
LOOKS_LIKE_FILE = re.compile(r"^[\w\-./]+\.(md|py|sh|json|log|txt|vec|inc|patch|csv)$")


def canonical(token: str) -> str:
    """Normalise the ways docs point at temp-dir files so they can be resolved.

    `$TMP/ds4-live-env.snapshot.txt` and the literal /private/var/... path are the
    same reference, but neither matches a bare-filename pattern as written. These
    are the highest-severity cases (executable recipes), so they must not be
    silently skipped by the token filter.
    """
    for prefix in ("$TMP/", "${TMP}/", TMP + "/"):
        if token.startswith(prefix):
            return token[len(prefix):]
    if token.startswith("/") and "/T/" in token:
        return os.path.basename(token)
    return token


def looks_like_path(token: str) -> bool:
    """A filename token, excluding prose with ellipses or spaces.

    `.../qsa_fast.py` is a sentence fragment pointing at another repo, not a
    resolvable path, and must not inflate the count.
    """
    if "..." in token or " " in token:
        return False
    return bool(LOOKS_LIKE_FILE.match(token))


def resolves(token: str) -> bool:
    if token.startswith(("/", "~")):
        return os.path.exists(os.path.expanduser(token))
    base = os.path.basename(token)
    return any(os.path.exists(os.path.join(root, token))
               or os.path.exists(os.path.join(root, base)) for root in ROOTS)


def scan() -> dict:
    docs = sorted(
        set(glob.glob(".codebase-memory/**/*.md", recursive=True)
            + glob.glob("PLAN-*.md")
            + ["DS4FORK.md", "AGENTS.md", "AGENT.md"])
        - {SELF_EXCLUDE}
    )
    found = {}
    for doc in docs:
        whitelisted = ALLOWED_DEAD.get(doc, set())
        with open(doc, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for match in IN_BACKTICKS.finditer(text):
            token = match.group(1).strip()
            # Accept either the whole backtick span or its trailing word, so
            # "see tools/foo.py" and "`tools/foo.py`" are both tested.
            for raw in (token, token.split()[-1] if " " in token else token):
                candidate = canonical(raw)
                if looks_like_path(candidate) and not resolves(candidate):
                    if candidate not in whitelisted:
                        found.setdefault(doc, set()).add(candidate)
                    break
    return {doc: sorted(refs) for doc, refs in sorted(found.items())}


OUT_OF_REPO_PREFIXES = ("omlx-", "pi-", "MBP-DS-")

# References whose absence actually breaks an operator recipe, not just a citation.
EXECUTABLE_CUES = ("ds4-live-env.snapshot.txt", "ds4-live-argv.txt")

# Curated additions to detection below. Some citations are embedded in prose or
# elided paths (`. /private/var/folders/gp/.../opencode/ds4-live-env...`) that the
# token filter cannot parse without becoming fragile in the other direction. A
# missed entry here means someone follows a dead recipe, so this list is explicit
# and hand-reviewed rather than fully derived. All four verified dead 2026-11-17.
KNOWN_SEVERITY_A = {
    ".codebase-memory/TODO-20261105.md",
    ".codebase-memory/RESUME-MERGE-CLOSEOUT-20261106.md",
    ".codebase-memory/RESUME-3C-20261106.md",
    ".codebase-memory/HANDOVER-DSPARK-LANE-20261105.md",
}


def markdown(results: dict) -> str:
    total = sum(len(v) for v in results.values())
    in_repo = {d: r for d, r in results.items()
               if not os.path.basename(d).startswith(OUT_OF_REPO_PREFIXES)}
    out_repo = {d: r for d, r in results.items() if d not in in_repo}
    sev_a = {d: r for d, r in results.items()
             if any(c in r for c in EXECUTABLE_CUES) or d in KNOWN_SEVERITY_A}
    for d in KNOWN_SEVERITY_A:      # curated entries may have nothing else detected
        sev_a.setdefault(d, [])
    L = ["# Doc-rot index -- dead references in project documentation", "",
         "Generated by `.codebase-memory/doc-rot-scan.py --markdown`. **Do not hand-edit**;",
         "regenerate it, and treat its counts as current only as of this run:",
         "",
         "```sh",
         "python3 .codebase-memory/doc-rot-scan.py --markdown "
         "> .codebase-memory/DOC-ROT-20261117.md",
         "```", "",
         f"Scanned {len(in_repo) + len(out_repo) + 0} docs; "
         f"**{len(in_repo)} carry {sum(len(v) for v in in_repo.values())} unresolvable "
         f"in-repo references** (plus {len(out_repo)} docs whose dead paths point at "
         f"*other* repositories, which is expected, not rot).", "",
         "## Why this exists", "",
         "Workstream docs accumulated references to scratch artifacts -- temp-dir snapshots,",
         "one-off probes, capture JSONs, rotated logs -- that were never committed and have",
         "since been deleted, mostly by OS temp cleanup. The dangerous case is not an unreadable",
         "citation but an executable recipe that silently no-ops or boots the wrong environment.",
         "Check a reference here before acting on one in an older doc.", ""]
    L += ["## Severity A -- dead path inside an executable recipe", ""]
    for d in sorted(sev_a):
        found_cues = [c for c in EXECUTABLE_CUES if c in sev_a[d]]
        cues = found_cues or list(EXECUTABLE_CUES)
        note = "" if found_cues else " (hand-curated: cited in prose the filter cannot parse)"
        L.append(f"- `{d.replace('.codebase-memory/', '')}` cites "
                 + ", ".join(f"`{c}`" for c in cues)
                 + " in boot/verify steps. **Those files no longer exist.**" + note)
    L += ["",
          "**Replacement:** the capture environment now lives in the launcher itself --",
          "`COLLECT_TRAIN_DUMP`, `COLLECT_PARITY_DIR`, `COLLECT_EXACT` (`ds4-server.sh:222-225`)",
          "plus `DSPARK_TD_*` at `:228-236` -- driven by `./ds4-server.sh restart-collect`.",
          "A plain `restart-qwen` runs with zero capture by design, which is also what",
          "`ds4-server.sh:218` warns about. No snapshot file is needed any more.", ""]
    L += ["## Severity B -- unresolvable in-repo citations, by document", ""]
    for d in sorted(in_repo, key=lambda x: -len(in_repo[x])):
        if d in sev_a:
            continue
        L.append(f"- **`{d.replace('.codebase-memory/', '')}`** ({len(in_repo[d])}): "
                 + ", ".join(f"`{r}`" for r in in_repo[d]))
    L += ["", "## Notes about other repositories (not rot)", "",
          f"{len(out_repo)} docs are recon of external projects (oMLX, Pi, a Mac-BP vision",
          "repro). Their dead paths were never in this repo; read them as external notes and",
          "do not chase the files here.", ""]
    for d in sorted(out_repo, key=lambda x: -len(out_repo[x])):
        L.append(f"- `{d.replace('.codebase-memory/', '')}` ({len(out_repo[d])})")
    L += ["", "## Reading the categories", "",
          "- `.py` / `.sh` probes -- one-off measurement scripts, never committed. Re-derive",
          "  from the doc's own prose; do not assume they still exist.",
          "- `.json` / `.csv` / `.vec` -- capture dumps, golden vectors, A/B arms. Volatile by",
          "  design; the conclusions live in the prose.",
          "- `.log` -- rotated logs. Current ones are in `log/`, older ones are gone.",
          "- `$TMP/...` -- session temp. Not durable: anything that matters must be committed.",
          "  See `.codebase-memory/20261117-kv/` for today's example of doing that.",
          "- `X.md` cross-doc citations -- genuinely missing documents: renamed, deleted, or",
          "  never written. Verify with `ls .codebase-memory/` before trusting the claim around it.",
          "",
          "## Known limits of this scan", "",
          f"- Only backticked tokens that look like a bare filename are tested ({total} raw hits",
          "  before filtering). An earlier naive pass reported 82 docs / 200+ refs by counting",
          "  prose such as 'in AGENTS.md' and ellipsis fragments like '.../qsa_fast.py'. Any",
          "  first-pass grep count in this repo should be assumed wrong until read.",
          "- The scanner excludes its own output file, and whitelists the two snapshot names in",
          "  `20261117-kv/INDEX.md`, where they are cited *as examples* of doc rot.",
          "- Absolute paths are tested on this machine only; on another host they may exist.",
          "",
          "## Supersession chains (newest is live)", "",
          "- DSpark lane: `DSPARK-STATE-20261029.md` -> `DSPARK-STATE-20261102.md` ->",
          "  `MORNING-20261106-MEMO.md` -> `RESUME-MERGE-CLOSEOUT-20261106.md` ->",
          "  `HANDOFF-DECODE-FIX-DSPARK-20261108.md`",
          "- KV / prefill dips: the `HANDOVER-*` series -> `PLAN-*` ->",
          "  `HANDOFF-20261117-PREFILL-DIPS.md` (current), with durable rules in `adr.md`",
          "  under the three ADRs dated 2026-11-17.", ""]
    return "\n".join(L)


def main() -> int:
    results = scan()
    total = sum(len(v) for v in results.values())
    if "--json" in sys.argv:
        print(json.dumps(results, indent=1))
    elif "--markdown" in sys.argv:
        print(markdown(results))
    else:
        print(f"{len(results)} documents carry {total} unresolvable references\n")
        for doc, refs in sorted(results.items(), key=lambda kv: -len(kv[1])):
            print(f"{doc.replace('.codebase-memory/', '')} ({len(refs)})")
            for ref in refs:
                print(f"    {ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
