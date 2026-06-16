frappe.ui.form.on('Vobiz User Mapping', {
	refresh(frm) {
		load_vobiz_number_options(frm);
	},
	queue_source(frm) {
		if (frm.doc.queue_source !== 'Patient') {
			frm.set_value('sr_medical_department', '');
			frm.set_value('sr_followup_id', '');
			frm.set_value('fallback_user', '');
		}
	},
});

function load_vobiz_number_options(frm) {
	frappe.call({
		method: 'vobiz_click_to_call.vobiz_click_to_call.doctype.vobiz_settings.vobiz_settings.get_caller_id_options',
	}).then((r) => {
		const options = r.message || [];
		frm.set_df_property('caller_id', 'options', [''].concat(options).join('\n'));
		frm.refresh_field('caller_id');
	});
}
