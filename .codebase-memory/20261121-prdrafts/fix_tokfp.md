# kvstore restamp-laundering fix (PR #1020 holes 1+2) — DONE

status: fix implemented, tested RED->GREEN, builds clean; nothing committed (per constraints); engine untouched
tree: /Users/naz/Projects/ds4 @ 5c55542 + uncommitted changes (ds4_kvstore.c, ds4_kvstore.h, ds4_server.c, new untracked verify script)
note: the modified .md files in git status belong to the concurrent docs agent, not this task.

## 1. Chain verified (brief line numbers were stale; mechanism real, actual lines given)
- Compat shortcut ds4_kvstore.c:2972 (now 3015) ds4_kvstore_existing_compatible(): reuse from model_id/model_fp/quant/ctx/text-sha ONLY, zero fingerprint consultation. CONFIRMED (0 tokfp refs pre-fix).
- Store path ds4_kvstore.c:3210: compat true -> stat_reused++ -> ds4_kvstore_rewrite_trailer() at 3217 -> return. Fresh rows never staged. CONFIRMED.
- Restamp ds4_kvstore.c:3052 rewrite_trailer: ftruncate trailer at payload end, hooks->write (stamps CURRENT fp), header rewritten with hdr.ext_flags | hooks->ext_flag. CONFIRMED.
- Server hooks ds4_server.c:12633 (write cb stamps ds4_engine_tokenizer_fingerprint), 12718 (ext_flag = KV_EXT_TOKFP|KV_EXT_TOOL_MAP). CONFIRMED.
- Load ds4_kvstore.c:3780 rejects mismatch via parser kv_file_tokfp_ok (2739). Laundering sequence fully confirmed.
- Hole 2: kv_file_tokfp_ok returns ok=true/present=false on missing/unreadable TKF section; load site ignored present -> TOKFP-claiming file with unreadable stamp loaded unvalidated. CONFIRMED (different code shape than brief's have_fp quote, identical semantics).

## 2. Fail-open -> fail-closed decision (hole 2)
Made CONSISTENT BY FAILING CLOSED on the CLAIM, on both sides:
  verdict = parser-match AND NOT (TOKFP-bit-claimed AND stamp-section-unreadable).
- Why: the TOKFP bit is set only by our own writers, which always emit the section; a header claiming a stamp the file cannot show means torn/rotted/tampered envelope. Codebase doctrine already drops unverifiable envelopes (rewrite_trailer failure -> unlink; corrupt text ref -> drop). Accepting claim-without-proof reproduces the laundering shape exactly: header's own word shielding payload from validation.
- Availability semantics preserved: UNCLAIMED legacy files (bit clear, no section) keep trusted behavior everywhere — load, compat, chain, pick_parent. The delta-chain link rule (present+match required) is untouched (it was already the strict side).
- Cost of the choice is one re-prefill+replace per already-inconsistent file; such files are already broken by definition.

## 3. The fix (contained; no public signature broken)
- ONE shared definition of "what does this file's stamp say": kv_file_tokfp_verdict_ok(match, present, ext_flags) over the existing single parser kv_file_tokfp_ok; exposed as kv_file_tokfp_gate(fp-positioned) and kv_file_path_tokfp_ok(path). No second byte parser anywhere.
- Compat function factored into static kv_cache_existing_compatible(kc, engine, ...); the gate joins the compatible expression before touch/unlink decisions, so a stale-stamp file takes the incompatible branch: replaced (unlink unless M-4 dependents; then in-place full-store overwrite), never reused-and-restamped. Rejected files fall through to the full store, which writes fresh rows with the fresh stamp.
- ds4_kvstore_existing_compatible keeps its signature (engine NULL -> current fp 0 -> unclaimed files unchanged; fail closed for claims) so ds4_server.c tests and tests/kv_policy_harness.c compile and behave identically; new ds4_kvstore_existing_compatible_for_engine(kc, engine, ...) is used by the production store path.
- Load site (3861) now uses the same gate incl. the claim rule.
- DS4_TOKENIZER_FP_PROBES is a static const array, NOT env-tunable, and the fp is cached in the engine. The existing test mechanism used instead: fingerprint(NULL-engine)==0 (already relied upon by the write cb's cx->s ? ... : 0), with tests controlling the file side via stub TKF bytes. No production hook added.

## 4. RED -> GREEN transcript (negative control done as required: test built+run BEFORE the source fix)
### RED — unpatched ds4_kvstore.c, test-only diff in ds4_server.c:
ds4_server.c:27621: assertion failed: !ds4_kvstore_existing_compatible(&kc, path, sha, text, tl, 0, 2, 32768)
ds4_server.c:27622: assertion failed: access(path, F_OK) != 0
ds4_server.c:27634: assertion failed: !ds4_kvstore_existing_compatible(&kc, path, sha, text, tl, 0, 2, 32768)
ds4_server.c:27635: assertion failed: access(path, F_OK) != 0
ds4_server.c:27647: assertion failed: !ds4_kvstore_existing_compatible(&kc, path, sha, text, tl, 0, 2, 32768)
ds4_server.c:27648: assertion failed: access(path, F_OK) != 0
ds4-server tests: 6 failure(s)
  (6 failures = cases 1/3/5 x2 asserts each; positive cases 2/4 silent-pass => failures are attributable solely to the missing gate)

### GREEN — same test, patched kvstore:
1120 23:41:27 ds4-server: structural syntax inside message content in 1 message(s) first_role=user - rendered verbatim, so it becomes real structure to the model
1120 23:41:27 ds4-server: divergence anchor stored tokens=442903 (target 442343)
1120 23:41:27 ds4-server: divergence anchor stored tokens=458752 (target 450000)
ds4-server tests: ok
  rc=0

### Other runs
- make ds4-server: rc=0, clean compile/link of new binary on disk (running engine unaffected; its image is the old inode).
- tests/kv_policy_harness.c: -fsyntax-only rc=0 (API compatible; its files carry ext_flags=0 and never claim TOKFP, so the gate cannot change its verdicts; its g6 hook uses TOOL_MAP only).

## 5. Behavioral cost
Steady state: unchanged. A same-text store now pays one extra open+trailer read per
reuse decision (local, ~26 bytes).
After a tokenizer-behaviour change (or vocab/merge-file edit), per affected prompt text:
- load rejects the stale stamped file (already happened pre-fix) -> one re-prefill;
- NEW: the following store also rejects the shortcut -> full fresh checkpoint written
  (payload staging + write that the bug had skipped). Once per file; the next reuse
  sees matching stamps and is the cheap path again.
- fail-closed claim rule additionally converts already-torn stamped files (TOKFP bit,
  unreadable section: trailer-rewrite survivors, disk rot) from "load unvalidated" to
  "reject + replace": one extra re-prefill per such file, which is the correctness the
  guard was built for.
Trigger frequency in production: only on tokenizer-behaviour changes and stamp-envelope
damage. Not per-request.

## 6. Needs a production window / could not verify
- End-to-end laundering sequence with a real engine (real tokenizer change + restart):
  cannot be simulated without stopping production. The prepared script
  .codebase-memory/20261118-kvstorm/verify_tokfp_laundering_fix.sh (syntax-checked, NOT
  run; never stops the engine) brackets the deploy: before/after greedy cache-ON parity
  (temperature 0, thinking off, 76k-anchor prompts from the parity run) + live
  fingerprint-corruption proof (snapshot inode+stamp -> corrupt the 8 TKF payload bytes
  of files embedding our sentinel doc -> re-request -> expect replacement by
  temp+rename [inode change]; "LAUNDERED" verdict == same inode + stamp restored ==
  bug live). Needs KVDIR=<prod --kv-disk-dir>.
- ./ds4_test (bare and --server): NOT run — requires the engine stopped (forbidden).
- tests/kv_policy_harness binary NOT built/run (owned by `make test`, which also runs
  ./ds4_test). Compile-compatibility proven by syntax-only check; behavior unchanged by
  construction (files never claim TOKFP).
- Note observed mid-session: production engine PID is now 29866, not 65953 (another
  agent restarted it); I never ran any mutating ds4-server.sh subcommand (one
  read-only `status` slipped through a combined command; it only printed the PID).

## 6b. Hand-back command if you want the engine-suite coverage later
  ./ds4-server.sh stop && make test-server-logic && make test; ./ds4-server.sh start-qwen
  (that is the engine-stopped window; nothing in this task requires it — the fix's
   suite, ds4_server_test, is already green with the engine running.)

## 7. Diff (git diff -- ds4_kvstore.c ds4_kvstore.h ds4_server.c)
```
diff --git a/ds4_kvstore.c b/ds4_kvstore.c
index b7fc59e..f24dc81 100644
--- a/ds4_kvstore.c
+++ b/ds4_kvstore.c
@@ -2761,6 +2761,49 @@ static bool kv_file_tokfp_ok(FILE *fp, uint64_t payload_bytes,
     return ok;
 }
 
+/* The one shared verdict over the stamp parser's findings, used by BOTH the
+ * load path and the store-reuse shortcut so the two can never define
+ * "what does this file's stamp say" differently (the restamp-laundering
+ * class: the shortcut consulted none of this at all).  Rules:
+ *   - a TKF section that mismatches the engine's current fingerprint
+ *     rejects, claimed or not (parser verdict);
+ *   - fail-closed on the claim: the header's TOKFP bit is set only by
+ *     writers that also emit the section, so a claim without a readable
+ *     section means a torn/rotted/tampered envelope and cannot prove the
+ *     payload.  Unclaimed legacy files keep their trusted behavior.
+ * current fingerprint is 0 when engine is NULL (no model in unit tests). */
+static bool kv_file_tokfp_verdict_ok(uint8_t ext_flags, bool match, bool present) {
+    if (!match) return false;
+    if ((ext_flags & DS4_KVSTORE_EXT_TOKFP) != 0 && !present) return false;
+    return true;
+}
+
+/* The verdict for an already-open file with fp positioned at the payload
+ * start (the position load-time callers hold after the text section). */
+static bool kv_file_tokfp_gate(FILE *fp, uint8_t ext_flags,
+                               uint64_t payload_bytes, ds4_engine *engine) {
+    bool present = false;
+    bool match = kv_file_tokfp_ok(fp, payload_bytes, engine, &present);
+    return kv_file_tokfp_verdict_ok(ext_flags, match, present);
+}
+
+/* The verdict for a path-keyed checkpoint (the store-reuse shortcut).  A
+ * file whose stamp cannot be read at all is not reusable: the caller
+ * replaces it and the full store re-earns the envelope. */
+static bool kv_file_path_tokfp_ok(const char *path, ds4_engine *engine) {
+    FILE *fp = fopen(path, "rb");
+    if (!fp) return false;
+    ds4_kvstore_entry hdr = {0};
+    uint32_t text_bytes = 0;
+    bool ok = false;
+    /* read_header leaves fp at the start of the text section. */
+    if (ds4_kvstore_read_header(fp, &hdr, &text_bytes) &&
+        fseeko(fp, (off_t)text_bytes, SEEK_CUR) == 0)
+        ok = kv_file_tokfp_gate(fp, hdr.ext_flags, hdr.payload_bytes, engine);
+    fclose(fp);
+    return ok;
+}
+
 /* Choose the deepest existing anchor checkpoint that a continued store can
  * delta from: same quant regime and model, rendered text a byte prefix of
  * this text, stamped with the engine tokenizer fingerprint, and carrying an
@@ -2969,10 +3012,11 @@ static bool kv_dir_has_dependents(ds4_kvstore *kc, const char sha[41]) {
     return found;
 }
 
-bool ds4_kvstore_existing_compatible(ds4_kvstore *kc, const char *path,
-                                     const char sha[41],
-                                     const char *text, size_t text_len,
-                                     int model_id, int quant_bits, int ctx_size) {
+static bool kv_cache_existing_compatible(ds4_kvstore *kc, ds4_engine *engine,
+                                         const char *path,
+                                         const char sha[41],
+                                         const char *text, size_t text_len,
+                                         int model_id, int quant_bits, int ctx_size) {
     if (access(path, F_OK) != 0) return false;
     ds4_kvstore_entry e = {0};
     if (!ds4_kvstore_read_entry_file(path, sha, &e)) return false;
@@ -2986,6 +3030,16 @@ bool ds4_kvstore_existing_compatible(ds4_kvstore *kc, const char *path,
                        e.quant_bits == (uint8_t)quant_bits) &&
                       e.ctx_size <= (uint32_t)ctx_size &&
                       kv_cache_file_text_matches(path, sha, text, text_len);
+    /* Restamp-laundering gate (PR #1020 hole 1): a text-identical file can
+     * carry the WRONG token history after a tokenizer-behaviour change.  The
+     * load path rejects such a file, but accepting it here would let the
+     * store take the reuse shortcut - keeping the stale payload and having
+     * rewrite_trailer stamp it with the CURRENT fingerprint - bypassing the
+     * guard once and forever.  Rejected files fall through to the full
+     * store, which writes the fresh rows with the fresh stamp.  One shared
+     * definition of the stamp verdict in kv_file_path_tokfp_ok; never
+     * re-implement its byte parsing here. */
+    if (compatible) compatible = kv_file_path_tokfp_ok(path, engine);
     if (compatible) {
         /* Refresh hits/recency so an actively-revalidated checkpoint is not
          * ranked as idle by the LRU/halving passes. */
@@ -3017,6 +3071,34 @@ bool ds4_kvstore_existing_compatible(ds4_kvstore *kc, const char *path,
     return true;
 }
 
+/* Engine-less form: the current tokenizer fingerprint is unknown (0), so a
+ * file stamped with any real fingerprint is NOT accepted (fail closed; see
+ * kv_file_tokfp_verdict_ok).  Unclaimed legacy files are unaffected.
+ * Production reuse checks know their engine and must call
+ * ds4_kvstore_existing_compatible_for_engine. */
+bool ds4_kvstore_existing_compatible(ds4_kvstore *kc, const char *path,
+                                     const char sha[41],
+                                     const char *text, size_t text_len,
+                                     int model_id, int quant_bits, int ctx_size) {
+    return kv_cache_existing_compatible(kc, NULL, path, sha, text, text_len,
+                                        model_id, quant_bits, ctx_size);
+}
+
+/* Same as ds4_kvstore_existing_compatible plus the tokenizer-fingerprint
+ * gate against the current engine: a stamped file whose stored fingerprint
+ * differs from the engine's (or whose TOKFP claim lacks a readable stamp
+ * section behind it) is replaced, never restamped. */
+bool ds4_kvstore_existing_compatible_for_engine(ds4_kvstore *kc,
+                                                ds4_engine *engine,
+                                                const char *path,
+                                                const char sha[41],
+                                                const char *text, size_t text_len,
+                                                int model_id, int quant_bits,
+                                                int ctx_size) {
+    return kv_cache_existing_compatible(kc, engine, path, sha, text, text_len,
+                                        model_id, quant_bits, ctx_size);
+}
+
 static bool kv_trailer_serialized_size(const ds4_kvstore_trailer_hooks *hooks,
                                        const char *text,
                                        uint64_t *bytes_out) {
@@ -3207,9 +3289,11 @@ bool ds4_kvstore_store_live_prefix_text(ds4_kvstore *kc,
     char *path = ds4_kvstore_path_for_sha(kc, sha);
     const uint8_t reason_code = ds4_kvstore_reason_code(reason);
 
-    if (ds4_kvstore_existing_compatible(kc, path, sha, text, text_len,
-                                        model_id,
-                                        quant_bits, ds4_session_ctx(session))) {
+    if (ds4_kvstore_existing_compatible_for_engine(kc, engine, path, sha,
+                                                   text, text_len,
+                                                   model_id,
+                                                   quant_bits,
+                                                   ds4_session_ctx(session))) {
         /* Same sha already on disk: the trailer is refreshed and nothing is
          * written.  Counted separately, because a store that logs nothing is
          * exactly the case an offline census cannot see. */
@@ -3777,10 +3861,10 @@ static int kv_cache_try_load_one(ds4_kvstore *kc, ds4_engine *engine,
      * Reject before touching the payload (session untouched); the next
      * store under this text key writes a fresh stamped file.  Unstamped
      * legacy files keep today's trusted behavior. */
-    if (!kv_file_tokfp_ok(fp, hdr.payload_bytes, engine, NULL)) {
+    if (!kv_file_tokfp_gate(fp, hdr.ext_flags, hdr.payload_bytes, engine)) {
         *retryable = true;
         kv_logf(kc, DS4_KVSTORE_LOG_WARNING,
-                "%s: kv cache tokenizer fingerprint mismatch, refusing stale tokenization%s%s %s",
+                "%s: kv cache tokenizer fingerprint mismatch or unverifiable stamp, refusing stale tokenization%s%s %s",
                 kv_log_name(kc),
                 responses_protocol ? " " : "",
                 responses_protocol ? "RESPPROTO" : "",
diff --git a/ds4_kvstore.h b/ds4_kvstore.h
index d319cee..b2d7870 100644
--- a/ds4_kvstore.h
+++ b/ds4_kvstore.h
@@ -417,6 +417,17 @@ bool ds4_kvstore_existing_compatible(ds4_kvstore *kc, const char *path,
                                      const char sha[41],
                                      const char *text, size_t text_len,
                                      int model_id, int quant_bits, int ctx_size);
+/* Engine-less form above: unknown current fingerprint (0) fails closed for
+ * stamped files; use this engine-aware form (production store path) so a
+ * file stamped with a stale tokenizer fingerprint is replaced instead of
+ * accepted and re-stamped by the reuse path (PR #1020 restamp laundering). */
+bool ds4_kvstore_existing_compatible_for_engine(ds4_kvstore *kc,
+                                                ds4_engine *engine,
+                                                const char *path,
+                                                const char sha[41],
+                                                const char *text, size_t text_len,
+                                                int model_id, int quant_bits,
+                                                int ctx_size);
 uint64_t ds4_kvstore_compute_conv_id(const char *text, size_t text_len,
                                      uint64_t model_fp);
 uint64_t ds4_kvstore_model_fingerprint(const char *model_path);
diff --git a/ds4_server.c b/ds4_server.c
index 0ba7938..fc985db 100644
--- a/ds4_server.c
+++ b/ds4_server.c
@@ -27539,6 +27539,119 @@ static void test_kv_cache_keep_set_per_session(void) {
     rmdir(dir);
 }
 
+/* v2 checkpoint carrying a tokenizer-fingerprint trailer section.  `claim`
+ * sets KV_EXT_TOKFP in the header; `section` writes the 16-byte TKF section
+ * stamped with `fp` (LE) after the zero-filled payload.  Lets a test simulate
+ * a tokenizer-behaviour change without a model: the reuse gate compares the
+ * file's stamped fp against ds4_engine_tokenizer_fingerprint(engine), which
+ * is 0 for a NULL engine, so any nonzero stamped fp is "stale". */
+static void test_kv_tokfp_stub_file(const char *dir, const char *text,
+                                    bool claim, bool section, uint64_t fp,
+                                    uint32_t tokens, uint64_t payload_bytes) {
+    char sha[41];
+    sha1_bytes_hex(text, strlen(text), sha);
+    char name[44];
+    snprintf(name, sizeof(name), "%.40s.kv", sha);
+    char *path = path_join(dir, name);
+    FILE *f = fopen(path, "wb");
+    TEST_ASSERT(f != NULL);
+    if (!f) {
+        free(path);
+        return;
+    }
+    uint8_t h[KV_CACHE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA];
+    uint32_t bucket = DS4_KVSTORE_DEFAULT_ANCHOR_STEP > 0
+        ? tokens / (uint32_t)DS4_KVSTORE_DEFAULT_ANCHOR_STEP : 0u;
+    uint8_t ext = claim ? (uint8_t)(KV_EXT_TOKFP | KV_EXT_TOOL_MAP) : 0;
+    ds4_kvstore_fill_header_v2(h, 0, 2, KV_REASON_COLD, ext, tokens, 0, 32768,
+                               100, (uint64_t)time(NULL), payload_bytes,
+                               0, 0, bucket, 0, false);
+    uint8_t tl[4];
+    le_put32(tl, (uint32_t)strlen(text));
+    TEST_ASSERT(fwrite(h, 1, sizeof(h), f) == sizeof(h));
+    TEST_ASSERT(fwrite(tl, 1, sizeof(tl), f) == sizeof(tl));
+    TEST_ASSERT(fwrite(text, 1, strlen(text), f) == strlen(text));
+    for (uint64_t i = 0; i < payload_bytes; i++)
+        TEST_ASSERT(fputc(7, f) != EOF);
+    if (section) {
+        uint8_t sh[8] = {'T', 'K', 'F', 1, 0, 0, 0, 0};
+        le_put32(sh + 4, 8);
+        uint8_t fpb[8];
+        for (int i = 0; i < 8; i++) fpb[i] = (uint8_t)(fp >> (8 * i));
+        TEST_ASSERT(fwrite(sh, 1, sizeof(sh), f) == sizeof(sh));
+        TEST_ASSERT(fwrite(fpb, 1, sizeof(fpb), f) == sizeof(fpb));
+    }
+    TEST_ASSERT(fclose(f) == 0);
+    free(path);
+}
+
+static void test_kv_cache_store_compat_rejects_stale_tokfp(void) {
+    /* PR #1020 restamp-laundering hole: the store-path reuse shortcut
+     * (existing_compatible) never consulted the tokenizer fingerprint, so
+     * after a behaviour change the load rejected the stale file, the engine
+     * re-prefilled, and the following store took the shortcut: rewrite_trailer
+     * kept the stale payload and re-stamped it with the CURRENT fingerprint,
+     * permanently bypassing the guard.  The shortcut must reject a mismatched
+     * stamp and REPLACE the file (the subsequent store writes the fresh
+     * payload) rather than restamp it, and must fail closed when the header
+     * claims a stamp the section cannot prove (same verdict the load path
+     * applies).  Engine NULL => current fingerprint 0. */
+    char tmpl[] = "/tmp/ds4-kv-compat-tokfp-test.XXXXXX";
+    char *dir = mkdtemp(tmpl);
+    TEST_ASSERT(dir != NULL);
+    if (!dir) return;
+
+    const char *text = "checkpoint text whose tokenizer behaviour changed";
+    char sha[41];
+    sha1_bytes_hex(text, strlen(text), sha);
+
+    kv_disk_cache kc = {0};
+    kc.enabled = true;
+    kc.dir = xstrdup(dir);
+    kc.opt = kv_cache_default_options();
+    kc.budget_bytes = 1ull << 30;
+
+    char *path = test_kv_path_for_text(dir, text);
+    const size_t tl = strlen(text);
+
+    /* 1. Stale stamp: incompatible AND replaced, not kept for restamping. */
+    test_kv_tokfp_stub_file(dir, text, true, true, 0x0BADC0DE0BADC0DEull,
+                            1024, 64);
+    TEST_ASSERT(!ds4_kvstore_existing_compatible(&kc, path, sha, text,
+                                                 tl, 0, 2, 32768));
+    TEST_ASSERT(access(path, F_OK) != 0);
+
+    /* 2. Matching stamp: reusable, file kept (reuse path alive). */
+    test_kv_tokfp_stub_file(dir, text, true, true, 0, 1024, 64);
+    TEST_ASSERT(ds4_kvstore_existing_compatible(&kc, path, sha, text,
+                                                tl, 0, 2, 32768));
+    TEST_ASSERT(access(path, F_OK) == 0);
+    unlink(path);
+
+    /* 3. Claimed TOKFP bit, unreadable/missing stamp section: fail closed. */
+    test_kv_tokfp_stub_file(dir, text, true, false, 0, 1024, 64);
+    TEST_ASSERT(!ds4_kvstore_existing_compatible(&kc, path, sha, text,
+                                                 tl, 0, 2, 32768));
+    TEST_ASSERT(access(path, F_OK) != 0);
+
+    /* 4. Legacy unstamped (no claim, no section): trusted behaviour kept. */
+    test_kv_tokfp_stub_file(dir, text, false, false, 0, 1024, 64);
+    TEST_ASSERT(ds4_kvstore_existing_compatible(&kc, path, sha, text,
+                                                tl, 0, 2, 32768));
+    unlink(path);
+
+    /* 5. Defense-in-depth: TKF section present and mismatching even though
+     * the header forgot to claim it - the parser's verdict still applies. */
+    test_kv_tokfp_stub_file(dir, text, false, true, 42, 1024, 64);
+    TEST_ASSERT(!ds4_kvstore_existing_compatible(&kc, path, sha, text,
+                                                 tl, 0, 2, 32768));
+    TEST_ASSERT(access(path, F_OK) != 0);
+
+    kv_cache_close(&kc);
+    free(path);
+    rmdir(dir);
+}
+
 static void test_kv_cache_store_compat_rejects_stale_model_fp(void) {
     /* After a weight swap a same-text file carries the old fingerprint.  The
      * store path must not declare it compatible (that would silently disable
@@ -29747,6 +29860,7 @@ static void ds4_server_unit_tests_run(void) {
     test_kv_cache_stale_at_load_isolates_conversations();
     test_kv_cache_conv_id_distinguishes_shared_head_sessions();
     test_kv_cache_keep_set_per_session();
+    test_kv_cache_store_compat_rejects_stale_tokfp();
     test_kv_cache_store_compat_rejects_stale_model_fp();
     test_kv_cache_find_skips_payload_less_files();
     test_kv_cache_continued_step_aligns_to_prefill_chunk();
```

---
## ROUND 2 (review BLOCK) — IN PROGRESS
- [ ] F1: tri-state verdict MATCH/MISMATCH/UNREADABLE; UNREADABLE = refuse reuse, KEEP file, distinct log
- [ ] F2: lock fact check->rewrite (file:line) -> void-or-fix
- [ ] F3a: engine-aware test, nonzero current fp (real _for_engine)
- [ ] F3b: real store-path test (replacement with fresh rows + current stamp)
- [ ] F3c: unreadable test: file exists, bytes untouched
- [ ] F3d: engine NULL on _for_engine -> loud refusal, no fp-0 laundering match
- [ ] report accuracy on availability tradeoff; RED->GREEN round 2 transcripts; engine status

## ROUND 2 RESULTS — all findings addressed, suite green

### Findings disposition
- F1 HIGH unlink-on-transient: FIXED. Tri-state enum ds4_kvstamp_verdict {MATCH, MISMATCH, UNREADABLE} (ds4_kvstore.c ~2739+); parser kv_file_tokfp_read distinguishes ferror vs clean EOF (truncated/absent-behind-claim section => UNREADABLE); compat core: UNREADABLE => refuse reuse, entry freed, NO touch, NO unlink, WARNING log "stamp unreadable, checkpoint kept and not reused"; MISMATCH => unchanged incompatible branch (unlink/in-place replace by design).
- F2 HIGH check-vs-mutate TOCTOU: VOID (not a race). Quoted facts: the check (ds4_kvstore.c:3292 compat) and the mutation (ds4_server.c rewrite at kvstore 3301) run inside ONE critical section: pthread_mutex_lock(&s->kv_mu) ds4_server.c:12964 ... unlock :12974, wrapping ds4_kvstore_store_live_prefix_text (:12965). Every other kvstore-file mutator in the server takes the same leaf lock: unlink-entry :13114/:13130, refresh+sweep :13277 (its own comment: "the leaf-lock hierarchy every other kv op uses" :13273-13276), text loads :13324/:13430, immutable-bits copy :13065; kvstore-internal eviction runs inside those sections and ds4_kvstore.c:1206 documents "this runs with the server's kv_mu held". kv_cache_evict (ds4_server.c:12465) has ZERO production callers (tests only). ds4_agent.c contains no store/compat/rewrite calls (grep verified) -> no cross-process writer of --kv-disk-dir. Single-open stream verdict not required; path-based verdict under the lock is sound.
- F3a HIGH weak match-case: FIXED via new public ds4_kvstore_existing_compatible_for_tokfp(kc, uint64_t current_tok_fp, ...) + test_kv_cache_compat_tokfp_value_form (CUR=0x0123456789ABCDEF: stored==CUR reusable+preserved; stored!=CUR refuse+replace; stored==0 vs CUR refuse+replace -- kills hardcoded-zero). Honest note: _for_tokfp did not exist pre-fix, so this test cannot be RED against anything; its negative control is the round-1 6-failure RED (gate) plus round-2 RED case 27739 (fp-0 match) and the keep-asserts.
- F3b store-path depth: NOT reachable model-free, exactly why: ds4_kvstore_store_live_prefix_text needs ds4_session_tokens/ds4_session_stage_payload of a real checkpoint graph; ds4_session is opaque outside ds4.c and ds4_session_create (ds4.h:413) needs a loaded model -> the suite's own instrumentation comment (ds4_server.c ~12729) states tests "cannot hand the policy a real checkpoint graph". Closest reachable = test_kv_cache_compat_replaced_then_reusable (refuse+remove -> fresh stamped file at same path -> reuse restored); payload freshness itself is verified post-deploy by the script phase (b).
- F3c unreadable test: ADDED test_kv_cache_compat_keeps_unreadable_stamp (truncated-mid-value and absent-behind-claim: compat false + access==0 + full-file memcmp unchanged).
- F3d engine NULL on _for_engine: LOUDLY REFUSED: kv_logf WARNING "...reuse check without an engine: stamped checkpoints cannot be validated (kept, not reused)" then UNKNOWN sentinel => stamped/claimed files UNREADABLE (refuse+keep); fingerprint(NULL)==0 is no longer a comparable current anywhere in compat. Plain ds4_kvstore_existing_compatible also uses UNKNOWN (never 0). Load site uses kv_tokfp_current (NULL engine => UNKNOWN => decline, file kept -- production load always has a real engine). Legacy UNCLAIMED unclaimed files: unchanged everywhere, incl. tests at 27568 and kv_policy_harness (ext_flags=0; verified -fsyntax-only rc=0).
- M-4/eviction race with the new path: none introduced. UNREADABLE never removes, so it cannot orphan delta children (M-4 only worried removal); the later full store replaces via tmp+rename under the same kv_mu hold, the pre-existing "replace in place" M-4 semantics for dependent parents. Eviction runs only inside kv_mu-held kvstore calls (see F2 citations); cross-thread dependents creation is serialized by the same lock.
- Availability tradeoff (correcting round-1 claim "steady state unchanged" -- too broad):
  1. Steady state (readable stamps, real engine): unchanged, plus ~26 extra bytes of trailer read per reuse decision.
  2. MISMATCH after a tokenizer change: one full fresh store per stale file (was: silent restamp - the bug).
  3. Claimed-but-unreadable (TOKFP bit, torn/missing section): previously LOADABLE UNVALIDATED and restamped; now rejected by load (one re-prefill) and refused+kept by compat. Such files are corrupt by definition, but note: this is a real availability change for corrupt-shaped files with NO tokenizer drift.
  4. Transient read failure (EIO/EINTR) on a stamped file: costs one declined reuse (full store rewrite instead of no-op) or one cold prefill at load - file is now never deleted (this was F1).
  5. Repeated UNREADABLE skips the touch refresh, so LRU may rank such a file idle; self-heals when the stamp becomes readable again, or eviction retires it normally.

### Round-2 RED (vs round-1 fix code, test-only diff) - 8 failures:
  ds4_server.c:27637: assertion failed: access(path, F_OK) == 0
  ds4_server.c:27640: assertion failed: test_kv_file_snapshot(path, snap2, sizeof snap2) == n0 && memcmp(snap, snap2, n0) == 0
  ds4_server.c:27655: assertion failed: access(path, F_OK) == 0
  ds4_server.c:27694: assertion failed: access(path, F_OK) == 0
  ds4_server.c:27696: assertion failed: test_kv_file_snapshot(path, snap2, sizeof snap2) == n0 && memcmp(snap, snap2, n0) == 0
  ds4_server.c:27706: assertion failed: access(path, F_OK) == 0
  ds4_server.c:27708: assertion failed: test_kv_file_snapshot(path, snap2, sizeof snap2) == n0 && memcmp(snap, snap2, n0) == 0
  ds4_server.c:27739: assertion failed: !ds4_kvstore_existing_compatible(&kc, path, sha, text, tl, 0, 2, 32768)
  ds4-server tests: 8 failure(s)
  (reject-refuse asserts passed; failures are exactly the keep-after-unreadable [27637/27640/27655/27694-27708, F1] and zero-stamp-match [27739, F3d])

### Round-2 GREEN (patched kvstore + new tests):
  ds4-server tests: ok
  make test-server-logic rc=0; make ds4-server rc=0 (ds4_kvstore.c recompiled clean); kv_policy_harness -fsyntax-only rc=0.

### Engine status: UP, never stopped this round. PID 29866 (started 2026-11-20 23:39:30, HTTP 200 on :8002) - running the PRE-FIX image. Disk ./ds4-server rebuilt with the fix. Deploy stays with the coordinator (nothing committed).

### Deploy checklist (coordinator)
1. Review/commit: ds4_kvstore.c, ds4_kvstore.h, ds4_server.c (tests), .codebase-memory/20261118-kvstorm/verify_tokfp_laundering_fix.sh (untracked).
2. Optional pre-window: make test-server-logic; ./ds4_server_test (engine may stay up).
3. Capture output-parity BEFORE restart: KVDIR=<prod kv dir> .codebase-memory/20261118-kvstorm/verify_tokfp_laundering_fix.sh before
4. Window: ./ds4-server.sh stop; (binary already rebuilt, or make ds4-server); ./ds4-server.sh start-qwen
5. .codebase-memory/20261118-kvstorm/verify_tokfp_laundering_fix.sh after   (phase a parity + phase b live laundering-proof)
6. Confirm prod answers (:8002 /v1/models 200) and log shows no unexpected "stamp unreadable" spam (would indicate a kv-dir read problem, not the fix).

---
## ROUND 3 — IN PROGRESS
- [ ] step 0 tree coherence (build+suite vs round-2 report) -> running now
- [ ] MF1: tri-state out of compat into store; abort-before-eviction on UNREADABLE; store-level test; availability accounting item 6
- [ ] MF2/3: verify script -> isolated instance, unique sentinel, parsed envelope, refusal guard, INCONCLUSIVE fixtures
- [ ] RED/GREEN transcripts incl. store-level evidence

## ROUND 3 RESULTS

### Step 0 — tree coherence: PASS, matches round-2 report
make test-server-logic rc=0 -> `ds4_server tests: ok`; ./ds4_server_test rc=0 -> ok; make ds4-server rc=0 ("up to date", 0 errors). Modified set exactly my three files + the untracked script. Observation: the docs agent's md modifications disappeared from git status mid-window (their commit/cleanup elsewhere); no interleaved edits found in my files.

### MF1 HIGH — store-level UNREADABLE still lost the file: FIXED
- Tri-state propagated out of the compat helper: core kv_cache_existing_compatible gains ds4_kvstamp_verdict *stamp_out (NULL for all public wrappers -> behavior of helpers/tests unchanged).
- ds4_kvstore_store_live_prefix_text now consults the verdict directly: MISMATCH/base-incompat -> proceeds to delta staging, eviction, tmp+rename (replace by design); **UNREADABLE -> returns before any of that** (free text/path/tokens; no staging, no eviction, no rename), with a store-level distinct log line "kv cache store aborted before eviction/replacement: stamp unreadable tokens=%d reason=%s file=%s" in addition to the gate's "stamp unreadable, checkpoint kept and not reused".
- Direction traded, in one sentence: on a transient stamp-read failure we LOSE the chance to write fresh correct data on this request and bet that the next request stores again, because an EIO on the external NVMe volume is a plausible recurring class (the same class F_FULLFSYNC exists for) and losing a healthy 400k-token checkpoint costs far more than skipping one store.
- Store-level test: honestly NOT reachable end-to-end inside ds4_server_test - the function returns at the engine-quant gate (ds4_kvstore.c:3287-3292, routed_quant without a loaded model is not loadable) and the ds4_session_tokens gate (3296-3305) long before reaching the reuse shortcut; ds4_session is opaque outside ds4.c and ds4_session_create (ds4.h:413) needs a loaded model; grep-confirmed the suite never creates an engine or session. Closest reachable assertion added, pinning the EXACT decision input the store branch consumes: test_kv_cache_stamp_verdict_store_gate via the new public ds4_kvstore_existing_stamp_verdict(path, current_fp) (same kv_file_path_tokfp_read the store calls): asserts MATCH/MISMATCH/UNREADABLE on every shape (match, differ, zero-vs-nonzero, truncated, absent-behind-claim, readable-vs-unknown-current, legacy, missing-file=UNREADABLE-not-MISMATCH) AND per-shape byte-identical purity (memcmp before/after each call). The abort branch itself is pinned by source (diff hunk: return placed before delta staging) and proven end-to-end by the script's launder phase (PRE_BIN = LAUNDERED negative control, POST_BIN = BLOCKED).

### Availability accounting (added item 6, replacing LRU-nuance padding)
1-5 as round 2. 6. FALL-THROUGH RISK, accepted and bounded: on MISMATCH the compat unlink happens first; if staging/writing then fails (kvstore store path's stage_rc != 0 -> return false) there is NO file at all for that prefix until the next successful store. This is bounded because the removed file was VERIFIED stale - its rows are by definition wrong-tokenizer data, i.e. garbage for every future load; what is "lost" is only the (useless) stale envelope. 7. Dependent-parent replacement: when M-4 keeps a mismatched parent (live v3 children), the subsequent full store rename-overwrites it in place - pre-existing M-4 semantics, unchanged by this fix; child loads still re-verify the parent's chain/stamp AND the child's own stamp, so a stale child can never splice under the replaced parent; stale children themselves follow this same refuse->replace path at their next store. 8. Eviction on the abort path: none - the UNREADABLE abort returns before the eviction call (store's ds4_kvstore_evict is reached only in the fresh-write branch, inside kv_mu as before).

### MF2 HIGH — script touched the production cache: FIXED (isolated-instance design)
Rewritten as phase-based script; it NEVER targets :8002 and never mutates any dir outside its own temp tree:
- require_prod_down(): refuses if the production port answers anything at all (only one ds4 process can hold the engine lock => the script announces it needs an ENGINE WINDOW scheduled by the coordinator, and never stops/starts prod itself).
- require_isolated_dir(): every kv dir is created under /private/var/folders/gp/.../tokfp-fix-runs; any path resolving to /Volumes/FireCuda520/ds4-kv-qwen (or anything outside the temp root) is REFUSED at both creation and use.
- All mutations happen only while the isolated instance is STOPPED (no out-of-band writes into a live server's cache dir).
- Per-run nonce identity (tokfp-r3-<time>-<random>) verified ONLY inside the PARSED TEXT SECTION (header: KVC magic + version + payload-ABI byte, hdr_total = 48+extra+4, text_size word, text, payload), plus content-address re-validation sha1(text)==basename; file identity (inode, birthtime, size, stamp) re-parsed and re-compared immediately before the XOR mutation, mismatch => SKIP (and skips force INCONCLUSIVE).
- Stamp offset is COMPUTED (ht+text_bytes+payload_bytes), never pattern-scanned anywhere in the blob.

### MF3 HIGH — false PASS states: FIXED + fixtures
classify() labels per file: LAUNDERED (same inode+birthtime, stamp restored in place) => overall FAIL; BLOCKED (new inode/birthtime, parses valid, nonce-bearing, content-addressed, stamp==pre-corruption) => PASS; MISSING/evicted => INCONCLUSIVE explicitly (never counts as success - the round-2 bug counted it as BLOCKED); STUCK (kept corrupt, reuse never fired) => INCONCLUSIVE; FOREIGN/unparseable/content-address-violated => INCONCLUSIVE; empty snapshot or any skipped corruption => INCONCLUSIVE. selftest mode exercises all six classifications as pure-python fixtures in a private mkdtemp (no engine, no cache) - currently 6/6 PASS.

### Transcripts
- Step 0 (above).
- Round-3 suite run during implementation (pre-purity-fix): 1 failure in the new verdict test - my own purity assert compared bytes across different shapes; test-only fix, not a source regression.
- Final GREEN: make test-server-logic rc=0, `ds4-server tests: ok`; ./ds4_server_test rc=0, `ds4-server tests: ok`; make ds4-server rc=0; kv_policy_harness -fsyntax-only rc=0; script selftest rc=0.
- RED status for the MF1 change itself: no new suite test can be RED against round-2 code because ds4_kvstore_existing_stamp_verdict did not exist (undefined symbol). The runtime negative control for the store-level bug is the deploy-window launder phase on PRE_BIN (expect verdict FAIL/LAUNDERED) vs POST_BIN (expect PASS) - that pairing is the real RED->GREEN for the store level. Round-1 (6 failures, gate) and round-2 (8 failures, keep+zero-match) RED transcripts remain in this file and are still valid evidence for their layers.

### Engine status
Production NEVER stopped this round: PID 29866 (started 2026-11-20 23:39:30), :8002 HTTP 200, running the PRE-FIX image; the rebuilt (round-3) binary is on ./ds4-server on disk only. Nothing committed (git diff = 791 lines over ds4_kvstore.c/.h + ds4_server.c tests; script untracked).

### What is PROVEN vs only ARGUED
Proven (runtime, this tree): tri-state verdict correctness and purity (new suite test); reuse-gate refusal semantics and keep-vs-replace split at the compat boundary (round-2 suite); whole server-logic suite green incl. M-4/model_fp/load tests; production binary compiles; harness API intact; script classification logic (selftest fixtures).
Argued (source-level, not runtime in-suite): the store's abort-before-eviction branch itself (session/engine gates; pinned by diff + verdict-input test); the MISMATCH unlink-then-staging-fail hole's boundedness (data was verified-stale by definition).
Deferred to deploy window (runtime, isolated): PRE_BIN LAUNDERED vs POST_BIN BLOCKED end-to-end; cold-vs-warm and PRE-vs-POST output parity.

### Remaining deploy checklist (coordinator)
1. Commit review: ds4_kvstore.c/.h, ds4_server.c, verify_tokfp_laundering_fix.sh.
2. Before commit, snapshot a PRE_BIN for the negative control: build HEAD cleanly in a separate worktree/checkout (I never ran checkout/stash).
3. Engine window: ./ds4-server.sh stop.
4. PRE_BIN=.../head/ds4-server .codebase-memory/20261118-kvstorm/verify_tokfp_laundering_fix.sh launder   -> expect FAIL/LAUNDERED (harness proves the test can fail)
5. POST_BIN=$(pwd)/ds4-server .codebase-memory/.../verify_tokfp_laundering_fix.sh launder                  -> expect PASS
6. PRE_BIN=... POST_BIN=... verify_tokfp_laundering_fix.sh parity                                          -> expect PARITY PASS (fix output-neutral)
7. ./ds4-server.sh start-qwen; confirm :8002 200; watch logs for "store aborted before eviction/replacement" (rare = healthy; a storm = the kv volume is failing reads, investigate the volume, not the fix).

## ROUND 3 ADDENDUM (sibling artifacts absorbed; work continued, not reordered)

### F2 disposition - final, with sibling evidence (VOID, fully serialized)
Folded into the store-gate code comment at ds4_kvstore.c (check->rewrite call site) and the report:
- s->kv_mu (ds4_server.c:11116) held across the entire library call: lock 12964 / unlock 12974 wraps compat (:3359) AND rewrite_trailer (:3368).
- All other mutators same mutex: unlinks 13114-13121 / 13130-13145, divergence sweep 13277-13280, loads 13324-13328 / 13430-13434.
- Eviction not threaded: inline under kv_mu (ds4_kvstore.c:3397; convention note :1206). Server threads (pthread_create 19009/19016/19032/19078) reach the kv dir only through those wrapped functions. tool_memory_unlink (11261) = list surgery, not file I/O.
- Cross-process: flock instance lock ds4.c:63521-63548 taken at engine creation ds4.c:75011 - no second ds4/ds4-server process can exist.
- Therefore single-open refactor unnecessary. RESIDUAL = out-of-band writes by a non-ds4 process; our own verify script WAS exactly that, and the isolated-dir/temp-root/stopped-instance design is the only remaining protection (stated in both the script header and the kvstore comment) - production serialization guarantees nothing against it.

### API-absence proof (separate evidence class, distinct from behavioral RED)
Compiled HEAD's ds4_kvstore.c (git show into temp, no checkout; compile rc=0) and linked the round-2/3 test object set against it:
```
Undefined symbols for architecture arm64:
  "_ds4_kvstore_existing_compatible_for_engine", referenced from: _ds4_server_unit_tests_run in ds4_server_test.o
  "_ds4_kvstore_existing_compatible_for_tokfp",  referenced from: _ds4_server_unit_tests_run in ds4_server_test.o
  "_ds4_kvstore_existing_stamp_verdict",         referenced from: _ds4_server_unit_tests_run in ds4_server_test.o
```
Pre-fix main exposes NO gate API at all - the value-form/verdict tests cannot be behavioral-RED against anything real. Behavioral RED lives in round-1 (6 failures) + round-2 (8 failures) transcripts; store-level behavioral RED/GREEN is the deploy-window PRE_BIN(LAUNDERED)/POST_BIN(PASS) pairing.

### Final round-3 transcripts (after comment-only edits; rebuilt)
- make test-server-logic rc=0 -> `ds4-server tests: ok`
- ./ds4_server_test rc=0 -> `ds4-server tests: ok`
- make ds4-server rc=0, 0 errors
- script selftest rc=0 (6/6 fixtures)
- kv_policy_harness -fsyntax-only rc=0 (unchanged, earlier this round)
- Independent corroboration of step-0: sibling session ran the suite built at 00:16 and saw ok/rc=0 at 00:20, engine untouched; that session made zero repo edits. I am the only live editor.

### Engine status (end of round 3)
Never stopped: PID 29866, :8002 HTTP 200, pre-fix image in memory; round-3 binary on disk only. Nothing committed. Diff = 803 lines over ds4_kvstore.c/.h + ds4_server.c; script untracked.

### Deploy checklist (updated)
1. Review+commit: ds4_kvstore.c, ds4_kvstore.h, ds4_server.c, verify_tokfp_laundering_fix.sh.
2. PRE_BIN from clean HEAD via separate worktree (needed for the launder negative control AND the parity phase).
3. Engine window: ./ds4-server.sh stop (script refuses otherwise; flock means no overlap possible).
4. verify_tokfp_laundering_fix.sh launder <PRE_BIN>   -> expect FAIL/LAUNDERED (proves the check can fail)
5. verify_tokfp_laundering_fix.sh launder ./ds4-server -> expect PASS/BLOCKED
6. PRE_BIN=... POST_BIN=... ... parity                 -> expect PARITY PASS (+ cold-vs-warm notes)
7. ./ds4-server.sh start-qwen; verify :8002 200; "store aborted before eviction/replacement" = healthy fail-closed (rare); a storm of them = volume read failure, investigate the volume.

### Source-edit mtimes (single-writer confirmation, all inside this session's working window)
ds4_kvstore.c   2026-11-21 00:24:06  (F2 comment fold-in)
ds4_kvstore.h   2026-11-21 00:15:33  (verdict enum + API decls)
ds4_server.c    2026-11-21 00:15:57  (store-gate/purity test fixes)
verify_tokfp_laundering_fix.sh 2026-11-21 00:24:06 (header justification)
