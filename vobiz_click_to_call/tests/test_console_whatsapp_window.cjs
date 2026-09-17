const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function testConsole(app) {
    let active = true, sequence = 0, requests = [], refreshes = 0;
    let response = { message: { success: false, result: { error: 'Window closed on server' } } };
    const timers = new Map(), controls = new Map(), data = new Map(), notices = [];
    function control(selector) {
        if (!controls.has(selector)) controls.set(selector, {
            props: {}, attrs: {}, value: selector === '[data-wa-reply]' ? 'Keep this draft' : '',
            first() { return this; },
            prop(key, value) { if (value === undefined) return this.props[key]; this.props[key] = value; return this; },
            attr(key, value) { if (value === undefined) return this.attrs[key]; this.attrs[key] = value; return this; },
            text(value) { this.textValue = value; return this; },
            val(value) { if (value === undefined) return this.value; this.value = value; return this; },
            toggle(value) { this.visible = value; return this; },
            removeClass(value) { this.removedClass = value; return this; },
            find: control,
        });
        return controls.get(selector);
    }
    const element = { scrollHeight: 100, clientHeight: 100, scrollTop: 0 };
    const list = {
        first() { return this; }, get: () => element,
        data: (key, value) => value === undefined ? data.get(key) : data.set(key, value),
        attr: key => key === 'data-conversation' ? 'CONV' : '0',
        find: () => ({ last: () => ({ attr: () => 'LAST' }) }),
    };
    const bodyElement = {};
    const body = {
        get: () => bodyElement, data: () => ({ reference_doctype: 'Patient', reference_name: 'PAT' }),
        find: selector => selector === '[data-wa-chat-list]' ? list : control(selector),
    };
    const view = { $body: body, $list: list, element, conversation: 'CONV' };
    function jqueryPromise(promise) {
        return { then: (ok, error) => jqueryPromise(promise.then(ok, error)), always: fn => promise.finally(fn) };
    }
    const ctx = {
        __: value => value,
        clearTimeout: id => timers.delete(id),
        setTimeout: (fn, delay) => { const id = ++sequence; timers.set(id, { fn, delay }); return id; },
        frappe: {
            pages: { 'vobiz-agent-console': {} },
            utils: { escape_html: value => value }, datetime: { str_to_user: value => value },
            show_alert: value => notices.push(value), msgprint: value => notices.push(value),
            call: args => { requests.push(args); return jqueryPromise(Promise.resolve(response)); },
        },
    };
    vm.createContext(ctx);
    const page = path.resolve(__dirname, '../../..', app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js');
    vm.runInContext(fs.readFileSync(page, 'utf8') + ';this.Console = VobizAgentConsole;', ctx);
    const obj = Object.create(ctx.Console.prototype);
    obj.state = { queue: [] };
    obj.active_whatsapp_view = () => active ? view : null;
    obj.is_current_whatsapp_view = candidate => active && candidate.element === element;
    obj.schedule_whatsapp_sync = () => {};
    obj.refresh_inline_whatsapp = () => refreshes++;
    assert.equal(obj.can_send_workdesk_whatsapp(body), true, 'Sending does not wait for window information');
    list.data('wa-window-state', { state: { can_send_free_form: false } });
    assert.equal(obj.can_send_workdesk_whatsapp(body), true, 'The backend validates sending restrictions');
    obj.send_workdesk_whatsapp(body);
    obj.send_workdesk_whatsapp(body);
    assert.equal(requests.length, 1, 'Repeated Enter/click cannot duplicate an in-flight send');
    assert.equal(control('[data-wa-send]').props.disabled, true);
    await new Promise(setImmediate);
    assert.equal(control('[data-wa-reply]').value, 'Keep this draft', 'Server denial preserves draft');
    assert.equal(control('[data-wa-send]').props.disabled, false, 'Server denial allows another attempt');
    assert.equal(refreshes, 0);
    assert.ok(notices.some(value => value.message === 'Window closed on server'));
    await assert.rejects(async () => obj.send_workdesk_whatsapp_media(body, 'CONV', {}), /Window closed on server/);
    assert.equal(refreshes, 0, 'A rejected media send does not refresh away the failed attempt');
    response = { message: { success: true } };
    obj.send_workdesk_whatsapp(body);
    await new Promise(setImmediate);
    assert.equal(control('[data-wa-reply]').value, '');
    assert.equal(refreshes, 1);
    active = false;
    const before = requests.length;
    obj.send_workdesk_whatsapp(body);
    await assert.rejects(() => obj.send_workdesk_whatsapp_media(body, 'CONV', {}), /not ready/);
    assert.equal(requests.length, before, 'An inactive chat cannot send');
    assert.equal(timers.size, 0, 'No messaging-window expiry timer is started');
    console.log(`${app}: unblocked composer, duplicate protection, backend rejection, drafts and inactive-chat guards passed`);

}
(async () => {
    await testConsole('vobiz_click_to_call');
    await testConsole('vobiz_system_call');
})().catch(error => { console.error(error); process.exitCode = 1; });
