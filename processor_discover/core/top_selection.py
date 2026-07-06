"""Top-module selection heuristics for processor discovery."""

import os
import re
from typing import List

from .heuristics import (
    UTILITY_PATTERNS,
    ensure_mapping as _ensure_mapping,
    is_functional_unit_name as _is_functional_unit_name,
    is_interface_module_name as _is_interface_module_name,
    is_micro_stage_name as _is_micro_stage_name,
    is_peripheral_like_name as _is_peripheral_like_name,
    reachable_size as _reachable_size,
)
from ..utils.log import print_green, print_yellow


def _is_fpga_path(path: str) -> bool:
    """Return True if the file lives under an 'fpga' or 'boards' folder (case-insensitive).
    Treat these as FPGA board wrapper trees to exclude from core detection.
    Works with relative or absolute paths.
    """
    try:
        p = path.replace("\\", "/").lower()
        return (
            "/fpga/" in p
            or p.startswith("fpga/")
            or "/fpga-" in p
            or p.endswith("/fpga")
            or "/boards/" in p
            or p.startswith("boards/")
            or "/board/" in p
            or p.startswith("board/")
        )
    except Exception:
        return False


def _analyze_instantiation_patterns(module_name: str, file_path: str) -> dict:
    """
    Analyze what types of components a module instantiates to classify it as CPU core vs SoC top.
    Returns a dict with counts of different component types found.
    """
    if not file_path or not os.path.exists(file_path):
        return {}

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return {}

    # CPU core component patterns (things a CPU would instantiate)
    cpu_patterns = [
        r"\b(alu|arithmetic|logic)\b",
        r"\b(mul|mult|multiplier)\b",
        r"\b(div|divider|division)\b",
        r"\b(fpu|float|floating)\b",
        r"\b(cache|icache|dcache)\b",
        r"\b(mmu|tlb)\b",
        r"\b(branch|pred|predictor)\b",
        r"\b(decode|decoder)\b",
        r"\b(execute|exec|execution)\b",
        r"\b(fetch|instruction)\b",
        r"\b(register|regfile|rf|mprf)\b",
        r"\b(pipeline|pipe)\b",
        r"\b(hazard|forward|forwarding)\b",
        r"\b(csr|control|status)\b",
        # SuperScalar and RISC-V specific patterns
        r"\b(schedule|scheduler|issue|dispatch)\b",
        r"\b(retire|commit|completion)\b",
        r"\b(reservation|station|rob|reorder)\b",
        r"\b(inst|instr|instruction)\b",
        r"\b(mem|memory)(?!_external|_ext|_sys)\b",  # Internal memory components
        r"\b(lsu|load|store)\b",
        r"\b(sys|system)_(?!bus|external)\b",  # System components but not external bus
    ]

    # SoC/System component patterns (things an SoC top would instantiate)
    # Note: Exclude memory/clock patterns that could be internal to CPU cores
    soc_patterns = [
        r"\b(gpio|pin|port)\b",
        r"\b(uart|serial)\b",
        r"\b(spi|i2c)\b",
        r"\b(timer|counter)\b",
        r"\b(interrupt|plic|clint)\b",
        r"\b(dma|direct|memory|access)\b",
        r"\b(peripheral|periph)\b",
        r"\b(bridge|interconnect)\b",
        r"\b(debug|jtag)\b",
        r"\b(external_mem|ext_mem|ddr|sdram)\b",
        r"\b(system_bus|main_bus|soc_bus)\b",
    ]

    instantiation_regex = r"^\s*(\w+)\s*(?:#\s*\([^)]*\))?\s*(\w+)\s*\("

    cpu_score = 0
    soc_score = 0
    total_instances = 0
    instantiated_modules = []

    for match in re.finditer(
        instantiation_regex, content, re.MULTILINE | re.IGNORECASE
    ):
        module_type = match.group(1).lower()
        instance_name = match.group(2).lower()
        total_instances += 1
        instantiated_modules.append(module_type)

        combined_text = f"{module_type} {instance_name}"

        for pattern in cpu_patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                cpu_score += 1
                break

        for pattern in soc_patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                soc_score += 1
                break

    return {
        "cpu_score": cpu_score,
        "soc_score": soc_score,
        "total_instances": total_instances,
        "cpu_ratio": cpu_score / max(total_instances, 1),
        "soc_ratio": soc_score / max(total_instances, 1),
        "instantiated_modules": instantiated_modules,
    }


def _analyze_cpu_signals(
    module_name: str, file_path: str, instantiated_in_file: str
) -> dict:
    """
    Analyze the signals/ports used when instantiating a module to determine if it's a CPU core.
    Look for CPU-characteristic signals like address buses, data buses, memory interfaces, etc.
    """
    if (
        not file_path
        or not os.path.exists(file_path)
        or not os.path.exists(instantiated_in_file)
    ):
        return {"cpu_signal_score": 0, "signals_found": []}

    try:
        with open(instantiated_in_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return {"cpu_signal_score": 0, "signals_found": []}

    # CPU core signal patterns (things connected to CPU cores)
    cpu_signal_patterns = [
        r"\b(addr|address|mem_addr|i_addr|d_addr)\b",
        r"\b(data|mem_data|i_data|d_data|mem_rdata|mem_wdata|rdata|wdata)\b",
        r"\b(mem_req|mem_gnt|mem_rvalid|mem_we|mem_be|we|be)\b",
        r"\b(instr|instruction|i_req|i_gnt|i_rvalid)\b",
        r"\b(icache|dcache|cache_req|cache_resp)\b",
        r"\b(clk|clock|rst|reset|rstn)\b",
        r"\b(irq|interrupt|exception)\b",
        r"\b(halt|stall|flush|valid|ready)\b",
        r"\b(hart|hartid|mhartid)\b",
        r"\b(retire|commit|trap)\b",
        r"\b(axi|ahb|apb|wb|wishbone)\b",
        r"\b(avalon|tilelink)\b",
    ]

    # Look for instantiation of the specific module and analyze its connections
    module_instantiation_pattern = (
        rf"\b{re.escape(module_name)}\s+(?:#\s*\([^)]*\))?\s*(\w+)\s*\((.*?)\);"
    )

    cpu_signal_score = 0
    signals_found = []

    for match in re.finditer(
        module_instantiation_pattern, content, re.DOTALL | re.IGNORECASE
    ):
        instance_name = match.group(1)
        port_connections = match.group(2)

        # Analyze the port connections
        for pattern in cpu_signal_patterns:
            signal_matches = re.findall(pattern, port_connections, re.IGNORECASE)
            if signal_matches:
                cpu_signal_score += len(signal_matches)
                signals_found.extend(signal_matches)

    # Bonus points for instance names that suggest CPU core
    instance_pattern = rf"\b{re.escape(module_name)}\s+(?:#\s*\([^)]*\))?\s*(\w+)\s*\("
    for match in re.finditer(instance_pattern, content, re.IGNORECASE):
        instance_name = match.group(1).lower()
        if any(
            term in instance_name for term in ["cpu", "core", "proc", "hart", "riscv"]
        ):
            cpu_signal_score += 20  # Higher bonus for CPU-like instance names
            signals_found.append(f"instance_name:{instance_name}")
        elif instance_name.startswith("core"):  # core0, core1, etc.
            cpu_signal_score += 25  # Even higher for numbered cores
            signals_found.append(f"instance_name:{instance_name}")

    return {
        "cpu_signal_score": cpu_signal_score,
        "signals_found": list(set(signals_found)),  # Remove duplicates
    }


def _find_cpu_core_in_soc(top_module: str, module_graph: dict, modules: list) -> str:
    """
    If the top module is a SoC, try to find the actual CPU core it instantiates.
    Returns the CPU core module name, or the original top_module if not found.
    """
    print_green(f"[CORE_SEARCH] Starting SoC analysis for top_module: {top_module}")

    if not top_module or not modules:
        print_yellow(
            f"[CORE_SEARCH] Early return: top_module={top_module}, modules_count={len(modules) if modules else 0}"
        )
        return top_module

    # Create module name to file path mapping
    module_to_file = {}
    for module_name, file_path in modules:
        module_to_file[module_name] = file_path

    print_green(
        f"[CORE_SEARCH] Created module mapping with {len(module_to_file)} entries"
    )

    # Get the file path for the top module
    top_file_path = module_to_file.get(top_module)
    if not top_file_path:
        print_yellow(f"[CORE_SEARCH] No file found for top_module: {top_module}")
        return top_module

    print_green(f"[CORE_SEARCH] Found file for {top_module}: {top_file_path}")

    # Analyze what the top module instantiates
    patterns = _analyze_instantiation_patterns(top_module, top_file_path)
    if not patterns:
        print_yellow(f"[CORE_SEARCH] No instantiation patterns found for {top_module}")
        return top_module

    print_green(f"[CORE_SEARCH] Instantiation patterns: {patterns}")

    # Check if this looks like a SoC (has peripherals)
    soc_ratio = patterns.get("soc_ratio", 0)
    total_instances = patterns.get("total_instances", 0)
    instantiated_modules = patterns.get("instantiated_modules", [])

    # If SoC ratio is significant OR has many instances, look for CPU core candidates among instantiated modules
    if (
        soc_ratio > 0.2
    ):  # Has significant peripheral instantiations (increased threshold)
        print_green(
            f"[CORE_SEARCH] {top_module} appears to be a SoC (soc_ratio={soc_ratio:.2f}, total_instances={total_instances}), searching for CPU core..."
        )

        # Look for CPU core candidates among instantiated modules
        cpu_core_candidates = []

        for inst_module in instantiated_modules:
            # Skip obvious non-CPU modules
            if any(
                skip in inst_module.lower()
                for skip in [
                    "ram",
                    "rom",
                    "timer",
                    "uart",
                    "gpio",
                    "spi",
                    "i2c",
                    "vga",
                    "dma",
                    "bus",
                    "matrix",
                    "interface",
                    "sync",
                    "interconnect",
                ]
            ):
                continue

            # Analyze this potential CPU core - do case-insensitive module lookup
            inst_file_path = None
            proper_module_name = None
            for module_name, file_path in module_to_file.items():
                if module_name.lower() == inst_module.lower():
                    inst_file_path = file_path
                    proper_module_name = module_name  # Use the proper-cased module name
                    break

            if inst_file_path:
                # Analyze the signals used when instantiating this module
                signal_analysis = _analyze_cpu_signals(
                    proper_module_name, inst_file_path, top_file_path
                )

                inst_patterns = _analyze_instantiation_patterns(
                    proper_module_name, inst_file_path
                )
                if inst_patterns:
                    inst_cpu_ratio = inst_patterns.get("cpu_ratio", 0)
                    inst_soc_ratio = inst_patterns.get("soc_ratio", 0)
                    inst_total = inst_patterns.get("total_instances", 0)

                    # Score this module as a CPU core candidate
                    cpu_score = 0

                    # Prefer modules with CPU-like keywords in name
                    name_lower = proper_module_name.lower()
                    if any(
                        cpu_term in name_lower
                        for cpu_term in ["cpu", "core", "risc", "processor", "hart"]
                    ):
                        cpu_score += 10

                    # Special bonus for repo-specific core names (like 'aukv' for AUK-V)
                    if top_module:
                        top_parts = [
                            part.lower()
                            for part in top_module.replace("_", " ").split()
                            if len(part) > 2
                        ]
                        for part in top_parts:
                            if part in name_lower and part not in [
                                "soc",
                                "system",
                                "top",
                                "eggs",
                            ]:
                                cpu_score += 15  # Higher bonus for repo-specific names

                    # Signal analysis score - this is the key addition!
                    signal_score = signal_analysis["cpu_signal_score"]
                    cpu_score += signal_score

                    # Prefer modules with CPU-like internal structure
                    if inst_cpu_ratio > inst_soc_ratio:
                        cpu_score += 5
                    if inst_cpu_ratio > 0.3:
                        cpu_score += 3

                    # Prefer modules with reasonable complexity (not too simple, not too complex)
                    if 5 <= inst_total <= 50:
                        cpu_score += 2

                    # Only consider if it has some positive indicators
                    if cpu_score > 0:
                        cpu_core_candidates.append(
                            (
                                proper_module_name,
                                cpu_score,
                                inst_cpu_ratio,
                                inst_total,
                                signal_analysis,
                            )
                        )
                        print_green(
                            f"[CORE_SEARCH] Found CPU core candidate: {proper_module_name}"
                        )
                        print_green(
                            f"[CORE_SEARCH]   Total score={cpu_score} (signal_score={signal_score}, cpu_ratio={inst_cpu_ratio:.2f}, instances={inst_total})"
                        )
                        print_green(
                            f"[CORE_SEARCH]   CPU signals found: {signal_analysis['signals_found']}"
                        )

        # Select the best CPU core candidate
        if cpu_core_candidates:
            # Sort by score descending, then by CPU ratio descending
            cpu_core_candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
            selected_core = cpu_core_candidates[0][0]
            print_green(f"[CORE_SEARCH] Selected CPU core: {selected_core}")
            return selected_core
        else:
            print_yellow(
                f"[CORE_SEARCH] No suitable CPU core found in {top_module}, keeping original top module"
            )

    return top_module


def rank_top_candidates(
    module_graph, module_graph_inverse, repo_name=None, modules=None
):
    """
    Rank module candidates to identify the best top module.
    Analyzes both module connectivity and instantiation patterns to distinguish CPU cores from SoC tops.
    """
    # module_graph: A -> [B, C] means A instantiates B and C
    # module_graph_inverse: B -> [A] means B is instantiated by A

    instantiates = _ensure_mapping(
        module_graph
    )  # What each module instantiates (its children)
    instantiated_by = _ensure_mapping(
        module_graph_inverse
    )  # What instantiates each module (its parents)

    nodes = set(instantiated_by.keys()) | set(instantiates.keys())
    for n in nodes:
        instantiated_by.setdefault(n, [])
        instantiates.setdefault(n, [])

    # Filter out Verilog keywords and invalid module names
    valid_modules = []
    verilog_keywords = {
        "if",
        "else",
        "always",
        "initial",
        "begin",
        "end",
        "case",
        "default",
        "for",
        "while",
        "assign",
    }

    for module in nodes:
        if (
            module not in verilog_keywords
            and len(module) > 1
            and (module.replace("_", "").isalnum())
        ):
            valid_modules.append(module)

    # Find candidates: modules with few parents (few modules instantiate them) are preferred as top modules
    zero_parent_modules = [m for m in valid_modules if not instantiated_by.get(m, [])]
    low_parent_modules = [
        m for m in valid_modules if len(instantiated_by.get(m, [])) <= 2
    ]

    # Always include standalone 'core' and 'cpu' modules as candidates
    core_cpu_modules = [
        m for m in valid_modules if m.lower() in ["core", "cpu", "processor"]
    ]

    # Include repo name matches even if they have many parents
    repo_name_matches = []
    cpu_core_matches = []

    # Create module name to file path mapping for instantiation analysis
    module_to_file = {}
    if modules:
        for module_name, file_path in modules:
            module_to_file[module_name] = file_path

    if repo_name and len(repo_name) > 2:
        repo_lower = repo_name.lower()
        for module in valid_modules:
            module_lower = module.lower()
            # Enhanced repo matching - include exact matches regardless of parent count
            if (
                repo_lower == module_lower
                or repo_lower in module_lower
                or module_lower in repo_lower
            ):
                repo_name_matches.append(module)

            # Also check for common variations
            repo_variations = [repo_lower, repo_lower.upper(), repo_lower.capitalize()]
            for variation in repo_variations:
                if variation == module:
                    repo_name_matches.append(module)
                    print_green(
                        f"[REPO-MATCH] Found repo variation match: {module} -> {variation}"
                    )
                    break

            # Enhanced CPU core detection using instantiation patterns
            if (
                any(
                    pattern in module_lower
                    for pattern in [
                        repo_lower,
                        "cpu",
                        "core",
                        "risc",
                        "processor",
                        "microcontroller",
                    ]
                )
                and module not in zero_parent_modules
                and module not in low_parent_modules
                and (
                    module_lower == "microcontroller"
                    or module_lower == "core"
                    or module_lower == "cpu"
                    or not any(
                        bad_pattern in module_lower
                        for bad_pattern in [
                            "div",
                            "mul",
                            "alu",
                            "fpu",
                            "cache",
                            "mem",
                            "bus",
                            "_ctrl",
                            "ctrl_",
                            "reg",
                            "decode",
                            "fetch",
                            "exec",
                            "forward",
                            "hazard",
                            "pred",
                            "sm3",
                            "sha",
                            "aes",
                            "des",
                            "rsa",
                            "ecc",
                            "crypto",
                            "hash",
                            "cipher",
                            "encrypt",
                            "decrypt",
                            "uart",
                            "spi",
                            "i2c",
                            "gpio",
                            "timer",
                            "interrupt",
                            "dma",
                            "pll",
                            "clk",
                            "pwm",
                            "aon",
                            "hclk",
                            "oitf",
                            "wrapper",
                            "regs",
                        ]
                    )
                )
                and not any(
                    module_lower.startswith(prefix)
                    for prefix in ["sirv_", "apb_", "axi_", "ahb_", "wb_", "avalon_"]
                )
            ):  # Exclude peripheral prefix modules

                # Check instantiation patterns if file path is available
                is_cpu_core = False
                file_path = module_to_file.get(module)
                if file_path:
                    patterns = _analyze_instantiation_patterns(module, file_path)
                    if patterns:
                        cpu_ratio = patterns.get("cpu_ratio", 0)
                        soc_ratio = patterns.get("soc_ratio", 0)
                        total_instances = patterns.get("total_instances", 0)

                        if total_instances > 0 and (
                            cpu_ratio > soc_ratio * 1.5 or cpu_ratio > 0.3
                        ):
                            is_cpu_core = True
                            print_green(
                                f"[INSTANTIATION] {module}: CPU core (cpu_ratio={cpu_ratio:.2f}, soc_ratio={soc_ratio:.2f}, instances={total_instances})"
                            )
                        elif total_instances == 0:
                            is_cpu_core = True
                            print_green(
                                f"[INSTANTIATION] {module}: CPU core (fallback - no instantiations found)"
                            )

                if is_cpu_core and module not in repo_name_matches:
                    cpu_core_matches.append(module)

    candidates = list(
        set(
            zero_parent_modules
            + low_parent_modules
            + core_cpu_modules
            + repo_name_matches
            + cpu_core_matches
        )
    )

    if not candidates:
        candidates = valid_modules

    repo_lower = (repo_name or "").lower()
    scored = []

    # Normalize repo name: remove hyphens and underscores for matching
    repo_normalized = repo_lower.replace("-", "").replace("_", "")

    for c in candidates:
        reach = _reachable_size(
            instantiates, c
        )  # How many modules does this one instantiate (directly or indirectly)
        score = reach * 10  # Base score from connectivity
        name_lower = c.lower()
        name_normalized = name_lower.replace("_", "")

        # REPOSITORY NAME MATCHING (Highest Priority)
        # Only apply repo matching if the module actually exists in the dependency graph
        if repo_normalized and len(repo_normalized) > 2 and c in module_graph:
            if repo_normalized == name_normalized:
                score += 50000
            elif repo_normalized in name_normalized:
                score += 40000
            elif name_normalized in repo_normalized:
                score += 35000
            else:
                # Check initialism matching (e.g., "black-parrot" → "bp")
                # Extract initials from words separated by hyphens or underscores
                repo_words = repo_lower.replace("_", "-").split("-")
                if len(repo_words) >= 2:
                    initialism = "".join(word[0] for word in repo_words if word)
                    # Check if module starts with initialism + underscore (e.g., "bp_core")
                    if name_lower.startswith(initialism + "_"):
                        # Check if it's a core/processor/cpu module
                        if any(
                            x in name_lower
                            for x in [
                                "core",
                                "processor",
                                "cpu",
                                "unicore",
                                "multicore",
                            ]
                        ):
                            score += 45000
                            print_green(
                                f"[REPO-MATCH] Initialism match: {repo_lower} → {initialism} → {c}"
                            )

                # Fuzzy matching
                clean_repo = repo_lower
                clean_module = name_lower

                for pattern in ["_cpu", "_core", "cpu_", "core_", "_top", "top_"]:
                    clean_repo = clean_repo.replace(pattern, "")
                    clean_module = clean_module.replace(pattern, "")

                if clean_repo == clean_module and len(clean_repo) > 1:
                    score += 30000
                elif clean_repo in clean_module or clean_module in clean_repo:
                    score += 20000

        # SPECIAL CASE: "Top" module when repo name doesn't match any real module
        if name_lower == "top" and repo_lower:
            # Check if the exact repo name exists as a real module in the graph
            repo_name_exists = any(
                repo_lower == mod.lower()
                for mod in module_graph.keys()
                if mod in valid_modules
            )
            if not repo_name_exists:
                score += 48000  # High but slightly less than exact repo match

        # ARCHITECTURAL INDICATORS
        if any(term in name_lower for term in ["cpu", "processor"]):
            score += 2000

        # Special case for microcontroller - this is a CPU top module
        if "microcontroller" in name_lower:
            score += 3000

        # CPU TOP MODULE DETECTION (Very High Priority)
        # Look for typical CPU top module patterns
        cpu_top_patterns = [
            f"{repo_lower}_top",
            f"top_{repo_lower}",
            f"{repo_lower}_cpu",
            f"cpu_{repo_lower}",
            "cpu_top",
            "core_top",
            "processor_top",
            "riscv_top",
            "risc_top",
        ]
        if repo_lower:
            cpu_top_patterns.extend(
                [repo_lower, f"{repo_lower}_core", f"core_{repo_lower}"]
            )

        for pattern in cpu_top_patterns:
            if name_lower == pattern:
                # Ensure it's not a functional unit
                if not any(
                    unit in name_lower
                    for unit in [
                        "fadd",
                        "fmul",
                        "fdiv",
                        "fsqrt",
                        "fpu",
                        "div",
                        "mul",
                        "alu",
                    ]
                ):
                    score += 45000
                    break

        # DIRECT CORE NAME PATTERNS (high priority - we want cores, not SoCs)
        # Special case for exact "Core" module name - very common CPU top module pattern
        if name_lower == "core":
            score += 40000

        # Look for modules that are exactly the repo name (likely the core)
        if repo_lower and name_lower == repo_lower:
            score += 25000

        # Specific CPU core boost - give highest priority to actual core modules
        if "core" in name_lower and repo_lower:
            # Check if it's a functional unit core first - apply heavy penalty
            if any(
                unit in name_lower
                for unit in [
                    "fadd",
                    "fmul",
                    "fdiv",
                    "fsqrt",
                    "fpu",
                    "div",
                    "mul",
                    "alu",
                    "mem",
                    "cache",
                    "bus",
                    "_ctrl",
                    "ctrl_",
                    "reg",
                    "decode",
                    "fetch",
                    "exec",
                    "forward",
                    "hazard",
                    "pred",
                    "shift",
                    "barrel",
                    "adder",
                    "mult",
                    "divider",
                    "encoder",
                    "decoder",
                ]
            ):
                # Exception: don't penalize microcontroller
                if "microcontroller" not in name_lower:
                    score -= 15000
            # Penalize subsystem cores - they're usually wrappers around the actual core
            elif "subsys" in name_lower or "subsystem" in name_lower:
                score -= 8000
            # Strong boost for exact core modules like "repo_core"
            elif (
                name_lower == f"{repo_lower}_core" or name_lower == f"core_{repo_lower}"
            ):
                score += 25000
            # Generic pattern: any module ending with "_core" that looks like a main core module
            elif name_lower.endswith("_core"):
                score += 20000
            # Medium boost for modules containing both repo name and core
            elif repo_lower in name_lower and "core" in name_lower:
                score += 15000

        if "core" in name_lower:
            # Heavy penalty for functional unit cores
            if any(
                unit in name_lower
                for unit in [
                    "fadd",
                    "fmul",
                    "fdiv",
                    "fsqrt",
                    "fpu",
                    "div",
                    "mul",
                    "alu",
                ]
            ):
                score -= 10000
            # Additional penalty for other peripheral cores (but exclude microcontroller)
            elif not ("microcontroller" in name_lower) and any(
                unit in name_lower
                for unit in [
                    "mem",
                    "cache",
                    "bus",
                    "_ctrl",
                    "ctrl_",
                    "reg",
                    "decode",
                    "fetch",
                    "exec",
                    "forward",
                    "hazard",
                    "pred",
                    "shift",
                    "barrel",
                    "adder",
                    "mult",
                    "divider",
                    "encoder",
                    "decoder",
                ]
            ):
                score -= 5000
            else:
                score += 1500

        if any(arch in name_lower for arch in ["riscv", "risc", "mips", "arm"]):
            score += 1000

        if name_lower.endswith("_top") or name_lower.startswith("top_"):
            score += 800

        # Penalize single functional units (ALU, multiplier, divider, etc.)
        if _is_functional_unit_name(name_lower):
            score -= 12000
        # Penalize micro-stage modules that are unlikely to be CPU tops
        if _is_micro_stage_name(name_lower):
            score -= 40000

        # Penalize interface-only modules
        if _is_interface_module_name(name_lower):
            score -= 12000

        # Path-aware penalty: if the module's file lives in a micro-stage subfolder, penalize
        mod_file = None
        if modules:
            for mname, mfile in modules:
                if mname == c:
                    mod_file = mfile
                    break
        if mod_file:
            path_l = mod_file.replace("\\", "/").lower()
            stage_dirs = [
                "/fetchunit/",
                "/fetchstage/",
                "/rename",
                "/renamelogic/",
                "/scheduler/",
                "/decode",
                "/commit",
                "/dispatch",
                "/issue",
                "/execute",
                "/integerbackend/",
                "/memorybackend/",
                "/fpbackend/",
                "/muldivunit/",
                "/floatingpointunit/",
            ]
            if any(sd in path_l for sd in stage_dirs):
                score -= 15000

        # SOC penalty - we want CPU cores, not full system-on-chip
        if "soc" in name_lower:
            score -= 5000

        # Penalize utility library modules when project-specific modules exist
        # e.g., penalize bsg_* modules when bp_* modules exist (basejump_stl vs black-parrot)
        if repo_lower and len(repo_lower) > 2:
            repo_words = repo_lower.replace("_", "-").split("-")
            if len(repo_words) >= 2:
                initialism = "".join(word[0] for word in repo_words if word)
                # Check if any modules start with the project initialism
                project_modules_exist = any(
                    m.lower().startswith(initialism + "_") for m in valid_modules
                )

                # If project modules exist (like bp_*) and this is a utility (like bsg_*)
                if project_modules_exist:
                    # Penalize modules that don't start with the project initialism
                    # Common utility prefixes: bsg_, hardfloat_, common_
                    if not name_lower.startswith(initialism + "_"):
                        # Only penalize if it starts with a known utility prefix
                        utility_prefixes = [
                            "bsg_",
                            "common_",
                            "util_",
                            "lib_",
                            "helper_",
                        ]
                        if any(
                            name_lower.startswith(prefix) for prefix in utility_prefixes
                        ):
                            score -= 35000

        # STRUCTURAL HEURISTICS
        num_children = len(instantiates.get(c, []))  # What this module instantiates
        num_parents = len(instantiated_by.get(c, []))  # Who instantiates this module

        # Boost CPU cores (modules with few parents and "core"/"cpu"/"processor" in name)
        # These are better targets for testing than SoC tops
        # Can have multiple parents (different top-level wrappers, test harnesses, etc.)
        is_likely_core = (
            num_parents >= 1
            and num_parents <= 3
            and any(pattern in name_lower for pattern in ["core", "cpu", "processor"])
            and not any(
                bad in name_lower
                for bad in ["_top", "top_", "soc", "system", "wrapper"]
            )
        )

        if is_likely_core and num_children > 2:
            score += 25000  # Very strong preference for CPU cores
        elif num_children > 10 and num_parents == 0:
            score += 1000
        elif num_children > 5 and num_parents <= 1:
            score += 500
        elif num_children > 2:
            score += 200

        # Boost if the module instantiates components from multiple CPU subsystems (suggests a core)
        if modules:
            mod_file = None
            for mname, mfile in modules:
                if mname == c:
                    mod_file = mfile
                    break
            if mod_file and os.path.exists(mod_file):
                patterns = _analyze_instantiation_patterns(c, mod_file)
                insts = patterns.get("instantiated_modules", []) if patterns else []
                subsys_hits = 0
                text = " ".join(insts)
                for kw in [
                    "fetch",
                    "decode",
                    "rename",
                    "issue",
                    "commit",
                    "schedule",
                    "lsu",
                    "cache",
                    "branch",
                    "rob",
                    "regfile",
                    "csr",
                ]:
                    if re.search(rf"\b{kw}\b", text, re.I):
                        subsys_hits += 1
                if subsys_hits >= 3:
                    score += 4000

        # NEGATIVE INDICATORS
        if any(
            pattern in name_lower
            for pattern in [
                "_tb",
                "tb_",
                "test",
                "bench",
                "compliance",
                "verify",
                "checker",
                "monitor",
                "fpv",
                "bind",
                "assert",
            ]
        ):
            score -= 10000

        peripheral_terms = [
            "uart",
            "spi",
            "i2c",
            "gpio",
            "timer",
            "dma",
            "plic",
            "clint",
            "baud",
            "fifo",
            "ram",
            "rom",
            "cache",
            "pwm",
            "aon",
            "hclk",
            "oitf",
            "wrapper",
            "regs",
        ]
        if any(term in name_lower for term in peripheral_terms):
            score -= 5000

        # Very strong penalty for modules that look like memory/fabric/peripheral wrappers
        if _is_peripheral_like_name(name_lower):
            score -= 15000

        # Generic penalty for likely peripheral module prefixes
        peripheral_prefixes = ["sirv_", "apb_", "axi_", "ahb_", "wb_", "avalon_"]
        if any(name_lower.startswith(prefix) for prefix in peripheral_prefixes):
            score -= 7000

        if any(pattern in name_lower for pattern in ["debug", "jtag", "bram"]):
            score -= 2000

        if any(name_lower.startswith(pat) for pat in UTILITY_PATTERNS):
            score -= 2000

        if reach < 2:
            score -= 1000

        if len(name_lower) > 25:
            score -= len(name_lower) * 5
        elif len(name_lower) < 6:
            score += 100

        scored.append((score, reach, c))

    # CONDITIONAL TOP MODULE PENALTY
    # Check if there are any "core" or "cpu" candidates in the list that are better choices than "top"
    # This includes:
    # 1. Modules with "core", "cpu", "processor", or "riscv" in their names (but not wrapped in _top/top_)
    # 2. Exact matches like "CPU", "Core", "Processor", "RISCV" (standalone names)
    # 3. Exclude peripheral cores (SPI, UART, I2C, GPIO, etc.)
    peripheral_patterns = [
        "spi",
        "uart",
        "i2c",
        "gpio",
        "timer",
        "pwm",
        "adc",
        "dac",
        "can",
        "usb",
        "eth",
        "pci",
    ]

    has_core_candidates = any(
        (
            any(
                pattern in c.lower()
                for pattern in ["core", "cpu", "processor", "riscv", "atom"]
            )
            or c in ["CPU", "Core", "Processor", "CORE", "RISCV"]
        )
        and not any(
            bad in c.lower() for bad in ["_top", "top_", "soc", "system", "wrapper"]
        )
        and not any(periph in c.lower() for periph in peripheral_patterns)
        for score, reach, c in scored
    )

    # If core candidates exist, apply penalty to "top" modules and boost to core/cpu modules
    if has_core_candidates:
        adjusted_scored = []
        for score, reach, c in scored:
            name_lower = c.lower()
            # Check if this is a top-level wrapper
            num_parents = len(instantiated_by.get(c, []))

            # Penalize if:
            # 1. Has "_top" or "top_" pattern (like e203_cpu_top, ibex_top)
            # 2. Is exactly named "top" (generic top module)
            is_top_wrapper = num_parents == 0 and (
                any(pattern in name_lower for pattern in ["_top", "top_"])
                or name_lower == "top"
            )

            # Boost if this is a CPU/core/RISCV module (exact matches or with cpu/core/riscv/atom in name)
            # Exclude peripheral cores (SPI_core, UART_core, etc.)
            is_cpu_core = (
                c in ["CPU", "Core", "Processor", "CORE", "RISCV"]
                or any(
                    pattern in name_lower
                    for pattern in ["_cpu", "cpu_", "_core", "core_", "riscv", "atom"]
                )
            ) and not any(periph in name_lower for periph in peripheral_patterns)

            # Check if this is a bus wrapper (has bus protocol suffix)
            bus_wrapper_patterns = ["_wb", "_axi", "_ahb", "_apb", "_obi", "_tilelink"]
            is_bus_wrapper = any(
                pattern in name_lower for pattern in bus_wrapper_patterns
            )

            # Always penalize top wrappers when core candidates exist, even if they have core/cpu/riscv in name
            # (e.g., RISCV_TOP should be penalized in favor of RISCV)
            if is_top_wrapper:
                # Apply a strong penalty to prefer cores over wrappers
                score -= 15000  # Strong penalty to overcome structural advantage
                print_yellow(
                    f"[RANKING] Applying top-wrapper penalty to {c} (core/cpu candidates available)"
                )
            elif is_cpu_core and is_bus_wrapper:
                # Bus wrappers get a smaller boost (prefer the unwrapped core)
                score += 5000  # Moderate boost for bus-wrapped cores
                print_yellow(f"[RANKING] Applying bus-wrapper boost to {c}")
            elif is_cpu_core and not any(
                bad in name_lower
                for bad in ["_top", "top_", "soc", "system", "wrapper"]
            ):
                # Pure cores get the full boost
                score += 10000  # Significant boost for CPU/core modules
                print_yellow(f"[RANKING] Applying CPU/core boost to {c}")

            adjusted_scored.append((score, reach, c))
        scored = adjusted_scored

    # Sort by score (descending), then by reach (descending), then by name
    scored.sort(reverse=True, key=lambda t: (t[0], t[1], t[2]))

    ranked = [c for score, _, c in scored if score > -5000]
    # If the top few are micro-stage or interface modules, try to skip them in favor of a core-like one
    filtered_ranked = [
        c
        for c in ranked
        if not _is_micro_stage_name(c.lower())
        and not _is_interface_module_name(c.lower())
    ]
    if filtered_ranked:
        ranked = filtered_ranked
    return ranked, cpu_core_matches
