#!/bin/zsh
# Post-deploy verification for the PR #1020 restamp-laundering fix.
#
# ROUND 3 DESIGN (review M-2/M-3): this script NEVER touches the production
# cache or the production engine. It runs an ISOLATED ds4-server instance on
# a different port with a fresh --kv-disk-dir created under the approved
# temp root. Only one ds4 process can hold the engine lock at a time, so an
# isolated run NEEDS AN ENGINE WINDOW: production must already be stopped by
# the coordinator (./ds4-server.sh stop). If anything answers on the
# production port this script REFUSES to run. It never starts, stops, or
# reconfigures production.
# WHY ISOLATION IS NOT OPTIONAL (round-3 F2 close-out): the production cache
# is fully serialized against every ds4 writer (server leaf mutex kv_mu plus
# the process-wide flock instance lock, ds4.c:63521), but those guarantees
# protect NOTHING against out-of-band writes - and this script's stamp
# corruption IS such a write. The isolated-dir design is therefore the only
# remaining protection, not hygiene: the one artifact that ever mutated a
# .kv without the server's lock must never touch a directory the server
# owns. Hence: temp-root-only kv dirs, hard refusal of
# /Volumes/FireCuda520/ds4-kv-qwen, and mutation only while the isolated
# instance is stopped.
#
# Phases:
#   selftest   verdict-classification fixtures in pure python - no engine,
#              no cache of any kind touched. Safe any time. Covers:
#              LAUNDERED (same inode, stamp restored), STUCK (same inode,
#              still corrupt), BLOCKED (fresh inode, valid replacement),
#              MISSING/evicted => INCONCLUSIVE (never PASS), FOREIGN =>
#              INCONCLUSIVE, EMPTY snapshot (skipped corruption) =>
#              INCONCLUSIVE.
#   parity     PRE_BIN vs POST_BIN cold+warm greedy completions in isolated
#              dirs: the fix must not move normal output.
#   launder    end-to-end store-level proof on the isolated instance:
#              real stores write stamped checkpoints; the instance is
#              STOPPED before any mutation (no out-of-band writes while a
#              server owns the dir); the stamped fingerprint is corrupted at
#              a PARSED offset after re-validating file identity (header
#              parse + sha1(text)==basename + nonce in the TEXT section, not
#              a substring match anywhere); restart, re-request, classify.
#              Run against PRE_BIN first: LAUNDERED there is the negative
#              control; POST_BIN must give BLOCKED (PASS).
#
# Usage:
#   ./verify_tokfp_laundering_fix.sh selftest
#   PRE_BIN=<pre-fix binary> POST_BIN=<rebuilt binary> ./verify_tokfp_laundering_fix.sh parity
#   POST_BIN=<rebuilt binary> ./verify_tokfp_laundering_fix.sh launder   # expect PASS
#   PRE_BIN=<pre-fix binary>  ./verify_tokfp_laundering_fix.sh launder   # expect LAUNDERED(FAIL)
set -u
REPO=/Users/naz/Projects/ds4
TEMPROOT=/private/var/folders/gp/hjkf5cnx3vg4pxxv0zbd1c8h0000gn/T/opencode
OUT=$TEMPROOT/tokfp-fix-runs
PROD_PORT=8002
ISO_PORT=${ISO_PORT:-8079}
MODEL=gguf/Qwen3.8-Flash-Next-Q4.gguf
FORBIDDEN_KVREAL=/Volumes/FireCuda520/ds4-kv-qwen
mkdir -p $OUT || { echo "cannot create $OUT" >&2; exit 2; }
say() { echo "$(date '+%H:%M:%S') $*" }
die() { say "REFUSED: $*"; exit 2 }

require_prod_down() {
  if curl -s -m 5 -o /dev/null http://127.0.0.1:$PROD_PORT/v1/models; then
    die "production engine answers on :$PROD_PORT. Only one ds4 process can hold the engine lock; this script needs an ENGINE WINDOW (coordinator: ./ds4-server.sh stop first, then re-run me, then start-qwen). I never touch production."
  fi
}

require_isolated_dir() {  # $1 = kv dir to create/use; validated BEFORE anything is created
  local base real
  base=$(basename "$1")
  case "$base" in ""|.|..) die "refusing malformed kv dir: $1" ;; esac
  if [ -e "$1" ]; then
    real=$(cd "$1" 2>/dev/null && pwd -P) || die "cannot resolve $1"
  else
    real=$(cd "$(dirname "$1")" 2>/dev/null && pwd -P) || die "parent of $1 does not exist; refusing to create it"
    real="$real/$base"
  fi
  case "$real" in
    "$FORBIDDEN_KVREAL"|"$FORBIDDEN_KVREAL"/*) die "refusing to use the production cache dir: $real" ;;
    "$TEMPROOT"/tokfp-fix-runs/*) ;;
    *) die "kv dir must live under $TEMPROOT/tokfp-fix-runs (isolation guarantee), got $real" ;;
  esac
  mkdir -p "$1" 2>/dev/null || die "cannot create $1"
}

ensure_run_nonce() {
  mkdir -p $OUT
  if [ ! -f $OUT/run.env ]; then
    echo "NONCE=tokfp-r3-$(date +%Y%m%d%H%M%S)-${RANDOM}${RANDOM}" > $OUT/run.env
  fi
  source $OUT/run.env
  say "per-run sentinel identity: $NONCE (in prompt text, verified inside each .kv TEXT section only)"
}

write_prompts() {
python3 - "$OUT" "${NONCE:?}" <<'PY'
import json, sys
out, nonce = sys.argv[1], sys.argv[2]
SENT = ("The catchment's baseflow is sustained by fractured bedrock rather than soil moisture, "
        "which decouples stream response from rainfall intensity. ")
UNIT = SENT * (76000 // len(SENT) + 1)
for i in range(2):
    doc = "[%s archive %d] " % (nonce, i) + UNIT[:76000]
    open("%s/prompt%d.json" % (out, i), "w").write(json.dumps({
        "model": "default",
        "messages": [{"role": "system", "content": doc},
                     {"role": "user", "content": "In one paragraph, what single factor governs the "
                                                 "effect described above, and why?"}],
        "temperature": 0, "max_tokens": 256, "stream": False,
        "chat_template_kwargs": {"enable_thinking": False}}))
PY
}

ISO_PID=
launch() {   # $1 bin, $2 kvdir, $3 logfile ; sets ISO_PID
  [ -x "$1" ] || die "binary not executable: $1"
  require_isolated_dir "$2"
  : > "$3"
  cd $REPO || exit 2
  $1 --model $MODEL --ctx 65536 --port $ISO_PORT --kv-disk-dir "$2" >> "$3" 2>&1 &
  ISO_PID=$!
  for i in {1..120}; do
    curl -s -m 5 -o /dev/null http://127.0.0.1:$ISO_PORT/v1/models && return 0
    kill -0 $ISO_PID 2>/dev/null || { say "isolated instance died (see $3)"; return 3; }
    sleep 5
  done
  say "isolated instance did not answer within 10 min"; return 4
}

shutdown() {
  [ -n "$ISO_PID" ] || return 0
  kill $ISO_PID 2>/dev/null
  wait $ISO_PID 2>/dev/null
  ISO_PID=
  sleep 3
}

one_run() { # $1 tag
  for p in 0 1; do
    curl -s -m 600 -H 'Content-Type: application/json' -d @$OUT/prompt$p.json \
      http://127.0.0.1:$ISO_PORT/v1/chat/completions > $OUT/resp-$1-p$p.json || return 1
    python3 -c "
import json
d=json.load(open('$OUT/resp-$1-p$p.json'))
t=(d['choices'][0]['message'].get('content') or '').strip()
open('$OUT/text-$1-p$p.txt','w').write(t)
print('    $1 p$p P=%-7s comp=%-4s chars=%d' % (d['usage']['prompt_tokens'], d['usage']['completion_tokens'], len(t)))
" || return 1
  done
}

TOOLS=$OUT/tokfp_tools.py
cat > $TOOLS <<'PYEOF'
import glob, hashlib, json, os, struct, sys

FIXED = 48
EXTRA = {1: 0, 2: 24, 3: 92}
ABI = 2
TKF = b"TKF\x01"

def parse(path):
    """Header-parsed envelope; returns dict or None. TKF section offset is
    COMPUTED (ht + text_bytes + payload_bytes), not pattern-scanned."""
    try:
        blob = open(path, "rb").read()
    except OSError:
        return None
    if len(blob) < FIXED + 5: return None
    if blob[0:3] != b"KVC" or blob[3] not in (1, 2, 3) or blob[20] != ABI:
        return None
    v = blob[3]; ht = FIXED + EXTRA[v] + 4
    if len(blob) < ht + 4: return None
    text_bytes = struct.unpack_from("<I", blob, FIXED + EXTRA[v])[0]
    payload_bytes = struct.unpack_from("<Q", blob, 40)[0]
    if ht + text_bytes + payload_bytes > len(blob): return None
    text = blob[ht:ht + text_bytes]
    tok_off = ht + text_bytes + payload_bytes
    stamp = None
    if blob[tok_off:tok_off + 4] == TKF and \
       struct.unpack_from("<I", blob, tok_off + 4)[0] == 8 and \
       tok_off + 16 <= len(blob):
        # hex, not bytes: json.dump cannot serialize bytes, and a TypeError here used to
        # truncate snap.json mid-write and make every later phase report INCONCLUSIVE.
        stamp = blob[tok_off + 8:tok_off + 16].hex()
    st = os.stat(path)
    return {"path": path, "version": v, "ht": ht,
            "text_bytes": text_bytes, "payload_bytes": payload_bytes,
            "sha_name": os.path.basename(path)[:40],
            "text_sha": hashlib.sha1(text).hexdigest(),
            "stamp": stamp, "size": len(blob),
            "inode": st.st_ino, "birth": st.st_birthtime}

def ours(path, nonce):
    e = parse(path)
    if not e: return None, "unparseable"
    blob = open(path, "rb").read()
    text = blob[e["ht"]:e["ht"] + e["text_bytes"]]
    if nonce.encode() not in text:
        return e, "not-our-text-section"          # nonce must be in the
    if e["text_sha"] != e["sha_name"]:            # PARSED TEXT SECTION only
        return e, "content-address-violated"
    if e["stamp"] is None:
        return e, "no-readable-TKF-at-computed-offset"
    return e, None

def snapshot(kvdir, nonce):
    rec = []
    for p in sorted(glob.glob(os.path.join(kvdir, "*.kv"))):
        e, why = ours(p, nonce)
        if e is None:
            print("  SKIP unparseable", p); continue
        if why == "not-our-text-section":
            continue                               # another run / other prompt
        if why:
            print("  SKIP %s: %s" % (os.path.basename(p)[:12], why))
            continue
        rec.append(e)
    return rec

def corrupt(rec):
    done = 0
    for e in rec:
        cur = parse(e["path"])
        if not cur or cur["inode"] != e["inode"] or cur["birth"] != e["birth"] \
           or cur["stamp"] != e["stamp"] or cur["text_sha"] != cur["sha_name"] \
           or cur["size"] != e["size"]:
            print("  SKIP revalidation failed at mutate time:", e["path"])
            continue
        off = cur["ht"] + cur["text_bytes"] + cur["payload_bytes"] + 8
        with open(e["path"], "r+b") as f:
            f.seek(off); f.write(bytes(b ^ 0xFF for b in bytes.fromhex(cur["stamp"])))
        done += 1
        print("  corrupted parsed-offset stamp:", os.path.basename(e["path"])[:12])
    print("  %d/%d file(s) corrupted" % (done, len(rec)))
    return done

def classify(kvdir, nonce, rec):
    if not rec:
        print("  no files in snapshot: nothing was exercised")
        return "INCONCLUSIVE"
    labels = []
    for e in rec:
        if not os.path.exists(e["path"]):
            labels.append("MISSING")
            print("  MISSING/evicted (NEVER counts as success):", e["path"])
            continue
        cur, why = ours(e["path"], nonce)
        if cur is None or why:
            labels.append("FOREIGN")
            print("  FOREIGN/unparseable at path (%s):" % why, e["path"])
            continue
        same_env = cur["inode"] == e["inode"] and cur["birth"] == e["birth"]
        if same_env and cur["stamp"] == e["stamp"]:
            labels.append("LAUNDERED")
            print("  LAUNDERED (bug live): same inode+birthtime, stamp"
                  " restored in place over stale rows:", e["path"])
        elif same_env:
            labels.append("STUCK")
            print("  STUCK: file kept with corrupt stamp; reuse never fired"
                  " (store may not have run):", e["path"])
        elif cur["stamp"] == e["stamp"]:
            labels.append("BLOCKED")
            print("  BLOCKED (fix live): replaced with a fresh valid"
                  " checkpoint (new inode/birth, stamp==pre-corruption):",
                  e["path"])
        else:
            labels.append("ODD")
            print("  ODD replacement (new envelope, different stamp):", e["path"])
    if "LAUNDERED" in labels:
        return "FAIL"
    if labels and all(l == "BLOCKED" for l in labels):
        return "PASS"
    return "INCONCLUSIVE"

def build_file(kvdir, nonce, with_stamp=True, ours_text=True, sha_match=True,
               stamp_val=b"\xAA" * 8, corrupt_on_disk=False):
    text = (nonce + " body").encode() if ours_text else b"some other run body"
    name = hashlib.sha1(text).hexdigest() if sha_match else "0" * 40
    ht = FIXED + EXTRA[2] + 4
    blob = bytearray(b"\x00" * (FIXED + EXTRA[2]))
    blob[0:3] = b"KVC"; blob[3] = 2; blob[20] = ABI
    struct.pack_into("<Q", blob, 40, 64)          # payload_bytes field
    blob += struct.pack("<I", len(text))
    blob += text
    blob += b"P" * 64
    if with_stamp:
        blob += TKF + struct.pack("<I", 8) + stamp_val
    p = os.path.join(kvdir, name + ".kv")
    open(p, "wb").write(bytes(blob))
    if corrupt_on_disk:
        off = ht + len(text) + 64 + 8
        with open(p, "r+b") as f:
            f.seek(off); f.write(bytes(b ^ 0xFF for b in stamp_val))
    return p

def selftest():
    import tempfile, shutil
    nonce = "tokfp-selftest-nonce-1234"
    d = tempfile.mkdtemp(prefix="tokfp-selftest-")
    try:
        # 1 LAUNDERED: same inode, stamp intact in place
        p = build_file(d, nonce)
        e = parse(p)
        assert classify(d, nonce, [e]) == "FAIL", "in-place intact stamp"
        # 2 STUCK: same inode, still corrupt => store never ran
        p = build_file(d, nonce, corrupt_on_disk=True)
        snap = parse(p); snap["stamp"] = b"\xAA" * 8   # snapshot pre-corruption
        assert classify(d, nonce, [snap]) == "INCONCLUSIVE", "stuck"
        # 3 BLOCKED: replaced fresh file (unlink+recreate, same logical name)
        p = build_file(d, nonce)
        e = parse(p)
        old = dict(e); old["inode"] -= 1; old["birth"] -= 10.0
        assert classify(d, nonce, [old]) == "PASS", "fresh replacement"
        # 4 MISSING: evicted file must NEVER read as success
        os.unlink(p)
        assert classify(d, nonce, [e]) == "INCONCLUSIVE", "missing"
        # 5 FOREIGN: unrelated replacement at our path name
        os.unlink(p) if os.path.exists(p) else None
        p = build_file(d, nonce, ours_text=False)
        assert classify(d, nonce, [e]) == "INCONCLUSIVE", "foreign"
        # 6 EMPTY snapshot: skipped corruption
        assert classify(d, nonce, []) == "INCONCLUSIVE", "empty"
        print("selftest: 6/6 fixtures OK (LAUNDERED, STUCK, BLOCKED, MISSING,"
              " FOREIGN, EMPTY)")
    finally:
        shutil.rmtree(d)
    return 0

cmd = sys.argv[1]
if cmd == "selftest":
    sys.exit(selftest())
kvdir, nonce, snapf = sys.argv[2], sys.argv[3], sys.argv[4]
if cmd == "snapshot":
    rec = snapshot(kvdir, nonce)
    json.dump(rec, open(snapf, "w"))
    print("snapshot: %d stamped file(s) for this run" % len(rec))
    sys.exit(0 if rec else 4)
elif cmd == "corrupt":
    sys.exit(0 if corrupt(json.load(open(snapf))) else 4)
elif cmd == "classify":
    v = classify(kvdir, nonce, json.load(open(snapf)))
    print("verdict: %s" % v)
    sys.exit({"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 3}[v])
sys.exit(2)
PYEOF

phase_selftest() {
  python3 $TOOLS selftest > $OUT/../tokfp-selftest.log 2>&1
  local rc=$?
  cat $OUT/../tokfp-selftest.log
  [ $rc = 0 ] && say "selftest: verdict classification fixtures OK (no engine, no cache touched)"
  return $rc
}

phase_parity() {
  require_prod_down
  ensure_run_nonce; write_prompts
  [ -n "${PRE_BIN:-}" ] && [ -n "${POST_BIN:-}" ] || die "parity needs PRE_BIN and POST_BIN"
  say "parity needs an ENGINE WINDOW (single-process lock); production verified DOWN"
  for tag in pre post; do
    local bin=$PRE_BIN; [ $tag = post ] && bin=$POST_BIN
    local kv=$OUT/kv-$tag-$(print -r -- $bin | cksum | awk '{print $1}')
    rm -rf $kv
    say "[$tag] launching isolated instance on :$ISO_PORT (fresh dir)"
    launch $bin $kv $OUT/server-$tag.log || { shutdown; return 1; }
    one_run $tag-cold || { shutdown; return 1; }
    one_run $tag-warm || { shutdown; return 1; }
    shutdown
  done
  local same=1
  for p in 0 1; do
    if cmp -s $OUT/text-pre-warm-p$p.txt $OUT/text-post-warm-p$p.txt; then
      say "  p$p warm output PRE == POST (identical)"
    else
      say "  p$p PRE != POST -- fix is NOT output-neutral; STOP"; same=0
    fi
    for tag in pre post; do
      cmp -s $OUT/text-$tag-cold-p$p.txt $OUT/text-$tag-warm-p$p.txt || \
        say "  [$tag] p$p cold != warm: cache-sensitive workload (note, not the fix)"
    done
  done
  [ $same = 1 ] && say "PARITY PASS: cache-ON greedy output identical PRE vs POST"
  return $((1 - same))
}

phase_launder() {
  require_prod_down
  ensure_run_nonce; write_prompts
  local bin=${BIN:?launder needs BIN=<path to ds4-server binary>}
  local kv=$OUT/kv-launder-$(print -r -- $bin | cksum | awk '{print $1}')
  rm -rf $kv
  say "launder: isolated instance $bin on :$ISO_PORT (fresh dir); ENGINE WINDOW required (prod verified DOWN)"
  launch $bin $kv $OUT/server-launder.log || return 3
  one_run launder-warm || { shutdown; return 1; }
  say "stopping instance BEFORE any mutation (no out-of-band writes while a server owns the dir)"
  shutdown
  python3 $TOOLS snapshot $kv "$NONCE" $OUT/snap.json
  local rc=$?
  if [ $rc = 4 ]; then
    say "INCONCLUSIVE: no run-identified stamped checkpoint (store never fired or parse failed)"
    return 3
  fi
  python3 $TOOLS corrupt $kv "$NONCE" $OUT/snap.json || { say "INCONCLUSIVE: every corruption skipped"; return 3; }
  launch $bin $kv $OUT/server-launder2.log || return 3
  one_run launder-stale || { shutdown; return 1; }
  say "waiting 45 s for async anchor stores"
  sleep 45
  shutdown
  say "load-side evidence (log tail):"
  grep -a 'fingerprint mismatch\|stamp unreadable' $OUT/server-launder2.log | tail -3
  python3 $TOOLS classify $kv "$NONCE" $OUT/snap.json
  rc=$?
  case $rc in
    0) say "PASS: stale stamp could not launder - checkpoints replaced with fresh stores" ;;
    1) say "FAIL/LAUNDERED: stamps restored in place over stale rows (expected for a PRE_BIN control run)" ;;
    *) say "INCONCLUSIVE: missing/skipped/foreign/stuck - see lines above" ;;
  esac
  return $rc
}

case "${1:-}" in
  selftest) mkdir -p $OUT; phase_selftest ;;
  parity)   phase_parity ;;
  launder)  BIN=$2 phase_launder ;;
  *) echo "usage: $0 selftest | parity | launder <binary>"
     echo "  parity needs PRE_BIN + POST_BIN env; launder takes the binary as arg 2"
     echo "  requires an engine window (production stopped); never targets production"
     exit 2 ;;
esac
