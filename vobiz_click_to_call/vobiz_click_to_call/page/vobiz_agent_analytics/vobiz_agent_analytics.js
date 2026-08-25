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
		this.attendance_timer = null;
		this.attendance_refresh_timer = null;
		this.request_id = 0;
		this.calls_request_id = 0;
		this.calls_loaded = false;
		this.calls_offset = 0;
		this.calls_limit = 50;
		this.has_more_calls = false;
		this.attendance_refresh_ms = 30000;
		this.state = {
			from_date: frappe.datetime.get_today(),
			to_date: frappe.datetime.get_today(),
			status_filter: 'total',
			queue_source: 'CRM Lead and Patient',
			agent_user: [],
			team: [],
			department: '',
			agents: [],
			agent_status_filter: 'all',
			agent_search: '',
			agent_sort: 'total',
			agent_page: 1,
			agent_page_size: 10,
			selected_agent_user: '',
			agent_calls: {},
			attendance_log_visible: {},
			selected_call_status: '',
			selected_call_page: 0,
			selected_call_limit: 25,
			selected_call_has_more: false
		};
		this.render();
		this.bind();
		this.load();
		this.start_attendance_refresh_timer();
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
							<div class="vobiz-checkbox-dropdown" data-role="agent-dropdown">
								<button type="button" class="form-control input-sm vobiz-checkbox-dropdown-toggle" data-action="toggle-checkbox-dropdown" data-target-role="agent">
									<span data-role="agent-label">${__('All Agents')}</span>
									<i class="fa fa-angle-down"></i>
								</button>
								<div class="vobiz-checkbox-dropdown-menu" data-role="agent-menu"></div>
							</div>
						</div>
						<div>
							<label>${__('Team')}</label>
							<div class="vobiz-checkbox-dropdown" data-role="team-dropdown">
								<button type="button" class="form-control input-sm vobiz-checkbox-dropdown-toggle" data-action="toggle-checkbox-dropdown" data-target-role="team">
									<span data-role="team-label">${__('All Teams')}</span>
									<i class="fa fa-angle-down"></i>
								</button>
								<div class="vobiz-checkbox-dropdown-menu" data-role="team-menu"></div>
							</div>
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

				<section class="vobiz-band hide" data-role="selected-call-section">
					<div class="vobiz-section-title">
						<h3 data-role="selected-call-title">${__('Call List')}</h3>
						<span class="text-muted" data-role="selected-calls-note"></span>
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
									<th>${__('Call Attempts')}</th>
									<th>${__('Status')}</th>
									<th>${__('Talk Time')}</th>
									<th>${__('Disposition')}</th>
									<th>${__('Time')}</th>
									<th>${__('Recording')}</th>
								</tr>
							</thead>
							<tbody data-role="selected-calls"></tbody>
						</table>
					</div>
					<div class="vobiz-selected-call-pagination" data-role="selected-call-pagination"></div>
				</section>

				<div class="vobiz-chart-grid vobiz-trend-grid">
					<section class="vobiz-band">
						<div class="vobiz-section-title">
							<h3>${__('Daily Call Trend')}</h3>
							<span class="text-muted" data-role="range-label"></span>
						</div>
						<div class="vobiz-chart" data-role="daily-chart"></div>
					</section>
					<section class="vobiz-band">
						<div class="vobiz-section-title">
							<h3>${__('Call Status Mix')}</h3>
							<span class="text-muted" data-role="status-mix-label"></span>
						</div>
						<div class="vobiz-chart vobiz-pie-chart-wrap" data-role="status-pie-chart"></div>
					</section>
				</div>

				<div class="vobiz-chart-grid single">
					<section class="vobiz-band vobiz-agent-section">
						<div class="vobiz-section-title">
							<h3>${__('Agent Performance')}</h3>
							<span class="text-muted">${__('Sorted by total calls')}</span>
						</div>
						<div class="vobiz-agent-overview" data-role="agent-overview"></div>
						<div class="vobiz-agent-toolbar">
							<div class="vobiz-agent-status-filter" data-role="agent-status-filter">
								<span>${__('Status')}:</span>
								<button class="active" data-agent-filter="all">${__('All Agents')}</button>
								<button data-agent-filter="online"><i class="fa fa-circle"></i>${__('Online')}</button>
								<button data-agent-filter="break"><i class="fa fa-circle"></i>${__('On Break')}</button>
								<button data-agent-filter="offline"><i class="fa fa-circle"></i>${__('Offline')}</button>
							</div>
							<div class="vobiz-agent-toolbar-actions">
								<div class="vobiz-agent-search">
									<i class="fa fa-search"></i>
									<input class="form-control input-sm" data-role="agent-search" placeholder="${__('Search by email...')}">
								</div>
								<div class="vobiz-agent-sort">
									<span><i class="fa fa-sort-amount-desc"></i>${__('Sort')}:</span>
									<select class="form-control input-sm" data-role="agent-sort">
										<option value="total">${__('Total Calls')}</option>
										<option value="unique">${__('Unique Calls')}</option>
										<option value="answer">${__('Answer Rate (%)')}</option>
										<option value="talk">${__('Total Talk Time')}</option>
										<option value="rejected">${__('Rejected Calls')}</option>
									</select>
								</div>
								<div class="vobiz-agent-page-size">
									<span>${__('Show')}:</span>
									<select class="form-control input-sm" data-role="agent-page-size">
										<option value="10">10</option>
										<option value="25">25</option>
										<option value="50">50</option>
										<option value="100">100</option>
									</select>
									<span>${__('agents')}</span>
								</div>
							</div>
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
				.vobiz-filter-grid { align-items: end; display: grid; gap: 12px; grid-template-columns: repeat(9, minmax(0, 1fr)); }
				.vobiz-filter-grid label { color: #667085; display: block; font-size: 11px; font-weight: 800; margin-bottom: 5px; text-transform: uppercase; }
				.vobiz-checkbox-dropdown { position: relative; }
				.vobiz-checkbox-dropdown-toggle { align-items: center; display: flex; gap: 8px; height: 30px; justify-content: space-between; line-height: 1.42857143; overflow: hidden; text-align: left; width: 100%; }
				.vobiz-checkbox-dropdown-toggle span { color: #344054; display: block; font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; text-transform: none; white-space: nowrap; }
				.vobiz-checkbox-dropdown-toggle i { color: #667085; flex: 0 0 auto; }
				.vobiz-checkbox-dropdown-menu { background: #fff; border: 1px solid #d9e2ee; border-radius: 6px; box-shadow: 0 12px 28px rgba(15, 23, 42, .12); display: none; left: 0; max-height: 230px; min-width: 260px; overflow-x: hidden; overflow-y: auto; padding: 6px; position: absolute; top: calc(100% + 4px); width: max(100%, 260px); z-index: 20; }
				[data-role="agent-menu"] { width: min(350px, calc(100vw - 48px)); }
				.vobiz-checkbox-dropdown.open .vobiz-checkbox-dropdown-menu { display: block; }
				.vobiz-checkbox-search { background: #fff; padding: 4px 4px 8px; position: sticky; top: -6px; z-index: 1; }
				.vobiz-checkbox-search input { height: 28px; }
				.vobiz-checkbox-option { align-items: center; border-radius: 4px; color: #344054; cursor: pointer; display: flex; font-size: 12px; gap: 8px; line-height: 1.25; margin: 0; min-height: 30px; padding: 6px 8px; text-transform: none; }
				.vobiz-checkbox-option:hover { background: #f6f8fb; }
				.vobiz-checkbox-option input { flex: 0 0 auto; margin: 0; }
				.vobiz-checkbox-option span { overflow-wrap: anywhere; white-space: normal; word-break: break-word; }
				.vobiz-checkbox-empty { color: #667085; font-size: 12px; padding: 8px; }
				.vobiz-filter-action { display: flex; justify-content: flex-end; }
				.vobiz-summary { padding: 0; }
				.vobiz-kpi-grid { display: grid; grid-template-columns: repeat(9, minmax(0, 1fr)); }
				.vobiz-kpi { background: #fff; border-right: 1px solid #edf0ea; min-height: 106px; padding: 18px 16px; position: relative; }
				.vobiz-kpi:last-child { border-right: 0; }
				.vobiz-kpi[role="button"] { cursor: pointer; }
				.vobiz-kpi[role="button"]:hover { background: #fbfcfa; }
				.vobiz-kpi[role="button"]:focus { box-shadow: inset 0 0 0 2px #2f80ed; outline: 0; }
				.vobiz-kpi:before { border-radius: 50%; content: ""; height: 8px; position: absolute; right: 16px; top: 18px; width: 8px; }
				.vobiz-kpi.missed:before { background: #e2554f; }
				.vobiz-kpi.connected-incoming:before { background: #14b8a6; }
				.vobiz-kpi.connected-outgoing:before { background: #2563eb; }
				.vobiz-kpi.connected:before { background: #1f9d72; }
				.vobiz-kpi.total:before { background: #f2a93b; }
				.vobiz-kpi.unique:before { background: #14b8a6; }
				.vobiz-kpi.average:before { background: #3b82c4; }
				.vobiz-kpi.busy:before { background: #b875e6; }
				.vobiz-kpi.no-answer:before { background: #5f7dd4; }
				.vobiz-kpi.rejected:before { background: #e11d48; }
				.vobiz-kpi span { color: #667085; display: block; font-size: 11px; font-weight: 800; text-transform: uppercase; }
				.vobiz-kpi strong { color: #344054; display: block; font-size: 30px; line-height: 1.05; margin: 9px 0 6px; }
				.vobiz-kpi small { color: #667085; display: block; font-size: 11px; min-height: 15px; }
				.vobiz-chart-grid { display: grid; gap: 16px; grid-template-columns: minmax(0, 1.4fr) minmax(320px, .6fr); }
				.vobiz-trend-grid { grid-template-columns: minmax(0, 3fr) minmax(300px, 2fr); }
				.vobiz-chart-grid.single { grid-template-columns: 1fr; }
				.vobiz-section-title { align-items: center; display: flex; gap: 10px; justify-content: space-between; margin-bottom: 12px; }
				.vobiz-section-title h3 { font-size: 15px; font-weight: 800; margin: 0; }
				.vobiz-section-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }
				.vobiz-chart { min-height: 320px; max-width: 100%; min-width: 0; }
				.vobiz-pie-chart-wrap { align-content: center; display: grid; min-height: 320px; }
				.vobiz-pie-chart { align-items: center; display: grid; gap: 22px; grid-template-columns: minmax(160px, 230px) minmax(0, 1fr); position: relative; }
				.vobiz-pie-svg { display: block; height: 230px; max-width: 100%; overflow: visible; width: 100%; }
				.vobiz-pie-slice { cursor: pointer; outline: none; transform-box: fill-box; transform-origin: center; transition: filter .16s ease, opacity .16s ease, transform .16s ease; }
				.vobiz-pie-slice:hover { filter: drop-shadow(0 8px 14px rgba(15, 23, 42, .18)); opacity: .96; transform: scale(1.035); }
				.vobiz-pie-slice:focus { filter: drop-shadow(0 8px 14px rgba(15, 23, 42, .18)); transform: scale(1.035); }
				.vobiz-pie-tooltip { background: #1e293b; border-radius: 8px; box-shadow: 0 12px 28px rgba(15, 23, 42, .22); color: #fff; display: none; left: 0; min-width: 150px; padding: 10px 12px; pointer-events: none; position: absolute; top: 0; transform: translate(-50%, -112%); z-index: 8; }
				.vobiz-pie-tooltip.visible { display: block; }
				.vobiz-pie-tooltip:after { border: 6px solid transparent; border-top-color: #1e293b; content: ""; left: 50%; position: absolute; top: 100%; transform: translateX(-50%); }
				.vobiz-pie-tooltip-label { align-items: center; display: flex; font-size: 12px; font-weight: 800; gap: 7px; line-height: 1.2; margin-bottom: 5px; }
				.vobiz-pie-tooltip-dot { border-radius: 50%; height: 9px; width: 9px; }
				.vobiz-pie-tooltip-value { font-size: 15px; font-weight: 900; line-height: 1.15; }
				.vobiz-pie-tooltip-percent { color: #cbd5e1; font-size: 11px; font-weight: 800; margin-left: 6px; }
				.vobiz-pie-empty { align-items: center; background: #f8fafc; border: 1px dashed #d9e2ee; border-radius: 8px; color: #667085; display: flex; font-size: 13px; font-weight: 700; justify-content: center; min-height: 220px; text-align: center; }
				.vobiz-pie-legend { display: grid; gap: 9px; min-width: 0; width: 100%; }
				.vobiz-pie-legend-item { align-items: center; border: 1px solid transparent; border-radius: 8px; display: flex; gap: 12px; justify-content: space-between; min-height: 42px; padding: 10px 12px; }
				.vobiz-pie-legend-item span { font-size: 12px; font-weight: 800; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
				.vobiz-pie-legend-item strong { font-size: 12px; font-weight: 900; text-align: right; white-space: nowrap; }
				.vobiz-pie-legend-item.connected { background: #ecfdf5; border-color: #d1fae5; color: #047857; }
				.vobiz-pie-legend-item.no_answer { background: #fffbeb; border-color: #fde68a; color: #b45309; }
				.vobiz-pie-legend-item.cancelled { background: #faf5ff; border-color: #e9d5ff; color: #7e22ce; }
				.vobiz-pie-legend-item.busy { background: #eff6ff; border-color: #dbeafe; color: #1d4ed8; }
				.vobiz-pie-legend-item.failed { background: #fef2f2; border-color: #fee2e2; color: #b91c1c; }
				.vobiz-pie-legend-item.missed { background: #fff1f2; border-color: #ffe4e6; color: #be123c; }
				.vobiz-pie-legend-item.other { background: #f8fafc; border-color: #e2e8f0; color: #475569; }
				.vobiz-axis-chart { display: grid; gap: 10px; grid-template-columns: 42px minmax(0, 1fr); }
				.vobiz-y-axis { color: #8a94a3; display: grid; font-size: 11px; grid-template-rows: repeat(5, 1fr); height: 230px; line-height: 1; text-align: right; }
				.vobiz-y-axis span { transform: translateY(-5px); }
				.vobiz-plot { min-width: 0; overflow-x: auto; overflow-y: hidden; padding-bottom: 4px; }
				.vobiz-plot-area { border-bottom: 1px solid #dfe4dc; border-left: 1px solid #dfe4dc; min-width: 100%; position: relative; width: 100%; }
				.vobiz-grid-lines { bottom: 34px; display: grid; grid-template-rows: repeat(4, 1fr); left: 0; pointer-events: none; position: absolute; right: 0; top: 0; }
				.vobiz-grid-lines span { border-top: 1px solid #edf0ea; }
				.vobiz-line-chart { height: 264px; min-width: 100%; position: relative; width: 100%; z-index: 1; }
				.vobiz-line-svg { display: block; height: 264px; overflow: visible; width: 100%; }
				.vobiz-line-area { fill: rgba(47, 128, 237, .11); }
				.vobiz-line-path { fill: none; stroke: #2f80ed; stroke-linecap: round; stroke-linejoin: round; stroke-width: 3; }
				.vobiz-line-dot { cursor: default; fill: #fff; stroke: #2f80ed; stroke-width: 3; transition: fill .16s ease, r .16s ease; }
				.vobiz-line-dot:hover { fill: #2f80ed; r: 6; }
				.vobiz-line-x-label { fill: #667085; font-size: 11px; }
				.vobiz-chart-legend { align-items: center; display: flex; flex-wrap: wrap; gap: 14px; margin-top: 12px; }
				.vobiz-legend-item { align-items: center; color: #475467; display: inline-flex; font-size: 12px; font-weight: 700; gap: 7px; }
				.vobiz-legend-dot { border-radius: 50%; height: 9px; width: 9px; }
				.vobiz-stack { background: #eef1ed; border-radius: 4px; display: flex; height: 18px; overflow: hidden; }
				.vobiz-stack span { display: block; height: 100%; }
				@keyframes vobiz-status-pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: .65; transform: scale(1.28); } }
				@keyframes vobiz-call-ring { 0%, 100% { transform: rotate(0); } 20% { transform: rotate(-16deg); } 40% { transform: rotate(14deg); } 60% { transform: rotate(-10deg); } 80% { transform: rotate(8deg); } }
				@keyframes vobiz-call-pulse { 0% { opacity: .75; transform: scale(.65); } 100% { opacity: 0; transform: scale(1.65); } }
				.vobiz-agent-grid { display: grid; gap: 14px; }
				.vobiz-agent-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; box-shadow: 0 1px 3px rgba(15, 23, 42, .06); overflow: hidden; position: relative; transition: border-color .18s ease, box-shadow .18s ease, transform .18s ease; }
				.vobiz-agent-card:hover { border-color: #a7f3d0; box-shadow: 0 10px 24px rgba(15, 23, 42, .08); transform: translateY(-1px); }
				.vobiz-agent-card[role="button"] { cursor: pointer; }
				.vobiz-agent-card[role="button"]:focus { border-color: #14b8a6; box-shadow: 0 0 0 3px rgba(20, 184, 166, .16); outline: 0; }
				.vobiz-agent-card.is-expanded { border-color: #14b8a6; box-shadow: 0 12px 28px rgba(15, 23, 42, .1); }
				.vobiz-agent-card-inner { align-items: center; display: grid; gap: 22px; grid-template-columns: minmax(250px, 300px) minmax(220px, 1fr) minmax(390px, .9fr) minmax(210px, 250px); padding: 20px; }
				.vobiz-agent-call-indicator { align-items: center; background: #dc2626; border: 2px solid #fff; border-radius: 999px; box-shadow: 0 8px 18px rgba(220, 38, 38, .28); color: #fff; display: inline-flex; height: 26px; justify-content: center; left: 10px; position: absolute; top: 10px; width: 26px; z-index: 2; }
				.vobiz-agent-call-indicator i { animation: vobiz-call-ring .9s ease-in-out infinite; font-size: 12px; transform-origin: 50% 50%; }
				.vobiz-agent-call-indicator::after { animation: vobiz-call-pulse 1.3s ease-out infinite; border: 1px solid rgba(220, 38, 38, .55); border-radius: 999px; content: ""; inset: -5px; position: absolute; }
				.vobiz-agent-identity { align-items: center; display: flex; gap: 14px; min-width: 0; }
				.vobiz-agent-identity-body { min-width: 0; }
				.vobiz-agent-avatar-wrap { flex: 0 0 auto; position: relative; }
				.vobiz-agent-avatar { align-items: center; background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 8px; box-shadow: 0 1px 2px rgba(15, 23, 42, .04); color: #334155; display: flex; font-size: 18px; font-weight: 900; height: 48px; justify-content: center; text-transform: uppercase; width: 48px; }
				.vobiz-agent-presence { border: 2px solid #fff; border-radius: 50%; bottom: -4px; height: 16px; position: absolute; right: -4px; width: 16px; }
				.vobiz-agent-presence.online { background: #10b981; }
				.vobiz-agent-presence.offline { background: #94a3b8; }
				.vobiz-agent-presence-ring { animation: vobiz-status-pulse 2s infinite ease-in-out; background: #34d399; border-radius: 50%; bottom: -4px; height: 16px; opacity: .75; position: absolute; right: -4px; width: 16px; }
				.vobiz-agent-title { color: #0f172a; font-size: 14px; font-weight: 900; line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
				.vobiz-agent-sub { color: #64748b; font-size: 11px; margin-top: 4px; }
				.vobiz-agent-meta { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
				.vobiz-agent-role { color: #64748b; font-size: 12px; font-weight: 700; }
				.vobiz-agent-status-pill { border: 1px solid #e2e8f0; border-radius: 999px; font-size: 10px; font-weight: 900; letter-spacing: .03em; padding: 2px 8px; text-transform: uppercase; }
				.vobiz-agent-status-pill.online { background: #10b981; border-color: #10b981; color: #fff; }
				.vobiz-agent-status-pill.break { background: #fffbeb; border-color: #fde68a; color: #b45309; }
				.vobiz-agent-status-pill.offline { background: #f1f5f9; border-color: #e2e8f0; color: #64748b; }
				.vobiz-agent-status-pill.over-break { background: #fff1f2; border-color: #fecdd3; color: #be123c; }
				.vobiz-agent-meter { align-self: center; display: grid; gap: 8px; min-width: 0; }
				.vobiz-agent-meter-head { align-items: center; display: flex; gap: 10px; justify-content: space-between; min-width: 0; }
				.vobiz-agent-meter-label { color: #475569; font-size: 12px; font-weight: 800; }
				.vobiz-agent-meter-count { background: #f8fafc; border-radius: 6px; color: #333333; font-size: 11px; font-weight: 900; max-width: 100%; overflow: hidden; padding: 2px 8px; text-overflow: ellipsis; white-space: nowrap; }
				.vobiz-agent-meter .vobiz-stack { background: #f1f5f9; border-radius: 999px; box-shadow: inset 0 1px 2px rgba(15, 23, 42, .08); height: 12px; }
				.vobiz-agent-meter .vobiz-stack span { transition: filter .18s ease; }
				.vobiz-agent-meter .vobiz-stack span:hover { filter: brightness(.95); }
				.vobiz-agent-metrics { background: rgba(248, 250, 252, .72); border: 1px solid #f1f5f9; border-radius: 8px; display: grid; gap: 14px; grid-template-columns: repeat(5, minmax(74px, 1fr)); padding: 14px; }
				.vobiz-agent-mini { min-width: 0; text-align: center; }
				.vobiz-agent-mini span { color: #333333; display: block; font-size: 10px; font-weight: 900; letter-spacing: .02em; margin-bottom: 5px; text-transform: uppercase; }
				.vobiz-agent-mini strong { color: #0f172a; display: block; font-size: 18px; font-weight: 900; line-height: 1.1; }
				.vobiz-agent-mini strong.vobiz-agent-talk-value { font-size: 14px; line-height: 1.2; white-space: nowrap; }
				.vobiz-agent-answer-badge { border: 1px solid #e2e8f0; border-radius: 8px; display: inline-block; min-width: 58px; padding: 5px 8px; text-align: center; }
				.vobiz-agent-answer-badge.good { background: #ecfdf5; border-color: #a7f3d0; color: #059669; }
				.vobiz-agent-answer-badge.warn { background: #fffbeb; border-color: #fde68a; color: #d97706; }
				.vobiz-agent-answer-badge.bad { background: #fff1f2; border-color: #fecdd3; color: #e11d48; }
				.vobiz-agent-metric-icon { color: #94a3b8; margin-right: 6px; }
				.vobiz-agent-rejected { color: #e11d48 !important; }
				.vobiz-agent-attendance { border-left: 1px solid #f1f5f9; display: grid; gap: 8px; min-width: 0; padding-left: 18px; }
				.vobiz-agent-attendance-title { color: #333333; font-size: 10px; font-weight: 900; letter-spacing: .02em; text-transform: uppercase; }
				.vobiz-agent-status { align-items: center; display: inline-flex; gap: 6px; }
				.vobiz-agent-status-dot { border-radius: 50%; height: 8px; width: 8px; }
				.vobiz-agent-status-dot.online { background: #10b981; }
				.vobiz-agent-status-dot.break { background: #f59e0b; }
				.vobiz-agent-status-dot.offline { background: #94a3b8; }
				.vobiz-agent-status-text { color: #0f172a; font-size: 16px; font-weight: 900; line-height: 1.1; }
				.vobiz-agent-status-times { display: grid; gap: 6px; }
				.vobiz-agent-status-times div { align-items: center; color: #64748b; display: flex; font-size: 12px; gap: 8px; justify-content: space-between; line-height: 1.2; white-space: nowrap; }
				.vobiz-agent-status-times b { color: #0f172a; font-weight: 900; }
				.vobiz-agent-status-times .online b { color: #059669; }
				.vobiz-agent-status-times .offline b { color: #64748b; }
				.vobiz-agent-details { background: #fbfdfc; border-top: 1px solid #e8eee9; display: grid; gap: 18px; grid-template-columns: minmax(280px, .75fr) minmax(360px, 1.1fr) minmax(260px, .8fr); padding: 18px 20px 20px; }
				.vobiz-agent-details-panel { min-width: 0; }
				.vobiz-agent-details-panel.full { grid-column: 1 / -1; }
				.vobiz-agent-details-panel h4 { color: #0f172a; font-size: 12px; font-weight: 900; letter-spacing: .02em; margin: 0 0 12px; text-transform: uppercase; }
				.vobiz-agent-attendance-full { background: #fff; border: 1px solid #e5edf5; border-radius: 8px; box-shadow: 0 1px 3px rgba(15, 23, 42, .04); padding: 16px; }
				.vobiz-agent-attendance-head { align-items: center; display: flex; gap: 12px; justify-content: space-between; margin-bottom: 12px; }
				.vobiz-agent-attendance-head h4 { margin: 0; }
				.vobiz-agent-attendance-log-btn { align-items: center; background: #f8fafc; border: 1px solid #d9e2ee; border-radius: 7px; color: #334155; display: inline-flex; font-size: 12px; font-weight: 800; gap: 7px; height: 30px; padding: 0 12px; }
				.vobiz-agent-attendance-log-btn:hover { background: #fff; border-color: #cbd5e1; color: #0f172a; }
				.vobiz-agent-attendance-summary { display: grid; gap: 16px; grid-template-columns: repeat(4, minmax(160px, 1fr)); margin-bottom: 18px; }
				.vobiz-agent-attendance-box { background: #fff; border: 1px solid #dbe5f0; border-radius: 10px; box-shadow: 0 1px 2px rgba(15, 23, 42, .03); min-height: 80px; padding: 16px 18px; }
				.vobiz-agent-attendance-box span { color: #71829d; display: block; font-size: 10px; font-weight: 900; letter-spacing: .02em; line-height: 1.1; margin-bottom: 9px; text-transform: uppercase; }
				.vobiz-agent-attendance-box b { color: #0f172a; display: block; font-size: 20px; font-weight: 900; line-height: 1.1; }
				.vobiz-agent-attendance-box.online b { color: #009b7d; }
				.vobiz-agent-attendance-box.break b { color: #d97706; }
				.vobiz-agent-attendance-box.offline b { color: #0f172a; }
				.vobiz-agent-break-history { margin-top: 2px; }
				.vobiz-agent-break-history h5 { color: #92400e; font-size: 11px; font-weight: 900; letter-spacing: .02em; margin: 0 0 10px; text-transform: uppercase; }
				.vobiz-agent-break-records { align-items: stretch; display: grid; gap: 16px; grid-template-columns: repeat(3, minmax(260px, 1fr)); }
				.vobiz-agent-break-record { background: #fff; border: 1px solid #dbe5f0; border-radius: 14px; box-shadow: 0 1px 2px rgba(15, 23, 42, .04); display: grid; gap: 16px; min-height: 142px; padding: 20px; }
				.vobiz-agent-break-card-head { align-items: center; display: flex; justify-content: space-between; min-width: 0; }
				.vobiz-agent-break-card-title { align-items: center; background: #fff8e6; border: 1px solid #fde68a; border-radius: 7px; color: #b45309; display: inline-flex; font-size: 10px; font-weight: 900; gap: 8px; line-height: 1; min-height: 28px; padding: 0 12px; text-transform: uppercase; }
				.vobiz-agent-break-card-title::before { background: #f7c965; border-radius: 50%; content: ""; height: 8px; width: 8px; }
				.vobiz-agent-break-date { color: #8b99b2; font-size: 12px; font-weight: 500; white-space: nowrap; }
				.vobiz-agent-break-divider { background: #edf2f7; height: 1px; width: 100%; }
				.vobiz-agent-break-card-body { align-items: end; display: grid; gap: 14px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
				.vobiz-agent-break-field span { color: #8b99b2; display: block; font-size: 10px; font-weight: 900; letter-spacing: .02em; line-height: 1.1; margin-bottom: 6px; text-transform: uppercase; }
				.vobiz-agent-break-field b { color: #0f172a; display: block; font-size: 15px; font-weight: 900; line-height: 1.15; }
				.vobiz-agent-break-field.total { text-align: right; }
				.vobiz-agent-break-field.total b { color: #d97706; }
				.vobiz-agent-break-record.in-progress { background: #fffaf0; border-color: #facc15; box-shadow: 0 1px 2px rgba(180, 83, 9, .08); }
				.vobiz-agent-break-record.in-progress .vobiz-agent-break-card-title { background: #fff; border-color: #facc15; color: #b45309; }
				.vobiz-agent-break-record.in-progress .vobiz-agent-break-date { color: #d97706; }
				.vobiz-agent-break-record.in-progress .vobiz-agent-break-divider { background: #fde68a; }
				.vobiz-agent-break-record.in-progress .vobiz-agent-break-field.end b { color: #b45309; font-style: italic; }
				.vobiz-agent-break-record.in-progress .vobiz-agent-break-field.total b { color: #b45309; }
				.vobiz-agent-break-record.over-break { background: #fff7ed; border-color: #fb923c; box-shadow: 0 1px 2px rgba(194, 65, 12, .1); }
				.vobiz-agent-break-record.over-break .vobiz-agent-break-card-title { background: #fff1f2; border-color: #fecdd3; color: #be123c; }
				.vobiz-agent-break-record.over-break .vobiz-agent-break-card-title::before { background: #f43f5e; }
				.vobiz-agent-break-record.over-break .vobiz-agent-break-field.total b { color: #be123c; }
				.vobiz-agent-break-warning { color: #be123c; display: block; font-size: 10px; font-weight: 900; margin-top: 5px; text-transform: uppercase; }
				.vobiz-agent-activity-log { border-top: 1px solid #edf2f7; margin-top: 18px; padding-top: 16px; }
				.vobiz-agent-activity-log h5 { color: #334155; font-size: 11px; font-weight: 900; letter-spacing: .02em; margin: 0 0 10px; text-transform: uppercase; }
				.vobiz-agent-activity-list { display: grid; gap: 10px; }
				.vobiz-agent-activity-row { align-items: center; background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; display: grid; gap: 14px; grid-template-columns: minmax(130px, .8fr) repeat(3, minmax(100px, 1fr)); min-height: 58px; padding: 12px 14px; }
				.vobiz-agent-activity-row.current { background: #f8fafc; border-color: #cbd5e1; }
				.vobiz-agent-activity-status { align-items: center; display: inline-flex; font-size: 12px; font-weight: 900; gap: 8px; text-transform: uppercase; }
				.vobiz-agent-activity-dot { border-radius: 50%; height: 8px; width: 8px; }
				.vobiz-agent-activity-dot.online { background: #10b981; }
				.vobiz-agent-activity-dot.break { background: #f59e0b; }
				.vobiz-agent-activity-dot.offline { background: #94a3b8; }
				.vobiz-agent-activity-field span { color: #8b99b2; display: block; font-size: 10px; font-weight: 900; letter-spacing: .02em; line-height: 1.1; margin-bottom: 5px; text-transform: uppercase; }
				.vobiz-agent-activity-field b { color: #0f172a; display: block; font-size: 13px; font-weight: 900; line-height: 1.15; }
				.vobiz-agent-activity-field.total b { color: #334155; }
				.vobiz-agent-details-head { align-items: center; display: flex; flex-wrap: wrap; gap: 10px; justify-content: space-between; margin-bottom: 12px; }
				.vobiz-agent-details-head h4 { margin: 0; }
				.vobiz-agent-call-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 10px; }
				.vobiz-agent-call-actions span { color: #64748b; font-size: 12px; font-weight: 800; }
				.vobiz-agent-call-actions select { appearance: auto; background-color: #fff; border: 1px solid #d9e2ee; border-radius: 6px; color: #0f172a; font-size: 12px; height: 30px; min-width: 170px; padding: 4px 8px; }
				.vobiz-agent-call-filter { align-items: center; background: #fff; border: 1px solid #d9e2ee; border-radius: 8px; box-shadow: 0 1px 3px rgba(15, 23, 42, .06); display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; padding: 14px 16px; }
				.vobiz-agent-call-filter-label { color: #334155; font-size: 14px; font-weight: 500; }
				.vobiz-agent-call-status-pill { background: #f1f5f9; border: 0; border-radius: 999px; color: #0f172a; font-size: 13px; font-weight: 500; min-height: 32px; padding: 6px 18px; }
				.vobiz-agent-call-status-pill.active { background: #e8f1ff; color: #0f172a; }
				.vobiz-agent-call-status-pill.unique.active { background: #ccfbf1; color: #0f766e; }
				.vobiz-agent-call-status-pill.connected.active { background: #dcfce7; color: #059669; }
				.vobiz-agent-call-status-pill.busy.active { background: #f3e8ff; color: #9333ea; }
				.vobiz-agent-call-status-pill.missed.active, .vobiz-agent-call-status-pill.failed.active, .vobiz-agent-call-status-pill.cancelled.active { background: #fff1f2; color: #e11d48; }
				.vobiz-agent-call-status-pill.no_answer.active { background: #eef2ff; color: #4f46e5; }
				.vobiz-agent-call-pagination { align-items: center; display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
				.vobiz-agent-call-pagination span { color: #64748b; font-size: 12px; font-weight: 800; margin-right: 4px; }
				.vobiz-agent-detail-list { display: grid; gap: 8px; }
				.vobiz-agent-detail-row { align-items: center; border-bottom: 1px solid #edf2ee; color: #475569; display: flex; font-size: 12px; gap: 10px; justify-content: space-between; min-height: 28px; padding-bottom: 8px; }
				.vobiz-agent-detail-row:last-child { border-bottom: 0; padding-bottom: 0; }
				.vobiz-agent-detail-row span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
				.vobiz-agent-detail-row strong { color: #0f172a; flex: 0 0 auto; font-weight: 900; text-align: right; }
				.vobiz-agent-detail-grid { display: grid; gap: 10px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
				.vobiz-agent-detail-metric { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; min-height: 74px; padding: 12px; }
				.vobiz-agent-detail-metric span { color: #64748b; display: block; font-size: 10px; font-weight: 900; text-transform: uppercase; }
				.vobiz-agent-detail-metric strong { color: #0f172a; display: block; font-size: 20px; font-weight: 900; line-height: 1.1; margin-top: 7px; overflow-wrap: anywhere; }
				.vobiz-agent-detail-bars { display: grid; gap: 10px; }
				.vobiz-agent-detail-bar { display: grid; gap: 6px; }
				.vobiz-agent-detail-bar-head { align-items: center; color: #475569; display: flex; font-size: 12px; font-weight: 800; justify-content: space-between; }
				.vobiz-agent-detail-bar-track { background: #e8eef2; border-radius: 999px; height: 9px; overflow: hidden; }
				.vobiz-agent-detail-bar-fill { border-radius: 999px; height: 100%; min-width: 2px; }
				.vobiz-agent-call-table-wrap { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; overflow-x: auto; }
				.vobiz-agent-call-table { margin: 0; }
				.vobiz-agent-call-table th { background: #fafbf9; color: #667085; font-size: 11px; font-weight: 800; white-space: nowrap; }
				.vobiz-agent-call-table td { color: #344054; font-size: 12px; vertical-align: middle; white-space: nowrap; }
				.vobiz-agent-overview { display: grid; gap: 16px; grid-template-columns: repeat(6, minmax(0, 1fr)); margin: 4px 0 32px; }
				.vobiz-agent-stat-card { align-items: center; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; box-shadow: 0 1px 4px rgba(15, 23, 42, .06); display: flex; justify-content: space-between; min-height: 96px; padding: 20px; }
				.vobiz-agent-stat-card span { color: #475569; display: block; font-size: 15px; font-weight: 700; }
				.vobiz-agent-stat-card strong { color: #020617; display: block; font-size: 28px; font-weight: 900; line-height: 1; margin-top: 8px; }
				.vobiz-agent-stat-icon { align-items: center; border-radius: 8px; display: flex; font-size: 22px; height: 48px; justify-content: center; width: 48px; }
				.vobiz-agent-stat-icon.active { background: #d1fae5; color: #059669; }
				.vobiz-agent-stat-icon.calls { background: #eef2ff; color: #4f46e5; }
				.vobiz-agent-stat-icon.answer { background: #fffbeb; color: #d97706; }
				.vobiz-agent-stat-icon.rejected { background: #fff1f2; color: #e11d48; }
				.vobiz-agent-stat-icon.over-break { background: #fff1f2; color: #be123c; }
				.vobiz-agent-toolbar { align-items: center; background: #fff; border: 1px solid #e9edf3; border-radius: 10px; box-shadow: 0 1px 5px rgba(15, 23, 42, .05); display: flex; gap: 20px; justify-content: space-between; margin: 0 0 18px; min-height: 72px; padding: 16px; }
				.vobiz-agent-status-filter { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; }
				.vobiz-agent-status-filter > span { color: #334155; font-size: 14px; font-weight: 800; margin-right: 8px; }
				.vobiz-agent-status-filter button { align-items: center; background: #f1f5f9; border: 0; border-radius: 8px; color: #475569; display: inline-flex; font-size: 12px; font-weight: 800; gap: 7px; height: 30px; padding: 0 14px; }
				.vobiz-agent-status-filter button.active { background: #0b1328; color: #fff; padding-left: 16px; padding-right: 16px; }
				.vobiz-agent-status-filter button i { color: #10b981; font-size: 8px; }
				.vobiz-agent-status-filter button[data-agent-filter="break"] i { color: #f59e0b; }
				.vobiz-agent-status-filter button[data-agent-filter="offline"] i { color: #94a3b8; }
				.vobiz-agent-toolbar-actions { align-items: center; display: flex; flex-wrap: nowrap; gap: 14px; justify-content: flex-end; }
				.vobiz-agent-search { position: relative; width: 245px; }
				.vobiz-agent-search i { color: #8aa0bf; font-size: 14px; left: 13px; pointer-events: none; position: absolute; top: 50%; transform: translateY(-50%); z-index: 1; }
				.vobiz-agent-search input { appearance: none; background: #fbfdff; border: 1px solid #d9e2ee; border-radius: 6px; box-shadow: none !important; color: #334155; font-size: 14px; height: 38px; line-height: 20px; padding: 8px 12px 8px 36px; transition: border-color .15s ease, background .15s ease; }
				.vobiz-agent-search input::placeholder { color: #94a3b8; }
				.vobiz-agent-search input:focus { background: #fff; border-color: #c7d2e2; box-shadow: none !important; outline: 0; }
				.vobiz-agent-sort { align-items: center; display: flex; gap: 9px; }
				.vobiz-agent-sort span { color: #64748b; font-size: 14px; white-space: nowrap; }
				.vobiz-agent-sort span i { color: #94a3b8; margin-right: 7px; }
				.vobiz-agent-sort select { appearance: auto; background-color: #fff; border: 1px solid #d9e2ee; border-radius: 6px; box-shadow: none !important; color: #0f172a; font-size: 14px; height: 38px; line-height: 20px; min-width: 175px; padding: 8px 12px; transition: border-color .15s ease; }
				.vobiz-agent-sort select:focus { border-color: #c7d2e2; box-shadow: none !important; outline: 0; }
				.vobiz-agent-page-size { align-items: center; color: #64748b; display: flex; font-size: 14px; gap: 7px; white-space: nowrap; }
				.vobiz-agent-page-size select { height: 38px; min-width: 72px; }
				.vobiz-agent-pagination { align-items: center; display: flex; gap: 8px; justify-content: space-between; padding-top: 14px; }
				.vobiz-agent-pagination-actions { display: flex; gap: 6px; }
				.vobiz-agent-empty { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; color: #64748b; font-size: 13px; font-weight: 700; padding: 24px; text-align: center; }
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
				.vobiz-selected-call-pagination { align-items: center; display: flex; gap: 8px; justify-content: flex-end; padding-top: 12px; }
				.vobiz-selected-call-pagination span { color: #64748b; font-size: 12px; font-weight: 800; margin-right: 4px; }
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
					.vobiz-agent-break-records { grid-template-columns: repeat(2, minmax(260px, 1fr)); }
					.vobiz-filter-action { justify-content: flex-start; }
					.vobiz-pie-chart { grid-template-columns: minmax(150px, 210px) minmax(0, 1fr); }
					.vobiz-agent-overview { grid-template-columns: repeat(2, minmax(0, 1fr)); }
					.vobiz-agent-details { grid-template-columns: 1fr; }
					.vobiz-agent-toolbar { align-items: stretch; flex-direction: column; }
					.vobiz-agent-toolbar-actions { flex-wrap: wrap; justify-content: flex-start; }
					.vobiz-agent-card-inner { align-items: stretch; grid-template-columns: 1fr; }
					.vobiz-agent-attendance { border-left: 0; border-top: 1px solid #f1f5f9; padding-left: 0; padding-top: 14px; }
				}
				@media (max-width: 640px) {
					.vobiz-agent-card-inner { gap: 16px; padding: 16px; }
					.vobiz-agent-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
					.vobiz-agent-detail-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
					.vobiz-agent-attendance-summary, .vobiz-agent-break-records { grid-template-columns: 1fr; }
					.vobiz-agent-attendance-head { align-items: flex-start; flex-direction: column; }
					.vobiz-agent-activity-row { grid-template-columns: 1fr; }
					.vobiz-agent-meter-head { align-items: flex-start; flex-direction: column; gap: 6px; }
					.vobiz-agent-overview { grid-template-columns: 1fr; }
					.vobiz-pie-chart { grid-template-columns: 1fr; justify-items: center; }
					.vobiz-pie-legend { width: 100%; }
					.vobiz-pie-svg { height: 210px; }
					.vobiz-agent-search, .vobiz-agent-sort, .vobiz-agent-sort select, .vobiz-agent-page-size { width: 100%; }
				}
			</style>
		`);
	}

	bind() {
		const $main = this.page.main;
		$main.on('click', '[data-action="open-console"]', () => frappe.set_route('vobiz-agent-console'));
		$main.on('click', '[data-action="refresh"]', () => this.load());
		$main.on('click', '[data-action="clear-filters"]', () => this.clear_filters());
		$main.on('click', '[data-action="show-call-list"]', (e) => this.show_call_list($(e.currentTarget).data('status-filter')));
		$main.on('keydown', '[data-action="show-call-list"]', (e) => {
			if (e.key === 'Enter' || e.key === ' ') {
				e.preventDefault();
				this.show_call_list($(e.currentTarget).data('status-filter'));
			}
		});
		$main.on('click', '[data-action="selected-call-page"]', (e) => {
			e.preventDefault();
			this.load_selected_calls(this.state.selected_call_status, Number($(e.currentTarget).data('page')) || 0);
		});
		$main.on('click', '[data-action="load-calls"]', () => this.load_calls(true));
		$main.on('click', '[data-action="load-more-calls"]', () => this.load_calls(false));
		$main.on('click', '[data-action="play-recording"]', (e) => this.play_recording($(e.currentTarget)));
		$main.on('click', '[data-action="stop-recording"]', (e) => this.stop_recording($(e.currentTarget)));
		$main.on('mouseenter focus', '.vobiz-pie-slice', (e) => this.show_pie_tooltip($(e.currentTarget), e));
		$main.on('mousemove', '.vobiz-pie-slice', (e) => this.move_pie_tooltip($(e.currentTarget), e));
		$main.on('mouseleave blur', '.vobiz-pie-slice', (e) => this.hide_pie_tooltip($(e.currentTarget)));
		$main.on('click', '[data-agent-filter]', (e) => this.set_agent_status_filter($(e.currentTarget).data('agent-filter')));
		$main.on('click', '[data-action="toggle-agent-details"]', (e) => {
			if ($(e.target).closest('a, button, select, input, audio, [data-recording-player], .vobiz-agent-details').length) return;
			this.toggle_agent_details($(e.currentTarget).data('agent-user'));
		});
		$main.on('keydown', '[data-action="toggle-agent-details"]', (e) => {
			if (e.key === 'Enter' || e.key === ' ') {
				e.preventDefault();
				this.toggle_agent_details($(e.currentTarget).data('agent-user'));
			}
		});
		$main.on('click', '[data-action="agent-call-status"]', (e) => {
			e.preventDefault();
			this.set_agent_call_status_filter($(e.currentTarget).data('agent-user'), $(e.currentTarget).data('status-filter'));
		});
		$main.on('click', '[data-action="agent-call-page"]', (e) => {
			e.preventDefault();
			this.set_agent_call_page($(e.currentTarget).data('agent-user'), Number($(e.currentTarget).data('page')) || 0);
		});
		$main.on('click', '[data-action="toggle-attendance-log"]', (e) => {
			e.preventDefault();
			e.stopPropagation();
			this.toggle_attendance_log($(e.currentTarget).data('agent-user'));
		});
		$main.on('click', '[data-action="toggle-checkbox-dropdown"]', (e) => {
			e.preventDefault();
			e.stopPropagation();
			this.toggle_checkbox_dropdown($(e.currentTarget).data('target-role'));
		});
		$main.on('click', '.vobiz-checkbox-dropdown-menu', (e) => e.stopPropagation());
		$(document).off('click.vobiz-agent-analytics').on('click.vobiz-agent-analytics', () => this.close_checkbox_dropdowns());
		$main.on('input', '[data-role="agent-search"]', (e) => {
			this.state.agent_search = ($(e.currentTarget).val() || '').trim();
			this.state.agent_page = 1;
			this.render_agent_chart(this.state.agents || []);
		});
		$main.on('change', '[data-role="agent-sort"]', (e) => {
			this.state.agent_sort = ($(e.currentTarget).val() || 'total').trim();
			this.state.agent_page = 1;
			this.render_agent_chart(this.state.agents || []);
		});
		$main.on('change', '[data-role="agent-page-size"]', (e) => {
			this.state.agent_page_size = Number($(e.currentTarget).val()) || 10;
			this.state.agent_page = 1;
			this.render_agent_chart(this.state.agents || []);
		});
		$main.on('click', '[data-action="agent-page"]', (e) => {
			e.preventDefault();
			this.state.agent_page = Math.max(1, Number($(e.currentTarget).data('page')) || 1);
			this.render_agent_chart(this.state.agents || []);
		});
		$main.on('change', '[data-role="from-date"], [data-role="to-date"], [data-role="status-filter"], [data-role="queue-source"], [data-role="department"]', () => {
			this.state.agent_page = 1;
			this.schedule_load();
		});
		$main.on('input', '.vobiz-checkbox-search input', (e) => {
			const dropdown_role = $(e.currentTarget).closest('.vobiz-checkbox-dropdown').data('role') || '';
			const role = dropdown_role.replace(/-dropdown$/, '');
			this.filter_checkbox_dropdown_options(role, ($(e.currentTarget).val() || '').trim());
		});
		$main.on('change', '[data-filter-role="agent"], [data-filter-role="team"]', (e) => {
			const role = $(e.currentTarget).data('filter-role');
			this.update_checkbox_dropdown_label(
				role,
				this.selected_values(role),
				this.checkbox_empty_label(role)
			);
			this.state.agent_page = 1;
			this.schedule_load();
		});
		$(document)
			.off('vobiz_availability_changed.vobiz-agent-analytics')
			.on('vobiz_availability_changed.vobiz-agent-analytics', () => this.schedule_load(true));
		$(document)
			.off('visibilitychange.vobiz-agent-analytics')
			.on('visibilitychange.vobiz-agent-analytics', () => {
				if (document.visibilityState === 'visible') {
					this.schedule_load(true);
				}
			});
		$(window)
			.off('focus.vobiz-agent-analytics')
			.on('focus.vobiz-agent-analytics', () => this.schedule_load(true));
	}

	toggle_checkbox_dropdown(role) {
		const $dropdown = this.page.main.find(`[data-role="${role}-dropdown"]`);
		const is_open = $dropdown.hasClass('open');
		this.close_checkbox_dropdowns();
		if (!is_open && !$dropdown.find('[data-action="toggle-checkbox-dropdown"]').prop('disabled')) {
			$dropdown.addClass('open');
		}
	}

	close_checkbox_dropdowns() {
		this.page.main.find('.vobiz-checkbox-dropdown').removeClass('open');
	}

	checkbox_empty_label(role) {
		if (role === 'agent') return __('All Agents');
		return __('All Teams');
	}

	set_agent_status_filter(filter) {
		this.state.agent_status_filter = filter || 'all';
		this.state.agent_page = 1;
		this.render_agent_chart(this.state.agents || []);
	}

	toggle_agent_details(agent_user) {
		agent_user = (agent_user || '').trim();
		this.state.selected_agent_user = this.state.selected_agent_user === agent_user ? '' : agent_user;
		this.render_agent_chart(this.state.agents || []);
		if (this.state.selected_agent_user) {
			this.load_agent_calls(this.state.selected_agent_user, true);
		}
	}

	toggle_attendance_log(agent_user) {
		agent_user = (agent_user || '').trim();
		if (!agent_user) return;
		this.state.attendance_log_visible = Object.assign({}, this.state.attendance_log_visible || {}, {
			[agent_user]: !Boolean((this.state.attendance_log_visible || {})[agent_user])
		});
		this.render_agent_chart(this.state.agents || []);
	}

	set_agent_call_status_filter(agent_user, status_filter) {
		agent_user = (agent_user || '').trim();
		const details = this.state.agent_calls[agent_user];
		if (!details) return;
		this.state.agent_calls[agent_user] = Object.assign({}, details, {
			status_filter: status_filter || 'total',
			page: 0
		});
		this.load_agent_calls(agent_user, true);
	}

	set_agent_call_page(agent_user, page) {
		agent_user = (agent_user || '').trim();
		const details = this.state.agent_calls[agent_user];
		if (!details || details.loading) return;
		this.state.agent_calls[agent_user] = Object.assign({}, details, {
			page: Math.max(0, page || 0)
		});
		this.load_agent_calls(agent_user, true);
	}

	agent_call_filters(agent_user, offset = 0, status_filter = 'total', limit = 25) {
		const filters = Object.assign({}, this.filters(), {
			include_calls: 1,
			calls_only: 1,
			call_limit: limit,
			call_offset: offset,
			status_filter: status_filter === 'unique' ? 'total' : (status_filter || 'total'),
			unique_only: status_filter === 'unique' ? 1 : 0
		});
		const queueSource = filters.queue_source || this.state.queue_source || 'CRM Lead and Patient';
		if (['CRM Lead', 'Discontinued'].includes(queueSource)) {
			filters.lead_owner = JSON.stringify([agent_user]);
			filters.agent_user = '';
		} else {
			filters.agent_user = agent_user === __('Unassigned') ? '' : agent_user;
		}
		return filters;
	}

	load_agent_calls(agent_user, reset = false) {
		agent_user = (agent_user || '').trim();
		if (!agent_user) return;
		const current = this.state.agent_calls[agent_user] || {
			calls: [],
			offset: 0,
			total: 0,
			has_more: false,
			loading: false,
			status_filter: 'total',
			page: 0,
			limit: 25
		};
		if (current.loading) return;
		const page = reset ? (current.page || 0) : current.page || 0;
		const limit = current.limit || 25;
		const offset = page * limit;
		const requestId = Date.now();
		this.state.agent_calls[agent_user] = Object.assign({}, current, {
			loading: true,
			request_id: requestId,
			calls: []
		});
		this.render_agent_chart(this.state.agents || []);
		const request = frappe.call({
			method: 'vobiz_click_to_call.api.console.get_analytics',
			args: this.agent_call_filters(agent_user, offset, current.status_filter || 'total', limit),
			freeze: false
		});
		request.then((r) => {
			const data = r.message || {};
			const latest = this.state.agent_calls[agent_user] || {};
			if (latest.request_id !== requestId) return;
			const fetched = data.calls || [];
			this.state.agent_calls[agent_user] = Object.assign({}, latest, {
				calls: fetched,
				offset: (data.call_offset || 0) + fetched.length,
				total: data.matching_call_count || fetched.length,
				has_more: Boolean(data.has_more_calls),
				page,
				limit,
				loading: false
			});
			this.render_agent_chart(this.state.agents || []);
		});
		const onFail = () => {
			const latest = this.state.agent_calls[agent_user] || {};
			this.state.agent_calls[agent_user] = Object.assign({}, latest, {
				loading: false,
				error: __('Unable to load agent calls.')
			});
			this.render_agent_chart(this.state.agents || []);
		};
		if (request.fail) {
			request.fail(onFail);
		} else if (request.catch) {
			request.catch(onFail);
		}
	}

	on_page_show() {
		if (!this.initialized) {
			this.load();
		}
	}

	schedule_load(preserve_agent_selection = false) {
		if (preserve_agent_selection) {
			this.preserve_agent_selection_on_load = true;
		}
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
			queue_source: 'CRM Lead and Patient',
			agent_user: '',
			team: [],
			department: '',
			selected_agent_user: '',
			agent_page: 1,
			agent_calls: {},
			attendance_log_visible: {},
			selected_call_status: '',
			selected_call_page: 0,
			selected_call_has_more: false
		});
		this.page.main.find('[data-role="from-date"]').val(today);
		this.page.main.find('[data-role="to-date"]').val(today);
		this.page.main.find('[data-role="status-filter"]').val('total');
		this.page.main.find('[data-role="queue-source"]').val('CRM Lead');
		this.set_checkbox_dropdown_selection('agent', []);
		this.set_checkbox_dropdown_selection('team', []);
		this.page.main.find('[data-role="department"]').val('');
		this.load();
	}

	show_call_list(status_filter) {
		clearTimeout(this.load_timer);
		const selected_status = (status_filter || 'total').trim();
		const effective_status = selected_status === 'unique' ? 'total' : selected_status;
		this.state.status_filter = effective_status;
		this.state.selected_call_status = selected_status;
		this.state.selected_call_page = 0;
		this.state.selected_call_has_more = false;
		this.page.main.find('[data-role="status-filter"]').val(effective_status);
		const request_id = ++this.request_id;
		const filters = this.filters();
		this.loading = true;
		this.calls_loaded = false;
		this.calls_offset = 0;
		this.has_more_calls = false;
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
					team: this.filter_values(data.team || filters.team),
					department: data.department || filters.department || ''
				});
			this.render_filters(data);
			this.render_kpis(data.summary || {}, data);
			this.render_charts(data);
			this.initialized = true;
			this.load_selected_calls(selected_status, 0);
		}).always(() => {
			if (request_id === this.request_id) {
				this.loading = false;
			}
		});
	}

	load_selected_calls(status_filter, page = 0) {
		const selected_status = (status_filter || this.state.status_filter || 'total').trim();
		const unique_only = selected_status === 'unique';
		const page_number = Math.max(0, Number(page) || 0);
		const limit = this.state.selected_call_limit || 25;
		const offset = page_number * limit;
		const filters = Object.assign({}, this.filters(), { status_filter: unique_only ? 'total' : selected_status });
		const label = this.call_list_label(selected_status);
		this.state.selected_call_status = selected_status;
		this.state.selected_call_page = page_number;
		this.page.main.find('[data-role="selected-call-section"]').removeClass('hide');
		this.page.main.find('[data-role="selected-call-title"]').text(__('{0} List', [label]));
		this.page.main.find('[data-role="selected-calls"]').html(`
			<tr>
				<td colspan="11" class="text-muted text-center">${__('Loading {0}...', [label.toLowerCase()])}</td>
			</tr>
		`);
		this.page.main.find('[data-role="selected-calls-note"]').text(__('Loading...'));
		this.page.main.find('[data-role="selected-call-pagination"]').html('');
		frappe.call({
			method: 'vobiz_click_to_call.api.console.get_analytics',
			args: Object.assign({}, filters, {
				include_calls: 1,
				calls_only: 1,
				call_limit: limit,
				call_offset: offset,
				unique_only: unique_only ? 1 : 0
			}),
			freeze: false
		}).then((r) => {
			const data = r.message || {};
			this.state.selected_call_has_more = Boolean(data.has_more_calls);
			this.render_selected_calls(data.calls || [], data, selected_status, page_number, limit);
		});
	}

	filters() {
		return {
			from_date: (this.page.main.find('[data-role="from-date"]').val() || frappe.datetime.get_today()).trim(),
			to_date: (this.page.main.find('[data-role="to-date"]').val() || frappe.datetime.get_today()).trim(),
			status_filter: (this.page.main.find('[data-role="status-filter"]').val() || 'total').trim(),
			queue_source: (this.page.main.find('[data-role="queue-source"]').val() || this.state.queue_source || 'CRM Lead and Patient').trim(),
			agent_user: JSON.stringify(this.selected_values('agent')),
			team: JSON.stringify(this.selected_values('team')),
			department: (this.page.main.find('[data-role="department"]').val() || '').trim()
		};
	}

	selected_values(role) {
		return this.page.main.find(`[data-filter-role="${role}"]:checked`).map((index, item) => $(item).val()).get();
	}

	filter_values(value) {
		if (Array.isArray(value)) {
			return value.map(item => String(item || '').trim()).filter(Boolean);
		}
		if (value == null || value === '') return [];
		try {
			const parsed = JSON.parse(value);
			if (Array.isArray(parsed)) {
				return parsed.map(item => String(item || '').trim()).filter(Boolean);
			}
		} catch (e) {
			// Keep compatibility with older single-value filter strings.
		}
		return String(value).split(',').map(item => item.trim()).filter(Boolean);
	}

	load() {
		const filters = this.filters();
		const preserve_agent_selection = Boolean(this.preserve_agent_selection_on_load);
		this.preserve_agent_selection_on_load = false;
		const selected_agent_user = preserve_agent_selection ? this.state.selected_agent_user : '';
		const agent_calls = preserve_agent_selection ? this.state.agent_calls : {};
		const attendance_log_visible = preserve_agent_selection ? (this.state.attendance_log_visible || {}) : {};
		const request_id = ++this.request_id;
		this.loading = true;
		this.calls_loaded = false;
		this.calls_offset = 0;
		this.has_more_calls = false;
		this.calls_request_id += 1;
		this.state.selected_agent_user = selected_agent_user;
		this.state.agent_calls = agent_calls;
		this.state.attendance_log_visible = attendance_log_visible;
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
				agent_user: this.filter_values(data.agent_user || filters.agent_user),
				team: this.filter_values(data.team || filters.team),
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
		this.render_checkbox_dropdown(
			'team',
			data.team_options || [],
			this.filter_values(data.team || this.state.team),
			__('All Teams'),
			{ searchable: true }
		);
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
		if (data.is_admin || data.is_team_leader) {
			const label = data.is_admin ? __('All Agents') : __('All Team Agents');
			this.render_checkbox_dropdown(
				'agent',
				agents,
				this.filter_values(data.agent_user || this.state.agent_user),
				label,
				{ searchable: true }
			);
		} else {
			const current = this.filter_values(data.agent_user || this.state.agent_user)[0] || agents[0] || '';
			this.render_checkbox_dropdown(
				'agent',
				current ? [current] : [],
				current ? [current] : [],
				__('My Calls')
			);
			this.page.main.find('[data-role="agent-dropdown"] [data-action="toggle-checkbox-dropdown"]').prop('disabled', true);
		}
		this.page.main.find('[data-role="range-label"]').text(`${frappe.datetime.str_to_user(data.from_date)} - ${frappe.datetime.str_to_user(data.to_date)}`);
	}

	render_checkbox_dropdown(role, options, selected, empty_label, config = {}) {
		const option_values = new Set(options);
		const selected_values = new Set(this.filter_values(selected).filter(value => option_values.has(value)));
		const $dropdown = this.page.main.find(`[data-role="${role}-dropdown"]`);
		const $button = $dropdown.find('[data-action="toggle-checkbox-dropdown"]');
		const $menu = this.page.main.find(`[data-role="${role}-menu"]`);
		if (!options.length) {
			$button.prop('disabled', true);
			$menu.html(`<div class="vobiz-checkbox-empty">${__('No options')}</div>`);
			this.update_checkbox_dropdown_label(role, [], empty_label);
			return;
		}
		$button.prop('disabled', false);
		const search = config.searchable ? `
			<div class="vobiz-checkbox-search">
				<input type="text" class="form-control input-sm" data-role="${this.escape(role)}-filter-search" placeholder="${this.escape(__('Search'))}">
			</div>
		` : '';
		$menu.html(search + options.map(value => {
			const checked = selected_values.has(value) ? ' checked' : '';
			return `
				<label class="vobiz-checkbox-option" title="${this.escape(value)}">
					<input type="checkbox" data-filter-role="${this.escape(role)}" value="${this.escape(value)}"${checked}>
					<span>${this.escape(value)}</span>
				</label>
			`;
		}).join(''));
		this.update_checkbox_dropdown_label(role, Array.from(selected_values), empty_label);
	}

	filter_checkbox_dropdown_options(role, query) {
		query = (query || '').toLowerCase();
		this.page.main.find(`[data-role="${role}-menu"] .vobiz-checkbox-option`).each((index, item) => {
			const text = ($(item).text() || '').toLowerCase();
			$(item).toggle(!query || text.includes(query));
		});
	}

	set_checkbox_dropdown_selection(role, values) {
		const selected = new Set(this.filter_values(values));
		this.page.main.find(`[data-filter-role="${role}"]`).each((index, item) => {
			$(item).prop('checked', selected.has($(item).val()));
		});
		this.update_checkbox_dropdown_label(
			role,
			Array.from(selected),
			this.checkbox_empty_label(role)
		);
	}

	update_checkbox_dropdown_label(role, selected, empty_label) {
		const values = this.filter_values(selected);
		const label = values.length === 0
			? empty_label
			: values.length === 1
				? values[0]
				: __('{0} selected', [values.length]);
		this.page.main.find(`[data-role="${role}-label"]`).text(label);
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
				calls_only: 1,
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
		this.page.main.find('[data-role="selected-call-section"]').addClass('hide');
		this.page.main.find('[data-role="selected-call-pagination"]').html('');
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
			{ label: __('Missed'), value: summary.missed || 0, note: `${summary.missed_rate || 0}% ${__('missed')}`, className: 'missed', status_filter: 'missed' },
			{ label: __('Connected Incoming'), value: summary.connected_inbound || 0, note: __('answered incoming calls'), className: 'connected-incoming', status_filter: 'connected_inbound' },
			{ label: __('Connected Outgoing'), value: summary.connected_outbound || 0, note: __('answered outgoing calls'), className: 'connected-outgoing', status_filter: 'connected_outbound' },
			{ label: __('Total Calls'), value: summary.total || 0, note: data.queue_source || '', className: 'total', status_filter: 'total' },
			{ label: __('Unique Calls'), value: summary.unique_calls || 0, note: __('repeat calls counted once'), className: 'unique', status_filter: 'unique' },
			{ label: __('Avg Talk Time'), value: summary.average_duration_label || '0s', note: __('connected calls only'), className: 'average' },
			{ label: __('Busy'), value: summary.busy || 0, note: __('busy outcomes'), className: 'busy', status_filter: 'busy' },
			{ label: __('No Answer'), value: summary.no_answer || 0, note: __('ring timeout'), className: 'no-answer', status_filter: 'no_answer' },
			{ label: __('Rejected Calls'), value: summary.rejected || summary.cancelled || 0, note: __('rejected outcomes'), className: 'rejected', status_filter: 'cancelled' }
		];
		this.page.main.find('[data-role="kpis"]').html(kpis.map(row => `
			<div class="vobiz-kpi ${this.escape(row.className)}" ${row.status_filter ? `data-action="show-call-list" data-status-filter="${this.escape(row.status_filter)}" role="button" tabindex="0"` : ''}>
				<span>${this.escape(row.label)}</span>
				<strong>${this.escape(String(row.value))}</strong>
				<small>${this.escape(row.note || '')}</small>
			</div>
		`).join(''));
	}

	render_calls(calls, append, data) {
		const html = calls.map(row => this.call_row_html(row)).join('');
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

	render_selected_calls(calls, data, status_filter, page = 0, limit = 25) {
		const label = this.call_list_label(status_filter);
		const html = calls.map(row => this.call_row_html(row, true)).join('');
		this.page.main.find('[data-role="selected-calls"]').html(
			html || `<tr><td colspan="11" class="text-muted text-center">${__('No {0} found for this filter.', [label.toLowerCase()])}</td></tr>`
		);
		const total = data && data.matching_call_count != null ? data.matching_call_count : calls.length;
		const start = total && calls.length ? (page * limit) + 1 : 0;
		const end = total && calls.length ? Math.min(total, (page * limit) + calls.length) : 0;
		const note = `${__('Showing')} ${start}-${end} ${__('of')} ${total} ${label.toLowerCase()}`;
		const hasPrevious = page > 0;
		const hasNext = Boolean(data && data.has_more_calls);
		this.page.main.find('[data-role="selected-calls-note"]').text(note);
		this.page.main.find('[data-role="selected-call-pagination"]').html(`
			<span>${this.escape(note)}</span>
			<button class="btn btn-default btn-xs" data-action="selected-call-page" data-page="${page - 1}" ${hasPrevious ? '' : 'disabled'}>${__('Previous')}</button>
			<button class="btn btn-default btn-xs" data-action="selected-call-page" data-page="${page + 1}" ${hasNext ? '' : 'disabled'}>${__('Next')}</button>
		`);
	}

	call_list_label(status_filter) {
		return {
			total: __('Total Calls'),
			connected: __('Connected Calls'),
			connected_inbound: __('Connected Incoming Calls'),
			connected_outbound: __('Connected Outgoing Calls'),
			missed: __('Missed Calls'),
			unique: __('Unique Calls'),
			busy: __('Busy Calls'),
			no_answer: __('No Answer Calls'),
			cancelled: __('Rejected Calls')
		}[status_filter || 'total'] || __('Calls');
	}

	call_row_html(row, include_attempts = false, srNo = null) {
		const route = row.reference_name ? `/app/${frappe.router.slug(row.reference_doctype || 'CRM Lead')}/${encodeURIComponent(row.reference_name)}` : '';
		return `
			<tr>
				${srNo == null ? '' : `<td>${this.escape(String(srNo))}</td>`}
				<td><a href="/app/vobiz-call-log/${this.escape(row.name || '')}"><code>${this.escape(row.name || '')}</code></a></td>
				<td>${this.escape(row.user || '')}</td>
				<td>${this.escape(row.reference_doctype || '')}</td>
				<td>${route ? `<a href="${route}"><code>${this.escape(row.reference_name || '')}</code></a>` : ''}</td>
				<td>${this.escape(row.customer_number || '')}</td>
				${include_attempts ? `<td>${this.escape(String(row.attempt_count || 1))}</td>` : ''}
				<td><span class="vobiz-status-pill" style="background:${this.bucket_fill(row.bucket)}; color:${this.bucket_color(row.bucket)}">${this.escape(row.bucket_label || row.status || '')}</span></td>
				<td>${this.escape(row.duration_label || '0s')}</td>
				<td>${this.escape(row.disposition || '')}</td>
				<td>${row.creation ? frappe.datetime.str_to_user(row.creation) : ''}</td>
				<td>${this.recording_button_html(row)}</td>
			</tr>
		`;
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
		this.render_status_pie_chart(data.summary || {});
		this.render_agent_chart(data.agents || []);
	}

	render_daily_chart(rows) {
		const max = Math.max(...rows.map(row => row.total || 0), 1);
		const axisMax = this.axis_max(max);
		const ticks = this.axis_ticks(axisMax);
		const chartWidth = this.page.main.find('[data-role="daily-chart"]').innerWidth() || 700;
		const plotWidth = Math.max(320, chartWidth - 52);
		this.page.main.find('[data-role="daily-chart"]').html(`
			<div class="vobiz-axis-chart">
				<div class="vobiz-y-axis">${ticks.map(value => `<span>${value}</span>`).join('')}</div>
				<div class="vobiz-plot">
					<div class="vobiz-plot-area">
						<div class="vobiz-grid-lines">${[0, 1, 2, 3].map(() => '<span></span>').join('')}</div>
						${this.daily_line_chart_html(rows, axisMax, plotWidth)}
					</div>
				</div>
			</div>
			${this.legend_html([
				{ label: __('Total Calls'), color: '#2f80ed' }
			])}
		`);
	}

	render_status_pie_chart(summary) {
		const busy = Number(summary.busy) || 0;
		const noAnswer = Number(summary.no_answer) || 0;
		const failed = Number(summary.failed) || 0;
		const cancelled = Number(summary.cancelled || summary.rejected) || 0;
		const missed = Math.max(0, (Number(summary.missed) || 0) - busy - noAnswer - failed - cancelled);
		const rows = [
			{ bucket: 'connected', label: __('Connected'), value: Number(summary.connected) || 0, color: '#10b981' },
			{ bucket: 'no_answer', label: __('No Answer'), value: noAnswer, color: '#f59e0b' },
			{ bucket: 'cancelled', label: __('Rejected'), value: cancelled, color: '#a855f7' },
			{ bucket: 'busy', label: __('Busy'), value: busy, color: '#3b82f6' },
			{ bucket: 'failed', label: __('Failed'), value: failed, color: '#ef4444' },
			{ bucket: 'missed', label: __('Missed'), value: missed, color: '#e2554f' },
			{ bucket: 'other', label: __('Other'), value: Number(summary.other) || 0, color: '#64748b' }
		].filter(row => row.value > 0);
		const total = rows.reduce((sum, row) => sum + row.value, 0);
		this.page.main.find('[data-role="status-mix-label"]').text(total ? `${total} ${__('calls')}` : __('No calls'));
		if (!total) {
			this.page.main.find('[data-role="status-pie-chart"]').html(`<div class="vobiz-pie-empty">${__('No call status data for this filter.')}</div>`);
			return;
		}
		let cursor = 0;
		const geometry = {
			cx: 115,
			cy: 115,
			radius: 104
		};
		const slices = rows.map(row => {
			const start = cursor;
			cursor += (row.value / total) * 100;
			return Object.assign({}, row, {
				percent: (row.value / total) * 100,
				startAngle: (start / 100) * 360 - 90,
				endAngle: (cursor / 100) * 360 - 90
			});
		});
		this.page.main.find('[data-role="status-pie-chart"]').html(`
			<div class="vobiz-pie-chart">
				<svg class="vobiz-pie-svg" viewBox="0 0 230 230" role="img" aria-label="${this.escape(__('Call Status Mix'))}">
					${slices.map(row => this.pie_slice_svg(row, geometry)).join('')}
				</svg>
				<div class="vobiz-pie-tooltip" data-role="pie-tooltip"></div>
				<div class="vobiz-pie-legend">
					${rows.map(row => {
						const percent = total ? ((row.value / total) * 100).toFixed(1) : '0.0';
						return `
							<div class="vobiz-pie-legend-item ${this.escape(row.bucket)}">
								<span>${this.escape(row.label)}</span>
								<strong>${this.escape(this.format_number(row.value))} (${percent}%)</strong>
							</div>
						`;
					}).join('')}
				</div>
			</div>
		`);
	}

	pie_slice_svg(row, geometry) {
		const d = this.pie_slice_path(geometry.cx, geometry.cy, geometry.radius, row.startAngle, row.endAngle);
		const title = `${row.label}: ${row.value} (${row.percent.toFixed(1)}%)`;
		return `
			<path class="vobiz-pie-slice" d="${this.escape(d)}" fill="${this.escape(row.color)}" tabindex="0" aria-label="${this.escape(title)}" data-label="${this.escape(row.label)}" data-value="${this.escape(this.format_number(row.value))}" data-percent="${this.escape(row.percent.toFixed(1))}" data-color="${this.escape(row.color)}"></path>
		`;
	}

	show_pie_tooltip($slice, event) {
		const $chart = $slice.closest('.vobiz-pie-chart');
		const $tooltip = $chart.find('[data-role="pie-tooltip"]');
		const color = $slice.data('color') || '#10b981';
		$tooltip.html(`
			<div class="vobiz-pie-tooltip-label">
				<i class="vobiz-pie-tooltip-dot" style="background:${this.escape(color)}"></i>
				<span>${this.escape($slice.data('label') || '')}</span>
			</div>
			<div>
				<span class="vobiz-pie-tooltip-value" style="color:${this.escape(color)}">${this.escape($slice.data('value') || '0')}</span>
				<span class="vobiz-pie-tooltip-percent">${this.escape($slice.data('percent') || '0.0')}%</span>
			</div>
		`).addClass('visible');
		this.move_pie_tooltip($slice, event);
	}

	move_pie_tooltip($slice, event) {
		const $chart = $slice.closest('.vobiz-pie-chart');
		const $tooltip = $chart.find('[data-role="pie-tooltip"]');
		if (!$tooltip.length) return;
		const chartOffset = $chart.offset();
		const chartRect = $chart.get(0).getBoundingClientRect();
		const sliceRect = $slice.get(0).getBoundingClientRect();
		const x = event && event.pageX ? event.pageX - chartOffset.left : (sliceRect.left - chartRect.left) + (sliceRect.width / 2);
		const y = event && event.pageY ? event.pageY - chartOffset.top : (sliceRect.top - chartRect.top) + 18;
		$tooltip.css({
			left: `${Math.max(72, Math.min($chart.outerWidth() - 72, x))}px`,
			top: `${Math.max(58, y)}px`
		});
	}

	hide_pie_tooltip($slice) {
		$slice.closest('.vobiz-pie-chart').find('[data-role="pie-tooltip"]').removeClass('visible');
	}

	pie_slice_path(cx, cy, radius, startAngle, endAngle) {
		const start = this.pie_point(cx, cy, radius, startAngle);
		const end = this.pie_point(cx, cy, radius, endAngle);
		const largeArc = endAngle - startAngle > 180 ? 1 : 0;
		return [
			`M ${cx} ${cy}`,
			`L ${start.x.toFixed(2)} ${start.y.toFixed(2)}`,
			`A ${radius} ${radius} 0 ${largeArc} 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`,
			'Z'
		].join(' ');
	}

	pie_point(cx, cy, radius, angle) {
		const radians = (angle * Math.PI) / 180;
		return {
			x: cx + radius * Math.cos(radians),
			y: cy + radius * Math.sin(radians)
		};
	}

	render_agent_chart(agents) {
		this.state.agents = agents || [];
		this.render_agent_overview(this.state.agents);
		this.sync_agent_controls();
		const rows = this.filtered_agent_rows(this.state.agents);
		if (!rows.length) {
			this.state.selected_agent_user = '';
			this.page.main.find('[data-role="agent-chart"]').html(`<div class="vobiz-agent-empty">${__('No agents found for this view.')}</div>`);
			this.stop_attendance_timer();
			return;
		}
		if (this.state.selected_agent_user && !rows.some(row => row.user === this.state.selected_agent_user)) {
			this.state.selected_agent_user = '';
		}
		const pageSize = [10, 25, 50, 100].includes(Number(this.state.agent_page_size))
			? Number(this.state.agent_page_size)
			: 10;
		const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
		const currentPage = Math.min(Math.max(1, Number(this.state.agent_page) || 1), totalPages);
		this.state.agent_page = currentPage;
		this.state.agent_page_size = pageSize;
		const start = (currentPage - 1) * pageSize;
		const pageRows = rows.slice(start, start + pageSize);
		this.page.main.find('[data-role="agent-chart"]').html(`
			<div class="vobiz-agent-grid">
				${pageRows.map(row => this.agent_card_html(row)).join('')}
			</div>
			<div class="vobiz-agent-pagination">
				<span class="text-muted">${__('Showing {0}-{1} of {2} agents', [start + 1, Math.min(start + pageSize, rows.length), rows.length])}</span>
				<div class="vobiz-agent-pagination-actions">
					<button class="btn btn-default btn-sm" data-action="agent-page" data-page="${currentPage - 1}" ${currentPage <= 1 ? 'disabled' : ''}>${__('Previous')}</button>
					<span class="btn btn-default btn-sm disabled">${__('Page {0} of {1}', [currentPage, totalPages])}</span>
					<button class="btn btn-default btn-sm" data-action="agent-page" data-page="${currentPage + 1}" ${currentPage >= totalPages ? 'disabled' : ''}>${__('Next')}</button>
				</div>
			</div>
		`);
		this.start_attendance_timer();
	}

	start_attendance_timer() {
		this.stop_attendance_timer();
		this.attendance_timer = setInterval(() => this.update_live_attendance_times(), 1000);
		this.update_live_attendance_times();
	}

	stop_attendance_timer() {
		if (this.attendance_timer) {
			clearInterval(this.attendance_timer);
			this.attendance_timer = null;
		}
	}

	start_attendance_refresh_timer() {
		this.stop_attendance_refresh_timer();
		this.attendance_refresh_timer = setInterval(() => this.refresh_attendance_if_needed(), this.attendance_refresh_ms);
	}

	stop_attendance_refresh_timer() {
		if (this.attendance_refresh_timer) {
			clearInterval(this.attendance_refresh_timer);
			this.attendance_refresh_timer = null;
		}
	}

	refresh_attendance_if_needed() {
		if (document.visibilityState === 'hidden' || this.loading) return;
		const hasBreak = (this.state.agents || []).some(row => row.availability_status === 'Away');
		const hasExpandedAgent = Boolean(this.state.selected_agent_user);
		if (hasBreak || hasExpandedAgent) {
			this.schedule_load(true);
		}
	}

	update_live_attendance_times() {
		const now = Date.now();
		this.page.main.find('.vobiz-agent-card').each((index, card) => {
			const $card = $(card);
			const renderedAt = Number($card.attr('data-rendered-at')) || now;
			const elapsed = Math.max(0, Math.floor((now - renderedAt) / 1000));
			const currentBucket = $card.attr('data-current-bucket') || '';
			$card.find('[data-live-total]').each((itemIndex, item) => {
				const $item = $(item);
				const bucket = $item.attr('data-live-total') || '';
				const base = Number($item.attr('data-base-seconds')) || 0;
				const startedAt = Number($item.attr('data-started-at')) || 0;
				const liveSeconds = startedAt && bucket === currentBucket
					? Math.max(0, Math.floor((now - startedAt) / 1000))
					: (bucket === currentBucket ? elapsed : 0);
				const seconds = base + liveSeconds;
				$item.text(this.duration_from_seconds(seconds));
			});
			$card.find('[data-live-current], [data-live-break-record], [data-live-activity-record]').each((itemIndex, item) => {
				const $item = $(item);
				const base = Number($item.attr('data-base-seconds')) || 0;
				const startedAt = Number($item.attr('data-started-at')) || 0;
				const seconds = startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : base + elapsed;
				$item.text(this.duration_from_seconds(seconds));
			});
		});
	}

	render_agent_overview(agents) {
		const totalAgents = agents.length;
		const activeAgents = agents.filter(row => row.is_online).length;
		const totalCalls = agents.reduce((sum, row) => sum + (Number(row.total) || 0), 0);
		const connectedCalls = agents.reduce((sum, row) => sum + (Number(row.connected) || 0), 0);
		const uniqueCalls = agents.reduce((sum, row) => sum + (Number(row.unique_calls) || 0), 0);
		const totalRejected = agents.reduce((sum, row) => sum + (Number(row.rejected || row.cancelled) || 0), 0);
		const overBreakAgents = agents.filter(row => row.has_over_break || Number(row.over_break_count || 0) > 0).length;
		const avgAnswer = totalCalls
			? ((connectedCalls / totalCalls) * 100).toFixed(1)
			: '0.0';
		const cards = [
			{ label: __('Active Agents'), value: `${activeAgents} / ${totalAgents}`, icon: 'fa-users', className: 'active' },
			{ label: __('Total Calls Handled'), value: totalCalls, icon: 'fa-phone', className: 'calls' },
			{ label: __('Unique Calls'), value: uniqueCalls, icon: 'fa-address-book-o', className: 'calls' },
			{ label: __('Average Answer Rate'), value: `${avgAnswer}%`, icon: 'fa-headphones', className: 'answer' },
			{ label: __('Rejected Calls'), value: totalRejected, icon: 'fa-phone', className: 'rejected' },
			{ label: __('Over Break'), value: overBreakAgents, icon: 'fa-exclamation-triangle', className: 'over-break' },
		];
		this.page.main.find('[data-role="agent-overview"]').html(cards.map(card => `
			<div class="vobiz-agent-stat-card">
				<div>
					<span>${this.escape(card.label)}</span>
					<strong>${this.escape(String(card.value))}</strong>
				</div>
				<div class="vobiz-agent-stat-icon ${this.escape(card.className)}">
					<i class="fa ${this.escape(card.icon)}"></i>
				</div>
			</div>
		`).join(''));
	}

	sync_agent_controls() {
		this.page.main.find('[data-agent-filter]').removeClass('active');
		this.page.main.find(`[data-agent-filter="${this.escape(this.state.agent_status_filter || 'all')}"]`).addClass('active');
		this.page.main.find('[data-role="agent-search"]').val(this.state.agent_search || '');
		this.page.main.find('[data-role="agent-sort"]').val(this.state.agent_sort || 'total');
		this.page.main.find('[data-role="agent-page-size"]').val(String(this.state.agent_page_size || 10));
	}

	filtered_agent_rows(agents) {
		const filter = this.state.agent_status_filter || 'all';
		const query = (this.state.agent_search || '').toLowerCase();
		const sort = this.state.agent_sort || 'total';
		const rows = (agents || []).filter(row => {
			const isBreak = row.availability_status === 'Away';
			const matchesStatus = filter === 'all'
				|| (filter === 'online' && !isBreak && row.is_online)
				|| (filter === 'break' && isBreak)
				|| (filter === 'offline' && !isBreak && !row.is_online);
			const matchesSearch = !query || String(row.user || '').toLowerCase().includes(query);
			return matchesStatus && matchesSearch;
		});
		const value = {
			total: row => Number(row.total) || 0,
			unique: row => Number(row.unique_calls) || 0,
			answer: row => Number(row.answer_rate) || 0,
			talk: row => Number(row.talk_seconds) || 0,
			rejected: row => Number(row.rejected || row.cancelled) || 0,
		}[sort] || (row => Number(row.total) || 0);
		return rows.sort((a, b) => value(b) - value(a));
	}

	agent_card_html(row) {
		const connected = row.connected || 0;
		const missed = row.missed || 0;
		const total = row.total || 0;
		const unique = row.unique_calls || 0;
		const rejected = row.rejected || row.cancelled || 0;
		const other = Math.max(0, total - connected - missed);
		const is_online = Boolean(row.is_online);
		const is_on_call = Boolean(row.is_on_call);
		const availability_label = row.availability_label || (is_online ? __('Online') : __('Offline'));
		const duration_label = row.current_availability_duration_label || row.availability_duration_label || '';
		const current_label = row.current_availability_label || availability_label;
		const stateClass = row.availability_status === 'Away' ? 'break' : (is_online ? 'online' : 'offline');
		const currentBucket = stateClass === 'break' ? 'break' : (stateClass === 'online' ? 'online' : 'offline');
		const currentStartedAt = Number(row.current_availability_since_epoch_ms) || 0;
		const currentDurationSeconds = Number(row.current_availability_duration_seconds) || 0;
		const hasOverBreak = Boolean(row.has_over_break) || Number(row.over_break_count || 0) > 0;
		const overBreakCount = Number(row.over_break_count || 0) || 0;
		const onlineBaseSeconds = Math.max(0, (Number(row.online_today_seconds) || 0) - (currentBucket === 'online' ? currentDurationSeconds : 0));
		const offlineBaseSeconds = Math.max(0, (Number(row.offline_today_seconds) || 0) - (currentBucket === 'offline' ? currentDurationSeconds : 0));
		const online_today = this.duration_from_seconds(onlineBaseSeconds + (currentBucket === 'online' ? currentDurationSeconds : 0), row.online_today_label || '0m');
		const offline_today = this.duration_from_seconds(offlineBaseSeconds + (currentBucket === 'offline' ? currentDurationSeconds : 0), row.offline_today_label || '0m');
		const renderedAt = Date.now();
		const liveAttrs = `data-current-bucket="${this.escape(currentBucket)}" data-rendered-at="${renderedAt}"`;
		const answerRate = Number(row.answer_rate || 0);
		const answerClass = answerRate >= 70 ? 'good' : (answerRate >= 45 ? 'warn' : 'bad');
		const initials = (row.user || '?').trim().slice(0, 1).toUpperCase();
		const isExpanded = this.state.selected_agent_user === row.user;
		const cardTitle = isExpanded ? __('Hide agent details') : __('Show agent details');
		const segment = (value, bucket) => {
			if (!value || !total) return '';
			return `<span title="${this.escape(this.bucket_label(bucket))}: ${value}" style="background:${this.bucket_color(bucket)}; width:${Math.round((value / total) * 100)}%"></span>`;
		};
		return `
			<div class="vobiz-agent-card ${isExpanded ? 'is-expanded' : ''}" role="button" tabindex="0" aria-expanded="${isExpanded ? 'true' : 'false'}" title="${this.escape(cardTitle)}" data-action="toggle-agent-details" data-agent-user="${this.escape(row.user || '')}" ${liveAttrs}>
				${is_on_call ? `<span class="vobiz-agent-call-indicator" title="${this.escape(__('On Call'))}"><i class="fa fa-phone"></i></span>` : ''}
				<div class="vobiz-agent-card-inner">
					<div class="vobiz-agent-identity">
						<div class="vobiz-agent-avatar-wrap">
							<div class="vobiz-agent-avatar">${this.escape(initials)}</div>
							${is_online ? '<span class="vobiz-agent-presence-ring"></span>' : ''}
							<span class="vobiz-agent-presence ${is_online ? 'online' : 'offline'}"></span>
						</div>
						<div class="vobiz-agent-identity-body">
							<div class="vobiz-agent-title" title="${this.escape(row.user || '')}">${this.escape(row.user || '')}</div>
							<div class="vobiz-agent-meta">
								<span class="vobiz-agent-role">${__('Agent')}</span>
								<span class="vobiz-agent-status-pill ${stateClass}">${this.escape(availability_label)}</span>
								${hasOverBreak ? `<span class="vobiz-agent-status-pill over-break">${this.escape(__('Over Break'))}${overBreakCount > 1 ? ` ${overBreakCount}` : ''}</span>` : ''}
							</div>
						</div>
					</div>

					<div class="vobiz-agent-meter">
						<div class="vobiz-agent-meter-head">
							<span class="vobiz-agent-meter-label"><i class="fa fa-bar-chart vobiz-agent-metric-icon"></i>${__('Performance Timeline')}</span>
							<span class="vobiz-agent-meter-count">${missed} ${__('Missed')} / ${connected} ${__('Connected')}</span>
						</div>
						<div class="vobiz-stack" title="${this.escape(`${missed} ${__('Missed')}, ${connected} ${__('Connected')}, ${other} ${__('Other')}`)}">
							${segment(missed, 'missed')}
							${segment(connected, 'connected')}
							${segment(other, 'other')}
						</div>
					</div>

					<div class="vobiz-agent-metrics">
						<div class="vobiz-agent-mini">
							<span>${__('Answer')}</span>
							<strong class="vobiz-agent-answer-badge ${answerClass}">${answerRate}%</strong>
						</div>
						<div class="vobiz-agent-mini">
							<span>${__('Total Talk')}</span>
							<strong class="vobiz-agent-talk-value"><i class="fa fa-clock-o vobiz-agent-metric-icon"></i>${this.escape(row.talk_time_label || '0s')}</strong>
						</div>
						<div class="vobiz-agent-mini">
							<span>${__('Total Calls')}</span>
							<strong>${total}</strong>
						</div>
						<div class="vobiz-agent-mini">
							<span>${__('Unique')}</span>
							<strong>${unique}</strong>
						</div>
						<div class="vobiz-agent-mini">
							<span>${__('Rejected')}</span>
							<strong class="${rejected ? 'vobiz-agent-rejected' : ''}">${rejected}</strong>
						</div>
					</div>

					<div class="vobiz-agent-attendance" title="${this.escape(row.availability_status || availability_label)}">
						<div class="vobiz-agent-status">
							<span class="vobiz-agent-status-dot ${stateClass}"></span>
							<strong class="vobiz-agent-status-text">${this.escape(current_label)}</strong>
						</div>
						<div class="vobiz-agent-status-times">
							<div class="online"><span>${__('Online Today')}</span><b data-live-total="online" data-base-seconds="${onlineBaseSeconds}" data-started-at="${currentBucket === 'online' ? currentStartedAt : 0}">${this.escape(online_today)}</b></div>
							<div class="offline"><span>${__('Offline Today')}</span><b data-live-total="offline" data-base-seconds="${offlineBaseSeconds}" data-started-at="${currentBucket === 'offline' ? currentStartedAt : 0}">${this.escape(offline_today)}</b></div>
							${duration_label ? `<div><span>${__('Currently')} ${this.escape(current_label)}</span><b data-live-current="1" data-base-seconds="${currentDurationSeconds}" data-started-at="${currentStartedAt}">${this.escape(duration_label)}</b></div>` : ''}
						</div>
					</div>
				</div>
				${isExpanded ? this.agent_details_html(row) : ''}
			</div>
		`;
	}

	agent_details_html(row) {
		const callDetails = this.state.agent_calls[row.user || ''] || {};
		return `
			<div class="vobiz-agent-details">
				${this.agent_attendance_history_html(row)}
				${this.agent_call_table_html(row.user || '', callDetails)}
			</div>
		`;
	}

	agent_attendance_history_html(row) {
		const records = row.attendance_records || [];
		const currentBucket = row.availability_status === 'Away' ? 'break' : (row.is_online ? 'online' : 'offline');
		const currentStartedAt = Number(row.current_availability_since_epoch_ms) || 0;
		const currentDurationSeconds = Number(row.current_availability_duration_seconds) || 0;
		const onlineBaseSeconds = Math.max(0, (Number(row.online_today_seconds) || 0) - (currentBucket === 'online' ? currentDurationSeconds : 0));
		const breakBaseSeconds = Math.max(0, (Number(row.break_today_seconds) || 0) - (currentBucket === 'break' ? currentDurationSeconds : 0));
		const offlineBaseSeconds = Math.max(0, (Number(row.offline_today_seconds) || 0) - (currentBucket === 'offline' ? currentDurationSeconds : 0));
		const online = this.duration_from_seconds(onlineBaseSeconds + (currentBucket === 'online' ? currentDurationSeconds : 0), row.online_today_label || '0m');
		const breakTime = this.duration_from_seconds(breakBaseSeconds + (currentBucket === 'break' ? currentDurationSeconds : 0), row.break_today_label || '0m');
		const offline = this.duration_from_seconds(offlineBaseSeconds + (currentBucket === 'offline' ? currentDurationSeconds : 0), row.offline_today_label || '0m');
		const breakCount = Number(row.break_count || 0) || 0;
		const showAttendanceLog = Boolean((this.state.attendance_log_visible || {})[row.user || '']);
		const breakRecords = records
			.filter(record => (record.bucket || '') === 'break')
			.map((record, index) => Object.assign({}, record, { break_number: index + 1 }))
			.reverse();
		const breakRows = breakRecords.length
			? breakRecords.map((record) => {
				const from = this.time_only(record.from);
				const isCurrent = Boolean(record.is_current);
				const to = isCurrent ? __('Ongoing') : this.time_only(record.to);
				const date = this.date_only(record.from);
				const breakDuration = this.duration_from_seconds(record.duration_seconds, record.duration_label);
				const title = isCurrent ? __('In Progress') : `${__('Break')} ${record.break_number}`;
				const isOverBreak = Boolean(record.is_over_break);
				const overBreakLabel = record.over_break_label || this.duration_from_seconds(record.over_break_seconds || 0, '');
				const liveStartedAt = isCurrent ? (currentStartedAt || Number(record.from_epoch_ms) || 0) : 0;
				const liveAttrs = isCurrent
					? `data-live-break-record="1" data-base-seconds="0" data-started-at="${liveStartedAt}"`
					: '';
				return `
					<div class="vobiz-agent-break-record ${isCurrent ? 'in-progress' : ''} ${isOverBreak ? 'over-break' : ''}">
						<div class="vobiz-agent-break-card-head">
							<div class="vobiz-agent-break-card-title">${this.escape(title)}</div>
							<div class="vobiz-agent-break-date">${this.escape(date)}</div>
						</div>
						<div class="vobiz-agent-break-divider"></div>
						<div class="vobiz-agent-break-card-body">
							<div class="vobiz-agent-break-field"><span>${__('Start')}</span><b>${this.escape(from)}</b></div>
							<div class="vobiz-agent-break-field end"><span>${__('End')}</span><b>${this.escape(to)}</b></div>
							<div class="vobiz-agent-break-field total"><span>${__('Total')}</span><b ${liveAttrs}>${this.escape(breakDuration)}</b>${isOverBreak && overBreakLabel ? `<em class="vobiz-agent-break-warning">${this.escape(__('Over by'))} ${this.escape(overBreakLabel)}</em>` : ''}</div>
						</div>
					</div>
				`;
			}).join('')
			: `<div class="vobiz-empty">${__('No breaks taken today.')}</div>`;
		return `
			<div class="vobiz-agent-details-panel full vobiz-agent-attendance-full">
				<div class="vobiz-agent-attendance-head">
					<h4>${__('Full Attendance')}</h4>
					<button type="button" class="vobiz-agent-attendance-log-btn" data-action="toggle-attendance-log" data-agent-user="${this.escape(row.user || '')}">
						<i class="fa fa-list-ul"></i>
						<span>${showAttendanceLog ? __('Hide Attendance Log') : __('Attendance Log')}</span>
					</button>
				</div>
				<div class="vobiz-agent-attendance-summary">
					<div class="vobiz-agent-attendance-box online"><span>${__('Online Today')}</span><b data-live-total="online" data-base-seconds="${onlineBaseSeconds}" data-started-at="${currentBucket === 'online' ? currentStartedAt : 0}">${this.escape(online)}</b></div>
					<div class="vobiz-agent-attendance-box offline"><span>${__('Offline Today')}</span><b data-live-total="offline" data-base-seconds="${offlineBaseSeconds}" data-started-at="${currentBucket === 'offline' ? currentStartedAt : 0}">${this.escape(offline)}</b></div>
					<div class="vobiz-agent-attendance-box break"><span>${__('Break Time')}</span><b data-live-total="break" data-base-seconds="${breakBaseSeconds}" data-started-at="${currentBucket === 'break' ? currentStartedAt : 0}">${this.escape(breakTime)}</b></div>
					<div class="vobiz-agent-attendance-box"><span>${__('Breaks Taken')}</span><b>${breakCount}</b></div>
				</div>
				<div class="vobiz-agent-break-history">
					<h5>${__('Break History')}</h5>
					<div class="vobiz-agent-break-records">${breakRows}</div>
				</div>
				${showAttendanceLog ? this.agent_activity_log_html(row, records, currentStartedAt) : ''}
			</div>
		`;
	}

	agent_activity_log_html(row, records, currentStartedAt) {
		const activityRecords = (records || []).slice().reverse();
		const rows = activityRecords.length
			? activityRecords.map((record) => {
				const bucket = record.bucket || (record.status === 'Away' ? 'break' : (record.status === 'Available' || record.status === 'Busy' ? 'online' : 'offline'));
				const label = record.label || (bucket === 'break' ? __('Break') : (bucket === 'online' ? __('Online') : __('Offline')));
				const isCurrent = Boolean(record.is_current);
				const from = this.time_only(record.from);
				const to = isCurrent ? __('Ongoing') : this.time_only(record.to);
				const duration = this.duration_from_seconds(record.duration_seconds, record.duration_label);
				const liveStartedAt = isCurrent ? (currentStartedAt || Number(record.from_epoch_ms) || 0) : 0;
				const liveAttrs = isCurrent
					? `data-live-activity-record="1" data-base-seconds="0" data-started-at="${liveStartedAt}"`
					: '';
				return `
					<div class="vobiz-agent-activity-row ${isCurrent ? 'current' : ''}">
						<div class="vobiz-agent-activity-status">
							<span class="vobiz-agent-activity-dot ${this.escape(bucket)}"></span>
							<span>${this.escape(label)}</span>
						</div>
						<div class="vobiz-agent-activity-field"><span>${__('Start')}</span><b>${this.escape(from)}</b></div>
						<div class="vobiz-agent-activity-field"><span>${__('End')}</span><b>${this.escape(to)}</b></div>
						<div class="vobiz-agent-activity-field total"><span>${__('Total')}</span><b ${liveAttrs}>${this.escape(duration)}</b></div>
					</div>
				`;
			}).join('')
			: `<div class="vobiz-empty">${__('No attendance activity found today.')}</div>`;
		return `
			<div class="vobiz-agent-activity-log">
				<h5>${__('Attendance Log')}</h5>
				<div class="vobiz-agent-activity-list">${rows}</div>
			</div>
		`;
	}

	agent_call_table_html(agentUser, details) {
		const calls = details.calls || [];
		const shown = calls.length;
		const total = Number(details.total || shown) || 0;
		const page = Number(details.page || 0) || 0;
		const limit = Number(details.limit || 25) || 25;
		const start = total && shown ? (page * limit) + 1 : 0;
		const end = total && shown ? Math.min(total, (page * limit) + shown) : 0;
		const hasPrevious = page > 0;
		const hasNext = Boolean(details.has_more);
		const note = details.loading
			? __('Loading calls...')
			: `${__('Showing')} ${start}-${end} ${__('of')} ${total} ${__('calls')}`;
		const statusOptions = [
			['total', __('All')],
			['unique', __('Unique Calls')],
			['connected', __('Connected')],
			['connected_inbound', __('Connected Inbound')],
			['connected_outbound', __('Connected Outbound')],
			['missed', __('Missed')],
			['busy', __('Busy')],
			['no_answer', __('No Answer')],
			['failed', __('Failed')],
			['cancelled', __('Rejected')]
		];
		return `
			<div class="vobiz-agent-details-panel full">
				<div class="vobiz-agent-call-filter">
					<span class="vobiz-agent-call-filter-label">${__('Filter Status')}:</span>
					${statusOptions.map(([value, label]) => `
						<button class="vobiz-agent-call-status-pill ${this.escape(value)} ${(details.status_filter || 'total') === value ? 'active' : ''}" data-action="agent-call-status" data-agent-user="${this.escape(agentUser)}" data-status-filter="${this.escape(value)}">
							${this.escape(label)}
						</button>
					`).join('')}
				</div>
				<div class="vobiz-agent-call-table-wrap">
					<table class="table table-sm vobiz-agent-call-table">
						<thead>
							<tr>
								<th>${__('Sr No')}</th>
								<th>${__('Call Log')}</th>
								<th>${__('Agent')}</th>
								<th>${__('Queue')}</th>
								<th>${__('Reference')}</th>
								<th>${__('Customer')}</th>
								<th>${__('Call Attempts')}</th>
								<th>${__('Status')}</th>
								<th>${__('Talk Time')}</th>
								<th>${__('Disposition')}</th>
								<th>${__('Time')}</th>
								<th>${__('Recording')}</th>
							</tr>
						</thead>
						<tbody>
							${calls.length
								? calls.map((call, index) => this.call_row_html(call, true, (page * limit) + index + 1)).join('')
								: `<tr><td colspan="12" class="text-muted text-center">${details.loading ? __('Loading calls...') : this.escape(details.error || __('No calls found for this agent.'))}</td></tr>`}
						</tbody>
					</table>
				</div>
				<div class="vobiz-agent-call-pagination">
					<span>${this.escape(note)}</span>
					<button class="btn btn-default btn-xs" data-action="agent-call-page" data-agent-user="${this.escape(agentUser)}" data-page="${page - 1}" ${!hasPrevious || details.loading ? 'disabled' : ''}>
						${__('Previous')}
					</button>
					<button class="btn btn-default btn-xs" data-action="agent-call-page" data-agent-user="${this.escape(agentUser)}" data-page="${page + 1}" ${!hasNext || details.loading ? 'disabled' : ''}>
						${__('Next')}
					</button>
				</div>
			</div>
		`;
	}

	daily_line_chart_html(rows, axisMax, width) {
		width = Math.max(320, Math.round(Number(width) || 0));
		const plotHeight = 230;
		const height = 264;
		const padding = { top: 18, right: 18, bottom: 28, left: 18 };
		const usableWidth = width - padding.left - padding.right;
		const usableHeight = plotHeight - padding.top - padding.bottom;
		const points = (rows.length ? rows : [{ total: 0, date: '' }]).map((row, index, list) => {
			const x = list.length === 1 ? padding.left : padding.left + ((usableWidth / (list.length - 1)) * index);
			const y = padding.top + (1 - ((Number(row.total) || 0) / axisMax)) * usableHeight;
			return { x, y, row };
		});
		const linePoints = points.map(point => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' ');
		const areaPoints = [
			`${points[0].x.toFixed(2)},${(plotHeight - padding.bottom).toFixed(2)}`,
			linePoints,
			`${points[points.length - 1].x.toFixed(2)},${(plotHeight - padding.bottom).toFixed(2)}`
		].join(' ');
		return `
			<div class="vobiz-line-chart">
				<svg class="vobiz-line-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${this.escape(__('Daily Call Trend'))}">
					<polygon class="vobiz-line-area" points="${this.escape(areaPoints)}"></polygon>
					<polyline class="vobiz-line-path" points="${this.escape(linePoints)}"></polyline>
					${points.map(point => `
						<circle class="vobiz-line-dot" cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="4">
							<title>${this.escape(`${point.row.date || ''} - ${this.format_number(point.row.total || 0)} ${__('calls')}`)}</title>
						</circle>
					`).join('')}
					${points.map(point => `
						<text class="vobiz-line-x-label" x="${point.x.toFixed(2)}" y="252" text-anchor="middle">${this.escape(this.short_date(point.row.date))}</text>
					`).join('')}
				</svg>
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

	format_number(value) {
		return (Number(value) || 0).toLocaleString();
	}

	time_only(value) {
		if (!value) return '';
		const text = String(value);
		const match = text.match(/(\d{1,2}:\d{2}(?::\d{2})?)/);
		return match ? match[1] : text;
	}

	date_only(value) {
		if (!value) return '';
		const text = String(value);
		const match = text.match(/(\d{1,2})-(\d{1,2})-(\d{4})/);
		if (!match) return '';
		const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
		const day = String(Number(match[1]) || match[1]);
		const month = months[(Number(match[2]) || 1) - 1] || match[2];
		return `${day} ${month} ${match[3]}`;
	}

	duration_from_seconds(seconds, fallback = '') {
		const total = Math.max(0, Number(seconds) || 0);
		if (!total) return fallback || '0s';
		const hours = Math.floor(total / 3600);
		const minutes = Math.floor((total % 3600) / 60);
		const secs = total % 60;
		const parts = [];
		if (hours) parts.push(`${hours}h`);
		if (minutes) parts.push(`${minutes}m`);
		if (secs || !parts.length) parts.push(`${secs}s`);
		return parts.join(' ');
	}

	escape(value) {
		return frappe.utils.escape_html(value == null ? '' : String(value));
	}
}
