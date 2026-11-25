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
