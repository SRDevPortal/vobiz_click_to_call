frappe.pages['vobiz-agent-analytics'].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: '',
		single_column: true
	});
	$(wrapper).find('.page-head').hide();
	$(wrapper).find('.page-body').css('padding-top', '12px');
	wrapper.vobiz_agent_analytics = new VobizAgentAnalytics(page);
};

frappe.pages['vobiz-agent-analytics'].on_page_show = function(wrapper) {
	if (wrapper.vobiz_agent_analytics) {
		wrapper.vobiz_agent_analytics.on_page_show();
	}
};

class VobizAgentAnalytics {
	constructor(page) {
		this.page = page;
		this.charts = {};
		this.initialized = false;
		this.loading = false;
		this.calls_loading = false;
		this.load_timer = null;
		this.request_id = 0;
		this.calls_request_id = 0;
		this.calls_loaded = false;
		this.calls_offset = 0;
		this.calls_limit = 50;
		this.has_more_calls = false;
		this.state = {
			from_date: frappe.datetime.get_today(),
			to_date: frappe.datetime.get_today(),
			status_filter: 'total',
			queue_source: 'CRM Lead',
			agent_user: '',
			team: '',
			department: ''
		};
		this.render();
		this.bind();
		this.load();
	}

	render() {
		this.page.main.html(`
			<div class="vobiz-analytics-page">
				<div class="vobiz-analytics-head">
					<div>
						<div class="vobiz-eyebrow">${__('APP / VOBIZ CALL CENTER / ANALYTICS')}</div>
						<h2>${__('Vobiz Call Analytics')}</h2>
					</div>
					<div class="vobiz-head-actions">
						<button class="btn btn-default btn-sm" data-action="open-console">
							<i class="fa fa-phone"></i> ${__('Agent Console')}
						</button>
						<button class="btn btn-primary btn-sm" data-action="refresh">
							<i class="fa fa-refresh"></i> ${__('Refresh')}
						</button>
					</div>
				</div>

				<section class="vobiz-band">
					<div class="vobiz-filter-grid">
						<div>
							<label>${__('From Date')}</label>
							<input type="date" class="form-control input-sm" data-role="from-date" value="${frappe.datetime.get_today()}">
						</div>
						<div>
							<label>${__('To Date')}</label>
							<input type="date" class="form-control input-sm" data-role="to-date" value="${frappe.datetime.get_today()}">
						</div>
						<div>
							<label>${__('Queue Source')}</label>
							<select class="form-control input-sm" data-role="queue-source"></select>
						</div>
						<div>
							<label>${__('Status')}</label>
							<select class="form-control input-sm" data-role="status-filter">
								<option value="total">${__('All Calls')}</option>
								<option value="missed">${__('Missed')}</option>
								<option value="connected">${__('Connected')}</option>
								<option value="busy">${__('Busy')}</option>
								<option value="no_answer">${__('No Answer')}</option>
								<option value="failed">${__('Failed')}</option>
								<option value="cancelled">${__('Cancelled')}</option>
							</select>
						</div>
						<div>
							<label>${__('Agent')}</label>
							<select class="form-control input-sm" data-role="agent-user"></select>
						</div>
						<div>
							<label>${__('Team')}</label>
							<select class="form-control input-sm" data-role="team"></select>
						</div>
						<div>
							<label>${__('Department')}</label>
							<select class="form-control input-sm" data-role="department"></select>
						</div>
						<div class="vobiz-filter-action">
							<button class="btn btn-default btn-sm" data-action="clear-filters">${__('Clear All Filters')}</button>
						</div>
					</div>
				</section>

				<section class="vobiz-band vobiz-summary">
					<div class="vobiz-kpi-grid" data-role="kpis"></div>
				</section>

				<div class="vobiz-chart-grid single">
					<section class="vobiz-band">
						<div class="vobiz-section-title">
							<h3>${__('Daily Call Trend')}</h3>
							<span class="text-muted" data-role="range-label"></span>
						</div>
						<div class="vobiz-chart" data-role="daily-chart"></div>
					</section>
				</div>

				<div class="vobiz-chart-grid single">
					<section class="vobiz-band vobiz-agent-section">
						<div class="vobiz-section-title">
							<h3>${__('Agent Performance')}</h3>
							<span class="text-muted">${__('Sorted by total calls')}</span>
						</div>
						<div data-role="agent-chart"></div>
					</section>
				</div>

				<section class="vobiz-band">
					<div class="vobiz-section-title">
						<h3>${__('Call Log Table')}</h3>
						<div class="vobiz-section-actions">
							<span class="text-muted" data-role="calls-note">${__('Call logs load on demand')}</span>
							<button class="btn btn-default btn-sm" data-action="load-calls">${__('Load Call Logs')}</button>
						</div>
					</div>
					<div class="vobiz-table-wrap">
						<table class="table table-sm vobiz-table">
							<thead>
								<tr>
									<th>${__('Call Log')}</th>
									<th>${__('Agent')}</th>
									<th>${__('Queue')}</th>
									<th>${__('Reference')}</th>
									<th>${__('Customer')}</th>
									<th>${__('Status')}</th>
									<th>${__('Talk Time')}</th>
									<th>${__('Disposition')}</th>
									<th>${__('Time')}</th>
									<th>${__('Recording')}</th>
								</tr>
							</thead>
							<tbody data-role="calls"></tbody>
						</table>
					</div>
					<div class="vobiz-load-more">
						<button class="btn btn-default btn-sm hide" data-action="load-more-calls">${__('Load More')}</button>
					</div>
				</section>
			</div>
		`);
		this.inject_styles();
	}

	inject_styles() {
		if ($('#vobiz-agent-analytics-style').length) return;
		$('head').append(`
			<style id="vobiz-agent-analytics-style">
				.vobiz-analytics-page { background: #f7f8f6; color: #243042; margin: 0 -15px -15px; min-height: calc(100vh - 72px); padding: 24px; }
				.vobiz-analytics-head { align-items: center; display: flex; justify-content: space-between; margin-bottom: 20px; }
				.vobiz-analytics-head h2 { font-size: 22px; font-weight: 800; margin: 0; }
				.vobiz-eyebrow { color: #667085; font-size: 11px; font-weight: 800; letter-spacing: .04em; margin-bottom: 4px; }
				.vobiz-head-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }
				.vobiz-band { background: #fff; border: 1px solid #e2e6df; border-radius: 8px; box-shadow: 0 1px 2px rgba(36, 48, 66, .03); margin-bottom: 16px; padding: 16px; }
				.vobiz-filter-grid { align-items: end; display: grid; gap: 12px; grid-template-columns: repeat(8, minmax(0, 1fr)); }
				.vobiz-filter-grid label { color: #667085; display: block; font-size: 11px; font-weight: 800; margin-bottom: 5px; text-transform: uppercase; }
				.vobiz-filter-action { display: flex; justify-content: flex-end; }
				.vobiz-summary { padding: 0; }
				.vobiz-kpi-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); }
				.vobiz-kpi { background: #fff; border-right: 1px solid #edf0ea; min-height: 106px; padding: 18px 16px; position: relative; }
				.vobiz-kpi:last-child { border-right: 0; }
				.vobiz-kpi:before { border-radius: 50%; content: ""; height: 8px; position: absolute; right: 16px; top: 18px; width: 8px; }
				.vobiz-kpi.missed:before { background: #e2554f; }
				.vobiz-kpi.connected:before { background: #1f9d72; }
				.vobiz-kpi.total:before { background: #f2a93b; }
				.vobiz-kpi.average:before { background: #3b82c4; }
				.vobiz-kpi.busy:before { background: #b875e6; }
				.vobiz-kpi.no-answer:before { background: #5f7dd4; }
				.vobiz-kpi span { color: #667085; display: block; font-size: 11px; font-weight: 800; text-transform: uppercase; }
				.vobiz-kpi strong { color: #344054; display: block; font-size: 30px; line-height: 1.05; margin: 9px 0 6px; }
				.vobiz-kpi small { color: #667085; display: block; font-size: 11px; min-height: 15px; }
				.vobiz-chart-grid { display: grid; gap: 16px; grid-template-columns: minmax(0, 1.4fr) minmax(320px, .6fr); }
				.vobiz-chart-grid.single { grid-template-columns: 1fr; }
				.vobiz-section-title { align-items: center; display: flex; gap: 10px; justify-content: space-between; margin-bottom: 12px; }
				.vobiz-section-title h3 { font-size: 15px; font-weight: 800; margin: 0; }
				.vobiz-section-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }
				.vobiz-chart { min-height: 320px; }
				.vobiz-axis-chart { display: grid; gap: 10px; grid-template-columns: 42px minmax(0, 1fr); }
				.vobiz-y-axis { color: #8a94a3; display: grid; font-size: 11px; grid-template-rows: repeat(5, 1fr); height: 230px; line-height: 1; text-align: right; }
				.vobiz-y-axis span { transform: translateY(-5px); }
				.vobiz-plot { min-width: 0; }
				.vobiz-plot-area { border-bottom: 1px solid #dfe4dc; border-left: 1px solid #dfe4dc; display: grid; grid-template-rows: 230px 34px; position: relative; }
				.vobiz-grid-lines { bottom: 34px; display: grid; grid-template-rows: repeat(4, 1fr); left: 0; pointer-events: none; position: absolute; right: 0; top: 0; }
				.vobiz-grid-lines span { border-top: 1px solid #edf0ea; }
				.vobiz-daily-chart { align-items: end; display: grid; gap: 12px; grid-auto-flow: column; grid-auto-columns: minmax(50px, 1fr); height: 230px; padding: 8px 12px 0; position: relative; z-index: 1; }
				.vobiz-day { align-items: end; display: grid; gap: 6px; grid-template-rows: 18px minmax(0, 1fr); justify-items: center; min-width: 0; }
				.vobiz-day-total { color: #475467; font-size: 11px; font-weight: 800; }
				.vobiz-day-bar { align-items: stretch; display: flex; flex-direction: column; justify-content: end; width: min(38px, 72%); }
				.vobiz-day-segment { width: 100%; }
				.vobiz-x-axis { color: #667085; display: grid; font-size: 11px; gap: 12px; grid-auto-flow: column; grid-auto-columns: minmax(50px, 1fr); padding: 8px 12px 0; text-align: center; }
				.vobiz-chart-legend { align-items: center; display: flex; flex-wrap: wrap; gap: 14px; margin-top: 12px; }
				.vobiz-legend-item { align-items: center; color: #475467; display: inline-flex; font-size: 12px; font-weight: 700; gap: 7px; }
				.vobiz-legend-dot { border-radius: 50%; height: 9px; width: 9px; }
				.vobiz-stack { background: #eef1ed; border-radius: 4px; display: flex; height: 18px; overflow: hidden; }
				.vobiz-stack span { display: block; height: 100%; }
				.vobiz-agent-grid { display: grid; gap: 10px; }
				.vobiz-agent-card { align-items: center; border: 1px solid #edf0ea; border-radius: 8px; display: grid; gap: 14px; grid-template-columns: minmax(220px, 280px) minmax(0, 1fr) repeat(3, minmax(88px, 112px)); padding: 12px; }
				.vobiz-agent-card:hover { background: #fbfcfa; }
				.vobiz-agent-title { color: #344054; font-size: 13px; font-weight: 800; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
				.vobiz-agent-sub { color: #667085; font-size: 11px; margin-top: 4px; }
				.vobiz-agent-meter { align-self: center; display: grid; gap: 6px; }
				.vobiz-agent-meter .vobiz-stack { height: 16px; }
				.vobiz-agent-mini { align-self: center; }
				.vobiz-agent-mini span { color: #667085; display: block; font-size: 10px; font-weight: 800; text-transform: uppercase; }
				.vobiz-agent-mini strong { color: #344054; display: block; font-size: 18px; line-height: 1.1; margin-top: 4px; }
				.vobiz-breakdown { display: grid; gap: 10px; }
				.vobiz-breakdown-row { align-items: center; display: grid; gap: 10px; grid-template-columns: 110px minmax(0, 1fr) 44px; }
				.vobiz-breakdown-label { color: #475467; font-size: 12px; font-weight: 800; }
				.vobiz-bar-track { background: #eef1ed; border-radius: 999px; height: 10px; overflow: hidden; }
				.vobiz-bar-fill { background: #64748b; height: 100%; }
				.vobiz-table-wrap { overflow-x: auto; }
				.vobiz-table { margin: 0; table-layout: auto; }
				.vobiz-table th { background: #fafbf9; color: #667085; font-size: 11px; font-weight: 800; white-space: nowrap; }
				.vobiz-table td { color: #344054; font-size: 12px; vertical-align: middle; white-space: nowrap; }
				.vobiz-load-more { display: flex; justify-content: center; padding-top: 12px; }
				.vobiz-status-pill { border-radius: 999px; display: inline-flex; font-size: 11px; font-weight: 800; padding: 3px 8px; }
				.vobiz-recording-player { align-items: center; display: inline-flex; gap: 8px; min-width: 230px; }
				.vobiz-recording-player audio { display: none; }
				.vobiz-recording-time { color: #475467; font-size: 11px; font-weight: 800; min-width: 72px; }
				.vobiz-spectrum { align-items: center; display: inline-flex; gap: 3px; height: 26px; min-width: 84px; }
				.vobiz-spectrum span { animation: vobiz-spectrum-pulse .82s ease-in-out infinite; animation-play-state: paused; background: #111827; border-radius: 999px; display: block; height: 8px; width: 4px; }
				.vobiz-spectrum span:nth-child(1) { height: 18px; animation-delay: -.08s; }
				.vobiz-spectrum span:nth-child(2) { height: 24px; animation-delay: -.22s; }
				.vobiz-spectrum span:nth-child(3) { height: 14px; animation-delay: -.14s; }
				.vobiz-spectrum span:nth-child(4) { height: 22px; animation-delay: -.31s; }
				.vobiz-spectrum span:nth-child(5) { height: 12px; animation-delay: -.18s; }
				.vobiz-spectrum span:nth-child(6) { height: 26px; animation-delay: -.37s; }
				.vobiz-spectrum span:nth-child(7) { height: 16px; animation-delay: -.11s; }
				.vobiz-spectrum span:nth-child(8) { height: 22px; animation-delay: -.29s; }
				.vobiz-spectrum span:nth-child(9) { height: 10px; animation-delay: -.16s; }
				.vobiz-spectrum span:nth-child(10) { height: 20px; animation-delay: -.34s; }
				.vobiz-recording-player.playing .vobiz-spectrum span { animation-play-state: running; }
				@keyframes vobiz-spectrum-pulse {
					0%, 100% { transform: scaleY(.45); opacity: .56; }
					50% { transform: scaleY(1); opacity: 1; }
				}
				@media (max-width: 1100px) {
					.vobiz-analytics-page { padding: 14px; }
					.vobiz-filter-grid, .vobiz-kpi-grid, .vobiz-chart-grid { grid-template-columns: 1fr; }
					.vobiz-filter-action { justify-content: flex-start; }
					.vobiz-agent-card { grid-template-columns: 1fr; }
				}
			</style>
		`);
	}

	bind() {
		const $main = this.page.main;
		$main.on('click', '[data-action="open-console"]', () => frappe.set_route('vobiz-agent-console'));
		$main.on('click', '[data-action="refresh"]', () => this.load());
		$main.on('click', '[data-action="clear-filters"]', () => this.clear_filters());
		$main.on('click', '[data-action="load-calls"]', () => this.load_calls(true));
		$main.on('click', '[data-action="load-more-calls"]', () => this.load_calls(false));
		$main.on('click', '[data-action="play-recording"]', (e) => this.play_recording($(e.currentTarget)));
		$main.on('click', '[data-action="stop-recording"]', (e) => this.stop_recording($(e.currentTarget)));
		$main.on('change', '[data-role="from-date"], [data-role="to-date"], [data-role="status-filter"], [data-role="queue-source"], [data-role="agent-user"], [data-role="team"], [data-role="department"]', () => this.schedule_load());
	}

	on_page_show() {
		if (!this.initialized) {
			this.load();
		}
	}

	schedule_load() {
		clearTimeout(this.load_timer);
		this.load_timer = setTimeout(() => this.load(), 350);
	}

	clear_filters() {
		clearTimeout(this.load_timer);
		const today = frappe.datetime.get_today();
		this.state = Object.assign({}, this.state, {
			from_date: today,
			to_date: today,
			status_filter: 'total',
			queue_source: 'CRM Lead',
			agent_user: '',
			team: '',
			department: ''
		});
		this.page.main.find('[data-role="from-date"]').val(today);
		this.page.main.find('[data-role="to-date"]').val(today);
		this.page.main.find('[data-role="status-filter"]').val('total');
		this.page.main.find('[data-role="queue-source"]').val('CRM Lead');
		this.page.main.find('[data-role="agent-user"]').val('');
		this.page.main.find('[data-role="team"]').val('');
		this.page.main.find('[data-role="department"]').val('');
		this.load();
	}

	filters() {
		return {
			from_date: (this.page.main.find('[data-role="from-date"]').val() || frappe.datetime.get_today()).trim(),
			to_date: (this.page.main.find('[data-role="to-date"]').val() || frappe.datetime.get_today()).trim(),
			status_filter: (this.page.main.find('[data-role="status-filter"]').val() || 'total').trim(),
			queue_source: (this.page.main.find('[data-role="queue-source"]').val() || this.state.queue_source || 'CRM Lead').trim(),
			agent_user: (this.page.main.find('[data-role="agent-user"]').val() || this.state.agent_user || '').trim(),
			team: (this.page.main.find('[data-role="team"]').val() || '').trim(),
			department: (this.page.main.find('[data-role="department"]').val() || '').trim()
		};
	}

	load() {
		const filters = this.filters();
		const request_id = ++this.request_id;
		this.loading = true;
		this.calls_loaded = false;
		this.calls_offset = 0;
		this.has_more_calls = false;
		this.calls_request_id += 1;
		this.render_calls_placeholder();
		frappe.call({
			method: 'vobiz_click_to_call.api.console.get_analytics',
			args: Object.assign({}, filters, { include_calls: 0 }),
			freeze: false
		}).then((r) => {
			if (request_id !== this.request_id) return;
			const data = r.message || {};
			this.state = Object.assign({}, this.state, filters, {
				queue_source: data.queue_source || filters.queue_source,
				agent_user: data.agent_user || filters.agent_user || '',
				team: data.team || filters.team || '',
				department: data.department || filters.department || ''
			});
			this.render_filters(data);
			this.render_kpis(data.summary || {}, data);
			this.render_charts(data);
			this.initialized = true;
		}).always(() => {
			if (request_id === this.request_id) {
				this.loading = false;
			}
		});
	}

	render_filters(data) {
		const sources = data.queue_sources || ['CRM Lead', 'Patient', 'Discontinued'];
		const $source = this.page.main.find('[data-role="queue-source"]');
		$source.html(sources.map(value => `<option value="${this.escape(value)}">${this.escape(__(value))}</option>`).join(''));
		$source.val(data.queue_source || this.state.queue_source || sources[0]);
		this.page.main.find('[data-role="from-date"]').val(data.from_date || this.state.from_date);
		this.page.main.find('[data-role="to-date"]').val(data.to_date || this.state.to_date);
		this.page.main.find('[data-role="status-filter"]').val(data.status_filter || this.state.status_filter);
		const teams = data.team_options || [];
		const $team = this.page.main.find('[data-role="team"]');
		if (teams.length) {
			$team.prop('disabled', false).html([
				`<option value="">${__('All Teams')}</option>`,
				...teams.map(value => `<option value="${this.escape(value)}">${this.escape(value)}</option>`)
			].join(''));
			$team.val(data.team || this.state.team || '');
		} else {
			$team.prop('disabled', true).html(`<option value="">${__('All Teams')}</option>`).val('');
		}
		const departments = data.department_options || [];
		const $department = this.page.main.find('[data-role="department"]');
		if (departments.length) {
			$department.prop('disabled', false).html([
				`<option value="">${__('All Departments')}</option>`,
				...departments.map(value => `<option value="${this.escape(value)}">${this.escape(value)}</option>`)
			].join(''));
			$department.val(data.department || this.state.department || '');
		} else {
			$department.prop('disabled', true).html(`<option value="">${__('All Departments')}</option>`).val('');
		}
		const agents = data.agent_options || [];
		const $agent = this.page.main.find('[data-role="agent-user"]');
		if (data.is_admin || data.is_team_leader) {
			const label = data.is_admin ? __('All Agents') : __('All Team Agents');
			$agent.prop('disabled', false).html([
				`<option value="">${label}</option>`,
				...agents.map(value => `<option value="${this.escape(value)}">${this.escape(value)}</option>`)
			].join(''));
			$agent.val(data.agent_user || this.state.agent_user || '');
		} else {
			const current = data.agent_user || agents[0] || this.state.agent_user || '';
			$agent.prop('disabled', true).html(`<option value="${this.escape(current)}">${this.escape(current || __('My Calls'))}</option>`);
			$agent.val(current);
		}
		this.page.main.find('[data-role="range-label"]').text(`${frappe.datetime.str_to_user(data.from_date)} - ${frappe.datetime.str_to_user(data.to_date)}`);
	}

	load_calls(reset) {
		if (this.calls_loading) return;
		const filters = this.filters();
		const offset = reset ? 0 : this.calls_offset;
		const request_id = ++this.calls_request_id;
		this.calls_loading = true;
		this.page.main.find('[data-action="load-calls"], [data-action="load-more-calls"]').prop('disabled', true);
		this.page.main.find('[data-role="calls-note"]').text(__('Loading call logs...'));
		frappe.call({
			method: 'vobiz_click_to_call.api.console.get_analytics',
			args: Object.assign({}, filters, {
				include_calls: 1,
				call_limit: this.calls_limit,
				call_offset: offset
			}),
			freeze: false
		}).then((r) => {
			if (request_id !== this.calls_request_id) return;
			const data = r.message || {};
			this.calls_loaded = true;
			this.calls_offset = (data.call_offset || 0) + (data.calls || []).length;
			this.has_more_calls = !!data.has_more_calls;
			this.render_calls(data.calls || [], !reset, data);
		}).always(() => {
			if (request_id === this.calls_request_id) {
				this.calls_loading = false;
				this.page.main.find('[data-action="load-calls"], [data-action="load-more-calls"]').prop('disabled', false);
			}
		});
	}

	render_calls_placeholder() {
		this.page.main.find('[data-role="calls"]').html(`
			<tr>
				<td colspan="10" class="text-muted text-center">${__('Click Load Call Logs to fetch matching rows.')}</td>
			</tr>
		`);
		this.page.main.find('[data-role="calls-note"]').text(__('Call logs load on demand'));
		this.page.main.find('[data-action="load-calls"]').removeClass('hide');
		this.page.main.find('[data-action="load-more-calls"]').addClass('hide');
	}

	render_kpis(summary, data) {
		const kpis = [
			{ label: __('Missed'), value: summary.missed || 0, note: `${summary.missed_rate || 0}% ${__('missed')}`, className: 'missed' },
			{ label: __('Connected'), value: summary.connected || 0, note: `${summary.answer_rate || 0}% ${__('answer rate')}`, className: 'connected' },
			{ label: __('Total Calls'), value: summary.total || 0, note: data.queue_source || '', className: 'total' },
			{ label: __('Avg Talk Time'), value: summary.average_duration_label || '0s', note: __('connected calls only'), className: 'average' },
			{ label: __('Busy'), value: summary.busy || 0, note: __('busy outcomes'), className: 'busy' },
			{ label: __('No Answer'), value: summary.no_answer || 0, note: __('ring timeout'), className: 'no-answer' }
		];
		this.page.main.find('[data-role="kpis"]').html(kpis.map(row => `
			<div class="vobiz-kpi ${this.escape(row.className)}">
				<span>${this.escape(row.label)}</span>
				<strong>${this.escape(String(row.value))}</strong>
				<small>${this.escape(row.note || '')}</small>
			</div>
		`).join(''));
	}

	render_calls(calls, append, data) {
		const html = calls.map(row => {
			const route = row.reference_name ? `/app/${frappe.router.slug(row.reference_doctype || 'CRM Lead')}/${encodeURIComponent(row.reference_name)}` : '';
			return `
				<tr>
					<td><a href="/app/vobiz-call-log/${this.escape(row.name || '')}"><code>${this.escape(row.name || '')}</code></a></td>
					<td>${this.escape(row.user || '')}</td>
					<td>${this.escape(row.reference_doctype || '')}</td>
					<td>${route ? `<a href="${route}"><code>${this.escape(row.reference_name || '')}</code></a>` : ''}</td>
					<td>${this.escape(row.customer_number || '')}</td>
					<td><span class="vobiz-status-pill" style="background:${this.bucket_fill(row.bucket)}; color:${this.bucket_color(row.bucket)}">${this.escape(row.bucket_label || row.status || '')}</span></td>
					<td>${this.escape(row.duration_label || '0s')}</td>
					<td>${this.escape(row.disposition || '')}</td>
					<td>${row.creation ? frappe.datetime.str_to_user(row.creation) : ''}</td>
					<td>${this.recording_button_html(row)}</td>
				</tr>
			`;
		}).join('');
		const $body = this.page.main.find('[data-role="calls"]');
		if (append && html) {
			$body.append(html);
		} else {
			$body.html(html || `<tr><td colspan="10" class="text-muted text-center">${__('No calls found for this filter.')}</td></tr>`);
		}
		const shown = this.calls_offset || calls.length || 0;
		const total = data && data.matching_call_count != null ? data.matching_call_count : shown;
		this.page.main.find('[data-role="calls-note"]').text(`${__('Showing')} ${shown} ${__('of')} ${total} ${__('matching call logs')}`);
		this.page.main.find('[data-action="load-calls"]').toggleClass('hide', !!calls.length || append);
		this.page.main.find('[data-action="load-more-calls"]').toggleClass('hide', !this.has_more_calls);
	}

	recording_button_html(row) {
		if (row.bucket !== 'connected') {
			return `<span class="text-muted">-</span>`;
		}
		if (!row.recording_download_url) {
			return `<span class="text-muted">${this.escape(row.recording_status || __('No recording'))}</span>`;
		}
		return `
			<div class="vobiz-recording-player" data-recording-player>
				<button class="btn btn-default btn-xs vobiz-recording-btn" data-action="play-recording">
					<i class="fa fa-play"></i> ${__('Play')}
				</button>
				<div class="vobiz-spectrum" aria-hidden="true">
					${Array.from({ length: 10 }).map(() => '<span></span>').join('')}
				</div>
				<span class="vobiz-recording-time" data-role="recording-time">0:00 / 0:00</span>
				<button class="btn btn-default btn-xs hide" data-action="stop-recording">
					<i class="fa fa-stop"></i> ${__('Stop')}
				</button>
				<audio preload="none" src="${this.escape(row.recording_download_url)}"></audio>
			</div>
		`;
	}

	play_recording($button) {
		const $player = $button.closest('[data-recording-player]');
		const audio = $player.find('audio').get(0);
		if (!audio) return;
		this.page.main.find('audio').each((_, item) => {
			if (item !== audio) {
				const $other = $(item).closest('[data-recording-player]');
				$other.data('resetting', true);
				item.pause();
				this.set_recording_player_state($other, item, 'idle');
				setTimeout(() => $other.removeData('resetting'), 0);
			}
		});
		$(audio)
			.off('loadedmetadata.vobiz-analytics timeupdate.vobiz-analytics ended.vobiz-analytics pause.vobiz-analytics')
			.on('loadedmetadata.vobiz-analytics timeupdate.vobiz-analytics', () => this.update_recording_time($player, audio))
			.on('ended.vobiz-analytics', () => this.set_recording_player_state($player, audio, 'ended'))
			.on('pause.vobiz-analytics', () => {
				if ($player.data('resetting')) return;
				if (!audio.ended && audio.currentTime > 0) {
					this.set_recording_player_state($player, audio, 'paused');
				}
			});
		if (audio.paused) {
			const promise = audio.play();
			this.set_recording_player_state($player, audio, 'playing');
			if (promise && promise.catch) {
				promise.catch(() => this.set_recording_player_state($player, audio, 'idle'));
			}
		} else {
			audio.pause();
			this.set_recording_player_state($player, audio, 'paused');
		}
		this.update_recording_time($player, audio);
	}

	stop_recording($button) {
		const $player = $button.closest('[data-recording-player]');
		const audio = $player.find('audio').get(0);
		if (!audio) return;
		$player.data('resetting', true);
		audio.pause();
		try {
			audio.currentTime = 0;
		} catch (e) {
			// Some browsers can reject seeking before metadata is ready.
		}
		this.set_recording_player_state($player, audio, 'idle');
		setTimeout(() => $player.removeData('resetting'), 0);
	}

	set_recording_player_state($player, audio, state) {
		if (!$player || !$player.length) return;
		const is_playing = state === 'playing';
		$player.toggleClass('playing', is_playing);
		$player.find('[data-action="play-recording"]').html(is_playing
			? `<i class="fa fa-pause"></i> ${__('Pause')}`
			: `<i class="fa fa-play"></i> ${__('Play')}`);
		$player.find('[data-action="stop-recording"]').toggleClass('hide', state === 'idle' || state === 'ended');
		if (state === 'ended') {
			try {
				audio.currentTime = 0;
			} catch (e) {
				// ignore seek failures
			}
		}
		this.update_recording_time($player, audio);
	}

	update_recording_time($player, audio) {
		if (!$player || !$player.length || !audio) return;
		const current = this.format_audio_time(audio.currentTime || 0);
		const duration = Number.isFinite(audio.duration) ? this.format_audio_time(audio.duration) : '0:00';
		$player.find('[data-role="recording-time"]').text(`${current} / ${duration}`);
	}

	format_audio_time(seconds) {
		seconds = Math.max(0, Math.floor(Number(seconds) || 0));
		const minutes = Math.floor(seconds / 60);
		const remainder = seconds % 60;
		return `${minutes}:${String(remainder).padStart(2, '0')}`;
	}

	render_charts(data) {
		this.render_daily_chart(data.daily || []);
		this.render_agent_chart(data.agents || []);
	}

	render_daily_chart(rows) {
		const max = Math.max(...rows.map(row => row.total || 0), 1);
		const axisMax = this.axis_max(max);
		const ticks = this.axis_ticks(axisMax);
		this.page.main.find('[data-role="daily-chart"]').html(`
			<div class="vobiz-axis-chart">
				<div class="vobiz-y-axis">${ticks.map(value => `<span>${value}</span>`).join('')}</div>
				<div class="vobiz-plot">
					<div class="vobiz-plot-area">
						<div class="vobiz-grid-lines">${[0, 1, 2, 3].map(() => '<span></span>').join('')}</div>
						<div class="vobiz-daily-chart">
							${rows.map(row => this.daily_bar_html(row, axisMax)).join('')}
						</div>
						<div class="vobiz-x-axis">
							${rows.map(row => `<span>${this.escape(this.short_date(row.date))}</span>`).join('')}
						</div>
					</div>
				</div>
			</div>
			${this.legend_html([
				{ label: __('Total shown above each bar'), color: '#475467' },
				{ label: __('Missed'), color: this.bucket_color('missed') },
				{ label: __('Connected'), color: this.bucket_color('connected') },
				{ label: __('Other'), color: this.bucket_color('other') }
			])}
		`);
	}

	render_agent_chart(agents) {
		const rows = agents.slice(0, 8);
		if (!rows.length) {
			this.page.main.find('[data-role="agent-chart"]').html(`<div class="text-muted">${__('Agent chart is available for System Manager users.')}</div>`);
			return;
		}
		this.page.main.find('[data-role="agent-chart"]').html(`
			<div class="vobiz-agent-grid">
				${rows.map(row => this.agent_card_html(row)).join('')}
			</div>
			${this.legend_html([
				{ label: __('Missed'), color: this.bucket_color('missed') },
				{ label: __('Connected'), color: this.bucket_color('connected') },
				{ label: __('Other'), color: this.bucket_color('other') }
			])}
		`);
	}

	agent_card_html(row) {
		const connected = row.connected || 0;
		const missed = row.missed || 0;
		const total = row.total || 0;
		const other = Math.max(0, total - connected - missed);
		const segment = (value, bucket) => {
			if (!value || !total) return '';
			return `<span title="${this.escape(this.bucket_label(bucket))}: ${value}" style="background:${this.bucket_color(bucket)}; width:${Math.round((value / total) * 100)}%"></span>`;
		};
		return `
			<div class="vobiz-agent-card">
				<div>
					<div class="vobiz-agent-title" title="${this.escape(row.user || '')}">${this.escape(row.user || '')}</div>
					<div class="vobiz-agent-sub">${__('Agent')}</div>
				</div>
				<div class="vobiz-agent-meter">
					<div class="vobiz-stack">
						${segment(missed, 'missed')}
						${segment(connected, 'connected')}
						${segment(other, 'other')}
					</div>
					<div class="vobiz-agent-sub">${missed} ${__('missed')} / ${connected} ${__('connected')}</div>
				</div>
				<div class="vobiz-agent-mini"><span>${__('Answer')}</span><strong>${row.answer_rate || 0}%</strong></div>
				<div class="vobiz-agent-mini"><span>${__('Avg Talk')}</span><strong>${this.escape(row.average_duration_label || '0s')}</strong></div>
				<div class="vobiz-agent-mini"><span>${__('Total')}</span><strong>${total}</strong></div>
			</div>
		`;
	}

	daily_bar_html(row, axisMax) {
		const total = row.total || 0;
		const height = total ? Math.max(8, Math.round((total / axisMax) * 190)) : 3;
		const connected = row.connected || 0;
		const missed = row.missed || 0;
		const other = Math.max(0, total - connected - missed);
		const segment = (value, bucket) => {
			if (!value || !total) return '';
			return `<div class="vobiz-day-segment" title="${this.escape(this.bucket_label(bucket))}: ${value}" style="background:${this.bucket_color(bucket)}; height:${Math.max(3, Math.round((value / total) * height))}px"></div>`;
		};
		return `
			<div class="vobiz-day">
				<div class="vobiz-day-total">${total}</div>
				<div class="vobiz-day-bar" title="${this.escape(row.date)} - ${total} ${__('calls')}">
					${segment(other, 'other')}
					${segment(connected, 'connected')}
					${segment(missed, 'missed')}
					${!total ? `<div class="vobiz-day-segment" style="background:#d8ddd6; height:${height}px"></div>` : ''}
				</div>
			</div>
		`;
	}

	legend_html(rows) {
		return `
			<div class="vobiz-chart-legend">
				${rows.map(row => `
					<div class="vobiz-legend-item">
						<span class="vobiz-legend-dot" style="background:${row.color}"></span>
						<span>${this.escape(row.label)}</span>
					</div>
				`).join('')}
			</div>
		`;
	}

	bucket_color(bucket) {
		return {
			missed: '#e2554f',
			failed: '#cf5f72',
			cancelled: '#9a6be8',
			busy: '#b875e6',
			no_answer: '#5f7dd4',
			connected: '#1f9d72',
			other: '#64748b'
		}[bucket] || '#64748b';
	}

	bucket_fill(bucket) {
		return {
			missed: '#fdeceb',
			failed: '#fae8ec',
			cancelled: '#f0eafb',
			busy: '#f5eafa',
			no_answer: '#eaf0ff',
			connected: '#e4f7ef',
			other: '#eef2f7'
		}[bucket] || '#eef2f7';
	}

	bucket_label(bucket) {
		return {
			missed: __('Missed'),
			failed: __('Failed'),
			cancelled: __('Cancelled'),
			busy: __('Busy'),
			no_answer: __('No Answer'),
			connected: __('Connected'),
			other: __('Other')
		}[bucket] || __('Other');
	}

	axis_max(value) {
		const raw = Math.max(4, value || 1);
		const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
		const normalized = raw / magnitude;
		const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
		return nice * magnitude;
	}

	axis_ticks(max) {
		const step = max / 4;
		return [max, max - step, max - step * 2, max - step * 3, 0].map(value => Math.round(value));
	}

	short_date(value) {
		if (!value) return '';
		const parts = String(value).split('-');
		if (parts.length !== 3) return value;
		return `${parts[2]}-${parts[1]}`;
	}

	escape(value) {
		return frappe.utils.escape_html(value == null ? '' : String(value));
	}
}
