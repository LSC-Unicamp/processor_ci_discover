# ProcessorCI Discover Documentation

## Package Boundary

`processor_discover/` is the canonical implementation package. Public callers
should use either:

```bash
python -m processor_discover.cli
```

or the compatibility wrapper:

```bash
python discover_config_generator.py
```

Do not add new root-level implementation modules unless they are compatibility
shims.

## Discovery Pipeline

The discovery flow is organized by responsibility:

- `core/`: pipeline, graph, file management, config generation, and top-module
  selection.
- `lang/`: language-specific helpers for Chisel and Bluespec.
- `runners/`: HDL tool integrations such as Verilator and GHDL.
- `utils/`: runtime, logging, and locking utilities.

## Test Expectations

Prefer tests that do not require network access or full HDL toolchains. Use
small local fixtures and mock external tools where possible.

Useful checks:

```bash
pytest
python -m processor_discover.cli --help
python discover_config_generator.py --help
```

## Output Contract

Generated JSON should remain compatible with ProcessorCI config consumers:

- `name`
- `folder`
- `files`
- `include_dirs`
- `repository`
- `top_module`
- `extra_flags`
- `language_version`

Document any new field before relying on it in another ProcessorCI repository.
