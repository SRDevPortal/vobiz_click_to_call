frappe.ui.form.on('Vobiz User Mapping', {
	refresh(frm) {
		load_vobiz_number_options(frm);
		setup_patient_routing_multi_ui(frm);
		setup_fallback_user_multi_ui(frm);
	},
	team(frm) {
		load_team_leader(frm);
	},
	queue_source(frm) {
		if (!queue_source_includes_patient(frm.doc.queue_source)) {
			frm.set_value('sr_medical_department', '');
			frm.set_value('sr_medical_departments', '');
			frm.set_value('sr_followup_id', '');
			frm.set_value('sr_followup_ids', '');
		}
		setup_patient_routing_multi_ui(frm);
	},
	sr_medical_department(frm) {
		setup_patient_routing_multi_ui(frm);
	},
	sr_followup_id(frm) {
		setup_patient_routing_multi_ui(frm);
	},
	fallback_user(frm) {
		setup_fallback_user_multi_ui(frm);
	},
});

frappe.provide('vobiz_click_to_call');
if (!$('#vobiz-user-mapping-style').length) {
	$('head').append(`
		<style id="vobiz-user-mapping-style">
			.vobiz-multi-values { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
			.vobiz-route-chip { align-items: center; background: #eef6ff; border: 1px solid #d8e9ff; border-radius: 6px; color: #1f4f82; display: inline-flex; gap: 4px; padding: 3px 7px; }
			.vobiz-route-chip .btn { color: #60758c; line-height: 1; margin: 0; padding: 0 0 0 2px; }
		</style>
	`);
}

function load_vobiz_number_options(frm) {
	frappe.call({
		method: 'vobiz_click_to_call.vobiz_click_to_call.doctype.vobiz_settings.vobiz_settings.get_caller_id_options',
	}).then((r) => {
		const options = r.message || [];
		frm.set_df_property('caller_id', 'options', [''].concat(options).join('\n'));
		frm.refresh_field('caller_id');
	});
}

function load_team_leader(frm) {
	if (!frm.doc.team) {
		return;
	}
	frappe.call({
		method: 'vobiz_click_to_call.vobiz_click_to_call.doctype.vobiz_user_mapping.vobiz_user_mapping.get_team_leader',
		args: {
			team: frm.doc.team
		}
	}).then((r) => {
		if (r.message) {
			frm.set_value('team_leader', r.message);
		}
	});
}

function setup_patient_routing_multi_ui(frm) {
	const show = queue_source_includes_patient(frm.doc.queue_source);
	setup_multi_value_field(frm, {
		fieldname: 'sr_medical_department',
		store_fieldname: 'sr_medical_departments',
		button_title: __('Add Department'),
		empty_text: __('No extra departments added')
	}, show);
	setup_multi_value_field(frm, {
		fieldname: 'sr_followup_id',
		store_fieldname: 'sr_followup_ids',
		button_title: __('Add Follow up ID'),
		empty_text: __('No extra follow up IDs added')
	}, show);
}

function queue_source_includes_patient(queue_source) {
	return ['Patient', 'CRM Lead and Patient'].includes(queue_source || '');
}

function setup_fallback_user_multi_ui(frm) {
	setup_multi_value_field(frm, {
		fieldname: 'fallback_user',
		store_fieldname: 'fallback_users',
		button_title: __('Add Fallback User'),
		empty_text: __('No fallback users added')
	}, true);
}

function setup_multi_value_field(frm, opts, show) {
	const field = frm.fields_dict[opts.fieldname];
	if (!field || !field.$wrapper) return;
	if (frm.fields_dict[opts.store_fieldname]) {
		frm.set_df_property(opts.store_fieldname, 'hidden', 1);
	}

	const $wrapper = field.$wrapper;
	const $inputArea = $wrapper.find('.control-input').first();
	if (!$inputArea.length) return;

	if (!show) {
		$wrapper.find('.vobiz-multi-add-btn, .vobiz-multi-values').remove();
		return;
	}

	if (!$inputArea.find('.vobiz-multi-add-btn').length) {
		$inputArea.css({ display: 'flex', gap: '6px', alignItems: 'center' });
		$inputArea.children(':not(.vobiz-multi-add-btn)').first().css({ flex: '1 1 auto' });
		$inputArea.append(`
			<button class="btn btn-default btn-sm vobiz-multi-add-btn" type="button" title="${frappe.utils.escape_html(opts.button_title)}">
				<i class="fa fa-plus"></i>
			</button>
		`);
		$inputArea.find('.vobiz-multi-add-btn').on('click', () => {
			const value = (frm.doc[opts.fieldname] || '').toString().trim();
			if (!value) {
				frappe.show_alert({ message: opts.button_title, indicator: 'orange' });
				return;
			}
			add_multi_value(frm, opts.store_fieldname, value);
			render_multi_values(frm, opts);
		});
	}

	if (!$wrapper.find('.vobiz-multi-values').length) {
		$wrapper.append('<div class="vobiz-multi-values"></div>');
	}
	render_multi_values(frm, opts);
}

function render_multi_values(frm, opts) {
	const field = frm.fields_dict[opts.fieldname];
	if (!field || !field.$wrapper) return;
	const values = get_multi_values(frm.doc[opts.store_fieldname]);
	const $list = field.$wrapper.find('.vobiz-multi-values');
	if (!$list.length) return;
	$list.html(values.length ? values.map(value => `
		<span class="vobiz-route-chip" data-value="${frappe.utils.escape_html(value)}">
			${frappe.utils.escape_html(value)}
			<button class="btn btn-xs btn-link" type="button" data-remove-value="${frappe.utils.escape_html(value)}">
				<i class="fa fa-times"></i>
			</button>
		</span>
	`).join('') : `<div class="text-muted small">${frappe.utils.escape_html(opts.empty_text)}</div>`);
	$list.find('[data-remove-value]').on('click', (event) => {
		const value = $(event.currentTarget).data('remove-value');
		remove_multi_value(frm, opts.store_fieldname, value);
		render_multi_values(frm, opts);
	});
}

function get_multi_values(value) {
	const seen = new Set();
	const values = [];
	String(value || '').replace(/,/g, '\n').split('\n').forEach(row => {
		row = row.trim();
		if (row && !seen.has(row)) {
			values.push(row);
			seen.add(row);
		}
	});
	return values;
}

function add_multi_value(frm, fieldname, value) {
	const values = get_multi_values(frm.doc[fieldname]);
	if (!values.includes(value)) {
		values.push(value);
		frm.set_value(fieldname, values.join('\n'));
	}
}

function remove_multi_value(frm, fieldname, value) {
	const values = get_multi_values(frm.doc[fieldname]).filter(row => row !== value);
	frm.set_value(fieldname, values.join('\n'));
}
