# Phase 2 implementation plan

Phase 2 makes failed configurations reproducible and useful for repair. The validation gate remains the RV-Bench config check: existing source/include paths followed by Verilator lint or GHDL analyze and elaborate. Simulation smoke is a separate evaluation metric.

## Decisions needed before implementation

| Topic | Proposed contract | Check |
| --- | --- | --- |
| Report version | Keep the existing config schema at `2.0`; version `report.json` independently and document migration rules. | Old config consumers still load the canonical config. |
| Candidate identity | Derive a stable ID from normalized top, ordered sources, sorted includes, flags, parameters, and build context. | Same inputs give the same ID; changed build context gives a different ID. |
| Attempt identity | Give each tool invocation an ordered ID within its candidate. Keep immutable command arguments, cwd policy, input snapshot, exit or signal, duration, and log path. | Incremental retries and analyze/elaborate stages are individually visible. |
| Path policy | Store repository-relative input paths and a source revision; avoid temporary absolute work directories as replay inputs. | Reports can be compared across checkouts. |
| Unavailable tools | Treat a missing compiler, timeout, interruption, and resource exhaustion as distinct inconclusive outcomes. | None publish a usable config. |
| Pinned fixtures | Select a small RV-Bench subset with revision hashes and expected top/build outcomes. | Repeated runs are deterministic within the documented toolchain. |

## Bounded work packages

### 2A. Report contract and command evidence

Add stable candidate and invocation IDs, a versioned report schema, generator/tool/source provenance, and per-command records for Verilator and GHDL. Retain raw logs and existing canonical config fields. Start with the HDL paths; generator paths can follow as an explicit extension. Verify analyze and elaborate are separate GHDL invocations, retries remain ordered, no-command failures explain why, and reports serialize identically for pinned inputs except documented runtime fields.

### 2B. Structured diagnostics

Normalize missing module/entity, include/package, macro, file order, parameter mismatch, unsupported construct, vendor IP, and tool failure. Each diagnostic should carry category, raw message, candidate/invocation ID, source location when available, nearby source context, and a recovery class. Preserve the original log verbatim. Add table-driven Verilator and GHDL fixtures, including unknown-error fallback.

### 2C. Bounds and outcomes

Define candidate count, per-command timeout, and total run budget. Record the limit that stopped work and distinguish timeout, interruption, unavailable tool, resource limit, and ordinary validation failure. A stopped search must retain earlier candidate evidence and must not publish an unvalidated config. Verify process cleanup and budget behavior with controlled subprocess fixtures.

### 2D. Repository execution isolation

Inventory Bender, FuseSoC, and generator preparation commands. Run untrusted repository scripts in a bounded child workspace with explicit network, filesystem, time, and output policy. Record commands and outputs in the report. Verify that scripts cannot modify the source checkout or central config index outside the intended output path.

### 2E. CLI and migration

Add stable text and JSON modes with documented exit codes for validated, no-success, blocked, and tool failure. Include report paths and selected candidate IDs. Keep current CLI usage compatible, add migration fixtures for existing consumers, and test stdout independently of diagnostics on stderr.

## Dependency order and review gates

Implement 2A before 2B and 2C so later work has stable IDs and invocation records. Implement 2D before running untrusted project scripts in the pinned regression set. Complete 2E after the report and outcome vocabulary settle. Review each package's diff, tests, and fixture output independently before merge; do not mark a Phase 2 roadmap checkbox complete from a partial package.
