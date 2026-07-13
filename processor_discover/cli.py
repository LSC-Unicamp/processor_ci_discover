"""CLI entrypoint for processor CI config discovery."""

import argparse
import json
import os
import shutil

from processor_discover.utils.log import print_red


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for config discovery."""
    parser = argparse.ArgumentParser(description="Generate processor configurations")

    parser.add_argument(
        "-u",
        "--processor-url",
        type=str,
        required=True,
        help="URL of the processor repository",
    )
    parser.add_argument(
        "-p",
        "--config-path",
        type=str,
        default="config/",
        help="Path to save the configuration file",
    )
    parser.add_argument(
        "-g",
        "--plot-graph",
        action="store_true",
        help="Plot the module dependency graph",
    )
    parser.add_argument(
        "-a",
        "--add-to-config",
        action="store_true",
        help="Add the generated configuration to a central config file",
    )
    parser.add_argument(
        "-n",
        "--no-llama",
        action="store_true",
        help="Skip OLLAMA processing for top module identification",
    )
    parser.add_argument(
        "-m", "--model", type=str, default="qwen2.5:32b", help="OLLAMA model to use"
    )
    parser.add_argument(
        "-l",
        "--local-repo",
        type=str,
        default=None,
        help="Path to local repository (skips cloning if provided)",
    )
    parser.add_argument(
        "-t",
        "--top-module",
        type=str,
        default=None,
        help="Force a specific top module (tried first, then falls back to heuristics on failure)",
    )
    parser.add_argument(
        "--core-name",
        type=str,
        default=None,
        help="Override config and clone folder name (owner_repo is recommended for collisions)",
    )
    parser.add_argument(
        "--top-file",
        type=str,
        default=None,
        help="Select the source file when multiple files declare the requested top module",
    )
    parser.add_argument(
        "--dependency-target",
        type=str,
        default=None,
        help="Select a dependency-manager target (for example a Bender target)",
    )

    return parser


def main(argv: list[str] | None = None) -> int | None:
    """Run the config discovery CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        from processor_discover.core.config_generator import (
            generate_processor_config,
        )

        config = generate_processor_config(
            args.processor_url,
            args.config_path,
            args.plot_graph,
            args.add_to_config,
            args.no_llama,
            args.model,
            args.local_repo,
            args.top_module,
            core_name=args.core_name,
            top_file_override=args.top_file,
            dependency_target=args.dependency_target,
        )
        print("Result: ")
        print(json.dumps(config, indent=4))
        return None

    except Exception as e:
        print_red(f"[ERROR] {e}")
        if os.path.exists("temp"):
            shutil.rmtree("temp")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
