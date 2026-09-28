# Session Protocol: token look-ahead & resumable work

Build sessions can end at any time: usage limits, context size or session expiry. This protocol makes sure an interrupted session never leaves a task half-done and that the next session knows exactly where to resume.

## 1. Start of every session

1. Read `state/checkpoint.json`. It records `phase`, `taskId`, `step`, `branch`, `lastGreenCommit`, `nextAction` and `openQuestions`.
2. `git status` to confirm that the branch named in the checkpoint exists locally and has no stray changes.
3. Run `python tools/gate.py` (the local review workers; GitHub Actions is manual-only because its minutes are billed). If it is red, fixing it becomes the next task.
4. Resume at `nextAction`. Do not start something new while a checkpointed task is unfinished.

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
- Work on a branch `wip/<feature-id>`. Commit at every natural break point, for example after tests are written, after the implementation passes locally, or after refactoring.
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

## 5. Recalibration

Every 10 tasks, compare `actualTokens` with `estTokens` in `state/usage_log.jsonl`. If a size class is consistently more than 20% off, adjust `sizes` in `features.json` or re-classify the affected features.
