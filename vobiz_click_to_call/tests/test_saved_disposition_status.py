import inspect
import unittest
from unittest.mock import patch

import frappe
from vobiz_click_to_call.api import call


class SavedDispositionStatusTests(unittest.TestCase):
    def test_status_only_save_exposes_persisted_marker_without_provider_request(self):
        row = frappe._dict(name="CALL-1", user="agent@example.test", status="Completed",
                          disposition="", disposition_at="2026-11-16 12:00:00",
                          disposition_by="agent@example.test")
        with patch.object(frappe, "session", frappe._dict(user=row.user)), \
             patch.object(frappe, "get_doc", return_value=row), \
             patch.object(frappe, "get_roles", return_value=[]), \
             patch.object(call, "normalize_existing"), \
             patch.object(call, "sync_live_call_if_finished") as sync, \
             patch.object(call, "get_reference_title", return_value=""), \
             patch.object(call, "_has_customer_outcome", return_value=False), \
             patch.object(call, "mask_phone", return_value=""), \
             patch.object(call, "confirmation_pending", return_value=False):
            result = inspect.unwrap(call.get_call_status)("CALL-1", sync_provider=0)
        self.assertEqual(result["disposition"], "")
        self.assertEqual(result["disposition_at"], row.disposition_at)
        self.assertEqual(result["disposition_by"], row.disposition_by)
        sync.assert_not_called()
