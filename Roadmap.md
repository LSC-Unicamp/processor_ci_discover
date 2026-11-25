# ProcessorCI Discover — Development Roadmap

**Project:** [ProcessorCI Discover](https://github.com/LSC-Unicamp/processor_ci_discover)  
**Scope:** Discovery, validation, diagnostics, and generation of processor build configurations for the ProcessorCI suite and RV-Bench.  
**Status:** Phase 1 merged; follow-up completion and Phase 2 preparation
**Last revised:** 2026-11-25

## 1. Purpose and direction

Evolve ProcessorCI Discover from a tool that chooses one compilable configuration into a tool that **discovers candidate processor tops, builds and validates their configurations, preserves unsuccessful attempts, and produces actionable diagnostics** for downstream tools and future automated repair.

The immediate priority is **candidate preservation without losing the current working heuristic fallback**. Configuration validation should follow RV-Bench's RTL validation methodology. Longer-term priorities are better top-selection accuracy, standardized outputs, reproducibility, broader HDL support, and an agent-driven repair workflow.

### Guiding principles

1. **Preserve evidence:** A failed candidate is useful if its source set, compilation command, log, and failure reason are retained.
2. **Keep working fallback:** Retaining failing candidates must not prevent Discover from finding a successful one.
3. **Separate discovery from validation:** A plausible CPU top, a config passing RV-Bench-style RTL validation, and a design proven by simulation are different states.
4. **Maintain ProcessorCI compatibility:** Defaults and output fields required by the existing infrastructure remain available.
5. **Do not treat merely compiling a module as proof it is the intended processor.**
6. **Make outputs reproducible:** Record repository revision, source dependencies, flags, tools, and result status.
7. **Keep agent repair optional and isolated:** The deterministic discovery pipeline must work without an LLM or agent.

---

## 2. Main feature: preserve up to three candidate configurations

A single discovery run should retain at most **three distinct exported candidate configurations**, chosen by role:

| Role | Source | Required behavior |
| --- | --- | --- |
| **Requested top** | Explicit user-provided top module | Always export the partial or complete configuration, even if validation fails. Include errors and enough context for later repair. |
| **First heuristic top** | Highest-ranked candidate selected by heuristics, independently of the requested top | Export its partial or complete configuration, even if validation fails. |
| **Successful heuristic fallback** | First heuristic candidate that passes the required validation, after trying candidates as needed | Export this validated configuration so the usual ProcessorCI workflow can continue. |

### Selection rules

- Compute heuristic ranking **independently** from the supplied top module.
- Evaluate the explicitly requested candidate even if it is absent from the heuristic list.
- Preserve the first ranked heuristic candidate even when it fails.
- Continue trying ranked heuristic candidates until one passes validation or the search budget is exhausted.
- **Deduplicate** candidates with identical resolved top, build context, and relevant parameters; one candidate may satisfy multiple roles.
- Export **zero to three distinct** candidate configurations; if no candidate succeeds, do not invent a successful configuration.
- Preserve **all attempted candidates in a run report**, even if only up to three are promoted to standalone config files.
- Apply explicit bounds (candidate count, per-attempt timeout, total run time) and record when search ends due to a limit.

### Example

Input top: `cpu_top`  
Heuristic ranking: `soc_top`, `processor_core`, `alu`

1. `cpu_top` fails → export requested partial config with diagnostics.
2. `soc_top` fails → export first-heuristic partial config with diagnostics.
3. `processor_core` passes validation → export successful heuristic config.

If `soc_top` passes, a separate third config is unnecessary. If `cpu_top` is also the first heuristic top with the same build context, export it once while recording both roles.

### Proposed output layout

```text
configs/
├── example_cpu.json                         # Selected usable config, only if validated
├── example_cpu.requested.json               # Distinct requested-top candidate, if applicable
├── example_cpu.heuristic-first.json         # Distinct first-heuristic candidate, if applicable
└── discovery/
    └── example_cpu/
        ├── report.json                      # All attempts, roles, ranks, selection rationale
        └── logs/
            ├── requested.log
            ├── heuristic-first.log
            └── heuristic-fallback-*.log
```

**Compatibility requirement:** Keep the canonical usable config at its existing expected location and format. The example naming scheme is provisional: choose names and an output policy that cannot overwrite or ambiguously classify candidate files. If no usable config exists, make that clear via exit status and report metadata. **Never put failed candidate configs into the central usable-config index.**

### Data model

Introduce a common candidate-result type shared by Verilog/SystemVerilog and VHDL discovery (and later generators):

```python
@dataclass
class CandidateResult:
    top_module: str
    top_file: str | None
    roles: list[str]                   # requested, heuristic_first, heuristic_fallback
    heuristic_rank: int | None
    status: str                        # see validation states below
    files: list[str]
    include_dirs: list[str]
    extra_flags: list[str]
    parameters: dict
    compile_command: list[str]
    exit_code: int | None
    errors: list[dict]
    log_path: str | None
```

The discovery pipeline should return candidate results; serialization and final config selection should be separate stages.

---


## 3. Configuration contract and infrastructure defaults

Discover feeds a larger infrastructure; several fields require **stable defaults even when detection is not yet implemented**.

### Defaults to retain

- **`march`:** Keep the current default **`rv32i`** for downstream compatibility. Treat it as a *default assumption*, not a discovered ISA claim. Add provenance such as `march_source: "default"` or an equivalent field; replace it only when reliable detection or explicit user input supplies a value.
- **`two_memory`:** Keep the current default **`false`** for downstream compatibility. Detection/selection of single versus split instruction/data memory belongs primarily in **ProcessorCI Wrapper** and should be addressed there later. Discover should not claim to infer the correct memory topology without evidence.

These defaults should be maintained across successful and failed candidate configs unless overridden by an explicit supported input. They should not be confused with factual processor characterization.

### Schema consistency

- Resolve the current inconsistency between `sim_files` and `files` in the different output paths/documentation; choose and document one canonical schema with backward compatibility.
- Version the config schema and introduce structured `discovery` metadata without breaking existing consumers.
- Preserve fields needed by Inspector, Wrapper, NTV, and other ProcessorCI tools.
- Distinguish **usable production configs** from **diagnostic partial configs**.
- Validate serialized output against a machine-readable schema.
- Record the exact source revision, top file, source paths, include directories, flags, macros, language version, parameters, commands and tool versions.
- Use relative paths or another explicitly reproducible path policy.

### Example diagnostic extension

```json
{
  "schema_version": "2.0",
  "name": "example_cpu",
  "top_module": "cpu_top",
  "sim_files": ["rtl/cpu_top.sv", "rtl/decode.sv"],
  "include_dirs": ["rtl/include"],
  "extra_flags": [],
  "march": "rv32i",
  "two_memory": false,
  "is_simulable": false,
  "discovery": {
    "roles": ["requested"],
    "status": "compile_failed",
    "validation_level": "dependency_resolved",
    "complete": false,
    "defaults": {"march": "default", "two_memory": "default"},
    "errors": [
      {
        "category": "missing_module",
        "symbol": "register_file",
        "file": "rtl/cpu_top.sv",
        "line": 42,
        "message": "Cannot resolve module reference"
      }
    ],
    "compile_command": ["verilator", "--lint-only", "..."],
    "log_file": "logs/requested.log"
  }
}
```

*Illustrative partial configuration only.* The shown lint command is a **failed diagnostic command**. The config validation gate follows RV-Bench's RTL validation methodology; it does not require a smoke simulation. The precise legacy keys must be checked against existing consumers before schema changes are merged.

---

## 4. Remove the current Ollama integration; revisit agents later

**Near-term decision:** Remove the existing Ollama-specific discovery integration and its associated selection paths, configuration flags, documentation and tests as appropriate. Do not merely leave it as an unused hidden fallback.

Discover's core should remain **deterministic and independent of any LLM server**.

### Later agent integration (separate phase)

Do **not** reintroduce Ollama in its current form. Instead, define a generic external agent or agent-harness interface that can:

1. Consume failed candidates, structured errors, exact compile commands, relevant source files and logs.
2. Propose patches, source/include changes, build flags or top-selection changes.
3. Apply changes only inside an isolated workspace and produce a diff.
4. Invoke Discover validation again through a stable API/CLI interface.
5. Export validated configurations together with patch provenance.
6. Require policy checks or approval for changes affecting hardware semantics.

The agent runner may later be implemented with a chosen agent harness; **no Ollama dependency should be part of the initial roadmap milestones**.

---

## 5. Prioritized work phases

### Phase 1 — Multi-candidate preservation and result model (**P0**)

- [x] Define candidate identity, roles, statuses, and deduplication rules.
- [x] Introduce a common `CandidateResult`/attempt structure.
- [ ] Preserve failed Verilator and GHDL candidate source sets and logs.
- [x] Evaluate the supplied top independently of heuristic ranking.
- [x] Preserve the initial heuristic top even when it fails.
- [x] Continue heuristic fallback to the first validation-successful top.
- [x] Export at most three distinct candidate configs and a full attempt report.
- [x] Ensure failed configs cannot enter the usable-config index.
- [x] Retain `march=rv32i` and `two_memory=false` defaults.
- [x] Add regression tests for requested top, first heuristic, fallback, no-success, deduplication and output compatibility.

**Exit criteria:** Every relevant path is preserved; normal fallback still works; downstream consumers continue to receive an unambiguous usable config when available.

### Phase 2 — Actionable diagnostics and reproducibility (**P1**)

- [ ] Normalize common errors: missing module/entity, include/package, macro, order, parameter mismatch, unsupported construct, vendor IP, tool failure.
- [ ] Attach exact failing command, error location, source context, candidate identity and suggested recovery class.
- [ ] Produce a versioned `report.json` with complete attempt history and provenance.
- [ ] Pin repository revisions and record HDL tools/generator versions.
- [ ] Add resource bounds and explicit outcomes for timeout or interrupted builds.
- [ ] Isolate executions of untrusted repository scripts/build systems.
- [ ] Add stable CLI output modes and machine-readable exit codes.

**Exit criteria:** Failed configs are reproducible and self-describing enough for an external repair workflow.

See [Phase 2 readiness audit](docs/phase2-readiness.md) for the current gaps, dependencies, and acceptance checks.

### Phase 3— Better processor and dependency discovery (**P1**)

- [ ] Expose ranked candidates with selection scores and reasoning.
- [ ] Distinguish CPU core tops from SoC, simulation, FPGA wrapper and non-processor modules.
- [ ] Handle duplicate module names, parameterizations and multi-target repositories.
- [ ] Improve source/package ordering, include detection, macros, and language-version selection.
- [ ] Prefer trustworthy project manifests (e.g., Bender/FuseSoC) where available, while retaining heuristic fallback.
- [ ] Avoid globally injecting unverified, core-specific preprocessor macros.
- [ ] Add an inspection-only candidate listing mode.
- [ ] Create a fixed set of known-good and known-failing CPU-top cases.

**Exit criteria:** Correct-top identification and RV-Bench-style config validation improve on pinned benchmarks, without selecting unrelated modules simply because they compile.

### Phase 4 — Unified HDL/generator support (**P2**)

- [ ] Standardize Verilog/SystemVerilog/VHDL candidate and diagnostic outputs.
- [ ] Improve VHDL library and generic handling.
- [ ] Normalize Chisel/Scala and Bluespec generated-RTL discovery into the shared pipeline.
- [ ] Record generator command, source-to-output mapping and generated RTL artifacts.
- [ ] Identify projects containing more than one language/build target; avoid simplistic global language precedence.
- [ ] Define explicit support boundaries for genuinely mixed-language simulation.

**Exit criteria:** Each supported frontend produces comparable candidate records, including failures.

### Phase 5 — ProcessorCI suite and RV-Bench integration (**P2**)

- [ ] Confirm the schema with Inspector, Wrapper, NTV and other config consumers.
- [ ] Preserve defaults relied upon by the infrastructure while distinguishing them from inferred facts.
- [ ] Establish a stable versioned API/CLI contract and migration tests.
- [ ] Make valid-config selection deterministic.
- [ ] Use pinned RV-Bench repositories/configurations for automated regression and scale tests.
- [ ] Coordinate future `two_memory` correction and interface support with **Wrapper**, rather than implementing speculative detection in Discover.

**Exit criteria:** Discovery results can be consumed consistently by downstream tools, and regressions are caught on pinned workloads.

### Phase 6 — External repair agents (**P3**, deferred)

- [ ] Design an agent-neutral diagnose/patch/validate protocol.
- [ ] Provide an isolated patch workspace with diff and provenance capture.
- [ ] Allow repeatable revalidation of an agent-proposed modification.
- [ ] Require explicit safeguards for behavior-changing modifications.
- [ ] Measure repair success and false-positive repair rate.

**Exit criteria:** An independently selected agent harness can fix a failed candidate and demonstrate the fix through Discover validation, without becoming a required runtime dependency.

### Phase 7 — Expanded repository coverage (**P3**, optional)

- [ ] Support multiple CPU implementations/configurations per repository.
- [ ] Support bounded parameter and build-target sweeps.
- [ ] Supply optional bus/interface evidence for Wrapper integration (not automatic wrapper generation).
- [ ] Cache reusable compilation and generation outputs safely.

---

## 6. Implementation entry points

*Targets to inspect and adapt; exact names/locations may change as the project evolves.*

| Area | Planned change |
| --- | --- |
| `core/api.py` | Candidate result/diagnostic structures |
| `runners/verilator_runner.py` | Preserve per-attempt evidence; apply the RV-Bench-style Verilator lint gate to the resolved config |
| `runners/ghdl_runner.py` | Preserve per-attempt evidence; clarify VHDL validation level |
| `core/config_generator.py` | Separate requested, first-heuristic, and validated-fallback paths |
| `core/output.py` | Serialize candidate configs, selected config and report |
| `core/config.py` | Enforce schema/defaults and prevent partial configs from being treated as usable |
| `core/top_selection.py` | Ranking evidence and accurate CPU-top selection |
| `cli.py` | Candidate listing, output policy, diagnostics and validation controls |
| `lang/` | Common result format for Chisel/Bluespec and VHDL support |
| `tests/` | Golden cases, status boundaries, compatibility and regressions |

Proposed CLI options (`--output-mode candidates`, `--diagnostics-json`, `--max-candidates`) and future subcommands (`candidates`, `validate`, `diagnose`) are **design proposals**, not claims of current functionality.

---

## 7. Evaluation plan

Use a **pinned subset of RV-Bench** and representative standalone ProcessorCI repositories: simple RTL cores, large SystemVerilog projects, VHDL cores, generators, multiple top candidates, unavailable dependencies, and known error conditions.

| Metric | Meaning |
| --- | --- |
| Top-1 / top-k CPU selection accuracy | Is the intended CPU top ranked correctly? |
| Correct-top elaboration rate | Does the intended top elaborate, rather than just any compiling module? |
| Simulator build rate | Can a runnable simulator be built? |
| Smoke simulation execution/pass rate | Does the build actually execute the defined simulation test? |
| Failure-preservation completeness | Do failed candidates include reproducible inputs and diagnostics? |
| Fallback success rate | Does the heuristic search find a validated alternative? |
| Schema compatibility | Can existing ProcessorCI consumers load the resulting usable config? |
| Reproducibility rate | Do pinned inputs/toolchains produce equivalent outputs? |
| Runtime distribution | Median and p95 elapsed time, including unsuccessful searches |
| Agent repair success (later) | Fraction of genuine failures repaired and revalidated |

Track **discovery accuracy**, **build success**, and **simulation execution** as separate metrics. Do not inflate success by counting an unrelated but compilable module as a correctly discovered CPU.

### Essential regression scenarios

- [x] Explicit top succeeds; heuristic top differs.
- [ ] Explicit top fails; first heuristic top fails; later heuristic succeeds.
- [x] Explicit top fails; first heuristic succeeds.
- [x] Explicit top equals first heuristic candidate; deduplication preserves both roles.
- [x] No candidate passes validation; no usable config is published.
- [ ] Early candidate has partial source/include information and errors; all are preserved.
- [ ] Lint passes but simulator build fails.
- [ ] Simulator build passes but smoke simulation fails or is blocked.
- [ ] Missing include/macro/package/module, incorrect order, duplicate top name, VHDL-specific failure.
- [ ] Chisel/Bluespec generator failure produces a comparable report (once supported).
- [ ] Defaults `march=rv32i` and `two_memory=false` remain stable for downstream consumers.
- [ ] All generated outputs are deterministic for pinned inputs.

---

## 8. First milestone: definition of done

**Prioritize this milestone before broader language expansion or agent development.**

- [x] Requested top always has a retained complete or partial candidate result.
- [x] First heuristic top has an independent retained result.
- [x] Fallback continues until the first candidate meeting the required validation gate or the search budget ends.
- [x] No more than three distinct candidate configurations are exported.
- [ ] Full attempt history and diagnostic logs are retained.
- [x] Successful, failed and inconclusive validation are explicitly distinguished.
- [x] Existing canonical usable-config path and required downstream fields remain compatible.
- [x] `march` defaults to `rv32i`; `two_memory` defaults to `false` pending later Wrapper work.
- [x] Ollama-specific integration is removed from the current core discovery workflow.
- [x] Regression tests exercise all three-candidate cases and no-success behavior.

**Validation gate:** Match RV-Bench config validation: verify declared source files and include directories, then run Verilator lint for Verilog/SystemVerilog or GHDL analyze and elaborate for VHDL. Record the exact level passed. Generator projects require their generated RTL to exist before the applicable RTL tool check. Simulator execution may be evaluated separately; it is not required to publish a configuration validated by this gate.

---

## 9. Decisions and deferred work

| Topic | Decision |
| --- | --- |
| Maximum standalone configs | At most three **distinct** candidate configs per run, with complete history in one report |
| Requested top failure | Preserve partial config and diagnostics |
| First heuristic failure | Preserve partial config and diagnostics |
| Heuristic fallback | Continue independently to find a validated candidate |
| `march` | Keep infrastructure default `rv32i`; mark as default, not detected ISA |
| `two_memory` | Keep infrastructure default `false`; actual topology handling to be addressed in Wrapper |
| Config validation | Follow RV-Bench's source/include checks and language-specific RTL tool checks; report this as config validation, not simulation execution |
| Ollama | Remove existing direct integration; consider an agent-neutral harness later |
| Chisel/Bluespec | Experimental now; unify and harden after core reliability milestones |
| Repair patches | Later, isolated, diff-based, revalidated, and optional |

## 10. Open design questions

1. How should Discover share or mirror RV-Bench's config validation gate without diverging as either implementation evolves?
2. How should failed candidate files be named so existing scripts scanning `configs/*.json` never mistake them for usable configurations? A dedicated `candidates/` subdirectory may be safer than the illustrative flat layout.
3. Which existing downstream tools read `sim_files`, `files`, `is_simulable`, and other legacy fields directly? Lock down compatibility before schema migration.
4. How should top-module identity incorporate project profile, parameters, macros, and source file, especially when duplicate module names exist?
5. If simulation is evaluated separately, what portable clock/reset test strategy is appropriate before ProcessorCI Wrapper is involved?
6. Which repository build scripts are safe to execute automatically, and what isolation/resource limits are required?

These questions should be answered during Phases 1–2; they do **not** block creating a stable candidate-result/report structure first.

```
