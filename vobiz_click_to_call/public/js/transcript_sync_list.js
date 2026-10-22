// Preserve any existing Vobiz AI list configuration.
(() => {
    const settings = frappe.listview_settings["Vobiz Call Log"] || {};
    const previous = settings.onload;
    settings.onload = function (listview) {
        if (previous) previous.call(this, listview);
        if (frappe.user.has_role("System Manager")) {
            listview.page.add_inner_button(__("Sync Missing Transcriptions"), () => {
                frappe.set_route("List", "Vobiz Transcript Sync");
            });
        }
    };
    frappe.listview_settings["Vobiz Call Log"] = settings;
})();
