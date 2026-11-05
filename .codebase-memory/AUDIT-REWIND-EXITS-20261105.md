# AUDIT — "armed and truthful, or deliberately cleared": every rewind-window exit

Read-only pass (08:20-08:30, engine untouched) over the invariant that three separate
incidents were projections of. Findings only; no engine change made here.

## The invariant
> At every return that leaves `checkpoint_valid == true`, either the verifier window is
> armed **and truthful for every position the gate will later admit**, or it was cleared
> deliberately.

Three routes found tonight were all violations of it, each discovered by a label rather
than by reasoning: eval-site clearing (`265679b` + tolerance), the stochastic
direct-prefix commit that never armed (`18fa4f8`), and an armed window whose edge
position the gate refused (`ecef491`).

## The seven writers of `dspark_rewind_end`
| line | writer | meaning |
|---|---|---|
| 62960, 64069 | clear to 0 | `ds4_session_dspark_capture_invalidate` sites (the `__func__` label is stamped here) |
| 64198 | `= 0` in `dspark_rewind_begin` | **a block in flight owns no window until it arms one** — this is why every early return after `begin` strands |
| 64212 | `= checkpoint.len` in `dspark_rewind_keep` | the arming edge |
| 78397 | `= 0`, cause `"sync"` | generic sync |
| 80803 | `= 0` | session invalidate/adoption path |
| 89285 | `= 0`, cause `"rewind-fail"` | `dspark_rewind_recent` failed after the restore was refused |

## Prime suspect for a fourth route (found by reading, NOT yet by any incident)
`ds4_session_eval_dspark_speculative_argmax`, the branch entered at 84228 and returning
at 84279 (comment at 84221: *"Larger support blocks can outgrow the retained
prefixes…"*):

```
84228  s->checkpoint.len = start;
84229  ds4_session_dspark_capture_invalidate(s);      <- end = 0, label = this function
84230  prefix_ok = spec_frontier_commit_prefix(...)
84246  token_vec_push(&s->checkpoint, drafts[i]);     <- tokens committed
84253  s->checkpoint_valid = true;
84254  ds4_session_dspark_capture_note_checkpoint(s);
84279  return n_accept;                               <- NO dspark_rewind_keep()
```

That is the same shape as `18fa4f8` in the neighbouring stochastic driver - which is
where the confidence comes from, and also the reason for caution: the argmax driver
**does** arm in its sibling branch (84136 `dspark_rewind_keep(s)`), so this is an
inconsistency between two near-identical paths, not a proven live defect. The stochastic
tail (84778 push → 84792 valid → 84796 `ds4_session_eval_probe_tp` → 84822
`return n_accept`) is the second candidate; there an eval does run, so it is genuinely
ambiguous rather than obviously missing an arm.

**Why it has not been seen:** a strand only costs anything when a rewind is *requested*
afterwards, and `resample=1` requests have occurred 4 times in the whole log set
(~1 per 40 min of deep DSML traffic). Absence of evidence, not evidence of absence.

**The sensor already exists and needs no change.** A rebuild caused by this path would
print `end_state=ds4_session_eval_dspark_speculative_argmax`, because the label is
stamped by whichever `capture_invalidate` last ran. If that string ever appears in
`log/ds4.log`, the fix is the same one line, gated on `dspark_rewind_window_tolerance()`,
as `18fa4f8` - and the model-free gate test in `tests/test_session_state.c` already pins
the geometry the arm must produce.

## Not violations (checked, so nobody re-derives them)
- The six `return n_accept` / `return -1` exits **before** `dspark_rewind_begin`
  (stochastic 84495-84569, argmax 83903-84000): they never touch the window, so the
  previous block's armed window survives, which is the intended behaviour.
- Every `return -1` after a failed verifier rollback sets `checkpoint_valid = false`
  (84753, 84773, 84786, 84800 / argmax 84291, 84360, 84384) - the window is irrelevant
  because the checkpoint is gone, and the sync path rebuilds.
- 84325 / 84423 / 84822 return after replay-based commits where `ds4_session_eval_probe_tp`
  ran: under `DS4_REWIND_RESUME` the tolerance is what keeps those windows alive; the
  argmax 84279 case above is different because **no** eval intervenes.

## Open items this audit leaves
1. If `end_state=ds4_session_eval_dspark_speculative_argmax` appears: apply the one-line
   arm, gated on the tolerance, and re-run `make test-session-state-gpu`.
2. The DS4.1 twin window (89167) rejects `pos >= d->rewind_end` unconditionally with no
   shared `covers()` predicate - qwen/ds41 semantics remain **deliberately** untouched.
   Record that as an ADR decision rather than an accident of line placement.
3. Consider a cheap "committed tokens while the window is dead" counter in both drivers.
   It would surface a route-4 the moment it happens instead of waiting for a ~700 s
   stall to notice - the one improvement the label system still lacks, since labels only
   speak when a rewind is requested.
4. `resample=1` remains unobserved post-fix (0 events across six boots); the field proof
   for `18fa4f8` still rests on the counters (`sample_partial_accepts=228` in a 2-minute
   sampled run) plus the model-free geometry test.
