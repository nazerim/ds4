#!/bin/bash
# Follow-ups: (1) toolcall retest @1024 with output capture, (2) long-ctx (~50k) A/B.
set -eu
cd "$(dirname "$0")/.."
MODEL=ds4flash.gguf
SUPPORT=gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf
BIN=./ds4
D=/private/var/folders/gp/hjkf5cnx3vg4pxxv0zbd1c8h0000gn/T
LOG=log/dspark-followups-$(date +%H%M%S).log
exec > >(tee -a "$LOG") 2>&1

echo "== 1. toolcall @1024 =="
P='You are a coding agent with tools read_file, write_file, run_tests. The user says: "config.json has too many keys; sort them and drop the deprecated ones, then re-run the tests." Emit the first tool call you would make and briefly plan the remaining steps. Write out the full plan in detail including every step, then emit the tool call again in DSML.'
"$BIN" -m "$MODEL" --tokens 1024 --temp 0 --seed 12345 --nothink -p "$P" > "$D/tc.base.out" 2> "$D/tc.base.err" || true
DS4_DSPARK_STATS=1 "$BIN" --dspark -m "$MODEL" --mtp-model "$SUPPORT" --tokens 1024 --temp 0 --seed 12345 --nothink -p "$P" > "$D/tc.dsp.out" 2> "$D/tc.dsp.err" || true
echo "BASE tps=$(sed -n 's/.*generation: \([0-9.]*\) t\/s.*/\1/p' "$D/tc.base.err" | tail -1) bytes=$(wc -c < "$D/tc.base.out")"
echo "DSPK tps=$(sed -n 's/.*generation: \([0-9.]*\) t\/s.*/\1/p' "$D/tc.dsp.err" | tail -1) bytes=$(wc -c < "$D/tc.dsp.out")"
grep 'DSpark stats' "$D/tc.dsp.err" | tail -1 | sed 's/^ds4: DSpark stats //' | sed 's/time_ms.*//'
cmp -s "$D/tc.base.out" "$D/tc.dsp.out" && echo "outputs identical" || echo "outputs differ"

echo
echo "== 2. long-ctx A/B (prompt = $D/longctx_64k.txt) =="
LP=$(cat "$D/longctx_64k.txt")
"$BIN" -m "$MODEL" --ctx 98304 --tokens 256 --temp 0 --seed 12345 --nothink -p "$LP" > "$D/lc.base.out" 2> "$D/lc.base.err" || true
DS4_DSPARK_STATS=1 "$BIN" --dspark -m "$MODEL" --mtp-model "$SUPPORT" --ctx 98304 --tokens 256 --temp 0 --seed 12345 --nothink -p "$LP" > "$D/lc.dsp.out" 2> "$D/lc.dsp.err" || true
echo "BASE: $(grep -E 'prefill tokens|prompt' "$D/lc.base.err" | head -2 | tr '\n' ' ')"
echo "BASE prefill=$(sed -n 's/.*prefill: \([0-9.]*\) t\/s.*/\1/p' "$D/lc.base.err" | tail -1) tps=$(sed -n 's/.*generation: \([0-9.]*\) t\/s.*/\1/p' "$D/lc.base.err" | tail -1)"
echo "DSPK prefill=$(sed -n 's/.*prefill: \([0-9.]*\) t\/s.*/\1/p' "$D/lc.dsp.err" | tail -1) tps=$(sed -n 's/.*generation: \([0-9.]*\) t\/s.*/\1/p' "$D/lc.dsp.err" | tail -1)"
grep 'DSpark stats' "$D/lc.dsp.err" | tail -1 | sed 's/^ds4: DSpark stats //' | sed 's/time_ms.*//'
grep -oE 'accepted_len_hist=[^ ]+|draft_len_hist=[^ ]+' "$D/lc.dsp.err" | tail -2
cmp -s "$D/lc.base.out" "$D/lc.dsp.out" && echo "outputs identical" || { echo "outputs differ"; head -c 150 "$D/lc.base.out" | tr '\n' ' '; echo; head -c 150 "$D/lc.dsp.out" | tr '\n' ' '; echo; }
echo "FOLLOWUPS_DONE"
