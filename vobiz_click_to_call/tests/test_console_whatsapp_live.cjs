const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function testConsole(app) {
    let visible = true, sequence = 0, calls = [], timers = new Map(), handlers = new Map();
    let respond = async () => ({ message: { success: true, messages: [] } });
    const document = { hidden: false, documentElement: { contains: el => el.connected } };
    function node(name) { return { name, getAttribute: () => name }; }
    function makeList(names, conversation = 'CONV') {
        let top = 0;
        const list = { attrs: { 'data-conversation': conversation, 'data-before': 'oldest', 'data-has-more': '1' }, nodes: names.map(node) };
        list.element = {
            connected: true, clientHeight: 200,
            get scrollHeight() { return list.nodes.length * 100; },
            get scrollTop() { return top; },
            set scrollTop(value) { top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
        };
        const data = new Map();
        list.data = (key, value) => value === undefined ? data.get(key) : data.set(key, value);
        list.first = () => list;
        list.get = () => list.element;
        list.attr = function(key, value) { if (value === undefined) return this.attrs[key]; this.attrs[key] = value; return this; };
        list.find = selector => ({
            last: () => ({ attr: () => list.nodes.at(-1)?.name }),
            each: fn => list.nodes.forEach((el, i) => fn(i, el)),
            remove() {},
        });
        list.append = message => list.nodes.push(message.node);
        return list;
    }
    let list = makeList(['M1', 'M2', 'M3']);
    const body = { length: 1, find: selector => selector === '[data-wa-playback]' ? { each() {} } : list, data: () => ({ reference_doctype: 'Patient', reference_name: 'PAT' }), draft: 'Unsent draft' };
    const ctx = {
        document, __: s => s,
        clearTimeout: id => timers.delete(id),
        setTimeout: (fn, delay) => { const id = ++sequence; timers.set(id, { fn, delay }); return id; },
        $: html => ({ node: node(html), find: () => ({ one() {} }) }),
        frappe: {
            pages: { 'vobiz-agent-console': {} },
            realtime: { on: (event, fn) => handlers.set(event, fn), off: (event, fn) => { assert.equal(handlers.get(event), fn); handlers.delete(event); } },
            call: args => { calls.push(args); return respond(args); },
        },
    };
    vm.createContext(ctx);
    const apps = path.resolve(__dirname, '../../..');
    const page = path.join(apps, app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js');
    vm.runInContext(fs.readFileSync(page, 'utf8') + ';this.Console = VobizAgentConsole;', ctx);
    const obj = Object.create(ctx.Console.prototype);
    obj.apply_whatsapp_window = () => {};
    obj.whatsapp_window_check_failed = () => {};
    obj.state = { active_workdesk_body: body };
    obj.is_console_visible = () => visible;
    obj.workdesk_whatsapp_message_html = message => message.name;

    obj.bind_realtime(); obj.bind_realtime();
    assert.equal(handlers.size, app === 'vobiz_system_call' ? 6 : 5, 'No duplicate realtime subscriptions');
    handlers.get('wa_chat_new_message')({ conversation: 'OTHER' });
    assert.equal(timers.size, 0);
    handlers.get('wa_chat_new_message')({ conversation: 'CONV', message: { name: 'UNTRUSTED' } });
    assert.equal(timers.size, 1);
    assert.equal(list.nodes.length, 3, 'Broadcast content is not rendered before authorized fetch');
    obj.stop_whatsapp_sync();

    list.element.scrollTop = list.element.scrollHeight;
    respond = async () => ({ message: { success: true, messages: [{ name: 'M4' }, { name: 'M4' }], has_more_after: false } });
    await obj.sync_inline_whatsapp();
    assert.equal(calls.at(-1).args.after_message, 'M3');
    assert.equal(calls.at(-1).args.reference_name, 'PAT');
    assert.equal(list.nodes.map(n => n.name).join(','), 'M1,M2,M3,M4');
    assert.equal(list.element.scrollTop, 200, 'Follow new messages when at bottom');
    assert.equal(body.draft, 'Unsent draft');
    assert.equal(list.attrs['data-before'], 'oldest');
    assert.equal(list.attrs['data-has-more'], '1');

    list = makeList(Array.from({ length: 60 }, (_, i) => `H${i}`));
    list.element.scrollTop = 100;
    respond = async () => ({ message: { success: true, messages: [{ name: 'NEW' }], has_more_after: true } });
    await obj.sync_inline_whatsapp();
    assert.equal(list.nodes.length, 61, 'Older loaded history is retained');
    assert.equal(list.element.scrollTop, 100, 'Reading position is retained');
    assert.equal([...timers.values()].at(-1).delay, 150, 'Drain missed-message pages promptly');

    let release;
    respond = () => new Promise(resolve => { release = resolve; });
    const before = calls.length;
    const first = obj.sync_inline_whatsapp();
    await obj.sync_inline_whatsapp();
    assert.equal(calls.length, before + 1, 'No overlapping fetches for the same chat');
    release({ message: { success: true, messages: [] } });
    await first;
    assert.equal([...timers.values()].at(-1).delay, 150, 'Events during fetch trigger another check');

    const stale = obj.sync_inline_whatsapp();
    list = makeList(['P1'], 'OTHER');
    release({ message: { success: true, messages: [{ name: 'WRONG-PATIENT' }] } });
    await stale;
    assert.equal(list.nodes.map(n => n.name).join(','), 'P1', 'Late responses cannot leak into another patient chat');

    obj.stop_whatsapp_sync();
    document.hidden = true;
    obj.schedule_whatsapp_sync(0);
    const count = calls.length;
    await obj.sync_inline_whatsapp();
    assert.equal(calls.length, count);
    assert.equal(timers.size, 0);
    document.hidden = false;
    visible = false;
    obj.schedule_whatsapp_sync(0);
    assert.equal(timers.size, 0);
    visible = true;

    list.attrs['data-loading'] = '1';
    await obj.sync_inline_whatsapp();
    assert.equal(calls.length, count, 'Wait for older-history pagination to finish');
    list.attrs['data-loading'] = '0';
    respond = async () => { throw new Error('Disconnected'); };
    await obj.sync_inline_whatsapp();
    assert.equal([...timers.values()].at(-1).delay, 10000, 'Recover after disconnect through polling');

    const pending = (respond = () => new Promise(resolve => { release = resolve; }), obj.sync_inline_whatsapp());
    obj.stop_whatsapp_sync();
    release({ message: { success: true, messages: [{ name: 'CLOSED' }] } });
    await pending;
    assert.equal(list.nodes.map(n => n.name).join(','), 'P1');
    assert.equal(timers.size, 0, 'Closing chat cancels pending synchronization');
    list = makeList(['123']);
    obj.append_live_whatsapp_messages(obj.active_whatsapp_view(), [{ name: 123 }]);
    assert.equal(list.nodes.length, 1, 'Numeric database IDs deduplicate against DOM string IDs');
    obj.unbind_realtime();
    assert.equal(handlers.size, 0);
    console.log(`${app}: live updates, pagination, dedupe, scrolling, drafts, access context, lifecycle and recovery passed`);
}
(async () => {
    await testConsole('vobiz_click_to_call');
    await testConsole('vobiz_system_call');
})().catch(err => { console.error(err); process.exitCode = 1; });
