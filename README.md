# processor_ci_discover

Standalone home for the Processor CI config discovery tool, migrated from `processor_ci/config_generator.py`.

## What this repo now contains

- `processor_discover/`: package root
- `processor_discover/core/`: generator and core helpers
- `processor_discover/lang/`: Chisel/Bluespec helpers
- `processor_discover/runners/`: simulator/minimization backends
- `processor_discover/utils/`: shared utilities (logging, locks)
- `processor_discover/cli.py`: CLI parsing and package entrypoint
- `discover_config_generator.py`: stable standalone wrapper around the package CLI
- `requirements.txt`: Python dependencies for this tool

## Quick start

1. Create a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the tool:

```bash
python -m processor_discover.cli -u <processor_repo_url> -p config/
```

Or directly:

```bash
python discover_config_generator.py -u <processor_repo_url> -p config/
```

Both entrypoints use the same parser and behavior.

## Common usage

- Remote repository:

```bash
python -m processor_discover.cli -u https://github.com/<org>/<repo> -p config/
```

- Remote repository using the standalone wrapper:

```bash
python discover_config_generator.py \
  -u https://github.com/<org>/<repo> \
  -p config/
```

- Local repository (no clone):

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo> \
  -l /path/to/local/repo \
  -p config/
```

- Local repository with an explicit output directory:

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo>.git \
  -l ~/src/<repo> \
  -p ./generated-configs
```

- Force a specific top module (try first, then fallback to heuristics):

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo> \
  -t <top_module_name> \
  -p config/
```

- Force a top module while processing a local checkout:

```bash
python -m processor_discover.cli \
  -u https://github.com/chipsalliance/rocket-chip.git \
  -l ~/src/rocket-chip \
  -t RocketTile \
  -p config/
```

- Skip OLLAMA and rely on local heuristics/runners:

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo> \
  -n \
  -p config/
```

- Skip OLLAMA and use a forced top module:

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo> \
  -n \
  -t <top_module_name> \
  -p config/
```

- Add the generated processor config to a central config file:

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo> \
  -a \
  -p config/
```

- Select a different OLLAMA model:

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo> \
  -m llama3.1:8b \
  -p config/
```

- Generate the dependency graph plot:

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo> \
  -g \
  -p config/
```

- Combine common CI-friendly options:

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo> \
  -l "$PWD/vendor/<repo>" \
  -n \
  -a \
  -p "$PWD/config"
```

- Programmatic use from Python:

```python
from processor_discover.core.config_generator import generate_processor_config

config = generate_processor_config(
    url="https://github.com/<org>/<repo>",
    config_path="config/",
    local_repo="/path/to/local/repo",
    no_llama=True,
    top_module_override="<top_module_name>",
)
print(config["top_module"])
```

## CLI reference

```text
usage: python -m processor_discover.cli [-h] -u PROCESSOR_URL
                                           [-p CONFIG_PATH] [-g] [-a] [-n]
                                           [-m MODEL] [-l LOCAL_REPO]
                                           [-t TOP_MODULE]
```

- `-u`, `--processor-url`: processor repository URL. Required.
- `-p`, `--config-path`: output config directory. Defaults to `config/`.
- `-g`, `--plot-graph`: plot the discovered module dependency graph.
- `-a`, `--add-to-config`: also write/update a central config file.
- `-n`, `--no-llama`: skip OLLAMA-assisted filtering/top selection.
- `-m`, `--model`: OLLAMA model name. Defaults to `qwen2.5:32b`.
- `-l`, `--local-repo`: use a local checkout instead of cloning.
- `-t`, `--top-module`: try a specific top module first, then fall back to heuristics if it fails.

## Developer notes

- `processor_discover.cli.build_parser()` exposes the CLI parser for tests.
- `processor_discover.cli.main(argv=None)` supports argument injection for tests and wrappers.
- `processor_discover.core.config_generator.generate_processor_config(...)` remains the stable programmatic entrypoint.
- `processor_discover.core.config_generator.main(argv=None)` is a compatibility shim into the package CLI.

## Notes

- The current migration keeps CLI behavior stable while moving parser ownership into `processor_discover/cli.py`.
- Output configuration files are written under `config/` by default.
