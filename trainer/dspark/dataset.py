"""Segment assembly for capture corpora.

A training segment is a maximal run of records with strictly increasing pos.
Boundaries: flags==1 (authoritative from v1.3; also most of v1.2) PLUS a
break-split at pos <= prev.pos (day-1's two unhooked disk-KV adoptions,
where flags could not fire). This mirrors tools/td_classify.py semantics.

Excluded records (n_label==0): kept in segment membership as structural
placeholders (their pos participates in gap accounting) but masked out of
targets (drop rule shared across the toolchain).
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field

from .data_contract import CORPUS_ROOT, FLAG_SEGMENT_START
from .records import RecHeader, read_headers


@dataclass
class Segment:
    seg_id: str                     # f"{pid-stem}:{start_idx}"
    file: str
    start: int
    recs: list = field(default_factory=list)   # RecHeader list, file order

    @property
    def usable(self):
        return [r for r in self.recs if r.usable]

    @property
    def depth(self):
        return max((r.pos for r in self.recs), default=0)

    def stats(self):
        u = self.usable
        gaps = [r.streak for r in u if r.gap_for_stats]
        return {"records": len(self.recs), "usable": len(u),
                "depth_max": self.depth,
                "mean_gap": (sum(gaps) / len(gaps)) if gaps else None}


def build_segments(recs_path: str) -> list[Segment]:
    stem = os.path.basename(recs_path).removesuffix(".recs")
    segs: list[Segment] = []
    cur: Segment | None = None
    prev_pos: int | None = None
    for i, r in read_headers(recs_path):
        boundary = (cur is None or (r.flags & FLAG_SEGMENT_START)
                    or (prev_pos is not None and r.pos <= prev_pos))
        if boundary and cur is not None:
            segs.append(cur)
            cur = None
        if cur is None:
            cur = Segment(seg_id=f"{stem}:{i}", file=recs_path, start=i)
        cur.recs.append(r)
        prev_pos = r.pos
    if cur and cur.recs:
        segs.append(cur)
    return segs


def corpus_segments(days: list[str] | None = None,
                    root: str = CORPUS_ROOT) -> list[Segment]:
    """Segments across banked day dirs (and optional 'active')."""
    if days is None:
        days = sorted(d for d in os.listdir(root)
                      if os.path.isdir(os.path.join(root, d)))
    segs = []
    for d in days:
        for p in sorted(glob.glob(os.path.join(root, d, "train_pid*.recs"))):
            segs.extend(build_segments(p))
    return segs


def split_manifest(days_train: list[str], days_holdout: list[str],
                   root: str = CORPUS_ROOT, out: str | None = None):
    """Day-level, segment-granular split. Returns dict of counts; writes
    JSON mapping seg_id -> split when `out` given."""
    mapping = {}
    for group, days in (("train", days_train), ("holdout", days_holdout)):
        for s in corpus_segments(days, root=root):
            mapping[s.seg_id] = group
    if out:
        json.dump(mapping, open(out, "w"))
    return {"train_segments": sum(v == "train" for v in mapping.values()),
            "holdout_segments": sum(v == "holdout" for v in mapping.values())}
