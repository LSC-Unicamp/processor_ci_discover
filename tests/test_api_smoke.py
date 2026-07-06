import inspect
import unittest

import discover_config_generator
from processor_discover.core.config_generator import generate_processor_config


class ApiSmokeTests(unittest.TestCase):
    def test_generate_processor_config_import_path_is_preserved(self) -> None:
        self.assertTrue(callable(generate_processor_config))

    def test_standalone_entrypoint_delegates_to_package_cli(self) -> None:
        source = inspect.getsource(discover_config_generator)

        self.assertIn("from processor_discover.cli import main", source)
        self.assertIn("raise SystemExit(main())", source)


if __name__ == "__main__":
    unittest.main()
