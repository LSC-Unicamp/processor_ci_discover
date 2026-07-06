"""Shared name and graph heuristics for top-module selection."""

from collections import deque
from typing import Any, Iterable

UTILITY_PATTERNS = ("gen_", "dff", "buf", "full_handshake", "fifo", "mux", "regfile")

FUNCTIONAL_UNIT_TERMS = (
    "multiplier",
    "divider",
    "div",
    "mul",
    "alu",
    "adder",
    "shifter",
    "barrel",
    "encoder",
    "decoder",
    "fpu",
    "fpdiv",
    "fpsqrt",
    "cache",
    "icache",
    "dcache",
    "tlb",
    "btb",
    "branch",
    "predictor",
    "ras",
    "returnaddress",
    "rsb",
)

HDL_EXTRA_FUNCTIONAL_UNIT_TERMS = (
    "fadd",
    "fmul",
    "fdiv",
    "fsub",
    "fma",
    "fcmp",
    "fcvt",
)


def is_peripheral_like_name(name: str) -> bool:
    """Return True for common peripheral, memory, and bus-fabric names."""
    normalized = (name or "").lower()
    if ("axi" in normalized) or normalized.startswith(
        ("axi_", "apb_", "ahb_", "wb_", "avalon_", "tl_", "tilelink_")
    ):
        return True
    if any(
        term in normalized
        for term in ["memory", "ram", "rom", "cache", "sdram", "ddr", "bram"]
    ):
        return True
    if any(
        term in normalized
        for term in [
            "uart",
            "spi",
            "i2c",
            "gpio",
            "timer",
            "dma",
            "plic",
            "clint",
            "jtag",
            "bridge",
            "interconnect",
            "xbar",
        ]
    ):
        return True
    if any(
        term in normalized
        for term in ["axi4", "axi_lite", "axi4lite", "axi_lite_ctrl", "axi_ctrl"]
    ):
        return True
    return False


def is_functional_unit_name(
    name: str,
    *,
    extra_terms: Iterable[str] = (),
) -> bool:
    """Return True for small functional units rather than full processor tops."""
    normalized = (name or "").lower()
    for term in (*FUNCTIONAL_UNIT_TERMS, *tuple(extra_terms)):
        if term in normalized:
            return True

    if (
        "_bp_" in normalized
        or normalized.endswith("_bp")
        or normalized.startswith("bp_pred")
        or "bpred" in normalized
    ):
        if not any(
            term in normalized
            for term in ["core", "processor", "cpu", "unicore", "multicore"]
        ):
            return True

    return False


def is_micro_stage_name(name: str) -> bool:
    """Return True for pipeline-stage blocks that are not full CPU tops."""
    normalized = (name or "").lower()
    terms = [
        "fetch",
        "decode",
        "rename",
        "issue",
        "schedule",
        "commit",
        "retire",
        "execute",
        "registerread",
        "registerwrite",
        "regread",
        "regwrite",
        "lsu",
        "mmu",
        "reorder",
        "rob",
        "iq",
        "btb",
        "bpu",
        "ras",
        "predecode",
        "dispatch",
        "wakeup",
        "queue",
        "storequeue",
        "loadqueue",
        "activelist",
        "freelist",
        "rmt",
        "nextpc",
        "pcstage",
    ]
    exact_stage_names = [
        "wb",
        "id",
        "ex",
        "mem",
        "if",
        "ma",
        "wr",
        "pc",
        "ctrl",
        "regs",
        "alu",
        "dram",
        "iram",
        "halt",
        "machine",
    ]
    if normalized in exact_stage_names:
        return True
    if (
        "_rs_" in normalized
        or normalized.startswith("rs_")
        or normalized.endswith("_rs")
        or normalized == "rs"
    ):
        return True
    return any(term in normalized for term in terms)


def is_interface_module_name(
    name: str,
    *,
    suffixes: tuple[str, ...] = ("if", "_if", "_inf", "inf"),
) -> bool:
    """Return True for interface-like module names."""
    normalized = (name or "").lower()
    return (
        any(normalized.endswith(suffix) for suffix in suffixes)
        or "interface" in normalized
    )


def ensure_mapping(mapping: Any) -> dict[str, list[str]]:
    """Normalize a graph-like input into a dict: node -> list(children/parents)."""
    out: dict[str, list[str]] = {}
    if not mapping:
        return out

    if isinstance(mapping, dict):
        for key, value in mapping.items():
            key_str = str(key)
            if value is None:
                out[key_str] = []
            elif isinstance(value, (list, tuple, set)):
                out[key_str] = [str(item) for item in value]
            else:
                out[key_str] = [str(value)]
        return out

    if isinstance(mapping, (list, tuple)):
        pair_like = all(
            isinstance(item, (list, tuple)) and len(item) == 2 for item in mapping
        )
        if pair_like:
            for parent, children in mapping:
                key = str(parent)
                if children is None:
                    out.setdefault(key, [])
                elif isinstance(children, (list, tuple, set)):
                    out.setdefault(key, []).extend(str(item) for item in children)
                else:
                    out.setdefault(key, []).append(str(children))
            return out
        if all(isinstance(item, (str, bytes)) for item in mapping):
            for node in mapping:
                out[str(node)] = []
            return out

    try:
        for item in mapping:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                key = str(item[0])
                value = item[1]
                if isinstance(value, (list, tuple, set)):
                    out.setdefault(key, []).extend(str(child) for child in value)
                else:
                    out.setdefault(key, []).append(str(value))
            elif isinstance(item, (str, bytes)):
                out.setdefault(str(item), [])
    except Exception:
        pass

    return out


def reachable_size(children_of: Any, start: str) -> int:
    """Return number of reachable distinct nodes, excluding start."""
    children_map = ensure_mapping(children_of)
    seen = set()
    queue = deque([start])
    while queue:
        current = queue.popleft()
        children = children_map.get(current, []) or []
        if isinstance(children, (str, bytes)):
            children = [children]
        for child in children:
            child_name = str(child)
            if child_name not in seen and child_name != start:
                seen.add(child_name)
                queue.append(child_name)
    return len(seen)
