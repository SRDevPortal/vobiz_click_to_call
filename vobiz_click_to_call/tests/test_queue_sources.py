import unittest
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch
from vobiz_click_to_call.services.queue_sources import parse_queue_sources, queue_source_options, queue_includes
from vobiz_click_to_call.api import console
from vobiz_click_to_call.vobiz_click_to_call.doctype.vobiz_user_mapping import vobiz_user_mapping as mapping


class QueueSourcesTests(unittest.TestCase):
    def test_ndr_only_mapping_never_falls_back_to_leads_or_autodial(self):
        self.assertEqual(queue_source_options("NDR"), [])
        source = console._agent_queue_source({"queue_source": "NDR"})
        self.assertEqual(source, "NDR")
        self.assertEqual(console._selected_queue_source(source, "CRM Lead"), "NDR")
        with patch.object(console, "_queue_for_doctype") as query:
            self.assertEqual(console._lead_queue(25, agent={"queue_source": "NDR"}), [])
        query.assert_not_called()
        self.assertEqual(queue_source_options("Patient\nNDR"), ["Patient"])
        self.assertEqual(console._selected_queue_source("Patient\nNDR", "NDR"), "Patient")
        with patch.object(console, "_agent_context", return_value={"queue_source": "NDR"}):
            self.assertFalse(console._has_mapped_queue_access("CRM Lead"))

    def test_mapping_ndr_assignment_requires_setting_but_retains_existing_assignment(self):
        for enabled, previous, selection, allowed in (
            (1, None, "NDR", True), (1, None, "CRM Lead\nNDR", True),
            (0, None, "NDR", False), (0, {"queue_source": "Patient"}, "Patient\nNDR", False),
            (0, {"queue_source": "Patient\nNDR"}, "Patient\nNDR", True),
            (0, {"queue_source": "Patient\nNDR"}, "Patient", True),
            (1, None, "Invalid", False), (1, None, "", False),
        ):
            doc = SimpleNamespace(queue_source=selection, get_doc_before_save=lambda: previous)
            with self.subTest(enabled=enabled, previous=previous, selection=selection), \
                 patch.object(mapping.frappe, "throw", side_effect=ValueError), \
                 patch.object(mapping, "_", side_effect=lambda text: text):
                if allowed:
                    mapping.VobizUserMapping.validate_queue_sources(doc, {"enable_ndr_queue": enabled})
                    self.assertEqual(doc.queue_source, selection)
                else:
                    with self.assertRaises(ValueError):
                        mapping.VobizUserMapping.validate_queue_sources(doc, {"enable_ndr_queue": enabled})

    def test_mapping_picker_offers_ndr_only_when_enabled(self):
        for enabled in (0, 1):
            with patch.object(mapping.frappe, "has_permission", return_value=True), \
                 patch.object(mapping.frappe, "db", Mock(get_single_value=Mock(return_value=enabled))):
                options = mapping.get_queue_source_options.__wrapped__()
            self.assertEqual("NDR" in options, bool(enabled))
            self.assertIn("Patient", options)

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
