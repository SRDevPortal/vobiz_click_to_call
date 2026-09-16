// Requires jsdom (e.g. NODE_PATH=/tmp/vobiz-dom-check/node_modules node <this file>).
const { JSDOM } = require('jsdom');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function check(app) {
    const apps = path.resolve(__dirname, '../../..');
    const dom = new JSDOM('<body><div id="workdesk"></div></body>', {
        url: 'http://localhost:8000/app/vobiz-agent-console', runScripts: 'outside-only', pretendToBeVisual: true,
    });
    const w = dom.window;
    w.eval(fs.readFileSync(path.join(apps, 'frappe/node_modules/jquery/dist/jquery.js'), 'utf8'));
    w.__ = s => s;
    let requests = 0;
    const state = {
        can_send_free_form: true, server_time: '2026-09-16 12:00:00',
        free_form_expires_at: '2026-09-17 12:00:00', last_customer_message_at: '2026-09-16 12:00:00',
    };
    let response = { success: true, messages: [], message_statuses: [], unread_count: 0, messaging_window: state };
    let fail = false;
    w.frappe = {
        pages: { 'vobiz-agent-console': {} }, get_route: () => ['vobiz-agent-console'],
        utils: { escape_html: s => String(s || '') }, datetime: { str_to_user: s => s },
        call: () => {
            requests++;
            const deferred = w.$.Deferred();
            return (fail ? deferred.reject(new Error('Connection failed')) : deferred.resolve({ message: response })).promise();
        },
    };
    const source = fs.readFileSync(path.join(apps, app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js'), 'utf8');
    w.eval(source + ';window.Console = VobizAgentConsole;');
    const obj = Object.create(w.Console.prototype);
    const body = w.$('#workdesk');
    obj.state = { active_workdesk_body: body, queue: [] };
    body.data('whatsapp-reference', { reference_doctype: 'Patient', reference_name: 'TEST' });
    obj.schedule_whatsapp_sync = () => {};
    function render(windowState) {
        const wa = {
            available: true, conversation: 'CONV', messaging_window: windowState,
            messages: [{ name: 123, direction: 'Outbound', delivery_status: 'Read', body: 'Test' }],
        };
        body.html(obj.workdesk_whatsapp_html({ whatsapp: wa }));
        obj.initialize_whatsapp_window(body, wa);
        return wa;
    }
    try {
        render(state);
        assert.equal(requests, 0, 'Initial guidance does not wait for background polling');
        assert.match(body.find('[data-wa-window-text]').text(), /Messaging window open/);
        assert.equal(body.find('[data-wa-send]').prop('disabled'), false);
        assert.equal(body.find('[data-wa-reply]').prop('disabled'), false);
        body.find('[data-wa-reply]').val('Keep draft');
        fail = true;
        await obj.sync_inline_whatsapp();
        assert.equal(body.find('[data-wa-send]').prop('disabled'), false, 'Failed refresh retains a known unexpired window');
        assert.equal(body.find('[data-wa-reply]').val(), 'Keep draft');

        render(null);
        await obj.sync_inline_whatsapp();
        assert.match(body.find('[data-wa-window-text]').text(), /Could not check/);
        assert.notEqual(body.find('[data-wa-window-retry]').css('display'), 'none');
        assert.equal(body.find('[data-wa-send]').prop('disabled'), true);
        assert.equal(body.find('[data-wa-template]').first().prop('disabled'), false);

        fail = false;
        await obj.sync_inline_whatsapp();
        assert.match(body.find('[data-wa-window-text]').text(), /Messaging window open/);
        assert.equal(body.find('[data-wa-window-retry]').css('display'), 'none');
        assert.equal(body.find('[data-wa-send]').prop('disabled'), false);

        // A rendering problem in unrelated history must not keep the banner waiting.
        render(null);
        obj.append_live_whatsapp_messages = () => { throw new Error('History render failed'); };
        await obj.sync_inline_whatsapp();
        assert.match(body.find('[data-wa-window-text]').text(), /Messaging window open/);
        assert.equal(body.find('[data-wa-send]').prop('disabled'), false);

        const cached = render(state);
        cached.window_received_at = Date.now() - 25 * 60 * 60 * 1000;
        obj.initialize_whatsapp_window(body, cached);
        assert.match(body.find('[data-wa-window-text]').text(), /Messaging window closed/);
        assert.equal(body.find('[data-wa-send]').prop('disabled'), true, 'Reopening a cached tab cannot extend expiry');
        console.log(`${app}: real jQuery/DOM initial rendering, failed polls, retry recovery, expiry and draft preservation passed`);
    } finally {
        obj.stop_whatsapp_sync();
        w.close();
    }
}
(async () => {
    await check('vobiz_click_to_call');
    await check('vobiz_system_call');
})().catch(error => { console.error(error); process.exitCode = 1; });
