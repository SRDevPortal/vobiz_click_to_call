frappe.ui.form.on('Vobiz Settings', {
	refresh(frm) {
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
