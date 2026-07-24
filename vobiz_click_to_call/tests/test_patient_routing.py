from __future__ import annotations

import unittest

from vobiz_click_to_call.services.patient_routing import patient_matches_mapping


class TestPatientRouting(unittest.TestCase):
    mapping = {
        "sr_medical_departments": "Regional\nAyurveda",
        "sr_followup_ids": "0\n2",
        "sr_dpt_diseases": "Psoriasis\nEczema",
        "sr_dpt_languages": "Hindi\nEnglish",
    }

    def test_non_regional_patient_ignores_disease_and_language(self):
        patient = {
            "sr_medical_department": "Ayurveda",
            "sr_followup_id": "2",
            "sr_dpt_disease": "",
            "sr_dpt_language": "",
        }
        self.assertTrue(patient_matches_mapping(patient, self.mapping))

    def test_regional_patient_requires_matching_disease_and_language(self):
        patient = {
            "sr_medical_department": "Regional",
            "sr_followup_id": "0",
            "sr_dpt_disease": "Psoriasis",
            "sr_dpt_language": "Hindi",
        }
        self.assertTrue(patient_matches_mapping(patient, self.mapping))
        patient["sr_dpt_language"] = "Tamil"
        self.assertFalse(patient_matches_mapping(patient, self.mapping))

    def test_regional_patient_with_missing_classification_is_not_visible(self):
        patient = {
            "sr_medical_department": "Regional",
            "sr_followup_id": "2",
            "sr_dpt_disease": "",
            "sr_dpt_language": "Hindi",
        }
        self.assertFalse(patient_matches_mapping(patient, self.mapping))
