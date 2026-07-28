(function () {
    const FIELD_CANDIDATES = [
        "mobile_no",
        "mobile",
        "phone",
        "phone_no",
        "contact_mobile",
        "contact_phone",
        "whatsapp_no",
        "sr_mobile_no",
        "sr_whatsapp_no",
        "alternate_phone",
    ];

    const DEFAULT_DOCTYPES = ["CRM Lead", "Contact", "Patient", "Customer"];
    let DOCTYPES = DEFAULT_DOCTYPES.slice();
    const TERMINAL_STATUSES = ["Completed", "Failed", "Busy", "No Answer", "Cancelled"];
    let currentPoller = null;
    let statusPollInFlight = false;
    const registeredDoctypes = new Set();
    let allowedDoctypesLoaded = false;

    function currentRoute() {
        if (!window.frappe || !frappe.get_route) return [];
        return frappe.get_route() || [];
    }

    function isDeskHome() {
        return window.location && window.location.pathname === "/app/home";
    }

    function shouldLoadAllowedDoctypes() {
        if (isDeskHome()) return false;
        const route = currentRoute();
        return route[0] === "Form" || route[0] === "List";
    }

    function ensureStyles() {
        if ($("#vobiz-click-to-call-style").length) return;

        $("head").append(`
            <style id="vobiz-click-to-call-style">
                .vobiz-call-host {
                    position: relative;
                }
                .vobiz-call-host input,
                .vobiz-call-host textarea {
                    padding-right: 34px;
                }
                .vobiz-call-btn {
                    align-items: center;
                    background: var(--control-bg, #f7fafc);
                    border: 1px solid var(--border-color, #d1d8dd);
                    border-radius: 50%;
                    color: var(--text-color, #36414c);
                    cursor: pointer;
                    display: inline-flex;
                    height: 26px;
                    justify-content: center;
                    line-height: 1;
                    padding: 0;
                    width: 26px;
                }
                .vobiz-call-btn:hover {
                    background: var(--btn-default-hover-bg, #eef2f7);
                    color: var(--primary, #2490ef);
                }
                .vobiz-field-call {
                    position: absolute;
                    right: 4px;
                    top: 50%;
                    transform: translateY(-50%);
                    z-index: 3;
                }
                .control-value .vobiz-field-call {
                    margin-left: 8px;
                    position: static;
                    transform: none;
                    vertical-align: middle;
                }
                .vobiz-grid-call {
                    height: 22px;
                    margin-left: 6px;
                    min-width: 22px;
                    width: 22px;
                }
                .vobiz-live-panel {
                    background: var(--card-bg, #fff);
                    border: 1px solid var(--border-color, #d1d8dd);
                    border-radius: 8px;
                    bottom: 24px;
                    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.16);
                    min-width: 300px;
                    padding: 12px;
                    position: fixed;
                    right: 24px;
                    z-index: 1050;
                }
                .vobiz-live-panel-title {
                    align-items: center;
                    display: flex;
                    font-weight: 600;
                    gap: 8px;
                    justify-content: space-between;
                    margin-bottom: 8px;
                }
                .vobiz-live-status {
                    font-size: 13px;
                    margin-bottom: 8px;
                }
                .vobiz-live-meta {
                    color: var(--text-muted, #6b7280);
                    font-size: 12px;
                    margin-bottom: 10px;
                }
                .vobiz-live-actions {
                    display: flex;
                    gap: 8px;
                    justify-content: flex-end;
                }
                .vobiz-call-history-section {
                    border-top: 1px solid var(--border-color, #d1d8dd);
                    margin-top: 10px;
                    padding-top: 10px;
                }
                .vobiz-call-history-title {
                    align-items: center;
                    display: flex;
                    font-weight: 600;
                    justify-content: space-between;
                    margin-bottom: 8px;
                }
                .vobiz-call-history-stats {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 8px;
                    margin-bottom: 8px;
                }
                .vobiz-call-history-stat {
                    background: var(--control-bg, #f7fafc);
                    border: 1px solid var(--border-color, #d1d8dd);
                    border-radius: 6px;
                    font-size: 12px;
                    padding: 4px 8px;
                }
                .vobiz-call-history-row {
                    align-items: center;
                    display: grid;
                    gap: 8px;
                    grid-template-columns: minmax(110px, 1fr) 90px 120px 80px;
                    padding: 5px 0;
                }
                .vobiz-call-history-row:not(:last-child) {
                    border-bottom: 1px solid var(--border-color, #edf0f2);
                }
                @media (max-width: 768px) {
                    .vobiz-live-panel {
                        bottom: 12px;
                        left: 12px;
                        min-width: 0;
                        right: 12px;
                    }
                    .vobiz-call-history-row {
                        grid-template-columns: 1fr;
                    }
                }
            </style>
        `);
    }

    function setupForm(frm) {
        ensureStyles();
        bindGridRender(frm);

        if (frm.is_new()) {
            removeButtons(frm);
            return;
        }

        frappe.call({
            method: "vobiz_click_to_call.api.call.get_call_capability",
            args: {
                reference_doctype: frm.doctype,
                reference_name: frm.doc.name,
            },
        }).then((r) => {
            frm.vobiz_call_capability = r.message || {};
            renderButtons(frm);
            renderCallHistory(frm);
        });
    }

    function bindGridRender(frm) {
        if (frm.__vobiz_grid_bound) return;
        frm.__vobiz_grid_bound = true;
        $(frm.wrapper).on("grid-row-render.vobiz-click-to-call", function () {
            window.setTimeout(() => renderGridButtons(frm), 50);
        });
    }

    function renderButtons(frm) {
        removeButtons(frm);

        const capability = frm.vobiz_call_capability || {};
        if (!capability.can_call) return;

        FIELD_CANDIDATES.forEach((fieldname) => attachFieldButton(frm, fieldname));
        renderGridButtons(frm);
    }

    function removeButtons(frm) {
        $(frm.wrapper).find(".vobiz-call-btn").remove();
        $(frm.wrapper).find(".vobiz-call-host").removeClass("vobiz-call-host");
    }

    function attachFieldButton(frm, fieldname) {
        const control = frm.fields_dict[fieldname];
        if (!control || !frm.doc[fieldname]) return;

        let $host = control.$wrapper.find(".control-input-wrapper:visible").first();
        if (!$host.length) {
            $host = control.$wrapper.find(".control-value:visible").first();
        }
        if (!$host.length) return;

        $host.addClass("vobiz-call-host");
        const $button = makeButton("vobiz-field-call").attr("data-fieldname", fieldname);
        $button.on("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            confirmAndStartCall(frm, {
                phone_field: fieldname,
                phone_number: frm.doc[fieldname],
            });
        });
        $host.append($button);
    }

    function renderGridButtons(frm) {
        const capability = frm.vobiz_call_capability || {};
        if (!capability.can_call) return;

        const table = frm.fields_dict.phone_nos;
        if (!table || !table.grid || !table.grid.grid_rows) return;

        table.grid.grid_rows.forEach((gridRow) => {
            const row = gridRow.doc;
            if (!row || !row.phone) return;

            const $cell = gridRow.wrapper.find('[data-fieldname="phone"]').first();
            if (!$cell.length || $cell.find(".vobiz-grid-call").length) return;

            const $button = makeButton("vobiz-grid-call");
            $button.on("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                confirmAndStartCall(frm, {
                    phone_number: row.phone,
                });
            });

            const $static = $cell.find(".static-area").first();
            ($static.length ? $static : $cell).append($button);
        });
    }

    function makeButton(extraClass) {
        return $(`
            <button class="vobiz-call-btn ${extraClass}" type="button" title="${__("Call with Vobiz")}" aria-label="${__("Call with Vobiz")}">
                <i class="fa fa-phone"></i>
            </button>
        `);
    }

    function confirmAndStartCall(frm, args) {
        const number = args.phone_number || frm.doc[args.phone_field] || "";
        frappe.confirm(__("Call {0}?", [frappe.utils.escape_html(number)]), () => {
            frappe.call({
                method: "vobiz_click_to_call.api.call.start_call",
                args: {
                    reference_doctype: frm.doctype,
                    reference_name: frm.doc.name,
                    phone_field: args.phone_field,
                    phone_number: args.phone_number,
                },
                freeze: true,
                freeze_message: __("Starting call..."),
            }).then((r) => {
                const message = r.message || {};
                $(document).trigger("vobiz_refresh_availability");
                showLivePanel(message.call_log, frm);
                frappe.show_alert({
                    message: __("Call started: {0}", [message.call_log || "Vobiz"]),
                    indicator: "green",
                });
            });
        });
    }

    function renderCallHistory(frm) {
        if (!frm.dashboard || !frm.dashboard.wrapper || frm.is_new()) return;

        frappe.call({
            method: "vobiz_click_to_call.api.disposition.get_reference_call_summary",
            args: {
                reference_doctype: frm.doctype,
                reference_name: frm.doc.name,
            },
        }).then((r) => {
            const summary = r.message || {};
            const rows = summary.rows || [];
            const $dashboard = $(frm.dashboard.wrapper);
            $dashboard.find(".vobiz-call-history-section").remove();

            const rowHtml = rows.length
                ? rows.map((row) => `
                    <div class="vobiz-call-history-row">
                        <a href="/app/vobiz-call-log/${encodeURIComponent(row.name)}">${escapeHtml(row.name)}</a>
                        <span>${escapeHtml(row.status || "")}</span>
                        <span>${escapeHtml(row.disposition || row.ai_disposition || "")}</span>
                        <span>${row.duration || 0}s</span>
                    </div>
                `).join("")
                : `<div class="text-muted">${__("No Vobiz calls yet")}</div>`;

            $dashboard.append(`
                <div class="vobiz-call-history-section">
                    <div class="vobiz-call-history-title">
                        <span>${__("Vobiz Calls")}</span>
                        <button class="btn btn-xs btn-default vobiz-open-call-list" type="button">${__("Open Logs")}</button>
                    </div>
                    <div class="vobiz-call-history-stats">
                        <span class="vobiz-call-history-stat">${__("Attempts")}: ${summary.total || 0}</span>
                        <span class="vobiz-call-history-stat">${__("Connected")}: ${summary.connected || 0}</span>
                        <span class="vobiz-call-history-stat">${__("Missed")}: ${summary.missed || 0}</span>
                    </div>
                    ${rowHtml}
                </div>
            `);

            $dashboard.find(".vobiz-open-call-list").on("click", () => {
                frappe.route_options = {
                    reference_doctype: frm.doctype,
                    reference_name: frm.doc.name,
                };
                frappe.set_route("List", "Vobiz Call Log");
            });
        });
    }

    function showLivePanel(callLog, frm) {
        if (!callLog) return;
        ensureStyles();
        if (currentPoller) {
            window.clearInterval(currentPoller);
            currentPoller = null;
        }

        $(".vobiz-live-panel").remove();
        const startedAt = Date.now();
        const $panel = $(`
            <div class="vobiz-live-panel" data-call-log="${escapeHtml(callLog)}">
                <div class="vobiz-live-panel-title">
                    <span><i class="fa fa-phone"></i> ${__("Vobiz Call")}</span>
                    <span class="vobiz-live-timer">00:00</span>
                </div>
                <div class="vobiz-live-status">${__("Calling customer...")}</div>
                <div class="vobiz-live-meta"></div>
                <div class="vobiz-live-actions">
                    <button class="btn btn-xs btn-default vobiz-open-log" type="button">${__("Open Log")}</button>
                    <button class="btn btn-xs btn-danger vobiz-cancel-call" type="button">${__("Cancel")}</button>
                </div>
            </div>
        `);
        $("body").append($panel);

        $panel.find(".vobiz-open-log").on("click", () => {
            frappe.set_route("Form", "Vobiz Call Log", callLog);
        });
        $panel.find(".vobiz-cancel-call").on("click", () => cancelCall(callLog));

        pollStatus(callLog, frm, $panel, startedAt);
        currentPoller = window.setInterval(() => pollStatus(callLog, frm, $panel, startedAt), 15000);
    }

    function pollStatus(callLog, frm, $panel, startedAt) {
        if (statusPollInFlight) return;
        statusPollInFlight = true;
        updateTimer($panel, startedAt);
        const request = frappe.call({
            method: "vobiz_click_to_call.api.call.get_call_status",
            args: { call_log: callLog, sync_provider: 0 },
        }).then((r) => {
            const data = r.message || {};
            updateTimer($panel, startedAt);
            $panel.find(".vobiz-live-status").text(statusText(data.status));
            $panel.find(".vobiz-live-meta").text([
                data.customer_number_display ? __("Customer: {0}", [data.customer_number_display]) : "",
                data.agent_mobile_display ? __("Agent: {0}", [data.agent_mobile_display]) : "",
                data.recording_status ? __("Recording: {0}", [data.recording_status]) : "",
                data.ai_disposition ? __("AI: {0}", [data.ai_disposition]) : "",
            ].filter(Boolean).join(" | "));
            $panel.find(".vobiz-cancel-call").toggle(Boolean(data.can_cancel));

            if (TERMINAL_STATUSES.includes(data.status)) {
                if (currentPoller) {
                    window.clearInterval(currentPoller);
                    currentPoller = null;
                }
                $(document).trigger("vobiz_refresh_availability");
                if (frm) renderCallHistory(frm);
                notifyCompletion(data.status);
                if (!data.disposition && !$panel.data("disposition-opened")) {
                    $panel.data("disposition-opened", true);
                    openDispositionDialog(callLog, data.status, frm);
                }
            }
        });
        request.always(() => {
            statusPollInFlight = false;
        });
    }

    function cancelCall(callLog) {
        frappe.confirm(__("Cancel this Vobiz call?"), () => {
            frappe.call({
                method: "vobiz_click_to_call.api.call.cancel_call",
                args: { call_log: callLog },
                freeze: true,
                freeze_message: __("Cancelling call..."),
            }).then((r) => {
                frappe.show_alert({
                    message: (r.message && r.message.message) || __("Call cancellation requested."),
                    indicator: "orange",
                });
            });
        });
    }

    function openDispositionDialog(callLog, status, frm) {
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
                    default: defaultDisposition(status),
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
                        call_log: callLog,
                        disposition: values.disposition,
                        notes: values.notes,
                        follow_up_datetime: values.follow_up_datetime,
                        mark_dnd: values.mark_dnd ? 1 : 0,
                    },
                    freeze: true,
                    freeze_message: __("Saving disposition..."),
                }).then(() => {
                    dialog.hide();
                    frappe.show_alert({ message: __("Call disposition saved"), indicator: "green" });
                    if (frm) {
                        frm.reload_doc();
                        renderCallHistory(frm);
                    }
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

    function statusText(status) {
        const labels = {
            Queued: __("Calling customer..."),
            Ringing: __("Calling customer..."),
            "Customer Answered": __("Customer answered. Calling agent..."),
            "Agent Answered": __("Agent answered. Calling customer..."),
            "Agent Ringing": __("Calling agent..."),
            Connected: __("Connected"),
            Completed: __("Call completed"),
            Failed: __("Call failed"),
            Busy: __("Busy"),
            "No Answer": __("No answer"),
            Cancelled: __("Cancelled"),
        };
        return labels[status] || status || __("Calling...");
    }

    function defaultDisposition(status) {
        if (status === "Completed" || status === "Connected") return "Connected";
        if (status === "No Answer") return "No Answer";
        if (status === "Busy") return "Busy";
        if (status === "Failed") return "Failed";
        return "";
    }

    function updateTimer($panel, startedAt) {
        const seconds = Math.floor((Date.now() - startedAt) / 1000);
        const minutes = Math.floor(seconds / 60);
        const remaining = seconds % 60;
        $panel.find(".vobiz-live-timer").text(`${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`);
    }

    function notifyCompletion(status) {
        if (!("Notification" in window) || Notification.permission !== "granted") return;
        try {
            new Notification(__("Vobiz call finished"), { body: status || __("Completed") });
        } catch (e) {
            // Browser notifications are best-effort only.
        }
    }

    function escapeHtml(value) {
        return frappe.utils.escape_html(String(value || ""));
    }

    function registerDoctype(doctype) {
        if (!doctype || registeredDoctypes.has(doctype)) return;
        registeredDoctypes.add(doctype);
        const events = {
            refresh: setupForm,
            onload_post_render: setupForm,
            after_save: setupForm,
        };

        FIELD_CANDIDATES.forEach((fieldname) => {
            events[fieldname] = renderButtons;
        });

        frappe.ui.form.on(doctype, events);
    }

    $(document).on("vobiz_availability_changed", function () {
        if (window.cur_frm && DOCTYPES.includes(cur_frm.doctype)) {
            setupForm(cur_frm);
        }
    });

    function loadAllowedDoctypes() {
        if (allowedDoctypesLoaded || !shouldLoadAllowedDoctypes()) return;
        if (!window.frappe || !frappe.session || frappe.session.user === "Guest") {
            DOCTYPES.forEach(registerDoctype);
            allowedDoctypesLoaded = true;
            return;
        }

        frappe.call({
            method: "vobiz_click_to_call.api.call.get_allowed_doctypes_api",
        }).then((r) => {
            DOCTYPES = Array.isArray(r.message) && r.message.length ? r.message : DEFAULT_DOCTYPES.slice();
            DOCTYPES.forEach(registerDoctype);
            allowedDoctypesLoaded = true;
            if (window.cur_frm && DOCTYPES.includes(cur_frm.doctype)) {
                setupForm(cur_frm);
            }
        });
    }

    $(document).on("page-change route-change", loadAllowedDoctypes);

    if (frappe.ready) {
        frappe.ready(loadAllowedDoctypes);
    } else {
        $(loadAllowedDoctypes);
    }
})();
