import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from processor_discover.core.config_generator import generate_processor_config
from processor_discover.core.top_selection import rank_top_candidates
from processor_discover.lang.bluespec_manager import (
    find_top_module as find_bluespec_top_module,
)
from processor_discover.lang.chisel_manager import (
    find_top_module as find_chisel_top_module,
)


class TopSelectionRegressionTests(unittest.TestCase):
    def test_core_ranking_prefers_cpu_core_over_peripheral_wrapper(self) -> None:
        module_graph = {
            "SoC": ["CpuCore", "uart"],
            "CpuCore": ["ALU"],
            "ALU": [],
            "uart": [],
        }
        module_graph_inverse = {
            "SoC": [],
            "CpuCore": ["SoC"],
            "ALU": ["CpuCore"],
            "uart": ["SoC"],
        }
        modules = [(name, f"{name}.sv") for name in module_graph]

        candidates, _ = rank_top_candidates(
            module_graph,
            module_graph_inverse,
            repo_name="demo",
            modules=modules,
        )

        self.assertEqual(candidates[0], "CpuCore")

    def test_chisel_top_selection_keeps_core_choice(self) -> None:
        module_graph = {
            "Top": ["Core", "Uart"],
            "Core": ["ALU"],
            "ALU": [],
            "Uart": [],
        }
        module_graph_inverse = {
            "Top": [],
            "Core": ["Top"],
            "ALU": ["Core"],
            "Uart": ["Top"],
        }

        top_module = find_chisel_top_module(
            module_graph,
            module_graph_inverse,
            [(name, f"{name}.scala") for name in module_graph],
            repo_name="rocket",
        )

        self.assertEqual(top_module, "Core")

    def test_bluespec_top_selection_keeps_core_choice(self) -> None:
        module_graph = {
            "mkTop": ["mkCore", "mkUart"],
            "mkCore": ["mkALU"],
            "mkALU": [],
            "mkUart": [],
        }
        module_graph_inverse = {
            "mkTop": [],
            "mkCore": ["mkTop"],
            "mkALU": ["mkCore"],
            "mkUart": ["mkTop"],
        }

        top_module = find_bluespec_top_module(
            module_graph,
            module_graph_inverse,
            [(name, f"/repo/Core/{name}.bsv") for name in module_graph],
            repo_name="rocket",
        )

        self.assertEqual(top_module, "mkCore")

    def test_bluespec_add_to_config_uses_save_config(self) -> None:
        with (
            tempfile.TemporaryDirectory() as repo_dir,
            tempfile.TemporaryDirectory() as config_dir,
        ):
            with (
                patch(
                    "processor_discover.core.config_generator.handle_dependency_manager",
                    return_value=False,
                ),
                patch(
                    "processor_discover.core.config_generator.find_and_log_files",
                    return_value=(["Core.bsv"], ".bsv"),
                ),
                patch(
                    "processor_discover.core.config_generator.process_bluespec_project",
                    return_value={"name": "demo", "top_module": "mkCore"},
                ),
                patch(
                    "processor_discover.core.config_generator.save_config",
                ) as save_config,
            ):
                config = generate_processor_config(
                    "https://example.invalid/demo.git",
                    config_dir,
                    add_to_config=True,
                    local_repo=repo_dir,
                )

        self.assertEqual(config["repository"], "https://example.invalid/demo.git")
        save_config.assert_called_once()
        central_config_path, saved_config, processor_name = save_config.call_args.args
        self.assertEqual(central_config_path, str(Path(config_dir) / "config.json"))
        self.assertEqual(saved_config["top_module"], "mkCore")
        self.assertEqual(processor_name, "demo")


if __name__ == "__main__":
    unittest.main()
