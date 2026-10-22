import json
import unittest
from unittest.mock import Mock, patch
from datetime import datetime
import frappe
from vobiz_click_to_call.services import transcript_sync as sync
from vobiz_click_to_call.services.transcript_provider import (
    TranscriptReader, TranscriptLookupError, TranscriptAccessError, text_value,
)
from vobiz_click_to_call.services.client import ProviderTemporaryError


class ProviderTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.reader = object.__new__(TranscriptReader)
        self.reader.auth_id = "MA_TEST"
        self.reader.auth_token = "test-only"
        self.reader.base_url = "https://api.vobiz.ai/api/v1"
        self.call = frappe._dict(call_uuid="call-1", recording_id="rec-1")
        self.row = dict(account_id="MA_TEST", call_uuid="call-1", transcription_id="trans-1",
                        recording_id="rec-1", transcription_text="Customer requested a callback.")
        self.reader.read_json = Mock(return_value={"objects": [self.row], "meta": {"next": None}})

    def test_exact_call_and_account(self):
        result = self.reader.lookup(self.call)
        self.assertEqual(result["text"], self.row["transcription_text"])
        self.assertEqual(result["account_id"], "MA_TEST")
        self.assertEqual(self.reader.read_json.call_args.args[1]["call_uuid"], "call-1")

    def test_wrong_call_account_or_recording_never_used(self):
        for key in ("call_uuid", "account_id", "recording_id"):
            with self.subTest(key=key):
                row = dict(self.row, **{key: "wrong"})
                self.reader.read_json.return_value = {"objects": [row]}
                self.assertIsNone(self.reader.lookup(self.call))

    def test_ambiguous_transcripts_rejected(self):
        self.reader.read_json.return_value = {"objects": [self.row, dict(self.row, transcription_id="second")]}
        with self.assertRaises(TranscriptLookupError):
            self.reader.lookup(self.call)

    def test_provider_pending_not_saved(self):
        self.reader.read_json.return_value = {"objects": [dict(self.row, transcription_status="processing")]}
        self.assertIsNone(self.reader.lookup(self.call))

    def test_existing_detail_lookup_only_after_exact_match(self):
        self.reader.read_json.side_effect = [
            {"objects": [dict(self.row, transcription_text="")]},
            {"status": "success", "transcription": {"Valid": True, "String": "Recovered text"}},
        ]
        self.assertEqual(self.reader.lookup(self.call)["text"], "Recovered text")
        self.assertEqual(self.reader.read_json.call_args.args[0], "Transcription/trans-1/transcription/")

    def test_no_external_url_download(self):
        self.reader.read_json.return_value = {"objects": [dict(self.row, transcription_text="",
            transcription_id="", transcription_url="http://127.0.0.1/private")]}
        self.assertIsNone(self.reader.lookup(self.call))
        self.assertEqual(self.reader.read_json.call_count, 1)

    def test_invalid_list_shape_is_failure_not_unavailable(self):
        self.reader.read_json.return_value = {"message": "Unexpected error"}
        with self.assertRaises(TranscriptLookupError):
            self.reader.lookup(self.call)

    def test_pagination_not_silently_truncated(self):
        self.reader.read_json.return_value = {"objects": [self.row], "meta": {"next": "/next"}}
        with self.assertRaises(TranscriptLookupError):
            self.reader.lookup(self.call)

    def test_missing_identity_does_not_query(self):
        self.assertIsNone(self.reader.lookup({}))
        self.reader.read_json.assert_not_called()

    def test_oversized_text_rejected(self):
        self.reader.read_json.return_value = {"objects": [dict(self.row, transcription_text="x" * (1024*1024+1))]}
        with self.assertRaises(TranscriptLookupError):
            self.reader.lookup(self.call)

    def test_go_null_string(self):
        self.assertEqual(text_value({"String": "text", "Valid": False}), "")
        self.assertEqual(text_value({"String": "text", "Valid": True}), "text")

    def test_transport_limits_and_auth_errors(self):
        del self.reader.read_json
        response = Mock(status_code=403, headers={})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("vobiz_click_to_call.services.transcript_provider.requests.get", return_value=response) as get,              patch("vobiz_click_to_call.services.recovery_policy.claim_provider_read"):
            with self.assertRaises(TranscriptAccessError):
                self.reader.read_json("Transcriptions/")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual(get.call_args.kwargs["timeout"], (5, 10))
        self.assertTrue(get.call_args.kwargs["stream"])

    def test_response_size_bound(self):
        del self.reader.read_json
        response = Mock(status_code=200, headers={})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [b"x" * (2*1024*1024+1)]
        with patch("vobiz_click_to_call.services.transcript_provider.requests.get", return_value=response),              patch("vobiz_click_to_call.services.recovery_policy.claim_provider_read"):
            with self.assertRaises(TranscriptLookupError):
                self.reader.read_json("Transcriptions/")


class TranscriptSyncTests(unittest.TestCase):
    def setUp(self):
        self.call = frappe._dict(name="TEST-CALL", creation=datetime(2026, 10, 14),
            account_id="MA_TEST", call_uuid="call-1", recording_id="rec-1", status="Completed",
            transcript_text="", transcription_text="", transcription_id="",
            disposition="Manual result", ai_disposition_status="Not Requested")
        self.payload = dict(account_id="MA_TEST", call_uuid="call-1", transcription_id="trans-1", text="Recovered")
        self.run = frappe._dict(name="TEST-RUN", status="Running", from_date="2026-10-14", to_date="2026-10-14",
            batch_size=25, requested_by="Administrator", account_id="MA_TEST", processed=0, updated=0,
            skipped=0, failed=0, unavailable=0, retry_count=0, next_attempt=None, scan_until=datetime(2026,10,15))
        self.throw = patch.object(frappe, "throw", side_effect=lambda msg,*a,**k: (_ for _ in ()).throw(ValueError(msg)))
        self.throw.start()
        self.addCleanup(self.throw.stop)

    def test_options_bound_dates_and_batch_size(self):
        sync.validate_options("2026-10-01", "2026-10-31", 25)
        for start,end,size in [("2026-10-01","2026-11-01",25),("2026-10-02","2026-10-01",25),
                               ("2026-10-01","2026-10-01",0),("2026-10-01","2026-10-01",51),
                               ("2026-10-01","2026-10-01","2.5")]:
            with self.assertRaises(ValueError): sync.validate_options(start,end,size)

    def test_transcript_only_allowlist_without_save_hooks(self):
        with patch.object(frappe.db,"get_value",return_value=self.call),              patch.object(frappe.db,"set_value") as update, patch.object(frappe,"get_doc") as get_doc:
            self.assertEqual(sync.apply_transcript(self.call.copy(),self.payload)[0],"Updated")
        fields=update.call_args.args[2]
        self.assertEqual(set(fields),{"transcript_text","transcription_text","transcript_status",
            "transcript_received_at","transcript_error","transcription_id"})
        self.assertFalse(update.call_args.kwargs["update_modified"])
        get_doc.assert_not_called()
        self.assertEqual(self.call.disposition,"Manual result")

    def test_callback_wins_race_existing_text_preserved(self):
        snapshot=self.call.copy()
        self.call.transcript_text="Callback transcript"
        with patch.object(frappe.db,"get_value",return_value=self.call), patch.object(frappe.db,"set_value") as update:
            self.assertEqual(sync.apply_transcript(snapshot,self.payload)[0],"Skipped")
        update.assert_not_called()

    def test_either_text_field_prevents_overwrite(self):
        self.call.transcription_text="Legacy transcript"
        self.assertEqual(sync.eligibility(self.call,"MA_TEST"),"Transcript already present.")

    def test_changed_identity_or_transcription_id_is_not_overwritten(self):
        for key,value in [("recording_id","changed"),("transcription_id","other-transcript")]:
            snapshot=self.call.copy()
            current=self.call.copy()
            current[key]=value
            with patch.object(frappe.db,"get_value",return_value=current), patch.object(frappe.db,"set_value") as update:
                self.assertEqual(sync.apply_transcript(snapshot,self.payload)[0],"Failed")
                update.assert_not_called()

    def test_active_call_and_wrong_account_skipped(self):
        self.assertTrue(sync.eligibility(dict(self.call,status="Connected"),"MA_TEST"))
        self.assertTrue(sync.eligibility(self.call,"OTHER"))

    def test_keyset_uses_creation_and_name_not_offset(self):
        self.run.cursor_time=datetime(2026,10,14,12)
        self.run.cursor_name="last"
        with patch.object(frappe.db,"sql",return_value=[]) as sql:
            sync.candidates(self.run,25)
        query,params=sql.call_args.args
        self.assertNotIn("OFFSET",query)
        self.assertNotIn("COUNT",query)
        self.assertIn("creation = %s AND name > %s",query)
        self.assertEqual(params[-2: ],["last",25])

    def test_no_manager_no_start(self):
        with patch.object(frappe,"only_for",side_effect=frappe.PermissionError), patch.object(sync,"lock") as lock:
            with self.assertRaises(frappe.PermissionError): sync.start("TEST")
            lock.assert_not_called()

    def test_retry_preserves_cursor_and_honors_delay(self):
        with patch.object(sync,"active_run",return_value=self.run), patch.object(frappe.db,"set_value") as update,              patch.object(frappe.db,"commit"):
            sync.retry_or_fail("TEST",self.call,ProviderTemporaryError("busy",retry_after=120))
        values=update.call_args.args[2]
        self.assertEqual(values["retry_count"],1)
        self.assertNotIn("cursor_name",values)
        self.assertIsNotNone(values["next_attempt"])

    def test_third_temporary_failure_moves_on_with_audit(self):
        self.run.retry_count=2
        with patch.object(sync,"active_run",return_value=self.run), patch.object(sync,"checkpoint") as checkpoint:
            sync.retry_or_fail("TEST",self.call,ProviderTemporaryError("busy"))
        self.assertEqual(checkpoint.call_args.args[2],"Failed")

    def test_local_read_budget_does_not_exhaust_call_retries(self):
        self.run.retry_count=2
        with patch.object(sync,"active_run",return_value=self.run), patch.object(sync,"checkpoint") as checkpoint,              patch.object(frappe.db,"set_value") as update, patch.object(frappe.db,"commit"):
            sync.retry_or_fail("TEST",self.call,ProviderTemporaryError("budget",source="local_budget",retry_after=60))
        checkpoint.assert_not_called()
        self.assertEqual(update.call_args.args[2]["retry_count"],2)

    def test_pause_during_http_discards_inflight_result(self):
        # First run read, iteration read, post-HTTP read sees Paused.
        provider=Mock(auth_id="MA_TEST",auth_token="test")
        provider.lookup.return_value=self.payload
        def value(doctype,*args,**kwargs):
            return 1 if doctype=="User" else self.call
        with patch.object(sync,"lock") as lock, patch.object(sync,"active_run",side_effect=[self.run,self.run,None]),              patch.object(sync,"TranscriptReader",return_value=provider),              patch.object(sync,"candidates",return_value=[self.call]), patch.object(sync,"require_manager"),              patch.object(frappe.db,"get_value",side_effect=value), patch.object(frappe.db,"sql",return_value=[(50,)]),              patch.object(frappe.db,"commit"), patch.object(frappe.db,"rollback"),              patch.object(frappe,"set_user"), patch.object(sync,"apply_transcript") as apply,              patch.object(sync,"checkpoint") as checkpoint:
            lock.return_value.__enter__.return_value=True
            sync.run_batch("TEST-RUN")
        provider.lookup.assert_called_once()
        apply.assert_not_called()
        checkpoint.assert_not_called()


    def test_total_limit_validation(self):
        for value in (1000, 10000, 100000):
            sync.validate_options("2026-10-01", "2026-10-01", 25, value)
        for value in (0, -1, "", None, "2.5", 100001):
            with self.assertRaises(ValueError):
                sync.validate_options("2026-10-01", "2026-10-01", 25, value)

    def test_resume_caps_next_batch_to_remaining_total(self):
        self.run.max_call_logs = 1000
        self.run.processed = 997
        with patch.object(frappe.db, "sql", return_value=[]) as sql:
            sync.candidates(self.run, 25)
        self.assertEqual(sql.call_args.args[1][-1], 3)

    def test_reached_limit_does_not_fetch_more_candidates(self):
        self.run.max_call_logs = 1000
        self.run.processed = 1000
        with patch.object(frappe.db, "sql") as sql:
            self.assertEqual(sync.candidates(self.run, 25), [])
        sql.assert_not_called()

    def test_skipped_and_unavailable_count_toward_total(self):
        self.run.max_call_logs = 1
        for outcome in ("Updated", "Skipped", "Unavailable", "Failed"):
            values = sync.progress_values(self.run, self.call, outcome, "Result")
            self.assertEqual(values["status"], "Completed")
            self.assertEqual(values["processed"], 1)
            self.assertEqual(values[sync.COUNTERS[outcome]], 1)
            self.assertIn("Maximum call logs reached", values["last_message"])


class TranscriptSyncDatabaseTests(unittest.TestCase):
    """Local fixtures only; all provider I/O is mocked, no real calls."""
    def setUp(self):
        self.run = frappe.get_doc(dict(doctype=sync.RUN, from_date="1999-01-01",
            to_date="1999-01-01", batch_size=1)).insert(ignore_permissions=True)
        self.call = frappe.get_doc(dict(doctype=sync.CALL, name="TRANSCRIPT-TEST-" + self.run.name,
            creation="1999-01-01 12:00:00", modified="1999-01-01 12:00:00",
            status="Completed", account_id="MA_TEST", call_uuid="test-call-" + self.run.name,
            transcript_text="", transcription_text="", recording_id="test-recording",
            disposition="Manual test disposition", ai_disposition_status="Not Requested",
            user="Administrator", owner="Administrator"))
        self.call.db_insert()
        frappe.db.commit()
        self.payload = dict(text="Test recovery text", transcription_id="test-transcript",
                            call_uuid=self.call.call_uuid, account_id="MA_TEST")
        self.provider = Mock(auth_id="MA_TEST", auth_token="test-only")
        self.provider.lookup.return_value = self.payload

    def tearDown(self):
        frappe.db.rollback()
        frappe.db.delete(sync.RESULT, {"sync_run": self.run.name})
        frappe.db.delete(sync.CALL, {"name": self.call.name})
        frappe.db.delete(sync.RUN, {"name": self.run.name})
        frappe.db.commit()

    def start_fixture(self):
        with patch.object(sync,"get_auth_credentials",return_value=("MA_TEST","test-only")),              patch.object(sync,"enqueue") as enqueue:
            sync.start(self.run.name)
        enqueue.assert_called_once_with(self.run.name)
        rows=sync.candidates(frappe.get_doc(sync.RUN,self.run.name),50)
        self.assertEqual([r.name for r in rows],[self.call.name])

    def test_worker_updates_only_transcript_and_checkpoints_then_completes(self):
        self.start_fixture()
        before=frappe.db.get_value(sync.CALL,self.call.name,
            ["status","disposition","ai_disposition_status","modified"],as_dict=True)
        with patch.object(sync,"TranscriptReader",return_value=self.provider):
            sync.run_batch(self.run.name)
            sync.run_batch(self.run.name)
        after=frappe.db.get_value(sync.CALL,self.call.name,
            ["status","disposition","ai_disposition_status","modified"],as_dict=True)
        self.assertEqual(before,after)
        self.assertEqual(frappe.db.get_value(sync.CALL,self.call.name,"transcript_text"),"Test recovery text")
        state=frappe.get_doc(sync.RUN,self.run.name)
        self.assertEqual((state.status,state.processed,state.updated),("Completed",1,1))
        self.assertEqual(frappe.db.count(sync.RESULT,{"sync_run":self.run.name}),1)

    def test_failed_checkpoint_rolls_back_call_then_resume_recovers_once(self):
        self.start_fixture()
        with patch.object(sync,"TranscriptReader",return_value=self.provider),              patch.object(sync,"checkpoint",side_effect=RuntimeError("test rollback")),              patch.object(frappe,"log_error"):
            with self.assertRaises(RuntimeError):
                sync.run_batch(self.run.name)
        self.assertFalse(frappe.db.get_value(sync.CALL,self.call.name,"transcript_text"))
        self.assertEqual(frappe.db.get_value(sync.RUN,self.run.name,"processed"),0)
        self.assertEqual(frappe.db.get_value(sync.RUN,self.run.name,"status"),"Failed")
        self.assertEqual(frappe.db.count(sync.RESULT,{"sync_run":self.run.name}),0)
        self.start_fixture()
        with patch.object(sync,"TranscriptReader",return_value=self.provider):
            sync.run_batch(self.run.name)
        self.assertEqual(frappe.db.get_value(sync.RUN,self.run.name,"updated"),1)
        self.assertEqual(frappe.db.count(sync.RESULT,{"sync_run":self.run.name}),1)


    def test_total_limit_stops_worker_before_second_call(self):
        second = frappe.get_doc(dict(doctype=sync.CALL, name=self.call.name + "-SECOND",
            creation="1999-01-01 12:00:00", modified="1999-01-01 12:00:00",
            status="Completed", account_id="MA_TEST", call_uuid="test-second",
            transcript_text="", transcription_text="", owner="Administrator"))
        second.db_insert()
        frappe.db.set_value(sync.RUN, self.run.name, {"max_call_logs": 1, "batch_size": 25})
        frappe.db.commit()
        try:
            self.start_fixture()
            with patch.object(sync, "TranscriptReader", return_value=self.provider):
                sync.run_batch(self.run.name)
            state = frappe.get_doc(sync.RUN, self.run.name)
            self.assertEqual((state.status, state.processed, state.updated), ("Completed", 1, 1))
            self.provider.lookup.assert_called_once()
            self.assertFalse(frappe.db.get_value(sync.CALL, second.name, "transcript_text"))
        finally:
            frappe.db.rollback()
            frappe.db.delete(sync.CALL, {"name": second.name})
            frappe.db.commit()
