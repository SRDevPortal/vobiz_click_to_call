"""Encounter-scoped outcomes; no clinical, follow-up-status or shipment writes."""
import json
import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime
from vobiz_click_to_call.services.queue_sources import enabled_sources
TERMINAL = {"Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"}

def tags():
    return frappe.get_all("NDR Tag", filters={"enabled": 1}, pluck="name", order_by="name asc")

def hangup_time(call):
    if call.get("ndr_hangup_at"):
        return call.ndr_hangup_at
    if call.status not in TERMINAL:
        return None
    try:
        context = json.loads(call.get("request_json") or "{}")
    except (TypeError, ValueError):
        context = {}
    reset = context.get("manual_local_reset") or {}
    value = call.get("end_time") or reset.get("at") or call.get("modified") or now_datetime()
    value = get_datetime(value)
    frappe.db.set_value("Vobiz Call Log", call.name, "ndr_hangup_at", value, update_modified=False)
    call.ndr_hangup_at = value
    return value

def validate_values(tag, notes, follow_up_at, options):
    if tag not in options:
        frappe.throw(_("Select a valid NDR tag."))
    if tag in {"Reattempt", "Address Update", "Refused", "Escalate", "Reattempt Requested", "Address Correction Required", "Customer Refused Delivery", "Escalation Required"} and not notes:
        frappe.throw(_("A note is required for this outcome."))
    if len(notes) > 4000:
        frappe.throw(_("Keep the note within 4000 characters."))

def validate_start(encounter, reference_doctype, reference_name, retry_of=None):
    if not encounter:
        return None
    previous = frappe.get_doc("Vobiz Call Log", retry_of) if retry_of else None
    if previous and (previous.user != frappe.session.user or previous.get("ndr_encounter") != encounter):
        frappe.throw(_("Invalid NDR retry."), frappe.PermissionError)
    if not previous and not frappe.get_single("Vobiz Settings").get("enable_ndr_outcomes"):
        return None
    if "NDR" not in enabled_sources():
        frappe.throw(_("NDR mapping is required."), frappe.PermissionError)
    doc = frappe.get_doc("Patient Encounter", encounter)
    doc.check_permission("read")
    if reference_doctype != "Patient" or doc.patient != reference_name:
        frappe.throw(_("The NDR encounter does not belong to this patient."), frappe.PermissionError)
    if not previous:
        from vobiz_click_to_call.api.encounter_queue import _settings, configuration
        columns, filters, catalog = configuration(_settings("ndr"), "ndr")
        if not frappe.get_list("Patient Encounter", filters=filters + [["name", "=", encounter]], pluck="name", page_length=1):
            frappe.throw(_("Encounter is no longer in NDR."), frappe.PermissionError)
    return encounter

def attach(call, encounter):
    if encounter:
        frappe.db.set_value("Vobiz Call Log", call.name, {"ndr_encounter": encounter, "ndr_outcome_state": "Pending"})
        call.ndr_encounter = encounter
        call.ndr_outcome_state = "Pending"

def authorized_call(name):
    if frappe.session.user == "Guest" or "NDR" not in enabled_sources():
        frappe.throw(_("NDR access required."), frappe.PermissionError)
    call = frappe.get_doc("Vobiz Call Log", name)
    if call.user != frappe.session.user and "System Manager" not in frappe.get_roles():
        frappe.throw(_("This call belongs to another agent."), frappe.PermissionError)
    if not call.get("ndr_encounter"):
        frappe.throw(_("This is not an NDR call."))
    encounter = frappe.get_doc("Patient Encounter", call.ndr_encounter)
    encounter.check_permission("read")
    if call.reference_doctype != "Patient" or call.reference_name != encounter.patient:
        frappe.throw(_("Invalid call encounter relationship."), frappe.PermissionError)
    return call, encounter

@frappe.whitelist()
def get_context(call_log):
    call = frappe.get_doc("Vobiz Call Log", call_log)
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."), frappe.PermissionError)
    if call.user != frappe.session.user:
        call.check_permission("read")
    if not call.get("ndr_encounter"):
        return {"ndr": False}
    call, encounter = authorized_call(call_log)
    return {"ndr": True, "saved": call.ndr_outcome_state == "Saved",
            "terminal": call.status in TERMINAL, "encounter": encounter.name,
            "order_id": encounter.get("pe_shipkia_order_id"), "awb": encounter.get("pe_shipkia_awb_number"),
            "follow_up_at": hangup_time(call), "tags": tags(), "history": frappe.get_all("NDR Interaction Log",
                filters={"patient_encounter": encounter.name},
                fields=["tag", "notes", "follow_up_at", "callback_at", "agent", "creation"], order_by="creation desc", limit=10)}

@frappe.whitelist()
def pending():
    if "NDR" not in enabled_sources():
        return []
    if not frappe.db.has_column("Vobiz Call Log", "ndr_outcome_state"):
        return []
    return frappe.get_all("Vobiz Call Log",
        filters={"user": frappe.session.user, "ndr_outcome_state": "Pending", "status": ["in", list(TERMINAL)]},
        fields=["name"], order_by="creation asc", limit=20)

@frappe.whitelist(methods=["POST"])
def save(call_log, tag, notes="", follow_up_at=None, correct=0, callback_at=None):
    call, encounter = authorized_call(call_log)
    correct = frappe.utils.cint(correct)
    if correct and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only System Managers can correct a saved outcome."), frappe.PermissionError)
    if call.status not in TERMINAL:
        frappe.throw(_("End the call before saving its NDR outcome."))
    frappe.db.sql("select name from `tabPatient Encounter` where name=%s for update", encounter.name)
    frappe.db.sql("select name from `tabVobiz Call Log` where name=%s for update", call.name)
    existing = frappe.db.get_value("NDR Interaction Log", {"call_log": call.name}, "name")
    if existing and not correct:
        return {"name": existing, "duplicate": True}
    # Form-encoded requests may turn a JSON null into an empty string.
    # db.set_value does not normalize blank Datetime values like Document.save.
    callback_at = callback_at.strip() if isinstance(callback_at, str) else callback_at
    callback_at = callback_at or None
    follow_up_at = hangup_time(call)
    notes = (notes or "").strip()
    validate_values(tag, notes, follow_up_at, tags())
    if callback_at:
        if tag != "Callback":
            frappe.throw(_("Callback time is only applicable to the Callback tag."))
        callback_at = get_datetime(callback_at)
        if callback_at <= now_datetime():
            frappe.throw(_("Callback time must be in the future."))
    if existing:
        log = frappe.get_doc("NDR Interaction Log", existing)
        log.update({"tag": tag, "notes": notes, "follow_up_at": follow_up_at, "callback_at": callback_at})
        log.save(ignore_permissions=True)  # Track Changes retains the correction audit.
    else:
        log = frappe.get_doc(dict(doctype="NDR Interaction Log", call_log=call.name,
            patient_encounter=encounter.name, tag=tag, notes=notes, follow_up_at=follow_up_at,
            agent=frappe.session.user, call_started_at=call.creation, callback_at=callback_at)).insert(ignore_permissions=True)
    encounter.reload()
    latest = frappe.db.get_value("Vobiz Call Log", encounter.get("ndr_latest_call"), "creation") if encounter.get("ndr_latest_call") else None
    if not latest or get_datetime(call.creation) >= get_datetime(latest):
        frappe.db.set_value("Patient Encounter", encounter.name, {
            "ndr_tag": tag, "ndr_note": notes, "ndr_follow_up_at": follow_up_at,
            "ndr_updated_by": frappe.session.user, "ndr_updated_at": now_datetime(),
            "ndr_latest_call": call.name, "ndr_callback_at": callback_at}, update_modified=False)
    frappe.db.set_value("Vobiz Call Log", call.name, {"ndr_outcome_state": "Saved"})
    return {"name": log.name}


@frappe.whitelist()
def history(encounter):
    if "NDR" not in enabled_sources() and "System Manager" not in frappe.get_roles():
        frappe.throw(_("NDR access required."), frappe.PermissionError)
    doc = frappe.get_doc("Patient Encounter", encounter)
    doc.check_permission("read")
    return frappe.get_all("NDR Interaction Log", filters={"patient_encounter": doc.name},
        fields=["call_log", "tag", "notes", "follow_up_at", "callback_at", "agent", "creation"],
        order_by="creation desc", limit=50)


@frappe.whitelist()
def history_access():
    return bool(frappe.session.user != "Guest"
                and ("NDR" in enabled_sources() or "System Manager" in frappe.get_roles())
                and frappe.has_permission("Patient Encounter", "read"))


@frappe.whitelist()
def form_summary(encounter):
    doc = frappe.get_doc("Patient Encounter", encounter)
    doc.check_permission("read")
    return {"history_access": history_access(),
            "handled_by": frappe.db.get_value("User", doc.get("ndr_updated_by"), "full_name") if doc.get("ndr_updated_by") else ""}
