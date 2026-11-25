"""Output helpers for generated processor discovery configs."""

import shutil
import subprocess
from pathlib import Path

from ..core.api import DiscoveryReport


def collect_provenance(repo_root: str) -> dict:
    """Record the source revision and available tool versions for the report."""
    root = Path(repo_root)
    provenance = {"repository_revision": None, "repository_dirty": None, "tool_versions": {}}
    try:
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=root,
            capture_output=True, text=True, timeout=5,
        )
        if git_root.returncode != 0 or Path(git_root.stdout.strip()).resolve() != root.resolve():
            raise ValueError("The source directory is not a repository root")
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5,
        )
        if revision.returncode == 0:
            provenance["repository_revision"] = revision.stdout.strip()
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root, capture_output=True, text=True, timeout=5,
            )
            provenance["repository_dirty"] = bool(dirty.stdout.strip()) if dirty.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    for tool in ("verilator", "ghdl"):
        if not shutil.which(tool):
            continue
        try:
            result = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                provenance["tool_versions"][tool] = (result.stdout or result.stderr).splitlines()[0]
        except (OSError, subprocess.TimeoutExpired, IndexError):
            pass
    return provenance


def create_output_json(
    repo_name,
    url,
    filtered_files,
    include_dirs,
    top_module,
    language_version,
    is_simulable=False,
):
    """
    Creates the output JSON structure for the processor configuration.
    Schema version 2.0 – uses ``sim_files`` for the candidate file list.
    """
    return {
        "schema_version": "2.0",
        "name": repo_name,
        "folder": repo_name,
        "sim_files": filtered_files,
        "include_dirs": list(include_dirs),
        "repository": url,
        "top_module": top_module,
        "extra_flags": [],
        "language_version": language_version,
        "march": "rv32i",
        "two_memory": False,
        "is_simulable": is_simulable,
    }


def build_discovery_report(
    repo_name: str,
    repository_url: str,
    candidates: list,
    successful_candidate=None,
    output_json: dict | None = None,
    repo_root: str | None = None,
) -> DiscoveryReport:
    """Build a ``DiscoveryReport`` from the collected candidate results.

    Parameters
    ----------
    repo_name : str
        Processor repository name.
    repository_url : str
        The original Git URL.
    candidates : list[CandidateResult]
        All candidates explored during discovery.
    successful_candidate : CandidateResult or None
        The candidate that produced a working config.
    output_json : dict or None
        The final JSON that was written to disk.
    """
    report = DiscoveryReport(
        repo_name=repo_name,
        repository_url=repository_url,
        candidates=candidates,
        successful_candidate=successful_candidate,
        output_json=output_json,
        provenance=collect_provenance(repo_root) if repo_root else {},
    )
    return report


def save_discovery_report(
    report: DiscoveryReport,
    output_dir: str,
    repo_name: str,
) -> str:
    """Write the full ``DiscoveryReport`` to ``<output_dir>/report.json``.

    Returns the path to the written report file.
    """
    import json
    import os

    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report.to_dict(), fh, indent=4, default=str)
    return report_path


__all__ = [
    "create_output_json",
    "build_discovery_report",
    "save_discovery_report",
]
