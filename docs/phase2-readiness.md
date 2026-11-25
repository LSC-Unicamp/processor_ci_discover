# Phase 2 readiness audit

Checked against `Roadmap.md` after Phase 1 merge. The roadmap marks only behavior verified by code and tests. The RV-Bench-style configuration gate is source/include checks plus Verilator lint or GHDL analyze and elaborate; smoke simulation is separate.

## Phase 1 work still required

| Gap | Current evidence | Completion check |
| --- | --- | --- |
| Candidate identity | Verilator and GHDL deduplicate by top and file; build flags, parameters, and source context are excluded. | Distinct build contexts remain distinct; identical contexts merge requested and heuristic roles. |
| Complete candidate export | CLI accepts `--max-candidates` above three, and success/failure exports are performed in several paths. | Report retains every attempt while only the requested, first heuristic, and first validated fallback configurations are exported, with no more than three distinct files. |
| Full attempt evidence | Some early failures have no command or log; Verilog and VHDL paths use different log locations. | Every attempt, including a missing requested top and timeout, has source/include data, command when executed, status, and a durable log or explicit no-command reason. |
| Explicit validation levels | Results use `compilation_success` and `failure`; inconclusive conditions are not consistently represented. | Config validation, failed validation, and blocked/timeout outcomes are separate and never reported as simulation execution. |
| Default provenance | Canonical and candidate outputs now carry `march=rv32i` and `two_memory=false`; generated-language outputs mark default provenance, while RTL outputs do not yet. | All exported paths mark whether each value was inferred, supplied, or defaulted. |
| Regression matrix | Existing tests cover common Verilog cases and a focused VHDL failure case. | Add requested=first-heuristic dedup, three distinct outputs, VHDL no-success, and compatibility fixtures. |

## Phase 2 implementation order

1. Define a versioned report contract with candidate identity, immutable attempt IDs, repository commit/dirty state, tool versions, commands, and source paths. Add a JSON schema and serialization tests.
2. Normalize common HDL diagnostics into categories with file/line, source context, candidate ID, and recovery class. Keep raw logs intact.
3. Make per-attempt timeout and total candidate budget explicit; record timeout/interruption rather than collapsing them into compile failure.
4. Isolate repository preparation scripts in a bounded workspace with a documented execution policy.
5. Add stable CLI JSON/text modes and documented exit codes for validated, no-success, blocked, and tool failure.

No Phase 2 checkbox is marked complete yet; the merged implementation supplies foundations but not the complete exit criteria above.
