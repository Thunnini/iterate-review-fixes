# Review Loop Policy

Use this policy to keep the loop convergent without allowing one recurring issue to block independent work.

## Finding identity

Create one stable fingerprint per root cause. Base it on:

- the violated invariant;
- the responsible execution or data path;
- the relevant state transition or trust boundary;
- a reproducer, failing test, or concrete evidence;
- the affected responsibility, not a volatile line number.

Prefer readable identifiers such as `sync-late-update-after-lock` or `parser-accepts-truncated-header`. Keep the fingerprint when a fix moves code or changes the surface symptom. Split findings only when their causes and independent remedies differ.

## Finding states

- `open`: require a fix or explicit deferral.
- `fixed-pending-review`: apply a fix, but do not claim resolution until a later pass no longer reproduces it.
- `resolved`: verify the invariant after a fix or an incidental resolution.
- `deferred-recurring`: exhaust direct attempts or detect an oscillating/non-convergent remedy.
- `deferred-decision`: require an explicit product, architecture, compatibility, or API decision before a meaningful fix can be chosen.
- `deferred-dependent`: depend on a deferred root finding and cannot be solved independently.
- `accepted-residual`: preserve an unresolved finding only after the user explicitly accepts its risk.

Treat `resolved` as historical. If it reappears, retain the same fingerprint and its prior attempt count.

## Loop decisions

Continue whenever at least one finding is `open` or `fixed-pending-review`, even if other findings are deferred. Do not impose a global pass limit.

Report only when either:

- all findings are resolved; or
- every unresolved finding is `deferred-recurring`, `deferred-decision`, or `deferred-dependent`.

If a pass finds no actionable issue but a fix is still pending verification, finish the pass so the guard can mark the absent fixed finding resolved.

## Independence test

Continue with another finding only when its fix can be designed, implemented, and validated without choosing the unresolved policy or architecture behind a deferred finding.

Mark it `deferred-dependent` when:

- its correct behavior changes with the deferred decision;
- its test oracle cannot be defined before that decision;
- fixing it would commit to one of several materially different product choices; or
- it would modify the same invariant through a competing mechanism.

Do not mark a finding dependent merely because it touches the same file.

## Oscillation and disguised recurrence

Treat the following as recurrence even if review wording changes:

- A fix restores one path but breaks the same invariant in another path.
- A later fix reverts the behavioral effect of an earlier fix.
- Test fixtures are changed to accept the defect without a justified behavior change.
- Repeated local patches move the failure rather than address ownership, lifetime, ordering, or trust assumptions.

Record the attempted approaches and the missing decision. Defer that fingerprint, then continue with independent findings.

## Guard commands

Run commands with the state path created during preflight:

```bash
python3 <skill-dir>/scripts/loop_guard.py start-pass --state "$state_path"

python3 <skill-dir>/scripts/loop_guard.py find --state "$state_path" \
  --fingerprint sync-late-update-after-lock \
  --title "Late sync callback restores locked state" \
  --root-cause "Callback lacks a session epoch check" \
  --evidence "Regression test and traced callback path"

python3 <skill-dir>/scripts/loop_guard.py finish-pass --state "$state_path"

python3 <skill-dir>/scripts/loop_guard.py fix --state "$state_path" \
  --fingerprint sync-late-update-after-lock \
  --summary "Gate callback writes by session epoch"

python3 <skill-dir>/scripts/loop_guard.py defer --state "$state_path" \
  --fingerprint architecture-owner-conflict \
  --kind decision \
  --reason "The intended ownership model requires user selection"

python3 <skill-dir>/scripts/loop_guard.py resolve --state "$state_path" \
  --fingerprint architecture-owner-conflict \
  --evidence "Independent refactor removed both reproductions"

python3 <skill-dir>/scripts/loop_guard.py reopen --state "$state_path" \
  --fingerprint architecture-owner-conflict \
  --additional-attempts 1 \
  --reason "User selected a new ownership model"

python3 <skill-dir>/scripts/loop_guard.py accept --state "$state_path" \
  --fingerprint low-impact-legacy-limit \
  --reason "User accepted the documented compatibility limit"

python3 <skill-dir>/scripts/loop_guard.py status --state "$state_path"
```

`find` automatically changes a recurring fingerprint to `deferred-recurring` when it reappears after the maximum number of recorded fixes. Use `deferred-decision` instead when no responsible fix can be attempted before the user chooses the intended contract. Neither state defers or stops unrelated findings.

Use `reopen` only after the user supplies a new direction or approves more scope. It preserves the attempt history and grants the requested additional attempts. Use `accept` only for an explicit residual-risk decision; acceptance is not resolution.

## Commit cleanup

Keep provisional commits small enough to attribute a change to a root cause. Before rewriting:

1. Require a clean worktree.
2. Save the old tip in a temporary ref outside normal branch names.
3. Review commits after `source_tip` and identify squash, reorder, and drop operations.
4. Squash follow-ups into the root-cause commit.
5. Drop a commit only when its effect is absent from or superseded in the intended final tree.
6. Compare the rewritten tree and full diff with the saved tip.
7. Run validation and review the rewritten commits again.

Preserve a changed final tree only when the difference is intentional and reviewed as new work.

## Reports

For a deferred report, separate:

- solved findings;
- recurring root findings;
- decision-blocked root findings;
- dependent findings;
- attempted remedies;
- evidence and impact;
- decisions the user can make next.

For a clean report, include the final commits and validation evidence. Ask for an integration method only after branch readiness is established. Never equate a clean test run with a clean review; require both.
