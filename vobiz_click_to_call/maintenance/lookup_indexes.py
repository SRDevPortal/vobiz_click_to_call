"""Controlled provider-identity index repair; no automatic large-table DDL."""
from vobiz_ai.maintenance.lookup_indexes import repair_specs

# Composite indexes survive Frappe's reconciliation of single-column search_index metadata.
# Equality on provider identity/source is followed by the actual newest-first ordering.
TARGETS = {
    "recording": ("tabVobiz Call Log", "idx_vctc_recording_source_creation", ("recording_id", "source_app", "creation")),
    "transcription": ("tabVobiz Call Log", "idx_vctc_transcription_source_creation", ("transcription_id", "source_app", "creation")),
}


def repair(target=None, apply=False):
    return repair_specs(TARGETS, target, apply)
