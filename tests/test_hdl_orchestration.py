import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from processor_discover.core.config_generator import generate_processor_config
from processor_discover.runners.verilator_runner import _detect_language_for_files


@contextmanager
def temporary_cwd():
    previous = os.getcwd()
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        try:
            yield Path(tmpdir)
        finally:
            os.chdir(previous)


def write_verilog_repo(repo_dir: Path) -> None:
    (repo_dir / "heuristic_core.sv").write_text(
        "module HeuristicCore; ALU alu(); endmodule\n",
        encoding="utf-8",
    )
    (repo_dir / "forced_top.sv").write_text(
        "module ForcedTop; endmodule\n",
        encoding="utf-8",
    )
    (repo_dir / "alu.sv").write_text(
        "module ALU; endmodule\n",
        encoding="utf-8",
    )


def write_vhdl_repo(repo_dir: Path) -> None:
    (repo_dir / "heuristic_core.vhd").write_text(
        "entity HeuristicCore is\nend entity;\n"
        "architecture rtl of HeuristicCore is begin end architecture;\n",
        encoding="utf-8",
    )
    (repo_dir / "forced_top.vhd").write_text(
        "entity ForcedTop is\nend entity;\n"
        "architecture rtl of ForcedTop is begin end architecture;\n",
        encoding="utf-8",
    )


class HdlOrchestrationTests(unittest.TestCase):
    def test_concurrent_assertion_in_v_file_uses_systemverilog(self) -> None:
        with tempfile.TemporaryDirectory() as repo_tmp:
            source = Path(repo_tmp) / "assertions.v"
            source.write_text(
                "module core(input clk); assert property (@(posedge clk) 1'b1); endmodule\n",
                encoding="utf-8",
            )
            language = _detect_language_for_files(repo_tmp, ["assertions.v"])

        self.assertEqual(language, "1800-2023")

    def test_missing_local_repo_does_not_fall_back_to_clone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "already-cloned-core"
            with patch(
                "processor_discover.core.config_generator.clone_and_validate_repo"
            ) as clone:
                with self.assertRaisesRegex(FileNotFoundError, str(missing)):
                    generate_processor_config(
                        "https://example.invalid/demo.git",
                        tmpdir,
                        no_llama=True,
                        local_repo=str(missing),
                    )

            clone.assert_not_called()

    def test_failed_cpu_top_does_not_fall_back_to_pipeline_register(self) -> None:
        attempts = []

        def verilator_side_effect(**kwargs):
            attempts.append(kwargs["top_module"])
            return 1, "cpu failed", [], set()

        with (
            tempfile.TemporaryDirectory() as repo_tmp,
            tempfile.TemporaryDirectory() as config_tmp,
            temporary_cwd(),
        ):
            repo_dir = Path(repo_tmp)
            (repo_dir / "core.sv").write_text(
                "module CpuCore; MEMWB stage(); endmodule\n", encoding="utf-8"
            )
            (repo_dir / "memwb.sv").write_text(
                "module MEMWB; endmodule\n", encoding="utf-8"
            )

            with (
                patch(
                    "processor_discover.core.config_generator.handle_dependency_manager",
                    return_value=False,
                ),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=verilator_side_effect,
                ),
            ):
                config = generate_processor_config(
                    "https://example.invalid/demo.git",
                    config_tmp,
                    no_llama=True,
                    local_repo=str(repo_dir),
                )

        self.assertEqual(attempts, ["CpuCore"])
        self.assertFalse(config["is_simulable"])
        self.assertNotEqual(config["top_module"], "MEMWB")

    def test_valid_verilog_override_is_first_candidate(self) -> None:
        with (
            tempfile.TemporaryDirectory() as repo_tmp,
            tempfile.TemporaryDirectory() as config_tmp,
            temporary_cwd(),
        ):
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            write_verilog_repo(repo_dir)

            with (
                patch(
                    "processor_discover.core.config_generator.handle_dependency_manager",
                    return_value=False,
                ),
                patch(
                    "processor_discover.runners.verilator_runner.compile_incremental",
                    side_effect=AssertionError(
                        "direct runner import should not be used"
                    ),
                ),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    return_value=(0, "ok", ["forced_top.sv"], {"include"}),
                ) as verilator_incremental,
            ):
                config = generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    no_llama=True,
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                )

        call_kwargs = verilator_incremental.call_args.kwargs
        self.assertEqual(call_kwargs["top_module"], "ForcedTop")
        self.assertEqual(config["top_module"], "ForcedTop")
        self.assertEqual(config["sim_files"], ["forced_top.sv"])
        self.assertEqual(config["include_dirs"], ["include"])
        self.assertEqual(config["name"], "demo")
        self.assertEqual(config["repository"], "https://example.invalid/demo.git")
        self.assertEqual(config["language_version"], "1800-2023")
        self.assertTrue(config["is_simulable"])

    def test_override_failure_falls_back_to_heuristic_candidate(self) -> None:
        attempts = []

        def verilator_side_effect(**kwargs):
            attempts.append(kwargs["top_module"])
            if kwargs["top_module"] == "ForcedTop":
                return 1, "forced failed", [], set()
            return 0, "heuristic ok", ["heuristic_core.sv"], set()

        with (
            tempfile.TemporaryDirectory() as repo_tmp,
            tempfile.TemporaryDirectory() as config_tmp,
            temporary_cwd(),
        ):
            repo_dir = Path(repo_tmp)
            write_verilog_repo(repo_dir)

            with (
                patch(
                    "processor_discover.core.config_generator.handle_dependency_manager",
                    return_value=False,
                ),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=verilator_side_effect,
                ),
            ):
                config = generate_processor_config(
                    "https://example.invalid/demo.git",
                    config_tmp,
                    no_llama=True,
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                )

        self.assertEqual(attempts, ["ForcedTop", "HeuristicCore"])
        self.assertEqual(config["top_module"], "HeuristicCore")
        self.assertEqual(config["sim_files"], ["heuristic_core.sv"])
        self.assertTrue(config["is_simulable"])

    def test_missing_override_falls_back_to_ranked_candidate(self) -> None:
        attempts = []

        def verilator_side_effect(**kwargs):
            attempts.append(kwargs["top_module"])
            return 0, "ok", ["heuristic_core.sv"], set()

        with (
            tempfile.TemporaryDirectory() as repo_tmp,
            tempfile.TemporaryDirectory() as config_tmp,
            temporary_cwd(),
        ):
            repo_dir = Path(repo_tmp)
            write_verilog_repo(repo_dir)

            with (
                patch(
                    "processor_discover.core.config_generator.handle_dependency_manager",
                    return_value=False,
                ),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=verilator_side_effect,
                ),
            ):
                config = generate_processor_config(
                    "https://example.invalid/demo.git",
                    config_tmp,
                    no_llama=True,
                    local_repo=str(repo_dir),
                    top_module_override="MissingTop",
                )
                config_file_exists = (Path(config_tmp) / "demo.json").exists()

        self.assertEqual(attempts, ["HeuristicCore"])
        self.assertEqual(config["top_module"], "HeuristicCore")
        self.assertTrue(config_file_exists)

    def test_vhdl_override_uses_ghdl(self) -> None:
        with (
            tempfile.TemporaryDirectory() as repo_tmp,
            tempfile.TemporaryDirectory() as config_tmp,
            temporary_cwd(),
        ):
            repo_dir = Path(repo_tmp)
            write_vhdl_repo(repo_dir)

            with (
                patch(
                    "processor_discover.core.config_generator.handle_dependency_manager",
                    return_value=False,
                ),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=AssertionError(
                        "Verilator should not be used for VHDL top"
                    ),
                ),
                patch(
                    "processor_discover.core.config_generator.ghdl_incremental",
                    return_value=(True, "ghdl ok", ["forced_top.vhd"], "ForcedTop"),
                ) as ghdl_incremental,
            ):
                config = generate_processor_config(
                    "https://example.invalid/demo.git",
                    config_tmp,
                    no_llama=True,
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                )

        call_kwargs = ghdl_incremental.call_args.kwargs
        self.assertEqual(call_kwargs["top_candidates"][0], "ForcedTop")
        self.assertEqual(call_kwargs["top_module_override"], "ForcedTop")
        self.assertEqual(config["top_module"], "ForcedTop")
        self.assertEqual(config["sim_files"], ["forced_top.vhd"])
        self.assertTrue(config["is_simulable"])

    def test_no_llama_does_not_call_ollama_helpers(self) -> None:
        with (
            tempfile.TemporaryDirectory() as repo_tmp,
            tempfile.TemporaryDirectory() as config_tmp,
            temporary_cwd(),
        ):
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            write_verilog_repo(repo_dir)

            with (
                patch(
                    "processor_discover.core.config_generator.handle_dependency_manager",
                    return_value=False,
                ),
                patch(
                    "processor_discover.core.pipeline.get_filtered_files_list",
                    side_effect=AssertionError(
                        "get_filtered_files_list should not be called"
                    ),
                ),
                patch(
                    "processor_discover.core.pipeline.get_top_module",
                    side_effect=AssertionError("get_top_module should not be called"),
                ),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    return_value=(0, "ok", ["heuristic_core.sv"], set()),
                ) as verilator_incremental,
            ):
                config = generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    no_llama=True,
                    local_repo=str(repo_dir),
                )

            written_config = json.loads(
                (config_dir / "demo.json").read_text(encoding="utf-8")
            )

        call_kwargs = verilator_incremental.call_args.kwargs
        self.assertEqual(call_kwargs["top_module"], "HeuristicCore")
        self.assertEqual(config["top_module"], "HeuristicCore")
        self.assertEqual(written_config["top_module"], "HeuristicCore")
        self.assertEqual(
            written_config["repository"], "https://example.invalid/demo.git"
        )


if __name__ == "__main__":
    unittest.main()
