# Execution proof: the barrier runs on the upstream tree

Captured 2026-11-17 22:52-22:57. Built from `origin/main` `b3f5018` + the barrier
patch, with the throwaway instrumentation in `barrier_probe.py` (same directory,
reads `git show HEAD:` so it survives its own edits). Reverted before the pushed
commit: `grep -c BARRIER-PROBE ds4_kvstore.c` was 0 at push, and the tree was
rebuilt clean (0 warnings) with `_fsync` and `_fcntl` both still referenced.

Why this was needed: the fork build proved execution through `kv_debug_enabled()`,
which does not exist in origin/main (0 occurrences). The ported barrier had never
visibly run anywhere, and `nm` showing the symbols only proves the call compiled in.

## Conditions

- Upstream `ds4-server`, warning-free build, base `b3f5018`.
- Model `gguf/Qwen3.8-Flash-Next-Q4.gguf` (177 GB) with `--ssd-streaming -c 32768 --port 8002`.
- KV dir: throwaway `opencode/kvproof` on the internal SSD. The production cache at
  `/Volumes/FireCuda520/ds4-kv-qwen` was never opened by this binary.
- Every DeepSeek V4 / V4.1 / Vision-Exp GGUF on this host fails to load in upstream's
  build: `required metadata key is missing: deepseek4.block_count`. Only Qwen3.8
  loads. That, not the RAM budget alone, is why model-backed `make test` cases cannot
  run on the upstream tree here.

## Run 1 -- single barrier (8 executions over 8 stores)

```
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=35.07
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=47.62
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=44.34
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=33.78
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=38.06
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=42.65
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=51.64
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=46.88

```

## Store side of the same run

```
    1117 22:54:14 ds4-server: kv cache stored tokens=1805 trimmed=0 reason=cold key=token-text size=167.76 MiB save=72.5 ms
    1117 22:55:01 ds4-server: kv cache stored tokens=1813 trimmed=0 reason=evict key=token-text size=168.00 MiB save=82.8 ms
    1117 22:55:03 ds4-server: kv cache stored tokens=1795 trimmed=0 reason=cold key=token-text size=167.46 MiB save=82.7 ms
    1117 22:55:06 ds4-server: kv cache stored tokens=1799 trimmed=0 reason=evict key=token-text size=167.58 MiB save=72.7 ms
    1117 22:55:08 ds4-server: kv cache stored tokens=2048 trimmed=643 reason=cold key=token-text size=175.06 MiB save=83.0 ms
    1117 22:55:11 ds4-server: kv cache stored tokens=2695 trimmed=0 reason=evict key=token-text size=194.50 MiB save=89.3 ms
    1117 22:55:13 ds4-server: kv cache stored tokens=2048 trimmed=1439 reason=cold key=token-text size=175.06 MiB save=95.9 ms
    1117 22:55:59 ds4-server: kv cache stored tokens=3491 trimmed=0 reason=shutdown key=token-text size=218.43 MiB save=89.2 ms

```

## Run 2 -- a second barrier on the same, already-synced file

Question: is 17-48 ms the price of the calls, or of writeback the kernel had not yet
done? Re-barreiring the same file immediately answers it.

```
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=47.63
    BARRIER-PROBE2 second_fsync=0/0 second_fullsync=0/0 ms=4.00 (cum=51.64)
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=16.67
    BARRIER-PROBE2 second_fsync=0/0 second_fullsync=0/0 ms=3.79 (cum=20.48)
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=39.77
    BARRIER-PROBE2 second_fsync=0/0 second_fullsync=0/0 ms=3.66 (cum=43.47)
    BARRIER-PROBE fsync=0/0 fullsync=0/0 ms=34.66
    BARRIER-PROBE2 second_fsync=0/0 second_fullsync=0/0 ms=4.07 (cum=38.75)

```

## Reading

- `fsync=0/0 fullsync=0/0` on all 12 barrier executions: both calls return 0 with errno 0, so
  `F_FULLFSYNC` is honoured, not silently ignored, on this host from the upstream
  build. `INDEX.md` listed exactly this as unresolved for the Thunderbolt bridge; note
  this run wrote to the **internal** volume, so the bridge question stays open.
- Barrier 16.7-51.6 ms while pages were dirty; 3.7-4.1 ms when nothing was pending. The
  fixed cost is ~4 ms and the rest is the physical write.
- Fork production (external volume, ~647 MiB stores, unbarriered `save=` ~166 ms)
  measured 4.05 ms mean over 6 stores. Different device and duty cycle, which is why
  both figures are reported and neither is presented as *the* cost. The earlier
  "~2.4 % of store time" framing in the commit notes was the favourable case only and
  has been replaced.

> Count note (corrected 2026-11-18 after review): this file earlier said "seven
> stores" and "all ten stores". Actual: 12 barrier executions across two runs
> (8 in run 1 over 8 stores, 4 in run 2 over 4 stores). The run-1 log grew after
> the first capture because a second batch of requests reused the same server.
