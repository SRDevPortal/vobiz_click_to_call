from unittest import TestCase
from unittest.mock import MagicMock, patch
import frappe
from vobiz_click_to_call.services import mapping_recovery as recovery


class RecoveryDispatchTests(TestCase):
    def test_skips_recent_active_but_keeps_terminal_missing_and_stale(self):
        maps = [frappe._dict(name='M'+str(n), current_call_log='C'+str(n)) for n in range(4)]
        calls = [frappe._dict(name='C0',status='Agent Ringing',modified='recent'),
                 frappe._dict(name='C1',status='Completed',modified='recent'),
                 frappe._dict(name='C2',status='Agent Ringing',modified='old')]
        cache=MagicMock(); cache.get_value.return_value=''
        with patch.object(frappe,'cache',return_value=cache), \
             patch.object(frappe,'get_all',side_effect=[maps,calls]), \
             patch.object(frappe.utils,'now',return_value='now'), \
             patch.object(frappe.utils,'time_diff_in_seconds',side_effect=lambda a,b: 10 if b=='recent' else 61), \
             patch.object(recovery,'enqueue_recovery') as enqueue:
            self.assertEqual(recovery.enqueue_pending_recovery(), {'checked':4,'queued':3})
        self.assertEqual([c.args for c in enqueue.call_args_list], [('C1','M1'),('C2','M2'),('C3','M3')])

    def test_empty_scan_does_not_query_all_calls(self):
        cache=MagicMock(); cache.get_value.return_value=''
        with patch.object(frappe,'cache',return_value=cache), patch.object(frappe,'get_all',return_value=[]) as get_all:
            self.assertEqual(recovery.enqueue_pending_recovery(), {'checked':0,'queued':0})
        get_all.assert_called_once()
