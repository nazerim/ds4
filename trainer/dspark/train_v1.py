"""Training loop v1 (engine-free): h-conditioned markov draft surrogate.

The engine scores draft candidates as draft_logits + markov_bias, where the
markov path is w1[prev] @ w2[cand] (verified vs ds4.c:36545/36866, night
run #2 review). We cannot run the 3-stage draft transformer offline, but the
CAPTURED hidden states (h[pos-1] = 3 taps x 4096, fp8) are exactly the
information the transformer consumes. v1 surrogate:

    z[cand] = w1[seed] @ w2[cand]  +  (x @ A) @ w2[cand]
              ^^^^^^^^^^^^^^^^^^^     ^^^^^^^^^^^^^^^^^^
              official markov path    learned low-rank context from h
              (fine-tuned)            (A init = 0 => starts at v0 FT)

Objectives (per the plan, adapted to what's observable offline):
  - stage-CE proxy: cross_entropy(z, labels[1])   (next committed token)
  - confidence:   two auxiliary logits from the same x@A trunk ->
                  BCE P(streak>=1), P(streak>=3)   (joint, weighted)

Eval on frozen day-3 holdout: top1/top3 vs v0 markov FT (.179/.280),
boundary-stratified (Wilson CIs), confidence AUCs vs probe floor (.740).
If h adds CE lift, the taps carry draftable signal and full-mirror training
is justified; the delta is a LOWER bound (no attention/moe in the loop).

RAM design: one f32 feature slab (~10 GB at current corpus), fp8->f32
decoded once from blade mmaps; batches index the slab; nothing duplicated.

Usage: uv run python -m dspark.train_v1 [--epochs N] [--rank R] [--lr X]
                                        [--conf-w W]
Writes trainer/TRAIN_V1.json + trainer/v1_head.pt
"""
from __future__ import annotations

import glob
import json
import os
import struct
import sys
import time

import numpy as np
import torch
from torch import nn

from .data_contract import RECORD_BYTES, HEADER_BYTES, STREAK_CLAMP, VOCAB
from .data_contract import decode_e4m3

CORPUS = "/Volumes/FireCuda520/ds4-train"
TENSORS = "/Volumes/FireCuda520/ds4-train/tensors-20261101"
TRAIN_DAYS = ["20261029-day1", "20261029-day2", "20261101-day4"]
HOLD_DAYS = ["20261029-day3"]
HDR = struct.Struct("<IiBBBB5i")
CTL = {128806, 128807, 128808, 128809, 128810, 128811, 128812, 128813,
       128814, 128825, 128829}


def load_day(day, quarantine=None, codes8=False):
    """Return (X fp8-decoded f32 [N,12288], seed, y, ctl, streak) for the
    day's usable records (nfeat=3, n_label>=2, streak<clamp, pos-known).
    quarantine: loaded dspark.loops json -> excluded records are skipped.
    codes8 (S4 night patch): keep X as raw e4m3 uint8 codes [N,12288]
    (4x smaller slab; decode_e4m3 applied per batch - pure function of
    the bytes, so batches are BIT-IDENTICAL to load-time decoding)."""
    from .loops import file_exclusions
    xs, seeds, ys, ctls, sts = [], [], [], [], []
    for rp in sorted(glob.glob(os.path.join(CORPUS, day, "train_pid*.recs"))):
        excl = file_exclusions(rp, quarantine) if quarantine else set()
        size = os.path.getsize(rp)
        n = size // RECORD_BYTES
        with open(rp, "rb") as fd:
            mm = memoryview(fd.read())
        for i in range(n):
            if i in excl:
                continue
            r = HDR.unpack(mm[i * RECORD_BYTES:i * RECORD_BYTES + 32])
            pos, seed, streak, nlab, nfeat = r[:5]
            if nlab < 2 or streak >= STREAK_CLAMP or nfeat != 3:
                continue
            if not (0 <= seed < VOCAB and 0 <= r[7] < VOCAB):
                continue
            feats = bytes(mm[i * RECORD_BYTES + HEADER_BYTES:
                             (i + 1) * RECORD_BYTES])
            if codes8:
                xs.append(np.frombuffer(feats, dtype=np.uint8))
            else:
                xs.append(np.asarray(decode_e4m3(feats), dtype=np.float32))
            seeds.append(seed)
            ys.append(r[7])                       # labels[1] = next token
            ctls.append(any(t in CTL for t in r[6:11]) or seed in CTL)
            sts.append(streak)
            del feats
    return (np.stack(xs), np.asarray(seeds), np.asarray(ys),
            np.asarray(ctls, dtype=bool), np.asarray(sts, dtype=np.int64))


def main():
    torch.manual_seed(0)
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    # two-phase mode: first --phase1 E epochs train ONLY the h-pathway
    # (proj+conf) with w1/w2 frozen at OFFICIAL -> clean attribution of
    # delta_top1_from_h (joint co-adaptation muddied it in run #1)
    phase1 = int(sys.argv[sys.argv.index("--phase1") + 1]) \
        if "--phase1" in sys.argv else 0
    rank = int(sys.argv[sys.argv.index("--rank") + 1]) \
        if "--rank" in sys.argv else 256
    epochs = int(sys.argv[sys.argv.index("--epochs") + 1]) \
        if "--epochs" in sys.argv else 6
    lr = float(sys.argv[sys.argv.index("--lr") + 1]) \
        if "--lr" in sys.argv else 3e-4
    conf_w = float(sys.argv[sys.argv.index("--conf-w") + 1]) \
        if "--conf-w" in sys.argv else 0.2
    wd = float(sys.argv[sys.argv.index("--wd") + 1]) \
        if "--wd" in sys.argv else 0.01
    tag = sys.argv[sys.argv.index("--out-tag") + 1] \
        if "--out-tag" in sys.argv else ""
    qpath = sys.argv[sys.argv.index("--quarantine") + 1] \
        if "--quarantine" in sys.argv else ""
    from .loops import load_quarantine
    quarantine = load_quarantine(qpath or None)
    suffix = f"_{tag}" if tag else ""

    smoke = "--smoke" in sys.argv
    if smoke:
        suffix = "_smoke"
    train_days = sys.argv[sys.argv.index("--train-days") + 1].split(",") \
        if "--train-days" in sys.argv else TRAIN_DAYS
    hold_days = sys.argv[sys.argv.index("--hold-days") + 1].split(",") \
        if "--hold-days" in sys.argv else HOLD_DAYS
    trunk = sys.argv[sys.argv.index("--trunk") + 1] \
        if "--trunk" in sys.argv else "lin"
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) \
        if "--seed" in sys.argv else 0     # 0 = anchor-identical trajectory
    codes8 = "--codes8" in sys.argv     # S4 night: uint8 e4m3 slab (4x RAM)
    ckpt = "--ckpt" in sys.argv         # S4 night: per-epoch checkpoint
    src = np.load(os.path.join(TENSORS, "mtp_nonexpert_f32.npz"))
    print(f"loading corpora (peak ~2x slab)... dev={dev} smoke={smoke} "
          f"trunk={trunk} train={train_days} hold={hold_days}")
    Xtr, Str, Ytr, Ctr, Ltr = load_day_days(train_days, cap=3000 if smoke else 0,
                                            quarantine=quarantine,
                                            codes8=codes8)
    Xho, Sho, Yho, Cho, Lho = load_day_days(hold_days, cap=1500 if smoke else 0,
                                            quarantine=quarantine)
    if smoke:
        epochs = 1
    print(f"train N={len(Xtr)} holdout N={len(Xho)}")

    xtr = torch.from_numpy(Xtr)              # CPU slab; batches move to dev
    slabs_ho = torch.from_numpy(Xho)
    del Xtr, Xho

    def to_f32(xb):
        """Per-batch e4m3 decode when the slab stores uint8 codes; no-op
        otherwise. decode_e4m3 is a pure function of the bytes, so decoded
        batches are BIT-IDENTICAL to load-time decoding (anchor path)."""
        if xb.dtype != torch.uint8:
            return xb
        flat = decode_e4m3(memoryview(xb.numpy().reshape(-1)))
        return torch.from_numpy(flat.reshape(xb.shape))
    W1 = torch.from_numpy(
        src["mtp.2.markov_head.markov_w1.weight"])          # [V,256]
    W2 = torch.from_numpy(
        src["mtp.2.markov_head.markov_w2.weight"])
    V, R0 = W1.shape

    model = V1Head(W1, W2, rank, conf_w, trunk).to(dev)
    g_w = {"params": [model.w1, model.w2], "lr": lr}
    g_p = {"params": list(model.proj.parameters()) + [model.log_s], "lr": lr}
    g_c = {"params": model.conf.parameters(), "lr": lr}
    g_cp = {"params": [model.confproj], "lr": lr}
    opt = torch.optim.AdamW([g_w, g_p, g_c, g_cp], weight_decay=wd)
    if phase1:
        g_w["lr"] = 0.0
    bs = 1024
    N = len(xtr)
    Str_ = torch.from_numpy(Str)
    Ytr_ = torch.from_numpy(Ytr)
    T1_ = torch.from_numpy((Ltr >= 1).astype(np.float32))
    T3_ = torch.from_numpy((Ltr >= 3).astype(np.float32))

    def h_eval(want_scores=False):
        """Holdout pass: top1/top3/boundary hits (+ conf scores)."""
        hits1 = hits3 = n = bh = bn = 0
        c_scores = []
        with torch.no_grad():
            for i in range(0, len(slabs_ho), bs):
                xb = to_f32(slabs_ho[i:i + bs]).to(dev)
                z, c1, c3 = model(xb, torch.from_numpy(Sho[i:i + bs]).to(dev))
                yb = torch.from_numpy(Yho[i:i + bs]).to(dev)
                m3 = z.topk(3, dim=-1).indices
                hits1 += int((m3[:, 0] == yb).sum())
                hits3 += int((m3 == yb[:, None]).any(-1).sum())
                sel = Cho[i:i + bs]
                if sel.any():
                    tgt = torch.from_numpy(Yho[i:i + bs][sel]).to(dev)
                    bh += int((z[torch.from_numpy(sel).to(dev)].argmax(-1)
                               == tgt).sum())
                    bn += int(sel.sum())
                if want_scores:
                    c_scores.append(torch.stack([c1, c3], 1).cpu())
                n += len(yb)
        d = {"top1": hits1 / n, "top3": hits3 / n, "bh": bh, "bn": bn}
        if want_scores:
            d["scores"] = torch.cat(c_scores).numpy()
        return d

    torch.manual_seed(seed)                # after parse (0 = anchor path)
    cosine = "--cosine" in sys.argv
    curve = []
    ckpt_dir = os.path.dirname(os.path.dirname(__file__))
    ckpt_json = os.path.join(ckpt_dir, f"TRAIN_V1{suffix}_PARTIAL.json")
    ckpt_pt = os.path.join(ckpt_dir, f"v1_head{suffix}_PARTIAL.pt")
    t_ep = time.time()
    for ep in range(epochs):
        if ep == phase1:                     # phase 2: unfreeze markov
            g_w["lr"] = lr
        if cosine:                           # manual cosine (phase1 freeze
            f = 0.5 * (1 + np.cos(np.pi * ep / epochs))   # aware: g_w base
            g_p["lr"] = lr * f
            g_c["lr"] = lr * f
            g_cp["lr"] = lr * f
            if ep >= phase1:
                g_w["lr"] = lr * f
            elif phase1:
                g_w["lr"] = 0.0
        perm = torch.randperm(N, generator=torch.Generator().manual_seed(
            seed * 100000 + ep))           # seed=0 => anchor's manual_seed(ep)
        tot = ce_tot = cf_tot = 0.0
        for i in range(0, N, bs):
            idx = perm[i:i + bs]
            xb = to_f32(xtr[idx]).to(dev)
            sb = Str_[idx].to(dev)
            yb = Ytr_[idx].to(dev)
            t1b, t3b = T1_[idx].to(dev), T3_[idx].to(dev)
            opt.zero_grad(set_to_none=True)
            z, c1, c3 = model(xb, sb)
            ce = nn.functional.cross_entropy(z, yb)
            bce = (nn.functional.binary_cross_entropy_with_logits(c1, t1b)
                   + nn.functional.binary_cross_entropy_with_logits(c3, t3b))
            loss = ce + conf_w * bce
            loss.backward()
            opt.step()
            m = len(idx)
            tot += float(loss.detach()) * m
            ce_tot += float(ce.detach()) * m
            cf_tot += float(bce.detach()) * m
        he = h_eval()
        curve.append({"epoch": ep, "holdout_top1": round(he["top1"], 4),
                      "holdout_top3": round(he["top3"], 4),
                      "epoch_s": round(time.time() - t_ep, 1)})
        print(f"  epoch {ep}: loss {tot/N:.4f} (ce {ce_tot/N:.4f}, "
              f"bce {cf_tot/N:.4f}) | holdout top1 {he['top1']:.4f} "
              f"top3 {he['top3']:.4f} ({time.time() - t_ep:.0f}s)",
              flush=True)
        t_ep = time.time()
        if ckpt:
            # atomic per-epoch checkpoint (hard-stop safety; the FINAL
            # TRAIN_V1{suffix}.json below is still written only on
            # completion, so partial curves are clearly labelled)
            part = {"incomplete": True, "last_epoch": ep,
                    "pool": int(N), "holdout_curve": curve,
                    "note": "live checkpoint; see v1_head*_PARTIAL.pt"}
            tmp = ckpt_json + ".tmp"
            with open(tmp, "w") as fd:
                json.dump(part, fd, indent=1)
            os.replace(tmp, ckpt_json)
            tmp = ckpt_pt + ".tmp"
            torch.save({k: v.detach().cpu()
                        for k, v in model.state_dict().items()}, tmp)
            os.replace(tmp, ckpt_pt)

    # FINAL: ablation with the h-context (full model) and without (proj
    # zeroed post-training) — the DELTA is the tap-information content.
    from .markov_ft import wilson   # reuse verified helper
    from .confidence_probe import auc
    evals = {}
    proj_params = list(model.proj.parameters())
    proj_snapshot = [p.detach().clone() for p in proj_params]
    for etag in ("with_h", "no_h"):
        if etag == "no_h":
            with torch.no_grad():
                for p in proj_params:
                    p.zero_()
        evals[etag] = h_eval(want_scores=True)
    with torch.no_grad():
        for p, s in zip(proj_params, proj_snapshot):
            p.copy_(s)
    cy = np.stack([(Lho >= 1).astype(float), (Lho >= 3).astype(float)], 1)
    r = {"protocol": dict(rank=rank, epochs=epochs, lr=lr, conf_w=conf_w,
                          phase1_frozen=phase1, wd=wd, cosine=cosine,
                          seed=seed,
                          trunk=trunk,
                          cli=list(sys.argv[1:]),
                          logit_scale_init=0.2,
                          train_days=list(train_days), holdout=list(hold_days),
                          note="z = (w1[seed] + x@A) @ w2.T * exp(log_s); "
                               "A init 0; no_h eval zeros A post-training"),
         "v0_reference_top1": 0.179, "v0_reference_top3": 0.280,
         "holdout_curve": curve}
    for etag, e in evals.items():
        r[f"{etag}_top1"] = round(e["top1"], 4)
        r[f"{etag}_top3"] = round(e["top3"], 4)
        for nm, col in (("auc_ge1", 0), ("auc_ge3", 1)):
            v = auc(e["scores"][:, col], cy[:, col])
            if v is not None:
                r[f"{etag}_{nm}"] = round(v, 4)
    r["delta_top1_from_h"] = round(evals["with_h"]["top1"]
                                   - evals["no_h"]["top1"], 4)
    e = evals["with_h"]
    if e["bn"]:
        r["holdout_boundary_top1"] = round(e["bh"] / e["bn"], 4)
        r["holdout_boundary_n"] = e["bn"]
        r["holdout_boundary_ci95"] = wilson(e["bh"], e["bn"])
    print(json.dumps(r, indent=1))
    out = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       f"TRAIN_V1{suffix}.json")
    with open(out, "w") as fd:
        json.dump(r, fd, indent=1, allow_nan=False)
    torch.save({k: v.detach().cpu() for k, v in
                model.state_dict().items()},
               os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            f"v1_head{suffix}.pt"))
    print(f"wrote {out}")


class V1Head(nn.Module):
    def __init__(self, w1, w2, rank, conf_w, trunk="lin"):
        super().__init__()
        self.w1 = nn.Parameter(w1.clone())            # [V, 256] trainable
        self.w2 = nn.Parameter(w2.clone())
        self.ctx_dim = w1.shape[1]                    # fixed: markov width
        self.rank = rank
        # h-pathway trunk -> ctx_dim context. Zero-output init means the
        # model starts EXACTLY at the v0 markov path (clean attribution).
        if trunk == "mlp":
            l1 = nn.Linear(12288, 512)
            l2 = nn.Linear(512, self.ctx_dim, bias=False)
            with torch.no_grad():
                l2.weight.zero_()                     # zero-output property
            self.proj = nn.Sequential(l1, nn.ReLU(), l2)
        else:
            self.proj = nn.Linear(12288, self.ctx_dim, bias=False)
            with torch.no_grad():
                self.proj.weight.zero_()
        # official markov logits arrive unscaled (CE init ~23); learnable
        # temperature fixes calibration without hand-tuning a constant
        self.log_s = nn.Parameter(torch.tensor(float(np.log(0.2))))
        # confidence runs off its own h-embedding: no gradient interference
        # with the CE-trained proj (separate trunks, joint optimization)
        self.confproj = nn.Parameter(torch.zeros(12288, rank))
        self.conf = nn.Sequential(nn.Linear(rank, 128), nn.ReLU(),
                                  nn.Linear(128, 2))

    def forward(self, x, seed):
        ctx = self.w1[seed] + self.proj(x)             # [B, ctx_dim]
        z = (ctx @ self.w2.T) * torch.exp(self.log_s)   # [B, V], scaled
        c = self.conf(x @ self.confproj)
        return z, c[:, 0], c[:, 1]


def load_day_days(days, cap=0, quarantine=None, codes8=False):
    acc = [[], [], [], [], []]
    for d in days:
        parts = load_day(d, quarantine=quarantine, codes8=codes8)
        for a, p in zip(acc, parts):
            a.append(p[:cap] if cap else p)
        if cap and all(len(x) >= cap for x in acc):
            break
    return tuple(np.concatenate(a)[:cap] if cap else np.concatenate(a)
                 for a in acc)


if __name__ == "__main__":
    main()
