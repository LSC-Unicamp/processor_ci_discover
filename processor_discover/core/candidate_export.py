"""Select the three role-bearing candidate configurations from a full attempt history."""

import json
import re
from pathlib import Path

from .api import CandidateResult
from .output import create_output_json


def candidate_identity(candidate: CandidateResult) -> tuple:
    """Top, build inputs and parameters determine whether two attempts are identical."""
    return (
        candidate.top_module,
        candidate.top_file,
        tuple(candidate.files),
        tuple(candidate.include_dirs),
        tuple(candidate.extra_flags),
        json.dumps(candidate.parameters, sort_keys=True, default=str),
    )


def select_candidates(attempts: list[CandidateResult]) -> list[CandidateResult]:
    """Promote requested, first heuristic, and first validated fallback in role order."""
    requested = next((c for c in attempts if "requested" in c.roles), None)
    heuristic = next((c for c in attempts if "first_heuristic" in c.roles), None)
    fallback = next((c for c in attempts if c.status == "compilation_success" and "fallback" in c.roles), None)
    selected = []
    identities = set()
    for candidate in (requested, heuristic, fallback):
        if candidate is None:
            continue
        identity = candidate_identity(candidate)
        if identity not in identities:
            selected.append(candidate)
            identities.add(identity)
    return selected


def export_candidates(config_path: str, repo_name: str, url: str, language_version: str, attempts: list[CandidateResult]) -> list[str]:
    """Replace prior generated candidate files with the selected run's evidence."""
    output_dir = Path(config_path) / "discovery" / repo_name / "candidates"
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_file in output_dir.glob(f"{repo_name}_*.json"):
        old_file.unlink()
    written = []
    for index, candidate in enumerate(select_candidates(attempts), 1):
        config = create_output_json(
            repo_name, url, candidate.files, candidate.include_dirs,
            candidate.top_module, language_version,
            candidate.status == "compilation_success",
        )
        config["extra_flags"] = list(candidate.extra_flags)
        config["discovery"] = {
            "has_successful_candidate": candidate.status == "compilation_success",
            "candidate_status": candidate.status,
            "roles": list(candidate.roles),
            "heuristic_rank": candidate.heuristic_rank,
            "top_file": candidate.top_file,
            "parameters": candidate.parameters,
            "compile_command": candidate.compile_command,
            "exit_code": candidate.exit_code,
            "errors": list(candidate.errors),
            "log_file": candidate.log_path,
            "defaults": {"march": "default", "two_memory": "default"},
        }
        safe_top = re.sub(r"[^a-zA-Z0-9_]", "_", candidate.top_module or "unknown")
        filename = output_dir / f"{repo_name}_{safe_top}_{'success' if candidate.status == 'compilation_success' else 'attempt' + str(index)}.json"
        filename.write_text(json.dumps(config, indent=2, default=str), encoding="utf-8")
        written.append(str(filename))
    return written
