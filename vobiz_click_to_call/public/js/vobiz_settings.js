frappe.ui.form.on('Vobiz Settings', {
	refresh(frm) {
        setup_ndr_pickers(frm);
        frm.add_custom_button(__('Configure Encounter Queue'), () => configure_encounter_queue(frm));
		frm.add_custom_button(__('Sync AI Dispositions'), () => {
			frappe.call({
				method: 'vobiz_click_to_call.vobiz_click_to_call.doctype.vobiz_settings.vobiz_settings.sync_ai_disposition_options',
				freeze: true,
				freeze_message: __('Syncing SR Lead Disposition records...')
			}).then((r) => {
				const data = r.message || {};
				frm.set_value('ai_disposition_options', data.options || '');
				frm.refresh_field('ai_disposition_options');
				frappe.show_alert({
					message: __('Synced {0} AI disposition options', [data.count || 0]),
					indicator: 'green'
				});
			});
		});
	}
});

function setup_ndr_pickers(frm) {
    for (const [setting, field, label] of [
        ['ndr_shipkia_stages', 'pe_shipkia_stage', 'Shipkia Stages'],
        ['ndr_shipkia_statuses', 'pe_shipkia_status', 'Shipkia Statuses']
    ]) {
        const wrapper = frm.fields_dict[setting + '_picker'];
        if (!wrapper) continue;
        let selected;
        try {
            selected = JSON.parse(frm.doc[setting] || '[]');
            if (!Array.isArray(selected) || selected.some(value => typeof value !== 'string')) throw new Error();
        } catch (error) {
            wrapper.$wrapper.text(__('Invalid NDR settings. Correct the saved stage/status JSON before editing.'));
            continue;
        }
        wrapper.$wrapper.empty();
        let initializing = true;
        const option = value => ({value, label: frappe.utils.escape_html(value), description: ''});
        const control = frappe.ui.form.make_control({
            parent: wrapper.$wrapper,
            render_input: true,
            df: {
                fieldname: setting + '_selection', fieldtype: 'MultiSelectList', label: __(label),
                placeholder: __('Select values'),
                description: __('Match any selected value. Both stage and status must match, along with Encounter Queue filters.'),
                read_only: !frappe.user.has_role('System Manager') || !frm.perm.some(permission => permission.write),
                get_data: async search => {
                    const response = await frappe.call({
                        method: 'vobiz_click_to_call.api.encounter_queue.get_ndr_options',
                        args: {field, search}
                    });
                    return [...new Set([...selected, ...(response.message || [])])]
                        .map(option);
                },
                onchange: () => {
                    if (initializing) return;
                    selected = control.get_value();
                    return frm.set_value(setting, JSON.stringify(selected));
                }
            }
        });
        // MultiSelectList renders labels as HTML, including its initial status text.
        control._options = selected.map(option);
        Promise.resolve(control.set_value(selected)).finally(() => { initializing = false; });
    }
}

async function configure_encounter_queue(frm) {
    const response = await frappe.call({
        method: 'vobiz_click_to_call.api.encounter_queue.get_configuration_fields'
    });
    let columns, filters;
    try {
        columns = JSON.parse(frm.doc.encounter_queue_fields || '["name", "patient", "patient_name", "encounter_date", "status"]');
        filters = JSON.parse(frm.doc.encounter_queue_filters || '[]');
        if (!Array.isArray(columns) || !Array.isArray(filters)) throw new Error();
    } catch (error) {
        frappe.msgprint(__('Correct the Encounter Queue JSON fields before opening the editor.'));
        return;
    }
    await frappe.model.with_doctype('Patient Encounter');
    const catalog = response.message || [];
    let group;
    const dialog = new frappe.ui.Dialog({
        title: __('Configure Encounter Queue'),
        size: 'extra-large',
        fields: [
            {fieldname: 'columns', fieldtype: 'MultiCheck', label: __('Visible columns (maximum 20)'),
                columns: 3,
                options: catalog.map(field => ({
                    label: field.label + ' (' + field.fieldname + ')',
                    value: field.fieldname, checked: columns.includes(field.fieldname)
                }))},
            {fieldname: 'filters_heading', fieldtype: 'Section Break', label: __('Show encounters matching all filters')},
            {fieldname: 'filters', fieldtype: 'HTML'}
        ],
        primary_action_label: __('Apply to Settings'),
        primary_action: async values => {
            const selected = values.columns || [];
            if (!selected.length || selected.length > 20) {
                frappe.msgprint(__('Choose between 1 and 20 columns.'));
                return;
            }
            // Retain saved ordering and append newly selected fields.
            const ordered = columns.filter(field => selected.includes(field));
            selected.forEach(field => { if (!ordered.includes(field)) ordered.push(field); });
            await frm.set_value('encounter_queue_fields', JSON.stringify(ordered, null, 2));
            await frm.set_value('encounter_queue_filters', JSON.stringify(group.get_filters().map(filter => filter.slice(0, 4)), null, 2));
            dialog.hide();
            frappe.show_alert({message: __('Save Vobiz Settings to apply these queue rules.'), indicator: 'blue'});
        }
    });
    group = new frappe.ui.FilterGroup({
        doctype: 'Patient Encounter',
        parent: dialog.fields_dict.filters.$wrapper,
        on_change: () => {}
    });
    // Inline FilterGroup has no popover button to update.
    group.update_filter_button = () => {};
    await group.add_filters(filters.map(filter => filter.length === 3 ? ['Patient Encounter', ...filter] : filter));
    dialog.show();
}
