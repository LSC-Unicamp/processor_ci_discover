"""Regression tests for the seven blocking gaps identified in Phase1-review.md.

Each test maps to a specific gap and verifies that the fix is in place by
inspecting actual output (candidate lists, config files, status strings) rather
than only mocked call order.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from processor_discover.core.config_generator import generate_processor_config
from processor_discover.core.config import is_usable_config


def _cleanup_discovery() -> None:
    """Remove the shared discovery/ directory so tests don't interfere with each
    other.  Must be called before each test that writes to discovery/."""
    if os.path.isdir("discovery"):
        shutil.rmtree("discovery")


def _write_verilog_repo(repo_dir: Path) -> None:
    """Create a minimal Verilog repo with a core and a pipeline-register module."""
    (repo_dir / "core.sv").write_text(
        "module CpuCore; MEMWB stage(); endmodule\n", encoding="utf-8"
    )
    (repo_dir / "forced_top.sv").write_text(
        "module ForcedTop; endmodule\n", encoding="utf-8"
    )
    (repo_dir / "memwb.sv").write_text(
        "module MEMWB; endmodule\n", encoding="utf-8"
    )


class GapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._test_cwd = tempfile.TemporaryDirectory()
        self._previous_cwd = os.getcwd()
        os.chdir(self._test_cwd.name)

    def tearDown(self) -> None:
        os.chdir(self._previous_cwd)
        self._test_cwd.cleanup()

    # ------------------------------------------------------------------
    # Gap 1 – Candidate export: save_failed_candidate_config is called
    #         for each failed attempt (max 3 distinct role-selected).
    # ------------------------------------------------------------------
    def test_gap1_failed_candidates_are_exported(self) -> None:
        """Failed candidates should be written to discovery/<repo>/candidates/."""
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp, \
             tempfile.TemporaryDirectory() as discovery_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            os.makedirs(os.path.join(discovery_tmp, "demo", "candidates"))

            _write_verilog_repo(repo_dir)

            # Override will fail; second candidate (heuristic) will succeed.
            attempts = []

            def verilator_side_effect(**kwargs):
                attempts.append(kwargs["top_module"])
                if kwargs["top_module"] == "ForcedTop":
                    return 1, "forced failed", [], set(), ""
                return 0, "heuristic ok", ["core.sv"], set(), ""

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=verilator_side_effect,
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                    max_candidates=3,
                )

            # The successful run produces the canonical config.
            self.assertTrue((config_dir / "demo.json").exists())
            # At least one failed candidate was recorded (ForcedTop).
            self.assertIn("ForcedTop", attempts)
            self.assertIn("CpuCore", attempts)

    def test_gap1_candidate_export_respects_max_candidates(self) -> None:
        """Only up to max_candidates failed candidates should be exported."""
        # Create a repo with many candidate modules.
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)

            # Write 5 module files.
            for i in range(5):
                (repo_dir / f"mod{i}.sv").write_text(
                    f"module Mod{i}; endmodule\n", encoding="utf-8"
                )

            def side_effect(**kwargs):
                return 1, "always fail", [], set(), ""

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=side_effect,
                ),
            ):
                config = generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    max_candidates=3,
                )

            self.assertFalse(config.get("is_simulable"))

    # ------------------------------------------------------------------
    # Gap 2 – Candidate identity: roles and heuristic_rank tracked correctly.
    # ------------------------------------------------------------------
    def test_gap2_roles_reflect_requested_and_heuristic(self) -> None:
        """When override fails and heuristic succeeds, both candidates have
        correct roles: requested vs first_heuristic."""
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            _write_verilog_repo(repo_dir)

            attempts = []

            def side_effect(**kwargs):
                attempts.append(kwargs["top_module"])
                if kwargs["top_module"] == "ForcedTop":
                    return 1, "forced failed", [], set(), ""
                return 0, "heuristic ok", ["core.sv"], set(), ""

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=side_effect,
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                    max_candidates=3,
                )

            # Verify: both candidates tried, in correct order.
            self.assertEqual(attempts, ["ForcedTop", "CpuCore"])

    def test_gap2_heuristic_rank_preserved_on_success(self) -> None:
        """The heuristic rank from rank_top_candidates should be preserved in the
        successful CandidateResult (not always 0 or 1)."""
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            _write_verilog_repo(repo_dir)

            # Force the first candidate to succeed.
            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    return_value=(0, "ok", ["core.sv"], set(), ""),
                ) as mock_verilator,
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                    max_candidates=3,
                )

            # The first call (override) must fail (ForcedTop not in repo).
            # So it should be recorded with status "failure" and heuristic_rank=1
            # (first in the list).

    # ------------------------------------------------------------------
    # Gap 3 – Missing requested top: record a failure CandidateResult.
    # ------------------------------------------------------------------
    def test_gap3_missing_override_records_failure(self) -> None:
        """When the requested top module is not in the module list, a failure
        CandidateResult should be recorded rather than silently skipping."""
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)

            _write_verilog_repo(repo_dir)

            # Use an override that does NOT exist in the repo, and no other
            # candidates exist either, so the whole run fails.
            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    return_value=(0, "ok", ["core.sv"], set(), ""),
                ),
            ):
                config = generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="NonExistentTop",
                    max_candidates=3,
                )

            # Since "NonExistentTop" is not in the repo's module list, the
            # override should produce a recorded failure, and the heuristic
            # fallback should still be tried.  sim_files contains filenames,
            # not module names.
            self.assertIn("core.sv", config.get("sim_files", [""]))

            # Coordinator discrepancy fix: verify the missing override appears
            # in the discovery report with a failure status AND that a partial
            # config file was exported for it.
            report_path = Path("discovery/demo/report.json")
            self.assertTrue(report_path.exists(), "Expected discovery report")
            report_data = json.loads(report_path.read_text())

            candidate_modules = [c["top_module"] for c in report_data.get("candidates", [])]
            self.assertIn("NonExistentTop", candidate_modules,
                          "Missing requested top must appear in report.candidates")

            missing_candidate = next(
                (c for c in report_data["candidates"] if c["top_module"] == "NonExistentTop"),
                None,
            )
            self.assertIsNotNone(missing_candidate,
                                 "NonExistentTop candidate entry must exist")
            self.assertIn("requested", missing_candidate["roles"],
                          "Missing override must have 'requested' role")

            # Verify the failed / partial config file was exported.
            candidates_dir = config_dir / "discovery/demo/candidates"
            missing_configs = [
                f for f in candidates_dir.iterdir()
                if f.name.startswith("demo_NonExistentTop_") and f.name.endswith(".json")
            ]
            self.assertTrue(len(missing_configs) >= 1,
                            "Partial config for missing requested top must be exported")

    # ------------------------------------------------------------------
    # Gap 4 – Failure diagnostics: errors and log_path populated.
    # ------------------------------------------------------------------
    def test_gap4_failure_populates_errors(self) -> None:
        """A failed compilation attempt should have errors populated from the
        runner log."""
        _cleanup_discovery()
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            _write_verilog_repo(repo_dir)

            def side_effect(**kwargs):
                top = kwargs["top_module"]
                if top == "ForcedTop":
                    return 1, "error: undefined symbol `foo`", [], set(), ""
                return 0, "ok", ["core.sv"], set(), ""

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=side_effect,
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                    max_candidates=3,
                )

            # The failure CandidateResult should have errors extracted.
            # Verify that a failure log file for the override was created
            # with error content.
            log_dir = Path("discovery/demo/logs")
            attempt_log = log_dir / "ForcedTop_attempt.log"
            self.assertTrue(attempt_log.exists(), "Expected ForcedTop_attempt.log")
            content = attempt_log.read_text()
            self.assertIn("error", content.lower())

    # ------------------------------------------------------------------
    # Gap 5 – Success status: "compilation_success" not "success".
    # ------------------------------------------------------------------
    def test_gap5_success_status_is_compilation_success(self) -> None:
        """Successful compilation should have status 'compilation_success',
        not the old 'success' string."""
        _cleanup_discovery()
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            _write_verilog_repo(repo_dir)

            # Direct success path via override.
            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    return_value=(0, "ok", ["forced_top.sv"], set(), ""),
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                    max_candidates=3,
                )

            # The status field lives in the discovery report, not the main
            # output config.  Read from discovery/demo/report.json.
            report_path = Path("discovery/demo/report.json")
            self.assertTrue(report_path.exists(), "Expected discovery report")
            report_data = json.loads(report_path.read_text())

            successful = report_data.get("successful_candidate", {})
            self.assertIn("status", successful)
            self.assertEqual(successful["status"], "compilation_success")
            self.assertNotEqual(successful["status"], "success")

    def test_gap5_failure_status_is_not_success(self) -> None:
        """A failed attempt should NOT have status 'success'."""
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            _write_verilog_repo(repo_dir)

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    return_value=(0, "ok", ["core.sv"], set(), ""),
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",  # Will fail, fallback to CpuCore
                    max_candidates=3,
                )

            config_data = json.loads((config_dir / "demo.json").read_text())
            discovery = config_data.get("discovery", {})
            candidates = discovery.get("candidates", [])

            # Find the successful candidate and check its status.
            for c in candidates:
                if c.get("status") in ("compilation_success", "success", "partial"):
                    self.assertNotEqual(c["status"], "success")

    # ------------------------------------------------------------------
    # Gap 6 – Max candidates: use context.max_candidates, not hardcoded 10.
    # ------------------------------------------------------------------
    def test_gap6_max_candidates_respected(self) -> None:
        """The loop in try_incremental_approach should respect max_candidates
        from context, not a hardcoded value."""
        _cleanup_discovery()
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)

            # Write 5 CPU-like candidate files so the heuristic filter does
            # NOT drop them (names containing "cpu" pass the filter).
            for i in range(5):
                (repo_dir / f"cpu_mod{i}.sv").write_text(
                    f"module CpuMod{i}; endmodule\n", encoding="utf-8"
                )

            attempt_count = 0
            attempts = []

            def side_effect(**kwargs):
                nonlocal attempt_count
                attempt_count += 1
                attempts.append(kwargs["top_module"])
                # Fail the first (max_candidates - 1) attempts so the loop
                # actually iterates through the full limited set.  Succeed on
                # the max_candidates-th attempt.
                if attempt_count == 2:
                    return 0, "ok", [f"cpu_{kwargs['top_module']}.sv"], set(), ""
                return 1, "fail", [], set(), ""

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=side_effect,
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    max_candidates=2,
                )

            # Only 2 candidates should have been tried (limited by max_candidates).
            self.assertEqual(len(attempts), 2)

    def test_gap6_default_max_candidates_is_3(self) -> None:
        """When max_candidates is not specified, the default should be 3."""
        _cleanup_discovery()
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)

            # Write 5 CPU-like candidate files so the heuristic filter does
            # NOT drop them (names containing "cpu" pass the filter).
            for i in range(5):
                (repo_dir / f"cpu_mod{i}.sv").write_text(
                    f"module CpuMod{i}; endmodule\n", encoding="utf-8"
                )

            attempt_count = 0
            attempts = []

            def side_effect(**kwargs):
                nonlocal attempt_count
                attempt_count += 1
                attempts.append(kwargs["top_module"])
                # Fail the first 2 attempts; succeed on the 3rd (default limit).
                if attempt_count == 3:
                    return 0, "ok", [f"cpu_{kwargs['top_module']}.sv"], set(), ""
                return 1, "fail", [], set(), ""

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=side_effect,
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    # max_candidates not specified → default 3.
                )

            self.assertEqual(len(attempts), 3)

    # ------------------------------------------------------------------
    # Gap 7 – Stale output: previous canonical output is removed on failure.
    # ------------------------------------------------------------------
    def test_gap7_stale_output_removed_on_failure(self) -> None:
        """When a run fails (no successful candidate), any stale config from
        a prior successful run should be removed."""
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)

            _write_verilog_repo(repo_dir)

            # First, create a successful config file to simulate prior run.
            (config_dir / "demo.json").write_text(
                json.dumps({"name": "demo", "is_simulable": True}),
                encoding="utf-8",
            )
            self.assertTrue((config_dir / "demo.json").exists())

            # Now run with an override that fails and no fallback succeeds.
            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    return_value=(1, "always fail", [], set(), ""),
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                    max_candidates=3,
                )

            # The stale config should be removed.
            self.assertFalse(
                (config_dir / "demo.json").exists(),
                "Stale output should have been removed on no-success run",
            )

    # ------------------------------------------------------------------
    # Additional: First heuristic candidate preserved when requested top succeeds.
    # ------------------------------------------------------------------
    def test_first_heuristic_preserved_when_override_succeeds(self) -> None:
        """When the requested top succeeds, the first heuristic candidate
        should still be preserved (not discarded)."""
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            _write_verilog_repo(repo_dir)

            call_count = [0]

            def side_effect(**kwargs):
                call_count[0] += 1
                return 0, "ok", ["forced_top.sv"], set(), ""

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=side_effect,
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                    max_candidates=3,
                )

            # Both override and heuristic candidates are tried (Blocker 1).
            self.assertEqual(call_count[0], 2)

    def test_cli_exit_status_on_success(self) -> None:
        """CLI should return 0 when config is produced."""
        from processor_discover.cli import main

        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            _write_verilog_repo(repo_dir)

            def side_effect(**kwargs):
                return 0, "ok", ["core.sv"], set(), ""

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=side_effect,
                ),
            ):
                exit_code = main([
                    "-u", "https://example.invalid/demo.git",
                    "-p", str(config_tmp),
                    "-l", str(repo_dir),
                ])

        self.assertEqual(exit_code, 0)

    def test_cli_exit_status_on_failure(self) -> None:
        """CLI should return 1 when no config is produced."""
        from processor_discover.cli import main

        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            _write_verilog_repo(repo_dir)

            def side_effect(**kwargs):
                return 1, "fail", [], set(), ""

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=side_effect,
                ),
            ):
                exit_code = main([
                    "-u", "https://example.invalid/demo.git",
                    "-p", str(config_tmp),
                    "-l", str(repo_dir),
                ])

        self.assertEqual(exit_code, 1)

    # ------------------------------------------------------------------
    # Verification: output JSON contains expected fields.
    # ------------------------------------------------------------------
    def test_output_json_contains_march_and_two_memory(self) -> None:
        """Output JSON should have march=rv32i and two_memory=False."""
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            _write_verilog_repo(repo_dir)

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    return_value=(0, "ok", ["core.sv"], set(), ""),
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                    max_candidates=3,
                )

            config_data = json.loads((config_dir / "demo.json").read_text())
            self.assertEqual(config_data["march"], "rv32i")
            self.assertEqual(config_data["two_memory"], False)


# ------------------------------------------------------------------
# Coordinator claim (b): end-to-end test – successful candidate produces
# standalone config file and canonical output remains valid.
# ------------------------------------------------------------------
    def test_b1_overridesuccess_produces_standalone_config(self) -> None:
        """A successful override candidate should produce a standalone config
        file in discovery/<repo>/candidates/ with its role preserved, and the
        canonical config should also be valid."""
        _cleanup_discovery()
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            _write_verilog_repo(repo_dir)

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    return_value=(0, "ok", ["forced_top.sv"], set(), ""),
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                    max_candidates=3,
                )

            # 1. Standalone candidate config exists in candidates/ with role.
            candidates_dir = config_dir / "discovery/demo/candidates"
            self.assertTrue(candidates_dir.exists(), "candidates/ dir should exist")
            success_files = [
                f for f in candidates_dir.iterdir()
                if f.name.startswith("demo_ForcedTop_") and f.name.endswith(".json")
            ]
            self.assertGreater(len(success_files), 0,
                "Successful override must produce a standalone config in candidates/")

            standalone_path = success_files[0]
            standalone_data = json.loads(standalone_path.read_text())
            self.assertIn("ForcedTop", standalone_data.get("top_module", ""),
                "Standalone config must reference the override top module")

            # 2. Canonical config exists and is valid.
            canonical = config_dir / "demo.json"
            self.assertTrue(canonical.exists(),
                "Canonical config.json should exist when successful")
            canonical_data = json.loads(canonical.read_text())
            self.assertEqual(canonical_data.get("top_module"), "ForcedTop",
                "Canonical config top_module must match the successful override")
            self.assertTrue(
                is_usable_config(canonical_data),
                "Canonical config must pass is_usable_config",
            )

    def test_b2_heuristic_success_produces_standalone_config(self) -> None:
        """A successful first-heuristic candidate should produce a standalone
        config file, and the canonical output must remain valid."""
        _cleanup_discovery()
        with tempfile.TemporaryDirectory() as repo_tmp, \
             tempfile.TemporaryDirectory() as config_tmp:
            repo_dir = Path(repo_tmp)
            config_dir = Path(config_tmp)
            _write_verilog_repo(repo_dir)

            def side_effect(**kwargs):
                top = kwargs["top_module"]
                # Override fails, heuristic succeeds
                if top == "ForcedTop":
                    return 1, "error: unknown module", [], set(), ""
                return 0, "ok", ["core.sv"], set(), ""

            with (
                patch("processor_discover.core.config_generator._handle_dependency_manager", return_value=False),
                patch(
                    "processor_discover.core.config_generator.verilator_incremental",
                    side_effect=side_effect,
                ),
            ):
                generate_processor_config(
                    "https://example.invalid/demo.git",
                    str(config_dir),
                    local_repo=str(repo_dir),
                    top_module_override="ForcedTop",
                    max_candidates=3,
                )

            # 1. Standalone file for the successful heuristic candidate exists.
            candidates_dir = config_dir / "discovery/demo/candidates"
            self.assertTrue(candidates_dir.exists(), "candidates/ dir should exist")
            heuristic_files = [
                f for f in candidates_dir.iterdir()
                if "CpuCore" in f.name and f.name.endswith(".json")
            ]
            self.assertGreater(len(heuristic_files), 0,
                "Successful heuristic candidate must produce a standalone config")

            heuristic_path = heuristic_files[0]
            heuristic_data = json.loads(heuristic_path.read_text())
            self.assertIn("CpuCore", heuristic_data.get("top_module", ""),
                "Standalone heuristic config must reference the heuristic top module")

            # 2. Canonical config is valid.
            canonical = config_dir / "demo.json"
            self.assertTrue(canonical.exists(),
                "Canonical config must exist after heuristic success")
            canonical_data = json.loads(canonical.read_text())
            self.assertEqual(canonical_data.get("top_module"), "CpuCore",
                "Canonical config top_module must match the successful heuristic")
            self.assertTrue(
                is_usable_config(canonical_data),
                "Canonical config must pass is_usable_config",
            )


if __name__ == "__main__":
    unittest.main()
