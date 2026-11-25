from processor_discover.core.config import is_usable_config
from processor_discover.core.config_generator import normalize_generated_config
from subprocess import CompletedProcess
from unittest.mock import patch


def test_usable_config_requires_positive_validation_evidence():
    config = {
        "schema_version": "2.0",
        "top_module": "CpuCore",
        "sim_files": ["rtl/core.sv"],
    }
    assert not is_usable_config(config)

    config["discovery"] = {"has_successful_candidate": True, "validation_gate": {"passed": False}}
    assert not is_usable_config(config)

    config["discovery"]["validation_gate"]["passed"] = True
    assert is_usable_config(config)


def test_generated_config_gets_compatible_defaults_only_after_success(tmp_path):
    (tmp_path / "core.v").write_text("module CpuCore; endmodule\n")
    with patch("processor_discover.core.config_generator.shutil.which", return_value="/usr/bin/verilator"), patch(
        "processor_discover.core.config_generator.subprocess.run",
        return_value=CompletedProcess([], 0, "", ""),
    ):
        generated = normalize_generated_config({
            "top_module": "CpuCore", "files": ["core.v"], "is_simulable": True,
        }, str(tmp_path))
    assert generated["sim_files"] == ["core.v"]
    assert generated["march"] == "rv32i"
    assert generated["two_memory"] is False
    assert is_usable_config(generated)

    failed = normalize_generated_config({
        "top_module": "CpuCore", "files": [], "is_simulable": False,
    })
    assert not is_usable_config(failed)
    assert failed["discovery"]["candidate_status"] == "failure"
