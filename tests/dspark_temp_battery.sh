#!/bin/bash
# DSpark A/B at TEMPERATURE 1.0 (production sampling regime) vs greedy.
# Reports the sample_* counters that are identically 0 at temp=0.
# Run only with no ds4 engine up (process lock). Sequential.
set -eu
cd "$(dirname "$0")/.."
MODEL=ds4flash.gguf
SUPPORT=gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf
BIN=./ds4
TEMP="${1:-1.0}"
TOKENS="${2:-512}"
D=/private/var/folders/gp/hjkf5cnx3vg4pxxv0zbd1c8h0000gn/T
PDIR=$(mktemp -d /tmp/dspark-t1-prompts.XXXXXX)
TSV=log/dspark-temp${TEMP}-$(date +%Y%m%d-%H%M%S).tsv
trap 'rm -rf "$PDIR"' EXIT

emit() { local id="$1"; shift; printf '%s' "$*" > "$PDIR/$id.txt"; }
emit pyrefactor 'Refactor this Python class to remove duplicated validation and make it thread-safe, keeping the public API. Explain each change briefly.

class OrderService:
    def __init__(self, repo, mailer):
        self.repo = repo; self.mailer = mailer
    def create_order(self, user_id, items):
        if user_id is None: raise ValueError("user_id required")
        if not items: raise ValueError("items required")
        for it in items:
            if it["qty"] <= 0: raise ValueError("bad qty")
        o = self.repo.save({"user": user_id, "items": items, "status": "new"})
        self.mailer.send(user_id, "created", str(o)); return o
    def update_order(self, oid, user_id, items):
        if user_id is None: raise ValueError("user_id required")
        if not items: raise ValueError("items required")
        for it in items:
            if it["qty"] <= 0: raise ValueError("bad qty")
        o = self.repo.update(oid, {"user": user_id, "items": items})
        self.mailer.send(user_id, "updated", str(o)); return o'
emit bashops 'Write a production-grade bash function rotate_logs that takes a directory, a glob, and a max rotation count, compressing old logs. Handle spaces in filenames, prefer POSIX constructs, explain edge cases.'
emit cstruct 'Write a C11 single-file LRU cache, fixed capacity, intrusive doubly-linked list + open-addressing hash with FNV-1a. get/put, memory-ownership comments, main() demoing eviction. Small embedded-C style, no allocations in hot paths.'
emit docrewrite 'Rewrite this runbook section for a new on-call engineer. Preserve every factual claim, tighten prose, add a "first 10 minutes" checklist.

When the disk cache fills, the KV store retires lineages untouched within the retire-grace window. Frontier pinning keeps the deepest anchor of every live conversation. A miss after retirement re-prefills from the nearest surviving anchor; divergence anchors bound rebuild cost when the common prefix falls between rungs.'
emit mathq 'A factory produces widgets across three shifts. Morning produces 20% more than night, evening 15% fewer than morning, night 400 units. Defect rates 1.5%, 2.2%, 0.8%. If a shipment must be at least 98% defect-free to go whole, can the day production ship whole, or how many good units ship?'
emit toolcall 'You are a coding agent with tools read_file, write_file, run_tests. User: "config.json has too many keys; sort them and drop deprecated ones, then re-run tests." Emit the first tool call and plan the remaining steps in detail.'

run_case() {
  local id="$1" mode="$2" exact="$3"
  local err="$PDIR/$id.$mode.$exact.err"
  local ex=""; [ "$exact" = 1 ] && ex="--mtp-exact-sampling"
  if [ "$mode" = dspark ]; then
    DS4_DSPARK_STATS=1 "$BIN" --dspark $ex -m "$MODEL" --mtp-model "$SUPPORT" \
      --tokens "$TOKENS" --temp "$TEMP" --top-p 0.95 --min-p 0.05 --seed 12345 --nothink \
      -p "$(cat "$PDIR/$id.txt")" >/dev/null 2>"$err" || true
  else
    "$BIN" -m "$MODEL" --tokens "$TOKENS" --temp "$TEMP" --top-p 0.95 --min-p 0.05 \
      --seed 12345 --nothink -p "$(cat "$PDIR/$id.txt")" >/dev/null 2>"$err" || true
  fi
  local tps stats
  tps=$(sed -n 's/.*generation: \([0-9.]*\) t\/s.*/\1/p' "$err" | tail -1)
  stats=""; [ "$mode" = dspark ] && stats=$(grep 'DSpark stats' "$err" | tail -1 | sed 's/^ds4: DSpark stats //' || true)
  printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$mode" "$exact" "${tps:-NA}" "$stats" | tee -a "$TSV"
}

echo -e "id\tmode\texact\ttps\tstats" > "$TSV"
for id in pyrefactor bashops cstruct docrewrite mathq toolcall; do
  run_case "$id" baseline 0
  run_case "$id" dspark 0
  run_case "$id" dspark 1
done
echo "wrote $TSV"
