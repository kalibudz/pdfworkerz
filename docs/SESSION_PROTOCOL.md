# Session Protocol: token look-ahead & resumable work

Build sessions can end at any time: usage limits, context size or session expiry. This protocol makes sure an interrupted session never leaves a task half-done and that the next session knows exactly where to resume.

## 1. Start of every session

**Run this first, before anything else:**

```bash
python tools/resume.py
```

It prints what the last session was doing, whether it finished, the uncommitted files, any WIP branch that is ahead, and the next action. Then:

1. The checkpoint (`state/checkpoint.json`) records `phase`, `taskId`, `step`, `branch`, `lastGreenCommit`, `nextAction`, `openQuestions`, and `status` with `startedAt`.
2. `git status` to confirm that the branch named in the checkpoint exists locally and to look over anything uncommitted before changing it.
3. Run `python tools/gate.py` (the local review workers; GitHub Actions is manual-only because its minutes are billed). If it is red, fixing it becomes the next task.
4. **Resume at `nextAction` without being asked.** An interrupted task is picked up automatically; do not start something new while one is unfinished, and do not wait for the owner to re-describe it.

### When the session before ended abruptly

A usage limit, a context overflow or an expiry stops a session between two tool calls. There is no chance to write a checkpoint then, so the mark is written **before** the work instead:

```bash
python tools/resume.py --begin "EDT-13 slice 2: align bar"   # before starting
python tools/resume.py --end                                 # after the gate is green and the work is committed
```

A session that starts and finds `status` still `in-progress` knows the one before it was cut off. `python tools/resume.py --check` exits 1 in that case, for any wrapper that wants to branch on it.

**This step 1 is only advisory** -- nothing makes a session actually run it. Found by the owner (2026-10-02): a session was cut off mid-task, and several more sessions' worth of unrelated work went by with the abandoned, uncommitted work sitting untouched the whole time, because none of them happened to run `resume.py` first and nothing forced the question. `python tools/gate.py`'s first step is now `resume.py --check` (INF-10), so the one command every session runs before every commit catches this even when step 1 above was skipped -- a red gate from this step alone means: stop, read its report, and go resume that task before starting anything else, the same as any other gate failure.

Recovering, in order:

1. `python tools/resume.py` — read the report in full.
2. Look over the uncommitted files it lists. They are the interrupted task's work in progress, not stray edits: keep what is right, and do not discard anything before reading it.
3. `git log --oneline main..wip/<id>` for any WIP branch the report shows as ahead.
4. `python tools/gate.py` — the only trustworthy statement about where the code actually stands. The checkpoint's `step` can be one step stale, because the session died before updating it; the gate never is.
5. Carry on from `nextAction`, then finish the task normally (section 4) and `--end` it.

## 2. Look-ahead before every task

```bash
python tools/session_budget.py --remaining <tokens left in this session>
```

- Estimated sizes are S ≈ 50k, M ≈ 150k and L ≈ 400k tokens (`sizes` in `tracker/features.json`).
- A task may start only if `estimate ≤ remaining × (1 − 0.25)`. The 25% reserve covers verification, the checkpoint commit and surprises.
- The tool lists unfinished (in-progress or failing) work first, then planned work in phase order, and only shows tasks that fit.
- If nothing fits: write the checkpoint (step 4) and end the session cleanly.
- For the remaining-token figure, use the session's usage display or tool when one is available. If the figure is unknown, assume only an S task fits.

## 3. During a task

- Tasks are atomic: one feature (or one acceptance criterion of an L feature) together with its tests.
- `python tools/resume.py --begin "<task>"` before the first change, so an abrupt end is detectable.
- Work on a branch `wip/<feature-id>`. Commit at every natural break point, for example after tests are written, after the implementation passes locally, or after refactoring. A commit is what survives an abrupt end; unpushed work in the editor is not.
- Before any long-running or risky step, update the checkpoint and commit to the WIP branch.
- Sub-agents each get exactly one bounded task. The orchestrator never dispatches a sub-agent whose estimate exceeds the remaining usable budget. Sub-agents commit to their own WIP branch so partial work survives.

## 4. End of every task (or when the budget runs low)

1. Run the full gate, which includes the suite with evidence: `python tools/gate.py`, then `python tools/update_tracker.py --write`.
2. Update `state/checkpoint.json` (the `nextAction` must be concrete enough for a cold start) and `PROGRESS.md`.
3. Append one line to `state/usage_log.jsonl`:
   ```json
   {"date": "2026-09-26", "taskId": "INF-06", "estimate": "M", "estTokens": 150000, "actualTokens": 132000, "outcome": "done"}
   ```
4. Commit. Push only with the owner's approval; a push is a backup and triggers no CI run.
5. `python tools/resume.py --end`, so the next session sees a clean ending rather than an interrupted task.

## 5. Recalibration

Every 10 tasks, compare `actualTokens` with `estTokens` in `state/usage_log.jsonl`. If a size class is consistently more than 20% off, adjust `sizes` in `features.json` or re-classify the affected features.
