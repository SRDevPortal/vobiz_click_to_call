from __future__ import annotations

from typing import Any


REGIONAL_DEPARTMENT = "Regional"


def split_mapping_values(value: str | None, first: str | None = None) -> list[str]:
    values = []
    seen = set()
    for raw in (first or "", value or ""):
        for row in str(raw).replace(",", "\n").splitlines():
            row = row.strip()
            if row and row not in seen:
                values.append(row)
                seen.add(row)
    return values


def patient_matches_mapping(patient: Any, mapping: Any) -> bool:
    department = _value(patient, "sr_medical_department")
    followup_id = _value(patient, "sr_followup_id")
    departments = split_mapping_values(
        _value(mapping, "sr_medical_departments"),
        first=_value(mapping, "sr_medical_department"),
    )
    followup_ids = split_mapping_values(
        _value(mapping, "sr_followup_ids"),
        first=_value(mapping, "sr_followup_id"),
    )
    if not department or department not in departments:
        return False
    if not followup_id or followup_id not in followup_ids:
        return False
    if department != REGIONAL_DEPARTMENT:
        return True

    disease = _value(patient, "sr_dpt_disease")
    language = _value(patient, "sr_dpt_language")
    diseases = split_mapping_values(
        _value(mapping, "sr_dpt_diseases"),
        first=_value(mapping, "sr_dpt_disease"),
    )
    languages = split_mapping_values(
        _value(mapping, "sr_dpt_languages"),
        first=_value(mapping, "sr_dpt_language"),
    )
    return bool(disease and disease in diseases and language and language in languages)


def _value(record: Any, fieldname: str) -> str:
    if hasattr(record, "get"):
        value = record.get(fieldname)
    else:
        value = getattr(record, fieldname, None)
    return "" if value is None else str(value).strip()
