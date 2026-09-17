"""Recording start must not wait for the shared background queue."""
import json
import unittest
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import frappe
from vobiz_click_to_call.services import recording
from vobiz_click_to_call.api import webhook


class SessionRecordingTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.stored = {}
        self.db.get_value.side_effect = lambda *a, **kw: (self.stored.copy()
            if kw.get('as_dict') else self.stored.get('recording_request_json'))
        self.db.set_value.side_effect = lambda dt, name, values, **kw: self.stored.update(values)
        self.doc = frappe._dict(name='CALL-TEST', callback_token='secret-token', call_uuid='provider-call',
            caller_id='+910000000001', customer_number='+910000000002', user_mobile='+910000000003',
            call_flow='Agent First', recording_status='Not Started')
        self.settings = frappe._dict(enable_recording=1, recording_time_limit=3600,
            max_call_duration=3600, recording_format='mp3', enable_transcription=1,
            transcription_type='auto', agent_ring_timeout=30)
        self.replace(frappe, 'db', self.db)
        self.replace(frappe, 'enqueue', MagicMock())
        self.replace(frappe.utils, 'now', lambda: '2026-09-17 16:00:00')
        self.replace(recording, 'build_callback_url', lambda method, name, token, settings:
            'https://erp.invalid/'+method+'?call_log='+name+'&token='+token)
        self.replace(recording, 'get_settings', lambda: self.settings)

    def replace(self, obj, name, value):
        p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop);return value

    def test_session_records_without_worker_or_provider_rest_request(self):
        client=self.replace(recording,'VobizClient',MagicMock())
        element=ET.fromstring(recording.session_recording_xml(self.doc,self.settings))
        self.assertEqual(element.tag,'Record')
        self.assertEqual(element.get('recordSession'),'true')
        self.assertEqual(element.get('redirect'),'false')
        self.assertEqual(element.get('playBeep'),'false')
        self.assertEqual(element.get('timeout'),'3600')
        self.assertEqual(element.get('maxLength'),'3600')
        self.assertIn('secret-token',element.get('callbackUrl'))
        self.assertIn('transcription_callback',element.get('transcriptionUrl'))
        self.assertNotIn('secret-token',self.stored['recording_request_json'])
        self.assertEqual(self.stored['recording_status'],'Starting')
        self.assertNotIn('recording_started_at',self.stored)
        frappe.enqueue.assert_not_called();client.assert_not_called()

    def test_disabled_recording_leaves_call_and_state_unchanged(self):
        self.settings.enable_recording=0
        self.assertEqual(recording.session_recording_xml(self.doc,self.settings),'')
        self.db.set_value.assert_not_called()

    def test_transcription_disabled_omits_transcription_attributes(self):
        self.settings.enable_transcription=0
        root=ET.fromstring(recording.session_recording_xml(self.doc,self.settings))
        self.assertNotIn('transcriptionUrl',root.attrib)
        self.assertEqual(self.stored['transcript_status'],'Not Requested')

    def test_configured_format_and_limit_are_preserved(self):
        self.settings.update(recording_format='wav',recording_time_limit=600)
        root=ET.fromstring(recording.session_recording_xml(self.doc,self.settings))
        self.assertEqual(root.get('fileFormat'),'wav')
        self.assertEqual(root.get('maxLength'),'600')

    def test_repeated_answer_does_not_reset_state_or_enqueue_rest(self):
        first=recording.session_recording_xml(self.doc,self.settings)
        self.db.set_value.reset_mock()
        self.assertEqual(recording.session_recording_xml(self.doc,self.settings),first)
        self.db.set_value.assert_not_called()
        webhook._start_recording_safely(self.doc.name)
        frappe.enqueue.assert_not_called()

    def test_legacy_queued_job_skips_session_even_if_status_was_reset(self):
        recording.session_recording_xml(self.doc,self.settings)
        self.doc.recording_status='Not Started'
        self.replace(frappe,'get_doc',lambda *a: self.doc)
        client=self.replace(recording,'VobizClient',MagicMock())
        recording.start_recording_if_needed(self.doc.name)
        client.assert_not_called()

    def test_active_or_completed_legacy_recording_is_not_replaced(self):
        for state in ['Starting','Started','Completed']:
            with self.subTest(state=state):
                self.stored.update(recording_status=state,recording_request_json='{}')
                self.assertEqual(recording.session_recording_xml(self.doc,self.settings),'')

    def test_completed_session_not_started_again_on_later_dial(self):
        recording.session_recording_xml(self.doc,self.settings)
        self.stored.update(recording_status='Completed',recording_id='REC-ID')
        self.assertEqual(recording.session_recording_xml(self.doc,self.settings),'')

    def test_both_mobile_call_orders_record_before_dial(self):
        self.replace(webhook,'get_settings',lambda:self.settings)
        self.replace(webhook,'build_callback_url',recording.build_callback_url)
        self.replace(webhook,'provider_phone_number',lambda n:n)
        for flow,target in [('Agent First',self.doc.customer_number),('Customer First',self.doc.user_mobile)]:
            with self.subTest(flow=flow):
                self.doc.call_flow=flow
                root=ET.fromstring(webhook._dial_xml(self.doc))
                self.assertEqual([e.tag for e in root],['Record','Dial'])
                self.assertEqual(root.find('Dial/Number').text,target)
                self.assertIn('dial_callback',root.find('Dial').get('callbackUrl'))

    def test_legacy_call_still_uses_existing_recording_start(self):
        self.doc.update(recording_status='Not Started',recording_request_json='{}',recording_id=None)
        self.replace(frappe,'get_doc',lambda *a:self.doc)
        client=self.replace(recording,'VobizClient',MagicMock())
        client.return_value.start_call_recording.return_value={'recording_id':'legacy-rec'}
        recording.start_recording_if_needed(self.doc.name)
        client.return_value.start_call_recording.assert_called_once()

    def test_invalid_saved_metadata_does_not_claim_session_recording(self):
        for value in ['invalid','[]','null','{}']:
            self.assertFalse(recording.uses_session_recording({'recording_request_json':value}))


if __name__=='__main__':unittest.main()
