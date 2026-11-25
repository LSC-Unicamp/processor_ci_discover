from unittest.mock import patch

from processor_discover.core.api import RunContext
from processor_discover.core.config_generator import try_incremental_approach


def test_requested_top_that_is_first_heuristic_has_both_roles(tmp_path):
    (tmp_path / "core.sv").write_text("module CpuCore; endmodule\n")
    context = RunContext(
        repo_root=tmp_path,
        repo_name="demo",
        top_module_override="CpuCore",
        config_path=tmp_path / "configs",
    )
    context.cache["heuristic_first_top"] = "CpuCore"
    with patch(
        "processor_discover.core.config_generator.verilator_incremental",
        return_value=(0, "lint passed", ["core.sv"], set(), "verilator --lint-only core.sv"),
    ):
        result = try_incremental_approach(
            str(tmp_path), "demo", ["CpuCore"], [("CpuCore", "core.sv")],
            {"CpuCore": []}, top_module_override="CpuCore", context=context,
        )

    assert result[-1] is True
    assert len(context.candidates) == 1
    assert context.candidates[0].roles == ["requested", "first_heuristic"]
    assert context.candidates[0].heuristic_rank == 1


def test_timeout_is_inconclusive_and_keeps_log(tmp_path):
    (tmp_path / "core.sv").write_text("module CpuCore; endmodule\n")
    context = RunContext(repo_root=tmp_path, repo_name="demo", config_path=tmp_path / "configs")
    with patch(
        "processor_discover.core.config_generator.verilator_incremental",
        return_value=(1, "[TIMEOUT] Process killed after 5s", ["core.sv"], set(), "verilator --lint-only core.sv"),
    ):
        try_incremental_approach(
            str(tmp_path), "demo", ["CpuCore"], [("CpuCore", "core.sv")],
            {"CpuCore": []}, context=context,
        )

    assert context.candidates[0].status == "inconclusive"
    assert "[TIMEOUT]" in (tmp_path / "configs/discovery/demo/logs/CpuCore_attempt-1.log").read_text()


def test_first_ranked_heuristic_is_recorded_even_if_it_looks_like_a_peripheral(tmp_path):
    for top in ("AXIWrapper", "CpuCore"):
        (tmp_path / f"{top}.sv").write_text(f"module {top}; endmodule\n")
    context = RunContext(repo_root=tmp_path, repo_name="demo", config_path=tmp_path / "config")
    context.cache["heuristic_first_top"] = "AXIWrapper"

    def compile_attempt(**kwargs):
        top = kwargs["top_module"]
        return (1 if top == "AXIWrapper" else 0, "error: lint failed" if top == "AXIWrapper" else "ok", [f"{top}.sv"], set(), f"verilator --top-module {top}")

    with patch("processor_discover.core.config_generator.verilator_incremental", side_effect=compile_attempt):
        try_incremental_approach(
            str(tmp_path), "demo", ["AXIWrapper", "CpuCore"],
            [("AXIWrapper", "AXIWrapper.sv"), ("CpuCore", "CpuCore.sv")],
            {"AXIWrapper": [], "CpuCore": []}, context=context,
        )

    assert context.candidates[0].top_module == "AXIWrapper"
    assert context.candidates[0].roles == ["first_heuristic"]
    assert context.candidates[0].status == "failure"


def test_missing_source_is_retained_without_claiming_a_command_ran(tmp_path):
    context = RunContext(repo_root=tmp_path, repo_name="demo", config_path=tmp_path / "config")
    context.cache["heuristic_first_top"] = "CpuCore"
    try_incremental_approach(
        str(tmp_path), "demo", ["CpuCore"], [("CpuCore", "missing.sv")],
        {"CpuCore": []}, context=context,
    )
    candidate = context.candidates[0]
    assert candidate.roles == ["first_heuristic"]
    assert candidate.files == ["missing.sv"]
    assert candidate.compile_command == ""
    assert "Missing source" in (tmp_path / "config/discovery/demo/logs/missing-source-1.log").read_text()
