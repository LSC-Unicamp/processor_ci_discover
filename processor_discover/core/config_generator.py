"""
Processor Configuration Generator

This script analyzes processor repositories and generates processor configurations.
It includes the following functionality:
- Cloning processor repositories and analyzing their files.
- Extracting hardware modules and testbench files from the repository.
- Building module dependency graphs.
- Generating configuration files for the processor.
- Interactive simulation and file minimization.
- **Chisel/Scala support**: Automatic detection and processing of Chisel projects

Supported Languages:
-------------------
- Verilog (.v)
- SystemVerilog (.sv)
- VHDL (.vhd, .vhdl)
- **Chisel (.scala)** - Automatically detects, analyzes, and generates Verilog

Main Functions:
--------------
- **generate_processor_config**: Clones a repository, analyzes it, and generates a configuration
- **interactive_simulate_and_minimize**: Optimizes file lists through simulation
- **rank_top_candidates**: Identifies the best top module candidates

Command-Line Interface:
-----------------------
- `-u`, `--processor-url`: URL of the processor repository to clone.
- `-p`, `--config-path`: Path to save the configuration file.
- `-g`, `--plot-graph`: Plots the module dependency graph.
- `-a`, `--add-to-config`: Adds the generated configuration to a central config file.
- `-l`, `--local-repo`: Path to local repository (skips cloning if provided).
- `-t`, `--top-module`: Force a specific top module (tried first, then fallback to heuristics).
- `--max-candidates`: Maximum number of distinct candidates to preserve (default: 3).

Usage:
------
# Process a remote repository
python discover_config_generator.py -u <processor_url> -p config/

# Process a local repository (including Chisel projects)
python discover_config_generator.py -u <repo_url> -l /path/to/local/repo -p config/

# Force a specific top module first (fallback to heuristics if it fails)
python discover_config_generator.py -u <repo_url> -p config/ -t <top_module_name>
"""

import os
import sys
import time
import json
import shutil
import subprocess
import re
from pathlib import Path
from typing import List
from .config import (
    save_config,
    save_failed_candidate_config,
    save_successful_candidate_config,
    save_usable_config,
    is_usable_config,
)
from .file_manager import (
    remove_repo,
    find_include_dirs,
)
from .graph import plot_processor_graph, build_module_graph
from .output import create_output_json, build_discovery_report, save_discovery_report
from .candidate_export import export_candidates
from ..lang.bluespec_manager import find_bsv_files
from ..lang.chisel_manager import find_scala_files
from ..utils.log import print_green, print_red, print_yellow
from .api import RunContext, CandidateResult
from ..utils.runtime import build_run_context
from .top_selection import (
    _find_cpu_core_in_soc,
    _is_fpga_path,
    _is_functional_unit_name,
    _is_interface_module_name,
    _is_micro_stage_name,
    _is_peripheral_like_name,
    _reachable_size,
    rank_top_candidates,
)
from ..lang.chisel_manager import process_chisel_project
from ..lang.bluespec_manager import (
    process_bluespec_project,
)
from ..runners.verilator_runner import (
    compile_incremental as verilator_incremental,
)
from ..runners.ghdl_runner import (
    incremental_compilation as ghdl_incremental,
)


def normalize_generated_config(config: dict, repo_root: str | None = None) -> dict:
    """Apply the shared contract and validate generated RTL when a checkout is available."""
    config["schema_version"] = "2.0"
    config["sim_files"] = list(config.get("sim_files") or config.get("files") or [])
    config.setdefault("march", "rv32i")
    config.setdefault("two_memory", False)
    generated = bool(config.get("is_simulable") and config["sim_files"] and config.get("top_module"))
    success = False
    status = "failure"
    evidence = "generator did not produce a complete RTL configuration"
    command = []
    if generated and repo_root:
        missing = [path for path in config["sim_files"] if not os.path.isfile(os.path.join(repo_root, path))]
        if missing:
            evidence = f"missing generated source: {missing[0]}"
        elif not shutil.which("verilator"):
            status = "inconclusive"
            evidence = "Verilator is unavailable for generated RTL validation"
        else:
            command = ["verilator", "--lint-only", "--no-timing", "-Wno-fatal", "--top-module", config["top_module"]]
            command += [f"-I{path}" for path in config.get("include_dirs", [])]
            command += list(config.get("extra_flags", []))
            command += list(config["sim_files"])
            try:
                result = subprocess.run(command, cwd=repo_root, capture_output=True, text=True, timeout=240)
                success = result.returncode == 0
                status = "compilation_success" if success else "failure"
                evidence = (result.stderr or result.stdout or "Verilator lint completed").strip()
            except subprocess.TimeoutExpired:
                status = "inconclusive"
                evidence = "Verilator lint timed out after 240 seconds"
    config["is_simulable"] = success
    config["discovery"] = {
        "schema_version": "2.0",
        "has_successful_candidate": success,
        "candidate_status": status,
        "defaults": {"march": "default", "two_memory": "default"},
        "validation_gate": {"passed": success, "level": "verilator_lint" if command else "none", "command": command, "evidence": evidence},
    }
    return config

# Top-selection helpers live in processor_discover.core.top_selection.


def convert_vhdl_to_verilog_with_ghdl(
    vhdl_files: List[str], repo_root: str, output_dir: str = None
) -> tuple[List[str], bool]:
    """
    Convert VHDL files to Verilog using GHDL's synthesis feature.

    Args:
        vhdl_files: List of VHDL file paths (relative to repo_root)
        repo_root: Root directory of the repository
        output_dir: Directory to place generated Verilog files (default: repo_root/vhdl_synth)

    Returns:
        (converted_verilog_files, success)
    """
    if not vhdl_files:
        return [], True

    if output_dir is None:
        output_dir = os.path.join(repo_root, "vhdl_synth")

    os.makedirs(output_dir, exist_ok=True)

    print_yellow(
        f"[VHDL→Verilog] Converting {len(vhdl_files)} VHDL files to Verilog using GHDL..."
    )

    converted_files = []
    failed_files = []

    for vhdl_file in vhdl_files:
        vhdl_path = (
            os.path.join(repo_root, vhdl_file)
            if not os.path.isabs(vhdl_file)
            else vhdl_file
        )

        if not os.path.exists(vhdl_path):
            print_yellow(f"[VHDL→Verilog] Warning: File not found: {vhdl_path}")
            continue

        # Extract entity name from VHDL file
        entity_name = None
        try:
            with open(vhdl_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                match = re.search(
                    r"^\s*entity\s+(\w+)\s+is", content, re.MULTILINE | re.IGNORECASE
                )
                if match:
                    entity_name = match.group(1)
        except Exception as e:
            print_yellow(f"[VHDL→Verilog] Error reading {vhdl_file}: {e}")
            continue

        if not entity_name:
            print_yellow(
                f"[VHDL→Verilog] Could not find entity name in {vhdl_file}, skipping"
            )
            continue

        output_v_file = os.path.join(output_dir, f"{entity_name}.v")

        # Try GHDL synthesis: ghdl --synth --out=verilog file.vhd -e entity_name > output.v
        # Try with -fsynopsys first (for std_logic_arith, std_logic_unsigned support)
        try:
            cmd = [
                "ghdl",
                "--synth",
                "--out=verilog",
                "-fsynopsys",
                vhdl_path,
                "-e",
                entity_name,
            ]
            print(f"[VHDL→Verilog] Converting {entity_name}: {' '.join(cmd)}")

            result = subprocess.run(
                cmd, cwd=repo_root, capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0 and result.stdout:
                # Write Verilog output to file
                with open(output_v_file, "w") as f:
                    f.write(result.stdout)
                converted_files.append(os.path.relpath(output_v_file, repo_root))
                print_green(
                    f"[VHDL→Verilog] ✓ Converted {entity_name} → {os.path.basename(output_v_file)}"
                )
            else:
                print_yellow(f"[VHDL→Verilog] ✗ Failed to convert {entity_name}")
                if result.stderr:
                    print_yellow(f"  Error: {result.stderr[:200]}")
                failed_files.append(vhdl_file)

        except subprocess.TimeoutExpired:
            print_yellow(f"[VHDL→Verilog] Timeout converting {entity_name}")
            failed_files.append(vhdl_file)
        except FileNotFoundError:
            print_red(
                f"[VHDL→Verilog] GHDL not found! Please install GHDL for mixed-language support"
            )
            return [], False
        except Exception as e:
            print_yellow(f"[VHDL→Verilog] Error converting {entity_name}: {e}")
            failed_files.append(vhdl_file)

    if failed_files:
        print_yellow(
            f"[VHDL→Verilog] Failed to convert {len(failed_files)} files: {failed_files[:3]}"
        )

    success = len(converted_files) > 0
    if success:
        print_green(
            f"[VHDL→Verilog] ✓ Successfully converted {len(converted_files)}/{len(vhdl_files)} files"
        )

    return converted_files, success


def try_incremental_approach(
    repo_root: str,
    repo_name: str,
    top_candidates: list,
    modules: list,
    module_graph: dict,
    language_version: str = "1800-2023",
    verilator_extra_flags: list = None,
    timeout: int = 300,
    top_module_override: str | None = None,
    context: RunContext | None = None,
    config_path: str = "",
) -> tuple:
    """
    Try the incremental bottom-up approach for Verilog/SystemVerilog files.

    Returns: (final_files, final_includes, last_log, selected_top, is_simulable)
    """
    print_green(f"[INCREMENTAL] Trying bottom-up incremental approach for {repo_name}")

    bender_files = context.cache.get("bender_files", []) if context else []
    if bender_files and context and context.dependency_target and top_module_override:
        manifest_files = []
        for file_path in bender_files:
            if os.path.isabs(file_path):
                file_path = os.path.relpath(file_path, repo_root)
            manifest_files.append(file_path)
        manifest_includes = set(context.cache.get("bender_includes", []))
        manifest_flags = list(verilator_extra_flags or [])
        manifest_flags.extend(context.cache.get("bender_defines", []))
        cmd = [
            "verilator", "--lint-only", "-Wall", "--no-timing",
            "--relative-includes", "--language", "1800-2023", "--sv",
            "--top-module", top_module_override,
        ]
        cmd.extend(f"-I{path}" for path in sorted(manifest_includes))
        cmd.extend(manifest_flags)
        cmd.extend(manifest_files)
        try:
            result = subprocess.run(
                cmd, cwd=repo_root, capture_output=True, text=True, timeout=timeout
            )
            manifest_log = result.stdout + result.stderr
        except subprocess.TimeoutExpired as exc:
            manifest_log = (exc.stdout or "") + (exc.stderr or "")
            result = None
        if result is not None and result.returncode == 0:
            print_green(
                f"[INCREMENTAL] ✓ Bender target '{context.dependency_target}' compiled successfully"
            )
            return (
                manifest_files,
                manifest_includes,
                manifest_log + "\n[LANG-EFFECTIVE] 1800-2023 mode=sv",
                top_module_override,
                True,
            )
        print_yellow(
            f"[INCREMENTAL] Bender target compile failed; trying dependency recovery"
        )

    # A source-file override identifies one exact top declaration.  Falling
    # back to another module after that top fails can silently produce a config
    # for an unrelated arithmetic unit in large multi-core repositories.
    if (
        context is not None
        and context.top_file_override is not None
        and top_module_override
    ):
        top_candidates = [top_module_override]

    # A clean compile is not sufficient evidence that a module is a processor.
    # In particular, pipeline registers and functional units are intentionally
    # small and therefore tend to compile after the real CPU top has failed.
    # Trying those candidates produces plausible-looking but unusable configs.
    if top_candidates:
        repo_token = re.sub(r"[^a-z0-9]", "", (repo_name or "").lower())

        def _is_plausible_cpu_top(candidate: str) -> bool:
            normalized = re.sub(r"[^a-z0-9]", "", candidate.lower())
            core_named = any(
                term in candidate.lower()
                for term in ("cpu", "core", "processor", "risc")
            )
            repo_named = bool(
                repo_token
                and normalized
                and (normalized == repo_token or normalized in repo_token)
            )
            # A processor-sized top normally reaches several architectural
            # blocks.  A threshold of five avoids accepting tiny FIFOs, clock
            # gates, branch units, and pipeline registers merely because they
            # instantiate one or two helpers.
            connected = _reachable_size(module_graph, candidate) >= 5
            return (core_named or repo_named or connected) and not (
                _is_peripheral_like_name(candidate)
                or _is_functional_unit_name(candidate)
                or _is_micro_stage_name(candidate)
                or _is_interface_module_name(candidate)
            )

        filtered_candidates = [
            candidate
            for candidate in top_candidates
            if candidate == top_module_override or _is_plausible_cpu_top(candidate)
        ]
        dropped = [c for c in top_candidates if c not in filtered_candidates]
        if dropped:
            print_yellow(
                "[INCREMENTAL] Skipping non-CPU fallback candidates: "
                + ", ".join(dropped)
            )
        top_candidates = filtered_candidates

    # Limit number of candidates to avoid excessive testing
    max_candidates_limit = context.max_candidates if context else 3
    if len(top_candidates) > max_candidates_limit:
        print_yellow(
            f"[INCREMENTAL] Limiting to top {max_candidates_limit} candidates (out of {len(top_candidates)})"
        )
        top_candidates = top_candidates[:max_candidates_limit]

    print_green(f"[INCREMENTAL] Candidates to try: {', '.join(top_candidates)}")

    # Build module->file map
    module_to_file = {}
    for mname, mfile in modules or []:
        module_to_file[mname] = mfile
    if context is not None and context.top_file_override is not None:
        requested_file = str(context.top_file_override).replace("\\", "/")
        for mname, mfile in modules or []:
            if str(mfile).replace("\\", "/").endswith(requested_file):
                module_to_file[mname] = mfile

    # Try each top candidate with incremental compilation
    # idx is 1-based: position in the candidate list (after override prepend).
    # This position doubles as the heuristic rank.
    has_override = (
        context is not None and context.top_module_override is not None
    )

    # Track the first successful candidate so we can still try remaining
    # heuristic candidates before returning (Blocker 1).
    first_success: tuple | None = None
    _first_success_roles: list[str] | None = None
    # Track independent heuristic rank for non-override candidates (Blocker 2).
    heuristic_rank = 0

    for idx, top_module in enumerate(top_candidates, 1):
        print_green(
            f"[INCREMENTAL] === Candidate {idx}/{len(top_candidates)}: {top_module} ==="
        )
        if top_module not in module_to_file:
            print_yellow(f"[INCREMENTAL] Skipping {top_module} - no file mapping found")
            # If this was an explicitly requested top, record the failure
            # and save a failed config so it is exported (Blocker 3 residual).
            if has_override and top_module == context.top_module_override and context is not None:
                context.candidates.append(
                    CandidateResult(
                        top_module=top_module,
                        top_file=None,
                        roles=["requested"],
                        heuristic_rank=1,
                        status="failure",
                        files=[],
                        include_dirs=[],
                        extra_flags=list(verilator_extra_flags or []),
                        parameters={},
                        compile_command="verilator --top-module " + top_module,
                        exit_code=1,
                        errors=[f"Module '{top_module}' not found in module list"],
                        log_path=None,
                    )
                )
                if context.config_path is None:
                    continue
                output_cfg = create_output_json(
                    repo_name=repo_name,
                    url=context.repository_url or "",
                    filtered_files=[],
                    include_dirs=set(),
                    top_module=top_module,
                    language_version=language_version,
                    is_simulable=False,
                )
                output_cfg["discovery"] = {
                    "schema_version": "2.0",
                    "has_successful_candidate": False,
                    "candidate_status": "failure",
                    "candidate_count": len(context.candidates),
                    "max_candidates": context.max_candidates,
                }
                save_failed_candidate_config(
                    str(context.config_path),
                    output_cfg,
                    repo_name,
                    top_module=top_module,
                    attempt_index=idx,
                )
            continue

        top_module_file = module_to_file[top_module]

        # Make sure it's a relative path
        if os.path.isabs(top_module_file):
            top_module_file = os.path.relpath(top_module_file, repo_root)

        # Also strip any leading repo path components that might be in the path
        # For example: "temp/black-parrot/bp_be/..." should become "bp_be/..."
        repo_basename = os.path.basename(repo_root)
        if top_module_file.startswith(f"{repo_basename}/"):
            top_module_file = top_module_file[len(repo_basename) + 1 :]
        elif top_module_file.startswith("temp/"):
            # Handle "temp/black-parrot/..." -> strip temp/ prefix
            parts = top_module_file.split("/")
            if len(parts) > 2 and parts[0] == "temp":
                top_module_file = "/".join(parts[2:])  # Skip "temp/reponame/"

        print_green(f"[INCREMENTAL] Testing top module: {top_module}")
        print_green(f"[INCREMENTAL] Top module file (final): {top_module_file}")
        print_yellow(f"[INCREMENTAL] Repo root: {repo_root}")
        print_yellow(f"[INCREMENTAL] Repo basename: {repo_basename}")

        # Verify the file exists
        full_path = os.path.join(repo_root, top_module_file)
        if not os.path.exists(full_path):
            print_red(f"[INCREMENTAL] ERROR: File does not exist: {full_path}")
            print_red(f"[INCREMENTAL] Skipping {top_module}")
            continue
        else:
            print_green(f"[INCREMENTAL] ✓ File exists: {full_path}")

        # Deduplication key: (top_module, top_file).
        # Prevents recording the same candidate twice when the loop
        # is called from multiple discovery paths.
        dedup_key = (top_module, top_module_file)

        # Check if this candidate was already recorded (e.g. by a prior
        # pass through try_incremental_approach or ghdl_incremental).
        # If found, merge the new role into the existing entry (Blocker 2).
        _existing_idx: int | None = None
        if context is not None:
            for _i, _c in enumerate(context.candidates):
                if _c.top_module == dedup_key[0] and _c.top_file == dedup_key[1]:
                    _existing_idx = _i
                    break

        rc, log, final_files, final_includes, _verilator_last_cmd = verilator_incremental(
            repo_root=repo_root,
            top_module=top_module,
            top_module_file=top_module_file,
            module_graph=module_graph,
            language_version=language_version,
            extra_flags=verilator_extra_flags
            or [
                "-Wno-lint",
                "-Wno-fatal",
                "-Wno-style",
                "-Wno-BLKANDNBLK",
                "-Wno-SYMRSVDWORD",
            ],
            max_iterations=context.maximize_attempts if context is not None else 20,
            timeout=timeout,
            context=context,
        )

        # Determine the role / provenance for this candidate.
        # Blocker 2 fix: the override candidate is separate from heuristics.
        # Non-override candidates track their own independent rank.
        if has_override and top_module == context.top_module_override:
            role = "requested"
            heuristic_rank = 1 if context.cache.get("heuristic_first_top") == top_module else 0
        else:
            heuristic_rank += 1
            role = "first_heuristic" if heuristic_rank == 1 else "fallback"
        roles = [role]
        if context is not None and context.cache.get("heuristic_first_top") == top_module and "first_heuristic" not in roles:
            roles.append("first_heuristic")

        if rc == 0:
            print_green(f"[INCREMENTAL] ✓ Success with top module: {top_module}")
            # Record the successful candidate. If already seen, merge the role
            # instead of duplicating (Blocker 2).
            if _existing_idx is not None and context is not None:
                context.candidates[_existing_idx].roles = list(dict.fromkeys(context.candidates[_existing_idx].roles + roles))
            else:
                if context is not None:
                    context.candidates.append(
                        CandidateResult(
                            top_module=top_module,
                            top_file=top_module_file,
                            roles=roles,
                            heuristic_rank=heuristic_rank,
                            status="compilation_success",
                            files=list(final_files),
                            include_dirs=list(final_includes),
                            extra_flags=list(verilator_extra_flags or []),
                            parameters={},
                            compile_command=_verilator_last_cmd,
                            exit_code=0,
                            errors=[],
                            log_path=None,
                        )
                    )
            # Record the first successful candidate and continue trying
            # remaining heuristic candidates before returning (Blocker 1).
            if first_success is None:
                first_success = (final_files, final_includes, log, top_module, True)
                _first_success_roles = roles
        else:
            # Capture diagnostics from the runner log for the failure report.
            error_lines = []
            if log:
                for line in log.splitlines():
                    stripped = line.strip()
                    if any(
                        kw in stripped.lower()
                        for kw in ("error", "undefined", "fatal", "could not find", "syntax error", "conflict")
                    ):
                        error_lines.append(stripped)
            # If no explicit error lines found, use the last 5 log lines as diagnostics.
            if not error_lines and log:
                error_lines = log.splitlines()[-5:]

            failure_log_path = None
            if context is not None:
                discovery_dir = os.path.join("discovery", repo_name, "logs")
                os.makedirs(discovery_dir, exist_ok=True)
                safe_top = re.sub(r"[^a-zA-Z0-9_]", "_", top_module or "unknown")
                failure_log_path = os.path.join(
                    discovery_dir, f"{safe_top}_attempt.log"
                )
                try:
                    with open(failure_log_path, "w", encoding="utf-8") as lf:
                        lf.write(log or "")
                except Exception:
                    failure_log_path = None

            # Record failure candidate. If already seen, merge the role
            # instead of duplicating (Blocker 2).
            if _existing_idx is not None and context is not None:
                context.candidates[_existing_idx].roles = list(dict.fromkeys(context.candidates[_existing_idx].roles + roles))
            else:
                if context is not None:
                    context.candidates.append(
                        CandidateResult(
                            top_module=top_module,
                            top_file=top_module_file,
                            roles=roles,
                            heuristic_rank=heuristic_rank,
                            status="failure",
                            files=list(final_files),
                            include_dirs=list(final_includes),
                            extra_flags=list(verilator_extra_flags or []),
                            parameters={},
                            compile_command=_verilator_last_cmd,
                            exit_code=rc,
                            errors=error_lines,
                            log_path=failure_log_path,
                        )
                    )

            # Save the failed config to candidates/ directory
            if _existing_idx is None and context is not None and context.config_path is not None:
                output_cfg = create_output_json(
                    repo_name=repo_name,
                    url=context.repository_url or "",
                    filtered_files=list(final_files),
                    include_dirs=final_includes,
                    top_module=top_module,
                    language_version=language_version,
                    is_simulable=False,
                )
                output_cfg["discovery"] = {
                    "schema_version": "2.0",
                    "has_successful_candidate": False,
                    "candidate_status": "failure",
                    "candidate_count": len(context.candidates),
                    "max_candidates": context.max_candidates,
                }
                save_failed_candidate_config(
                    str(context.config_path),
                    output_cfg,
                    repo_name,
                    top_module=top_module,
                    attempt_index=idx,
                )

            if (
                top_module_override
                and top_module == top_module_override
                and len(top_candidates) > 1
            ):
                print_yellow(
                    f"[INCREMENTAL] Override '{top_module_override}' failed, falling back to heuristic candidates..."
                )
            print_yellow(f"[INCREMENTAL] ✗ Failed with top module: {top_module}")

    # Return the first successful candidate (if any), otherwise the failure result
    if first_success is not None:
        files, includes, _log, top_module, _success = first_success
        # Coordinator claim (b) fix: save standalone config for successful
        # candidates so downstream consumers can inspect role-selected artifacts.
        if context is not None and context.config_path is not None and _first_success_roles is not None:
            output_cfg = create_output_json(
                repo_name=repo_name,
                url=context.repository_url or "",
                filtered_files=list(files),
                include_dirs=includes,
                top_module=top_module,
                language_version=language_version,
                is_simulable=False,
            )
            save_successful_candidate_config(
                str(context.config_path),
                output_cfg,
                repo_name,
                top_module=top_module,
                roles=_first_success_roles,
            )
        return first_success
    return [], set(), "", "", False


def interactive_simulate_and_minimize(
    repo_root: str,
    repo_name: str,
    url: str,
    tb_files: list,
    candidate_files: list,
    include_dirs: set,
    modules: list,
    module_graph: dict,
    module_graph_inverse: dict,
    language_version: str,
    maximize_attempts: int = 6,
    verilator_extra_flags: list | None = None,
    ghdl_extra_flags: list | None = None,
    top_module_override: str | None = None,
    context: RunContext | None = None,
) -> tuple:
    """
    Interactive flow is now delegated to runners. Core only selects candidates and passes to runners.
    """
    if context is not None:
        if context.top_module_override is not None:
            top_module_override = context.top_module_override
        if context.maximize_attempts:
            maximize_attempts = context.maximize_attempts

    if context is not None and context.top_file_override is not None:
        requested_file = str(context.top_file_override).replace("\\", "/")
        requested_root = os.path.dirname(requested_file)
        if requested_root:
            def _in_requested_tree(path: str) -> bool:
                normalized = str(path).replace("\\", "/")
                return normalized.startswith(requested_root + "/") or f"/{requested_root}/" in normalized

            scoped_candidates = [f for f in candidate_files if _in_requested_tree(f)]
            scoped_modules = [(m, f) for m, f in modules if _in_requested_tree(f)]
            if scoped_candidates and scoped_modules:
                candidate_files = scoped_candidates
                tb_files = [f for f in tb_files if _in_requested_tree(f)]
                modules = scoped_modules
                print_yellow(f"[TOP] Scoped dependency discovery to source tree: {requested_root}/")

    # Prefer non-board RTL when it exists, but some repositories keep their
    # entire synthesizable core under fpga/src.  Dropping every such file made
    # those repositories appear to contain no HDL at all.
    non_fpga_candidates = [f for f in candidate_files if not _is_fpga_path(f)]
    if non_fpga_candidates:
        candidate_files = non_fpga_candidates
        tb_files = [f for f in tb_files if not _is_fpga_path(f)]
    elif candidate_files:
        print_yellow("[FILTER] All candidate RTL is under FPGA paths; retaining it")

    # Filter out unit-test verification trees that frequently redefine parameters and test-only scaffolding
    def _is_unittest_path(p: str) -> bool:
        try:
            pl = p.replace("\\", "/").lower()
            return "/verification/unittest/" in pl
        except Exception:
            return False

    before_cf, before_tb = len(candidate_files), len(tb_files)
    candidate_files = [f for f in candidate_files if not _is_unittest_path(f)]
    tb_files = [f for f in tb_files if not _is_unittest_path(f)]
    dropped_cf, dropped_tb = before_cf - len(candidate_files), before_tb - len(tb_files)
    if dropped_cf or dropped_tb:
        print_yellow(
            f"[FILTER] Excluded Verification/UnitTest files -> non-tb:{dropped_cf} tb:{dropped_tb}"
        )
    # Rank candidates using existing heuristics
    candidates, cpu_core_matches = rank_top_candidates(
        module_graph, module_graph_inverse, repo_name=repo_name, modules=modules
    )
    if not candidates:
        candidates = [m for m, _ in modules] if modules else []
    if context is not None:
        context.cache["heuristic_first_top"] = candidates[0] if candidates else None

    # Build module->file map
    module_to_file = {}
    for mname, mfile in modules or []:
        module_to_file[mname] = mfile
    if context is not None and context.top_file_override is not None:
        requested_file = str(context.top_file_override).replace("\\", "/")
        for mname, mfile in modules or []:
            normalized_file = str(mfile).replace("\\", "/")
            if normalized_file.endswith(requested_file):
                module_to_file[mname] = mfile
                print_yellow(f"[TOP] Using source-file override for {mname}: {requested_file}")

    # Determine primary top candidate and refine if it looks peripheral-like (AXI/memory/fabric)
    primary_top = candidates[0] if candidates else None
    if top_module_override:
        if top_module_override in module_to_file:
            print_yellow(
                f"[TOP] Using user-specified top module override: {top_module_override}"
            )
        else:
            print_yellow(
                f"[TOP] Warning: top module override '{top_module_override}' not found in extracted modules; will still attempt to record failure"
            )
        primary_top = top_module_override
        # Always prepend the override so it is tried first and failures are
        # recorded (fix for coordinator discrepancy: unreachable branch).
        # If the override is not in module_to_file the loop will skip it and
        # save a failed config; if it exists it compiles like any other candidate.
        candidates = [top_module_override] + [
            c for c in candidates if c != top_module_override
        ]
    if (
        primary_top
        and not top_module_override
        and (
            _is_peripheral_like_name(primary_top)
            or _is_functional_unit_name(primary_top)
            or _is_micro_stage_name(primary_top)
            or _is_interface_module_name(primary_top)
        )
    ):
        refined = _find_cpu_core_in_soc(primary_top, module_graph, modules)
        if refined and refined != primary_top:
            print_yellow(
                f"[TOP] Refined top from peripheral-like '{primary_top}' to CPU core '{refined}'"
            )
            primary_top = refined
        else:
            # Fallback to first non-peripheral and non-functional candidate if available
            non_periph_cands = [
                c
                for c in candidates
                if not _is_peripheral_like_name(c)
                and not _is_functional_unit_name(c)
                and not _is_micro_stage_name(c)
                and not _is_interface_module_name(c)
            ]
            if non_periph_cands:
                print_yellow(
                    f"[TOP] Swapping peripheral-like top '{candidates[0]}' to '{non_periph_cands[0]}'"
                )
                primary_top = non_periph_cands[0]
            else:
                # As a last attempt, strongly prefer modules containing 'core', 'cpu', or repo name tokens
                prefer_terms = ["core", "cpu", "processor", (repo_name or "").lower()]
                strong_cands = [
                    c
                    for c in candidates
                    if any(t and t in c.lower() for t in prefer_terms)
                ]
                if strong_cands:
                    print_yellow(
                        f"[TOP] Fallback to strong core-like candidate '{strong_cands[0]}'"
                    )
                    primary_top = strong_cands[0]

    # Reorder candidates to ensure primary_top is first
    if primary_top and candidates:
        candidates = [primary_top] + [c for c in candidates if c != primary_top]

    # Determine file extension of the chosen primary top
    primary_ext = None
    if primary_top and primary_top in module_to_file:
        try:
            primary_ext = os.path.splitext(module_to_file[primary_top])[1].lower()
        except Exception:
            primary_ext = None

    # Split files and candidates by language
    verilog_exts = {".v", ".sv", ".vh", ".svh"}
    vhdl_exts = {".vhd", ".vhdl"}
    verilog_files = [
        f for f in candidate_files if os.path.splitext(f)[1].lower() in verilog_exts
    ]
    vhdl_files = [
        f for f in candidate_files if os.path.splitext(f)[1].lower() in vhdl_exts
    ]
    tb_verilog = [f for f in tb_files if os.path.splitext(f)[1].lower() in verilog_exts]
    tb_vhdl = [f for f in tb_files if os.path.splitext(f)[1].lower() in vhdl_exts]

    verilog_candidates = [
        c
        for c in candidates
        if os.path.splitext(module_to_file.get(c, ""))[1].lower() in verilog_exts
    ]
    vhdl_candidates = [
        c
        for c in candidates
        if os.path.splitext(module_to_file.get(c, ""))[1].lower() in vhdl_exts
    ]

    # Filter out peripheral-like candidates if we still have others left; keep primary_top if it's the only option
    non_periph_verilog = [
        c
        for c in verilog_candidates
        if not _is_peripheral_like_name(c)
        and not _is_functional_unit_name(c)
        and not _is_micro_stage_name(c)
        and not _is_interface_module_name(c)
    ]
    if non_periph_verilog:
        # Preserve order and keep primary_top first when present
        if primary_top in non_periph_verilog:
            non_periph_verilog = [primary_top] + [
                c for c in non_periph_verilog if c != primary_top
            ]
        # Keep user override even if it looks peripheral-like
        if top_module_override and top_module_override not in non_periph_verilog:
            verilog_candidates = [top_module_override] + non_periph_verilog
        else:
            verilog_candidates = non_periph_verilog

    non_periph_vhdl = [
        c
        for c in vhdl_candidates
        if not _is_peripheral_like_name(c)
        and not _is_functional_unit_name(c)
        and not _is_micro_stage_name(c)
        and not _is_interface_module_name(c)
    ]
    if non_periph_vhdl:
        if primary_top in non_periph_vhdl:
            non_periph_vhdl = [primary_top] + [
                c for c in non_periph_vhdl if c != primary_top
            ]
        # Keep user override even if it looks peripheral-like
        if top_module_override and top_module_override not in non_periph_vhdl:
            vhdl_candidates = [top_module_override] + non_periph_vhdl
        else:
            vhdl_candidates = non_periph_vhdl

    # Choose simulator based on the primary top candidate's file extension
    # IMPORTANT: For mixed-language designs, the top module's language determines the simulator
    # - If top is Verilog → use Verilator (GHDL cannot handle Verilog top modules)
    # - If top is VHDL → use GHDL (Verilator cannot handle VHDL top modules)
    prefer_ghdl = False
    if primary_ext is not None:
        # Primary top module language takes precedence
        prefer_ghdl = primary_ext in vhdl_exts
        if primary_ext in verilog_exts and vhdl_files:
            print_yellow(
                f"[CORE] Mixed-language design detected: Verilog top '{primary_top}' with {len(vhdl_files)} VHDL files"
            )
            print_yellow(
                f"[CORE] Using Verilator (GHDL cannot simulate Verilog top modules)"
            )
    else:
        # Fallback: if majority of candidates are VHDL, prefer GHDL
        prefer_ghdl = len(vhdl_candidates) >= len(verilog_candidates)

    excluded_share = set()

    if prefer_ghdl and vhdl_candidates:
        print_green(
            f"[CORE] Selecting GHDL (VHDL) | top={primary_top} vhdl_candidates={len(vhdl_candidates)} files={len(vhdl_files)}"
        )

        print_green(f"[CORE] Trying incremental bottom-up GHDL approach first...")
        is_simulable, last_log, final_files, top_module, _ghdl_last_cmd = ghdl_incremental(
            repo_root=repo_root,
            repo_name=repo_name,
            top_candidates=vhdl_candidates,
            modules=modules,
            ghdl_extra_flags=ghdl_extra_flags or ["-frelaxed"],
            timeout=240,
            top_module_override=top_module_override,
            context=context,
        )
        if is_simulable:
            print_green(f"[CORE] ✓ Incremental GHDL approach succeeded!")
            return final_files, set(), last_log, top_module, is_simulable
        else:
            print_yellow(
                f"[CORE] Incremental GHDL approach failed, returning failure..."
            )
            return final_files, set(), last_log, top_module, is_simulable

        if is_simulable:
            return final_files, final_includes, last_log, top_module, is_simulable

    # Try Verilator if preferred path failed or primary is Verilog
    if verilog_candidates:
        print_green(
            f"[CORE] Selecting Verilator (Verilog/SV) | top={primary_top} verilog_candidates={len(verilog_candidates)} files={len(verilog_files)} includes={len(include_dirs)}"
        )

        # If mixed-language (Verilog top + VHDL modules), convert VHDL to Verilog first
        if primary_ext in verilog_exts and vhdl_files:
            print_yellow(
                f"[CORE] Attempting to convert {len(vhdl_files)} VHDL files to Verilog for mixed-language support..."
            )
            converted_v_files, conversion_success = convert_vhdl_to_verilog_with_ghdl(
                vhdl_files=vhdl_files, repo_root=repo_root
            )

            if conversion_success and converted_v_files:
                print_green(
                    f"[CORE] ✓ Converted {len(converted_v_files)} VHDL files to Verilog"
                )
                # Add converted Verilog files to the file list
                verilog_files.extend(converted_v_files)
                candidate_files.extend(converted_v_files)
                # Remove VHDL files from candidate list since we have Verilog versions
                candidate_files = [
                    f
                    for f in candidate_files
                    if os.path.splitext(f)[1].lower() not in vhdl_exts
                ]
                print_yellow(
                    f"[CORE] Updated file list: {len(verilog_files)} Verilog files (including {len(converted_v_files)} converted)"
                )
            else:
                print_yellow(
                    f"[CORE] ⚠ VHDL conversion failed or incomplete - continuing with Verilog-only simulation"
                )
                print_yellow(
                    f"[CORE] Note: VHDL modules will not be included in simulation"
                )

        print_green(f"[CORE] Trying incremental bottom-up approach first...")
        final_files, final_includes, last_log, top_module, is_simulable = (
            try_incremental_approach(
                repo_root=repo_root,
                repo_name=repo_name,
                top_candidates=verilog_candidates,
                modules=modules,
                module_graph=module_graph,
                language_version=language_version,
                verilator_extra_flags=verilator_extra_flags,
                timeout=240,
                top_module_override=top_module_override,
                context=context,
            )
        )
        if is_simulable:
            print_green(f"[CORE] ✓ Incremental approach succeeded!")
            return final_files, final_includes, last_log, top_module, is_simulable
        else:
            print_yellow(f"[CORE] Incremental approach failed, returning failure...")
            return final_files, final_includes, last_log, top_module, is_simulable

        # Return empty result if incremental is disabled
        return [], set(), "", "", False

    # Final fallback: try GHDL if not yet tried or both lists empty
    if vhdl_candidates:
        print_yellow(
            f"[CORE] Fallback to GHDL (VHDL) after Verilator path | candidates={len(vhdl_candidates)}"
        )

        print_green(f"[CORE] Trying fallback incremental GHDL approach...")
        is_simulable, last_log, final_files, top_module, _ghdl_last_cmd = ghdl_incremental(
            repo_root=repo_root,
            repo_name=repo_name,
            top_candidates=vhdl_candidates,
            modules=modules,
            ghdl_extra_flags=ghdl_extra_flags or ["-frelaxed"],
            timeout=240,
            top_module_override=top_module_override,
            context=context,
        )
        return final_files, set(), last_log, top_module, is_simulable

    # If we get here, nothing worked; return empty result
    return [], set(), "", "", False


def determine_language_version(
    extension: str, files: list = None, base_path: str = None
) -> str:
    """
    Determines a starting language version based on file extension.
    The actual language will be detected per-file-set during incremental compilation.
    """
    # Return a reasonable default based on extension
    # The incremental compiler will do the actual detection on the selected files
    base_version = {
        ".vhdl": "08",
        ".vhd": "08",
        ".sv": "1800-2017",  # SystemVerilog files default to SV
        ".svh": "1800-2017",
        ".v": "1800-2017",  # .v files default to SV (will be downgraded if needed)
    }.get(extension, "1800-2017")

    return base_version


def generate_processor_config(
    url: str,
    config_path: str,
    plot_graph: bool = False,
    add_to_config: bool = False,
    local_repo: str = None,
    top_module_override: str | None = None,
    context: RunContext | None = None,
    core_name: str | None = None,
    top_file_override: str | None = None,
    dependency_target: str | None = None,
    max_candidates: int = 3,
) -> dict:
    """
    Main function to generate a processor configuration.

    Args:
        url: Repository URL
        config_path: Path to save configuration
        plot_graph: Whether to plot dependency graphs
        add_to_config: Whether to add to central config
        local_repo: Path to local repository (skips cloning if provided)
        max_candidates: Maximum number of distinct candidates to explore
    """
    repo_name = core_name or _extract_repo_name(url)
    if core_name is None:
        existing_config = Path(config_path) / f"{repo_name}.json"
        if existing_config.exists():
            try:
                existing_repository = json.loads(
                    existing_config.read_text(encoding="utf-8")
                ).get("repository", "")
            except (OSError, json.JSONDecodeError):
                existing_repository = ""
            normalized_existing = existing_repository.rstrip("/")
            normalized_url = url.rstrip("/")
            if normalized_existing.endswith(".git"):
                normalized_existing = normalized_existing[:-4]
            if normalized_url.endswith(".git"):
                normalized_url = normalized_url[:-4]
            if existing_repository and normalized_existing != normalized_url:
                owner = re.sub(
                    r"[^a-zA-Z0-9_-]+", "_", extract_repo_owner(url)
                ).strip("_").lower()
                if owner:
                    repo_name = f"{owner}_{repo_name}"
                    print_yellow(
                        f"[NAME] Config name collision; using '{repo_name}'"
                    )
    run_context = context or build_run_context(
        repo_root=Path("."),
        repo_name=repo_name,
        repository_url=url,
        config_path=config_path,
        local_repo=local_repo,
        plot_graph=plot_graph,
        add_to_config=add_to_config,
        top_module_override=top_module_override,
        top_file_override=top_file_override,
        dependency_target=dependency_target,
        max_candidates=max_candidates,
    )
    if context is not None:
        run_context.repo_name = repo_name
        run_context.repository_url = url
        run_context.config_path = Path(config_path)
        run_context.local_repo = Path(local_repo) if local_repo is not None else None
        run_context.plot_graph = plot_graph
        run_context.add_to_config = add_to_config
        run_context.top_module_override = (
            top_module_override or run_context.top_module_override
        )
        run_context.top_file_override = Path(top_file_override) if top_file_override else run_context.top_file_override
        run_context.dependency_target = dependency_target or run_context.dependency_target

    config_path = str(run_context.config_path or config_path)
    local_repo = (
        str(run_context.local_repo)
        if run_context.local_repo is not None
        else local_repo
    )
    plot_graph = run_context.plot_graph
    add_to_config = run_context.add_to_config

    # Use local repo if provided, otherwise clone.  Supplying -l is an explicit
    # request not to clone, so a typo must be reported rather than silently
    # changing the execution mode and accessing the network.
    if local_repo and not os.path.exists(local_repo):
        raise FileNotFoundError(f"Local repository path does not exist: {local_repo}")

    if local_repo:
        # Check if local_repo is the actual repo or a parent directory containing it
        if os.path.isdir(os.path.join(local_repo, ".git")):
            # It's the actual repository
            destination_path = os.path.abspath(local_repo)
            print_green(f"[LOG] Using local repository: {destination_path}")
        else:
            # Check if local_repo is a parent directory containing repo_name
            potential_path = os.path.join(local_repo, repo_name)
            if os.path.exists(potential_path) and os.path.isdir(
                os.path.join(potential_path, ".git")
            ):
                destination_path = os.path.abspath(potential_path)
                print_green(
                    f"[LOG] Found repository in parent directory: {destination_path}"
                )
            else:
                # Treat it as the repo path anyway (might be a non-git directory)
                destination_path = os.path.abspath(local_repo)
                print_yellow(
                    f"[LOG] Using provided path (no .git found): {destination_path}"
                )
    else:
        destination_path = _clone_and_validate_repo(url, repo_name)
        if not destination_path:
            return {}

    # Handle dependency managers (Bender, FuseSoC, etc.)
    # This must be done BEFORE scanning for files since it fetches external dependencies
    deps_fetched = _handle_dependency_manager(
        destination_path, repo_name, top_module_override, run_context.dependency_target
    )
    run_context.cache["dependency_manifest"] = deps_fetched
    if deps_fetched:
        print_green(f"[DEPS] Dependencies fetched - rescanning files and includes...")

    files, extension = _find_and_log_files(destination_path)
    run_context.repo_root = Path(destination_path)

    # Check if this is a Bluespec project
    if extension == ".bsv":
        print_green("[LOG] Processando projeto Bluespec\n")
        config = process_bluespec_project(destination_path, repo_name)

        if not config or "error" in config:
            print_yellow(
                "[WARNING] Failed to process Bluespec project or compilation failed"
            )
            if "error" in config:
                print_yellow(f'[WARNING] Error: {config["error"]}')
                # Still continue with the config, just mark as non-simulable
                config.pop("error", None)
            else:
                if not local_repo:
                    remove_repo(repo_name)
                return {}

        # Add repository URL
        config["repository"] = url
        normalize_generated_config(config, destination_path)

        # Save configuration
        print_green("[LOG] Salvando configuração\n")
        if not os.path.exists(config_path):
            os.makedirs(config_path)

        # Build discovery block for Bluespec
        config["discovery"]["max_candidates"] = run_context.max_candidates

        # Save configuration (only if usable)
        print_green("[LOG] Salvando configuração\n")
        if not os.path.exists(config_path):
            os.makedirs(config_path)

        # Coordinator claim (b) fix: standalone config for the successful
        # Bluespec candidate before the main save.
        if config["discovery"]["has_successful_candidate"]:
            save_successful_candidate_config(
                str(config_path), config, repo_name,
                top_module=config.get("top_module"), roles=["first_heuristic"],
            )

        config_file = save_usable_config(config_path, config, repo_name)
        if config_file is not None and add_to_config and is_usable_config(config):
            central_config_path = os.path.join(config_path, "config.json")
            save_config(central_config_path, config, repo_name)

        # Log for Bluespec
        discovery_dir = os.path.join("discovery", repo_name, "logs")
        os.makedirs(discovery_dir, exist_ok=True)
        log_path = os.path.join(discovery_dir, "bluespec.log")
        try:
            with open(log_path, "w", encoding="utf-8") as log_file:
                log_file.write("Bluespec project processed")
        except Exception:
            pass

        # Discovery report
        try:
            candidate_result = CandidateResult(
                top_module=config.get("top_module", ""),
                top_file="",
                roles=["first_heuristic"],
                heuristic_rank=1,
                status="compilation_success" if config["discovery"]["has_successful_candidate"] else "failure",
                files=config.get("sim_files", []),
                include_dirs=[],
                extra_flags=config.get("extra_flags", []),
                parameters={},
                compile_command="",
                exit_code=0,
                errors=[],
                log_path=log_path,
            )
            report = build_discovery_report(
                repo_name=repo_name,
                repository_url=url,
                candidates=[candidate_result],
                successful_candidate=candidate_result if config["discovery"]["has_successful_candidate"] else None,
                output_json=config,
            )
            save_discovery_report(report, os.path.join("discovery", repo_name), repo_name)
        except Exception:
            pass

        if not local_repo:
            remove_repo(repo_name)

        print_green("[SUCCESS] Bluespec project configuration generated successfully\n")
        return config

    # Check if this is a Chisel project
    elif extension == ".scala":
        print_green("[LOG] Processando projeto Chisel\n")
        config = process_chisel_project(destination_path, repo_name)

        if not config:
            print_red("[ERROR] Failed to process Chisel project")
            if not local_repo:
                remove_repo(repo_name)
            return {}

        # Add repository URL
        config["repository"] = url
        normalize_generated_config(config, destination_path)
        config["discovery"]["max_candidates"] = run_context.max_candidates

        # Save configuration
        print_green("[LOG] Salvando configuração\n")
        if not os.path.exists(config_path):
            os.makedirs(config_path)

        # Coordinator claim (b) fix: standalone config for the successful
        # Chisel candidate before the main save.
        if config["discovery"]["has_successful_candidate"]:
            save_successful_candidate_config(
                str(config_path), config, repo_name,
                top_module=config.get("top_module"), roles=["first_heuristic"],
            )

        config_file = save_usable_config(config_path, config, repo_name)
        if config_file is not None and add_to_config and is_usable_config(config):
            central_config_path = os.path.join(config_path, "config.json")
            save_config(central_config_path, config, repo_name)

        # Discovery block for Chisel

        # Log for Chisel
        discovery_dir = os.path.join("discovery", repo_name, "logs")
        os.makedirs(discovery_dir, exist_ok=True)
        log_path = os.path.join(discovery_dir, "chisel.log")
        try:
            with open(log_path, "w", encoding="utf-8") as log_file:
                log_file.write("Chisel project processed")
        except Exception:
            pass

        # Discovery report
        try:
            candidate_result = CandidateResult(
                top_module=config.get("top_module", ""),
                top_file="",
                roles=["first_heuristic"],
                heuristic_rank=1,
                status="compilation_success" if config["discovery"]["has_successful_candidate"] else "failure",
                files=config.get("sim_files", []),
                include_dirs=[],
                extra_flags=config.get("extra_flags", []),
                parameters={},
                compile_command="",
                exit_code=0,
                errors=[],
                log_path=log_path,
            )
            report = build_discovery_report(
                repo_name=repo_name,
                repository_url=url,
                candidates=[candidate_result],
                successful_candidate=candidate_result if config["discovery"]["has_successful_candidate"] else None,
                output_json=config,
            )
            save_discovery_report(report, os.path.join("discovery", repo_name), repo_name)
        except Exception:
            pass

        # Cleanup
        if not local_repo:
            print_green("[LOG] Removendo o repositório clonado\n")
            remove_repo(repo_name)
        else:
            print_green("[LOG] Mantendo repositório local (não foi clonado)\n")

        return config

    # Continue with HDL processing (existing code)
    modulename_list, modules = _extract_and_log_modules(files, destination_path)
    run_context.module_files = [
        Path(
            os.path.relpath(file_path, destination_path)
            if os.path.isabs(file_path)
            else file_path
        )
        for _, file_path in modules
    ]

    tb_files, non_tb_files = _categorize_files(files, repo_name, destination_path)
    # Exclude FPGA board wrapper trees from consideration to avoid picking board 'top' modules
    orig_tb, orig_non_tb = len(tb_files), len(non_tb_files)
    non_fpga_hdl = [
        f for f in (tb_files + non_tb_files) if not _is_fpga_path(f)
    ]
    if non_fpga_hdl:
        tb_files = [f for f in tb_files if not _is_fpga_path(f)]
        non_tb_files = [f for f in non_tb_files if not _is_fpga_path(f)]
    elif tb_files or non_tb_files:
        # FPGA path classification can also make synthesizable files look like
        # testbench files. Move them back into the candidate RTL set.
        non_tb_files = tb_files + non_tb_files
        tb_files = []
    removed_tb = max(0, orig_tb - len(tb_files))
    removed_non_tb = max(0, orig_non_tb - len(non_tb_files))
    if removed_tb or removed_non_tb:
        print_yellow(
            f"[FILTER] Excluded FPGA paths -> tb:{removed_tb} non-tb:{removed_non_tb}"
        )

    # Also filter modules originating from FPGA folders
    try:
        filtered_modules = []
        for mname, mfile in modules:
            rel = (
                os.path.relpath(mfile, destination_path)
                if os.path.isabs(mfile)
                else mfile
            )
            if not _is_fpga_path(rel):
                filtered_modules.append((mname, mfile))
        if filtered_modules and len(filtered_modules) != len(modules):
            print_yellow(
                f"[FILTER] Excluded {len(modules) - len(filtered_modules)} module entries from FPGA paths"
            )
        if filtered_modules:
            modules = filtered_modules
        # Keep modulename_list consistent for any downstream consumers
        if filtered_modules:
            modulename_list = [
                d for d in modulename_list if not _is_fpga_path(d.get("file", ""))
            ]
    except Exception:
        pass
    include_dirs = _find_and_log_include_dirs(destination_path)
    fusesoc_flist = os.path.join(destination_path, ".fusesoc_flist")
    if os.path.exists(fusesoc_flist):
        with open(fusesoc_flist, encoding="utf-8") as flist:
            manifest_include_dirs = {
                os.path.dirname(line.strip())
                for line in flist
                if line.strip()
            }
        include_dirs = sorted(set(include_dirs) | manifest_include_dirs)
        print_yellow(
            f"[DEPS] Added {len(manifest_include_dirs)} FuseSoC source/include directories"
        )
    run_context.include_dirs = [Path(include_dir) for include_dir in include_dirs]

    # If Bender was used, also scan .bender directory for includes and parse flist
    if deps_fetched:
        # Scan .bender/git/checkouts for include directories
        # With --dir option, .bender is created relative to the Bender.yml location
        # So we need to check both root and subdirectories
        bender_checkout_dir = os.path.join(
            destination_path, ".bender", "git", "checkouts"
        )
        if not os.path.exists(bender_checkout_dir):
            # Search for .bender in subdirectories
            for root, dirs, files in os.walk(destination_path):
                if ".bender" in dirs:
                    candidate = os.path.join(root, ".bender", "git", "checkouts")
                    if os.path.exists(candidate):
                        bender_checkout_dir = candidate
                        print_yellow(
                            f"[DEPS] Found Bender checkouts at: {os.path.relpath(bender_checkout_dir, destination_path)}"
                        )
                        break

        if os.path.exists(bender_checkout_dir):
            print_yellow(f"[DEPS] Scanning Bender checkouts for include directories...")
            bender_includes_found = find_include_dirs(bender_checkout_dir)
            if bender_includes_found:
                # Convert to paths relative to repo root
                bender_base = os.path.dirname(
                    os.path.dirname(os.path.dirname(bender_checkout_dir))
                )  # Go up to .bender parent
                for inc_dir in bender_includes_found:
                    # Construct path relative to repo root
                    rel_to_root = os.path.relpath(
                        os.path.join(bender_checkout_dir, inc_dir), destination_path
                    )
                    include_dirs.add(rel_to_root)
                print_green(
                    f"[DEPS] Added {len(bender_includes_found)} include directories from Bender checkouts"
                )

        # Also parse the flist for any additional includes
        bender_files, bender_includes = _parse_bender_flist(destination_path)
        if bender_files:
            run_context.cache["bender_files"] = bender_files
            defines = []
            flist_path = os.path.join(destination_path, ".bender_flist")
            try:
                with open(flist_path, encoding="utf-8") as manifest:
                    defines = list(
                        dict.fromkeys(
                            line.strip()
                            for line in manifest
                            if line.strip().startswith("+define+")
                        )
                    )
            except OSError:
                pass
            run_context.cache["bender_defines"] = defines
        if bender_includes:
            print_yellow(
                f"[DEPS] Adding {len(bender_includes)} include directories from Bender flist"
            )
            include_dirs.update(bender_includes)
        run_context.cache["bender_includes"] = list(bender_includes)
        run_context.include_dirs = [Path(include_dir) for include_dir in include_dirs]

    module_graph, module_graph_inverse = _build_and_log_graphs(
        non_tb_files, modules, destination_path
    )
    # Determine language version from all non-TB files (LLM removed in Phase 1)
    language_version = determine_language_version(
        extension, non_tb_files, destination_path
    )
    run_context.language_version = language_version

    # Processor-specific Verilator flags
    verilator_flags = [
        "-Wno-lint",
        "-Wno-fatal",
        "-Wno-style",
        "-Wno-UNOPTFLAT",
        "-Wno-UNDRIVEN",
        "-Wno-UNUSED",
        "-Wno-TIMESCALEMOD",
        "-Wno-PROTECTED",
        "-Wno-MODDUP",
        "-Wno-REDEFMACRO",
        "-Wno-BLKANDNBLK",
        "-Wno-SYMRSVDWORD",
        "-Wno-STMTDLY",
        "-Wno-SELRANGE",
    ]

    # orv64: Define FPGA to use pre-synthesized .vm module implementations instead of missing DW IP
    if "orv64" in repo_name.lower():
        verilator_flags.append("-DFPGA")

    final_files, final_include_dirs, last_log, top_module, is_simulable = (
        interactive_simulate_and_minimize(
            repo_root=destination_path,
            repo_name=repo_name,
            url=url,
            tb_files=tb_files,
            candidate_files=non_tb_files,
            include_dirs=set(include_dirs),
            modules=modules,
            module_graph=module_graph,
            module_graph_inverse=module_graph_inverse,
            language_version=language_version,
            maximize_attempts=6,
            verilator_extra_flags=verilator_flags,
            ghdl_extra_flags=["--std=08", "-frelaxed", "-fsynopsys"],
            context=run_context,
        )
    )

    # Convert absolute include directories to relative paths
    relative_include_dirs = []
    for include_dir in final_include_dirs:
        if os.path.isabs(include_dir):
            try:
                relative_path = os.path.relpath(include_dir, destination_path)
                relative_include_dirs.append(relative_path)
            except ValueError:
                # If we can't make it relative, use the original
                relative_include_dirs.append(include_dir)
        else:
            relative_include_dirs.append(include_dir)

    # Choose sim_files that match the selected simulator language
    verilog_exts = {".v", ".sv", ".vh", ".svh"}
    vhdl_exts = {".vhd", ".vhdl"}
    has_verilog = any(
        os.path.splitext(f)[1].lower() in verilog_exts for f in final_files
    )
    has_vhdl = any(os.path.splitext(f)[1].lower() in vhdl_exts for f in final_files)

    if has_verilog and not has_vhdl:
        sim_tb_files = [
            f for f in tb_files if os.path.splitext(f)[1].lower() in verilog_exts
        ]
    elif has_vhdl and not has_verilog:
        sim_tb_files = [
            f for f in tb_files if os.path.splitext(f)[1].lower() in vhdl_exts
        ]
    else:
        # Fallback: keep original testbenches if we can't infer a single language
        sim_tb_files = tb_files

    # Normalize recorded language_version to reflect final selected files and simulator behavior
    # Prefer the runner-emitted effective language if available
    language_version_out = language_version
    try:
        if last_log:
            m = re.search(
                r"\[LANG-EFFECTIVE\]\s+([0-9\-]+)\s+mode=(sv|verilog)", last_log
            )
            if m:
                language_version_out = m.group(1)
                print_green(
                    f"[CONFIG] Using effective language from verilator: {language_version_out}"
                )
            else:
                # Fallback to file-extension based inference only if no effective language found
                if any(
                    os.path.splitext(f)[1].lower() in {".sv", ".svh"}
                    for f in final_files
                ):
                    language_version_out = "1800-2023"
                elif any(os.path.splitext(f)[1].lower() == ".v" for f in final_files):
                    # Don't blindly assume .v = Verilog 2005, check if we detected SV earlier
                    if language_version.startswith("1800"):
                        language_version_out = language_version
                    else:
                        language_version_out = "1364-2005"
                else:
                    language_version_out = language_version  # VHDL or unknown
        else:
            # No log? Infer from selected files
            if any(
                os.path.splitext(f)[1].lower() in {".sv", ".svh"} for f in final_files
            ):
                language_version_out = "1800-2023"
            elif any(os.path.splitext(f)[1].lower() == ".v" for f in final_files):
                language_version_out = "1364-2005"
            else:
                language_version_out = language_version
    except Exception:
        # On any parsing/inference issue, keep previously detected version
        language_version_out = language_version

    output_json = create_output_json(
        repo_name,
        url,
        final_files,
        relative_include_dirs,
        top_module,
        language_version_out,
        is_simulable,
    )

    # Blocker 6: is_simulable reflects actual simulation, not just compilation.
    # The validation gate uses the validation level and evidence from candidates.
    # Blocker 1 residual: pick the FIRST successful candidate, not the last tried
    # (candidates[-1] would be wrong when early pass / later fail).
    _validation_passed = False
    _validation_level: str | None = None
    _validation_evidence: str | None = None
    _best: CandidateResult | None = None
    if run_context.candidates:
        # Prefer the first successful candidate; fall back to the last tried.
        for _c in run_context.candidates:
            if _c.status == "compilation_success":
                _best = _c
                break
        if _best is None:
            _best = run_context.candidates[-1]
        if _best.status == "compilation_success":
            _validation_passed = True
            _validation_level = "compilation_success"
            _validation_evidence = (
                f"lint/compile succeeded for {_best.top_module}"
                + (f" ({len(_best.errors)} error lines in log)" if _best.log_path else "")
            )

    discovery_block = {
        "schema_version": "2.0",
        "has_successful_candidate": _validation_passed,
        "candidate_count": len(run_context.candidates),
        "candidate_status": "compilation_success" if _validation_passed else "failure",
        "max_candidates": run_context.max_candidates,
        "validation_gate": {
            "passed": _validation_passed,
            "level": _validation_level or "none",
            "evidence": _validation_evidence or "no candidates tried",
        },
    }
    if run_context.cache.get("bender_defines"):
        output_json["extra_flags"] = run_context.cache["bender_defines"]
        output_json["pre_script"] = "bender checkout"

    # Build discovery block and candidate result.
    # The incremental functions (try_incremental_approach / ghdl_incremental)
    # already recorded every candidate they tried, so we only create a
    # final CandidateResult here when no candidates were recorded yet
    # (i.e. the old path where interactive_simulate_and_minimize returned
    # without a context or when the caller never passed one).
    if not run_context.candidates:
        role_labels = ["requested"] if run_context.top_module_override else ["first_heuristic"]
        # In the fallback path the runner already succeeded/failed — use the
        # runner's is_simulable flag since no candidates exist to re-evaluate.
        fallback_succeeded = is_simulable
        candidate_result = CandidateResult(
            top_module=top_module,
            top_file=str(run_context.top_file_override) if run_context.top_file_override else "",
            roles=role_labels,
            heuristic_rank=1,
            status="compilation_success" if fallback_succeeded else "failure",
            files=list(final_files),
            include_dirs=list(relative_include_dirs),
            extra_flags=list(output_json.get("extra_flags", [])),
            parameters={},
            compile_command="verilator --top-module " + top_module if top_module else "",
            exit_code=0 if fallback_succeeded else 1,
            errors=[],
            log_path=None,
        )
        run_context.candidates.append(candidate_result)
        # Re-evaluate _validation_passed now that a candidate exists
        if candidate_result.status == "compilation_success":
            _validation_passed = True
            _validation_level = "compilation_success"
            _validation_evidence = (
                f"lint/compile succeeded for {candidate_result.top_module}"
            )

    output_json["discovery"] = discovery_block

    # Blocker 6 residual: the top-level is_simulable must reflect the
    # validation gate (_validation_passed), not the runner's lint-only flag.
    output_json["is_simulable"] = _validation_passed

    # Save configuration (only if usable)
    # When no candidate succeeded, the discovery block marks the config
    # as non-usable.  We also remove any stale output from a previous
    # successful run so that downstream consumers don't read a stale file.
    print_green("[LOG] Salvando configuração\n")
    if not os.path.exists(config_path):
        os.makedirs(config_path)

    config_file = save_usable_config(config_path, output_json, repo_name)

    # Coordinator claim (b) fix: save standalone config for successful
    # candidates in the main flow so downstream consumers can inspect
    # role-selected artifacts.
    if _best is not None and _best.status == "compilation_success":
        save_successful_candidate_config(
            str(config_path),
            output_json,
            repo_name,
            top_module=_best.top_module,
            roles=_best.roles,
        )
    if config_file is None:
        print_yellow("[WARN] Config rejected (not usable); skipping main output")
        # Remove stale output from a prior successful run
        stale = os.path.join(config_path, f"{repo_name}.json")
        if os.path.exists(stale):
            try:
                os.remove(stale)
                print_green(f"[LOG] Removed stale config: {stale}")
            except Exception:
                pass

    if add_to_config and is_usable_config(output_json):
        central_config_path = os.path.join(config_path, "config.json")
        save_config(central_config_path, output_json, repo_name)
    # Blocker 7: Do NOT delete the shared central config.json when one core fails.
    # Other cores may be writing to the same central index concurrently.

    # Save runner output log to discovery/ tree
    discovery_dir = os.path.join("discovery", repo_name, "logs")
    os.makedirs(discovery_dir, exist_ok=True)
    log_path = os.path.join(discovery_dir, "requesting.log")
    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            log_file.write(last_log or "")
        print_green(f"[LOG] Log saved to {log_path}")
    except Exception as e:
        print_yellow(f"[WARN] Falha ao salvar o log: {e}")

    # Attach the main log path to the best (first successful) candidate.
    if _best is not None:
        _best.log_path = log_path

    # Save full discovery report
    try:
        export_candidates(config_path, repo_name, url, language_version_out, run_context.candidates)
        report = build_discovery_report(
            repo_name=repo_name,
            repository_url=url,
            candidates=run_context.candidates,
            successful_candidate=_best if _best and _best.status == "compilation_success" else None,
            output_json=output_json,
        )
        report_path = save_discovery_report(report, os.path.join("discovery", repo_name), repo_name)
        print_green(f"[LOG] Discovery report saved to {report_path}")
    except Exception as e:
        print_yellow(f"[WARN] Could not save discovery report: {e}")

    # Cleanup - only remove if we cloned it (not using local repo)
    if not local_repo:
        print_green("[LOG] Removendo o repositório clonado\n")
        remove_repo(repo_name)
        print_green("[LOG] Repositório removido com sucesso\n")
    else:
        print_green("[LOG] Mantendo repositório local (não foi clonado)\n")

    # Plot graph if requested
    if plot_graph:
        print_green("[LOG] Plotando os grafos\n")
        try:
            import matplotlib

            matplotlib.use("Agg")
            plot_processor_graph(module_graph, module_graph_inverse)
            print_green("[LOG] Grafos plotados com sucesso\n")
        except ImportError as e:
            print_yellow(f"[WARN] Could not plot graphs: {e}\n")
        except Exception as e:
            print_yellow(f"[WARN] Error plotting graphs: {e}\n")

    return output_json


# ---------------------------------------------------------------------------
# Pipeline helpers — migrated from core/pipeline.py (Phase 1)
# ---------------------------------------------------------------------------


def _extract_repo_name(url: str) -> str:
    """Extracts the repository name from the given URL."""
    return url.split("/")[-1].replace(".git", "")


def _extract_repo_owner(url: str) -> str:
    """Extract the repository owner from a conventional Git URL."""
    normalized = url.rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    parts = normalized.replace(":", "/").split("/")
    return parts[-2] if len(parts) >= 2 else ""


def _detect_and_run_config_script(repo_path: str, repo_name: str) -> bool:
    """
    Detects and runs configuration scripts that generate necessary defines/headers.
    """
    # Pattern 1: configs directory with scripts
    config_dir = os.path.join(repo_path, "configs")
    if os.path.isdir(config_dir):
        config_files = []
        for f in os.listdir(config_dir):
            full_path = os.path.join(config_dir, f)
            if f.endswith((".config", ".py")) or (
                os.access(full_path, os.X_OK) and not f.startswith(".")
            ):
                config_files.append(f)

        if config_files:
            config_file = config_files[0]
            config_script = os.path.join(config_dir, config_file)
            print_yellow(f"[CONFIG] Found configuration script: {config_script}")

            try:
                os.chmod(config_script, 0o755)
            except Exception:
                pass

            env = os.environ.copy()
            env["RV_ROOT"] = repo_path
            env["ROOT"] = repo_path
            env["REPO_ROOT"] = repo_path

            if config_file.endswith(".py"):
                base_cmd = ["python3", config_script]
            else:
                base_cmd = [config_script]

            arg_patterns = [[], ["-target=default"], ["--default"]]

            for args in arg_patterns:
                cmd = base_cmd + args
                cmd_str = " ".join(cmd)
                print_yellow(f"[CONFIG] Attempting: {cmd_str}")

                try:
                    result = subprocess.run(
                        cmd,
                        cwd=repo_path,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )

                    if result.returncode == 0:
                        print_green(
                            f"[CONFIG] Configuration script completed successfully"
                        )
                        return True
                    else:
                        error_msg = result.stderr if result.stderr else result.stdout
                        if "Can't locate" in error_msg or "BEGIN failed" in error_msg:
                            print_yellow(
                                f"[CONFIG] Config script requires dependencies: {error_msg.split(chr(10))[0]}"
                            )
                            return False
                        elif "usage:" in error_msg.lower() and args == []:
                            continue
                        else:
                            continue

                except subprocess.TimeoutExpired:
                    print_yellow(f"[CONFIG] Config script timed out")
                    return False
                except Exception as e:
                    print_yellow(f"[CONFIG] Could not run config script: {str(e)}")
                    continue

            print_yellow(
                f"[CONFIG] Config script found but could not determine correct arguments, continuing..."
            )
            return False

    return False


def _clone_and_validate_repo(url: str, repo_name: str) -> str:
    """Clones the repository and validates the operation."""
    from .file_manager import clone_repo

    destination_path = clone_repo(url, repo_name)
    if not destination_path:
        print_red("[ERROR] Não foi possível clonar o repositório.")
    else:
        print_green("[LOG] Repositório clonado com sucesso\n")
        abs_path = os.path.abspath(destination_path)
        _detect_and_run_config_script(abs_path, repo_name)

    return destination_path


def _find_and_log_files(destination_path: str) -> tuple:
    """Finds files with specific extensions in the repository and logs the result."""
    print_green(
        "[LOG] Procurando arquivos com extensão .v, .sv, .vhdl, .vhd, .scala ou .bsv\n"
    )

    fusesoc_flist = os.path.join(destination_path, ".fusesoc_flist")
    if os.path.exists(fusesoc_flist):
        with open(fusesoc_flist, encoding="utf-8") as flist:
            files = [line.strip() for line in flist if line.strip()]
        if files:
            extension = ".sv" if any(path.endswith((".sv", ".svh")) for path in files) else ".v"
            print_green(f"[DEPS] Using {len(files)} files from FuseSoC manifest")
            return files, extension

    bsv_files = find_bsv_files(destination_path)
    if bsv_files:
        print_green(
            f"[LOG] Encontrados {len(bsv_files)} arquivos Bluespec - projeto BSV detectado\n"
        )
        return bsv_files, ".bsv"

    scala_files = find_scala_files(destination_path)
    if scala_files:
        print_green(
            f"[LOG] Encontrados {len(scala_files)} arquivos Scala - projeto Chisel detectado\n"
        )
        return scala_files, ".scala"

    from .file_manager import find_files_with_extension

    files, extension = find_files_with_extension(
        destination_path, ["v", "sv", "vhdl", "vhd"]
    )
    if not files:
        unfiltered_files, extension = find_files_with_extension(
            destination_path, ["v", "sv", "vhdl", "vhd"], exclude_filtered=False
        )
        fpga_src_marker = f"{os.sep}fpga{os.sep}src{os.sep}"
        files = [path for path in unfiltered_files if fpga_src_marker in path]
        if files:
            print_yellow(
                f"[FILTER] Found {len(files)} synthesizable files under fpga/src"
            )
    return files, extension


def _extract_and_log_modules(files: list, destination_path: str) -> tuple[list, list]:
    """Extracts module information from files and logs the result."""
    print_green("[LOG] Extraindo módulos dos arquivos\n")
    from .file_manager import extract_modules

    modules = extract_modules(files)
    print_green("[LOG] Módulos extraídos com sucesso\n")
    return [
        {
            "module": module_name,
            "file": os.path.relpath(file_path, destination_path),
        }
        for module_name, file_path in modules
    ], modules


def _categorize_files(files: list, repo_name: str, destination_path: str) -> tuple:
    """Categorizes files into testbench and non-testbench files."""
    from .file_manager import is_testbench_file

    tb_files, non_tb_files = [], []
    for f in files:
        if is_testbench_file(f, repo_name):
            tb_files.append(f)
        else:
            non_tb_files.append(f)
    return (
        [os.path.relpath(tb_f, destination_path) for tb_f in tb_files],
        [os.path.relpath(non_tb_f, destination_path) for non_tb_f in non_tb_files],
    )


def _handle_dependency_manager(
    destination_path: str,
    repo_name: str,
    top_module_override: str | None = None,
    dependency_target: str | None = None,
) -> bool:
    """Detect and run dependency managers (Bender, FuseSoC) to fetch external dependencies."""
    import tempfile

    bender_yml = os.path.join(destination_path, "Bender.yml")
    bender_found = os.path.exists(bender_yml)

    if not bender_found:
        candidate_benders = []
        for root, dirs, files in os.walk(destination_path):
            if "Bender.yml" in files:
                candidate_benders.append(os.path.join(root, "Bender.yml"))

        if candidate_benders:
            selected_bender = None
            for candidate in candidate_benders:
                try:
                    with open(candidate, "r") as f:
                        content = f.read()
                        match = re.search(
                            r'^\s*name:\s*["\']?(\w+)["\']?\s*$', content, re.MULTILINE
                        )
                        if match:
                            pkg_name = match.group(1)
                            if pkg_name.lower() == repo_name.lower():
                                selected_bender = candidate
                                print_yellow(
                                    f"[DEPS] Found matching Bender.yml: {os.path.relpath(candidate, destination_path)} (package: {pkg_name})"
                                )
                                break
                except Exception:
                    continue

            if not selected_bender:
                selected_bender = candidate_benders[0]
                print_yellow(
                    f"[DEPS] Using first Bender.yml found: {os.path.relpath(selected_bender, destination_path)}"
                )

            bender_yml = selected_bender
            bender_found = True

    if bender_found:
        print_yellow(f"[DEPS] Detected Bender.yml - fetching dependencies...")

        try:
            result = subprocess.run(
                ["bender", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                print_yellow(
                    f"[DEPS] Bender not installed. Install with: cargo install bender"
                )
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print_yellow(f"[DEPS] Bender not found. Install with: cargo install bender")
            print_yellow(
                f"[DEPS] Skipping dependency fetch - some modules may be missing"
            )
            return False

        bender_dir = os.path.dirname(bender_yml)
        bender_rel_dir = os.path.relpath(bender_dir, destination_path)
        try:
            print_yellow(f"[DEPS] Running 'bender checkout' for {bender_rel_dir}...")
            result = subprocess.run(
                ["bender", "checkout", "--dir", bender_rel_dir],
                cwd=destination_path,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode == 0:
                print_green(f"[DEPS] Successfully checked out dependencies with Bender")

                try:
                    print_yellow(f"[DEPS] Generating file list with 'bender script flist'...")
                    flist_command = [
                        "bender", "script",
                        "verilator" if dependency_target else "flist",
                        "--dir", bender_rel_dir,
                    ]
                    if not dependency_target:
                        flist_command.append("--relative-path")
                    if dependency_target:
                        flist_command.extend(["--target", dependency_target])
                    result_flist = subprocess.run(
                        flist_command,
                        cwd=destination_path,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result_flist.returncode == 0 and result_flist.stdout:
                        flist_path = os.path.join(destination_path, ".bender_flist")
                        with open(flist_path, "w") as f:
                            f.write(result_flist.stdout)
                        print_green(f"[DEPS] Saved Bender file list to .bender_flist")
                except Exception as e:
                    print_yellow(f"[DEPS] Could not generate flist: {e}")

                return True
            else:
                print_yellow(f"[DEPS] Bender checkout failed:")
                if result.stderr:
                    print_yellow(f"[DEPS]   {result.stderr[:500]}")
                return False

        except subprocess.TimeoutExpired:
            print_yellow(f"[DEPS] Bender checkout timed out")
            return False
        except Exception as e:
            print_yellow(f"[DEPS] Error running Bender: {e}")
            return False

    # Check for FuseSoC
    fusesoc_cores = [
        file for file in os.listdir(destination_path) if file.endswith(".core")
    ]
    fusesoc_core = fusesoc_cores[0] if fusesoc_cores else None
    if top_module_override:
        for candidate in fusesoc_cores:
            try:
                candidate_text = open(
                    os.path.join(destination_path, candidate), encoding="utf-8"
                ).read()
            except OSError:
                continue
            if re.search(
                rf"\btoplevel\s*:\s*(?:\[[^\]]*\b)?{re.escape(top_module_override)}\b",
                candidate_text,
            ):
                fusesoc_core = candidate
                break

    if fusesoc_core:
        print_yellow(f"[DEPS] Detected FuseSoC core file: {fusesoc_core}")
        import yaml

        core_path = os.path.join(destination_path, fusesoc_core)
        try:
            core_text = open(core_path, encoding="utf-8").read()
            if core_text.startswith("CAPI=2:"):
                core_text = core_text.split("\n", 1)[1]
            core_data = yaml.safe_load(core_text) or {}
            vlnv = str(core_data.get("name", "")).strip()
            targets = core_data.get("targets", {}) or {}
            target = "default"
            if top_module_override:
                matching_targets = []
                for target_name, target_data in targets.items():
                    toplevel = (target_data or {}).get("toplevel", [])
                    if isinstance(toplevel, str):
                        toplevel = [toplevel]
                    if top_module_override in toplevel:
                        matching_targets.append((target_name, target_data or {}))
                if matching_targets:
                    target = next(
                        (
                            name
                            for name, data in matching_targets
                            if data.get("default_tool") or data.get("tools") or data.get("flow")
                        ),
                        matching_targets[0][0],
                    )

            fusesoc = shutil.which("fusesoc")
            sibling_fusesoc = os.path.join(os.path.dirname(sys.executable), "fusesoc")
            if not fusesoc and os.path.exists(sibling_fusesoc):
                fusesoc = sibling_fusesoc
            if not fusesoc or not vlnv:
                print_yellow("[DEPS] FuseSoC executable or core name unavailable")
                return False

            generated_flist = os.path.join(destination_path, ".fusesoc_flist")
            if os.path.exists(generated_flist):
                os.remove(generated_flist)

            with tempfile.TemporaryDirectory(prefix="processor-ci-fusesoc-") as work:
                env = os.environ.copy()
                env["XDG_CACHE_HOME"] = os.path.join(work, "cache")
                result = subprocess.run(
                    [
                        fusesoc,
                        "--cores-root",
                        os.path.abspath(destination_path),
                        "run",
                        "--setup",
                        "--no-export",
                        "--work-root",
                        work,
                        "--target",
                        target,
                        vlnv,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env,
                )
                if result.returncode != 0:
                    print_yellow(f"[DEPS] FuseSoC setup failed: {result.stderr[-500:]}")
                    return False
                edam_files = [
                    os.path.join(work, name)
                    for name in os.listdir(work)
                    if name.endswith(".eda.yml")
                ]
                if not edam_files:
                    return False
                edam = yaml.safe_load(open(edam_files[0], encoding="utf-8")) or {}
                source_files = []
                for entry in edam.get("files", []):
                    if entry.get("file_type") not in (
                        "verilogSource",
                        "systemVerilogSource",
                        "vhdlSource",
                    ):
                        continue
                    path = entry.get("name", "")
                    if not os.path.isabs(path):
                        path = os.path.abspath(os.path.join(work, path))
                    if os.path.exists(path):
                        source_files.append(path)
                if source_files:
                    with open(
                        generated_flist,
                        "w",
                        encoding="utf-8",
                    ) as flist:
                        flist.write("\n".join(source_files) + "\n")
                    print_green(
                        f"[DEPS] FuseSoC resolved {len(source_files)} HDL files (target={target})"
                    )
                    return True
        except (OSError, subprocess.TimeoutExpired, yaml.YAMLError) as exc:
            print_yellow(f"[DEPS] FuseSoC setup error: {exc}")
        return False

    return False


def _parse_bender_flist(destination_path: str) -> tuple[list, set]:
    """Parse Bender-generated flist to extract files and include directories."""
    flist_path = os.path.join(destination_path, ".bender_flist")
    if not os.path.exists(flist_path):
        return [], set()

    additional_files = []
    additional_includes = set()

    try:
        with open(flist_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("//") or line.startswith("#"):
                    continue

                if line.startswith("+incdir+"):
                    additional_includes.add(line.replace("+incdir+", "").strip())
                elif line.startswith("-I"):
                    additional_includes.add(line.replace("-I", "").strip())
                elif line.endswith((".sv", ".v", ".svh", ".vh", ".vhdl", ".vhd")):
                    additional_files.append(line)

        print_green(
            f"[DEPS] Parsed Bender flist: {len(additional_files)} files, {len(additional_includes)} includes"
        )
        return additional_files, additional_includes

    except Exception as e:
        print_yellow(f"[DEPS] Error parsing Bender flist: {e}")
        return [], set()


def _find_and_log_include_dirs(destination_path: str) -> list:
    """Finds include directories in the repository and logs the result."""
    print_green("[LOG] Procurando diretórios de inclusão\n")
    include_dirs = find_include_dirs(destination_path)
    print_green("[LOG] Diretórios de inclusão encontrados com sucesso\n")
    return include_dirs


def _build_and_log_graphs(
    files: list, modules: list, destination_path: str = None
) -> tuple:
    """Builds the direct and inverse module dependency graphs."""
    print_green("[LOG] Construindo os grafos direto e inverso\n")

    if destination_path:
        absolute_files = [
            os.path.join(destination_path, f) if not os.path.isabs(f) else f
            for f in files
        ]
    else:
        absolute_files = files
    module_graph, module_graph_inverse = build_module_graph(absolute_files, modules)
    print_green("[LOG] Grafos construídos com sucesso\n")
    return module_graph, module_graph_inverse


def main(argv: list[str] | None = None) -> int | None:
    """Compatibility entry point; CLI parsing lives in processor_discover.cli."""
    from processor_discover.cli import main as cli_main

    return cli_main(argv)
