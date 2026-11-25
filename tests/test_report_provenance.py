import subprocess
from unittest.mock import patch

from processor_discover.core.output import collect_provenance


def test_report_records_exact_local_repository_revision(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "core.sv").write_text("module CpuCore; endmodule\n")
    subprocess.run(["git", "add", "core.sv"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    with patch("processor_discover.core.output.shutil.which", return_value=None):
        provenance = collect_provenance(str(tmp_path))
    assert provenance["repository_revision"] == expected
    assert provenance["repository_dirty"] is False

    (tmp_path / "core.sv").write_text("module CpuCore; wire x; endmodule\n")
    with patch("processor_discover.core.output.shutil.which", return_value=None):
        assert collect_provenance(str(tmp_path))["repository_dirty"] is True

    nested = tmp_path / "nested-source-without-its-own-git"
    nested.mkdir()
    with patch("processor_discover.core.output.shutil.which", return_value=None):
        assert collect_provenance(str(nested))["repository_revision"] is None
