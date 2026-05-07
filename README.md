# processor_ci_discover

Standalone home for the Processor CI config discovery tool, migrated from `processor_ci/config_generator.py`.

## What this repo now contains

- `config_generator.py`: full migrated config generator implementation
- `discover_config_generator.py`: stable standalone entrypoint
- `core/`: required helper modules migrated from `processor_ci/core`
- `verilator_runner.py`, `ghdl_runner.py`: simulator/minimization backends used by the generator
- `requirements.txt`: Python dependencies for this tool

## Quick start

1. Create a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the tool:

```bash
python discover_config_generator.py -u <processor_repo_url> -p config/
```

## Common usage

- Remote repository:

```bash
python discover_config_generator.py -u https://github.com/<org>/<repo> -p config/
```

- Local repository (no clone):

```bash
python discover_config_generator.py \
  -u https://github.com/<org>/<repo> \
  -l /path/to/local/repo \
  -p config/
```

- Force a specific top module (try first, then fallback to heuristics):

```bash
python discover_config_generator.py \
  -u https://github.com/<org>/<repo> \
  -t <top_module_name> \
  -p config/
```

## Notes

- The current migration keeps behavior equivalent to `processor_ci` by preserving imports and module layout.
- Output configuration files are written under `config/` by default.
