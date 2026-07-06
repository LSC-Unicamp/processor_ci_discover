import unittest

from processor_ci_discover.core.heuristics import (
    HDL_EXTRA_FUNCTIONAL_UNIT_TERMS,
    ensure_mapping,
    is_functional_unit_name,
    is_interface_module_name,
    is_micro_stage_name,
    is_peripheral_like_name,
    reachable_size,
)


class HeuristicsTests(unittest.TestCase):
    def test_peripheral_like_names(self) -> None:
        for name in ["axi_crossbar", "DataMemory", "uart_core", "gpio_bridge"]:
            self.assertTrue(is_peripheral_like_name(name))

        self.assertFalse(is_peripheral_like_name("CpuCore"))

    def test_functional_unit_names(self) -> None:
        for name in ["ALU", "fpdiv_unit", "branch_predictor", "mul_pipe"]:
            self.assertTrue(is_functional_unit_name(name))

        self.assertFalse(is_functional_unit_name("bp_core"))
        self.assertTrue(
            is_functional_unit_name(
                "fadd_unit",
                extra_terms=HDL_EXTRA_FUNCTIONAL_UNIT_TERMS,
            )
        )

    def test_micro_stage_names(self) -> None:
        for name in ["fetch_stage", "DecodeUnit", "issue_queue", "rob", "rs"]:
            self.assertTrue(is_micro_stage_name(name))

        self.assertFalse(is_micro_stage_name("RocketCore"))

    def test_interface_suffixes_are_configurable(self) -> None:
        self.assertTrue(is_interface_module_name("ControllerIF"))
        self.assertTrue(is_interface_module_name("cpu_inf"))
        self.assertFalse(is_interface_module_name("CoreIfc", suffixes=("if",)))
        self.assertTrue(is_interface_module_name("CoreIfc", suffixes=("if", "ifc")))

    def test_ensure_mapping_shapes(self) -> None:
        self.assertEqual(ensure_mapping({}), {})
        self.assertEqual(ensure_mapping({"a": None, "b": "c"}), {"a": [], "b": ["c"]})
        self.assertCountEqual(ensure_mapping({"a": {"b", "c"}})["a"], ["b", "c"])
        self.assertEqual(ensure_mapping([("a", ["b", "c"])]), {"a": ["b", "c"]})
        self.assertEqual(ensure_mapping(["a", "b"]), {"a": [], "b": []})

    def test_reachable_size(self) -> None:
        graph = {"top": ["core", "uart"], "core": ["alu"], "alu": [], "uart": []}

        self.assertEqual(reachable_size(graph, "top"), 3)
        self.assertEqual(reachable_size(graph, "core"), 1)


if __name__ == "__main__":
    unittest.main()
