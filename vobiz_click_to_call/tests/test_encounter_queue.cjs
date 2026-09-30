
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
            prop() { return this; }, text() { return this; }, val() { return ''; },
            find(selector) { return node(key + ' ' + selector); }
        });
        return nodes.get(key);
    };
    const sandbox = {
        __: s => s,
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
    console.log(app + ': encounter rendering, paging, Details, escaping and request ordering passed');
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
(async () => {
    await checkSettingsEditor();
    await check('vobiz_click_to_call');
    await check('vobiz_system_call');
})().catch(error => {console.error(error); process.exitCode = 1;});
