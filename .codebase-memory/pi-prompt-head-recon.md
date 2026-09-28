# Prompt-head stability recon — tool-manifest (read-only)

VERDICT: the drafted patch is WRONG. It does not touch the mechanism behind the measured 378k re-prefill, and it adds two new correctness bugs.

Drift: `/Users/naz/Projects/PiScratch/pi-extensions/tool-manifest.ts` and `/Users/naz/.pi/agent/extensions/tool-manifest.ts` are byte-identical (md5 7470b0f1881390050152b43c31c00df5). The `.bak-*`/`.retired-*` siblings are not loaded (`loader.js:575` requires a `.ts`/`.js` suffix), so exactly one handler exists. pi root: `/Users/naz/.npm-global/lib/node_modules/@earendil-works/pi-coding-agent`.

## A. Where and when the block is rendered

- `renderToolManifest` — tool-manifest.ts:55–92. Null at **line 58**: `if (active.length === 0 && guidelines.length === 0) return null;`, where `active` = `options.selectedTools` ∩ `BUILT_INS` (line 56).
- Handler `toolManifestExtension` — tool-manifest.ts:94–107. Drops: **line 97** (`!options`), **line 99** (`!manifest`). Guards: 103 (empty base), 104 (MARKER). **Line 105** returns `{ systemPrompt: current + "\n\n" + manifest }` — a FULL replacement. That is the defect.
- `runner.js:1042–1044` turns it into `currentOptions.forceSystemPrompt`; `agent-session.js:1319` stores it as `_runSystemPromptOptions`; `_installAgentForcedPromptProjection` (agent-session.js:1044–1058) collapses all system messages into one head of that text — and returns the context UNCHANGED when `_runSystemPromptOptions?.forceSystemPrompt` is undefined.
- `emitBeforeAgentStart` has ONE caller: `agent-session.js:1283`, inside `prompt()`. **agent-session.js:1100** clears `_runSystemPromptOptions = undefined` in every run's `finally`. **agent-session.js:1504/:1507** call `_runAgentPrompt(appMessage)` from `sendCustomMessage({triggerTurn:true})` WITHOUT emitting `before_agent_start`. ⇒ Any turn triggered by a custom message (subagent completion, supervisor reply, watchdog reminder — exactly an agent→subagent→agent round trip) renders the head from transcript sections only: NO manifest. This reproduces all three observed variants (manifest / advertised-subagents-only / neither). `renderToolManifest` returning null is a red herring: `systemPromptOptions` is always normalized (`types.d.ts:564`, `runner.js:1017`) and `selectedTools` = `defaultTools` = 6 tools (`~/.pi/agent/settings.json`).
- Load order (`loader.js:610–620`): cwd/.pi/extensions → ~/.pi/agent/extensions (tool-manifest) → packages (pi-subagents). tool-manifest runs FIRST and forces the prompt before `pi-subagents/src/extension/index.js:778–788` sets `sections.advertised_subagents`; once forced, `buildSystemPromptState` yields content with NO sections, so the catalog never reaches the head — documented at `pi-subagents/docs/agents.md:274`.
- A memo could hang off `ctx.sessionManager` (`types.d.ts:220`; `ReadonlySessionManager` exposes `getSessionId()`), reachable because handlers are `(event, ctx)` (`types.d.ts:974`).

## B. Assessment of the drafted patch

Memoizes on the base prompt: yes. Reuses instead of dropping: yes, nominally. Adds a warning: yes, but mis-keyed. Gaps:
1. Does not fix the observed failure — on the `sendCustomMessage` path the handler never runs, so no memo can inject anything. Dead code for the measured incident.
2. Cross-lane contamination: `[...manifestByBase.values()].pop()` returns the newest manifest from ANY session/lane in the process. `subagent/config.json` sets `globalConcurrencyLimit: 3`, so the parent can be handed a lane's 7-tool manifest — manufacturing the divergence it targets.
3. `memoKey` is broken: `s.length` is the SLICE length, so every base prompt >4096 chars (all of them; observed ~161 KB) keys as `4096:<hash>`. The hash covers only the first/last 2048 chars; the middle — containing divergence byte 159188 — is not in the key. Distinct conversations collide.
4. The key omits the tool set. With `customPrompt` set, `buildSystemPromptSections` (system-prompt.js:76–78) never renders `selectedTools`, so the base prompt is invariant under a `defaultTools` change ⇒ 6→7 (add `ls`) hits the same key and serves the stale 6-tool manifest forever. That is precisely the "stale memo vs live registry" risk.
5. No invalidation, no bound; a fallback value stored under a key is pinned permanently. Process-local, so it cannot survive a pi restart — which is when a resume happens — making the patch's central claim false across processes.
6. Change 3 warns on every legitimate base-prompt change (including other sessions in-process) ⇒ noise; never fires for the real failure; false negatives per #3. It is also unwired (Change 2 never calls it) and `lastHead` is module-global, not per session.
7. Change 2 drops the `!options` guard and routes undefined options into the foreign fallback (#2).
8. Leaves `forceSystemPrompt` in place, so `advertised_subagents` stays suppressed and every section change still folds into a full head rewrite.

## C. Minimal correct change set

All edits in `/Users/naz/Projects/PiScratch/pi-extensions/tool-manifest.ts`. Principle: stop replacing the prompt; inject a structured SECTION that pi persists in the transcript and replays — stability comes from the transcript, not a cache.
1. After line 33 (`GUIDELINE_HEADING`) add `const SECTION = "tool_manifest";` (must match `/^[a-z][a-z0-9_-]*$/`, system-prompt.js:7; not `preamble`).
2. In `toolManifestExtension` (94–107) replace the line-105 return with `event.systemPromptOptions.sections[SECTION] = manifest;` and return undefined. The runner passes the shared mutable normalized object (runner.js:1035), so later handlers and `_preparePromptAndToolLoadout` see it. Sections render right after `cwd` (system-prompt.js:105–109) — same position as today — wrapped `<tool_manifest>\n…\n</tool_manifest>` (line 112).
3. Delete the MARKER guard (line 104); assignment is idempotent. Keep line 99's `if (!manifest) return;` as a NO-OP that leaves an existing section untouched (never `delete`), so an empty-registry pass cannot withdraw the block. Line 103's empty-base guard is no longer load-bearing (nothing is replaced).
4. Leave `renderToolManifest` (55–92) unchanged; it is already pure in `options`. Add NO memo.
5. Optional diagnostic: a separate `before_provider_request` handler hashing the leading system message per `ctx.sessionManager.getSessionId()`, keeping the previous sha in a `Map<string,string>`, warning on change. That path sees the real wire head including the `sendCustomMessage` path. Verify ordering vs `transformContext` first (F.3).
6. Legitimate tool-set change at a session boundary needs no code: `_rebuildSystemPrompt` (agent-session.js:991; callers 925/1073/2330) refreshes `_baseSystemPromptOptions`, `_restoreToolsFromTranscript` (1062–1073) restores `toolsAdded` on resume, the next `before_agent_start` recomputes the section, and `diffSystemPromptSections` (system-prompt.js:135) emits a small patch rather than a head rewrite.
7. Update the header comment (1–29), especially "Cache-safety", to say the block is a structured section and record the pi-subagents interaction. Deploy `./deploy.sh --install` at a CONVERSATION BOUNDARY, then restart pi.

## D. Test to write first

File: `/Users/naz/Projects/PiScratch/pi-extensions/tests/tool-manifest.test.ts` (append after the handler-wiring block, ~line 108). Command:

    cd /Users/naz/Projects/PiScratch/pi-extensions && node --experimental-strip-types --no-warnings tests/tool-manifest.test.ts

Full suite is `bash tests/run-all.sh` (also runs `tests/deploy.sh` and `./deploy.sh --check`; not run here). With `event = { systemPrompt: base, systemPromptOptions: { selectedTools: active, toolSnippets, sections: {} } }`:
1. `handler(event) === undefined` — must NOT return a replacement prompt.
2. `sections.tool_manifest` contains MARKER and every name in `active`.
3. Resume/replay: a second pass whose `sections` is pre-populated only from the first pass's stored value, with `selectedTools: []` and `promptGuidelines: []`, leaves `sections.tool_manifest` byte-identical (not deleted, not replaced).
4. `Object.keys(sections)` is `["tool_manifest","advertised_subagents"]` when both are set, pinning the load-order dependency (E.3).
All four FAIL today (the handler returns `{ systemPrompt }` and never writes `sections`). Existing assertions at ~line 118 (`startsWith(base + "\n\n")`) and ~line 124 (`r2 === undefined` for MARKER) must be rewritten — idempotency moves from early-return to key assignment.

## E. Risks

1. One-time head change for every live session at deploy (untagged text → `<tool_manifest>` tag), so existing ds4 ladders re-root once; bytes differ from today even though position does not. Deploy at a conversation boundary.
2. Section ordering depends on load order (loader.js:610–620). Moving pi-subagents into a project-local `.pi/extensions/` flips it and changes the head. Pinned by test D.4.
3. Newly-live `advertised_subagents`: dropping the forced prompt re-enables the catalog section (docs/agents.md:274). It recomputes every prompt and changes as lanes start/finish. It should append as a small system message rather than rewrite the head, but it is a NEW head-variation source previously suppressed. Verify on a real lane round trip before trusting it.
4. Staleness window: a `sendCustomMessage` turn replays the stored section, reflecting the tool set at the last real prompt. If `setActiveTools()` ran in between (agent-session.js:2330) the section lags one turn. Strictly better than today (the whole block vanished) and identical to how pi's own `tools` section behaves. Do not memo-patch it.
5. Lane divergence persists by design: `pi-subagents/src/extension/herdr-pi-bridge.js:152` still returns a replacement `systemPrompt` inside lanes. A lane is a separate conversation with its own ladder and must never be resumed as the parent lineage. `tool-activation.js:171–180` also mutates `selectedTools` after tool-manifest renders — harmless (loader tool filtered by `BUILT_INS`) but it changes persisted sections.
6. `ctx.getSystemPrompt()` (agent-session.js:880) now returns the sections render, not the forced text. Grep `~/.pi/agent/extensions/{disable-superpowers,superpowers-bootstrap-fix,secret-scrub}.ts` for `getSystemPrompt`/`before_agent_start` before deploying.
7. Any process-global memo is unsafe at `globalConcurrencyLimit: 3`. The section approach holds no shared state, so it is safe.

## F. Could not determine

1. Which observed variant maps to which request. Mechanism established (forced prompt present vs absent) but I did not read the ds4 trace or the linode session jsonl. Confirm by checking whether the request that wrote checkpoint 294912 was preceded by a custom-message-triggered turn. The exact manifest byte offset was not reproduced; divergence byte 159188 unverified.
2. Whether `before_provider_request` fires before or after `transformContext`'s forced-prompt projection. If before, the C.5 diagnostic compares the sections head, not the wire head. Verify first.
3. Where a 7-tool manifest including `ls` came from. `_restoreToolsFromTranscript` restores `toolsAdded` from the transcript and settings grant only 6. Candidates: a lane transcript replayed as a parent, or a `defaultTools` edit since 09-28. Check the linode jsonl's system-message `toolsAdded`.
4. Whether `tests/run-all.sh` and `./deploy.sh --check` are green today — no tests or deploy commands run, per constraints. Byte-identical copies imply `--check` passes.
5. Two files in pi-extensions show as git-modified (`agent-config/macos/settings.json` 17:10, `agent-config/subagent-config.json` 00:09). Both predate this session and match the operator's own documented 09-29 concurrency change; I wrote nothing but this report.
