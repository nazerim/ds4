# Bench CSVs behind upstream PR antirez/ds4#1358 (the speed claim)

Posted evidence has to stay re-derivable. These four CSVs are the raw sweeps behind
the parity table in the PR body, copied out of the session temp dir on 2026-11-18 --
the same temp dir that has already destroyed evidence twice this week
(`ds4-live-env.snapshot.txt`, `ds4-live-argv.txt`).

Identical command for every arm; only the binary differed:

    ./ds4-bench -m ds4flash.gguf \
      --prompt-file speed-bench/promessi_sposi.txt \
      --ctx-start 2048 --ctx-max 65536 --step-incr 2048 --gen-tokens 128 \
      --ssd-streaming --csv <arm>.csv

`--ssd-streaming` was required: the V4.1 test model needs ~156 GiB against a ~112 GiB
safe budget on this 128 GiB host. Machine Apple M5 Max, macOS, Metal; model
`gguf/DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix.gguf`. Arms were run alternating
A/B/B/A because `docs/PERFORMANCE.md` says one favorable run is not a speed result.

| file | arm | ctx points | mean prefill_tps | mean gen_tps | sha256[:12] |
|---|---|---|---|---|---|
| `barrier-before.csv` | baseline `b3f5018`, rep 1 (21:21-21:34) | 32 | 116.34 | 23.09 | `51529c72a7c6` |
| `barrier-after.csv` | patched (`a5e9e77` lineage), rep 1 (21:36-21:48) | 32 | 116.71 | 22.85 | `45d4815a2b30` |
| `barrier-after2.csv` | patched `f7db783`, rep 2 (21:57-22:10) | 32 | 116.12 | 23.18 | `84ebdcb605a4` |
| `barrier-before2.csv` | baseline `b3f5018`, rep 2 (22:29-22:41) | 32 | 115.28 | 23.06 | `e062444dafea` |

Recomputed from the archived copies: prefill 115.81 -> 116.41 (+0.52 %), gen 23.07 -> 23.02 (-0.26 %). Both below the largest same-arm spread, which is
the claim made in the PR.

The patched arms came from successive revisions (`a5e9e77`, `f7db783`); the final
`dcf77c4` differs only by comment text and the tolerated-`errno` branch, neither
reachable from `ds4-bench` -- and `ds4_bench.c` contains zero references to
`ds4_kvstore`, so the benchmark cannot exercise this patch on any revision. The PR
says so rather than implying the sweep measured the change.
