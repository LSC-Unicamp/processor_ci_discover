import json
from unittest.mock import patch

from processor_discover.core.config_generator import generate_processor_config


def test_fallback_result_updates_validation_block_before_save(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CpuCore.sv").write_text("module CpuCore; endmodule\n")
    config_dir = tmp_path / "config"

    with patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False), patch(
        "processor_discover.core.config_generator.interactive_simulate_and_minimize",
        return_value=(["CpuCore.sv"], set(), "lint passed", "CpuCore", True),
    ):
        result = generate_processor_config(
            "https://example.invalid/demo.git", str(config_dir), local_repo=str(repo),
        )

    assert result["discovery"]["validation_gate"]["passed"] is True
    assert result["discovery"]["defaults"] == {"march": "default", "two_memory": "default"}
    assert (config_dir / "demo.json").is_file()
    report = json.loads((config_dir / "discovery/demo/report.json").read_text())
    assert report["successful_candidate"]["top_module"] == "CpuCore"


def test_requested_top_is_reported_when_checkout_has_no_hdl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    config_dir = tmp_path / "config"
    with patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False):
        generate_processor_config(
            "https://example.invalid/demo.git", str(config_dir),
            local_repo=str(repo), top_module_override="MissingCpu",
        )
    report = json.loads((config_dir / "discovery/demo/report.json").read_text())
    assert report["candidates"][0]["top_module"] == "MissingCpu"
    assert report["candidates"][0]["roles"] == ["requested"]
    assert not (config_dir / "demo.json").exists()
