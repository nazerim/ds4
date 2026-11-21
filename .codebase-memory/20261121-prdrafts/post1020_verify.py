#!/usr/bin/env python3
"""Read back a posted PR review-comment reply and require an exact body match.

Uses the repo-scoped route: GET /repos/antirez/ds4/pulls/comments/{id}.
The PR-scoped /repos/antirez/ds4/pulls/1020/comments/{id} returns 404 for these
review comments, which is why the read-back is done this way.
"""
import json
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: post1020_verify.py <comment_id> <thread_root_id> <body_file>",
              file=sys.stderr)
        return 2
    cid, thread, body_file = sys.argv[1], int(sys.argv[2]), sys.argv[3]

    expected = open(body_file, encoding="utf-8").read().rstrip("\n")

    res = subprocess.run(
        ["gh", "api", f"repos/antirez/ds4/pulls/comments/{cid}"],
        capture_output=True, text=True)
    if res.returncode != 0:
        # A failed read-back is NOT permission to repost: the comment may exist.
        print("  READ-BACK FAILED to fetch: do NOT repost; inspect the list first.",
              file=sys.stderr)
        print("  " + res.stderr.strip().splitlines()[0] if res.stderr else "", file=sys.stderr)
        return 1

    got = json.loads(res.stdout)
    actual = (got.get("body") or "").rstrip("\n")
    problems = []
    if got.get("in_reply_to_id") != thread:
        problems.append(f"in_reply_to_id={got.get('in_reply_to_id')} expected {thread}")
    if got.get("user", {}).get("login") != "nazerim":
        problems.append(f"author={got.get('user', {}).get('login')}")
    if actual != expected:
        problems.append(f"body differs (posted {len(actual)} chars vs approved "
                        f"{len(expected)} chars)")
    if problems:
        print("  READ-BACK MISMATCH: " + "; ".join(problems), file=sys.stderr)
        print("  Do NOT repost. Inspect, then decide.", file=sys.stderr)
        return 1

    print(f"  READ-BACK EXACT: {got.get('html_url')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
