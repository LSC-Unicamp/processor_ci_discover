from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any


@dataclass
class CandidateResult:
    """Captures the full result of attempting to build one candidate config.

    Fields
    ------
    top_module : str or None
        The candidate module name that was tried.
    top_file : str or None
        Source file where *top_module* is declared (relative to repo root).
    roles : list[str]
        Provenance roles: "requested", "first_heuristic", "fallback", etc.
    heuristic_rank : int
        Heuristic rank assigned by ``rank_top_candidates`` (1 = best).
    status : str
        One of "success", "failure", "partial".
    files : list[str]
        Final file list that was used to compile this candidate.
    include_dirs : list[str]
        Include directories used for compilation.
    extra_flags : list[str]
        Compiler / simulator flags applied.
    parameters : dict[str, Any]
        Any simulator-specific parameters.
    compile_command : list[str] | str
        The exact compilation command (or command string) that was run.
    exit_code : int | None
        Process exit code, or ``None`` when not available.
    errors : list[str]
        Raw error / diagnostic strings extracted from the simulator log.
    log_path : str | None
        Path to the full log file written to disk (``None`` when not saved).
    """

    top_module: Optional[str] = None
    top_file: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    heuristic_rank: int = 0
    status: str = "failure"
    files: List[str] = field(default_factory=list)
    include_dirs: List[str] = field(default_factory=list)
    extra_flags: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    compile_command: str = ""
    exit_code: Optional[int] = None
    errors: List[str] = field(default_factory=list)
    log_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "top_module": self.top_module,
            "top_file": self.top_file,
            "roles": self.roles,
            "heuristic_rank": self.heuristic_rank,
            "status": self.status,
            "files": self.files,
            "include_dirs": self.include_dirs,
            "extra_flags": self.extra_flags,
            "parameters": self.parameters,
            "compile_command": self.compile_command,
            "exit_code": self.exit_code,
            "errors": self.errors,
            "log_file": self.log_path,
        }


@dataclass
class DiscoveryReport:
    """Captures the full discovery outcome for one processor repository.

    Fields
    ------
    repo_name : str
        Name of the processor repository (used as config key).
    repository_url : str
        The original Git URL that was cloned.
    schema_version : str
        Output-schema version (e.g. ``"2.0"``).
    candidates : list[CandidateResult]
        All candidate configs explored during discovery, in order.
    successful_candidate : CandidateResult | None
        The candidate that produced a working config (if any).
    output_json : dict[str, Any] | None
        The final ``config/<repo_name>.json`` payload (same structure
        expected by existing downstream consumers).
    """

    repo_name: str = ""
    repository_url: str = ""
    schema_version: str = "2.0"
    candidates: List[CandidateResult] = field(default_factory=list)
    successful_candidate: Optional[CandidateResult] = None
    output_json: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_name": self.repo_name,
            "repository_url": self.repository_url,
            "schema_version": self.schema_version,
            "candidates": [c.to_dict() for c in self.candidates],
            "successful_candidate": (
                self.successful_candidate.to_dict()
                if self.successful_candidate
                else None
            ),
            "output_json": self.output_json,
        }


@dataclass
class RunContext:
    """Lightweight context object carrying common runtime parameters.

    Fill this with fields that are frequently passed through the codebase so
    functions can accept a single ``RunContext`` instead of long parameter lists.
    """

    repo_root: Path
    repo_name: Optional[str] = None
    repository_url: Optional[str] = None
    config_path: Optional[Path] = None
    local_repo: Optional[Path] = None
    plot_graph: bool = False
    add_to_config: bool = False
    language_version: Optional[str] = None
    include_dirs: List[Path] = field(default_factory=list)
    module_files: List[Path] = field(default_factory=list)
    top_module_override: Optional[str] = None
    top_file_override: Optional[Path] = None
    dependency_target: Optional[str] = None
    maximize_attempts: int = 6
    dry_run: bool = False
    seed: Optional[str] = None
    max_candidates: int = 3
    cache: Dict[str, Any] = field(default_factory=dict)

    # Accumulated candidate results from discovery (populated by callers).
    candidates: List[CandidateResult] = field(default_factory=list)

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
            "plot_graph": self.plot_graph,
            "add_to_config": self.add_to_config,
            "language_version": self.language_version,
            "include_dirs": [str(p) for p in self.include_dirs],
            "module_files": [str(p) for p in self.module_files],
            "top_module_override": self.top_module_override,
            "top_file_override": str(self.top_file_override) if self.top_file_override is not None else None,
            "dependency_target": self.dependency_target,
            "maximize_attempts": self.maximize_attempts,
            "max_candidates": self.max_candidates,
            "dry_run": self.dry_run,
            "seed": self.seed,
            "candidates": [c.to_dict() for c in self.candidates],
        }


__all__ = ["CandidateResult", "DiscoveryReport", "RunContext"]
