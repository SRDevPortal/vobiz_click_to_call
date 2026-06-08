(function () {
    const DEFAULT_DOCTYPES = ["CRM Lead", "Contact", "Patient", "Customer"];
    const registeredDoctypes = new Set();

    function install() {
        if (!window.frappe) return;
        frappe.listview_settings = frappe.listview_settings || {};

        loadAllowedDoctypes((doctypes) => {
            doctypes.forEach(registerListDialer);
        });
    }

    function registerListDialer(doctype) {
        if (!doctype || registeredDoctypes.has(doctype)) return;
        registeredDoctypes.add(doctype);

            const existing = frappe.listview_settings[doctype] || {};
            if (existing.__vobiz_extended) return;

            const originalOnload = existing.onload;
            existing.onload = function (listview) {
                if (typeof originalOnload === "function") {
                    originalOnload.call(this, listview);
                }
                addListDialer(listview, doctype);
            };
            existing.__vobiz_extended = true;
            frappe.listview_settings[doctype] = existing;
    }

    function loadAllowedDoctypes(callback) {
        if (!frappe.session || frappe.session.user === "Guest") {
            callback(DEFAULT_DOCTYPES);
            return;
        }

        frappe.call({
            method: "vobiz_click_to_call.api.call.get_allowed_doctypes_api",
        }).then((r) => {
            callback(Array.isArray(r.message) && r.message.length ? r.message : DEFAULT_DOCTYPES);
        });
    }

    function addListDialer(listview, doctype) {
        if (!listview || !listview.page || listview.__vobiz_list_dialer) return;
        listview.__vobiz_list_dialer = true;

        listview.page.add_inner_button(__("Vobiz Call Selected"), () => {
            const selected = getSelected(listview);
            if (!selected.length) {
                frappe.msgprint(__("Select one record to call."));
                return;
            }
            callFromList(doctype, selected[0].name);
        });
    }

    function getSelected(listview) {
        if (typeof listview.get_checked_items === "function") {
            return listview.get_checked_items() || [];
        }
        return [];
    }

    function callFromList(doctype, name) {
        frappe.confirm(__("Start Vobiz call for {0}?", [frappe.utils.escape_html(name)]), () => {
            frappe.call({
                method: "vobiz_click_to_call.api.call.start_call",
                args: {
                    reference_doctype: doctype,
                    reference_name: name,
                },
                freeze: true,
                freeze_message: __("Starting call..."),
            }).then((r) => {
                const message = r.message || {};
                $(document).trigger("vobiz_refresh_availability");
                $(document).trigger("vobiz_list_call_started", [message.call_log]);
                frappe.show_alert({
                    message: __("Call started: {0}", [message.call_log || "Vobiz"]),
                    indicator: "green",
                });
            });
        });
    }

    $(install);
})();
