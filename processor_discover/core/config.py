"""
This module provides utilities for handling configuration files
in JSON format, including operations to load, save, and fetch specific data,
such as processor information.

Main functions:
- load_config: Loads a JSON configuration file.
- save_config: Saves a dictionary to a JSON configuration file.
- is_usable_config: Checks whether a config dict represents a fully working config.
- get_processor_data: Retrieves processor information from the configuration file.
"""

import os
import json
import re
from copy import deepcopy


def create_default_config(config_path: str, processor_name: str) -> None:
    """Creates a default configuration file with an empty dictionary.

    Args:
        config_path (str): Path to the JSON configuration file.

    Returns:
        None

    Raises:
        IOError: If there is an issue writing to the file.
    """
    full_config_path = os.path.join(config_path, f"{processor_name}.json")

    with open(full_config_path, "w", encoding="utf-8") as file:
        json.dump({}, file, indent=4)


def load_config(config_path: str, processor_name: str) -> dict:
    """Loads a JSON configuration file and returns its content.

    Args:
        config_path (str): Path to the JSON configuration file.

    Returns:
        dict: Content of the JSON file as a dictionary.

    Raises:
        FileNotFoundError: If the specified configuration file does not exist.
        json.JSONDecodeError: If the file is not a valid JSON.
    """
    if not os.path.exists(config_path):
        os.makedirs(config_path, exist_ok=True)
        raise FileNotFoundError(
            f"The configuration folder {config_path} was not found."
        )
        # create_default_config(config_path)

    full_config_path = os.path.join(config_path, f"{processor_name}.json")

    if not os.path.exists(full_config_path):
        create_default_config(config_path, processor_name)
        raise FileNotFoundError(
            f"The configuration file {full_config_path} was not found."
        )

    with open(full_config_path, "r", encoding="utf-8") as file:
        config_data = json.load(file)

    return config_data


def save_config(config_path: str, config_data: dict, processor_name: str) -> None:
    """Saves a dictionary to a specified JSON configuration file.

    Args:
        config_path (str): Path to the JSON configuration file.
        config_data (dict): Configuration data to be saved.

    Returns:
        None

    Raises:
        TypeError: If the data provided is not serializable to JSON.
        IOError: If there is an issue writing to the file.
    """
    if not os.path.exists(config_path):
        os.makedirs(config_path, exist_ok=True)

    full_config_path = os.path.join(config_path, f"{processor_name}.json")
    # Save the configuration data to the specified file

    with open(full_config_path, "w", encoding="utf-8") as file:
        json.dump(config_data, file, indent=4)


def is_usable_config(config_data: dict) -> bool:
    """Return ``True`` when *config_data* looks like a working processor config.

    A config is considered usable when all of the following hold:

    - ``schema_version`` is ``"2.0"`` (Phase 1 schema).
    - ``sim_files`` is a non-empty list.
    - ``top_module`` is a non-empty string.
    - The ``discovery`` block (if present) does **not** mark the config
      as failed/partial.

    Returns ``False`` otherwise, so that non-valid configs can be placed
    in a ``candidates/`` sub-directory without existing scripts mistaking
    them for usable.
    """
    if not isinstance(config_data, dict):
        return False

    # Require schema version 2.0
    if config_data.get("schema_version") != "2.0":
        return False

    # Must have files to simulate
    sim_files = config_data.get("sim_files")
    if not isinstance(sim_files, list) or len(sim_files) == 0:
        return False

    # Must have a top module
    if not config_data.get("top_module"):
        return False

    # If a discovery block exists, check that the config is not marked failed/partial
    discovery = config_data.get("discovery")
    if isinstance(discovery, dict):
        success = discovery.get("has_successful_candidate", False)
        if success is False:
            return False
        # Explicit fail markers
        if discovery.get("candidate_status") in ("failure",):
            return False

    return True


def save_usable_config(config_path: str, config_data: dict, processor_name: str) -> None:
    """Save a config **only** when it passes :func:`is_usable_config`.

    Usable configs go to the standard location.  Non-usable configs are
    silently discarded so that existing downstream scripts never encounter
    them.

    Parameters
    ----------
    config_path : str
        Path to the JSON configuration directory.
    config_data : dict
        The configuration dictionary.
    processor_name : str
        Name used for the output file.

    Returns
    -------
    str or None
        The full path written to disk, or ``None`` when the config was
        rejected.
    """
    if not is_usable_config(config_data):
        return None

    save_config(config_path, config_data, processor_name)
    return os.path.join(config_path, f"{processor_name}.json")


def save_failed_candidate_config(
    config_path: str, config_data: dict, processor_name: str,
    top_module: str | None = None, attempt_index: int = 0
) -> str | None:
    """Write a rejected config to ``discovery/<processor_name>/candidates/``.

    Failed / partial configs are preserved in a sub-directory so they can be
    inspected later but are excluded from the *usable-config* index.

    Parameters
    ----------
    config_path : str
        Base config directory (e.g. ``"config"``).
    config_data : dict
        The configuration dictionary.
    processor_name : str
        Name used for the output file (same as *repo_name*).
    top_module : str or None
        Top module name, included in the filename for uniqueness (Blocker 3).
    attempt_index : int
        1-based attempt index, included in the filename for uniqueness.

    Returns
    -------
    str or None
        Full path to the written file, or ``None`` when it could not be saved.
    """
    if not is_usable_config(config_data):
        # Mark the config explicitly as non-usable if discovery block doesn't already say so
        if "discovery" not in config_data or not isinstance(config_data.get("discovery"), dict):
            config_data["discovery"] = {
                "schema_version": "2.0",
                "has_successful_candidate": False,
                "candidate_status": "failure",
                "candidate_count": 1,
                "max_candidates": 3,
            }
        elif config_data["discovery"].get("has_successful_candidate"):
            # Already marked successful — don't save a duplicate failure copy
            return None

        discovery_dir = os.path.join(config_path, "discovery", processor_name, "candidates")
        os.makedirs(discovery_dir, exist_ok=True)
        # Unique per-candidate filename: include top_module and attempt index (Blocker 3)
        safe_module = re.sub(r"[^a-zA-Z0-9_]", "_", top_module or "unknown")
        file_path = os.path.join(discovery_dir, f"{processor_name}_{safe_module}_attempt{attempt_index}.json")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2, default=str)
            return file_path
        except Exception:
            return None
    return None


def save_successful_candidate_config(
    config_path: str, config_data: dict, processor_name: str,
    top_module: str | None = None, roles: list[str] | None = None
) -> str | None:
    """Write a successful-but-lint-only config to
    ``discovery/<processor_name>/candidates/`` for evidence.

    In Phase 1 the validation gate is lint-only (compilation success), so
    ``is_simulable`` is ``False`` and the config does *not* go through
    ``save_usable_config`` as a "usable" artifact.  Nevertheless the
    candidate is valid — we preserve it as a standalone file with its
    role so downstream consumers can inspect it.

    Uses the same ``discovery/<repo>/`` relative path convention as the
    discovery report so that the candidates directory is colocated and
    easily discoverable.

    Parameters
    ----------
    config_path : str
        Base config directory.
    config_data : dict
        The configuration dictionary (Phase 2 schema 2.0).
    processor_name : str
        Repository name.
    top_module : str or None
        Top module name, included in the filename.
    roles : list[str] or None
        Roles of the candidate (``"requested"``, ``"first_heuristic"``, …).

    Returns
    -------
    str or None
        Full path to the written file, or ``None`` when it could not be saved.
    """
    discovery_dir = os.path.join(config_path, "discovery", processor_name, "candidates")
    os.makedirs(discovery_dir, exist_ok=True)
    safe_module = re.sub(r"[^a-zA-Z0-9_]", "_", top_module or "unknown")
    file_path = os.path.join(discovery_dir, f"{processor_name}_{safe_module}_success.json")
    try:
        # Copy the config and add evidence metadata before writing.
        evidence_data = deepcopy(config_data)
        evidence_data.setdefault("discovery", {})["has_successful_candidate"] = True
        evidence_data.setdefault("discovery", {})["candidate_status"] = "compilation_success"
        evidence_data.setdefault("discovery", {})["roles"] = roles or []
        evidence_data.setdefault("discovery", {})["top_module"] = top_module or ""
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(evidence_data, f, indent=2, default=str)
        return file_path
    except Exception:
        return None


__all__ = [
    "create_default_config",
    "load_config",
    "save_config",
    "is_usable_config",
    "save_usable_config",
    "save_failed_candidate_config",
    "save_successful_candidate_config",
]
