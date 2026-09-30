frappe.ui.form.on('Vobiz Settings', {
	refresh(frm) {
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
