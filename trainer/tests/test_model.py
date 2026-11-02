"""Model-mirror load test: the extracted official weights must populate the
torch mirror exactly (72 non-expert tensors), with magnitudes matching the
extraction report (independent q8_0 decode sanity from the night-A run)."""
import json
import os

import pytest
import torch

from dspark.model import DSparkHead

TENSOR_DIR = "/Volumes/FireCuda520/ds4-train/tensors-20261101"
NPZ = os.path.join(TENSOR_DIR, "mtp_nonexpert_f32.npz")
REPORT = os.path.join(TENSOR_DIR, "extract_report.json")
blade = pytest.mark.skipif(not os.path.exists(NPZ), reason="tensors absent")


@blade
def test_state_dict_shape_contract():
    h = DSparkHead()
    sd = h.state_dict()
    assert len(sd) == 81 - 9            # experts stay native/frozen
    assert sd["stages.0.main_proj"].shape == (4096, 12288)
    assert sd["stages.2.markov_w1"].shape == (129280, 256)
    assert sd["stages.2.confidence_proj"].shape == (1, 4352)


@blade
def test_npz_load_populates_all_72():
    h = DSparkHead()
    n = h.load_npz(NPZ)
    assert n == 72, f"loaded {n}/72"
    rep = {e["name"]: e for e in json.load(open(REPORT))["extracted"]}
    mp = h.state_dict()["stages.0.main_proj"]
    assert abs(float(mp.abs().max()) - rep["mtp.0.main_proj.weight"]["absmax"]) \
        < 1e-3
    assert float(h.state_dict()["stages.2.markov_w1"].std()) == pytest.approx(
        rep["mtp.2.markov_head.markov_w1.weight"]["std"], rel=1e-3)
    # trainable parameters must be exactly the markov + confidence + norms
    trainable = [name for name, p in h.named_parameters() if p.requires_grad]
    assert set(trainable) == {
        "stages.0.main_norm", "stages.2.norm",
        "stages.2.markov_w1", "stages.2.markov_w2",
        "stages.2.confidence_proj",
    }
