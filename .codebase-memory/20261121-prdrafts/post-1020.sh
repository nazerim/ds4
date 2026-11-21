#!/usr/bin/env bash
# Post exactly ONE reply to PR antirez/ds4/pulls/1020 thread 4131745125.
# Fail-closed: every check must pass, and nothing is retried automatically.
#
# Deliberately avoids nested quotes: the approved text lives in reply2-approved.txt
# and is compared by checksum, and the read-back is done by post1020_verify.py which
# passes arguments as a list rather than through a shell string.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
pr=repos/antirez/ds4/pulls/1020
thread=4131745125
pin=9cbf7e590c318a421a5ac059fcf9ae68f99fd2d9
body="$DIR/reply2-final.md"
approved="$DIR/reply2-approved.txt"

test -s "$body"
test -s "$approved"

# 1. body must be byte-identical to the approved text (catches silent edits and
#    the truncation class of bug seen earlier in this lane)
cmp -s "$body" "$approved"
echo "  [1/5] body is byte-identical to reply2-approved.txt"

# 2. posting identity must be the PR author
test "$(gh api user --jq .login)" = nazerim
echo "  [2/5] authenticated as nazerim"

# 3. PR unchanged since approval: pinned head, open, ours
state_line=$(gh api "$pr" --jq '[.head.sha,.state,.user.login] | join(" ")')
test "$state_line" = "$pin open nazerim"
echo "  [3/5] PR head/state/author as pinned"

# 4. thread root still the reviewer's unanswered comment on ds4.c:65040
gh api --paginate --slurp "$pr/comments" \
  | jq -e --argjson t "$thread" 'flatten
      | (any(.[]; .id == $t and .user.login == "emilianbold"
             and .in_reply_to_id == null and .path == "ds4.c" and .line == 65040))
        and (all(.[]; .in_reply_to_id != $t))' >/dev/null
echo "  [4/5] thread root valid and not yet answered"

if [[ "${POST_IT:-0}" != "1" ]]; then
  echo "  DRY RUN: preflight passed. Set POST_IT=1 to post."
  exit 0
fi

# 5. post once. Do NOT repost on read-back failure; inspect first.
id=$(gh api -X POST "$pr/comments/$thread/replies" -F "body=@$body" --jq .id)
case "$id" in
  ''|*[!0-9]*) echo "  POST returned a non-numeric id: $id" >&2; exit 1 ;;
esac
echo "  [5/5] POST returned id=$id"
sleep 3

# read-back via the REPO-scoped route: /pulls/1020/comments/{id} 404s,
# /pulls/comments/{id} works.
python3 "$DIR/post1020_verify.py" "$id" "$thread" "$body"
