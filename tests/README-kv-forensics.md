# KV forensics & session tooling (Sep 2026 checkpoint work)

Scripts born while diagnosing the production reload loops (65465 / 453568 /
275968 incidents). They lived in /tmp; /tmp got wiped entirely (Sep 6, along
with the old Others/*.gguf files) — which is why they live here now.

| script | what it's for |
|---|---|
| `gguf_tokenizer.py` | read tokenizer metadata from any GGUF (no deps): `dump LO HI` = id->token, `tokenize TEXT` = naive joyai BPE ids. Caveat: ids are per-gguf — TQ-era and current gguf have different maps. |
| `kv_forensics.sh POS` | miss lines from log/ds4.log + the decoded `token_window` from log/ds4.trace at first-mismatch position POS (`==` agree, `!=` = drift boundary). |
| `tokenizer_fp_probe.c` | print `ds4_engine_tokenizer_fingerprint()` for a gguf. Build line in the file header. Same-binary two runs MUST match (pointer-hash bug regression); different ggufs MUST differ. inspect_only is NOT enough (tokenizer uninit — probe refuses with vocab<=0). Full load: stop the server first. |
| `killer_watch.sh` | capture ps/log tail the moment the running server PID exits (unresolved external-SIGTERM events Sep 4-6). Run detached; logs to /tmp/killer-watch.log (recreate there if it matters). |

## Interpretation cheat-sheet (from the incidents)
- Two tokenizations of identical bytes at the same position = pre-tokenizer
  boundary/offset effect (e.g. `'`+`.*` vs `'.`+`*` at 65465), NOT corruption.
- Loop = same `common=` position on every turn + `disk` hit of the SAME file
  at the SAME frontier. Since 9d955ec this self-heals: log line
  `kv cache discarded reason=frontier-contradicted` then recovery.
- Equivalence ("stored ids == whole-text re-tokenize") is NOT a valid load
  test: sampled thinking/DSML tails legitimately re-tokenize differently —
  that's the bridge's premise. Engine-identity (fingerprint) + behavioral
  (loop guard) are the shipped discriminators.
- Fingerprint rejects (`reason=tokenizer-fingerprint`) = checkpoint from a
  different tokenizer build/vocab; the fallback chain walks down; cold rebuild
  rewrites fresh under the key. Migration burst cost once ≈ one long cold
  prefill.
- pi harness `reason=shutdown` saves are graceful; a missing one at a known
  death = external kill (see killer_watch).
- macOS never reports leaked-memory via ASan (no LeakSanitizer); unit recipe:
  see AGENTS.md / commit trailers (`-DDS4_SERVER_TEST` + ds4_test_core.o).

Not preserved (single-use; existed only in the wiped /tmp - recreatable from
the session transcript if ever needed again): tokfp_mut (APFS-clone byte-flip
fingerprint demo), reduce_test.c (vision KEEP matrix unit - superseded by live
config validation on #971), askuser probe pair (384k tool-loop replay),
tokvar.py (offset-variance demo - effect documented above).
