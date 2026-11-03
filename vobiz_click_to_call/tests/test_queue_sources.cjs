
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let control;
let options = ['CRM Lead', 'Patient', 'Patient Encounter', 'Issue', 'Discontinued'];
const context = {
    __: x => x, $: () => ({length: 1}),
    frappe: {provide() {}, call: async () => ({message: options}), ui: {form: {
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
    options = [...options, 'NDR'];
    await context.setup_queue_source_picker(frm);
    assert.ok(control.df.options.includes('NDR'));
    control.set_value(['NDR']);
    assert.equal(frm.doc.queue_source, 'NDR');
    options = options.filter(option => option !== 'NDR');
    const changeCount = changes.length;
    await context.setup_queue_source_picker(frm);
    assert.equal(changes.length, changeCount);
    assert.ok(!control.df.options.includes('NDR'));
    assert.ok(control.df.description.includes('retained'));
    control.set_value(['Issue']);
    assert.equal(frm.doc.queue_source, 'Issue\nNDR');
    console.log('Queue Source picker: legacy initialization, multi-selection and patient routing checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
