frappe.ui.form.on("Vobiz Transcript Sync", {
    onload(frm) {
        if (frm.is_new()) {
            if (!frm.doc.from_date) frm.set_value("from_date", frappe.datetime.add_days(frappe.datetime.get_today(), -1));
            if (!frm.doc.to_date) frm.set_value("to_date", frappe.datetime.get_today());
        }
    },
    refresh(frm) {
        const draft = frm.doc.status === "Draft";
        ["from_date", "to_date", "batch_size", "max_call_logs"].forEach(field => frm.set_df_property(field, "read_only", !draft));
        frm.set_intro(__("Maximum call logs is the total checked across all batches, including skipped logs. Recovers existing transcripts only. Manual dispositions and call availability are unchanged. Dates use call log creation time. A long-queue worker and scheduler are required."));
        if (frm.is_new()) return;
        const invoke = async action => {
            await frappe.call({
                method: "vobiz_click_to_call.services.transcript_sync." + action,
                args: {name: frm.doc.name},
                freeze: true
            });
            await frm.reload_doc();
        };
        if (draft || ["Paused", "Failed"].includes(frm.doc.status)) {
            frm.add_custom_button(__(draft ? "Start Sync" : "Resume"), async () => {
                if (frm.is_dirty()) await frm.save();
                await invoke("start");
                frappe.show_alert({message: __("Transcript sync queued in the background."), indicator: "blue"});
            });
        }
        if (["Queued", "Running"].includes(frm.doc.status)) {
            frm.add_custom_button(__("Pause"), () => invoke("pause"));
        }
        frm.add_custom_button(__("Preview Next Calls"), async () => {
            if (frm.is_dirty()) await frm.save();
            const response = await frappe.call({
                method: "vobiz_click_to_call.services.transcript_sync.preview",
                args: {name: frm.doc.name}
            });
            const data = response.message;
            frappe.msgprint(__("Of the next {0} call logs, {1} have missing transcripts and are eligible. This is a sample, not a total count. No provider requests were made.", [data.checked, data.eligible]));
        });
        frm.add_custom_button(__("Refresh Progress"), () => frm.reload_doc());
        frm.add_custom_button(__("View Results"), () => {
            frappe.route_options = {sync_run: frm.doc.name};
            frappe.set_route("List", "Vobiz Transcript Sync Result");
        });
    }
});
