import unittest
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from vobiz_click_to_call.services.queue_sources import parse_queue_sources, queue_source_options, queue_includes
from vobiz_click_to_call.api import console


class QueueSourcesTests(unittest.TestCase):
    def test_legacy_combined_mapping_expands_without_duplicates(self):
        self.assertEqual(parse_queue_sources("CRM Lead and Patient"), ["CRM Lead", "Patient"])
        self.assertEqual(parse_queue_sources("CRM Lead and Patient\nPatient\nIssue"),
                         ["CRM Lead", "Patient", "Issue"])

    def test_single_and_multiple_sources(self):
        self.assertEqual(queue_source_options("Patient"), ["Patient"])
        self.assertEqual(queue_source_options("CRM Lead\nPatient Encounter\nIssue"),
                         ["CRM Lead", "Patient Encounter", "Issue"])
        self.assertTrue(queue_includes("Issue\nPatient", "Patient"))
        self.assertFalse(queue_includes("Patient Encounter", "Patient"))

    def test_console_allows_only_configured_sources(self):
        sources = console._agent_queue_source({"queue_source": "CRM Lead\nPatient Encounter"})
        self.assertEqual(console._queue_source_options(sources), ["CRM Lead", "Patient Encounter"])
        self.assertEqual(console._selected_queue_source(sources, "Patient Encounter"), "Patient Encounter")
        self.assertEqual(console._selected_queue_source(sources, "Patient"), "CRM Lead")
        self.assertEqual(console._selected_queue_source("CRM Lead and Patient", "Patient"), "Patient")

    def test_mapped_access_matches_each_configured_doctype(self):
        with patch.object(console, "_agent_context", return_value={"queue_source": "Issue\nPatient"}):
            self.assertTrue(console._has_mapped_queue_access("Patient"))
            self.assertTrue(console._has_mapped_queue_access("Issue"))
            self.assertFalse(console._has_mapped_queue_access("CRM Lead"))

    def test_inbound_patient_routing_includes_multi_source_mappings_only(self):
        path = Path(__file__).parents[1] / "api" / "inbound.py"
        tree = ast.parse(path.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "patient_route_mappings")
        rows = [{"queue_source": "CRM Lead"}, {"queue_source": "Issue\nPatient"},
                {"queue_source": "CRM Lead and Patient"}, {"queue_source": "Patient Encounter"}]
        fake = SimpleNamespace(db=SimpleNamespace(exists=lambda *a: True), get_meta=lambda *a: None,
                               get_all=lambda *a, **kw: rows)
        scope = dict(frappe=fake, Any=Any, queue_includes=queue_includes,
                     _existing_mapping_fields=lambda meta, fields: fields,
                     _patient_mapping_matches=lambda row, patient: True)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), scope)
        self.assertEqual(scope["patient_route_mappings"]({}), rows[1:3])

    def test_analytics_patient_scope_does_not_include_lead_only_mappings(self):
        allowed = set(parse_queue_sources(console._analytics_mapped_agent_queue_filter("Patient")))
        self.assertFalse(allowed.intersection(parse_queue_sources("CRM Lead")))
        self.assertTrue(allowed.intersection(parse_queue_sources("CRM Lead and Patient")))
        self.assertTrue(allowed.intersection(parse_queue_sources("Issue\nPatient")))


if __name__ == "__main__":
    unittest.main()
