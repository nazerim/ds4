#!/bin/bash
# DSpark acceptance + speed A/B on realistic opencode-like traffic.
# Usage: ./tests/dspark_traffic_probe.sh [tokens] [mode: all|dspark-only]
# Sequential (engine process lock); run only with no ds4 engine up.
set -eu
cd "$(dirname "$0")/.."

TOKENS="${1:-512}"
MODEL=ds4flash.gguf
SUPPORT=gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf
BIN=./ds4
PDIR=$(mktemp -d /tmp/dspark-probe-prompts.XXXXXX)
OUTF=log/dspark-traffic-$(date +%Y%m%d-%H%M%S).tsv
trap 'rm -rf "$PDIR"' EXIT

emit() { local id="$1" body="$2"; printf '%s' "$body" > "$PDIR/$id.txt"; }

emit pyrefactor 'Refactor this Python class to remove the duplicated validation logic and make it thread-safe. Keep the public API identical. Explain each change briefly.

```python
class OrderService:
    def __init__(self, repo, mailer):
        self.repo = repo
        self.mailer = mailer
    def create_order(self, user_id, items):
        if user_id is None:
            raise ValueError("user_id required")
        if not items:
            raise ValueError("items required")
        for it in items:
            if it["qty"] <= 0:
                raise ValueError("bad qty")
        order = self.repo.save({"user": user_id, "items": items, "status": "new"})
        self.mailer.send(user_id, "order created", str(order))
        return order
    def update_order(self, order_id, user_id, items):
        if user_id is None:
            raise ValueError("user_id required")
        if not items:
            raise ValueError("items required")
        for it in items:
            if it["qty"] <= 0:
                raise ValueError("bad qty")
        order = self.repo.update(order_id, {"user": user_id, "items": items})
        self.mailer.send(user_id, "order updated", str(order))
        return order
```'

emit bashops 'Write a production-grade bash function named rotate_logs that takes a directory, a basename glob, and a max rotation count, and compresses old logs. Handle spaces in filenames, prefer POSIX-compatible constructs, and explain the edge cases you cover.'

emit cstruct 'Write a C11 single-file LRU cache with fixed capacity using an intrusive doubly-linked list and an open-addressing hash table with FNV-1a. Provide get/put, comment memory ownership, and add a main() demoing eviction. Follow small embedded-C style: no allocations inside hot paths.'

emit docrewrite 'Rewrite the following internal runbook section for a new on-call engineer. Preserve every factual claim, tighten the prose, and add a short "first 10 minutes" checklist.

When the disk cache fills, the KV store retires lineages that were not touched within the retire-grace window. Frontier pinning keeps the deepest anchor of every live conversation. A miss after retirement re-prefills from the nearest surviving anchor; divergence anchors bound the rebuild cost when the common prefix falls between rungs. If the blade is unmounted, the engine refuses to start rather than silently creating the store on the internal SSD.'

emit mathq 'A factory produces widgets across three shifts. Morning produces 20% more than night, evening produces 15% fewer than morning, and night produces 400 units. Defect rates are 1.5%, 2.2%, and 0.8% respectively. If a shipment must be at least 98% defect-free to go out whole, can the day production ship whole, or how many good units ship?'

emit toolcall 'You are a coding agent with tools read_file, write_file, run_tests. The user says: "config.json has too many keys; sort them and drop the deprecated ones, then re-run the tests." Emit the first tool call you would make and briefly plan the remaining steps.'

run_case() {
  local id="$1" think="$2" mode="$3"
  local tf="--nothink"; [ "$think" = think ] && tf="--think"
  local err="$PDIR/$id.$think.$mode.err"
  if [ "$mode" = dspark ]; then
    DS4_DSPARK_STATS=1 "$BIN" --dspark -m "$MODEL" --mtp-model "$SUPPORT" \
      --tokens "$TOKENS" --temp 0 --seed 12345 $tf -p "$(cat "$PDIR/$id.txt")" \
      >/dev/null 2>"$err" || true
  else
    "$BIN" -m "$MODEL" --tokens "$TOKENS" --temp 0 --seed 12345 $tf \
      -p "$(cat "$PDIR/$id.txt")" >/dev/null 2>"$err" || true
  fi
  local tps stats
  tps=$(sed -n 's/.*generation: \([0-9.][0-9.]*\) t\/s.*/\1/p' "$err" | tail -1)
  stats=""
  [ "$mode" = dspark ] && stats=$(grep 'DSpark stats' "$err" | tail -1 | sed 's/^ds4: DSpark stats //' || true)
  printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$think" "$mode" "${tps:-NA}" "$stats" | tee -a "$OUTF"
}

echo -e "id\tthink\tmode\ttps\tdspark_stats" > "$OUTF"
for id in pyrefactor bashops cstruct docrewrite mathq toolcall; do
  run_case "$id" nothink baseline
  run_case "$id" nothink dspark
done
run_case mathq think baseline
run_case mathq think dspark
echo "wrote $OUTF"
