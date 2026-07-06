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
- `-n`, `--no-llama`: Skip OLLAMA processing for top module identification.
- `-m`, `--model`: OLLAMA model to use (default: 'qwen2.5:32b').
- `-l`, `--local-repo`: Path to local repository (skips cloning if provided).
- `-t`, `--top-module`: Force a specific top module (tried first, then fallback to heuristics).

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
import time
import json
import shutil
import subprocess
import re
from pathlib import Path
from typing import List
from .config import save_config
from .file_manager import (
    remove_repo,
    find_include_dirs,
)
from .graph import plot_processor_graph
from .output import create_output_json
from .pipeline import (
    build_and_log_graphs,
    categorize_files,
    clone_and_validate_repo,
    extract_and_log_modules,
    extract_repo_name,
    find_and_log_files,
    find_and_log_include_dirs,
    handle_dependency_manager,
    parse_bender_flist,
    process_files_with_llama,
)
from ..utils.log import print_green, print_red, print_yellow
from .api import RunContext
from ..utils.runtime import build_run_context
from .top_selection import (
    _find_cpu_core_in_soc,
    _is_fpga_path,
    _is_functional_unit_name,
    _is_interface_module_name,
    _is_micro_stage_name,
    _is_peripheral_like_name,
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
) -> tuple:
    """
    Try the incremental bottom-up approach for Verilog/SystemVerilog files.

    Returns: (final_files, final_includes, last_log, selected_top, is_simulable)
    """
    print_green(f"[INCREMENTAL] Trying bottom-up incremental approach for {repo_name}")

    # Limit number of candidates to avoid excessive testing
    MAX_CANDIDATES_TO_TRY = 10
    if len(top_candidates) > MAX_CANDIDATES_TO_TRY:
        print_yellow(
            f"[INCREMENTAL] Limiting to top {MAX_CANDIDATES_TO_TRY} candidates (out of {len(top_candidates)})"
        )
        top_candidates = top_candidates[:MAX_CANDIDATES_TO_TRY]

    print_green(f"[INCREMENTAL] Candidates to try: {', '.join(top_candidates)}")

    # Build module->file map
    module_to_file = {}
    for mname, mfile in modules or []:
        module_to_file[mname] = mfile

    # Try each top candidate with incremental compilation
    for idx, top_module in enumerate(top_candidates, 1):
        print_green(
            f"[INCREMENTAL] === Candidate {idx}/{len(top_candidates)}: {top_module} ==="
        )
        if top_module not in module_to_file:
            print_yellow(f"[INCREMENTAL] Skipping {top_module} - no file mapping found")
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

        rc, log, final_files, final_includes = verilator_incremental(
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

        if rc == 0:
            print_green(f"[INCREMENTAL] ✓ Success with top module: {top_module}")
            return final_files, final_includes, log, top_module, True
        else:
            if (
                top_module_override
                and top_module == top_module_override
                and len(top_candidates) > 1
            ):
                print_yellow(
                    f"[INCREMENTAL] Override '{top_module_override}' failed, falling back to heuristic candidates..."
                )
            print_yellow(f"[INCREMENTAL] ✗ Failed with top module: {top_module}")

    # If all failed, return empty result
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

    # Proactively drop any FPGA-related files from candidates to avoid board wrappers influencing top detection
    candidate_files = [f for f in candidate_files if not _is_fpga_path(f)]
    tb_files = [f for f in tb_files if not _is_fpga_path(f)]

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

    # Build module->file map
    module_to_file = {}
    for mname, mfile in modules or []:
        module_to_file[mname] = mfile

    # Determine primary top candidate and refine if it looks peripheral-like (AXI/memory/fabric)
    primary_top = candidates[0] if candidates else None
    if top_module_override:
        if top_module_override in module_to_file:
            print_yellow(
                f"[TOP] Using user-specified top module override: {top_module_override}"
            )
            primary_top = top_module_override
            # Ensure the override is tried first, keep heuristics as fallback
            candidates = [top_module_override] + [
                c for c in candidates if c != top_module_override
            ]
        else:
            print_yellow(
                f"[TOP] Warning: top module override '{top_module_override}' not found in extracted modules; falling back to heuristics"
            )
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
        is_simulable, last_log, final_files, top_module = ghdl_incremental(
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
        is_simulable, last_log, final_files, top_module = ghdl_incremental(
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
    no_llama: bool = False,
    model: str = "qwen2.5:32b",
    local_repo: str = None,
    top_module_override: str | None = None,
    context: RunContext | None = None,
) -> dict:
    """
    Main function to generate a processor configuration.

    Args:
        url: Repository URL
        config_path: Path to save configuration
        plot_graph: Whether to plot dependency graphs
        add_to_config: Whether to add to central config
        no_llama: Skip OLLAMA processing
        model: OLLAMA model to use
        local_repo: Path to local repository (skips cloning if provided)
    """
    repo_name = extract_repo_name(url)
    run_context = context or build_run_context(
        repo_root=Path("."),
        repo_name=repo_name,
        repository_url=url,
        config_path=config_path,
        local_repo=local_repo,
        model=model,
        plot_graph=plot_graph,
        add_to_config=add_to_config,
        no_llama=no_llama,
        top_module_override=top_module_override,
    )
    if context is not None:
        run_context.repo_name = repo_name
        run_context.repository_url = url
        run_context.config_path = Path(config_path)
        run_context.local_repo = Path(local_repo) if local_repo is not None else None
        run_context.model = model
        run_context.plot_graph = plot_graph
        run_context.add_to_config = add_to_config
        run_context.top_module_override = (
            top_module_override or run_context.top_module_override
        )
        run_context.no_llama = no_llama if context is None else context.no_llama

    config_path = str(run_context.config_path or config_path)
    local_repo = (
        str(run_context.local_repo)
        if run_context.local_repo is not None
        else local_repo
    )
    model = run_context.model
    plot_graph = run_context.plot_graph
    add_to_config = run_context.add_to_config

    # Use local repo if provided, otherwise clone
    if local_repo and os.path.exists(local_repo):
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
        destination_path = clone_and_validate_repo(url, repo_name)
        if not destination_path:
            return {}

    # Handle dependency managers (Bender, FuseSoC, etc.)
    # This must be done BEFORE scanning for files since it fetches external dependencies
    deps_fetched = handle_dependency_manager(destination_path, repo_name)
    if deps_fetched:
        print_green(f"[DEPS] Dependencies fetched - rescanning files and includes...")

    files, extension = find_and_log_files(destination_path)
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

        # Save configuration
        print_green("[LOG] Salvando configuração\n")
        if not os.path.exists(config_path):
            os.makedirs(config_path)

        config_file = os.path.join(config_path, f"{repo_name}.json")
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)

        if add_to_config:
            central_config_path = os.path.join(config_path, "config.json")
            save_config(central_config_path, config, repo_name)

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

        # Save configuration
        print_green("[LOG] Salvando configuração\n")
        if not os.path.exists(config_path):
            os.makedirs(config_path)

        config_file = os.path.join(config_path, f"{repo_name}.json")
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)

        if add_to_config:
            central_config_path = os.path.join(config_path, "config.json")
            save_config(central_config_path, config, repo_name)

        # Cleanup
        if not local_repo:
            print_green("[LOG] Removendo o repositório clonado\n")
            remove_repo(repo_name)
        else:
            print_green("[LOG] Mantendo repositório local (não foi clonado)\n")

        return config

    # Continue with HDL processing (existing code)
    modulename_list, modules = extract_and_log_modules(files, destination_path)
    run_context.module_files = [
        Path(
            os.path.relpath(file_path, destination_path)
            if os.path.isabs(file_path)
            else file_path
        )
        for _, file_path in modules
    ]

    tb_files, non_tb_files = categorize_files(files, repo_name, destination_path)
    # Exclude FPGA board wrapper trees from consideration to avoid picking board 'top' modules
    orig_tb, orig_non_tb = len(tb_files), len(non_tb_files)
    tb_files = [f for f in tb_files if not _is_fpga_path(f)]
    non_tb_files = [f for f in non_tb_files if not _is_fpga_path(f)]
    removed_tb = orig_tb - len(tb_files)
    removed_non_tb = orig_non_tb - len(non_tb_files)
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
        if len(filtered_modules) != len(modules):
            print_yellow(
                f"[FILTER] Excluded {len(modules) - len(filtered_modules)} module entries from FPGA paths"
            )
        modules = filtered_modules
        # Keep modulename_list consistent for any downstream consumers
        modulename_list = [
            d for d in modulename_list if not _is_fpga_path(d.get("file", ""))
        ]
    except Exception:
        pass
    include_dirs = find_and_log_include_dirs(destination_path)
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
        bender_files, bender_includes = parse_bender_flist(destination_path)
        if bender_includes:
            print_yellow(
                f"[DEPS] Adding {len(bender_includes)} include directories from Bender flist"
            )
            include_dirs.update(bender_includes)
        run_context.include_dirs = [Path(include_dir) for include_dir in include_dirs]

    module_graph, module_graph_inverse = build_and_log_graphs(
        non_tb_files, modules, destination_path
    )
    filtered_files, top_module = process_files_with_llama(
        run_context.no_llama,
        non_tb_files,
        tb_files,
        modules,
        module_graph,
        repo_name,
        model,
    )
    language_version = determine_language_version(
        extension, filtered_files, destination_path
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
            candidate_files=filtered_files,
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

    # Save configuration
    print_green("[LOG] Salvando configuração\n")
    if not os.path.exists(config_path):
        os.makedirs(config_path)

    config_file = os.path.join(config_path, f"{repo_name}.json")
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(output_json, f, indent=4)

    if add_to_config:
        central_config_path = os.path.join(config_path, "config.json")
        save_config(central_config_path, output_json, repo_name)

    # Save runner output log (lint/analyze/minimize transcript)
    print_green("[LOG] Salvando o log em logs/\n")
    if not os.path.exists("logs"):
        os.makedirs("logs")
    try:
        ts = f"{time.time():.0f}"
        with open(f"logs/{repo_name}_{ts}.log", "w", encoding="utf-8") as log_file:
            # last_log may be large; write as plain text
            log_file.write(last_log or "")
    except Exception as e:
        print_yellow(f"[WARN] Falha ao salvar o log: {e}")

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


def main(argv: list[str] | None = None) -> int | None:
    """Compatibility entry point; CLI parsing lives in processor_discover.cli."""
    from processor_discover.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
