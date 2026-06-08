frappe.pages['vobiz-agent-console'].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Vobiz Agent Console'),
		single_column: true
	});
	wrapper.vobiz_agent_console = new VobizAgentConsole(page);
};

frappe.pages['vobiz-agent-console'].on_page_show = function(wrapper) {
	if (wrapper.vobiz_agent_console) {
		wrapper.vobiz_agent_console.on_page_show();
	}
};

class VobizAgentConsole {
	constructor(page) {
		this.page = page;
		this.state = {
			queue: [],
			selected: null,
			active_call: null,
			call_started_at: null,
			dispositions: [],
			lead_disposition_context: {},
			restore_checked: false,
			restore_in_flight: false,
			active_workdesk_key: null,
			active_workdesk_body: null,
			active_workdesk_row: null,
			active_workdesk_dialog: null,
			detail_loading_key: null,
			workdesk_live_call: null,
			workdesk_live_call_log: null,
			workdesk_live_polling: false,
			disposition_prompted_call_log: null,
			navigating_from_workdesk: false,
			auto_dial: {
				running: false,
				in_flight: false,
				queue: [],
				cursor: 0,
				results: [],
				events: [],
				current: null,
				started_at: null,
				stopped_at: null
			}
		};
		this.timer = null;
		this.poller = null;
		this.render();
		this.bind();
		this.load();
		this.start_polling();
	}

	render() {
		this.page.main.html(`
			<div class="vobiz-console">
				<div class="vobiz-console-head">
					<div>
						<div class="vobiz-eyebrow">${__('APP / VOBIZ CALL CENTER / DIALER')}</div>
						<h2>${__('Vobiz Agent Call Center')}</h2>
					</div>
					<div class="vobiz-agent-state">
						<span class="vobiz-state-dot"></span>
						<span data-role="availability">${__('Checking')}</span>
					</div>
				</div>

				<div class="vobiz-stats" data-role="stats"></div>

				<section class="vobiz-band vobiz-dialer-control">
					<div>
						<strong>${__('Auto-Dial Controls')}</strong>
						<div class="text-muted" data-role="selected-count">${__('0 leads selected')}</div>
					</div>
					<div class="vobiz-actions">
						<button class="btn btn-default btn-sm" data-action="refresh">
							<i class="fa fa-refresh"></i> ${__('Refresh')}
						</button>
						<button class="btn btn-primary btn-sm" data-action="toggle-auto" data-role="auto-toggle">
							<i class="fa fa-play"></i> ${__('Start Auto Dial')}
						</button>
						<button class="btn btn-default btn-sm" data-action="auto-report">
							<i class="fa fa-list"></i> ${__('Auto Dial Report')}
						</button>
					</div>
				</section>

				<div class="vobiz-layout">
					<section class="vobiz-band vobiz-queue">
						<div class="vobiz-section-title">
							<h3>${__('Lead Queue')}</h3>
							<input class="form-control input-sm" data-role="search" placeholder="${__('Search')}">
						</div>
						<div class="vobiz-table-wrap">
							<table class="table table-sm vobiz-table">
								<thead>
									<tr>
										<th style="width: 34px"><input type="checkbox" data-role="check-all"></th>
										<th style="width: 170px">${__('CRM Lead ID')}</th>
										<th>${__('Name')}</th>
										<th>${__('Phone')}</th>
										<th>${__('Status')}</th>
										<th>${__('Next Action')}</th>
										<th style="width: 88px">${__('Action')}</th>
									</tr>
								</thead>
								<tbody data-role="queue"></tbody>
							</table>
						</div>
					</section>

					<aside class="vobiz-side">
						<section class="vobiz-band vobiz-active">
							<div class="vobiz-section-title">
								<h3>${__('Real-Time Agent Console')}</h3>
								<span class="vobiz-pill" data-role="call-status">${__('Idle')}</span>
							</div>
							<div class="vobiz-call-focus">
								<div class="vobiz-call-title" data-role="focus-name">${__('No active call')}</div>
								<div class="text-muted" data-role="focus-meta">${__('Select a lead to view context')}</div>
								<div class="vobiz-call-timer" data-role="timer">00:00</div>
								<div class="vobiz-call-controls">
									<button class="btn btn-default btn-sm" data-action="open-reference"><i class="fa fa-external-link"></i></button>
									<button class="btn btn-danger btn-sm" data-action="cancel-call"><i class="fa fa-phone"></i> ${__('End')}</button>
								</div>
								<div class="vobiz-call-assets" data-role="call-assets"></div>
							</div>
						</section>

						<section class="vobiz-band">
							<div class="vobiz-section-title">
								<h3>${__('Auto Dial Live')}</h3>
								<span class="vobiz-pill" data-role="auto-live-state">${__('Stopped')}</span>
							</div>
							<div class="vobiz-auto-live" data-role="auto-live"></div>
						</section>

						<section class="vobiz-band">
							<div class="vobiz-section-title">
								<h3>${__('Call Disposition')}</h3>
							</div>
							<select class="form-control" data-role="lead-status"></select>
							<select class="form-control" data-role="disposition"></select>
							<textarea class="form-control" rows="3" data-role="notes" placeholder="${__('Call notes')}"></textarea>
							<button class="btn btn-primary btn-sm" data-action="save-disposition">${__('Save')}</button>
						</section>
					</aside>
				</div>
			</div>
		`);
		this.inject_styles();
	}

	inject_styles() {
		if ($('#vobiz-agent-console-style').length) return;
		$('head').append(`
			<style id="vobiz-agent-console-style">
				.vobiz-console { background: #f7f7fb; margin: -15px; min-height: calc(100vh - 60px); padding: 24px; }
				.vobiz-console-head { align-items: center; display: flex; justify-content: space-between; margin-bottom: 20px; }
				.vobiz-console-head h2 { font-size: 22px; font-weight: 700; margin: 0; }
				.vobiz-eyebrow { color: #6b7280; font-size: 11px; font-weight: 700; letter-spacing: .04em; margin-bottom: 4px; }
				.vobiz-agent-state { align-items: center; background: #fff; border: 1px solid #e5e7eb; border-radius: 6px; display: flex; gap: 8px; padding: 8px 12px; }
				.vobiz-state-dot { background: #16a34a; border-radius: 50%; height: 9px; width: 9px; }
				.vobiz-stats { display: grid; gap: 16px; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 16px; }
				.vobiz-stat { background: #fff; border: 1px solid #ebeef2; border-radius: 8px; padding: 18px; }
				.vobiz-stat.clickable { cursor: pointer; transition: border-color .15s ease, transform .15s ease; }
				.vobiz-stat.clickable:hover { border-color: #b8d8ff; transform: translateY(-1px); }
				.vobiz-stat strong { display: block; font-size: 28px; line-height: 1.1; margin-top: 10px; }
				.vobiz-stat span { color: #4b5563; font-size: 12px; font-weight: 700; }
				.vobiz-performance-head { align-items: center; display: flex; justify-content: space-between; margin-bottom: 14px; }
				.vobiz-performance-head h4 { font-size: 18px; font-weight: 800; margin: 2px 0 0; }
				.vobiz-perf-kpis { display: grid; gap: 12px; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 18px; }
				.vobiz-perf-kpi { background: #f9fafb; border: 1px solid #eef0f3; border-radius: 8px; padding: 12px; }
				.vobiz-perf-kpi span { color: #6b7280; display: block; font-size: 11px; font-weight: 800; margin-bottom: 6px; text-transform: uppercase; }
				.vobiz-perf-kpi strong { font-size: 24px; line-height: 1; }
				.vobiz-performance-section { border-top: 1px solid #eef0f3; margin-top: 14px; padding-top: 14px; }
				.vobiz-performance-section h4 { font-size: 15px; font-weight: 800; margin: 0 0 10px; }
				.vobiz-performance-table { table-layout: auto; }
				.vobiz-performance-table th { color: #6b7280; font-size: 11px; font-weight: 800; }
				.vobiz-performance-table td { vertical-align: middle; white-space: normal; word-break: break-word; }
				.vobiz-performance-table code { white-space: nowrap; }
				.vobiz-icon { align-items: center; border: 1px solid currentColor; border-radius: 6px; display: inline-flex; height: 30px; justify-content: center; width: 30px; }
				.vobiz-band { background: #fff; border: 1px solid #ebeef2; border-radius: 8px; margin-bottom: 16px; padding: 16px; }
				.vobiz-dialer-control { align-items: center; display: flex; justify-content: space-between; }
				.vobiz-actions { display: flex; gap: 10px; }
				.vobiz-layout { display: grid; gap: 16px; grid-template-columns: minmax(0, 1fr) 320px; }
				.vobiz-section-title { align-items: center; display: flex; gap: 12px; justify-content: space-between; margin-bottom: 12px; }
				.vobiz-section-title h3 { font-size: 15px; font-weight: 700; margin: 0; }
				.vobiz-table-wrap { overflow-x: auto; }
				.vobiz-table { margin: 0; table-layout: fixed; }
				.vobiz-table th { color: #6b7280; font-size: 11px; font-weight: 700; }
				.vobiz-table td { overflow: hidden; text-overflow: ellipsis; vertical-align: middle; white-space: nowrap; }
				.vobiz-person { align-items: center; display: flex; gap: 9px; min-width: 0; }
				.vobiz-avatar { align-items: center; background: #eaf3ff; border-radius: 50%; color: #2563eb; display: inline-flex; flex: 0 0 auto; font-weight: 700; height: 28px; justify-content: center; width: 28px; }
				.vobiz-status { font-size: 12px; font-weight: 700; }
				.vobiz-status.New { color: #0284c7; } .vobiz-status.Qualified, .vobiz-status.Converted { color: #16a34a; }
				.vobiz-status.Not { color: #dc2626; } .vobiz-status.Contacted { color: #ca8a04; }
				.vobiz-side { min-width: 0; }
				.vobiz-pill { background: #eff6ff; border-radius: 999px; color: #2563eb; font-size: 12px; padding: 3px 8px; }
				.vobiz-call-focus { border-top: 1px solid #eef0f3; padding-top: 12px; }
				.vobiz-call-title { font-size: 16px; font-weight: 700; }
				.vobiz-call-timer { font-size: 36px; font-weight: 800; margin: 12px 0; }
				.vobiz-call-controls { display: flex; gap: 8px; }
				.vobiz-call-assets { border-top: 1px solid #eef0f3; font-size: 12px; margin-top: 12px; padding-top: 10px; }
				.vobiz-call-assets a { font-weight: 700; }
				.vobiz-auto-live { border-top: 1px solid #eef0f3; display: grid; gap: 8px; max-height: 220px; overflow: auto; padding-top: 10px; }
				.vobiz-auto-event { border-left: 3px solid #d1d5db; padding-left: 8px; }
				.vobiz-auto-event.active { border-color: #0ea5e9; }
				.vobiz-auto-event.done { border-color: #16a34a; }
				.vobiz-auto-event.failed { border-color: #dc2626; }
				.vobiz-auto-event strong { display: block; font-size: 12px; }
				.vobiz-auto-event span { color: #6b7280; display: block; font-size: 11px; overflow-wrap: anywhere; }
				.vobiz-live-call { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; margin-bottom: 12px; padding: 12px; }
				.vobiz-live-call-head { align-items: center; display: flex; justify-content: space-between; margin-bottom: 10px; }
				.vobiz-live-call-head strong { font-size: 13px; }
				.vobiz-live-pill { background: #eef2ff; border-radius: 999px; color: #3730a3; font-size: 11px; font-weight: 800; padding: 3px 8px; }
				.vobiz-live-steps { display: grid; gap: 8px; }
				.vobiz-live-step { align-items: start; display: grid; gap: 9px; grid-template-columns: 22px minmax(0, 1fr); }
				.vobiz-live-dot { background: #d1d5db; border-radius: 50%; height: 10px; margin: 5px auto 0; width: 10px; }
				.vobiz-live-step.active .vobiz-live-dot { background: #0ea5e9; box-shadow: 0 0 0 4px rgba(14, 165, 233, .12); }
				.vobiz-live-step.done .vobiz-live-dot { background: #16a34a; }
				.vobiz-live-step.failed .vobiz-live-dot { background: #dc2626; }
				.vobiz-live-title { font-size: 12px; font-weight: 800; }
				.vobiz-live-meta { color: #6b7280; font-size: 12px; overflow-wrap: anywhere; }
				.vobiz-transcript { background: #f9fafb; border: 1px solid #eef0f3; border-radius: 6px; margin-top: 6px; max-height: 120px; overflow: auto; padding: 8px; white-space: pre-wrap; }
				.vobiz-side textarea, .vobiz-side select { margin-bottom: 10px; }
				.vobiz-tabs { border-bottom: 1px solid #e5e7eb; display: flex; gap: 20px; margin: -4px 0 16px; }
				.vobiz-tabs button { background: none; border: 0; border-bottom: 2px solid transparent; font-weight: 700; padding: 8px 0; }
				.vobiz-tabs button.active { border-color: #111827; }
				.vobiz-context-grid { display: grid; gap: 16px; grid-template-columns: minmax(0, 1fr) 360px; min-height: 210px; }
				.vobiz-guidance-list { margin: 0; padding-left: 18px; }
				.vobiz-history-row { border-bottom: 1px solid #eef0f3; display: grid; gap: 8px; grid-template-columns: 130px 100px 1fr; padding: 8px 0; }
				.vobiz-info-list { display: grid; gap: 14px; margin-top: 12px; }
				.vobiz-info-row { align-items: center; display: grid; gap: 12px; grid-template-columns: 38px minmax(0, 1fr); }
				.vobiz-info-icon { align-items: center; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 50%; display: flex; height: 38px; justify-content: center; width: 38px; }
				.vobiz-detail-head { align-items: center; display: flex; gap: 12px; justify-content: space-between; margin-bottom: 14px; }
				.vobiz-detail-head h3 { font-size: 16px; font-weight: 700; margin: 0; }
				.vobiz-audio-list { display: grid; gap: 12px; }
				.vobiz-audio-card { border: 1px solid #eef0f3; border-radius: 8px; padding: 12px; }
				.vobiz-workdesk { min-height: 520px; }
				.vobiz-workdesk-top { margin-bottom: 14px; }
				.vobiz-workdesk-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-start; margin-top: 10px; }
				.vobiz-workdesk-actions .btn { white-space: nowrap; }
				.vobiz-workdesk-title h3 { font-size: 18px; font-weight: 800; margin: 0 0 4px; }
				.vobiz-call-route { align-items: center; color: #4b5563; display: flex; flex-wrap: wrap; font-size: 12px; gap: 8px; margin-top: 8px; }
				.vobiz-call-route-chip { background: #f9fafb; border: 1px solid #eef0f3; border-radius: 6px; font-weight: 700; padding: 4px 8px; }
				.vobiz-call-route-icon { color: #059669; }
				.vobiz-workdesk-grid { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
				.vobiz-workdesk-card { border: 1px solid #eef0f3; border-radius: 8px; padding: 12px; }
				.vobiz-workdesk-card h4 { font-size: 13px; font-weight: 800; margin: 0 0 10px; }
				.vobiz-field-grid { display: grid; gap: 10px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
				.vobiz-field { background: #f9fafb; border-radius: 6px; min-height: 54px; padding: 8px; }
				.vobiz-field-label { color: #6b7280; font-size: 11px; font-weight: 700; margin-bottom: 4px; }
				.vobiz-field-value { font-weight: 700; overflow-wrap: anywhere; }
				.vobiz-related-row { align-items: center; border-bottom: 1px solid #eef0f3; display: grid; gap: 10px; grid-template-columns: minmax(0, 1fr) auto; padding: 8px 0; }
				.vobiz-related-row:last-child { border-bottom: 0; }
				.vobiz-related-title { font-weight: 700; overflow-wrap: anywhere; }
				.vobiz-related-meta { color: #6b7280; font-size: 12px; overflow-wrap: anywhere; }
				.vobiz-clinical-history { display: grid; gap: 12px; }
				.vobiz-clinical-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; }
				.vobiz-clinical-head { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; margin-bottom: 10px; }
				.vobiz-clinical-head span { color: #6b7280; font-size: 12px; }
				.vobiz-clinical-section { border-top: 1px solid #f0f2f5; padding: 9px 0; }
				.vobiz-clinical-label { color: #374151; font-size: 12px; font-weight: 800; margin-bottom: 4px; }
				.vobiz-clinical-text { color: #4b5563; font-size: 13px; white-space: pre-wrap; word-break: break-word; }
				.vobiz-clinical-med-table { margin-bottom: 0; table-layout: fixed; }
				.vobiz-clinical-med-table th { color: #6b7280; font-size: 11px; font-weight: 800; }
				.vobiz-clinical-med-table td { font-size: 12px; white-space: normal; word-break: break-word; }
				.vobiz-empty { color: #6b7280; padding: 12px 0; }
				.vobiz-wa-chat-list { background: #f9fafb; border: 1px solid #eef0f3; border-radius: 8px; display: grid; gap: 8px; margin-top: 12px; max-height: 380px; overflow-y: auto; padding: 10px; }
				.vobiz-wa-loader { color: #6b7280; font-size: 12px; padding: 6px 0; text-align: center; }
				.vobiz-wa-message { border: 1px solid #eef0f3; border-radius: 8px; max-width: 82%; padding: 8px 10px; }
				.vobiz-wa-message.inbound { background: #fff; justify-self: start; }
				.vobiz-wa-message.outbound { background: #ecfdf5; justify-self: end; }
				.vobiz-wa-message-meta { color: #6b7280; font-size: 11px; font-weight: 700; margin-bottom: 4px; }
				.vobiz-wa-message-body { font-size: 13px; white-space: pre-wrap; word-break: break-word; }
				.vobiz-wa-composer { align-items: center; background: #fff; border: 1px solid #e5e7eb; border-radius: 999px; box-shadow: 0 1px 6px rgba(15, 23, 42, .06); display: grid; gap: 8px; grid-template-columns: auto auto minmax(0, 1fr) auto; margin-top: 12px; padding: 8px 10px 8px 14px; }
				.vobiz-wa-icon-btn { align-items: center; background: transparent; border: 0; color: #111827; display: inline-flex; font-size: 18px; height: 32px; justify-content: center; padding: 0; width: 32px; }
				.vobiz-wa-attach-wrap, .vobiz-wa-emoji-wrap { position: relative; }
				.vobiz-wa-menu { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; bottom: 42px; box-shadow: 0 12px 30px rgba(15, 23, 42, .16); display: none; left: 0; min-width: 190px; padding: 8px; position: absolute; z-index: 10; }
				.vobiz-wa-menu.show { display: grid; gap: 4px; }
				.vobiz-wa-menu button { align-items: center; background: transparent; border: 0; border-radius: 8px; display: flex; gap: 10px; padding: 8px 10px; text-align: left; width: 100%; }
				.vobiz-wa-menu button:hover { background: #f3f4f6; }
				.vobiz-wa-emoji-menu { grid-template-columns: repeat(8, 30px); min-width: 276px; }
				.vobiz-wa-emoji-menu button { font-size: 18px; justify-content: center; padding: 4px; }
				.vobiz-wa-composer textarea { background: transparent; border: 0; box-shadow: none; height: 36px; line-height: 20px; max-height: 96px; min-height: 36px; padding: 8px 4px; resize: none; }
				.vobiz-wa-composer textarea:focus { border: 0; box-shadow: none; outline: 0; }
				.vobiz-wa-send { align-items: center; background: #16a34a; border: 0; border-radius: 50%; color: #fff; display: inline-flex; font-size: 18px; height: 42px; justify-content: center; padding: 0; width: 42px; }
				.vobiz-wa-send:disabled { opacity: .65; }
				.vobiz-dialpad { align-items: center; display: grid; gap: 14px; grid-template-columns: 110px minmax(0, 1fr); }
				.vobiz-pad-grid { display: grid; gap: 8px; grid-template-columns: repeat(3, 1fr); }
				.vobiz-pad-grid button { background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 6px; font-weight: 700; height: 38px; }
				.vobiz-wave { align-items: center; display: flex; gap: 4px; height: 120px; }
				.vobiz-wave span { background: #6b7280; border-radius: 999px; display: block; width: 5px; }
				@media (max-width: 1100px) {
					.vobiz-stats, .vobiz-layout, .vobiz-context-grid, .vobiz-workdesk-grid, .vobiz-field-grid { grid-template-columns: 1fr; }
					.vobiz-console { padding: 14px; }
					.vobiz-workdesk-top { display: block; }
					.vobiz-workdesk-actions { justify-content: flex-start; margin-top: 10px; }
				}
			</style>
		`);
	}

	bind() {
		const $main = this.page.main;
		$main.on('click', '[data-action="refresh"]', () => this.load());
		$main.on('click', '[data-action="toggle-auto"]', () => this.toggle_auto_dial());
		$main.on('click', '[data-action="auto-report"]', () => this.open_auto_dial_report());
		$main.on('click', '[data-action="performance"]', (e) => this.open_performance_dialog($(e.currentTarget).data('metric')));
		$main.on('click', '[data-action="call-row"]', (e) => {
			e.stopPropagation();
			this.call_row($(e.currentTarget).closest('tr').data('index'));
		});
		$main.on('click', '[data-action="select-row"]', (e) => this.select_row($(e.currentTarget).data('index')));
		$main.on('click', '[data-action="call-selected"]', () => this.call_selected());
		$main.on('click', '[data-action="open-reference"]', () => this.open_reference());
		$main.on('click', '[data-action="cancel-call"]', () => this.cancel_call());
		$main.on('click', '[data-action="save-disposition"]', () => this.save_disposition());
		$main.on('change', '[data-role="lead-status"]', () => this.refresh_lead_disposition_options());
		$main.on('click', '[data-tab]', (e) => this.show_tab($(e.currentTarget).data('tab')));
		$main.on('change', '[data-role="check-all"]', (e) => {
			$main.find('[data-role="row-check"]').prop('checked', e.currentTarget.checked);
			this.update_selected_count();
		});
		$main.on('change', '[data-role="row-check"]', () => this.update_selected_count());
		$main.on('click', '[data-role="row-check"]', (e) => e.stopPropagation());
		$main.on('input', '[data-role="search"]', () => this.render_queue());
	}

	load() {
		frappe.call('vobiz_click_to_call.api.console.get_agent_console_data', { limit: 30 }).then((r) => {
			const data = r.message || {};
			this.state.queue = data.queue || [];
			this.state.active_call = data.active_call || null;
			this.state.dispositions = data.dispositions || [];
			if (!this.state.lead_disposition_context || !this.state.lead_disposition_context.name) {
				this.state.lead_disposition_context = { options: (data.dispositions || []).map(value => ({ name: value })) };
			}
			this.render_summary(data.summary || {});
			this.render_availability(data.availability || {}, data.active_call || {});
			this.render_queue();
			this.render_dispositions();
			this.render_active_call();
			this.refresh_workdesk_live_call();
			this.render_auto_toggle();
			this.refresh_auto_dial_current();
			this.render_auto_live();
			this.maybe_continue_auto_dial();
			if (!this.state.selected && this.state.queue.length) {
				this.select_row(0);
			}
			this.restore_workdesk_dialog();
		});
	}

	on_page_show() {
		this.state.restore_checked = false;
		this.restore_workdesk_dialog();
	}

	start_polling() {
		clearInterval(this.poller);
		this.poller = setInterval(() => this.load(), 5000);
		$(window).one('beforeunload', () => {
			clearInterval(this.poller);
			clearInterval(this.timer);
		});
	}

	render_summary(summary) {
		const stats = [
			{ metric: 'connected', label: __('Connected'), value: summary.connected || 0, icon: 'fa-phone', color: '#16a34a' },
			{ metric: 'missed', label: __('Missed'), value: summary.missed || 0, icon: 'fa-phone', color: '#dc2626' },
			{ metric: 'total', label: __('Avg Call Duration'), value: summary.average_duration_label || '0s', icon: 'fa-clock-o', color: '#0284c7' },
			{ metric: 'total', label: __('Total Call'), value: summary.total || 0, icon: 'fa-bar-chart', color: '#ca8a04' },
		];
		this.page.main.find('[data-role="stats"]').html(stats.map(s => `
			<div class="vobiz-stat clickable" data-action="performance" data-metric="${frappe.utils.escape_html(s.metric)}">
				<div class="vobiz-icon" style="color:${s.color}"><i class="fa ${s.icon}"></i></div>
				<strong>${frappe.utils.escape_html(String(s.value))}</strong>
				<span>${frappe.utils.escape_html(s.label)}</span>
			</div>
		`).join(''));
	}

	render_availability(capability, active_call) {
		const status = active_call.availability_status || capability.availability_status || (capability.can_call ? 'Available' : 'Unavailable');
		this.page.main.find('[data-role="availability"]').text(status);
		this.page.main.find('.vobiz-state-dot').css('background', status === 'Available' ? '#16a34a' : status === 'Busy' ? '#f97316' : '#9ca3af');
	}

	render_queue() {
		const query = (this.page.main.find('[data-role="search"]').val() || '').toLowerCase();
		const rows = this.state.queue
			.map((row, index) => ({ ...row, index }))
			.filter(row => !query || [row.name, row.title, row.company, row.phone, row.status, row.next_action].join(' ').toLowerCase().includes(query));
		this.page.main.find('[data-role="queue"]').html(rows.map(row => this.row_html(row)).join('') || `
			<tr><td colspan="7" class="text-muted text-center">${__('No callable records found')}</td></tr>
		`);
		this.update_selected_count();
	}

	row_html(row) {
		const initials = (row.title || row.name || '?').trim().slice(0, 1).toUpperCase();
		const statusClass = String(row.status || '').split(' ')[0];
		const loading = this.state.detail_loading_key === this.detail_key(row);
		return `
			<tr data-index="${row.index}" data-action="select-row">
				<td><input type="checkbox" data-role="row-check"></td>
				<td><code>${frappe.utils.escape_html(row.name || '')}</code></td>
				<td><div class="vobiz-person"><span class="vobiz-avatar">${frappe.utils.escape_html(initials)}</span><span>${frappe.utils.escape_html(row.title || row.name || '')}</span></div></td>
				<td>${frappe.utils.escape_html(row.phone || '')}</td>
				<td><span class="vobiz-status ${frappe.utils.escape_html(statusClass)}">${frappe.utils.escape_html(row.status || '')}</span></td>
				<td>${frappe.utils.escape_html(row.next_action || '')}</td>
				<td>
					<button class="btn btn-xs btn-primary" data-action="call-row" ${loading ? 'disabled' : ''}>
						<i class="fa ${loading ? 'fa-spinner fa-spin' : 'fa-phone'}"></i> ${loading ? __('Loading') : __('Details')}
					</button>
				</td>
			</tr>
		`;
	}

	update_selected_count() {
		const count = this.page.main.find('[data-role="row-check"]:checked').length;
		const session = this.state.auto_dial || {};
		if (session.running || (session.results || []).length) {
			const total = (session.queue || []).length;
			const done = (session.results || []).length;
			const status = session.running ? __('running') : __('stopped');
			this.page.main.find('[data-role="selected-count"]').text(
				__('{0} leads selected • Auto dial {1}: {2}/{3}', [count, status, done, total])
			);
			return;
		}
		this.page.main.find('[data-role="selected-count"]').text(__('{0} leads selected', [count]));
	}

	render_auto_toggle() {
		const session = this.state.auto_dial || {};
		const $button = this.page.main.find('[data-role="auto-toggle"]');
		if (session.running) {
			$button.removeClass('btn-primary').addClass('btn-danger')
				.html(`<i class="fa fa-stop"></i> ${__('Stop Auto Dial')}`);
			return;
		}
		$button.removeClass('btn-danger').addClass('btn-primary')
			.html(`<i class="fa fa-play"></i> ${__('Start Auto Dial')}`);
	}

	render_auto_live() {
		const session = this.state.auto_dial || {};
		const events = (session.events || []).slice(-12).reverse();
		const current = session.current || {};
		const currentDetail = `${current.status || __('Starting')}${current.call_log ? ` • ${current.call_log}` : ''}`;
		const currentHtml = current.lead ? `
			<div class="vobiz-auto-event active">
				<strong>${__('Current')}: ${frappe.utils.escape_html(current.lead)}</strong>
				<span>${frappe.utils.escape_html(currentDetail)}</span>
				<span>${frappe.utils.escape_html(current.phone || '')}</span>
			</div>
		` : '';
		this.page.main.find('[data-role="auto-live-state"]').text(session.running ? __('Running') : __('Stopped'));
		this.page.main.find('[data-role="auto-live"]').html(currentHtml + (events.map(event => `
			<div class="vobiz-auto-event ${frappe.utils.escape_html(event.state || '')}">
				<strong>${frappe.utils.escape_html(event.title || '')}</strong>
				<span>${frappe.utils.escape_html(event.detail || '')}</span>
				<span>${frappe.utils.escape_html(event.time || '')}</span>
			</div>
		`).join('') || (!currentHtml ? `<div class="text-muted">${__('Start auto dial to see live call actions here.')}</div>` : '')));
	}

	add_auto_event(title, detail, state) {
		const session = this.state.auto_dial || {};
		session.events = session.events || [];
		session.events.push({
			title,
			detail,
			state: state || '',
			time: frappe.datetime.now_datetime()
		});
		this.state.auto_dial = session;
		this.render_auto_live();
	}

	select_row(index) {
		const row = this.state.queue[index];
		if (!row) return;
		this.state.selected = row;
		frappe.call('vobiz_click_to_call.api.console.get_reference_context', {
			reference_doctype: row.doctype,
			reference_name: row.name,
			lite: 1
		}).then((r) => {
			this.state.context = r.message || {};
			this.apply_context_dispositions(this.state.context);
			this.show_tab('call_summary');
			this.render_focus(row);
		});
	}

	render_focus(row) {
		this.page.main.find('[data-role="focus-name"]').text(row.title || row.name || __('Selected record'));
		this.page.main.find('[data-role="focus-meta"]').text(`${row.doctype} • ${row.phone || __('No phone')}`);
	}

	render_active_call() {
		const active = this.state.active_call || {};
		const last = active.last_call || {};
		this.page.main.find('[data-role="call-status"]').text(active.status || last.status || __('Idle'));
		if (active.reference_name) {
			this.page.main.find('[data-role="focus-name"]').text(active.reference_name);
			this.page.main.find('[data-role="focus-meta"]').text(`${active.reference_doctype || ''} • ${active.customer_number_display || ''}`);
		} else if (last.status) {
			this.page.main.find('[data-role="focus-meta"]').text(__('Last call {0}', [last.status]));
		}
		if (active.name && !this.is_terminal_status(active.status)) {
			this.state.call_started_at = active.started_at ? new Date(active.started_at) : null;
			this.start_timer();
		} else {
			this.state.call_started_at = null;
			this.stop_timer();
			if (last.name && this.is_terminal_status(last.status)) {
				this.clear_tracked_live_call(last.name);
				this.maybe_prompt_workdesk_disposition(last);
			}
		}
		this.render_call_assets(active.name ? active : last);
		this.render_workdesk_live_call();
	}

	render_call_assets(call) {
		const rows = [];
		if (call.recording_status) {
			rows.push(`<div><strong>${__('Recording')}</strong>: ${frappe.utils.escape_html(call.recording_status)}</div>`);
		}
		if (call.recording_url) {
			const recordingUrl = call.recording_download_url || `/api/method/vobiz_click_to_call.api.recording.download?call_log=${encodeURIComponent(call.name || '')}`;
			rows.push(`<div><a href="${frappe.utils.escape_html(recordingUrl)}" target="_blank" rel="noopener">${__('Open Recording')}</a></div>`);
		}
		if (call.transcript_status) {
			rows.push(`<div><strong>${__('Transcript')}</strong>: ${frappe.utils.escape_html(call.transcript_status)}</div>`);
		}
		if (call.transcript_text) {
			rows.push(`<div class="vobiz-transcript">${frappe.utils.escape_html(call.transcript_text)}</div>`);
		}
		if (call.recording_error || call.transcript_error) {
			rows.push(`<div class="text-muted">${frappe.utils.escape_html(call.recording_error || call.transcript_error)}</div>`);
		}
		this.page.main.find('[data-role="call-assets"]').html(rows.join(''));
	}

	start_timer() {
		clearInterval(this.timer);
		const tick = () => {
			if (!this.state.call_started_at) {
				this.page.main.find('[data-role="timer"]').text('00:00');
				return;
			}
			const seconds = Math.max(0, Math.floor((Date.now() - this.state.call_started_at.getTime()) / 1000));
			const minutes = Math.floor(seconds / 60);
			this.page.main.find('[data-role="timer"]').text(`${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`);
		};
		tick();
		this.timer = setInterval(tick, 1000);
	}

	stop_timer() {
		clearInterval(this.timer);
		this.timer = null;
		this.page.main.find('[data-role="timer"]').text('00:00');
	}

	render_dispositions() {
		const context = this.state.lead_disposition_context || {};
		const statusOptions = context.status_options || [];
		const currentStatus = context.status || '';
		const currentDisposition = context.disposition || '';
		const options = [''].concat(this.state.dispositions || []);
		this.page.main.find('[data-role="lead-status"]').html([''].concat(statusOptions).map(value =>
			`<option value="${frappe.utils.escape_html(value)}">${frappe.utils.escape_html(value || __('Select CRM Status'))}</option>`
		).join('')).val(currentStatus);
		this.page.main.find('[data-role="disposition"]').html(options.map(value =>
			`<option value="${frappe.utils.escape_html(value)}">${frappe.utils.escape_html(value || __('Select SR Lead Disposition'))}</option>`
		).join('')).val(currentDisposition);
	}

	apply_context_dispositions(context) {
		const leadDisposition = ((context || {}).workdesk || {}).lead_disposition || {};
		const options = (leadDisposition.options || []).map(row => row.name).filter(Boolean);
		this.state.lead_disposition_context = leadDisposition;
		this.state.dispositions = options.length ? options : this.state.dispositions;
		this.render_dispositions();
	}

	refresh_lead_disposition_options() {
		const reference = this.active_disposition_reference();
		if (!reference.reference_doctype || !reference.reference_name) return;

		const leadStatus = this.page.main.find('[data-role="lead-status"]').val();
		frappe.call({
			method: 'vobiz_click_to_call.api.disposition.get_lead_disposition_context_api',
			args: {
				reference_doctype: reference.reference_doctype,
				reference_name: reference.reference_name,
				lead_status: leadStatus
			}
		}).then((r) => {
			const context = r.message || {};
			this.state.lead_disposition_context = context;
			this.state.dispositions = (context.options || []).map(row => row.name).filter(Boolean);
			this.render_dispositions();
		});
	}

	active_disposition_reference() {
		const active = this.state.active_call || {};
		const selected = this.state.selected || {};
		const contextReference = (this.state.context || {}).reference || {};
		return {
			reference_doctype: active.reference_doctype || selected.doctype || contextReference.doctype,
			reference_name: active.reference_name || selected.name || contextReference.name
		};
	}

	show_tab(tab) {
		this.page.main.find('[data-tab]').removeClass('active');
		this.page.main.find(`[data-tab="${tab}"]`).addClass('active');
		const context = this.state.context || {};
		if (tab === 'transcript') return this.render_transcript(context.history || []);
		if (tab === 'audio') return this.render_audio(context.history || []);
		if (tab === 'history') return this.render_history(context.history || []);
		this.render_call_summary(context);
	}

	render_call_summary(context) {
		const reference = context.reference || this.state.selected || {};
		const latest = (context.history || [])[0] || {};
		const guidance = context.guidance || {};
		const script = guidance.script || [__('Select a lead to load call guidance.')];
		this.page.main.find('[data-role="tab-panel"]').html(`
			<div class="vobiz-detail-head">
				<div>
					<h3>${frappe.utils.escape_html(reference.title || reference.name || __('Lead Details'))}</h3>
					<div class="text-muted">${frappe.utils.escape_html(reference.doctype || '')} • ${frappe.utils.escape_html(reference.phone || '')}</div>
				</div>
				<button class="btn btn-primary btn-sm" data-action="call-selected"><i class="fa fa-phone"></i> ${__('Start Call')}</button>
			</div>
			<strong>${__('Call Info')}</strong>
			<div class="vobiz-info-list">
				${this.info_row('fa-phone', __('Caller'), reference.phone || __('No phone'))}
				${this.info_row('fa-user', __('Agent'), latest.user || frappe.session.user)}
				${this.info_row('fa-calendar', __('Date'), latest.creation ? frappe.datetime.str_to_user(latest.creation) : __('No previous call'))}
				${this.info_row('fa-clock-o', __('Duration'), latest.duration_label || '00:00')}
			</div>
			<hr>
			<strong>${__('Guidance')}</strong>
			<ul class="vobiz-guidance-list">${script.map(line => `<li>${frappe.utils.escape_html(line)}</li>`).join('')}</ul>
		`);
	}

	info_row(icon, label, value) {
		return `
			<div class="vobiz-info-row">
				<div class="vobiz-info-icon"><i class="fa ${icon}"></i></div>
				<div><div class="text-muted">${frappe.utils.escape_html(label)}</div><strong>${frappe.utils.escape_html(String(value || ''))}</strong></div>
			</div>
		`;
	}

	render_transcript(history) {
		const rows = history.filter(row => row.transcript_text || row.transcript_status || row.ai_summary);
		this.page.main.find('[data-role="tab-panel"]').html(rows.map(row => `
			<div class="vobiz-audio-card">
				<div class="vobiz-detail-head">
					<div><strong>${frappe.utils.escape_html(row.name)}</strong><div class="text-muted">${frappe.datetime.str_to_user(row.creation)} • ${frappe.utils.escape_html(row.status || '')}</div></div>
					<a class="btn btn-xs btn-default" href="/app/vobiz-call-log/${frappe.utils.escape_html(row.name)}">${__('Open')}</a>
				</div>
				${row.ai_summary ? `<div><strong>${__('Summary')}</strong><div>${frappe.utils.escape_html(row.ai_summary)}</div></div>` : ''}
				${row.transcript_text ? `<div class="vobiz-transcript">${frappe.utils.escape_html(row.transcript_text)}</div>` : `<div class="text-muted">${frappe.utils.escape_html(row.transcript_status || __('No transcript yet'))}</div>`}
			</div>
		`).join('') || `<div class="text-muted">${__('No transcript available for this lead.')}</div>`);
	}

	render_audio(history) {
		const rows = history.filter(row => row.recording_url || row.recording_status);
		this.page.main.find('[data-role="tab-panel"]').html(`
			<div class="vobiz-audio-list">
				${rows.map(row => `
					<div class="vobiz-audio-card">
						<div><strong>${frappe.utils.escape_html(row.name)}</strong></div>
						<div class="text-muted">${frappe.datetime.str_to_user(row.creation)} • ${frappe.utils.escape_html(row.recording_status || row.status || '')} • ${frappe.utils.escape_html(row.duration_label || '')}</div>
						${row.recording_download_url ? `<audio controls src="${frappe.utils.escape_html(row.recording_download_url)}" style="width:100%; margin-top:8px;"></audio>` : `<div class="text-muted">${__('No audio file yet')}</div>`}
						${row.recording_download_url ? `<a href="${frappe.utils.escape_html(row.recording_download_url)}" target="_blank" rel="noopener">${__('Open Recording')}</a>` : ''}
					</div>
				`).join('') || `<div class="text-muted">${__('No recording available for this lead.')}</div>`}
			</div>
		`);
	}

	render_history(history) {
		this.page.main.find('[data-role="tab-panel"]').html(history.map(row => `
			<div class="vobiz-history-row">
				<div>${frappe.datetime.str_to_user(row.creation)}</div>
				<div>${frappe.utils.escape_html(row.status || '')}</div>
				<div>${frappe.utils.escape_html(row.disposition || row.ai_next_action || row.ai_summary || '')}</div>
			</div>
		`).join('') || `<div class="text-muted">${__('No previous interactions')}</div>`);
	}

	render_insights(history) {
		const total = history.length;
		const connected = history.filter(row => ['Connected', 'Completed'].includes(row.status)).length;
		const missed = history.filter(row => ['Failed', 'Busy', 'No Answer', 'Cancelled'].includes(row.status)).length;
		this.page.main.find('[data-role="tab-panel"]').html(`
			<div class="vobiz-stats" style="grid-template-columns: repeat(3, minmax(0, 1fr));">
				<div class="vobiz-stat"><span>${__('Previous Calls')}</span><strong>${total}</strong></div>
				<div class="vobiz-stat"><span>${__('Connected')}</span><strong>${connected}</strong></div>
				<div class="vobiz-stat"><span>${__('Missed')}</span><strong>${missed}</strong></div>
			</div>
		`);
	}

	call_row(index) {
		const row = this.state.queue[index];
		if (!row) return;
		if (this.state.detail_loading_key) return;
		this.state.selected = row;
		this.render_focus(row);
		this.set_detail_loading(row, true);
		const request = frappe.call({
			method: 'vobiz_click_to_call.api.console.get_reference_context',
			args: {
				reference_doctype: row.doctype,
				reference_name: row.name,
				lite: 1
			}
		});
		request.then((r) => {
			this.state.context = r.message || {};
			this.apply_context_dispositions(this.state.context);
			this.open_detail_dialog(row, r.message || {});
		});
		request.always(() => this.set_detail_loading(row, false));
	}

	detail_key(row) {
		return row && row.doctype && row.name ? `${row.doctype}::${row.name}` : '';
	}

	set_detail_loading(row, loading) {
		const key = this.detail_key(row);
		if (!key) return;
		if (loading) {
			this.state.detail_loading_key = key;
		} else if (this.state.detail_loading_key === key) {
			this.state.detail_loading_key = null;
		}
		this.render_queue();
	}

	open_detail_dialog(row, context) {
		context.workdesk = context.workdesk || {};
		context.loaded_workdesk_tabs = context.loaded_workdesk_tabs || { summary: true };
		this.state.navigating_from_workdesk = false;
		this.state.active_workdesk_key = row && row.doctype && row.name ? `${row.doctype}::${row.name}` : null;
		this.state.active_workdesk_row = row;
		const dialog = new frappe.ui.Dialog({
			title: __('Agent Workdesk'),
			size: 'extra-large',
			static: true,
			fields: [{ fieldname: 'details', fieldtype: 'HTML' }],
			primary_action_label: __('Start Call'),
			primary_action: () => this.handle_workdesk_primary_action(row)
		});
		this.state.active_workdesk_dialog = dialog;
		dialog.get_close_btn().show();
		dialog.$wrapper.on('hidden.bs.modal', () => {
			this.state.active_workdesk_key = null;
			this.state.active_workdesk_body = null;
			this.state.active_workdesk_row = null;
			this.state.active_workdesk_dialog = null;
			if (!this.state.navigating_from_workdesk) {
				this.clear_workdesk_return_state();
			}
		});
		dialog.show();
		const $body = dialog.get_field('details').$wrapper;
		this.state.active_workdesk_body = $body;
		const render = (tab) => {
			$body.find('[data-detail-tab]').removeClass('active');
			$body.find(`[data-detail-tab="${tab}"]`).addClass('active');
			const workdesk = context.workdesk || {};
			if (tab === 'encounters') {
				$body.find('[data-detail-panel]').html(this.workdesk_encounters_html(workdesk));
			} else if (tab === 'clinical-history') {
				$body.find('[data-detail-panel]').html(this.workdesk_clinical_history_html(workdesk));
			} else if (tab === 'reports') {
				$body.find('[data-detail-panel]').html(this.workdesk_reports_html(workdesk));
			} else if (tab === 'vobiz') {
				$body.find('[data-detail-panel]').html(this.workdesk_vobiz_html(workdesk, context.history || []));
			} else if (tab === 'whatsapp') {
				$body.find('[data-detail-panel]').html(this.workdesk_whatsapp_html(workdesk));
				setTimeout(() => this.scroll_whatsapp_to_bottom($body), 50);
			} else {
				$body.find('[data-detail-panel]').html(this.workdesk_lead_html(row, context));
				this.render_workdesk_live_call();
			}
		};
		$body.html(`
			<div class="vobiz-detail-dialog">
				<div class="vobiz-tabs">
					<button class="active" data-detail-tab="summary">${__('CRM Lead')}</button>
					<button data-detail-tab="encounters">${__('Encounters')}</button>
					<button data-detail-tab="clinical-history">${__('Patient Clinical History')}</button>
					<button data-detail-tab="reports">${__('Reports')}</button>
					<button data-detail-tab="vobiz">${__('Vobiz Summary')}</button>
					<button data-detail-tab="whatsapp">${__('WhatsApp')}</button>
				</div>
				<div data-detail-panel></div>
			</div>
		`);
		$body.on('click', '[data-detail-tab]', (e) => {
			const tab = $(e.currentTarget).data('detail-tab');
			this.load_workdesk_tab(row, context, tab, $body, render);
		});
		$body.on('click', '[data-workdesk-action]', (e) => this.handle_workdesk_action($(e.currentTarget).data('workdesk-action'), row, context, $body));
		$body.on('scroll', '[data-wa-chat-list]', (e) => {
			const el = e.currentTarget;
			if (el.scrollTop <= 24) {
				this.load_more_whatsapp_messages($(el));
			}
		});
		$body.on('click', '[data-wa-send]', () => this.send_workdesk_whatsapp($body));
		$body.on('click', '[data-wa-attach]', (e) => {
			e.stopPropagation();
			$body.find('[data-wa-emoji-menu]').removeClass('show');
			$(e.currentTarget).siblings('[data-wa-attach-menu]').toggleClass('show');
		});
		$body.on('click', '[data-wa-attach-action]', (e) => {
			e.stopPropagation();
			$body.find('[data-wa-attach-menu]').removeClass('show');
			this.open_workdesk_attachment_dialog($body, $(e.currentTarget).data('wa-attach-action'));
		});
		$body.on('click', '[data-wa-emoji]', (e) => {
			e.stopPropagation();
			$body.find('[data-wa-attach-menu]').removeClass('show');
			$(e.currentTarget).siblings('[data-wa-emoji-menu]').toggleClass('show');
		});
		$body.on('click', '[data-wa-emoji-value]', (e) => {
			e.stopPropagation();
			this.insert_workdesk_emoji($body, $(e.currentTarget).data('wa-emoji-value'));
			$body.find('[data-wa-emoji-menu]').removeClass('show');
		});
		$body.on('keydown', '[data-wa-reply]', (e) => {
			if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
				e.preventDefault();
				this.send_workdesk_whatsapp($body);
			}
		});
		$body.on('click', () => {
			$body.find('[data-wa-attach-menu], [data-wa-emoji-menu]').removeClass('show');
		});
		$body.on('click', '[data-open-doc]', (e) => {
			const $btn = $(e.currentTarget);
			this.remember_workdesk_return(row);
			frappe.set_route('Form', $btn.data('doctype'), $btn.data('name'));
		});
		render('summary');
		this.update_workdesk_primary_action(row);
	}

	handle_workdesk_primary_action(row) {
		const call = this.matching_active_call(row);
		if (call && call.name && !this.is_terminal_status(call.status)) {
			return this.cancel_call_log(call.name, row);
		}
		this.state.selected = row;
		return this.start_call_for_row(row);
	}

	update_workdesk_primary_action(row) {
		const dialog = this.state.active_workdesk_dialog;
		if (!dialog || !row) return;

		const call = this.matching_active_call(row);
		const isActive = Boolean(call && call.name && !this.is_terminal_status(call.status));
		dialog.get_primary_btn()
			.toggleClass('btn-primary', !isActive)
			.toggleClass('btn-danger', isActive)
			.prop('disabled', false)
			.html(isActive
				? `<i class="fa fa-phone"></i> ${__('Stop Call')}`
				: `<i class="fa fa-phone"></i> ${__('Start Call')}`);
		this.update_workdesk_header_call_action(row, call);
	}

	update_workdesk_header_call_action(row, call) {
		const $body = this.state.active_workdesk_body;
		if (!$body || !$body.length || !row) return;
		const isActive = Boolean(call && call.name && !this.is_terminal_status(call.status));
		$body.find('[data-workdesk-action="call"]')
			.toggleClass('btn-primary', !isActive)
			.toggleClass('btn-success', !isActive)
			.toggleClass('btn-danger', isActive)
			.attr('data-call-log', isActive ? call.name : '')
			.html(isActive
				? `<i class="fa fa-phone"></i> ${__('Stop Call')}`
				: `<i class="fa fa-phone"></i> ${__('Start Call')}`);
	}

	load_workdesk_tab(row, context, tab, $body, render) {
		const deferredTabs = ['encounters', 'clinical-history', 'reports', 'vobiz', 'whatsapp'];
		context.loaded_workdesk_tabs = context.loaded_workdesk_tabs || { summary: true };
		if (!deferredTabs.includes(tab) || context.loaded_workdesk_tabs[tab]) {
			render(tab);
			return;
		}

		$body.find('[data-detail-tab]').removeClass('active');
		$body.find(`[data-detail-tab="${tab}"]`).addClass('active');
		$body.find('[data-detail-panel]').html(`
			<div class="vobiz-workdesk-card">
				<div class="vobiz-empty">${__('Loading details...')}</div>
			</div>
		`);
		frappe.call({
			method: 'vobiz_click_to_call.api.console.get_workdesk_tab',
			args: {
				reference_doctype: row.doctype,
				reference_name: row.name,
				tab
			}
		}).then((r) => {
			const data = r.message || {};
			context.workdesk = Object.assign(context.workdesk || {}, data);
			if (data.history) {
				context.history = data.history;
			}
			context.loaded_workdesk_tabs[tab] = true;
			render(tab);
		});
	}

	workdesk_lead_html(row, context) {
		const workdesk = context.workdesk || {};
		const fields = ((workdesk.lead || {}).fields || []);
		return `
			<div class="vobiz-workdesk">
				${this.workdesk_header_html(row, context)}
				<div class="vobiz-workdesk-grid">
					<div class="vobiz-workdesk-card">
						<h4>${__('CRM Lead Data')}</h4>
						<div class="vobiz-field-grid">
							${fields.map(field => this.workdesk_field_html(field.label, field.value)).join('') || `<div class="vobiz-empty">${__('No CRM fields found for this record.')}</div>`}
						</div>
					</div>
					${this.workdesk_lead_disposition_html(workdesk)}
					<div class="vobiz-workdesk-card">
						<h4>${__('Guidance')}</h4>
						<div data-workdesk-live-call>${this.workdesk_live_call_html(row)}</div>
						<ul class="vobiz-guidance-list">${((context.guidance || {}).script || []).map(line => `<li>${frappe.utils.escape_html(line)}</li>`).join('')}</ul>
					</div>
				</div>
			</div>
		`;
	}

	workdesk_lead_disposition_html(workdesk) {
		const leadDisposition = workdesk.lead_disposition || {};
		const options = leadDisposition.options || [];
		if (!leadDisposition.name && !leadDisposition.status && !options.length) {
			return '';
		}
		return `
			<div class="vobiz-workdesk-card">
				<h4>${__('Lead Disposition')}</h4>
				<div class="vobiz-field-grid">
					${this.workdesk_field_html(__('CRM Status'), leadDisposition.status || '-')}
					${this.workdesk_field_html(__('Lead Disposition'), leadDisposition.disposition || '-')}
				</div>
				<hr>
				<div class="vobiz-related-meta">${__('Available for this lead')}</div>
				<div class="vobiz-info-list">
					${options.slice(0, 8).map(row => `
						<div class="vobiz-related-row">
							<div>
								<div class="vobiz-related-title">${frappe.utils.escape_html(row.name || '')}</div>
								<div class="vobiz-related-meta">${frappe.utils.escape_html(row.status || __('Any CRM Status'))}</div>
							</div>
						</div>
					`).join('') || `<div class="vobiz-empty">${__('No active SR Lead Disposition found for this status.')}</div>`}
				</div>
			</div>
		`;
	}

	render_workdesk_live_call() {
		const $body = this.state.active_workdesk_body;
		const row = this.state.active_workdesk_row;
		if (!$body || !$body.length || !row) return;
		$body.find('[data-workdesk-live-call]').html(this.workdesk_live_call_html(row));
		this.update_workdesk_primary_action(row);
	}

	workdesk_live_call_html(row) {
		const call = this.matching_active_call(row);
		if (!call || !call.name) {
			return `
				<div class="vobiz-live-call">
					<div class="vobiz-live-call-head">
						<strong>${__('Live Call')}</strong>
						<span class="vobiz-live-pill">${__('Idle')}</span>
					</div>
					<div class="vobiz-live-meta">${__('Start a call to see agent and customer live events here.')}</div>
				</div>
			`;
		}

		const steps = this.live_call_steps(call);
		const details = [
			call.dial_status ? __('Dial: {0}', [call.dial_status]) : '',
			call.hangup_cause ? __('Hangup: {0}', [call.hangup_cause]) : '',
			call.error_message ? call.error_message : ''
		].filter(Boolean).join(' · ');

		return `
			<div class="vobiz-live-call">
				<div class="vobiz-live-call-head">
					<strong>${__('Live Call')}</strong>
					<span class="vobiz-live-pill">${frappe.utils.escape_html(call.status || __('Active'))}</span>
				</div>
				<div class="vobiz-live-steps">
					${steps.map(step => `
						<div class="vobiz-live-step ${frappe.utils.escape_html(step.state)}">
							<span class="vobiz-live-dot"></span>
							<div>
								<div class="vobiz-live-title">${frappe.utils.escape_html(step.title)}</div>
								<div class="vobiz-live-meta">${frappe.utils.escape_html(step.meta)}</div>
							</div>
						</div>
					`).join('')}
				</div>
				${details ? `<div class="vobiz-live-meta" style="margin-top:10px;">${frappe.utils.escape_html(details)}</div>` : ''}
			</div>
		`;
	}

	matching_active_call(row) {
		const active = this.state.active_call || {};
		const tracked = this.state.workdesk_live_call || {};
		if (
			active.name &&
			active.reference_doctype === row.doctype &&
			active.reference_name === row.name
		) {
			return active;
		}
		const last = active.last_call || {};
		if (
			last.name &&
			(last.reference_doctype || active.reference_doctype) === row.doctype &&
			(last.reference_name || active.reference_name) === row.name
		) {
			return last;
		}
		if (
			tracked.name &&
			tracked.reference_doctype === row.doctype &&
			tracked.reference_name === row.name
		) {
			return tracked;
		}
		return null;
	}

	refresh_workdesk_live_call() {
		const callLog = this.state.workdesk_live_call_log;
		if (!callLog || this.state.workdesk_live_polling) return;
		const active = this.state.active_call || {};
		if (active.name === callLog) {
			this.state.workdesk_live_call = active;
			this.render_workdesk_live_call();
			return;
		}

		this.state.workdesk_live_polling = true;
		frappe.call({
			method: 'vobiz_click_to_call.api.call.get_call_status',
			args: { call_log: callLog }
		}).then((r) => {
			const call = r.message || {};
			if (call.name) {
				this.state.workdesk_live_call = call;
				this.render_workdesk_live_call();
				if (this.is_terminal_status(call.status)) {
					this.state.active_call = { last_call: call };
					this.clear_tracked_live_call(call.name);
					this.render_workdesk_live_call();
					this.maybe_prompt_workdesk_disposition(call);
				}
			}
		}).always(() => {
			this.state.workdesk_live_polling = false;
		});
	}

	live_call_steps(call) {
		const flow = call.call_flow || 'Customer First';
		const first = flow === 'Agent First' ? __('Agent') : __('Customer');
		const second = flow === 'Agent First' ? __('Customer') : __('Agent');
		const firstNumber = flow === 'Agent First' ? call.agent_mobile_display : call.customer_number_display;
		const secondNumber = flow === 'Agent First' ? call.customer_number_display : call.agent_mobile_display;
		const status = call.status || '';
		const terminal = ['Completed', 'Failed', 'Busy', 'No Answer', 'Cancelled', 'Canceled'].includes(status);
		const answeredFirst = Boolean(call.answer_time) || ['Agent Answered', 'Customer Answered', 'Agent Ringing', 'Connected', 'Completed'].includes(status);
		const connected = ['Connected', 'Completed'].includes(status);

		let firstState = 'active';
		let firstMeta = __('Calling {0}...', [first.toLowerCase()]);
		let secondState = 'waiting';
		let secondMeta = __('Waiting for {0} to answer.', [first.toLowerCase()]);

		if (answeredFirst || connected) {
			firstState = 'done';
			firstMeta = __('{0} answered.', [first]);
			secondState = connected ? 'done' : 'active';
			secondMeta = connected ? __('Call connected with {0}.', [second.toLowerCase()]) : __('Calling {0}...', [second.toLowerCase()]);
		}

		if (terminal) {
			if (connected || status === 'Completed') {
				firstState = 'done';
				secondState = 'done';
				firstMeta = __('{0} answered.', [first]);
				secondMeta = __('Call completed.');
			} else if (answeredFirst) {
				firstState = 'done';
				secondState = 'failed';
				firstMeta = __('{0} answered.', [first]);
				secondMeta = this.live_failure_text(call, second);
			} else {
				firstState = 'failed';
				secondState = 'waiting';
				firstMeta = this.live_failure_text(call, first);
				secondMeta = __('Not called because {0} did not connect.', [first.toLowerCase()]);
			}
		}

		return [
			{
				state: firstState,
				title: __('{0} leg', [first]),
				meta: `${firstMeta}${firstNumber ? ` ${firstNumber}` : ''}`
			},
			{
				state: secondState,
				title: __('{0} leg', [second]),
				meta: `${secondMeta}${secondNumber ? ` ${secondNumber}` : ''}`
			}
		];
	}

	live_failure_text(call, party) {
		const status = call.status || '';
		const signal = this.normalized_call_signal(call);
		if (status === 'Busy' || signal.includes('busy')) {
			return __('{0} line was busy.', [party]);
		}
		if (status === 'No Answer' || signal.includes('no-answer') || signal.includes('no answer') || signal.includes('timeout') || signal.includes('unanswered')) {
			return __('{0} did not respond or pick the call.', [party]);
		}
		if (
			status === 'Cancelled' ||
			status === 'Canceled' ||
			signal.includes('cancel') ||
			signal.includes('reject') ||
			signal.includes('decline') ||
			signal.includes('hangup-before-connect') ||
			signal.includes('originator-cancel')
		) {
			return __('{0} cut or rejected the call.', [party]);
		}
		if (signal.includes('hangup') && !['Connected', 'Completed'].includes(status)) {
			return __('{0} cut the call.', [party]);
		}
		return __('{0} call failed.', [party]);
	}

	normalized_call_signal(call) {
		return [
			call.status,
			call.call_status,
			call.dial_status,
			call.hangup_cause,
			call.error_message
		].filter(Boolean).join(' ').toLowerCase().replace(/_/g, '-');
	}

	workdesk_header_html(row, context) {
		const workdesk = context.workdesk || {};
		const agent = workdesk.agent || {};
		const agent_number = agent.agent_mobile || __('Not mapped');
		const customer_number = row.phone || __('No phone');
		return `
			<div class="vobiz-workdesk-top">
				<div class="vobiz-workdesk-title">
					<h3>${frappe.utils.escape_html(row.title || row.name || '')}</h3>
					<div class="vobiz-call-route">
						<span class="vobiz-call-route-chip">${__('Agent')}: ${frappe.utils.escape_html(agent_number)}</span>
						<i class="fa fa-link vobiz-call-route-icon" aria-hidden="true"></i>
						<span class="vobiz-call-route-chip">${__('Customer')}: ${frappe.utils.escape_html(customer_number)}</span>
					</div>
					<div class="text-muted">${frappe.utils.escape_html(row.doctype || '')} • ${frappe.utils.escape_html(row.phone || __('No phone'))}</div>
				</div>
				<div class="vobiz-workdesk-actions">
					<button class="btn btn-primary btn-sm" data-workdesk-action="call"><i class="fa fa-phone"></i> ${__('Start Call')}</button>
					<button class="btn btn-default btn-sm" data-workdesk-action="open-lead"><i class="fa fa-external-link"></i> ${__('Open Lead')}</button>
					<button class="btn btn-default btn-sm" data-workdesk-action="whatsapp"><i class="fa fa-whatsapp"></i> ${workdesk.whatsapp && workdesk.whatsapp.conversation ? __('Open WhatsApp') : __('WhatsApp')}</button>
					<button class="btn btn-default btn-sm" data-workdesk-action="new-encounter"><i class="fa fa-file-text-o"></i> ${__('Create Encounter')}</button>
				</div>
			</div>
		`;
	}

	workdesk_field_html(label, value) {
		return `
			<div class="vobiz-field">
				<div class="vobiz-field-label">${frappe.utils.escape_html(label || '')}</div>
				<div class="vobiz-field-value">${frappe.utils.escape_html(value === undefined || value === null || value === '' ? '-' : String(value))}</div>
			</div>
		`;
	}

	strip_html(value) {
		return String(value || '').replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').trim();
	}

	workdesk_encounters_html(workdesk) {
		return this.workdesk_related_html(__('Patient Encounters'), workdesk.encounters || [], 'Patient Encounter', (row) => [
			row.patient_name || row.patient || '',
			row.sr_encounter_type || '',
			row.sr_encounter_status || '',
			row.encounter_date || '',
			row.invoiced ? __('Invoiced') : ''
		].filter(Boolean).join(' • '), __('No previous encounter found.'), {
			label: __('Create Encounter'),
			action: 'new-encounter',
			icon: 'fa-file-text-o'
		});
	}

	workdesk_clinical_history_html(workdesk) {
		const history = workdesk.clinical_history || {};
		const patient = history.patient || {};
		const rows = history.rows || [];
		if (!patient.name && !rows.length) {
			return `<div class="vobiz-workdesk-card"><div class="vobiz-empty">${__('No linked patient found for clinical history.')}</div></div>`;
		}
		const patient_name = patient.patient_name || patient.first_name || patient.name || '-';
		const patient_id = patient.sr_patient_id || patient.patient_id || patient.name || '-';
		const mobile = patient.mobile || patient.mobile_no || patient.sr_mobile_no || '-';
		const phone = patient.phone || patient.phone_no || patient.sr_phone_no || '-';
		return `
			<div class="vobiz-workdesk-card">
				<h4>${__('Patient Clinical History')}</h4>
				<div class="vobiz-field-grid">
					${this.workdesk_field_html(__('Patient'), patient_name)}
					${this.workdesk_field_html(__('Patient ID'), patient_id)}
					${this.workdesk_field_html(__('Gender'), patient.sex || patient.gender || '-')}
					${this.workdesk_field_html(__('Mobile / Phone'), [mobile, phone].filter((value) => value && value !== '-').join(' / ') || '-')}
				</div>
				<hr>
				<div class="vobiz-clinical-history">
					${rows.map((row) => this.workdesk_clinical_history_row_html(row)).join('') || `<div class="vobiz-empty">${__('No encounters with clinical notes found.')}</div>`}
				</div>
			</div>
		`;
	}

	workdesk_clinical_history_row_html(row) {
		const date = row.encounter_date ? frappe.datetime.str_to_user(row.encounter_date) : '-';
		const practitioner = row.practitioner_name || row.practitioner || '';
		const section = (title, value) => {
			const clean = this.strip_html(value);
			return clean ? `
				<div class="vobiz-clinical-section">
					<div class="vobiz-clinical-label">${frappe.utils.escape_html(title)}</div>
					<div class="vobiz-clinical-text">${frappe.utils.escape_html(clean)}</div>
				</div>
			` : '';
		};
		const medications = row.medications || {};
		return `
			<div class="vobiz-clinical-card">
				<div class="vobiz-clinical-head">
					<strong>${frappe.utils.escape_html(row.name || '')}</strong>
					<span>${frappe.utils.escape_html([date, practitioner].filter(Boolean).join(' / '))}</span>
				</div>
				${section(__('Complaints'), row.sr_complaints)}
				${section(__('Observations'), row.sr_observations)}
				${section(__('Investigations'), row.sr_investigations)}
				${section(__('Diagnosis'), row.sr_diagnosis)}
				${section(__('Notes'), row.sr_notes)}
				${this.workdesk_medication_table_html(__('Ayurvedic Medications'), medications.drug_prescription || [])}
				${this.workdesk_medication_table_html(__('Homeopathy Medications'), medications.sr_homeopathy_drug_prescription || [])}
				${this.workdesk_medication_table_html(__('Allopathy Medications Considered'), medications.sr_allopathy_drug_prescription || [])}
			</div>
		`;
	}

	workdesk_medication_table_html(title, rows) {
		if (!rows.length) return '';
		return `
			<div class="vobiz-clinical-section">
				<div class="vobiz-clinical-label">${frappe.utils.escape_html(title)}</div>
				<div class="vobiz-table-wrap">
					<table class="table table-sm vobiz-clinical-med-table">
						<thead>
							<tr>
								<th>${__('Medication')}</th>
								<th>${__('Dosage')}</th>
								<th>${__('Period')}</th>
								<th>${__('Form')}</th>
								<th>${__('Instruction')}</th>
							</tr>
						</thead>
						<tbody>
							${rows.map((row) => `
								<tr>
									<td>${frappe.utils.escape_html(row.medication || '-')}</td>
									<td>${frappe.utils.escape_html(row.dosage || '-')}</td>
									<td>${frappe.utils.escape_html(row.period || '-')}</td>
									<td>${frappe.utils.escape_html(row.dosage_form || '-')}</td>
									<td>${frappe.utils.escape_html(row.sr_drug_instruction || '-')}</td>
								</tr>
							`).join('')}
						</tbody>
					</table>
				</div>
			</div>
		`;
	}

	workdesk_appointments_html(workdesk) {
		return this.workdesk_related_html(__('Patient Appointments'), workdesk.appointments || [], 'Patient Appointment', (row) => [
			row.patient_name || row.patient || '',
			row.appointment_date || row.appointment_datetime || '',
			row.appointment_time || '',
			row.status || row.department || row.practitioner || ''
		].filter(Boolean).join(' • '), __('No previous appointment found.'), {
			label: __('Create Appointment'),
			action: 'new-appointment',
			icon: 'fa-calendar'
		});
	}

	workdesk_invoices_html(workdesk) {
		return this.workdesk_related_html(__('Sales Invoices'), workdesk.sales_invoices || [], 'Sales Invoice', (row) => [
			row.customer || row.sr_si_patient_id || '',
			row.posting_date || '',
			row.status || '',
			row.grand_total ? frappe.format(row.grand_total, { fieldtype: 'Currency' }) : ''
		].filter(Boolean).join(' • '), __('No sales invoice found.'), {
			label: __('Create Sales Invoice'),
			action: 'new-invoice',
			icon: 'fa-file'
		});
	}

	workdesk_related_html(title, rows, doctype, metaBuilder, emptyText, emptyAction) {
		return `
			<div class="vobiz-workdesk-card">
				<h4>${frappe.utils.escape_html(title)}</h4>
				${rows.map(row => `
					<div class="vobiz-related-row">
						<div>
							<div class="vobiz-related-title">${frappe.utils.escape_html(row.name || '')}</div>
							<div class="vobiz-related-meta">${frappe.utils.escape_html(metaBuilder(row))}</div>
						</div>
						<button class="btn btn-xs btn-default" data-open-doc data-doctype="${frappe.utils.escape_html(doctype)}" data-name="${frappe.utils.escape_html(row.name || '')}">${__('Open')}</button>
					</div>
				`).join('') || this.workdesk_empty_action_html(emptyText, emptyAction)}
			</div>
		`;
	}

	workdesk_empty_action_html(emptyText, emptyAction) {
		if (!emptyAction) {
			return `<div class="vobiz-empty">${frappe.utils.escape_html(emptyText)}</div>`;
		}
		return `
			<div class="vobiz-empty">${frappe.utils.escape_html(emptyText)}</div>
			<div style="margin-top:12px;">
				<button class="btn btn-primary btn-sm" data-workdesk-action="${frappe.utils.escape_html(emptyAction.action)}">
					<i class="fa ${frappe.utils.escape_html(emptyAction.icon || 'fa-plus')}"></i> ${frappe.utils.escape_html(emptyAction.label)}
				</button>
			</div>
		`;
	}

	workdesk_reports_html(workdesk) {
		const reports = workdesk.reports || {};
		const files = reports.files || [];
		const ocr = reports.ocr || [];
		const insights = reports.insights || [];
		return `
			<div class="vobiz-workdesk-grid">
				<div class="vobiz-workdesk-card">
					<h4>${__('Files / Reports')}</h4>
					${files.map(row => `
						<div class="vobiz-related-row">
							<div>
								<div class="vobiz-related-title">${frappe.utils.escape_html(row.file_name || row.name || '')}</div>
								<div class="vobiz-related-meta">${frappe.utils.escape_html([row.file_type, row.attached_to_doctype, row.attached_to_name].filter(Boolean).join(' • '))}</div>
							</div>
							${row.file_url ? `<a class="btn btn-xs btn-default" target="_blank" rel="noopener" href="${frappe.utils.escape_html(row.file_url)}">${__('Open')}</a>` : ''}
						</div>
					`).join('') || `<div class="vobiz-empty">${__('No reports/files found.')}</div>`}
				</div>
				<div class="vobiz-workdesk-card">
					<h4>${__('AI / OCR Summary')}</h4>
					${ocr.map(row => this.workdesk_text_row(row.name, [row.status, row.confidence ? `${row.confidence}%` : '', row.pipeline].filter(Boolean).join(' • '), row.raw_text)).join('')}
					${insights.map(row => this.workdesk_text_row(row.name, [row.insight_type, row.confidence ? `${row.confidence}%` : '', row.pipeline].filter(Boolean).join(' • '), row.output_json || row.applied_fields)).join('')}
					${(!ocr.length && !insights.length) ? `<div class="vobiz-empty">${__('No AI report summary found.')}</div>` : ''}
				</div>
			</div>
		`;
	}

	workdesk_text_row(title, meta, text) {
		return `
			<div class="vobiz-audio-card">
				<div class="vobiz-related-title">${frappe.utils.escape_html(title || '')}</div>
				<div class="vobiz-related-meta">${frappe.utils.escape_html(meta || '')}</div>
				${text ? `<div class="vobiz-transcript">${frappe.utils.escape_html(String(text)).slice(0, 1200)}</div>` : ''}
			</div>
		`;
	}

	workdesk_vobiz_html(workdesk, history) {
		const vobiz = workdesk.vobiz || {};
		return `
			<div class="vobiz-workdesk-grid">
				<div class="vobiz-workdesk-card">
					<h4>${__('Vobiz Summary')}</h4>
					<div class="vobiz-field-grid">
						${this.workdesk_field_html(__('Total Calls'), vobiz.total || 0)}
						${this.workdesk_field_html(__('Connected'), vobiz.connected || 0)}
						${this.workdesk_field_html(__('Missed'), vobiz.missed || 0)}
						${this.workdesk_field_html(__('Latest Status'), (vobiz.latest || {}).status || '')}
					</div>
				</div>
				<div class="vobiz-workdesk-card">
					<h4>${__('Transcript / Audio')}</h4>
					${this.detail_transcript_html(history || vobiz.history || [])}
					<hr>
					${this.detail_audio_html(history || vobiz.history || [])}
				</div>
			</div>
		`;
	}

	workdesk_whatsapp_html(workdesk) {
		const wa = workdesk.whatsapp || {};
		const data = wa.data || {};
		const messages = wa.messages || [];
		if (!wa.available) {
			return `<div class="vobiz-workdesk-card"><div class="vobiz-empty">${frappe.utils.escape_html(wa.message || __('WhatsApp is not available.'))}</div></div>`;
		}
		return `
			<div class="vobiz-workdesk-card">
				<h4>${__('WhatsApp')}</h4>
				<div class="vobiz-field-grid">
					${this.workdesk_field_html(__('Conversation'), wa.conversation || __('No existing chat'))}
					${this.workdesk_field_html(__('Status'), data.status || '')}
					${this.workdesk_field_html(__('Unread'), data.unread_count || 0)}
					${this.workdesk_field_html(__('Temperature'), data.lead_temperature || '')}
				</div>
				<hr>
				<div class="vobiz-related-meta">${frappe.utils.escape_html(data.last_message_preview || data.ai_summary || __('WhatsApp chat preview.'))}</div>
				${wa.conversation ? this.workdesk_whatsapp_messages_html(messages, wa) : `<div class="vobiz-empty">${__('No WhatsApp conversation found for this lead.')}</div>`}
				${wa.conversation ? '' : `
					<div style="margin-top:12px;">
						<button class="btn btn-primary btn-sm" data-workdesk-action="whatsapp"><i class="fa fa-whatsapp"></i> ${__('Find Chat')}</button>
					</div>
				`}
			</div>
		`;
	}

	workdesk_whatsapp_messages_html(messages, wa = {}) {
		const first = messages[0] || {};
		const has_more = wa.has_more ? '1' : '0';
		const before = wa.next_before || first.creation || '';
		if (!messages.length) {
			return `
				<div class="vobiz-wa-chat-list" data-wa-chat-list data-conversation="${frappe.utils.escape_html(wa.conversation || '')}" data-before="" data-has-more="0">
					<div class="vobiz-empty">${__('No WhatsApp messages found.')}</div>
				</div>
				${this.workdesk_whatsapp_composer_html()}
			`;
		}
		return `
			<div class="vobiz-wa-chat-list" data-wa-chat-list data-conversation="${frappe.utils.escape_html(wa.conversation || '')}" data-before="${frappe.utils.escape_html(before || '')}" data-has-more="${has_more}">
				${wa.has_more ? `<div class="vobiz-wa-loader" data-wa-loader>${__('Scroll up to load older messages')}</div>` : ''}
				${messages.map((message) => {
					const direction = String(message.direction || '').toLowerCase();
					const side = direction === 'outbound' ? 'outbound' : 'inbound';
					const body = message.body || message.media_url || `[${message.content_type || __('Message')}]`;
					const meta = [
						message.direction || '',
						message.sender_type || '',
						message.creation ? frappe.datetime.str_to_user(message.creation) : ''
					].filter(Boolean).join(' • ');
					return `
						<div class="vobiz-wa-message ${side}">
							<div class="vobiz-wa-message-meta">${frappe.utils.escape_html(meta)}</div>
							<div class="vobiz-wa-message-body">${frappe.utils.escape_html(body)}</div>
						</div>
					`;
				}).join('')}
			</div>
			${this.workdesk_whatsapp_composer_html()}
		`;
	}

	workdesk_whatsapp_message_html(message) {
		const direction = String(message.direction || '').toLowerCase();
		const side = direction === 'outbound' ? 'outbound' : 'inbound';
		const body = message.body || message.media_url || `[${message.content_type || __('Message')}]`;
		const meta = [
			message.direction || '',
			message.sender_type || '',
			message.creation ? frappe.datetime.str_to_user(message.creation) : ''
		].filter(Boolean).join(' / ');
		return `
			<div class="vobiz-wa-message ${side}" data-wa-message="${frappe.utils.escape_html(message.name || '')}">
				<div class="vobiz-wa-message-meta">${frappe.utils.escape_html(meta)}</div>
				<div class="vobiz-wa-message-body">${frappe.utils.escape_html(body)}</div>
			</div>
		`;
	}

	workdesk_whatsapp_composer_html() {
		const emojis = ['😀', '😊', '🙏', '👍', '❤️', '😂', '🎉', '✅', '📞', '💊', '🩺', '💬', '🙌', '😇', '🤝', '⭐'];
		return `
			<div class="vobiz-wa-composer">
				<div class="vobiz-wa-attach-wrap">
					<button class="vobiz-wa-icon-btn" type="button" data-wa-attach title="${__('Attach')}"><i class="fa fa-plus"></i></button>
					<div class="vobiz-wa-menu" data-wa-attach-menu>
						<button type="button" data-wa-attach-action="image"><i class="fa fa-image"></i> ${__('Photo')}</button>
						<button type="button" data-wa-attach-action="document"><i class="fa fa-file-text-o"></i> ${__('Document')}</button>
					</div>
				</div>
				<div class="vobiz-wa-emoji-wrap">
					<button class="vobiz-wa-icon-btn" type="button" data-wa-emoji title="${__('Emoji')}"><i class="fa fa-smile-o"></i></button>
					<div class="vobiz-wa-menu vobiz-wa-emoji-menu" data-wa-emoji-menu>
						${emojis.map((emoji) => `<button type="button" data-wa-emoji-value="${emoji}">${emoji}</button>`).join('')}
					</div>
				</div>
				<textarea class="form-control" data-wa-reply placeholder="${__('Type a message')}"></textarea>
				<button class="vobiz-wa-send" type="button" data-wa-send title="${__('Send')}"><i class="fa fa-paper-plane"></i></button>
			</div>
		`;
	}

	handle_workdesk_action(action, row, context, $body) {
		const workdesk = context.workdesk || {};
		if (action === 'call') {
			return this.handle_workdesk_primary_action(row);
		} else if (action === 'open-lead') {
			this.remember_workdesk_return(row);
			frappe.set_route('Form', row.doctype, row.name);
		} else if (action === 'new-encounter') {
			this.new_doc_with_defaults('Patient Encounter', (workdesk.create_defaults || {})['Patient Encounter'] || {}, row);
		} else if (action === 'new-appointment') {
			this.new_doc_with_defaults('Patient Appointment', (workdesk.create_defaults || {})['Patient Appointment'] || {}, row);
		} else if (action === 'new-invoice') {
			this.new_doc_with_defaults('Sales Invoice', (workdesk.create_defaults || {})['Sales Invoice'] || {}, row);
		} else if (action === 'whatsapp') {
			if ($body && $body.length) {
				$body.find('[data-detail-tab="whatsapp"]').trigger('click');
			}
			setTimeout(() => this.open_whatsapp(row, $body), 60);
		}
	}

	new_doc_with_defaults(doctype, defaults, row) {
		if (row) {
			this.remember_workdesk_return(row);
		}
		const clean = {};
		Object.keys(defaults || {}).forEach(key => {
			if (defaults[key]) clean[key] = defaults[key];
		});
		frappe.new_doc(doctype, clean);
	}

	open_whatsapp(row, $body) {
		frappe.call('vobiz_click_to_call.api.console.get_whatsapp_conversation', {
			reference_doctype: row.doctype,
			reference_name: row.name
		}).then((r) => {
			const message = r.message || {};
			if (message.success && message.conversation) {
				if ($body && $body.length) {
					this.refresh_inline_whatsapp($body, message.conversation);
				}
			} else {
				frappe.msgprint(message.message || __('No WhatsApp conversation found for this lead.'));
			}
		});
	}

	refresh_inline_whatsapp($body, conversation) {
		frappe.call('vobiz_click_to_call.api.console.get_whatsapp_messages', {
			conversation,
			limit: 10
		}).then((r) => {
			const page = r.message || {};
			const wa = {
				conversation,
				has_more: page.has_more,
				next_before: page.next_before
			};
			const html = this.workdesk_whatsapp_messages_html(page.messages || [], wa);
			const $chat = $(html).filter('[data-wa-chat-list]');
			const $existing = $body.find('[data-wa-chat-list]').first();
			if ($existing.length) {
				$existing.replaceWith($chat);
			} else {
				$body.find('.vobiz-empty').last().replaceWith($chat);
			}
			if (!$body.find('[data-wa-reply]').length) {
				$body.find('.vobiz-workdesk-card').append(this.workdesk_whatsapp_composer_html());
			}
			this.scroll_whatsapp_to_bottom($body);
		});
	}

	load_more_whatsapp_messages($list) {
		if (!$list || !$list.length || $list.attr('data-loading') === '1' || $list.attr('data-has-more') !== '1') return;

		const conversation = $list.attr('data-conversation');
		const before = $list.attr('data-before');
		if (!conversation || !before) return;

		const el = $list.get(0);
		const old_height = el.scrollHeight;
		const old_top = el.scrollTop;
		$list.attr('data-loading', '1');
		$list.find('[data-wa-loader]').text(__('Loading older messages...'));
		frappe.call('vobiz_click_to_call.api.console.get_whatsapp_messages', {
			conversation,
			limit: 10,
			before
		}).then((r) => {
			const page = r.message || {};
			const messages = page.messages || [];
			$list.find('[data-wa-loader]').remove();
			if (messages.length) {
				$list.prepend(messages.map((message) => this.workdesk_whatsapp_message_html(message)).join(''));
			}
			if (page.has_more) {
				$list.prepend(`<div class="vobiz-wa-loader" data-wa-loader>${__('Scroll up to load older messages')}</div>`);
			}
			$list.attr('data-before', page.next_before || before);
			$list.attr('data-has-more', page.has_more ? '1' : '0');
			el.scrollTop = el.scrollHeight - old_height + old_top;
		}).always(() => {
			$list.attr('data-loading', '0');
		});
	}

	send_workdesk_whatsapp($body) {
		const $list = $body.find('[data-wa-chat-list]').first();
		const conversation = $list.attr('data-conversation');
		const $input = $body.find('[data-wa-reply]').first();
		const body = ($input.val() || '').trim();
		if (!conversation || !body) return;

		const $button = $body.find('[data-wa-send]').first();
		$button.prop('disabled', true);
		frappe.call({
			method: 'wa_chat_hub.api.runtime.send_reply',
			args: { conversation, body },
			type: 'POST'
		}).then((r) => {
			$input.val('');
			this.refresh_inline_whatsapp($body, conversation);
		}).always(() => {
			$button.prop('disabled', false);
		});
	}

	insert_workdesk_emoji($body, emoji) {
		const input = $body.find('[data-wa-reply]').get(0);
		if (!input || !emoji) return;
		const start = input.selectionStart || 0;
		const end = input.selectionEnd || 0;
		const value = input.value || '';
		input.value = `${value.slice(0, start)}${emoji}${value.slice(end)}`;
		input.focus();
		const next = start + String(emoji).length;
		input.setSelectionRange(next, next);
	}

	open_workdesk_attachment_dialog($body, kind) {
		const conversation = $body.find('[data-wa-chat-list]').first().attr('data-conversation');
		if (!conversation) {
			frappe.show_alert({ message: __('No WhatsApp conversation selected.'), indicator: 'orange' });
			return;
		}

		const is_image = kind === 'image';
		const input_id = is_image ? 'vobiz-wa-image-upload' : 'vobiz-wa-document-upload';
		const accept = is_image
			? 'image/*'
			: '.pdf,.txt,.doc,.docx,.xls,.xlsx,.ppt,.pptx,application/pdf,text/plain,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation';
		const dialog = new frappe.ui.Dialog({
			title: is_image ? __('Send Photo') : __('Send Document'),
			fields: [
				{
					fieldname: 'file_upload',
					fieldtype: 'HTML',
					options: `<input type="file" class="form-control" id="${input_id}" accept="${accept}" />`
				},
				{
					fieldname: 'caption',
					fieldtype: 'Small Text',
					label: is_image ? __('Caption') : __('File Name / Caption')
				}
			],
			primary_action_label: __('Upload & Send'),
			primary_action: (values) => {
				const file_input = dialog.$wrapper.find(`#${input_id}`).get(0);
				const file = file_input && file_input.files && file_input.files[0];
				if (!file) {
					frappe.show_alert({ message: __('Choose a file first.'), indicator: 'orange' });
					return;
				}
				if (is_image && (!file.type || !file.type.startsWith('image/'))) {
					frappe.show_alert({ message: __('Only image files are supported here.'), indicator: 'orange' });
					return;
				}

				dialog.get_primary_btn().prop('disabled', true).text(__('Uploading...'));
				this.upload_workdesk_whatsapp_file(conversation, file, is_image)
					.then((upload) => this.send_workdesk_whatsapp_media($body, conversation, {
						body: values.caption || '',
						content_type: is_image ? 'Image' : 'Document',
						media_url: upload.file_url || upload.media_url,
						display_media_url: upload.file_url || upload.media_url,
						file_name: upload.file_name || file.name,
						file_size: upload.file_size || ''
					}))
					.then(() => dialog.hide())
					.catch((err) => {
						frappe.msgprint({
							title: __('Send failed'),
							message: (err && err.message) || __('Could not upload and send file.'),
							indicator: 'red'
						});
					})
					.finally(() => {
						dialog.get_primary_btn().prop('disabled', false).text(__('Upload & Send'));
					});
			}
		});
		dialog.show();
	}

	upload_workdesk_whatsapp_file(conversation, file, is_image) {
		const formData = new FormData();
		formData.append('conversation', conversation);
		formData.append('file', file);
		const method = is_image
			? 'wa_chat_hub.api.runtime.upload_image_for_send'
			: 'wa_chat_hub.api.runtime.upload_document_for_send';

		return fetch(`/api/method/${method}`, {
			method: 'POST',
			headers: { 'X-Frappe-CSRF-Token': frappe.csrf_token },
			body: formData
		}).then((response) => response.json()).then((data) => {
			if (data.exc || (data.message && data.message.success === false)) {
				throw new Error((data.message && data.message.message) || data._server_messages || __('Upload failed.'));
			}
			return (data.message || {}).result || {};
		});
	}

	send_workdesk_whatsapp_media($body, conversation, payload) {
		return frappe.call({
			method: 'wa_chat_hub.api.runtime.send_reply',
			args: { conversation, ...payload },
			type: 'POST'
		}).then(() => {
			this.refresh_inline_whatsapp($body, conversation);
		});
	}

	scroll_whatsapp_to_bottom($body) {
		const el = $body.find('[data-wa-chat-list]').get(0);
		if (el) el.scrollTop = el.scrollHeight;
	}

	workdesk_return_key() {
		return 'vobiz_agent_console:last_workdesk';
	}

	remember_workdesk_return(row) {
		if (!row || !row.doctype || !row.name) return;
		this.state.navigating_from_workdesk = true;
		try {
			localStorage.setItem(this.workdesk_return_key(), JSON.stringify({
				doctype: row.doctype,
				name: row.name
			}));
		} catch (e) {
			// localStorage may be unavailable in private contexts.
		}
	}

	clear_workdesk_return_state() {
		try {
			localStorage.removeItem(this.workdesk_return_key());
		} catch (e) {
			// ignore storage failures
		}
	}

	restore_workdesk_dialog() {
		if (this.state.restore_checked || this.state.restore_in_flight) return;
		this.state.restore_checked = true;

		let saved = null;
		try {
			saved = JSON.parse(localStorage.getItem(this.workdesk_return_key()) || 'null');
		} catch (e) {
			saved = null;
		}
		if (!saved || !saved.doctype || !saved.name) return;
		const saved_key = `${saved.doctype}::${saved.name}`;
		if (this.state.active_workdesk_key === saved_key) return;

		this.state.restore_in_flight = true;
		frappe.call('vobiz_click_to_call.api.console.get_reference_context', {
			reference_doctype: saved.doctype,
			reference_name: saved.name
		}).then((r) => {
			const context = r.message || {};
			const row = this.state.queue.find((item) => item.doctype === saved.doctype && item.name === saved.name)
				|| context.reference
				|| { doctype: saved.doctype, name: saved.name, title: saved.name };
			this.state.selected = row;
			this.open_detail_dialog(row, context);
		}).catch(() => {
			this.clear_workdesk_return_state();
		}).always(() => {
			this.state.restore_in_flight = false;
		});
	}

	detail_summary_html(row, context) {
		const latest = (context.history || [])[0] || {};
		const guidance = (context.guidance || {}).script || [];
		return `
			<div class="vobiz-detail-head">
				<div>
					<h3>${frappe.utils.escape_html(row.title || row.name || '')}</h3>
					<div class="text-muted">${frappe.utils.escape_html(row.doctype || '')} • ${frappe.utils.escape_html(row.phone || '')}</div>
				</div>
			</div>
			<div class="vobiz-info-list">
				${this.info_row('fa-phone', __('Caller'), row.phone || __('No phone'))}
				${this.info_row('fa-user', __('Agent'), latest.user || frappe.session.user)}
				${this.info_row('fa-calendar', __('Date'), latest.creation ? frappe.datetime.str_to_user(latest.creation) : __('No previous call'))}
				${this.info_row('fa-clock-o', __('Duration'), latest.duration_label || '00:00')}
			</div>
			<hr>
			<ul class="vobiz-guidance-list">${guidance.map(line => `<li>${frappe.utils.escape_html(line)}</li>`).join('')}</ul>
		`;
	}

	detail_transcript_html(history) {
		const rows = history.filter(row => row.transcript_text || row.transcript_status || row.ai_summary);
		return rows.map(row => `
			<div class="vobiz-audio-card">
				<div><strong>${frappe.utils.escape_html(row.name)}</strong></div>
				<div class="text-muted">${frappe.datetime.str_to_user(row.creation)} • ${frappe.utils.escape_html(row.transcript_status || row.status || '')}</div>
				${row.ai_summary ? `<div><strong>${__('Summary')}</strong><div>${frappe.utils.escape_html(row.ai_summary)}</div></div>` : ''}
				${row.transcript_text ? `<div class="vobiz-transcript">${frappe.utils.escape_html(row.transcript_text)}</div>` : `<div class="text-muted">${frappe.utils.escape_html(row.transcript_error || row.transcript_status || __('No transcript yet'))}</div>`}
			</div>
		`).join('') || `<div class="text-muted">${__('No transcript available for this lead.')}</div>`;
	}

	detail_audio_html(history) {
		const rows = history.filter(row => row.recording_url || row.recording_status);
		return `
			<div class="vobiz-audio-list">
				${rows.map(row => `
					<div class="vobiz-audio-card">
						<div><strong>${frappe.utils.escape_html(row.name)}</strong></div>
						<div class="text-muted">${frappe.datetime.str_to_user(row.creation)} • ${frappe.utils.escape_html(row.recording_status || row.status || '')} • ${frappe.utils.escape_html(row.duration_label || '')}</div>
						${row.recording_download_url ? `<audio controls src="${frappe.utils.escape_html(row.recording_download_url)}" style="width:100%; margin-top:8px;"></audio>` : `<div class="text-muted">${__('No audio file yet')}</div>`}
					</div>
				`).join('') || `<div class="text-muted">${__('No recording available for this lead.')}</div>`}
			</div>
		`;
	}

	call_selected() {
		const row = this.state.selected;
		if (!row) {
			frappe.msgprint(__('Select a lead first.'));
			return;
		}
		this.start_call_for_row(row);
	}

	start_call_for_row(row) {
		if (!row) return Promise.resolve();
		return frappe.call({
			method: 'vobiz_click_to_call.api.call.start_call',
			args: {
				reference_doctype: row.doctype,
				reference_name: row.name,
				phone_field: row.phone_field,
				phone_number: row.phone
			},
			freeze: true,
			freeze_message: __('Starting call...')
		}).then((r) => {
			const message = r.message || {};
			if (message.call_log) {
				this.state.disposition_prompted_call_log = null;
				this.state.workdesk_live_call_log = message.call_log;
				this.state.workdesk_live_call = {
					name: message.call_log,
					status: message.status || __('Queued'),
					call_flow: message.call_flow || '',
					reference_doctype: row.doctype,
					reference_name: row.name,
					customer_number_display: row.phone || message.customer_number || '',
					agent_mobile_display: message.agent_mobile_display || ''
				};
				this.render_workdesk_live_call();
				this.update_workdesk_primary_action(row);
				this.refresh_workdesk_live_call();
			}
			frappe.show_alert({ message: __('Call started: {0}', [message.call_log || 'Vobiz']), indicator: 'green' });
			this.load();
			return message;
		});
	}

	toggle_auto_dial() {
		if ((this.state.auto_dial || {}).running) {
			this.stop_auto_dial();
			return;
		}
		this.start_auto_dial();
	}

	start_auto_dial() {
		const rows = this.page.main.find('[data-role="row-check"]:checked').map((_, el) => {
			const index = $(el).closest('tr').data('index');
			return this.state.queue[index];
		}).get().filter(Boolean);
		if (!rows.length) {
			frappe.msgprint(__('Select at least one lead to start auto dial.'));
			return;
		}
		if ((this.state.active_call || {}).name) {
			frappe.msgprint(__('Finish the active call before starting auto dial.'));
			return;
		}

		this.state.auto_dial = {
			running: true,
			in_flight: false,
			queue: rows,
			cursor: 0,
			results: [],
			events: [],
			current: null,
			started_at: frappe.datetime.now_datetime(),
			stopped_at: null
		};
		this.add_auto_event(__('Auto dial started'), __('{0} leads queued.', [rows.length]), 'active');
		this.update_selected_count();
		this.render_auto_toggle();
		this.run_next_auto_dial();
	}

	stop_auto_dial() {
		const session = this.state.auto_dial || {};
		session.running = false;
		session.in_flight = false;
		session.stopped_at = frappe.datetime.now_datetime();
		this.state.auto_dial = session;
		this.add_auto_event(__('Auto dial stopped'), __('Active call was not ended.'), 'failed');
		this.update_selected_count();
		this.render_auto_toggle();
		frappe.show_alert({ message: __('Auto dial stopped. Active call was not ended.'), indicator: 'orange' });
	}

	maybe_continue_auto_dial() {
		const session = this.state.auto_dial || {};
		if (!session.running || session.in_flight) return;
		if (session.current && session.current.call_log) return;
		if ((this.state.active_call || {}).name) return;
		if (session.cursor >= session.queue.length) {
			session.running = false;
			session.stopped_at = session.stopped_at || frappe.datetime.now_datetime();
			this.state.auto_dial = session;
			this.add_auto_event(__('Auto dial completed'), __('All selected leads have been processed.'), 'done');
			this.update_selected_count();
			this.render_auto_toggle();
			return;
		}
		this.run_next_auto_dial();
	}

	run_next_auto_dial() {
		const session = this.state.auto_dial || {};
		if (!session.running || session.in_flight) return;
		const row = session.queue[session.cursor];
		if (!row) {
			this.maybe_continue_auto_dial();
			return;
		}

		session.cursor += 1;
		session.in_flight = true;
		session.current = {
			lead: row.name,
			title: row.title || row.name,
			phone: row.phone || '',
			status: __('Starting'),
			started_at: frappe.datetime.now_datetime()
		};
		this.state.auto_dial = session;
		this.add_auto_event(__('Starting call'), `${row.name} • ${row.phone || __('No phone')}`, 'active');
		this.update_selected_count();

		this.start_call_for_row(row).then((message) => {
			session.current = {
				lead: row.name,
				title: row.title || row.name,
				phone: row.phone || '',
				call_log: message.call_log || '',
				status: message.status || __('Started'),
				started_at: frappe.datetime.now_datetime()
			};
			this.add_auto_event(__('Call request sent'), `${row.name} • ${message.call_log || __('No call log')}`, 'active');
			this.state.auto_dial = session;
			this.refresh_auto_dial_current(true);
		}).catch((error) => {
			session.results.push({
				lead: row.name,
				title: row.title || row.name,
				phone: row.phone || '',
				call_log: '',
				status: __('Failed'),
				error: (error && error.message) || '',
				time: frappe.datetime.now_datetime()
			});
			session.in_flight = false;
			session.current = null;
			this.state.auto_dial = session;
			this.add_auto_event(__('Call failed to start'), `${row.name} • ${(error && error.message) || ''}`, 'failed');
			this.maybe_continue_auto_dial();
		});
	}

	refresh_auto_dial_current(force) {
		const session = this.state.auto_dial || {};
		const current = session.current || {};
		if (!current.call_log || session.polling_current) return;
		const active = this.state.active_call || {};
		const terminal = ['Completed', 'Failed', 'Busy', 'No Answer', 'Cancelled', 'Canceled'];

		if (!force && active.name === current.call_log && !terminal.includes(active.status)) {
			current.status = active.status || current.status;
			session.current = current;
			this.state.auto_dial = session;
			this.render_auto_live();
			return;
		}

		session.polling_current = true;
		this.state.auto_dial = session;
		frappe.call({
			method: 'vobiz_click_to_call.api.call.get_call_status',
			args: { call_log: current.call_log }
		}).then((r) => {
			const call = r.message || {};
			const latest = this.state.auto_dial || {};
			const latestCurrent = latest.current || {};
			if (!call.name || latestCurrent.call_log !== call.name) return;

			latestCurrent.status = call.status || latestCurrent.status;
			latestCurrent.call = call;
			latest.current = latestCurrent;
			latest.polling_current = false;
			this.state.auto_dial = latest;

			if (terminal.includes(call.status)) {
				this.finish_auto_dial_call(call);
			} else {
				this.add_auto_event(__('Call update'), `${latestCurrent.lead} • ${call.status || __('Active')}`, 'active');
			}
		}).catch(() => {
			const latest = this.state.auto_dial || {};
			latest.polling_current = false;
			this.state.auto_dial = latest;
		}).always(() => {
			const latest = this.state.auto_dial || {};
			latest.polling_current = false;
			this.state.auto_dial = latest;
		});
	}

	finish_auto_dial_call(call) {
		const session = this.state.auto_dial || {};
		const current = session.current || {};
		if (!current.call_log || current.call_log !== call.name) return;

		const outcome = this.auto_call_outcome(call);
		session.results.push({
			lead: current.lead,
			title: current.title,
			phone: current.phone,
			call_log: call.name,
			status: outcome.label,
			duration: this.call_duration_label(call),
			time: frappe.datetime.now_datetime()
		});
		session.current = null;
		session.in_flight = false;
		this.state.auto_dial = session;
		this.state.active_call = { last_call: call };
		this.state.call_started_at = null;
		this.clear_tracked_live_call(call.name);
		this.stop_timer();
		this.add_auto_event(__('Next action'), `${current.lead} • ${outcome.label}. ${__('Moving to next lead.')}`, outcome.state);
		this.update_selected_count();
		this.render_auto_live();
		setTimeout(() => this.maybe_continue_auto_dial(), 900);
	}

	auto_call_outcome(call) {
		if (['Completed', 'Connected'].includes(call.status)) {
			return { label: __('Completed'), state: 'done' };
		}

		const flow = call.call_flow || 'Customer First';
		const first = flow === 'Agent First' ? __('Agent') : __('Customer');
		const second = flow === 'Agent First' ? __('Customer') : __('Agent');
		const answeredFirst = Boolean(call.answer_time) || ['Agent Answered', 'Customer Answered', 'Agent Ringing'].includes(call.status);
		const party = answeredFirst ? second : first;
		const signal = this.normalized_call_signal(call);
		let text = __('{0} Call Failed', [party]);
		if (call.status === 'Busy' || signal.includes('busy')) {
			text = __('{0} Busy', [party]);
		} else if (
			call.status === 'Cancelled' ||
			call.status === 'Canceled' ||
			signal.includes('cancel') ||
			signal.includes('reject') ||
			signal.includes('decline') ||
			signal.includes('hangup')
		) {
			text = __('{0} Busy / Cut Call', [party]);
		} else if (call.status === 'No Answer' || signal.includes('no-answer') || signal.includes('timeout') || signal.includes('unanswered')) {
			text = __('{0} Not Responding', [party]);
		}
		return { label: text, state: 'failed' };
	}

	call_duration_label(call) {
		const seconds = parseInt(call.billsec || call.duration || 0, 10) || 0;
		if (!seconds) return '0s';
		const minutes = Math.floor(seconds / 60);
		const rest = seconds % 60;
		return minutes ? `${minutes}m ${rest}s` : `${rest}s`;
	}

	is_terminal_status(status) {
		return ['Completed', 'Failed', 'Busy', 'No Answer', 'Cancelled', 'Canceled'].includes(status || '');
	}

	clear_tracked_live_call(callLog) {
		if (!callLog || this.state.workdesk_live_call_log !== callLog) return;
		this.state.workdesk_live_call_log = null;
		this.state.workdesk_live_call = null;
	}

	open_auto_dial_report() {
		const session = this.state.auto_dial || {};
		const total = (session.queue || []).length;
		const completed = (session.results || []).length;
		const remaining = Math.max(0, total - completed);
		const dialog = new frappe.ui.Dialog({
			title: __('Auto Dial Report'),
			size: 'large',
			fields: [{ fieldname: 'report', fieldtype: 'HTML' }]
		});
		dialog.show();
		dialog.get_field('report').$wrapper.html(`
			<div class="vobiz-auto-report">
				<div class="vobiz-stats" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
					<div class="vobiz-stat"><span>${__('Selected')}</span><strong>${total}</strong></div>
					<div class="vobiz-stat"><span>${__('Started')}</span><strong>${completed}</strong></div>
					<div class="vobiz-stat"><span>${__('Remaining')}</span><strong>${remaining}</strong></div>
					<div class="vobiz-stat"><span>${__('Status')}</span><strong>${session.running ? __('Running') : __('Stopped')}</strong></div>
				</div>
				<div class="vobiz-table-wrap">
					<table class="table table-sm vobiz-table">
						<thead>
							<tr>
								<th>${__('CRM Lead ID')}</th>
								<th>${__('Name')}</th>
								<th>${__('Phone')}</th>
								<th>${__('Call Log')}</th>
								<th>${__('Status')}</th>
								<th>${__('Talk Time')}</th>
								<th>${__('Time')}</th>
							</tr>
						</thead>
						<tbody>
							${(session.results || []).map(row => `
								<tr>
									<td><code>${frappe.utils.escape_html(row.lead || '')}</code></td>
									<td>${frappe.utils.escape_html(row.title || '')}</td>
									<td>${frappe.utils.escape_html(row.phone || '')}</td>
									<td>${row.call_log ? `<a href="/app/vobiz-call-log/${frappe.utils.escape_html(row.call_log)}">${frappe.utils.escape_html(row.call_log)}</a>` : ''}</td>
									<td>${frappe.utils.escape_html(row.status || '')}</td>
									<td>${frappe.utils.escape_html(row.duration || '0s')}</td>
									<td>${frappe.utils.escape_html(row.time || '')}</td>
								</tr>
							`).join('') || `<tr><td colspan="7" class="text-muted text-center">${__('No auto dial calls started yet.')}</td></tr>`}
						</tbody>
					</table>
				</div>
			</div>
		`);
	}

	open_performance_dialog(metric) {
		metric = metric || 'total';
		const label = this.performance_metric_label(metric);
		frappe.call('vobiz_click_to_call.api.console.get_call_performance', { status_filter: metric }).then((r) => {
			const data = r.message || {};
			const summary = data.summary || {};
			const overall = data.overall || summary;
			const dialog = new frappe.ui.Dialog({
				title: data.is_admin ? __('Team Call Performance - {0}', [label]) : __('My Call Performance - {0}', [label]),
				size: 'extra-large',
				fields: [{ fieldname: 'performance', fieldtype: 'HTML' }]
			});
			dialog.show();
			dialog.get_field('performance').$wrapper.html(`
				<div class="vobiz-performance">
					<div class="vobiz-performance-head">
						<div>
							<div class="text-muted">${__('Today')}</div>
							<h4>${frappe.utils.escape_html(label)} ${__('Calls')}</h4>
						</div>
						<div class="text-muted">${__('Overall today')}: ${overall.connected || 0} ${__('connected')} / ${overall.missed || 0} ${__('missed')} / ${overall.total || 0} ${__('total')}</div>
					</div>
					<div class="vobiz-perf-kpis">
						<div class="vobiz-perf-kpi"><span>${__('Showing')}</span><strong>${summary.total || 0}</strong></div>
						<div class="vobiz-perf-kpi"><span>${__('Connected')}</span><strong>${summary.connected || 0}</strong></div>
						<div class="vobiz-perf-kpi"><span>${__('Missed')}</span><strong>${summary.missed || 0}</strong></div>
						<div class="vobiz-perf-kpi"><span>${__('Avg Duration')}</span><strong>${frappe.utils.escape_html(summary.average_duration_label || '0s')}</strong></div>
					</div>
					${data.is_admin ? this.performance_agents_html(data.agents || [], metric) : ''}
					${this.performance_calls_html(data.calls || [], metric)}
				</div>
			`);
		});
	}

	performance_metric_label(metric) {
		if (metric === 'connected') return __('Connected');
		if (metric === 'missed') return __('Missed');
		return __('All');
	}

	performance_agents_html(agents, metric) {
		return `
			<div class="vobiz-performance-section">
				<h4>${__('Agent Performance')} <span class="text-muted">(${frappe.utils.escape_html(this.performance_metric_label(metric))})</span></h4>
				<div class="vobiz-table-wrap">
				<table class="table table-sm vobiz-performance-table">
					<thead>
						<tr>
							<th>${__('User')}</th>
							<th>${__('Connected')}</th>
							<th>${__('Missed')}</th>
							<th>${__('Avg Duration')}</th>
							<th>${__('Total Calls')}</th>
						</tr>
					</thead>
					<tbody>
						${agents.map(row => `
							<tr>
								<td>${frappe.utils.escape_html(row.user || '')}</td>
								<td>${row.connected || 0}</td>
								<td>${row.missed || 0}</td>
								<td>${frappe.utils.escape_html(row.average_duration_label || '0s')}</td>
								<td>${row.total || 0}</td>
							</tr>
						`).join('') || `<tr><td colspan="5" class="text-muted text-center">${__('No agent calls found today.')}</td></tr>`}
					</tbody>
				</table>
				</div>
			</div>
		`;
	}

	performance_calls_html(calls, metric) {
		return `
			<div class="vobiz-performance-section">
			<h4>${__('Call Data')} <span class="text-muted">(${frappe.utils.escape_html(this.performance_metric_label(metric))})</span></h4>
			<div class="vobiz-table-wrap">
				<table class="table table-sm vobiz-performance-table">
					<thead>
						<tr>
							<th>${__('Call Log')}</th>
							<th>${__('User')}</th>
							<th>${__('Lead')}</th>
							<th>${__('Status')}</th>
							<th>${__('Duration')}</th>
							<th>${__('Disposition')}</th>
							<th>${__('Time')}</th>
						</tr>
					</thead>
					<tbody>
						${calls.map(row => `
							<tr>
								<td><a href="/app/vobiz-call-log/${frappe.utils.escape_html(row.name || '')}"><code>${frappe.utils.escape_html(row.name || '')}</code></a></td>
								<td>${frappe.utils.escape_html(row.user || '')}</td>
								<td>${row.reference_name ? `<a href="/app/${frappe.router.slug(row.reference_doctype || 'CRM Lead')}/${frappe.utils.escape_html(row.reference_name)}"><code>${frappe.utils.escape_html(row.reference_name)}</code></a>` : ''}</td>
								<td>${frappe.utils.escape_html(row.status || '')}</td>
								<td>${frappe.utils.escape_html(row.duration_label || '0s')}</td>
								<td>${frappe.utils.escape_html(row.disposition || '')}</td>
								<td>${row.creation ? frappe.datetime.str_to_user(row.creation) : ''}</td>
							</tr>
						`).join('') || `<tr><td colspan="7" class="text-muted text-center">${__('No calls found today.')}</td></tr>`}
					</tbody>
				</table>
			</div>
			</div>
		`;
	}

	cancel_call() {
		const active = this.state.active_call || {};
		if (!active.name) return;
		this.cancel_call_log(active.name);
	}

	cancel_call_log(call_log, row) {
		if (!call_log) return Promise.resolve();
		return frappe.call('vobiz_click_to_call.api.call.cancel_call', { call_log }).then(() => {
			return frappe.call({
				method: 'vobiz_click_to_call.api.call.get_call_status',
				args: { call_log }
			});
		}).then((r) => {
			const call = r.message || { name: call_log, status: 'Cancelled' };
			if (this.state.workdesk_live_call_log === call_log) {
				this.clear_tracked_live_call(call_log);
			}
			this.state.active_call = { last_call: call };
			this.state.workdesk_live_call = null;
			this.render_workdesk_live_call();
			this.update_workdesk_primary_action(row || this.state.active_workdesk_row);
			this.maybe_prompt_workdesk_disposition(call);
			frappe.show_alert({ message: __('Call stopped.'), indicator: 'orange' });
			this.load();
		});
	}

	maybe_prompt_workdesk_disposition(call) {
		if (!call || !call.name || !this.is_terminal_status(call.status)) return;
		if (this.state.disposition_prompted_call_log === call.name) return;

		const row = this.state.active_workdesk_row || this.state.selected || {};
		const matchesWorkdesk = row.doctype && row.name &&
			call.reference_doctype === row.doctype &&
			call.reference_name === row.name;
		if (!matchesWorkdesk) return;

		this.state.disposition_prompted_call_log = call.name;
		setTimeout(() => this.open_post_call_disposition_dialog(call, row), 150);
	}

	open_post_call_disposition_dialog(call, row) {
		if (call.disposition) {
			frappe.msgprint({
				title: __('Call Disposed'),
				indicator: 'green',
				message: `
					<div><strong>${__('Disposition')}</strong>: ${frappe.utils.escape_html(call.disposition)}</div>
					${call.ai_disposition ? `<div><strong>${__('AI Suggestion')}</strong>: ${frappe.utils.escape_html(call.ai_disposition)}${call.ai_confidence ? ` (${frappe.utils.escape_html(String(call.ai_confidence))})` : ''}</div>` : ''}
					${call.ai_summary ? `<hr><div>${frappe.utils.escape_html(call.ai_summary)}</div>` : ''}
					${call.disposition_notes ? `<hr><div>${frappe.utils.escape_html(call.disposition_notes)}</div>` : ''}
				`
			});
			return;
		}

		const leadContext = this.state.lead_disposition_context || {};
		const statusOptions = leadContext.status_options || [];
		const currentStatus = leadContext.status || '';
		const options = this.state.dispositions || [];
		const suggested = call.ai_disposition && options.includes(call.ai_disposition) ? call.ai_disposition : '';
		const notes = [call.ai_summary, call.ai_next_action].filter(Boolean).join('\n\n');
		const dialog = new frappe.ui.Dialog({
			title: __('Complete Call Disposition'),
			fields: [
				{
					fieldname: 'call_info',
					fieldtype: 'HTML',
					options: `
						<div class="vobiz-workdesk-card">
							<div><strong>${frappe.utils.escape_html(row.title || row.name || call.reference_name || '')}</strong></div>
							<div class="text-muted">${frappe.utils.escape_html(call.status || '')}</div>
							${call.ai_disposition ? `<hr><div><strong>${__('AI Suggestion')}</strong>: ${frappe.utils.escape_html(call.ai_disposition)}${call.ai_confidence ? ` (${frappe.utils.escape_html(String(call.ai_confidence))})` : ''}</div>` : ''}
							${call.ai_summary ? `<div class="vobiz-related-meta">${frappe.utils.escape_html(call.ai_summary)}</div>` : ''}
						</div>
					`
				},
				{
					fieldname: 'lead_status',
					fieldtype: 'Select',
					label: __('CRM Status'),
					options: [''].concat(statusOptions).join('\n'),
					reqd: Boolean(statusOptions.length),
					default: currentStatus
				},
				{
					fieldname: 'disposition',
					fieldtype: 'Select',
					label: __('SR Lead Disposition'),
					options: [''].concat(options).join('\n'),
					reqd: 1,
					default: suggested
				},
				{
					fieldname: 'notes',
					fieldtype: 'Small Text',
					label: __('Notes'),
					reqd: 1,
					default: notes
				}
			],
			primary_action_label: __('Save Disposition'),
			primary_action: (values) => {
				if ((statusOptions.length && !values.lead_status) || !values.disposition || !values.notes) {
					frappe.msgprint(__('Select CRM status, SR lead disposition, and add notes.'));
					return;
				}
				dialog.get_primary_btn().prop('disabled', true).text(__('Saving...'));
				frappe.call('vobiz_click_to_call.api.disposition.save_disposition', {
					call_log: call.name,
					lead_status: values.lead_status,
					disposition: values.disposition,
					notes: values.notes
				}).then(() => {
					frappe.show_alert({ message: __('Disposition saved'), indicator: 'green' });
					dialog.hide();
					this.load();
				}).always(() => {
					dialog.get_primary_btn().prop('disabled', false).text(__('Save Disposition'));
				});
			}
		});
		dialog.show();
		if (statusOptions.length) {
			dialog.fields_dict.lead_status.$input.on('change', () => {
				const leadStatus = dialog.get_value('lead_status');
				frappe.call({
					method: 'vobiz_click_to_call.api.disposition.get_lead_disposition_context_api',
					args: {
						reference_doctype: row.doctype || call.reference_doctype,
						reference_name: row.name || call.reference_name,
						lead_status: leadStatus
					}
				}).then((r) => {
					const context = r.message || {};
					const refreshedOptions = (context.options || []).map(item => item.name).filter(Boolean);
					const refreshedSuggestion = call.ai_disposition && refreshedOptions.includes(call.ai_disposition) ? call.ai_disposition : '';
					this.state.lead_disposition_context = context;
					this.state.dispositions = refreshedOptions;
					dialog.set_df_property('disposition', 'options', [''].concat(refreshedOptions).join('\n'));
					dialog.set_value('disposition', refreshedSuggestion);
					this.render_dispositions();
				});
			});
		}
	}

	save_disposition() {
		const active = this.state.active_call || {};
		const leadStatus = this.page.main.find('[data-role="lead-status"]').val();
		const statusOptions = ((this.state.lead_disposition_context || {}).status_options || []);
		const disposition = this.page.main.find('[data-role="disposition"]').val();
		const notes = this.page.main.find('[data-role="notes"]').val();
		if (!active.name) {
			frappe.msgprint(__('No active call selected.'));
			return;
		}
		if ((statusOptions.length && !leadStatus) || !disposition || !notes) {
			frappe.msgprint(__('Select CRM status, SR lead disposition, and add notes.'));
			return;
		}
		frappe.call('vobiz_click_to_call.api.disposition.save_disposition', {
			call_log: active.name,
			lead_status: leadStatus,
			disposition,
			notes
		}).then(() => {
			frappe.show_alert({ message: __('Disposition saved'), indicator: 'green' });
			this.page.main.find('[data-role="notes"]').val('');
			this.load();
		});
	}

	open_reference() {
		const active = this.state.active_call || {};
		const row = this.state.selected || {};
		const doctype = active.reference_doctype || row.doctype;
		const name = active.reference_name || row.name;
		if (doctype && name) {
			frappe.set_route('Form', doctype, name);
		}
	}
}
