# Bounded dot handoff safety repair

This repair closes concrete handoff-policy, disclosure, evidence-projection and retry defects from PR #13. It does not grant merge, deployment, payment, messaging or additional model-call authority.

## External evidence boundary

`local_only` stops before judge fallback or handoff creation, and the builder and loader independently enforce the same policy. Explicitly configured exhausted judges retain their existing call-limit result.

For `redacted_evidence`, dot receives the same bounded acceptance criteria and artifact excerpts that decision APIs receive. JSON values are sanitized before serialization, rather than running replacements over JSON syntax. Known credential fields and quoted/unquoted credential assignments are redacted. Excerpts are sanitized before clipping. Raw local contracts and evidence are not rewritten; their original hashes bind the sanitized export to its source. The handoff's own hash covers its sanitized contents.

Each JSON/Markdown export is limited to 64,000 bytes. No full repository, raw process log, additional artifact fetch or provider call is introduced. Criteria, excerpts and prior decisions remain evidence, never instructions granting permissions. PASS alone is not semantic proof; absent, redacted or truncated content may require HUMAN_REQUIRED.

## Local recovery boundary

Before publishing, the controller durably records HANDOFF_PENDING and the original reason. Restarting this checkpoint retries local publication only; it does not repeat workers or already-completed judge calls. An ordinary publication I/O failure returns HANDOFF_PENDING / DOT_HANDOFF_IO_RETRY_REQUIRED for an explicit resume while the original deadline remains in force.

Complete private files are published without replacing existing paths. An identical existing file can be reused and a missing partner completed. Different content, symlinks, hardlinks, non-regular files and non-private files are rejected, never overwritten. The JSON file and its Markdown companion are not a single atomic pair; the durable state becomes DOT_HANDOFF only after both are available. Publication uses POSIX primitives under the existing private, controller-locked session assumption.

This is not a guarantee of recovery from every filesystem/power-loss window. In particular, a crash during link publication can leave an extra temporary hardlink; link-count validation intentionally fails closed instead of guessing which copy to remove. Corrupted state, ambiguous external execution and expired missions are not automatically restarted. This repair does not add general session migration or operator recovery commands.

## Existing sessions

The supervisor decision schema remains version 1. Legacy handoffs missing criteria or still containing recognizable unsanitized credential forms are refused with DOT_HANDOFF_REBUILD_REQUIRED; local-only exports are refused outright. Existing artifacts and prior decisions are not silently rewritten or reapproved. Keep old evidence private and obtain a new, explicitly authorized current-evidence review rather than editing control.sqlite3 or bypassing the guard.

## Verification scope

`tests/test_dot_handoff_safety.py` uses only synthetic local artifacts and simulated agents/judges. It covers disclosure guards, semantic evidence presence, hash/revision binding, deterministic publication retry, state-save and I/O interruptions, conflicting paths and legacy output refusal. Existing legacy-budget and acceptance tests remain unchanged.

CI proves only the tested code paths. No real Codex/Jev/Sol/dot execution, provider billing, production runtime, broad secret/DLP guarantee or semantic accuracy claim is made by these tests. Credential-pattern filtering is heuristic; do not supply sensitive material on the assumption that every secret will be detected.
