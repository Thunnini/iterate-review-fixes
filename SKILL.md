---
name: iterate-review-fixes
description: Autonomously review and repair code changes on a temporary Git branch through repeated review-fix-review cycles, isolate findings that recur without blocking independent fixes, clean the skill-created commit series, and report before asking whether to rebase/fast-forward or merge. Use when the user explicitly asks Codex to iteratively review and fix a branch or change set until no actionable findings remain or only recurring/dependent findings remain for user judgment.
---

# Iterate Review Fixes

Run a self-directed review and remediation loop without letting one stubborn finding block independent work. Keep all edits on a temporary branch and require the user's decision before integrating it.

## Establish authority and scope

- Use this workflow only when the user authorizes both review and modification. For review-only requests, report findings without creating a branch or editing files.
- Treat creating local commits and rewriting only the commits created by this workflow as authorized. Do not push, merge, resolve remote review threads, or modify unrelated systems unless the user explicitly asks.
- Read every applicable `AGENTS.md` and repository instruction before acting.
- Determine the requested review target and correct comparison base. Prefer the user-specified base; otherwise verify the upstream and merge base instead of guessing.

## Prepare the temporary branch

1. Inspect the current branch, HEAD, upstream, worktrees, and staged, unstaged, and untracked changes.
2. Require a named source branch and a clean worktree. If existing changes are present, preserve them and ask how they should be handled; never stash, discard, or absorb them silently.
3. Record `source_branch`, `source_tip`, `review_base`, and the validation commands required by the repository.
4. Create a unique `codex/review-fix-<slug>` branch from `source_tip`. Never reuse an unrelated existing branch.
5. Store loop state under the Git directory, not in the working tree. Initialize it with `scripts/loop_guard.py`.

Use a state path obtained from Git so linked worktrees remain isolated:

```bash
state_name="${temp_branch//\//-}.json"
state_path="$(git rev-parse --git-path "codex-review-loop/$state_name")"
python3 <skill-dir>/scripts/loop_guard.py init \
  --state "$state_path" \
  --source-branch "$source_branch" \
  --source-tip "$source_tip" \
  --review-base "$review_base" \
  --temp-branch "$temp_branch"
```

Read [references/review-loop-policy.md](references/review-loop-policy.md) before starting the first pass. Follow its fingerprint, dependency, commit-cleanup, and reporting rules.

## Run a review pass

1. Start a pass with `loop_guard.py start-pass`.
2. Review the full current change set against `review_base`, not only the latest fix commit.
3. Trace actual consumers and runtime paths. Report only concrete correctness, security, privacy, compatibility, or material maintainability problems supported by evidence.
4. Run focused diagnostics needed to distinguish a real defect from a plausible concern.
5. Record every finding with a stable root-cause fingerprint using `loop_guard.py find`.
6. Preserve an already deferred finding as deferred. Do not disguise it with a new fingerprint because its symptom or line number changed.
7. Finish the pass with `loop_guard.py finish-pass`.

Do not edit while the pass is open. Finish recording the review first so the pass describes one coherent tree state.

## Fix actionable findings

- Fix every independent finding whose state is `open` and whose solution stays within the authorized scope.
- Group fixes by root cause. Do not combine unrelated findings merely because they touch the same file.
- Add focused regression coverage when practical and run the narrowest meaningful validation before recording a fix.
- Record each attempted fix with `loop_guard.py fix`; the guard limits attempts per fingerprint, never the number of overall review passes.
- Make provisional commits that identify the finding or root cause. Use later `fixup!` commits for follow-up changes to the same logical fix when useful.
- Begin another full review pass after applying all currently actionable independent fixes.

## Isolate recurring, decision-blocked, or dependent findings

Mark only the affected finding as deferred when any of these applies:

- The same root-cause fingerprint survives the configured number of fix attempts.
- Fixing A creates B and fixing B recreates A.
- The apparent fix changes the symptom without restoring the violated invariant.
- Resolution requires an unapproved product, architecture, API, data-migration, or scope decision.

Use `defer --kind recurring` for a non-converging root finding, `defer --kind decision` when an unapproved decision prevents a meaningful first fix, and `defer --kind dependent` for findings that cannot be solved independently from a deferred root. Keep reviewing and fixing unrelated findings.

Never stop the whole loop merely because one finding is deferred. Interpret status as follows:

- `continue`: at least one actionable or pending-verification finding remains; continue review and repair.
- `review-required`: no review pass has completed yet; start the first pass.
- `finalize-clean`: no actionable or deferred finding remains; finalize the branch.
- `finalize-with-residual`: the user explicitly accepted every remaining residual finding; finalize while preserving them in the report.
- `report-deferred`: only recurring/decision/dependent findings remain; report them and wait for user judgment.

An unrelated fix may incidentally solve a deferred finding. Verify the original invariant and reproduction, then use `resolve` with evidence. Do not resume direct attempts on a deferred finding without user direction.

## Clean the commit series

Perform cleanup only after the guard returns `finalize-clean` or `finalize-with-residual`.

1. Review the complete diff and each workflow-created commit.
2. Record the old tip and create a temporary backup ref before rewriting.
3. Rewrite only commits after `source_tip`; never rewrite the source branch's pre-existing commits.
4. Squash commits addressing the same root cause, order dependent commits logically, and remove changes or commits made obsolete by a better later solution.
5. Confirm that cleanup preserves the intended final tree. If semantics intentionally change during cleanup, treat that as a new modification and review it again.
6. Delete the backup ref only after successful comparison and validation.
7. Run the required focused and broad validation, then perform one final review of the rewritten branch.
8. If the final review finds an actionable issue, record it and resume the loop. Do not report the branch as ready prematurely.

## Handle source-branch movement

Before reporting readiness, verify that `source_branch` still points to `source_tip`.

- If unchanged, continue.
- If advanced, rebase only the workflow-created range onto the new source tip, resolve clear in-scope conflicts, and rerun validation plus a full review.
- If the correct resolution is ambiguous, preserve the temporary branch and ask the user instead of guessing.

## Report and request the next decision

When only deferred findings remain, report:

- each fingerprint and violated invariant;
- evidence and impact;
- attempted fixes and why they did not converge;
- dependencies between deferred findings;
- current branch, commits, and validation state;
- concrete user choices.

Wait for the user's decision. Use `reopen` when they authorize another approach or expanded scope, and use `accept` when they explicitly accept the residual risk. Continue processing the temporary branch only after recording that decision.

When the final review is clean, report:

- source and temporary branches with relevant SHAs;
- findings and their fixes;
- any user-accepted residual findings;
- final commit list;
- validations and results;
- remaining risks.

Then ask whether to:

1. rebase the temporary commits onto the latest source branch, fast-forward it, and delete the local temporary branch after successful integration unless the user requests preservation;
2. create a merge commit; or
3. leave the temporary branch unmerged.

Do not perform the integration or delete the temporary branch until the user chooses.

## Clean up after fast-forward integration

After the user selects rebase/fast-forward, delete the workflow-created local temporary branch only after the source branch has successfully integrated its final tip. A successful rebase alone does not authorize cleanup. Honor an explicit request to preserve the branch.

Follow the [fast-forward cleanup policy](references/review-loop-policy.md#fast-forward-cleanup) for verification, safe deletion, and reporting. This automatic cleanup applies only to the rebase/fast-forward choice; it does not apply to the merge-commit or unmerged choices.
