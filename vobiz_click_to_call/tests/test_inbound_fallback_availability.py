from __future__ import annotations

import unittest
from pathlib import Path


class TestInboundFallbackAvailability(unittest.TestCase):
    def test_every_fallback_path_requires_agent_availability(self):
        source = (
            Path(__file__).resolve().parents[1] / "api" / "inbound.py"
        ).read_text(encoding="utf-8")

        patient_start = source.index("def resolve_patient_inbound_target")
        patient_primary_loop = source.index("    for mapping in mappings:", patient_start)
        patient_fallback_loop = source.index("    for mapping in mappings:", patient_primary_loop + 1)
        lead_start = source.index("def resolve_lead_owner_inbound_target")
        lead_fallback_loop = source.index("    for fallback_user in _fallback_users(mapping):", lead_start)
        general_start = source.index("def resolve_inbound_target")
        general_fallback_loop = source.index("    for fallback_user in _fallback_users(mapping):", general_start)
        next_start = source.index("def _next_inbound_fallback")
        next_fallback_loop = source.index("    for fallback_user in _fallback_users(mapping):", next_start)
        sections = (
            source[patient_fallback_loop:source.index("    end_mobile =", patient_fallback_loop)],
            source[lead_fallback_loop:source.index("    end_mobile =", lead_fallback_loop)],
            source[general_fallback_loop:source.index("    busy_ai_mobile =", general_fallback_loop)],
            source[next_fallback_loop:source.index("    busy_ai_mobile =", next_fallback_loop)],
        )

        for fallback_source in sections:
            self.assertIn("fallback_mapping and fallback_mobile", fallback_source)
            self.assertIn("_mapping_can_receive(fallback_user, fallback_mapping)", fallback_source)

    def test_primary_paths_still_require_availability(self):
        source = (
            Path(__file__).resolve().parents[1] / "api" / "inbound.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "mapping and primary_mobile and _mapping_can_receive(previous.user, mapping)",
            source,
        )
        self.assertIn(
            "mapping and primary_mobile and _mapping_can_receive(owner, mapping)",
            source,
        )

    def test_availability_contract_includes_console_and_mapping_state(self):
        source = (Path(__file__).resolve().parents[1] / "api" / "inbound.py").read_text(encoding="utf-8")
        start = source.index("def _mapping_can_receive")
        end = source.index("\ndef _start_recording_safely", start)
        helper = source[start:end]

        self.assertIn("is_agent_console_online(user)", helper)
        self.assertIn("get_mapping_unavailable_reason(mapping)", helper)

    def test_ai_agent_end_fallback_bypasses_only_ai_availability(self):
        app = Path(__file__).resolve().parents[1]
        source = (app / "api" / "inbound.py").read_text(encoding="utf-8")
        mapping_json = (
            app
            / "vobiz_click_to_call"
            / "doctype"
            / "vobiz_user_mapping"
            / "vobiz_user_mapping.json"
        ).read_text(encoding="utf-8")

        start = source.index("def _ai_agent_end_fallback_target")
        end = source.index("\ndef _mapping_can_receive", start)
        helper = source[start:end]

        self.assertIn('"fieldname": "ai_agent_end_fallback"', mapping_json)
        self.assertIn("ai_mapping = get_user_mapping(ai_user)", helper)
        self.assertIn('ai_mobile = _mapping_mobile(ai_mapping, "")', helper)
        self.assertIn('"is_mapped_agent": False', helper)
        self.assertNotIn("_mapping_can_receive(ai_user", helper)
        self.assertIn("_mapping_can_receive(fallback_user, fallback_mapping)", source)

    def test_logout_marks_the_mapping_offline(self):
        app = Path(__file__).resolve().parents[1]
        hooks = (app / "hooks.py").read_text(encoding="utf-8")
        mapping = (
            app / "vobiz_click_to_call" / "doctype" / "vobiz_user_mapping" / "vobiz_user_mapping.py"
        ).read_text(encoding="utf-8")

        self.assertIn("mark_user_offline_on_logout", hooks)
        self.assertIn('"availability_status": "Offline"', mapping)
        self.assertIn('"accept_calls": 0', mapping)
