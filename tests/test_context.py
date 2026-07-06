import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from processor_ci_discover.core.api import RunContext
from processor_ci_discover.core.config_generator import (
    interactive_simulate_and_minimize,
)
from processor_ci_discover.utils.runtime import build_run_context


class RunContextTests(unittest.TestCase):
    def test_to_dict_normalizes_paths(self) -> None:
        context = RunContext(
            repo_root="/tmp/example",
            repo_name="example",
            repository_url="https://example.invalid/repo.git",
            config_path="config/out",
            local_repo="/tmp/example",
            model="test-model",
            plot_graph=True,
            add_to_config=True,
            language_version="1800-2023",
            include_dirs=["inc", Path("rtl/include")],
            module_files=["rtl/top.sv"],
            top_module_override="top",
            no_llama=True,
            maximize_attempts=4,
        )

        as_dict = context.to_dict()

        self.assertEqual(as_dict["repo_root"], "/tmp/example")
        self.assertEqual(as_dict["repo_name"], "example")
        self.assertEqual(as_dict["repository_url"], "https://example.invalid/repo.git")
        self.assertEqual(as_dict["config_path"], "config/out")
        self.assertEqual(as_dict["local_repo"], "/tmp/example")
        self.assertEqual(as_dict["model"], "test-model")
        self.assertTrue(as_dict["plot_graph"])
        self.assertTrue(as_dict["add_to_config"])
        self.assertEqual(as_dict["language_version"], "1800-2023")
        self.assertEqual(as_dict["include_dirs"], ["inc", "rtl/include"])
        self.assertEqual(as_dict["module_files"], ["rtl/top.sv"])
        self.assertEqual(as_dict["top_module_override"], "top")
        self.assertTrue(as_dict["no_llama"])
        self.assertEqual(as_dict["maximize_attempts"], 4)

    def test_build_run_context_populates_runtime_options(self) -> None:
        context = build_run_context(
            repo_root="/tmp/example",
            repo_name="example",
            repository_url="https://example.invalid/repo.git",
            config_path="config",
            local_repo="/tmp/local",
            model="small-model",
            plot_graph=True,
            add_to_config=True,
            include_dirs=["inc"],
            module_files=["rtl/top.sv"],
        )

        self.assertEqual(context.repo_root, Path("/tmp/example"))
        self.assertEqual(context.config_path, Path("config"))
        self.assertEqual(context.local_repo, Path("/tmp/local"))
        self.assertEqual(context.model, "small-model")
        self.assertTrue(context.plot_graph)
        self.assertTrue(context.add_to_config)
        self.assertEqual(context.include_dirs, [Path("inc")])
        self.assertEqual(context.module_files, [Path("rtl/top.sv")])

    def test_interactive_flow_uses_context_top_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "forced.sv").write_text("module forced; endmodule\n")
            (repo_root / "heuristic.sv").write_text("module heuristic; endmodule\n")

            context = RunContext(
                repo_root=repo_root,
                repo_name="example",
                repository_url="https://example.invalid/repo.git",
                top_module_override="forced",
                maximize_attempts=2,
            )

            with (
                patch(
                    "processor_ci_discover.core.config_generator.rank_top_candidates",
                    return_value=(["heuristic"], []),
                ),
                patch(
                    "processor_ci_discover.core.config_generator.verilator_incremental",
                    return_value=(0, "ok", ["forced.sv"], {"inc"}),
                ) as verilator_incremental,
            ):
                final_files, final_includes, last_log, top_module, is_simulable = (
                    interactive_simulate_and_minimize(
                        repo_root=str(repo_root),
                        repo_name="example",
                        url="https://example.invalid/repo.git",
                        tb_files=[],
                        candidate_files=["forced.sv", "heuristic.sv"],
                        include_dirs=set(),
                        modules=[
                            ("forced", "forced.sv"),
                            ("heuristic", "heuristic.sv"),
                        ],
                        module_graph={},
                        module_graph_inverse={},
                        language_version="1800-2023",
                        context=context,
                    )
                )

            self.assertEqual(final_files, ["forced.sv"])
            self.assertEqual(final_includes, {"inc"})
            self.assertEqual(last_log, "ok")
            self.assertEqual(top_module, "forced")
            self.assertTrue(is_simulable)
            call_kwargs = verilator_incremental.call_args.kwargs
            self.assertEqual(call_kwargs["top_module"], "forced")
            self.assertEqual(call_kwargs["max_iterations"], 2)
            self.assertIs(call_kwargs["context"], context)


if __name__ == "__main__":
    unittest.main()
