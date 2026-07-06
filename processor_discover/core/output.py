"""Output helpers for generated processor discovery configs."""


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
    """
    return {
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
