You are conducting a bounded, read-only architectural reconnaissance benchmark on this repository snapshot.

## Objective
Treat the entire repository as the target and determine its current system topology, major structural risks, and highest-value deep-dive targets.

This is NOT a line-by-line exhaustive review. Start from the repository tree, identify the important subsystems, and inspect only the files needed to support evidence-based conclusions. Optimize for architectural information gained per file read.

## Rules
- READ ONLY.
- Do not edit, create, delete, commit, open a PR, merge, deploy, or change settings.
- Do not use external web sources.
- Do not inspect git history unless absolutely necessary to resolve a current-state contradiction.
- Do not run long builds or broad test suites.
- Do not assume README/docs are authoritative; cross-check claims against implementation/config/tests/workflows where relevant.
- Separate observed fact from inference.
- Every structural finding must name the exact repository paths that support it.
- If the repository is too large for a reasonable pass, report coverage and uncertainty explicitly.
- Maximum 8 findings.
- Keep the final answer concise; avoid tutorial material and generic software advice.

## Determine
1. Major subsystems and their relationships.
2. Active core vs experimental, legacy, duplicated, superseded, or disconnected parts.
3. Contradictions among docs, code, tests, workflows, and runtime assumptions.
4. The 3 highest-value areas for a deeper second-pass audit.
5. Whether the repository should stay one audit domain or be split before further work.

## Required output
### A. Coverage
Areas/directories inspected, approximate file count, and important areas intentionally not inspected or only sampled.

### B. System map
Up to 10 major components, one sentence each.

### C. Structural findings
For each: Severity (Critical/High/Medium), observed fact, risk/consequence, exact supporting paths, minimal next action. Maximum 8.

### D. Deep-dive targets
Rank the top 3 next audit targets by expected information value.

### E. Work split
State what can be delegated to cheaper models and what, if anything, genuinely requires a scarcer high-reasoning model.

### F. Final recommendation
Choose exactly one:
- WHOLE_REPO_OK
- SPLIT_BEFORE_DEEP_AUDIT
- TARGETED_REPAIR_FIRST
- INSUFFICIENT_COVERAGE

Finally list the exact files inspected. If large, group by directory with counts plus the most important paths.
