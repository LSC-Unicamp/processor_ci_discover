# ProcessorCI Discover

ProcessorCI Discover generates ProcessorCI configuration files from processor
repositories. It analyzes HDL source trees, selects candidate top modules, and
emits JSON configuration data that can be used by the rest of the ProcessorCI
suite.

This repository is the standalone home for the config-discovery tool that was
originally part of `processor_ci/config_generator.py`.

## Repository Layout

```text
processor_discover/          Canonical Python package
processor_discover/core/     Config generation pipeline and shared helpers
processor_discover/lang/     Chisel and Bluespec support helpers
processor_discover/runners/  Verilator/GHDL runner integrations
processor_discover/utils/    Logging, runtime, and locking helpers
tests/                       Smoke and behavior tests
discover_config_generator.py Compatibility wrapper around the package CLI
docs/                        Maintenance notes
requirements.txt             Python dependencies
```

## Installation

```bash
git clone https://github.com/LSC-Unicamp/processor_ci_discover.git
cd processor_ci_discover
python3 -m venv env
. env/bin/activate
pip install -r requirements.txt
```

Some discovery paths use external HDL tools such as Verilator or GHDL. OLLAMA is
optional and can be disabled with `-n`.

## Quick Start

Generate a config from a remote repository:

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo> \
  -p config/
```

Use the compatibility wrapper:

```bash
python discover_config_generator.py \
  -u https://github.com/<org>/<repo> \
  -p config/
```

Use a local checkout and skip OLLAMA:

```bash
python -m processor_discover.cli \
  -u https://github.com/<org>/<repo> \
  -l /path/to/local/repo \
  -n \
  -p generated-configs/
```

## Common Options

- `-u`, `--processor-url`: processor repository URL. Required.
- `-p`, `--config-path`: output config directory. Defaults to `config/`.
- `-l`, `--local-repo`: local checkout to analyze instead of cloning.
- `-t`, `--top-module`: preferred top module to try before heuristics.
- `-n`, `--no-llama`: disable OLLAMA-assisted filtering.
- `-m`, `--model`: OLLAMA model name.
- `-g`, `--plot-graph`: generate a dependency graph plot.
- `-a`, `--add-to-config`: update a central config file as well.

## Programmatic Use

```python
from processor_discover.core.config_generator import generate_processor_config

config = generate_processor_config(
    url="https://github.com/<org>/<repo>",
    config_path="config/",
    local_repo="/path/to/local/repo",
    no_llama=True,
    top_module_override="<top_module_name>",
)
```

## Development

Run tests with:

```bash
pytest
```

Run CLI help checks with:

```bash
python -m processor_discover.cli --help
python discover_config_generator.py --help
```

Keep `processor_discover/` as the canonical implementation. Root-level scripts
should remain compatibility wrappers unless a migration plan says otherwise.
See [docs/README.md](docs/README.md) for layout notes.

## Contributing

Issues and pull requests are welcome. Include a small repository fixture or test
case when changing top-module selection, HDL parsing, or config output shape.

## License

This project is licensed under the [MIT License](LICENSE).
