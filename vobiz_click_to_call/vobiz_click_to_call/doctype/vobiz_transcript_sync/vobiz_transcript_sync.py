import frappe
from frappe.model.document import Document


class VobizTranscriptSync(Document):
    def validate(self):
        from vobiz_click_to_call.services.transcript_sync import validate_options
        validate_options(self.from_date, self.to_date, self.batch_size, self.max_call_logs)
        previous = self.get_doc_before_save()
        runtime = [f.fieldname for f in self.meta.fields if f.read_only]
        if not previous:
            if self.status != "Draft" or any(self.get(k) for k in runtime if k != "status"):
                frappe.throw("Create a draft sync first; use Start to begin.")
        else:
            if any(self.get(k) != previous.get(k) for k in runtime):
                frappe.throw("Use the sync controls to change progress or status.")
            if previous.status != "Draft" and any(self.get(k) != previous.get(k) for k in ("from_date", "to_date", "batch_size", "max_call_logs")):
                frappe.throw("Create a new sync to change the date range, total limit or batch size.")
