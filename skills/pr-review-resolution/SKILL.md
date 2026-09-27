---
name: pr-review-resolution
description: Resolve the first Codex pull-request review in one bounded remediation pass, validate the fixes, and prevent circular review loops.
---

# PR Review Resolution

1. Read the current PR diff, first Codex review findings, and only directly relevant code/documentation.
2. Classify each finding as valid/actionable, already resolved, not applicable, or conflicting with authoritative architecture, security controls, explicit requirements, or documented decisions.
3. Fix every valid actionable finding immediately using the smallest safe change.
4. Search only the affected scope for repetitions of the same defect when necessary.
5. Preserve architecture, interfaces, validation, security controls, rollback paths, and install/uninstall symmetry.
6. Do not make unrelated refactors, style-only rewrites, speculative improvements, or architecture changes.
7. Run the smallest relevant validation set available. Never claim validation that was not run.
8. Re-read every original finding and verify all valid findings are resolved.
9. Commit remediation to the existing PR branch.
10. Request one fresh Codex review.
11. Do not merge automatically.

## Loop prevention
Do not repeatedly revise work for substantially equivalent comments. If a follow-up comment repeats a resolved issue without new evidence, is optional/style-only, proposes an alternative architecture without a blocking defect, or concerns unchanged unrelated code, keep the existing resolution and explain why.

After two materially similar review cycles, stop modifying the PR and report the remaining disagreement for human review.

## Stop conditions
Escalate instead of changing code if remediation would weaken security, remove validation merely to pass a test, contradict authoritative project documentation, require unsafe/destructive activity, or broaden the PR beyond scope.

## Completion report
Report only findings fixed, files changed, validation/tests run, intentionally unchanged findings with reasons, and whether a fresh review was requested. Do not merge.
