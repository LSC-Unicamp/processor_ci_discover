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
                "--no-llama",
                "--top-module",
                "ExampleTop",
            ]
        )

        self.assertEqual(args.processor_url, "https://example.invalid/repo.git")
        self.assertEqual(args.config_path, "config/")
        self.assertTrue(args.no_llama)
        self.assertEqual(args.top_module, "ExampleTop")

    def test_build_parser_requires_processor_url(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args([])


if __name__ == "__main__":
    unittest.main()
