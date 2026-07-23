frappe.listview_settings['Vobiz User Mapping'] = {
	onload() {
		frappe.call({
			method: 'vobiz_click_to_call.vobiz_click_to_call.doctype.vobiz_settings.vobiz_settings.get_caller_id_options',
		}).then((r) => {
			const callerIdField = frappe.meta.get_docfield('Vobiz User Mapping', 'caller_id');
			if (!callerIdField) return;

			callerIdField.options = [''].concat(r.message || []).join('\n');
		});
	},
};
