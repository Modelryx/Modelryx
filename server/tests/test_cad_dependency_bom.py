import unittest

from modelryx.services.cad_dependencies import is_managed_bom_reference


class DependencyBomTests(unittest.TestCase):
    def test_dependency_only_edges_do_not_become_bom_lines(self):
        self.assertFalse(is_managed_bom_reference({"properties": {"is_bom_occurrence": False}}))
        self.assertTrue(is_managed_bom_reference({"properties": {"is_bom_occurrence": True}}))

    def test_older_worker_references_remain_supported(self):
        for reference in ({}, {"properties": None}, {"properties": {"configuration": "Default"}}):
            with self.subTest(reference=reference):
                self.assertTrue(is_managed_bom_reference(reference))
