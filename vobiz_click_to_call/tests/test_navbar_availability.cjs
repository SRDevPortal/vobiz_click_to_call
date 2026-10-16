const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const test = require('node:test');
class Request extends Promise {
    always(fn) {return this.then(v => {fn(); return v;}, e => {fn(); throw e;});}
}
function setup() {
    const requests = [], events = {}, rendered = [], changed = [];
    const source = fs.readFileSync(path.join(__dirname, '../public/js/availability.js'), 'utf8');
    const ctx = {
        window: {location: {pathname: '/app/vobiz-agent-console'}},
        document: {}, console,
        frappe: {
            session: {user: 'AGENT'}, get_route: () => ['vobiz-agent-console'],
            realtime: {on: (name, fn) => {events[name] = fn;}},
            call: () => new Request(resolve => requests.push(resolve)),
        },
        $: value => ({
            on() {},
            trigger(name, args) {changed.push([name, args]);},
        }),
        rendered,
    };
    ctx.window.frappe = ctx.frappe;
    vm.createContext(ctx);
    const instrumented = source.replace(/\}\)\(\);\s*$/, `
        renderControl = data => rendered.push(data);
        ensureStyles = () => {};
        startActivityTracking = () => {};
        startAvailabilityRefreshTimer = () => {};
        bindBreakSyncEvents = () => {};
        window.testAvailability = {init, refresh, requestAvailabilityRefresh};
    })();`);
    vm.runInContext(instrumented, ctx);
    ctx.window.testAvailability.init();
    return {ctx, requests, events, rendered, changed};
}
const settle = () => new Promise(resolve => setImmediate(resolve));
test('Busy event invalidates older Available response and coalesces refreshes', async () => {
    const {requests, events, rendered, changed} = setup();
    assert.equal(requests.length, 1);
    events.vobiz_mapping_busy();
    events.vobiz_mapping_busy();
    requests[0]({message: {is_mapped: true, availability_status: 'Available'}});
    await settle();
    assert.equal(rendered.length, 0);
    assert.equal(requests.length, 2);
    requests[1]({message: {is_mapped: true, availability_status: 'Busy', current_call_log: 'CALL'}});
    await settle();
    assert.equal(rendered.at(-1).availability_status, 'Busy');
    assert.equal(changed.at(-1)[1][0].availability_status, 'Busy');
});
test('call completion refreshes both displays with authoritative Offline choice', async () => {
    const {requests, events, rendered, changed} = setup();
    requests[0]({message: {is_mapped: true, availability_status: 'Busy'}});
    await settle();
    events.vobiz_call_disconnected();
    assert.equal(requests.length, 2);
    requests[1]({message: {is_mapped: true, availability_status: 'Offline', current_call_log: ''}});
    await settle();
    assert.equal(rendered.at(-1).availability_status, 'Offline');
    assert.equal(changed.at(-1)[1][0].availability_status, 'Offline');
});
test('final mapping recovery event returns connected agent to Available', async () => {
    const {requests, events, rendered} = setup();
    requests[0]({message: {is_mapped: true, availability_status: 'Busy'}});
    await settle();
    events.vobiz_mapping_recovered();
    requests[1]({message: {is_mapped: true, availability_status: 'Available', current_call_log: ''}});
    await settle();
    assert.equal(rendered.at(-1).availability_status, 'Available');
});
