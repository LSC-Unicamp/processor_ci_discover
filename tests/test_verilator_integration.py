"""A real Verilator failure must leave usable diagnostic evidence."""

import json
import shutil
from pathlib import Path

import pytest

from processor_discover.core.config_generator import generate_processor_config


@pytest.mark.skipif(shutil.which("verilator") is None, reason="Verilator is unavailable")
def test_real_verilator_failure_preserves_source_set_and_log(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CpuCore.sv").write_text("module CpuCore; missing_module child(); endmodule\n")
    output = tmp_path / "configs"
    generate_processor_config(
        "https://example.invalid/demo.git", str(output),
        local_repo=str(repo), top_module_override="CpuCore", max_candidates=2,
    )
    report = json.loads((output / "discovery/demo/report.json").read_text())
    failed = report["candidates"][0]
    assert failed["status"] == "failure"
    assert failed["files"] == ["CpuCore.sv"]
    assert failed["compile_command"][:2] == ["verilator", "--lint-only"]
    assert "error" in Path(failed["log_file"]).read_text().lower()
    assert not (output / "demo.json").exists()
