
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let control;
let enabled = '0';
let responseError = false;
const requests = [];
const context = {
    __: x => x, $: () => ({length: 1}),
    frappe: {provide() {}, call: async request => {
        requests.push(request);
        if (responseError) throw new Error('Settings permission denied');
        return {message: {enable_ndr_queue: enabled}};
    }, ui: {form: {
        on() {},
        make_control({df}) {
            control = {df, value: [], set_value(value) {this.value = value; return df.onchange();}, get_value() {return this.value;}};
            return control;
        }
    }}}
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../public/js/vobiz_user_mapping.js'), 'utf8'), context);
(async () => {
    const changes = [];
    const frm = {
        doc: {queue_source: 'CRM Lead and Patient'},
        perm: [{write: 1}],
        fields_dict: {queue_source_picker: {$wrapper: {empty() {}, text() {}}}},
        set_value(field, value) {this.doc[field] = value; changes.push([field, value]);}
    };
    await context.setup_queue_source_picker(frm);
    assert.deepEqual(Array.from(control.value), ['CRM Lead', 'Patient']);
    assert.equal(changes.length, 0);
    control.set_value(['CRM Lead', 'Patient Encounter', 'Issue']);
    assert.equal(frm.doc.queue_source, ['CRM Lead', 'Patient Encounter', 'Issue'].join(String.fromCharCode(10)));
    assert.equal(context.queue_source_includes_patient(frm.doc.queue_source), false);
    control.set_value(['Issue', 'Patient']);
    assert.equal(context.queue_source_includes_patient(frm.doc.queue_source), true);
    assert.equal(context.queue_source_includes_patient('Patient Encounter'), false);
    control.set_value([]);
    assert.equal(frm.doc.queue_source, '');
    assert.ok(!control.df.options.includes('NDR'));
    assert.equal(requests[0].method, 'frappe.client.get_value');
    assert.equal(requests[0].args.doctype, 'Vobiz Settings');
    assert.equal(requests[0].args.fieldname, 'enable_ndr_queue');
    enabled = '1';
    await context.setup_queue_source_picker(frm);
    assert.ok(control.df.options.includes('NDR'));
    control.set_value(['NDR']);
    assert.equal(frm.doc.queue_source, 'NDR');
    enabled = 0;
    const changeCount = changes.length;
    await context.setup_queue_source_picker(frm);
    assert.equal(changes.length, changeCount);
    assert.ok(!control.df.options.includes('NDR'));
    assert.ok(control.df.description.includes('retained'));
    control.set_value(['Issue']);
    assert.equal(frm.doc.queue_source, 'Issue\nNDR');
    enabled = 1;
    await context.setup_queue_source_picker(frm);
    assert.ok(control.df.options.includes('NDR'));
    control.set_value(['Issue']);
    assert.equal(frm.doc.queue_source, 'Issue');
    enabled = null;
    await context.setup_queue_source_picker(frm);
    assert.ok(!control.df.options.includes('NDR'));
    responseError = true;
    const savedBeforeError = frm.doc.queue_source;
    await context.setup_queue_source_picker(frm);
    assert.equal(frm.doc.queue_source, savedBeforeError);
    assert.ok(requests.every(request => request.method === 'frappe.client.get_value'));
    console.log('Queue Source picker: legacy initialization, multi-selection and patient routing checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
