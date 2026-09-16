const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function testConsole(app) {
    let now = 900000, active = true, sequence = 0, scheduled, requests = [], refreshes = 0;
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
        Date: class extends Date { static now() { return now; } },
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
    obj.schedule_whatsapp_sync = delay => { scheduled = delay; };
    obj.refresh_inline_whatsapp = () => refreshes++;
    const inputSelector = '[data-wa-reply], [data-wa-attach], [data-wa-emoji]';
    const state = {
        can_send_free_form: true, reason: 'customer_replied_within_24h',
        last_customer_message_at: '2026-09-15 12:00:10',
        server_time: '2026-09-16 12:00:00', free_form_expires_at: '2026-09-16 12:00:10',
    };
    obj.apply_whatsapp_window(view, null);
    assert.equal(control(inputSelector).props.disabled, true, 'Unknown state cannot send normal messages');
    assert.equal(control('[data-wa-template]').visible, true);
    assert.equal(obj.can_send_workdesk_whatsapp(body), false);
    obj.apply_whatsapp_window(view, state, now - 1000);
    assert.equal(control(inputSelector).props.disabled, false);
    assert.equal(control('[data-wa-send]').props.disabled, false);
    assert.equal([...timers.values()][0].delay, 9000, 'Expiry uses server time and accounts for request latency');
    assert.ok(control('[data-wa-window-text]').textValue.includes(state.free_form_expires_at));
    assert.equal(control('[data-wa-reply]').value, 'Keep this draft');
    assert.equal(obj.can_send_workdesk_whatsapp(body), true);

    list.data('wa-sending', true);
    obj.render_whatsapp_window(view);
    assert.equal(control('[data-wa-send]').props.disabled, true, 'Polling cannot enable Send during a pending send');
    list.data('wa-sending', false);
    now += 9000;
    [...timers.values()][0].fn();
    assert.equal(control('[data-wa-send]').props.disabled, true, 'Window closes without waiting for another poll');
    assert.equal(control('[data-wa-template]').visible, true);
    assert.equal(control('[data-wa-reply]').value, 'Keep this draft');
    assert.equal(scheduled, 0);
    obj.send_workdesk_whatsapp(body);
    assert.equal(requests.length, 0, 'Enter/direct calls cannot bypass expired controls');
    await assert.rejects(() => obj.send_workdesk_whatsapp_media(body, 'CONV', {}), /approved template/);
    assert.equal(requests.length, 0);

    obj.apply_whatsapp_window(view, { can_send_free_form: false, last_customer_message_at: null });
    assert.ok(control('[data-wa-window-text]').textValue.includes('No incoming message'));
    assert.ok(control('[data-wa-window-text]').textValue.includes('patient replies'));
    obj.apply_whatsapp_window(view, { ...state, reason: 'ctwa_72h' });
    assert.ok(control('[data-wa-window-text]').textValue.includes('Click-to-WhatsApp'));
    assert.equal(control(inputSelector).props.disabled, false, 'Patient reply/new window re-enables controls');

    obj.send_workdesk_whatsapp(body);
    await new Promise(setImmediate);
    assert.equal(control('[data-wa-reply]').value, 'Keep this draft', 'Server denial preserves draft');
    assert.equal(refreshes, 0);
    assert.ok(notices.some(value => value.message === 'Window closed on server'));
    response = { message: { success: true } };
    obj.send_workdesk_whatsapp(body);
    await new Promise(setImmediate);
    assert.equal(control('[data-wa-reply]').value, '');
    assert.equal(refreshes, 1);

    obj.append_live_whatsapp_messages = () => {};
    obj.update_whatsapp_message_statuses = () => {};
    obj.whatsapp_status_message_names = () => [];
    response = { message: { success: true, messages: [], messaging_window: { can_send_free_form: false } } };
    await obj.sync_inline_whatsapp();
    assert.equal(control('[data-wa-send]').props.disabled, true, 'Live sync applies authoritative window even without new messages');
    obj.apply_whatsapp_window(view, state);
    const banner = control('[data-wa-window-text]').textValue;
    active = false;
    obj.apply_whatsapp_window(view, { can_send_free_form: false });
    assert.equal(control('[data-wa-window-text]').textValue, banner, 'Late responses cannot update another patient chat');
    assert.equal(obj.can_send_workdesk_whatsapp(body), false);
    obj.stop_whatsapp_sync();
    assert.equal(timers.size, 0, 'Window timer stops with chat lifecycle');
    console.log(`${app}: messaging windows, first contact, CTWA, expiry, clock skew, polling, draft preservation and send guards passed`);
}
(async () => {
    await testConsole('vobiz_click_to_call');
    await testConsole('vobiz_system_call');
})().catch(error => { console.error(error); process.exitCode = 1; });
