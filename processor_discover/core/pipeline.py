"""Pipeline helpers for processor repository discovery."""

import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

from .file_manager import (
    clone_repo,
    remove_repo,
    find_files_with_extension,
    extract_modules,
    is_testbench_file,
    find_include_dirs,
)
from .graph import build_module_graph
from .ollama import get_filtered_files_list, get_top_module
from ..lang.bluespec_manager import find_bsv_files
from ..lang.chisel_manager import find_scala_files
from ..utils.log import print_green, print_red, print_yellow


def extract_repo_name(url: str) -> str:
    """Extracts the repository name from the given URL."""
    return url.split("/")[-1].replace(".git", "")


def extract_repo_owner(url: str) -> str:
    """Extract the repository owner from a conventional Git URL."""
    normalized = url.rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    parts = normalized.replace(":", "/").split("/")
    return parts[-2] if len(parts) >= 2 else ""


def detect_and_run_config_script(repo_path: str, repo_name: str) -> bool:
    """
    Detects and runs configuration scripts that generate necessary defines/headers.

    Supports multiple patterns:
    - configs/*.config scripts (VeeR cores, etc.) - runs with no args or -target=default
    - configs/*.py scripts - runs with python3
    - configure scripts in root

    Returns:
        bool: True if a config script was found and run successfully
    """
    import subprocess

    # Pattern 1: configs directory with scripts
    config_dir = os.path.join(repo_path, "configs")
    if os.path.isdir(config_dir):
        # Find config scripts (.config, .py, executable files)
        config_files = []
        for f in os.listdir(config_dir):
            full_path = os.path.join(config_dir, f)
            if f.endswith((".config", ".py")) or (
                os.access(full_path, os.X_OK) and not f.startswith(".")
            ):
                config_files.append(f)

        if config_files:
            # Use the first config file found
            config_file = config_files[0]
            config_script = os.path.join(config_dir, config_file)
            print_yellow(f"[CONFIG] Found configuration script: {config_script}")

            # Make script executable
            try:
                os.chmod(config_script, 0o755)
            except Exception:
                pass

            # Set up environment variables
            env = os.environ.copy()
            env["RV_ROOT"] = repo_path  # VeeR-specific
            env["ROOT"] = repo_path
            env["REPO_ROOT"] = repo_path

            # Determine command based on file type
            if config_file.endswith(".py"):
                base_cmd = ["python3", config_script]
            else:
                base_cmd = [config_script]

            # Try different argument patterns
            arg_patterns = [
                [],  # No arguments (most generic)
                ["-target=default"],  # VeeR-style
                ["--default"],  # Common default flag
            ]

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
                            f"[CONFIG] ✓ Configuration script completed successfully"
                        )
                        return True
                    else:
                        error_msg = result.stderr if result.stderr else result.stdout
                        # Check for specific errors
                        if "Can't locate" in error_msg or "BEGIN failed" in error_msg:
                            print_yellow(
                                f"[CONFIG] ⚠ Config script requires dependencies: {error_msg.split(chr(10))[0]}"
                            )
                            return False
                        elif "usage:" in error_msg.lower() and args == []:
                            # Script requires arguments, try next pattern
                            continue
                        else:
                            # Other error, try next pattern
                            continue

                except subprocess.TimeoutExpired:
                    print_yellow(f"[CONFIG] Config script timed out")
                    return False
                except Exception as e:
                    print_yellow(f"[CONFIG] Could not run config script: {str(e)}")
                    continue

            # If all patterns failed but script exists, that's okay - continue anyway
            print_yellow(
                f"[CONFIG] Config script found but could not determine correct arguments, continuing..."
            )
            return False

    return False


def clone_and_validate_repo(url: str, repo_name: str) -> str:
    """Clones the repository and validates the operation."""
    destination_path = clone_repo(url, repo_name)
    if not destination_path:
        print_red("[ERROR] Não foi possível clonar o repositório.")
    else:
        print_green("[LOG] Repositório clonado com sucesso\n")

        # Convert to absolute path for config script
        abs_path = os.path.abspath(destination_path)

        # Try to detect and run config scripts
        detect_and_run_config_script(abs_path, repo_name)

    return destination_path


def find_and_log_files(destination_path: str) -> tuple:
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

    # First check for Bluespec files
    bsv_files = find_bsv_files(destination_path)
    if bsv_files:
        print_green(
            f"[LOG] Encontrados {len(bsv_files)} arquivos Bluespec - projeto BSV detectado\n"
        )
        return bsv_files, ".bsv"

    # Then check for Scala/Chisel files
    scala_files = find_scala_files(destination_path)
    if scala_files:
        print_green(
            f"[LOG] Encontrados {len(scala_files)} arquivos Scala - projeto Chisel detectado\n"
        )
        return scala_files, ".scala"

    # Otherwise, look for HDL files
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


def extract_and_log_modules(files: list, destination_path: str) -> tuple[list, list]:
    """Extracts module information from files and logs the result."""
    print_green("[LOG] Extraindo módulos dos arquivos\n")
    modules = extract_modules(files)
    print_green("[LOG] Módulos extraídos com sucesso\n")
    return [
        {
            "module": module_name,
            "file": os.path.relpath(file_path, destination_path),
        }
        for module_name, file_path in modules
    ], modules


def categorize_files(files: list, repo_name: str, destination_path: str) -> tuple:
    """Categorizes files into testbench and non-testbench files."""
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


def handle_dependency_manager(
    destination_path: str,
    repo_name: str,
    top_module_override: str | None = None,
    dependency_target: str | None = None,
) -> bool:
    """Detect and run dependency managers (Bender, FuseSoC) to fetch external dependencies.

    Args:
        destination_path: Path to the repository
        repo_name: Name of the repository

    Returns:
        bool: True if dependencies were successfully fetched, False otherwise
    """
    # Check for Bender (used by CVA6, PULP projects)
    # First check root, then check subdirectories (like hw/ip/*/Bender.yml)
    bender_yml = os.path.join(destination_path, "Bender.yml")
    bender_found = os.path.exists(bender_yml)

    if not bender_found:
        # Search for Bender.yml in subdirectories (e.g., Snitch: hw/ip/snitch/Bender.yml)
        # If multiple found, prefer the one whose package name matches repo_name
        candidate_benders = []
        for root, dirs, files in os.walk(destination_path):
            if "Bender.yml" in files:
                candidate_path = os.path.join(root, "Bender.yml")
                candidate_benders.append(candidate_path)

        if candidate_benders:
            # Try to find Bender.yml with matching package name
            selected_bender = None
            for candidate in candidate_benders:
                try:
                    with open(candidate, "r") as f:
                        content = f.read()
                        # Simple YAML parsing to find package name
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

            # If no match found, use the first one
            if not selected_bender:
                selected_bender = candidate_benders[0]
                print_yellow(
                    f"[DEPS] Using first Bender.yml found: {os.path.relpath(selected_bender, destination_path)}"
                )

            bender_yml = selected_bender
            bender_found = True

    if bender_found:
        print_yellow(f"[DEPS] Detected Bender.yml - fetching dependencies...")

        # Check if bender is installed
        try:
            result = subprocess.run(
                ["bender", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                print_yellow(
                    f"[DEPS] Bender not installed. Install with: cargo install bender"
                )
                print_yellow(
                    f"[DEPS] Skipping dependency fetch - some modules may be missing"
                )
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print_yellow(f"[DEPS] Bender not found. Install with: cargo install bender")
            print_yellow(
                f"[DEPS] Skipping dependency fetch - some modules may be missing"
            )
            return False

        # Run bender checkout to fetch and checkout dependencies
        # This creates local working copies in the .bender directory
        # Run from repo root but use --dir to point to the Bender.yml location
        bender_dir = os.path.dirname(bender_yml)
        bender_rel_dir = os.path.relpath(bender_dir, destination_path)
        try:
            print_yellow(f"[DEPS] Running 'bender checkout' for {bender_rel_dir}...")
            result = subprocess.run(
                ["bender", "checkout", "--dir", bender_rel_dir],
                cwd=destination_path,  # Run from repo root
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes timeout for fetching dependencies
            )

            if result.returncode == 0:
                print_green(
                    f"[DEPS] ✓ Successfully checked out dependencies with Bender"
                )

                # Generate file list with include directories
                try:
                    print_yellow(
                        f"[DEPS] Generating file list with 'bender script flist'..."
                    )
                    flist_command = [
                            "bender",
                            "script",
                            "verilator" if dependency_target else "flist",
                            "--dir",
                            bender_rel_dir,
                        ]
                    if not dependency_target:
                        flist_command.append("--relative-path")
                    if dependency_target:
                        flist_command.extend(["--target", dependency_target])
                    result_flist = subprocess.run(
                        flist_command,
                        cwd=destination_path,  # Run from repo root
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result_flist.returncode == 0 and result_flist.stdout:
                        # Save the flist to repo root for easier access
                        flist_path = os.path.join(destination_path, ".bender_flist")
                        with open(flist_path, "w") as f:
                            f.write(result_flist.stdout)
                        print_green(f"[DEPS] ✓ Saved Bender file list to .bender_flist")
                except Exception as e:
                    print_yellow(f"[DEPS] Could not generate flist: {e}")

                return True
            else:
                print_yellow(f"[DEPS] ✗ Bender checkout failed:")
                if result.stderr:
                    print_yellow(f"[DEPS]   {result.stderr[:500]}")
                return False

        except subprocess.TimeoutExpired:
            print_yellow(f"[DEPS] ✗ Bender checkout timed out")
            return False
        except Exception as e:
            print_yellow(f"[DEPS] ✗ Error running Bender: {e}")
            return False

    # Check for FuseSoC (used by some OpenHW projects)
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
                        f"[DEPS] ✓ FuseSoC resolved {len(source_files)} HDL files (target={target})"
                    )
                    return True
        except (OSError, subprocess.TimeoutExpired, yaml.YAMLError) as exc:
            print_yellow(f"[DEPS] FuseSoC setup error: {exc}")
        return False

    # No dependency manager detected
    return False


def parse_bender_flist(destination_path: str) -> tuple[list, set]:
    """Parse Bender-generated flist to extract files and include directories.

    Args:
        destination_path: Path to the repository

    Returns:
        tuple: (list of additional files, set of additional include dirs)
    """
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

                # Include directory directive
                if line.startswith("+incdir+"):
                    inc_dir = line.replace("+incdir+", "").strip()
                    additional_includes.add(inc_dir)
                elif line.startswith("-I"):
                    inc_dir = line.replace("-I", "").strip()
                    additional_includes.add(inc_dir)
                # File path
                elif line.endswith((".sv", ".v", ".svh", ".vh", ".vhdl", ".vhd")):
                    additional_files.append(line)

        print_green(
            f"[DEPS] Parsed Bender flist: {len(additional_files)} files, {len(additional_includes)} includes"
        )
        return additional_files, additional_includes

    except Exception as e:
        print_yellow(f"[DEPS] Error parsing Bender flist: {e}")
        return [], set()


def find_and_log_include_dirs(destination_path: str) -> list:
    """Finds include directories in the repository and logs the result."""
    print_green("[LOG] Procurando diretórios de inclusão\n")
    include_dirs = find_include_dirs(destination_path)
    print_green("[LOG] Diretórios de inclusão encontrados com sucesso\n")
    return include_dirs


def build_and_log_graphs(
    files: list, modules: list, destination_path: str = None
) -> tuple:
    """Builds the direct and inverse module dependency graphs and logs the result."""
    print_green("[LOG] Construindo os grafos direto e inverso\n")

    # Convert relative paths back to absolute paths for build_module_graph
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


def process_files_with_llama(
    no_llama: bool,
    non_tb_files: list,
    tb_files: list,
    modules: list,
    module_graph: dict,
    repo_name: str,
    model: str,
) -> tuple:
    """Processes files and identifies the top module using OLLAMA, if enabled."""
    if not no_llama:
        print_green(
            "[LOG] Utilizando OLLAMA para identificar os arquivos do processador\n"
        )
        filtered_files = get_filtered_files_list(
            non_tb_files, tb_files, modules, module_graph, repo_name, model
        )
        print_green("[LOG] Utilizando OLLAMA para identificar o módulo principal\n")
        top_module = get_top_module(
            non_tb_files, tb_files, modules, module_graph, repo_name, model
        )
    else:
        filtered_files, top_module = non_tb_files, ""
    return filtered_files, top_module
