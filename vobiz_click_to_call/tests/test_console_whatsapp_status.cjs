const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function testConsole(app) {
    let request, nextResponse, scheduled, active = true;
    const nodes = Array.from({ length: 205 }, (_, i) => ({
        attrs: { 'data-wa-status-message': `M${i}`, 'data-wa-status': 'sent' },
        getAttribute(key) { return this.attrs[key]; },
    }));
    const data = new Map();
    const list = {
        data: (key, value) => value === undefined ? data.get(key) : data.set(key, value),
        attr: () => '0',
        find: selector => ({
            each: fn => nodes.forEach((node, i) => fn(i, node)),
            last: () => ({ attr: () => 'LAST-MESSAGE' }),
        }),
    };
    const view = {
        $list: list,
        $body: { data: () => ({ reference_doctype: 'Patient', reference_name: 'PAT' }), draft: 'Keep draft' },
        element: { scrollTop: 80, scrollHeight: 1000, clientHeight: 200 }, conversation: 'CONV',
    };
    const ctx = {
        __: s => s,
        $: node => ({ replaceWith(html) {
            node.html = html;
            node.attrs['data-wa-status'] = html.match(/data-wa-status="([^"]+)"/)[1];
        } }),
        frappe: {
            pages: { 'vobiz-agent-console': {} },
            utils: { escape_html: value => String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;') },
            datetime: { str_to_user: value => value },
            call: async args => { request = args; return nextResponse; },
        },
    };
    vm.createContext(ctx);
    const apps = path.resolve(__dirname, '../../..');
    const page = path.join(apps, app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js');
    vm.runInContext(fs.readFileSync(page, 'utf8') + ';this.Console = VobizAgentConsole;', ctx);
    const obj = Object.create(ctx.Console.prototype);
    obj.apply_whatsapp_window = () => {};
    obj.whatsapp_window_check_failed = () => {};
    obj.state = { queue: [] };
    obj.active_whatsapp_view = () => active ? view : null;
    obj.is_current_whatsapp_view = other => active && other === view;
    obj.schedule_whatsapp_sync = delay => { scheduled = delay; };
    obj.workdesk_whatsapp_media_html = () => '';

    for (const status of ['Pending', 'Sent', 'Delivered', 'Read', 'Failed']) {
        const html = obj.whatsapp_delivery_status_html('MSG', status);
        assert.ok(html.includes(`data-wa-status="${status.toLowerCase()}"`));
        assert.ok(html.includes(`aria-label="${status}"`));
        assert.ok(html.includes(`>${status}</span>`));
        if (status === 'Sent') assert.equal((html.match(/<path /g) || []).length, 1);
        if (['Delivered', 'Read'].includes(status)) assert.equal((html.match(/<path /g) || []).length, 2);
        if (status === 'Failed') assert.ok(html.includes('fa-exclamation-circle'));
    }
    assert.ok(obj.whatsapp_delivery_status_html('MSG', null).includes('>Pending</span>'));
    assert.ok(obj.whatsapp_delivery_status_html('MSG', 'Unexpected').includes('>Unknown</span>'));
    const inbound = obj.workdesk_whatsapp_message_html({ name: 'IN', direction: 'Inbound', body: 'Hello', delivery_status: 'Read' });
    assert.ok(!inbound.includes('data-wa-status-message'));
    const outbound = obj.workdesk_whatsapp_message_html({ name: 'OUT', direction: 'Outbound', body: 'Hi', delivery_status: 'Delivered' });
    assert.ok(outbound.includes('data-wa-status-message="OUT"'));

    const first = obj.whatsapp_status_message_names(view);
    const second = obj.whatsapp_status_message_names(view);
    const third = obj.whatsapp_status_message_names(view);
    assert.equal(first.length, 100);
    assert.equal(second.length, 100);
    assert.equal(new Set([...first, ...second, ...third]).size, 205, 'Recovery polling covers all loaded messages');

    obj.handle_whatsapp_status({ conversation: 'OTHER', message: 'M0', delivery_status: 'Read' });
    assert.equal(scheduled, undefined);
    obj.handle_whatsapp_status({ conversation: 'CONV', message: 'M0', delivery_status: 'Read' });
    assert.equal(scheduled, 150);
    assert.equal(nodes[0].attrs['data-wa-status'], 'sent', 'Fetch authoritative status before changing the indicator');
    nextResponse = { message: { success: true, messages: [], message_statuses: [{ name: 'M0', delivery_status: 'Read' }, { name: 'M1', delivery_status: 'Failed' }] } };
    await obj.sync_inline_whatsapp();
    assert.ok(JSON.parse(request.args.status_message_names).includes('M0'), 'Realtime update gets priority');
    assert.equal(request.args.reference_name, 'PAT');
    assert.equal(nodes[0].attrs['data-wa-status'], 'read');
    assert.equal(nodes[1].attrs['data-wa-status'], 'failed');
    assert.equal(view.element.scrollTop, 80);
    assert.equal(view.$body.draft, 'Keep draft');
    assert.equal(scheduled, 10000, 'Status recovery shares the existing poll');
    nodes[0].attrs['data-wa-status-message'] = '123';
    obj.update_whatsapp_message_statuses(view, [{ name: 123, delivery_status: 'Delivered' }]);
    assert.equal(nodes[0].attrs['data-wa-status'], 'delivered', 'Database integer IDs match DOM string IDs');
    active = false;
    scheduled = undefined;
    obj.handle_whatsapp_status({ conversation: 'CONV', message: 'M2' });
    assert.equal(scheduled, undefined);
    console.log(`${app}: all indicators, outbound-only rendering, status-only updates, recovery and chat isolation passed`);
}
(async () => {
    await testConsole('vobiz_click_to_call');
    await testConsole('vobiz_system_call');
})().catch(error => { console.error(error); process.exitCode = 1; });
