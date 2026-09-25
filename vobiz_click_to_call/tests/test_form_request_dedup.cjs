const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../public/js/click_to_call.js'), 'utf8');
function setup() {
    const calls = [], rendered = [];
    const context = {
        window: {}, document: {}, console,
        $: () => ({on() {}}),
        frappe: {session: {user: 'agent'}, get_route: () => ['Form', 'CRM Lead', 'ONE'],
            ready() {}, ui: {form: {on() {}}}, call(args) {
                return new Promise((resolve, reject) => calls.push({args, resolve, reject}));
            }},
    };
    context.window.frappe = context.frappe;
    context.rendered = rendered;
    vm.createContext(context);
    vm.runInContext(source.replace(/\}\)\(\);\s*$/, `
        ensureStyles = () => {};
        bindGridRender = () => {};
        renderButtons = frm => rendered.push(frm.doc.name);
        renderCallHistory = () => {};
        removeButtons = () => {};
        window.controls = {setupForm, loadAllowedDoctypes};
    })();`), context);
    const frm = {doc: {name: 'ONE', modified: 'v1'}, doctype: 'CRM Lead', is_new: () => false};
    context.window.cur_frm = frm;
    context.cur_frm = frm;
    return {context, calls, rendered, frm, api: context.window.controls};
}
test('overlapping capability refresh shares a request and subsequent refresh is fresh', async () => {
    const t = setup();
    const a = t.api.setupForm(t.frm), b = t.api.setupForm(t.frm);
    assert.equal(a, b);
    assert.equal(t.calls.length, 1);
    t.calls[0].resolve({message: {can_call: true}});
    await a;
    assert.deepEqual(t.rendered, ['ONE']);
    const c = t.api.setupForm(t.frm);
    assert.equal(t.calls.length, 2);
    t.calls[1].resolve({message: {can_call: false}});
    await c;
    assert.equal(t.frm.vobiz_call_capability.can_call, false);
});
test('late response cannot overwrite another document or newer form state', async () => {
    const t = setup();
    const a = t.api.setupForm(t.frm);
    t.frm.doc = {name: 'TWO', modified: 'v2'};
    const b = t.api.setupForm(t.frm);
    t.calls[1].resolve({message: {can_call: false}});
    await b;
    t.calls[0].resolve({message: {can_call: true}});
    await a;
    assert.deepEqual(t.rendered, ['TWO']);
    assert.equal(t.frm.vobiz_call_capability.can_call, false);
});
test('failed capability request can be retried', async () => {
    const t = setup();
    const a = t.api.setupForm(t.frm);
    t.calls[0].reject(new Error('offline'));
    await a;
    const b = t.api.setupForm(t.frm);
    assert.equal(t.calls.length, 2);
    t.calls[1].resolve({message: {}});
    await b;
});
test('doctype discovery shares request and resets after failure', async () => {
    const t = setup();
    const a = t.api.loadAllowedDoctypes(), b = t.api.loadAllowedDoctypes();
    assert.equal(a, b);
    assert.equal(t.calls.length, 1);
    t.calls[0].reject(new Error('offline'));
    await a;
    const c = t.api.loadAllowedDoctypes();
    assert.equal(t.calls.length, 2);
    t.context.window.cur_frm = null;
    t.calls[1].resolve({message: ['CRM Lead']});
    await c;
});

test('synchronous transport setup failure releases both in-flight guards', async () => {
    const t = setup();
    const original = t.context.frappe.call;
    t.context.frappe.call = () => {throw new Error('setup failed');};
    await t.api.setupForm(t.frm);
    await t.api.loadAllowedDoctypes();
    t.context.frappe.call = original;
    const a = t.api.setupForm(t.frm);
    assert.equal(t.calls.length, 1);
    t.calls[0].resolve({message: {}});
    await a;
    const b = t.api.loadAllowedDoctypes();
    assert.equal(t.calls.length, 2);
    t.context.window.cur_frm = null;
    t.calls[1].resolve({message: ['CRM Lead']});
    await b;
});
