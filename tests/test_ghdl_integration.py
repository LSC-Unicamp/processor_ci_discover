"""A small real-tool check for the VHDL configuration gate."""

import json
import shutil
from pathlib import Path

import pytest

from processor_discover.core.config_generator import generate_processor_config


@pytest.mark.skipif(shutil.which("ghdl") is None, reason="GHDL is unavailable")
def test_real_ghdl_analyze_and_elaborate(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CpuCore.vhd").write_text(
        "entity CpuCore is end entity; architecture rtl of CpuCore is begin end architecture;\n"
    )
    output = tmp_path / "configs"

    generate_processor_config(
        "https://example.invalid/demo.git", str(output),
        local_repo=str(repo), top_module_override="CpuCore", max_candidates=2,
    )

    assert (output / "demo.json").is_file()
    report = json.loads((output / "discovery/demo/report.json").read_text())
    candidate = report["successful_candidate"]
    assert candidate["status"] == "compilation_success"
    assert candidate["compile_command"][:2] == ["ghdl", "-e"]
    assert (output / "discovery/demo/logs/ghdl-attempt-1.log").is_file()


@pytest.mark.skipif(shutil.which("ghdl") is None, reason="GHDL is unavailable")
def test_real_ghdl_failure_preserves_source_set_and_log(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CpuCore.vhd").write_text(
        "entity CpuCore is end entity; architecture rtl of CpuCore is begin broken_token; end architecture;\n"
    )
    output = tmp_path / "configs"
    generate_processor_config(
        "https://example.invalid/demo.git", str(output),
        local_repo=str(repo), top_module_override="CpuCore", max_candidates=2,
    )
    report = json.loads((output / "discovery/demo/report.json").read_text())
    failed = report["candidates"][0]
    assert failed["status"] == "failure"
    assert failed["files"] == ["CpuCore.vhd"]
    assert failed["compile_command"][:2] == ["ghdl", "-a"]
    assert "error" in Path(failed["log_file"]).read_text().lower()
    assert not (output / "demo.json").exists()
