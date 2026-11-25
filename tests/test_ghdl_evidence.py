from pathlib import Path
from unittest.mock import patch

from processor_discover.core.api import RunContext
from processor_discover.runners.ghdl_runner import incremental_compilation


def test_ghdl_preserves_failed_requested_attempt_and_first_heuristic(tmp_path):
    for name in ("Requested", "CpuCore"):
        (tmp_path / f"{name}.vhd").write_text(f"entity {name} is end entity;\n")
    config_dir = tmp_path / "configs"
    context = RunContext(
        repo_root=tmp_path,
        repo_name="demo",
        repository_url="https://example.invalid/demo.git",
        config_path=config_dir,
        top_module_override="Requested",
    )
    context.cache["heuristic_first_top"] = "CpuCore"

    def compile_attempt(_root, _repo, top, *_args, **_kwargs):
        if top == "Requested":
            return 1, "error: missing entity", ["Requested.vhd"], [], "ghdl -a Requested.vhd"
        return 0, "analysis and elaboration passed", ["CpuCore.vhd"], [], "ghdl -e CpuCore"

    with patch(
        "processor_discover.runners.ghdl_runner.compile_incremental",
        side_effect=compile_attempt,
    ):
        result = incremental_compilation(
            str(tmp_path), "demo", ["Requested", "CpuCore"],
            [("Requested", "Requested.vhd"), ("CpuCore", "CpuCore.vhd")],
            context=context,
        )

    assert result[0] is True and result[3] == "CpuCore"
    assert [candidate.roles for candidate in context.candidates] == [
        ["requested"], ["first_heuristic"],
    ]
    assert context.candidates[0].errors == ["error: missing entity"]
    assert Path(context.candidates[0].log_path).read_text() == "error: missing entity"
    assert (config_dir / "discovery/demo/candidates/demo_Requested_attempt1.json").is_file()


def test_ghdl_no_success_preserves_each_failure(tmp_path):
    for name in ("Requested", "CpuCore"):
        (tmp_path / f"{name}.vhd").write_text(f"entity {name} is end entity;\n")
    context = RunContext(
        repo_root=tmp_path, repo_name="demo", config_path=tmp_path / "configs",
        top_module_override="Requested",
    )
    context.cache["heuristic_first_top"] = "CpuCore"
    with patch(
        "processor_discover.runners.ghdl_runner.compile_incremental",
        side_effect=lambda _root, _repo, top, *_args, **_kwargs: (
            1, f"error: {top} failed", [f"{top}.vhd"], [], f"ghdl -a {top}.vhd"
        ),
    ):
        result = incremental_compilation(
            str(tmp_path), "demo", ["Requested", "CpuCore"],
            [("Requested", "Requested.vhd"), ("CpuCore", "CpuCore.vhd")],
            context=context,
        )

    assert result[0] is False
    assert len(context.candidates) == 2
    assert all(candidate.status == "failure" for candidate in context.candidates)
    assert len(list((tmp_path / "configs/discovery/demo/candidates").glob("*.json"))) == 2


def test_missing_requested_vhdl_entity_has_partial_result(tmp_path):
    (tmp_path / "CpuCore.vhd").write_text("entity CpuCore is end entity;\n")
    context = RunContext(
        repo_root=tmp_path, repo_name="demo", config_path=tmp_path / "configs",
        top_module_override="Missing",
    )
    context.cache["heuristic_first_top"] = "CpuCore"
    with patch(
        "processor_discover.runners.ghdl_runner.compile_incremental",
        return_value=(0, "ok", ["CpuCore.vhd"], [], "ghdl -e CpuCore"),
    ):
        result = incremental_compilation(
            str(tmp_path), "demo", ["Missing", "CpuCore"],
            [("CpuCore", "CpuCore.vhd")], context=context,
        )

    assert result[0] is True
    missing = context.candidates[0]
    assert missing.roles == ["requested"]
    assert missing.compile_command == "" and missing.exit_code is None
    assert Path(missing.log_path).is_file()
    assert (tmp_path / "configs/discovery/demo/candidates/demo_Missing_attempt1.json").is_file()
