import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class QueueIdSearchTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / "api" / "console.py"
        tree = ast.parse(path.read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"_queue_search_rows", "_sort_queue_search_rows"}]
        scope = {"Any": Any}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), scope)
        self.search = scope["_queue_search_rows"]

    def run_search(self, doctype, query, matches):
        calls = []
        def fetch_rows(dt, **kwargs):
            calls.append(kwargs["filters"])
            return matches if kwargs["filters"][-1][1] == "name" else []
        result = self.search(fetch_rows, doctype,
                             SimpleNamespace(has_field=lambda field: field in {"mobile_no", "lead_name", "patient_name", "sr_patient_id"}),
                             [[doctype, "owner", "=", "agent@example.com"]],
                             ["name"], "name asc", 25, query)
        return result, calls

    def test_alphanumeric_ids_search_name_and_preserve_scope(self):
        for doctype, query in [("CRM Lead", "CRM-LEAD-2026-687101"),
                               ("Patient", "PAT-2026-1234567")]:
            with self.subTest(doctype=doctype):
                row = {"name": query}
                class Row(dict):
                    __getattr__ = dict.__getitem__
                result, calls = self.run_search(doctype, query, [Row(row)])
                self.assertEqual(result, [row])
                self.assertIn([doctype, "name", "like", query + "%"], calls[0])
                self.assertTrue(all(call[0] == [doctype, "owner", "=", "agent@example.com"] for call in calls))
                self.assertFalse(any(call[-1][2] == "in" for call in calls))

    def test_custom_patient_id_search(self):
        calls = []
        row = SimpleNamespace(name="HLC-PAT-2026-43549", get=lambda field: "HLC-PAT-2026-43549")
        def fetch_rows(dt, **kwargs):
            calls.append(kwargs["filters"][-1])
            return [row] if kwargs["filters"][-1] == [dt, "sr_patient_id", "like", "SR71047%"] else []
        result = self.search(fetch_rows, "Patient",
                             SimpleNamespace(has_field=lambda field: field == "sr_patient_id"),
                             [], ["name", "sr_patient_id"], "name asc", 25, "SR71047")
        self.assertEqual([r.name for r in result], ["HLC-PAT-2026-43549"])

    def test_numeric_patient_id_still_searches_name(self):
        result, calls = self.run_search("Patient", "1234567890", [])
        self.assertEqual(calls[0][-1], ["Patient", "name", "like", "1234567890%"])
        self.assertTrue(any(call[-1][1] == "mobile_no" for call in calls))

    def test_formatted_phone_retains_normalized_lookup(self):
        result, calls = self.run_search("CRM Lead", "+91 9253384269", [])
        phone_filter = next(call[-1] for call in calls if call[-1][1] == "mobile_no")
        self.assertEqual(phone_filter[2], "in")
        self.assertIn("9253384269", phone_filter[3])


if __name__ == "__main__":
    unittest.main()
