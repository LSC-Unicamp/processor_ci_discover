# Phase 2 readiness audit

Checked against `Roadmap.md` after Phase 1 merge. The roadmap marks only behavior verified by code and tests. The RV-Bench-style configuration gate is source/include checks plus Verilator lint or GHDL analyze and elaborate; smoke simulation is separate.

## Phase 1 follow-up and Phase 2 planning gaps

| Gap | Current evidence | Completion check |
| --- | --- | --- |
| Candidate identity follow-up | Verilator and GHDL now use top, source set, includes, flags, parameters, and build context; duplicate roles merge. | Add a stable serialized candidate ID and test multi-target repositories with real tools. |
| Candidate export follow-up | The HDL finalizer now selects at most three role-bearing configs while retaining all attempts in the report. Generated-language paths still use their own one-candidate writer. | Unify these writers and prove stale output cleanup across repeated runs. |
| Inner command history | Candidate-level attempts and failure logs are retained. The report records only the last command of incremental retries; early failures lack a formal no-command reason. | Phase 2 records each executed command, source/include snapshot, outcome, and a durable log or explicit no-command reason. |
| Validation detail | HDL and generated RTL paths now distinguish compilation success, failure, and timeout/inconclusive. | Add a stable validation-level enum and separate unavailable-tool, interrupted, and resource-limit outcomes. |
| Default provenance follow-up | Canonical, role-selected candidates, and generated-language outputs mark `march=rv32i` and `two_memory=false` as defaults. | Verify downstream consumers accept these fields and support explicit overrides when introduced. |
| Regression matrix | Tests cover requested=first-heuristic dedup, three distinct exports, VHDL no-success, and a real GHDL analyze/elaborate fixture. | Add pinned RV-Bench cases, downstream compatibility fixtures, and multi-target real-tool cases. |

## Phase 2 implementation order

1. Finish the versioned report contract. Reports now record repository commit/dirty state and available Verilator/GHDL versions; add immutable attempt IDs, generator versions, a JSON schema, and serialization tests. Pin source revisions for reproducible reruns. GHDL commands currently reference a temporary work directory that is removed after validation, so record portable replay inputs as well.
2. Normalize common HDL diagnostics into categories with file/line, source context, candidate ID, and recovery class. Keep raw logs intact.
3. Make per-attempt timeout and total candidate budget explicit; record timeout/interruption rather than collapsing them into compile failure.
4. Isolate repository preparation scripts in a bounded workspace with a documented execution policy.
5. Add stable CLI JSON/text modes and documented exit codes for validated, no-success, blocked, and tool failure.

No Phase 2 checkbox is marked complete yet; this preparation branch adds foundations but not the complete exit criteria above.

See [Phase 2 implementation plan](phase2-implementation-plan.md) for bounded worker tasks, dependency order, and acceptance checks.
