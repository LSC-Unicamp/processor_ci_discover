import json

from processor_discover.core.api import CandidateResult
from processor_discover.core.candidate_export import candidate_identity, export_candidates, select_candidates


def candidate(top, role, status, files):
    return CandidateResult(
        top_module=top, top_file=f"{top}.sv", roles=[role], status=status,
        files=files, include_dirs=[], extra_flags=[], parameters={},
    )


def test_three_role_export_preserves_full_attempt_history_separately(tmp_path):
    attempts = [
        candidate("Requested", "requested", "failure", ["requested.sv"]),
        candidate("First", "first_heuristic", "failure", ["first.sv"]),
        candidate("Second", "fallback", "failure", ["second.sv"]),
        candidate("Third", "fallback", "compilation_success", ["third.sv"]),
    ]
    files = export_candidates(str(tmp_path), "demo", "https://example.invalid/demo", "1800-2023", attempts)
    assert len(attempts) == 4
    assert len(files) == 3
    assert [json.loads(open(path, encoding="utf-8").read())["top_module"] for path in files] == [
        "Requested", "First", "Third",
    ]


def test_identity_includes_build_context_and_shared_roles_export_once(tmp_path):
    requested = candidate("CpuCore", "requested", "compilation_success", ["core.sv"])
    requested.roles.append("first_heuristic")
    assert len(select_candidates([requested])) == 1
    other_context = candidate("CpuCore", "first_heuristic", "failure", ["core.sv", "params.sv"])
    assert candidate_identity(requested) != candidate_identity(other_context)
    files = export_candidates(str(tmp_path), "demo", "url", "1800-2023", [requested])
    assert len(files) == 1
