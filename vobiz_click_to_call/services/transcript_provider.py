"""Read existing Vobiz transcripts. Never generate transcripts or fetch arbitrary URLs."""
import json
import time
from urllib.parse import quote

import requests
from vobiz_click_to_call.services.client import ProviderTemporaryError, VobizClient

MAX_RESPONSE = 2 * 1024 * 1024
MAX_TEXT = 1024 * 1024


class TranscriptLookupError(Exception):
    pass


class TranscriptAccessError(TranscriptLookupError):
    """Stop the run rather than marking every call unavailable."""
    pass


def text_value(value):
    if isinstance(value, dict) and value.get("Valid") is True:
        value = value.get("String")
    return value.strip() if isinstance(value, str) else ""


class TranscriptReader(VobizClient):
    def read_json(self, path, params=None):
        from vobiz_click_to_call.services.recovery_policy import claim_provider_read
        claim_provider_read(self.auth_id)
        deadline = time.monotonic() + 20
        try:
            with requests.get(
                f"{self.base_url}/Account/{quote(self.auth_id, safe='')}/{path}",
                params=params or {},
                headers={"X-Auth-ID": self.auth_id, "X-Auth-Token": self.auth_token},
                timeout=(5, 10), stream=True, allow_redirects=False,
            ) as response:
                self._raise_temporary(response, "transcript read")
                if response.status_code in (401, 403) or (response.status_code == 404 and path == "Transcriptions/"):
                    raise TranscriptAccessError(f"Transcript API access failed (HTTP {response.status_code}).")
                if response.status_code == 404:
                    return {}
                if response.status_code != 200:
                    # Never expose provider bodies, signed URLs or secrets in job messages.
                    raise TranscriptLookupError(f"Provider transcript API returned HTTP {response.status_code}.")
                chunks, size = [], 0
                for chunk in response.iter_content(16384):
                    size += len(chunk)
                    if size > MAX_RESPONSE or time.monotonic() > deadline:
                        raise TranscriptLookupError("Provider transcript response exceeded the size/time limit.")
                    chunks.append(chunk)
                try:
                    data = json.loads(b"".join(chunks))
                except (ValueError, UnicodeError):
                    raise TranscriptLookupError("Provider transcript API returned invalid JSON.") from None
                if not isinstance(data, dict):
                    raise TranscriptLookupError("Provider transcript API returned an unexpected response.")
                return data
        except (requests.Timeout, requests.ConnectionError):
            raise ProviderTemporaryError("Temporary transcript API connection failure.", source="transport") from None
        except requests.RequestException:
            raise TranscriptLookupError("Transcript API request failed.") from None

    def lookup(self, doc):
        # Only trusted server-side provider IDs; never telephone or time matching.
        ids = list(dict.fromkeys(str(doc.get(k) or "").strip() for k in
                    ("recording_call_uuid", "call_uuid", "b_leg_uuid", "a_leg_uuid")))
        ids = [value for value in ids if value]
        if not ids:
            return None
        for call_uuid in ids:
            data = self.read_json("Transcriptions/", {"call_uuid": call_uuid, "limit": 50, "offset": 0})
            rows = data.get("objects")
            if not isinstance(rows, list):
                raise TranscriptLookupError("Provider transcript list has an unexpected format.")
            # Do not silently choose an incomplete/ambiguous page.
            if data.get("meta", {}).get("next"):
                raise TranscriptLookupError("Too many transcripts for this call; manual review required.")
            relevant = [row for row in rows if isinstance(row, dict)
                        and row.get("call_uuid") == call_uuid and row.get("account_id") == self.auth_id]
            if len(relevant) > 1:
                raise TranscriptLookupError("Multiple transcripts match this call; manual review required.")
            matches = []
            for row in relevant:
                if not isinstance(row, dict) or row.get("call_uuid") != call_uuid:
                    continue
                if row.get("account_id") != self.auth_id:
                    continue
                if doc.get("recording_id") and row.get("recording_id") and row["recording_id"] != doc["recording_id"]:
                    continue
                state = str(row.get("transcription_status") or "").lower()
                if state in ("failed", "error", "pending", "processing", "queued", "in_progress"):
                    continue
                text = text_value(row.get("transcription_text"))
                transcription_id = str(row.get("transcription_id") or "")
                if not text and transcription_id:
                    detail = self.read_json(f"Transcription/{quote(transcription_id, safe='')}/transcription/")
                    if str(detail.get("status") or "").lower() in ("success", "completed", "complete"):
                        text = text_value(detail.get("transcription"))
                if text:
                    if len(text.encode("utf-8")) > MAX_TEXT:
                        raise TranscriptLookupError("Transcript exceeds the supported size.")
                    matches.append(dict(text=text, transcription_id=transcription_id, call_uuid=call_uuid,
                                        account_id=self.auth_id))
            unique = {(x["transcription_id"], x["text"]) for x in matches}
            if len(unique) > 1:
                raise TranscriptLookupError("Multiple transcripts match this call; manual review required.")
            if matches:
                return matches[0]
        return None
