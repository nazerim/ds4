# ds4 KV "same delta checkpoint written twice" — forensics (read-only)

## Verdict

**BENIGN.** The store path never wrote the same sha twice; what looks like a double write
is the *same logical frontier* re-stored under a *different* sha, because the re-rendered
prompt text differs by tens of bytes between walks. The premise's date is also wrong: no
KV store event exists at 2026-09-27 18:38.

## Date correction (premise error)

Log prefixes are real wall clock: `ds4_server.c:10061-10063` does `localtime_r` +
`strftime("%m%d %H:%M:%S")`. The file *named* `ds4.log.20260927-221523` contains a **09-21**
run (all 100 stored lines prefixed `0921`); it was merely *rotated* on 09-27 22:15, so the
"18:38" was read off the rotation filename. On the live volume **nothing was created
between 09-27 18:24:15 and 18:51:47**.

## Evidence

Apparent pair (`ds4.log.20260927-221523`, content date 09-21):

    :2414  0921 18:38:01 kv cache stored tokens=311296 trimmed=0 reason=continued key=token-text size=4110.16 MiB save=1247.5 ms
    :2945  0921 18:59:34 kv cache stored tokens=311296 trimmed=0 reason=continued key=token-text size=4110.16 MiB save=1292.0 ms

Identical `size` is **not** evidence of identical sha: for a full (non-delta) checkpoint
size is a deterministic function of `tokens`. That window shows ≥3 resident conversations
(`conv=13786436099114872233`, `34271614622330062`, `12385926021164859580` in the
interleaved `kv cache evicted` lines), so two conversations reaching the same 8192-grid
boundary is expected.

The reproducible pattern (`ds4-qwen.log`, 09-28) — two **slots** each re-walking one
ladder off the same disk anchor:

    0928 17:25:09 slot 1: request reuses nothing; evicting checkpoint (106130 tokens, idle 1 s, protected)
    0928 17:25:11 kv cache hit text tokens=40960 ... file=.../e86e6f3857a8a467bfe5208c4a6c7f02b548d36b.kv
    0928 17:26:04 kv cache stored tokens=98304 ... delta=81920..98304 size=606.48 MiB save=173.1 ms
    0928 17:32:13 slot 0: request reuses nothing; evicting checkpoint (416088 tokens, idle 1443 s, protected)
    0928 17:32:23 kv cache hit text tokens=40960 ... file=.../e86e6f3857a8a467bfe5208c4a6c7f02b548d36b.kv
    0928 17:33:16 kv cache stored tokens=98304 ... delta=81920..98304 size=606.48 MiB save=168.4 ms

**Decisive on-disk proof** (headers only, first 120 B of each of 202 files in
`/Volumes/FireCuda520/ds4-kv-qwen`): grouping by `(conv_id, tokens, delta_from)` gives
**23 groups with ≥2 files, and 0 of 23 have identical text+trailer length**. Example —
`conv=0x69b26bf37e15c60e tokens=98304 delta_from=81920`, all three with identical
`payload_bytes=635586652` but different text+trailer, hence different sha and file:

    adaced2b6ced... created 09-28 10:16:54  txt+trailer=351852  parent=7ffd14ffd7f8
    934e6438fc25... created 09-28 17:26:04  txt+trailer=351907  parent=36740eedd50a
    cac3e069793a... created 09-28 17:33:16  txt+trailer=351761  parent=581cb223eea7

Length differs ⇒ bytes differ ⇒ sha1 differs ⇒ the sha-exact dedup *correctly* cannot
fire. `conv_id` is a **coarse** identity (`ds4_kvstore.c:338-352`, `ds4_kvstore.h:16`):
sha1 of only the first 131072 text bytes XORed with model_fp — equal `conv_id` does
**not** imply equal text.

## Occurrence counts (all logs: 481 `kv cache stored` lines, 98 with `delta=`)

- **Same-sha double writes proven: 0.**
- Same logical frontier stored ≥2× (disk ground truth): **23 groups / 29 redundant files
  / 16.96 GiB = 3.3 % of the 507 GiB volume.**
- Log-level look-alikes (same key label + tokens + delta range): **28 groups**; gap
  ≤120 s: **7 groups**, minimum gap **34 s**; tightest full-ladder replay is
  09-27 22:56:12→22:57:37 (**85 s**, `ds4-qwen.log.20260927-230801`).
- `key=token-text` is a *label*, not the key — grouping logs on it conflates conversations,
  which is exactly how this came to look like a double write.

## Code path — `ds4_kvstore_store_live_prefix_text` (`ds4_kvstore.c:2070`)

1. `:2140-2143` — `sha = sha1(text)`, `path = path_for_sha(sha)`. The sha is of the
   rendered text only, so any re-render drift yields a new file.
2. `:2146-2152` — **dedup gate** `ds4_kvstore_existing_compatible`: touch, rewrite
   trailer, `return true`. Emits **no** `kv cache stored` line, so every logged store is
   a genuinely new (or freshly unlinked) file.
3. `:2159-2181` — delta gate (`kv_delta_enabled` && `kv_delta_reason_chainable:559` &&
   len≥4 && `ds4_session_supports_delta`), then `kv_cache_refresh` + `kv_store_pick_parent:1804`.
4. `:2183-2196` — staging failure falls back to a **full** checkpoint: a *different* file
   (delta_from=0), logging `delta staging failed`. Not a re-write.
5. `:2248` temp `"<sha>.kv.tmp.<pid>"`; `:2312-2331` write header+text+payload+trailer,
   fflush, fclose; `:2333` `rename(tmp, path)`; `:2364-2381` the stored log line.
6. `:2418-2433` — `maybe_store_continued` then `ds4_kvstore_sweep_small_dense_divergents`,
   whose comment states its job is stopping re-render churn accumulating duplicates.

## Atomicity and dedup — direct answers

- **Payload writes: ATOMIC** — temp + `rename()` (`:2248`, `:2333`). Crash orphans are
  reaped by the `.kv.tmp.*` scan (`:1463-1476`), which can never match a live `<sha>.kv`.
- **Header touch: NOT atomic** — `ds4_kvstore_touch_file:687` opens `r+b` and rewrites the
  header in place; a crash mid-write can tear a good checkpoint. This is also why 98 of
  202 files have `mtime > created_at`, which reads like "written twice" but is only a
  hits/last_used bump (size and `created_at` unchanged).
- **Same-sha stores ARE deduped today** (`:2146`). The *only* same-sha rewrite path is
  `ds4_kvstore_existing_compatible:1963-1998`, which **unlinks** the file (`:1988-1993`)
  when incompatible — including the `e.ctx_size <= ctx_size` test. Two slots with
  different ctx_size storing the same text could ping-pong unlink+rewrite; not observed
  here (all 202 files carry `ctx_size=524288`).

## Is a dedupe still worth adding?

The sha dedupe already exists and works, so a same-sha check would be a no-op. A
`(conv_id, tokens, delta_from)` dedupe would be **wrong**: the texts genuinely differ and
the newer rendering is what future prompts will match, so skipping it would poison hit
rate. The correct fix is upstream — make the re-render deterministic (already tracked by
`220ef50`, `6ded49e`, `f32db2d`). Cost of doing nothing is bounded and self-healing:
3.3 % of budget, reclaimed by `sweep_small_dense_divergents` + LRU. Two small real
improvements this did surface: (1) make `ds4_kvstore_touch_file` atomic, since it is the
only non-atomic write in the store path; (2) log the store sha (first 8 hex) on the
`kv cache stored` line, because `key=token-text` alone cannot distinguish conversations.

## Could not determine

- The 09-21 run wrote to `/tmp/ds4-kv`, now gone, so the shas of the 18:38:01 / 18:59:34
  pair cannot be checked. I cannot fully exclude a same-sha rewrite there, but the code
  requires a prior unlink and ≥3 conversations were resident.
- No surviving log covers 09-27 17:50–18:52 (`ds4.log.20260927-221523` content is 09-21;
  `ds4-qwen.log.20260927-230801` starts at 0927 22:xx), so the log lines for the 09-27
  18:24 / 18:51 writes are unavailable. `ds4-qwen.trace` covers only 09-28 16:09+ and
  contains zero `kv cache stored` lines.
- I proved duplicate pairs differ in text *length* but did not diff the blobs (headers-only
  per constraint), so the drift site — tool-replay render vs. prompt head — is inferred
  from tracked commits, not directly observed.
