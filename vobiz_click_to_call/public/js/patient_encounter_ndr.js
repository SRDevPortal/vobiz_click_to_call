
frappe.ui.form.on('Patient Encounter', {
    refresh(frm) {
        if (!frm.fields_dict.ndr_view_history) return;
        frm.toggle_display('ndr_view_history', false);
        if (frm.is_new()) return;
        const name = frm.doc.name;
        const escape = frappe.utils.escape_html;
        const link = (doctype, name) => '/app/' + frappe.router.slug(doctype) + '/' + encodeURIComponent(name);
        const value_area = field => frm.fields_dict[field]?.$wrapper.find('.control-value');
        if (frm.doc.pe_shipkia_awb_number) {
            const awb = String(frm.doc.pe_shipkia_awb_number);
            value_area('pe_shipkia_awb_number')?.html('<a href="https://shipkia.com/tracking_result.html?tracking_id=' +
                escape(encodeURIComponent(awb.trim())) + '" target="_blank" rel="noopener noreferrer" title="' +
                escape(__('Track shipment in new tab')) + '">' + escape(awb) +
                ' <i class="fa fa-external-link" aria-hidden="true"></i></a>');
        }
        if (frm.doc.ndr_tag) {
            const color = frm.doc.ndr_tag === 'Callback' ? 'orange' : 'blue';
            value_area('ndr_tag')?.html('<a class="indicator-pill ' + color + '" href="' +
                escape(link('NDR Tag', frm.doc.ndr_tag)) + '">' + escape(frm.doc.ndr_tag) + '</a>');
        }
        const actions = frm.fields_dict.ndr_actions_html?.$wrapper;
        if (actions) actions.empty();
        frappe.call({method: 'vobiz_click_to_call.api.ndr_outcomes.form_summary', args: {encounter: name}}).then(r => {
            if (frm.doc.name !== name) return;
            const data = r.message || {};
            if (frm.doc.ndr_updated_by && data.handled_by) {
                value_area('ndr_updated_by')?.html('<a href="' + escape(link('User', frm.doc.ndr_updated_by)) + '">' +
                    escape(data.handled_by) + '</a><div class="text-muted small">' + escape(frm.doc.ndr_updated_by) + '</div>');
            }
            if (!actions) return;
            const call = frm.doc.ndr_latest_call;
            actions.html('<div style="display:flex;flex-wrap:wrap;gap:8px">' +
                (call ? '<a class="btn btn-default btn-sm" target="_blank" rel="noopener noreferrer" href="' +
                    escape(link('Vobiz Call Log', call)) + '">' + __('Open Latest Call') +
                    ' <i class="fa fa-external-link" aria-hidden="true"></i></a>' : '') +
                (data.history_access ? '<button type="button" class="btn btn-default btn-sm" data-ndr-history>' +
                    __('View NDR History') + '</button>' : '') + '</div>');
            actions.find('[data-ndr-history]').on('click', () => frm.trigger('ndr_view_history'));
        });
        // Other apps may reset tab visibility metadata during migration.
        if (frm.doc.ndr_tag || frm.doc.ndr_latest_call || frm.doc.ndr_note) {
            frm.set_df_property('pe_shipment_tracking_tab', 'depends_on',
                'eval:doc.pe_shipkia_order_id || doc.pe_shipkia_shipment || doc.pe_latest_support_ticket || doc.ndr_tag || doc.ndr_latest_call || doc.ndr_note');
        }
    },
    ndr_view_history(frm) {
        const name = frm.doc.name;
        frappe.call({method: 'vobiz_click_to_call.api.ndr_outcomes.history', args: {encounter: name}}).then(r => {
            if (frm.doc.name !== name) return;
            const escape = frappe.utils.escape_html;
            const rows = r.message || [];
            const content = rows.map(row => '<div><strong>' + escape(row.tag) + '</strong><br>' +
                escape(row.agent) + ' · ' + escape(String(row.creation)) +
                '<p>' + escape(row.notes || '') + '</p><p>' + __('Call Ended At') + ': ' +
                escape(String(row.follow_up_at || '—')) + '</p>' + (row.callback_at ? '<p>' + __('Callback Scheduled At') + ': ' + escape(String(row.callback_at)) + '</p>' : '') + '</div>').join('<hr>');
            frappe.msgprint({title: __('NDR Interaction History'), message: content || __('No previous outcomes')});
        });
    }
});
