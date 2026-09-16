"""Provider uncertainty is metadata, never a terminal call outcome."""
import frappe

LEGACY_STATUSES = frozenset({"Confirmation Pending", "Provider Unconfirmed"})
PENDING = "provider-confirmation-pending"


def confirmation_pending(doc):
    return doc.get("status") in LEGACY_STATUSES or (
        doc.get("status") in {"Queued", "Initiated"} and doc.get("call_status") == PENDING
    )


def normalize_legacy_status(doc, method=None):
    """Also protects callback saves of rows written by older app versions."""
    if doc.get("status") not in LEGACY_STATUSES:
        return
    doc.status = "Queued"
    # Preserve cancellation intent and other provider/browser evidence.
    if not doc.get("call_status"):
        doc.call_status = PENDING
    note = "Provider confirmation pending. A timeout does not confirm that the call ended."
    if note not in (doc.get("error_message") or ""):
        doc.error_message = note + ("\n" + doc.error_message if doc.get("error_message") else "")


def normalize_existing(doc):
    if doc.get("status") in LEGACY_STATUSES:
        # Reload under a row lock so a concurrent terminal webhook always wins.
        current = frappe.get_doc("Vobiz Call Log", doc.name, for_update=True)
        if current.status in LEGACY_STATUSES:
            normalize_legacy_status(current)
            current.save(ignore_permissions=True)
        frappe.db.commit()
        doc.reload()
