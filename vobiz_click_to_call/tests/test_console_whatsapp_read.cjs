const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function testConsole(app) {
    let active = true, snapshot, calls = [], scheduled, renders = 0, refreshes = 0;
    let respond = async () => ({ message: { success: true, marked_read: true, unread_count: 0 } });
    const view = {
        conversation: 'CONV',
        element: { scrollHeight: 1000, scrollTop: 800, clientHeight: 200 },
        $body: { data: () => ({ reference_doctype: 'Patient', reference_name: 'PAT' }) },
        $list: { data: (key, value) => value === undefined ? snapshot : (snapshot = value) },
    };
    const ctx = {
        window: { wa_chat_hub: { notifications: { refresh_count: () => refreshes++ } } },
        frappe: { pages: { 'vobiz-agent-console': {} }, call: args => { calls.push(args); return respond(args); } },
    };
    vm.createContext(ctx);
    const page = path.resolve(__dirname, '../../..', app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js');
    vm.runInContext(fs.readFileSync(page, 'utf8') + ';this.Console = VobizAgentConsole;', ctx);
    const obj = Object.create(ctx.Console.prototype);
    obj.state = { queue: [
        { whatsapp_conversation: 'CONV', whatsapp_unread_count: 3 },
        { whatsapp_conversation: 'OTHER', whatsapp_unread_count: 7 },
    ] };
    obj.is_current_whatsapp_view = other => active && other === view;
    obj.active_whatsapp_view = () => active ? view : null;
    obj.schedule_whatsapp_sync = delay => { scheduled = delay; };
    obj.render_queue = () => renders++;
    const reset = () => {
        snapshot = { read_version: '2026-09-16 12:00:00.123456', unread_count: 3 };
        obj.state.queue[0].whatsapp_unread_count = 3;
        active = true;
        scheduled = undefined;
    };
    reset();
    active = false;
    await obj.mark_visible_whatsapp_read(view);
    assert.equal(calls.length, 0, 'Hidden or switched chat stays unread');
    active = true;
    view.element.scrollTop = 0;
    await obj.mark_visible_whatsapp_read(view);
    assert.equal(calls.length, 0, 'Reading older messages does not clear unseen new messages');
    view.element.scrollTop = 800;
    await obj.mark_visible_whatsapp_read(view);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].type, 'POST');
    assert.equal(calls[0].args.reference_name, 'PAT');
    assert.equal(calls[0].args.read_version, '2026-09-16 12:00:00.123456');
    assert.equal(obj.state.queue[0].whatsapp_unread_count, 0);
    assert.equal(obj.state.queue[1].whatsapp_unread_count, 7);
    assert.equal(snapshot.unread_count, 0);
    assert.equal(renders, 1);
    assert.equal(refreshes, 1);
    await obj.mark_visible_whatsapp_read(view);
    assert.equal(calls.length, 1, 'Already read chat does not send repeated requests');

    reset();
    respond = async () => ({ message: { success: true, marked_read: false, unread_count: 4 } });
    await obj.mark_visible_whatsapp_read(view);
    assert.equal(snapshot.unread_count, 3);
    assert.equal(obj.state.queue[0].whatsapp_unread_count, 3);
    assert.equal(scheduled, 150, 'Newer server snapshot triggers catchup before retry');

    reset();
    let release;
    respond = () => new Promise(resolve => { release = resolve; });
    const pending = obj.mark_visible_whatsapp_read(view);
    const count = calls.length;
    await obj.mark_visible_whatsapp_read(view);
    assert.equal(calls.length, count, 'Only one read request per active view');
    obj.handle_whatsapp_message({ conversation: 'CONV' });
    assert.equal(snapshot, null, 'Incoming event invalidates old snapshot');
    release({ message: { success: true, marked_read: true } });
    await pending;
    assert.equal(obj.state.queue[0].whatsapp_unread_count, 3, 'Late read acknowledgement cannot erase newer unread state');
    assert.equal(scheduled, 150);

    reset();
    const stale = obj.mark_visible_whatsapp_read(view);
    active = false;
    release({ message: { success: true, marked_read: true } });
    await stale;
    assert.equal(obj.state.queue[0].whatsapp_unread_count, 3, 'Switching patient ignores stale UI updates');
    reset();
    respond = async () => { throw new Error('Disconnected'); };
    await obj.mark_visible_whatsapp_read(view);
    assert.equal(snapshot.unread_count, 3);
    assert.equal(obj.whatsapp_read_request, null, 'Failed requests can retry');
    snapshot = null;
    const before = calls.length;
    await obj.mark_visible_whatsapp_read(view);
    assert.equal(calls.length, before, 'Incomplete message catchup cannot mark read');
    console.log(`${app}: read marking, visibility, scroll position, badges, races, isolation and failures passed`);
}
(async () => {
    await testConsole('vobiz_click_to_call');
    await testConsole('vobiz_system_call');
})().catch(error => { console.error(error); process.exitCode = 1; });
