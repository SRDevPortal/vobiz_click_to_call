frappe.ui.form.on("Vobiz Call Log", {
    refresh(frm) {
        if (frm.is_new()) return;
        renderRecordingPlayer(frm);

        frm.add_custom_button(__("Sync CDR"), () => {
            frappe.call({
                method: "vobiz_click_to_call.api.cdr.sync_call_log",
                args: { call_log: frm.doc.name },
                freeze: true,
                freeze_message: __("Syncing CDR..."),
            }).then(() => {
                frm.reload_doc();
                frappe.show_alert({ message: __("CDR sync completed"), indicator: "green" });
            });
        });

        if (frm.doc.recording_url) {
            frm.add_custom_button(__("Play Recording"), () => {
                openRecordingDialog(frm);
            });
            frm.add_custom_button(__("Open Recording"), () => {
                window.open(recordingStreamUrl(frm.doc.name), "_blank", "noopener=yes");
            });
        }

        if (!frm.doc.disposition) {
            frm.add_custom_button(__("Add Disposition"), () => openDispositionDialog(frm));
        }
    },
});

function recordingStreamUrl(callLog) {
    return `/api/method/vobiz_click_to_call.api.recording.stream?call_log=${encodeURIComponent(callLog)}`;
}

function renderRecordingPlayer(frm) {
    frm.$wrapper.find(".vobiz-recording-player").remove();
    if (!frm.doc.recording_url) return;

    const url = recordingStreamUrl(frm.doc.name);
    const html = `
        <div class="vobiz-recording-player" style="margin: 12px 0; padding: 12px; border: 1px solid var(--border-color); border-radius: 6px;">
            <div style="font-weight: 600; margin-bottom: 8px;">${__("Recording")}</div>
            <audio controls preload="none" src="${frappe.utils.escape_html(url)}" style="width: 100%; display: block;"></audio>
        </div>
    `;

    const field = frm.get_field("recording_url");
    if (field && field.$wrapper) {
        field.$wrapper.after(html);
    } else {
        frm.layout.wrapper.find(".form-layout").first().prepend(html);
    }
}

function openRecordingDialog(frm) {
    const url = recordingStreamUrl(frm.doc.name);
    const dialog = new frappe.ui.Dialog({
        title: __("Recording"),
        fields: [
            {
                fieldname: "player",
                fieldtype: "HTML",
                options: `<audio controls autoplay preload="metadata" src="${frappe.utils.escape_html(url)}" style="width: 100%;"></audio>`,
            },
        ],
    });
    dialog.show();
}

function openDispositionDialog(frm) {
    loadDispositionOptions((options) => {
    const dialog = new frappe.ui.Dialog({
        title: __("Post-call Disposition"),
        fields: [
            {
                fieldname: "disposition",
                fieldtype: "Select",
                label: __("Disposition"),
                reqd: 1,
                options: options.join("\n"),
            },
            {
                fieldname: "notes",
                fieldtype: "Small Text",
                label: __("Call Notes"),
                reqd: 1,
            },
            {
                fieldname: "follow_up_datetime",
                fieldtype: "Datetime",
                label: __("Next Follow-up"),
            },
            {
                fieldname: "mark_dnd",
                fieldtype: "Check",
                label: __("Mark number Do Not Call"),
            },
        ],
        primary_action_label: __("Save"),
        primary_action(values) {
            frappe.call({
                method: "vobiz_click_to_call.api.disposition.save_disposition",
                args: {
                    call_log: frm.doc.name,
                    disposition: values.disposition,
                    notes: values.notes,
                    follow_up_datetime: values.follow_up_datetime,
                    mark_dnd: values.mark_dnd ? 1 : 0,
                },
                freeze: true,
                freeze_message: __("Saving disposition..."),
            }).then(() => {
                dialog.hide();
                frm.reload_doc();
                frappe.show_alert({ message: __("Disposition saved"), indicator: "green" });
            });
        },
    });
    dialog.show();
    });
}

function loadDispositionOptions(callback) {
    frappe.call({
        method: "vobiz_click_to_call.api.disposition.get_disposition_options_api",
    }).then((r) => {
        const options = Array.isArray(r.message) && r.message.length
            ? r.message
            : ["Connected", "No Answer", "Busy", "Failed", "Wrong Number", "Not Interested", "Interested", "Follow Up Required", "Converted", "Call Back Later", "Language Issue", "Duplicate Lead", "Invalid Number", "Do Not Call"];
        callback(options);
    });
}
