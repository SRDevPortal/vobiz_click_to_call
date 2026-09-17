// Requires jsdom; run with NODE_PATH pointing to its node_modules directory.
const { JSDOM } = require('jsdom');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function check(app) {
    const apps = path.resolve(__dirname, '../../..');
    const dom = new JSDOM('<body><div id="chat"><div data-wa-chat-list></div></div></body>', {
        url: 'http://localhost:8000/app/vobiz-agent-console', runScripts: 'outside-only', pretendToBeVisual: true,
    });
    const w = dom.window;
    w.eval(fs.readFileSync(path.join(apps, 'frappe/node_modules/jquery/dist/jquery.js'), 'utf8'));
    const $ = w.$;
    w.__ = s => s;
    let dialog, calls = [], uploads = 0;
    w.frappe = {
        pages: { 'vobiz-agent-console': {} },
        utils: { escape_html: value => String(value || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]) },
        msgprint: value => { throw new Error(value.message); },
        call: async args => { calls.push(args); return { message: { success: true, result: { sent: true } } }; },
        ui: { Dialog: function (config) {
            dialog = this;
            this.config = config;
            this.fields = Object.fromEntries(config.fields.map(field => [field.fieldname, { ...field }]));
            this.$wrapper = $('<div>').appendTo('body');
            for (const field of config.fields) if (field.fieldtype === 'HTML') this.$wrapper.append(field.options);
            const button = $('<button>').appendTo(this.$wrapper);
            this.get_primary_btn = () => button;
            this.set_df_property = (name, key, value) => { this.fields[name][key] = value; };
            this.set_value = () => {};
            this.show = () => {};
            this.hide = () => { this.hidden = true; };
        } },
    };
    const source = fs.readFileSync(path.join(apps, app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js'), 'utf8');
    w.eval(source + ';window.Console = VobizAgentConsole;');
    const obj = Object.create(w.Console.prototype);
    obj.refresh_inline_whatsapp = () => {};
    obj.upload_workdesk_whatsapp_file = async () => { uploads++; return { provider_file_url: 'https://media.example/upload.png' }; };
    const body = $('#chat').data('whatsapp-reference', { reference_doctype: 'Patient', reference_name: 'PAT' });
    body.find('[data-wa-chat-list]').data('wa-window-state', { state: { can_send_free_form: false } });
    const templates = [
        { name: 'appointment', language_code: 'hi', header_format: 'TEXT', header_preview: 'For {{1}}', header_variable_count: 1,
            body_preview: 'Dear {{2}}, contact {{1}}. Again: {{2}}.', body_variable_count: 2,
            body_variables: [{ index: 2, context: 'Dear {{2}}' }, { index: 1, context: 'contact {{1}}' }] },
        { name: 'plain', language_code: 'en', body_preview: 'Hello', body_variable_count: 0 },
        { name: 'photo', language_code: 'en', header_format: 'IMAGE', body_preview: 'Hello {{1}}', body_variable_count: 1 },
    ];
    try {
        obj.show_workdesk_template_dialog(body, 'CONV', templates);
        const fields = dialog.$wrapper.find('[data-wa-template-variable]');
        assert.deepEqual(Array.from(fields.map((i, el) => el.getAttribute('data-wa-template-variable')).get()), ['header_1', 'body_1', 'body_2']);
        assert.equal(dialog.fields.header_values.hidden, true, 'Text headers use guided inputs');
        assert.equal(Boolean(dialog.fields.followup_body.read_only), false, 'Template follow-up is not blocked by frontend window checks');
        assert.match(dialog.$wrapper.find('[data-wa-template-progress]').text(), /0 \/ 3/);
        await dialog.config.primary_action({});
        assert.equal(calls.length, 0);
        assert.equal(uploads, 0);
        assert.match(dialog.$wrapper.find('[data-wa-template-variable-errors]').text(), /Header \{\{1\}\}.*Message \{\{1\}\}.*Message \{\{2\}\}/);
        assert.equal(w.document.activeElement, fields.get(0), 'Missing field receives focus');

        function fill(key, value) { dialog.$wrapper.find(`[data-wa-template-variable="${key}"]`).val(value).trigger('input'); }
        fill('header_1', 'Delhi');
        fill('body_1', 'Sharma, Amit');
        fill('body_2', '<img src=x onerror=alert(1)> $& {{1}}');
        const expected = 'Dear <img src=x onerror=alert(1)> $& {{1}}, contact Sharma, Amit. Again: <img src=x onerror=alert(1)> $& {{1}}.';
        const preview = dialog.$wrapper.find('[data-wa-template-preview]');
        assert.equal(preview.find('img').length, 0, 'Entered text is escaped in preview');
        assert.ok(preview.text().includes(expected), 'Repeated placeholders, commas, dollars and literal placeholders stay intact');
        assert.ok(preview.text().includes('For Delhi'));
        assert.match(dialog.$wrapper.find('[data-wa-template-progress]').text(), /3 \/ 3/);
        await dialog.config.primary_action({});
        assert.deepEqual(Array.from(calls[0].args.body_values), ['Sharma, Amit', '<img src=x onerror=alert(1)> $& {{1}}']);
        assert.deepEqual(Array.from(calls[0].args.header_values), ['Delhi']);
        assert.equal(calls[0].args.body_preview, expected);
        assert.equal(calls[0].args.language_code, 'hi');
        assert.equal(calls[0].args.reference_name, 'PAT');

        dialog.$wrapper.find('[data-wa-template-select]').val('1').trigger('change');
        assert.equal(dialog.$wrapper.find('[data-wa-template-variable]').length, 0);
        assert.match(dialog.$wrapper.find('[data-wa-template-variables]').text(), /no variables/);
        assert.ok(!dialog.$wrapper.find('[data-wa-template-preview]').text().includes('Sharma'));
        await dialog.config.primary_action({});
        assert.deepEqual(Array.from(calls.at(-1).args.body_values), []);
        assert.deepEqual(Array.from(calls.at(-1).args.header_values), []);

        dialog.$wrapper.find('[data-wa-template-select]').val('2').trigger('change');
        assert.equal(dialog.fields.header_image.hidden, false);
        assert.equal(dialog.fields.header_values.hidden, false, 'Media URL remains separate from text variables');
        const before = calls.length;
        await dialog.config.primary_action({});
        assert.equal(calls.length, before, 'Image template cannot send with missing body variables');
        fill('body_1', '0');
        await dialog.config.primary_action({ header_values: 'https://media.example/photo.png?signature=a,b' });
        assert.deepEqual(Array.from(calls.at(-1).args.body_values), ['0']);
        assert.deepEqual(Array.from(calls.at(-1).args.header_values), ['https://media.example/photo.png?signature=a,b']);
        assert.equal(calls.at(-1).args.body_preview, 'Hello 0');

        assert.equal(obj.template_variable_slots({ body_variable_count: 3 }, 'body').length, 3, 'Declared counts work without preview text');
        assert.equal(obj.template_variable_slots({ body_preview: '{{2}} and {{1}} and {{2}}' }, 'body').map(slot => slot.index).join(','), '1,2');
        console.log(`${app}: guided fields, context, required validation, safe live preview, ordering, switching and media templates passed`);
    } finally { w.close(); }
}
(async () => {
    await check('vobiz_click_to_call');
    await check('vobiz_system_call');
})().catch(error => { console.error(error); process.exitCode = 1; });
