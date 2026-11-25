import subprocess
import sys
import unittest

from processor_discover.cli import build_parser


class CliSmokeTests(unittest.TestCase):
    def test_help_runs(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "processor_discover.cli", "-h"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Generate processor configurations", result.stdout)
        self.assertIn("--top-module", result.stdout)

    def test_build_parser_defaults_and_flags(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "--processor-url",
                "https://example.invalid/repo.git",
                "--max-candidates",
                "5",
                "--top-module",
                "ExampleTop",
                "--core-name",
                "owner_repo",
            ]
        )

        self.assertEqual(args.processor_url, "https://example.invalid/repo.git")
        self.assertEqual(args.config_path, "config/")
        self.assertEqual(args.max_candidates, 5)
        self.assertEqual(args.top_module, "ExampleTop")
        self.assertEqual(args.core_name, "owner_repo")

    def test_build_parser_requires_processor_url(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args([])


if __name__ == "__main__":
    unittest.main()
