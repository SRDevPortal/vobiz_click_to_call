
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function check(app) {
    let requests = [], responses = [], opened;
    const nodes = new Map();
    const node = key => {
        if (!nodes.has(key)) nodes.set(key, {
            html(value) { this.content = value; return this; },
            empty() { this.content = ''; return this; },
            prop() { return this; }, text(value) { this.content = value; return this; },
            val(value) { if (value === undefined) return this.value || ''; this.value = value; return this; },
            addClass() { return this; }, removeClass() { return this; },
            toggleClass(name, hidden) { this.hidden = hidden; return this; },
            find(selector) { return node(key + ' ' + selector); }
        });
        return nodes.get(key);
    };
    const sandbox = {
        __: s => s, clearTimeout, document: {addEventListener() {}},
        frappe: {
            pages: {'vobiz-agent-console': {}},
            utils: {escape_html: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;')},
            call: options => { requests.push(options); return responses.shift(); }
        }
    };
    vm.createContext(sandbox);
    const file = path.resolve(__dirname, '../../../', app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js');
    vm.runInContext(fs.readFileSync(file, 'utf8') + ';this.Console = VobizAgentConsole;', sandbox);
    const obj = Object.create(sandbox.Console.prototype);
    obj.page = {main: {find: node}};
    obj.state = {encounter_page: 2, encounters: [], encounter_columns: []};
    responses.push(Promise.resolve({message: {
        rows: [{name: 'ENC-1', patient_name: '<script>'}],
        columns: [{fieldname: 'patient_name', label: 'Patient'}], has_more: true
    }}));
    await obj.load_encounters();
    assert.equal(requests[0].args.limit_start, 25);
    assert.equal(obj.state.encounters[0].name, 'ENC-1');
    const html = node('[data-role="encounter-table"] tbody').content;
    assert.ok(html.includes('&lt;script>'));
    assert.ok(html.includes('data-action="call-row"'));
    obj.apply_context_dispositions = () => {};
    obj.open_detail_dialog = (row, context) => {opened = {row, context};};
    responses.push(Promise.resolve({message: {reference: {name:'PAT-1', title:'Patient'}}}));
    await obj.call_encounter_row(0);
    assert.equal(opened.row.name, 'PAT-1');
    assert.equal(opened.row.doctype, 'Patient');
    assert.equal(opened.row.encounter_name, 'ENC-1');
    assert.ok(requests.every(r => !r.method.endsWith('start_call')));
    // Out-of-order searches must not replace a newer page.
    let first;
    responses.push(new Promise(resolve => {first = resolve;}));
    const pending = obj.load_encounters();
    responses.push(Promise.resolve({message: {rows:[{name:'NEW'}], columns:[]}}));
    await obj.load_encounters();
    first({message: {rows:[{name:'OLD'}], columns:[]}});
    await pending;
    assert.equal(obj.state.encounters[0].name, 'NEW');
    responses.push(Promise.reject(new Error('Denied')));
    await obj.load_encounters();
    assert.equal(obj.state.encounters.length, 0);
    assert.ok(node('[data-role="encounter-table"] tbody').content.includes('Unable to load'));
    // NDR uses the same workdesk, but carries its view to both server endpoints.
    obj.state.console_view = 'encounters';
    obj.state.encounter_page = 3;
    node('[data-role="encounter-search"]').val('ordinary');
    responses.push(Promise.resolve({message: {rows: [{name: 'NDR-1'}], columns: [
        {fieldname: 'pe_shipkia_stage', label: 'Shipkia Stage'},
        {fieldname: 'pe_shipkia_status', label: 'Shipkia Status'}
    ]}}));
    await obj.switch_console_view('ndr');
    assert.equal(requests.at(-1).args.view, 'ndr');
    assert.equal(requests.at(-1).args.limit_start, 0);
    assert.equal(node('[data-role="encounter-search"]').val(), '');
    assert.equal(node('[data-role="encounter-title"]').content, 'NDR');
    assert.ok(node('[data-role="encounter-table"] thead').content.includes('data-encounter-sort="pe_shipkia_stage"'));
    obj.state.encounter_sort_by = 'pe_shipkia_status';
    obj.state.encounter_sort_order = 'desc';
    obj.render_encounters();
    assert.ok(node('[data-role="encounter-table"] thead').content.includes('aria-sort="descending"'));
    responses.push(Promise.resolve({message: {reference: {name: 'PAT-2'}}}));
    await obj.call_encounter_row(0);
    assert.equal(requests.at(-1).args.view, 'ndr');
    assert.equal(opened.row.encounter_name, 'NDR-1');
    assert.equal(opened.row.name, 'PAT-2');
    // A response from NDR cannot replace an Encounter page after switching tabs.
    obj.state.encounter_page = 2;
    node('[data-role="encounter-search"]').val('shipment');
    let oldNdr;
    responses.push(new Promise(resolve => {oldNdr = resolve;}));
    const oldRequest = obj.load_encounters();
    responses.push(Promise.resolve({message: {rows: [{name: 'ORDINARY'}], columns: []}}));
    await obj.switch_console_view('encounters');
    assert.equal(requests.at(-1).args.limit_start, 50);
    assert.equal(requests.at(-1).args.search, 'ordinary');
    assert.equal(requests.at(-1).args.sort_by, '');
    oldNdr({message: {rows: [{name: 'STALE-NDR'}], columns: []}});
    await oldRequest;
    assert.equal(obj.state.encounters[0].name, 'ORDINARY');
    responses.push(Promise.resolve({message: {rows: [{name: 'NDR-2'}], columns: []}}));
    await obj.switch_console_view('ndr');
    assert.equal(requests.at(-1).args.limit_start, 25);
    assert.equal(requests.at(-1).args.search, 'shipment');
    assert.equal(requests.at(-1).args.sort_by, 'pe_shipkia_status');
    assert.equal(requests.at(-1).args.sort_order, 'desc');
    let oldDetails;
    opened = null;
    responses.push(new Promise(resolve => {oldDetails = resolve;}));
    const pendingDetails = obj.call_encounter_row(0);
    obj.switch_console_view('queue');
    oldDetails({message: {reference: {name: 'STALE-PATIENT'}}});
    await pendingDetails;
    assert.equal(opened, null);
    responses.push(Promise.resolve({message: {ndr: true}}));
    await obj.load_encounter_views();
    assert.equal(node('[data-console-view="ndr"]').hidden, false);
    obj.state.console_view = 'ndr';
    responses.push(Promise.resolve({message: {ndr: false}}));
    await obj.load_encounter_views();
    assert.equal(node('[data-console-view="ndr"]').hidden, true);
    assert.equal(obj.state.console_view, 'queue');
    assert.ok(requests.every(r => !r.method.endsWith('start_call')));
    console.log(app + ': Encounter/NDR rendering, paging, sorting, Details, tab isolation and stale requests passed');
}

async function checkSettingsEditor() {
    let dialog, group, saved = {};
    class Dialog {
        constructor(options) { this.options = options; this.fields_dict = {filters: {$wrapper: {}}}; dialog = this; }
        show() { this.shown = true; }
        hide() {}
    }
    class FilterGroup {
        constructor(options) { this.options = options; group = this; }
        update_filter_button() { throw new Error('Inline groups do not have a filter button'); }
        async add_filters(filters) { this.filters = filters; this.update_filter_button(); }
        get_filters() { return [['Patient Encounter', 'pe_shipkia_status', '!=', 'Delivered', false]]; }
    }
    const sandbox = {__: value => value, frappe: {
        ui: {Dialog, FilterGroup, form: {on() {}}},
        call: async () => ({message: [{fieldname: 'name', label: 'ID'}, {fieldname: 'status', label: 'Status'}]}),
        model: {with_doctype: async () => {}}, show_alert() {}, msgprint() {}
    }};
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../public/js/vobiz_settings.js'), 'utf8'), sandbox);
    await sandbox.configure_encounter_queue({
        doc: {encounter_queue_fields: '["name"]', encounter_queue_filters: '[["pe_shipkia_status","!=","Delivered"]]'},
        set_value: async (field, value) => {saved[field] = value;}
    });
    assert.equal(dialog.shown, true);
    assert.equal(group.filters[0][0], 'Patient Encounter');
    await dialog.options.primary_action({columns: ['status', 'name']});
    assert.deepEqual(JSON.parse(saved.encounter_queue_fields), ['name', 'status']);
    assert.deepEqual(JSON.parse(saved.encounter_queue_filters), [['Patient Encounter', 'pe_shipkia_status', '!=', 'Delivered']]);
    console.log('Settings editor: column order and native filter serialization passed');
}

async function checkNdrSettings() {
    const controls = [], saved = {}, calls = [];
    const sandbox = {__: value => value, frappe: {
        ui: {form: {on() {}, make_control(options) {
            const control = {df: options.df, values: [],
                async set_value(values) { this.values = values; this.df.onchange(); },
                get_value() { return this.values; }};
            controls.push(control);
            return control;
        }}},
        user: {has_role: () => true},
        utils: {escape_html: value => value.replaceAll('<', '&lt;').replaceAll('>', '&gt;')},
        call: async options => { calls.push(options); return {message: ['NDR', 'In Transit', '<script>']}; }
    }};
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../public/js/vobiz_settings.js'), 'utf8'), sandbox);
    sandbox.setup_ndr_pickers({
        doc: {ndr_shipkia_stages: '["Previously used"]', ndr_shipkia_statuses: '[]'},
        perm: [{write: 1}],
        fields_dict: Object.fromEntries(['ndr_shipkia_stages_picker', 'ndr_shipkia_statuses_picker'].map(key =>
            [key, {$wrapper: {empty() {}}}])),
        set_value: async (field, value) => {saved[field] = value;}
    });
    await Promise.resolve();
    assert.equal(controls.length, 2);
    assert.deepEqual(saved, {}); // Opening Settings must not clear or dirty saved selections.
    const options = await controls[0].df.get_data('ND');
    assert.ok(options.some(option => option.value === 'Previously used'));
    assert.equal(options.find(option => option.value === '<script>').label, '&lt;script&gt;');
    assert.equal(calls[0].args.field, 'pe_shipkia_stage');
    assert.equal(calls[0].args.search, 'ND');
    controls[0].values = ['NDR', 'In Transit'];
    await controls[0].df.onchange();
    assert.deepEqual(JSON.parse(saved.ndr_shipkia_stages), ['NDR', 'In Transit']);
    controls[1].values = ['Undelivered'];
    await controls[1].df.onchange();
    assert.deepEqual(JSON.parse(saved.ndr_shipkia_statuses), ['Undelivered']);
    console.log('NDR settings: multiselect persistence, dynamic options and saved values passed');
}
(async () => {
    await checkSettingsEditor();
    await checkNdrSettings();
    await check('vobiz_click_to_call');
    await check('vobiz_system_call');
})().catch(error => {console.error(error); process.exitCode = 1;});
