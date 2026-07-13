from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any


@dataclass
class RunContext:
    """Lightweight context object carrying common runtime parameters.

    Fill this with fields that are frequently passed through the codebase so
    functions can accept a single `RunContext` instead of long parameter lists.
    """

    repo_root: Path
    repo_name: Optional[str] = None
    repository_url: Optional[str] = None
    config_path: Optional[Path] = None
    local_repo: Optional[Path] = None
    model: str = "qwen2.5:32b"
    plot_graph: bool = False
    add_to_config: bool = False
    language_version: Optional[str] = None
    include_dirs: List[Path] = field(default_factory=list)
    module_files: List[Path] = field(default_factory=list)
    top_module_override: Optional[str] = None
    top_file_override: Optional[Path] = None
    dependency_target: Optional[str] = None
    no_llama: bool = False
    maximize_attempts: int = 6
    dry_run: bool = False
    cache: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.repo_root, Path):
            self.repo_root = Path(self.repo_root)
        if self.config_path is not None and not isinstance(self.config_path, Path):
            self.config_path = Path(self.config_path)
        if self.local_repo is not None and not isinstance(self.local_repo, Path):
            self.local_repo = Path(self.local_repo)
        if self.top_file_override is not None and not isinstance(self.top_file_override, Path):
            self.top_file_override = Path(self.top_file_override)
        self.include_dirs = [Path(p) for p in self.include_dirs]
        self.module_files = [Path(p) for p in self.module_files]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_root": str(self.repo_root),
            "repo_name": self.repo_name,
            "repository_url": self.repository_url,
            "config_path": (
                str(self.config_path) if self.config_path is not None else None
            ),
            "local_repo": str(self.local_repo) if self.local_repo is not None else None,
            "model": self.model,
            "plot_graph": self.plot_graph,
            "add_to_config": self.add_to_config,
            "language_version": self.language_version,
            "include_dirs": [str(p) for p in self.include_dirs],
            "module_files": [str(p) for p in self.module_files],
            "top_module_override": self.top_module_override,
            "top_file_override": str(self.top_file_override) if self.top_file_override is not None else None,
            "dependency_target": self.dependency_target,
            "no_llama": self.no_llama,
            "maximize_attempts": self.maximize_attempts,
            "dry_run": self.dry_run,
        }


__all__ = ["RunContext"]
