"""Loop quarantine: depth rule, span geometry (B1 fix), log parsing, loader."""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dspark.data_contract import FLAG_SEGMENT_START  # noqa: E402
from dspark.loops import (build_quarantine, file_exclusions,  # noqa: E402
                          parse_spans, _segments)

HDR = struct.Struct("<IiBBBB5i")
REC = 32 + 12288
FEAT = b"\x00" * 12288


def _rec(pos, seed, lab1, nlab=2, flags=0):
    # labels[1] (field 7) is the bigram partner used by audits + train_v1
    return HDR.pack(pos, seed, 3, nlab, 3, flags, seed, lab1, 0, 0, 0) + FEAT


def _write(path, recs):
    with open(path, "wb") as f:
        f.write(b"".join(recs))


def _healthy(pos0, count, seg_start=True):
    return [_rec(pos0 + k, 1000 + k, 5000 + k,
                 flags=FLAG_SEGMENT_START if seg_start and k == 0 else 0)
            for k in range(count)]


def _loop(pos0, reps=40, uniq=20):
    out, p = [], pos0
    for _ in range(reps):
        for u in range(uniq):
            out.append(_rec(p, u, 100 + u,
                            flags=FLAG_SEGMENT_START if p == pos0 else 0))
            p += 1
    return out


def test_depth_rule_flags_loops_only(tmp_path):
    rp = str(tmp_path / "train_pid9.recs")
    _write(rp, _healthy(0, 1000) + _loop(1000, reps=25, uniq=20))
    segs = _segments(rp)
    assert [len(s[1]) for s in segs] == [1000, 500]
    q = {"version": 3, "segments": {"dayX/train_pid9": [1000]}, "spans": []}
    excl = file_exclusions(rp, q)           # day derives from parent dir:
    assert excl == set()                    # tmp_path name != "dayX"
    # exact key:
    real_day = os.path.basename(str(tmp_path))
    q["segments"] = {f"{real_day}/train_pid9": [1000]}
    excl = file_exclusions(rp, q)
    assert len(excl) == 500 and min(excl) == 1000


def test_log_parse_gen_geometry(tmp_path):
    lp = tmp_path / "ds4.log"
    lp.write_text(
        "1103 00:10:00 ds4: DS4_TD_PARITY draft dump ACTIVE -> "
        "/x/parity_pid555.bin (max 200000 cycles)\n"
        "1103 00:11:00 ds4-server: chat ctx=100000..174585:74585 gen=7000 "
        "TOOLS decoding chunk=20.0 t/s\n"
        "1103 00:12:00 ds4-server: chat ctx=174585..175303:718 thinking "
        "budget 8192 enforced after 8192 in-span tokens (gen=8192)\n"
        "1103 00:13:00 ds4-server: chat ctx=190000..190050:50 thinking loop "
        "tripwire: closed span at 2747 tokens (period 447)\n"
        "1103 00:13:30 ds4-server: completion ctx=120000..125000:5000 "
        "thinking loop tripwire: closed span at 3000 tokens (period 77)\n")
    spans = parse_spans([str(lp)])
    assert spans[0] == [175303, 183495, "budget", 555]      # [B+G-T, B+G]
    assert spans[1] == [190050, 192797, "trip", 555]        # no progress -> [B, B+T]
    assert spans[2] == [125000, 128000, "trip", 555]        # completion prefix
    # review B1 worked-example regression:
    assert spans[0][0] == 175303 and spans[0][1] == 175303 + 8192


def test_trip_gen_recovered_from_progress(tmp_path):
    lp = tmp_path / "d.log"
    lp.write_text(
        "1103 00:00:00 parity_pid777.bin active\n"
        "1103 00:01:00 ds4-server: chat ctx=200000..24595:175986 gen=3900 "
        "THINKING decoding chunk=18 t/s\n"
        "1103 00:02:00 ds4-server: chat ctx=24014..24595:581 thinking loop "
        "tripwire: closed span at 4058 tokens (period 447)\n")
    spans = parse_spans([str(lp)])
    assert spans[0] == [24595 + 3900 - 4058, 24595 + 3900, "trip", 777]


def test_attributed_span_respects_lineage(tmp_path):
    # deep window in a LONG segment is excised; an unrelated short segment
    # whose pos overlaps but never reaches hi survives (S2)
    rp = str(tmp_path / "train_pid555.recs")
    _write(rp, _loop(1000, reps=10, uniq=6))          # 60 recs pos 1000-1059
    q = {"version": 3, "segments": {}, "spans": [[1000, 1059, "trip", 555]]}
    assert len(file_exclusions(rp, q)) == 60


def test_foreign_pid_untouched(tmp_path):
    rp = str(tmp_path / "train_pid666.recs")
    _write(rp, _loop(1000, reps=2, uniq=30))
    q = {"version": 3, "segments": {}, "spans": [[1000, 1059, "trip", 555]]}
    assert file_exclusions(rp, q) == set()


def test_quarantine_json_roundtrip(tmp_path):
    daydir = tmp_path / "20269999-dayX"
    daydir.mkdir()
    _write(str(daydir / "train_pid7.recs"),
           _healthy(0, 800) + _loop(800, reps=30, uniq=15))
    q = build_quarantine(days=["20269999-dayX"], root=str(tmp_path))
    assert q["version"] == 3
    assert "20269999-dayX/train_pid7" in q["segments"]
    p = str(tmp_path / "q.json")
    json.dump(q, open(p, "w"))
    qq = json.load(open(p))
    excl = file_exclusions(str(daydir / "train_pid7.recs"), qq)
    assert len(excl) == 450


def test_version_guard(tmp_path):
    p = tmp_path / "old.json"
    json.dump({"version": 2, "segments": {}, "spans": []}, open(p, "w"))
    from dspark.loops import load_quarantine
    try:
        load_quarantine(str(p))
        assert False, "should have raised"
    except RuntimeError:
        pass


def test_train_loader_skips_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr("dspark.train_v1.CORPUS", str(tmp_path))
    from dspark import train_v1
    daydir = tmp_path / "20269999-dayY"
    daydir.mkdir()
    _write(str(daydir / "train_pid5.recs"),
           _healthy(0, 600) + _loop(600, reps=40, uniq=10))
    q = build_quarantine(days=["20269999-dayY"], root=str(tmp_path))
    X, S, Y, C, L = train_v1.load_day("20269999-dayY")
    assert len(S) == 1000
    X, S, Y, C, L = train_v1.load_day("20269999-dayY", quarantine=q)
    assert len(S) == 600



def test_attributed_budget_needs_depth_evidence(tmp_path):
    # depth-2 attributed budget window = legit long thinking -> KEPT now
    # (policy Nov-03: legit 8k chops measured 1.9-3.8x, loops 4.1-11.6x)
    rp = str(tmp_path / "train_pid555.recs")
    _write(rp, _loop(1000, reps=2, uniq=40))           # 80 recs, depth 2
    q = {"version": 3, "segments": {},
         "spans": [[1000, 1079, "budget", 555]]}
    assert file_exclusions(rp, q) == set()
    rp2 = str(tmp_path / "train_pid556.recs")
    _write(rp2, _loop(1000, reps=6, uniq=12))          # 72 recs, depth 6
    q2 = {"version": 3, "segments": {},
          "spans": [[1000, 1071, "budget", 556]]}
    assert len(file_exclusions(rp2, q2)) == 72
