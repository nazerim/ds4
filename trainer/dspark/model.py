"""Torch mirror of the official DSpark Vision-Exp draft head.

Structure is reverse-engineering-VERIFIED against the support GGUF inventory
(trainer/tests extract_report: 81 tensors = 24 bound x 3 stages + 9 special)
and ds4.c binding list (ds4.c:8750-8810 dspark_bind_tensor calls).

Weights load from mtp_nonexpert_f32.npz (blade: ds4-train/tensors-20261101/).
Expert stacks (q2_k / iq2_xxs) stay native — FROZEN for fine-tuning; a
runtime dequant kernel or one-time f32 materialization is a documented
day-work TODO (see PLAN-TRAINER-20261101.md).

forward() is intentionally not implemented until matched against the engine:
exact per-op semantics live in ds4.c draft paths (prepare_impl ~79700-79990
local + TP twin; metal graph in metal/dspark*.m if present). Anchor comments
below mark each region's authoritative source.
"""
from __future__ import annotations

import numpy as np
import torch
from torch import nn

HIDDEN = 4096
TAPS = 3
VOCAB = 129280
RANK = 256
EXPERTS = 256
N_STAGES = 3
HC_STREAMS = 4          # DS4_N_HC; hc_* mixing dims below
HC_MIX_IN = 24          # hc_attn_fn [16384, 24] observed

# explicit GGUF->module map, from ds4.c:8740-8810 bind list (24/stage x 3)
# + 9 specials; expert tensors (q2_k/iq2_xxs) intentionally absent: FROZEN.
_PER_STAGE = {
    "hc_attn_base.weight": "hc_attn.base",
    "hc_attn_fn.weight": "hc_attn.fn",
    "hc_attn_scale.weight": "hc_attn.scale",
    "attn_norm.weight": "attn.norm",
    "attn_q_a.weight": "attn.q_a",
    "attn_q_a_norm.weight": "attn.q_a_norm",
    "attn_q_b.weight": "attn.q_b",
    "attn_kv.weight": "attn.kv",
    "attn_kv_a_norm.weight": "attn.kv_a_norm",
    "attn_sinks.weight": "attn.sinks",
    "attn_output_a.weight": "attn.output_a",
    "attn_output_b.weight": "attn.output_b",
    "hc_ffn_base.weight": "hc_ffn.base",
    "hc_ffn_fn.weight": "hc_ffn.fn",
    "hc_ffn_scale.weight": "hc_ffn.scale",
    "ffn_norm.weight": "moe.norm",
    "ffn_gate_inp.weight": "moe.gate_inp",
    "exp_probs_b.bias": "moe.exp_probs_b",
    "ffn_gate_shexp.weight": "moe.sh_gate",
    "ffn_up_shexp.weight": "moe.sh_up",
    "ffn_down_shexp.weight": "moe.sh_down",
}
GGUF_TO_MODULE = {f"mtp.{s}.{suffix}": f"stages.{s}.{mod}"
                  for s in range(N_STAGES)
                  for suffix, mod in _PER_STAGE.items()}
GGUF_TO_MODULE.update({
    "mtp.0.main_proj.weight": "stages.0.main_proj",
    "mtp.0.main_norm.weight": "stages.0.main_norm",
    "mtp.2.norm.weight": "stages.2.norm",
    "mtp.2.hc_head_base.weight": "stages.2.hc_head_base",
    "mtp.2.hc_head_fn.weight": "stages.2.hc_head_fn",
    "mtp.2.hc_head_scale.weight": "stages.2.hc_head_scale",
    "mtp.2.markov_head.markov_w1.weight": "stages.2.markov_w1",
    "mtp.2.markov_head.markov_w2.weight": "stages.2.markov_w2",
    "mtp.2.confidence_head.proj.weight": "stages.2.confidence_proj",
})


class HCMLayerNorm(nn.Module):
    """Hyper-connection mixing (hc_{attn,ffn}_base/fn/scale per stage).
    Engine ref: ds4.c tensor_expect_layout ~5946 (fn [EMBD*N_HC, mix_in])."""

    def __init__(self, tag: str):
        super().__init__()
        self.register_parameter("base", nn.Parameter(torch.zeros(24), False))
        self.register_parameter("fn",
                                nn.Parameter(torch.zeros(HC_MIX_IN,
                                                         HIDDEN * HC_STREAMS),
                                             False))
        self.register_parameter("scale", nn.Parameter(torch.ones(3), False))
        self.tag = tag

    def forward(self, x):
        raise NotImplementedError("mirror semantics TODO — anchor ds4.c "
                                  f"hc_{self.tag} graph path")


class AttnBlock(nn.Module):
    """MLA-style attention per stage: q_a->q_b (32768=64x512?), kv compressed
    512 + norm, 64 head sinks, output via low-rank 8192. Engine ref: bind
    names attn_* (ds4.c:8774+); metal kernels under metal/."""

    def __init__(self):
        super().__init__()
        d = dict(
            norm=torch.zeros(HIDDEN),
            q_a=torch.zeros(1024, HIDDEN), q_a_norm=torch.zeros(1024),
            q_b=torch.zeros(32768, 1024),
            kv=torch.zeros(512, HIDDEN), kv_a_norm=torch.zeros(512),
            sinks=torch.zeros(64),
            output_a=torch.zeros(8192, HIDDEN), output_b=torch.zeros(HIDDEN, 8192),
        )
        for k, v in d.items():
            self.register_parameter(k, nn.Parameter(v, False))


class MoEBlock(nn.Module):
    """Router + shared expert dense; 256 experts kept FROZEN (native format,
    not materialized here). ffn_gate_inp [256, 4096], shexp gate/up/down."""

    def __init__(self):
        super().__init__()
        d = dict(
            norm=torch.zeros(HIDDEN),
            gate_inp=torch.zeros(EXPERTS, HIDDEN),
            exp_probs_b=torch.zeros(EXPERTS),
            sh_gate=torch.zeros(2048, HIDDEN),
            sh_up=torch.zeros(2048, HIDDEN),
            sh_down=torch.zeros(HIDDEN, 2048),
        )
        for k, v in d.items():
            self.register_parameter(k, nn.Parameter(v, False))


class Stage(nn.Module):
    def __init__(self, idx: int):
        super().__init__()
        self.idx = idx
        self.hc_attn = HCMLayerNorm("attn")
        self.attn = AttnBlock()
        self.hc_ffn = HCMLayerNorm("ffn")
        self.moe = MoEBlock()


class DSparkHead(nn.Module):
    """Official dspark head: input projection from the 3 target-layer taps
    (layers 40/41/42 of Vision-Exp -> 12288 -> main_proj -> 4096), N stage
    blocks (each a full draft layer), final norm + hc_head mixing, markov
    transition head and confidence head on the last stage."""

    def __init__(self):
        super().__init__()
        self.stages = nn.ModuleList(Stage(i) for i in range(N_STAGES))
        s0, sf = self.stages[0], self.stages[-1]
        s0.register_parameter("main_proj",
                              nn.Parameter(torch.zeros(HIDDEN,
                                                       TAPS * HIDDEN), False))
        s0.register_parameter("main_norm", nn.Parameter(torch.ones(HIDDEN)))
        sf.register_parameter("norm", nn.Parameter(torch.ones(HIDDEN)))
        sf.register_parameter("hc_head_base", nn.Parameter(torch.zeros(4), False))
        sf.register_parameter("hc_head_fn",
                              nn.Parameter(torch.zeros(4, HIDDEN * HC_STREAMS),
                                           False))
        sf.register_parameter("hc_head_scale", nn.Parameter(torch.ones(1), False))
        sf.register_parameter("markov_w1", nn.Parameter(torch.zeros(VOCAB, RANK)))
        sf.register_parameter("markov_w2", nn.Parameter(torch.zeros(VOCAB, RANK)))
        sf.register_parameter("confidence_proj",
                              nn.Parameter(torch.zeros(1, HIDDEN + RANK)))

    def load_npz(self, npz_path: str):
        """Populate every non-expert parameter from the blade extraction via
        the explicit engine binding map (ds4.c:8740-8810 dspark_bind_tensor).
        Returns count loaded; raises if != 72."""
        src = np.load(npz_path)
        got = 0
        missing = []
        for gguf_key, mod_key in GGUF_TO_MODULE.items():
            if gguf_key not in src:
                missing.append(gguf_key)
                continue
            sd = self.state_dict()
            if mod_key not in sd:
                missing.append(f"{gguf_key}->{mod_key}")
                continue
            v = torch.from_numpy(src[gguf_key])
            tgt = sd[mod_key]
            if v.shape != tgt.shape and v.numel() == tgt.numel():
                v = v.reshape(tgt.shape)      # (1,N) norm vectors -> (N,)
            if v.shape == tgt.shape:
                with torch.no_grad():
                    tgt.copy_(v)
                got += 1
            elif v.T.shape == tgt.shape:
                with torch.no_grad():
                    tgt.copy_(v.T)
                got += 1
            else:
                missing.append(f"{gguf_key} shape {tuple(v.shape)} != "
                               f"{tuple(tgt.shape)}")
        if missing:
            raise RuntimeError(f"load_npz mismatches: {missing}")
        self.n_loaded = got
        return got

