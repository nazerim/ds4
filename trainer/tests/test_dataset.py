"""Loader correctness against KNOWN corpus facts (the day-1 defects suite).

Numbers here are the audited ground truth from 2026-10-29 banking runs:
  day1: 27,371 recs, 153 n_label==0, 42 flag-segments + 2 unhooked adoptions
        (in-batch breaks at idx 1661 & 17378 -> break-split must find them)
  day2: 82,536 recs, 97 n_label==0, 97 segments, 0 breaks, 241+ flushes
  day3:  7,514 recs, 20 dropped(=n_label==0 at manifest time), 20 segments
These tests validate against /Volumes (blade) — skip if absent (CI portability).
"""
import os

import pytest

from dspark.data_contract import CORPUS_ROOT, VOCAB, decode_e4m3
from dspark.dataset import build_segments, corpus_segments, split_manifest
from dspark.records import feature_bytes, feature_mmap, read_headers

DAY1 = os.path.join(CORPUS_ROOT, "20261029-day1")
DAY2 = os.path.join(CORPUS_ROOT, "20261029-day2")
blade = pytest.mark.skipif(not os.path.isdir(DAY1), reason="blade not mounted")


@blade
def test_day1_record_and_exclusion_counts():
    p = os.path.join(DAY1, "train_pid72805.recs")
    recs = list(read_headers(p))
    assert len(recs) == 27371
    assert sum(1 for _, r in recs if not r.usable) == 153
    assert sum(1 for _, r in recs if r.usable) == 27218


@blade
def test_day1_segment_break_split_catches_unhooked_adoptions():
    p = os.path.join(DAY1, "train_pid72805.recs")
    segs = build_segments(p)
    # 42 flagged segment starts + 2 adoption breaks (flags could not fire)
    assert len(segs) == 44
    # every segment strictly monotone in pos
    for s in segs:
        pos = [r.pos for r in s.recs]
        assert all(b > a for a, b in zip(pos, pos[1:])), s.seg_id


@blade
def test_all_usable_records_satisfy_labels0_eq_seed():
    for _, r in read_headers(os.path.join(DAY2, "train_pid73865.recs")):
        if r.usable:
            assert r.labels[0] == r.seed
            assert 0 <= r.seed < VOCAB or r.seed == -1


@blade
def test_labels_in_vocab_range_and_gated():
    for _, r in read_headers(os.path.join(DAY2, "train_pid73865.recs")):
        assert 0 <= r.n_label <= 5
        for j in range(r.n_label):
            assert 0 <= r.labels[j] < VOCAB


@blade
def test_feature_decode_sanity_sample():
    p = os.path.join(DAY2, "train_pid73865.recs")
    fd, mm = feature_mmap(p)
    try:
        for i, r in read_headers(p):
            if i % 4096 or not r.usable:
                continue
            v = decode_e4m3(feature_bytes(mm, i, r.n_feat))
            assert v.shape == (r.n_feat * 4096,)
            assert not bool(((v * v).sum()) != (v * v).sum())  # no NaN
            assert float(v.max()) <= 448.0 and float(v.min()) >= -448.0
            assert float(abs(v).mean()) > 0.3
    finally:
        mm.close()
        fd.close()


@blade
def test_day2_segment_count_matches_flags():
    segs = build_segments(os.path.join(DAY2, "train_pid73865.recs"))
    assert len(segs) == 97                      # v1.2 server: flags all fired


@blade
def test_split_manifest_counts():
    m = split_manifest(["20261029-day1", "20261029-day2"],
                       ["20261029-day3"])
    assert m["train_segments"] == 44 + 97
    assert m["holdout_segments"] == 20
