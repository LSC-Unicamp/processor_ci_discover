from pathlib import Path
from typing import Iterable, Optional

from ..core.api import RunContext


def as_path_list(values: Optional[Iterable[Path | str]]) -> list[Path]:
    if not values:
        return []
    return [value if isinstance(value, Path) else Path(value) for value in values]


def build_run_context(
    repo_root: Path | str,
    repo_name: str,
    repository_url: str,
    *,
    config_path: Path | str | None = None,
    local_repo: Path | str | None = None,
    model: str = "qwen2.5:32b",
    plot_graph: bool = False,
    add_to_config: bool = False,
    no_llama: bool = False,
    top_module_override: str | None = None,
    top_file_override: Path | str | None = None,
    dependency_target: str | None = None,
    language_version: str | None = None,
    maximize_attempts: int = 6,
    include_dirs: Optional[Iterable[Path | str]] = None,
    module_files: Optional[Iterable[Path | str]] = None,
) -> RunContext:
    return RunContext(
        repo_root=repo_root if isinstance(repo_root, Path) else Path(repo_root),
        repo_name=repo_name,
        repository_url=repository_url,
        config_path=(
            config_path
            if config_path is None or isinstance(config_path, Path)
            else Path(config_path)
        ),
        local_repo=(
            local_repo
            if local_repo is None or isinstance(local_repo, Path)
            else Path(local_repo)
        ),
        model=model,
        plot_graph=plot_graph,
        add_to_config=add_to_config,
        language_version=language_version,
        include_dirs=as_path_list(include_dirs),
        module_files=as_path_list(module_files),
        top_module_override=top_module_override,
        top_file_override=(top_file_override if top_file_override is None or isinstance(top_file_override, Path) else Path(top_file_override)),
        dependency_target=dependency_target,
        no_llama=no_llama,
        maximize_attempts=maximize_attempts,
    )
