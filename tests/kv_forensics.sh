#!/bin/zsh
# kv_forensics.sh — pull the first-mismatch evidence for a token position.
#
# usage: ./kv_forensics.sh <common_pos> [tracefile]
#   e.g. ./kv_forensics.sh 322010
#
# Emits (a) the live miss lines from log/ds4.log and (b) the decoded
# token_window block(s) from log/ds4.trace (the trace already prints token
# text per side; `==` lines agree, `!=` is the drift boundary - two
# tokenizations of identical bytes = BPE variant/offset effect, not text
# corruption).
#
# NOTE: token ids are gguf-specific - the production model moved off the old
# TQ/ESOTERICKARMA gguf (Sep 6) and the vocab maps differ (id 11316 =
# ' mailbox' there, ' smooth' in the current file). For text<->id work on a
# specific gguf use tests/gguf_tokenizer.py dump/tokenize.
set -e
REPO=/Users/naz/Projects/ds4
POS=$1
TRACE=${2:-$REPO/log/ds4.trace}
[ -z "$POS" ] && { sed -n '2,8p' $0; exit 1; }
cd $REPO
echo "== miss lines =="
grep "common=$POS " log/ds4.log | tail -3 || echo "(none in current log)"
echo "== trace window =="
for ln in $(grep -n "first_mismatch_token: $POS\$" "$TRACE" | cut -d: -f1 | tail -2); do
  sed -n "${ln},$((ln+22))p" "$TRACE" | grep -vE '^\s*$'
  echo "---"
done
