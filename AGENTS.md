# Repository Working Rules

## Smallest safe change
Make the smallest change that fully resolves the task. Preserve existing architecture, interfaces, service behaviour, install/uninstall symmetry, and tests unless the task explicitly requires redesign.

Do not mix unrelated work into one pull request.

## Documentation-first
Update relevant documentation when a change affects installation, configuration, service behaviour, security, hardware control, operating assumptions, or future work. Search existing documentation, issues, and open pull requests before creating duplicates.

## PR review remediation
When Codex review findings exist, use `skills/pr-review-resolution/SKILL.md`.

On the first Codex review, inspect every finding and immediately fix each valid finding in the same PR using the smallest safe change. Do not blindly implement findings that conflict with authoritative documentation, security controls, or established architecture.

After remediation, run relevant targeted validation, verify the original valid findings are resolved, then request one fresh review. Do not merge automatically.

## Review-loop prevention
A follow-up review verifies remediation; it does not restart review of unchanged unrelated code. Do not reopen settled decisions for style, naming, optional refactors, micro-optimisation, or alternative architecture.

If two materially similar review cycles fail to converge, stop modifying the PR and report the disagreement for human review.

## Validation
Never claim tests, lint, syntax checks, installation checks, or hardware validation were run unless they actually were. Preserve rollback and uninstall paths for changes that can affect a live Raspberry Pi or fan-control system.
