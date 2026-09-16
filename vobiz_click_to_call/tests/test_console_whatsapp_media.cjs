const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const apps = path.resolve(__dirname, '../../..');
const reference = { reference_doctype: 'Patient', reference_name: 'PATIENT' };
const providerUrl = 'https://media.example/image.png?signature=a,b';
const uploaded = { file_url: '/files/image.png', media_url: providerUrl, file: 'FILE' };

async function checkConsole(app) {
    let dialog, request, fetched, notices = [], selectedFile;
    let reply = { success: true, result: { sent: true } };
    let uploadResponse = { ok: true, json: async () => ({ message: { success: true, result: uploaded } }) };
    const button = { prop() { return this; }, text() { return this; } };
    const body = {
        data: () => reference,
        find: () => ({ first: () => ({ attr: () => 'CONV', data: () => null }) }),
    };
    const ctx = {
        __: s => s,
        FormData: class { constructor() { this.values = {}; } append(k, v) { this.values[k] = v; } },
        fetch: async (url, options) => { fetched = { url, options }; return uploadResponse; },
        frappe: {
            pages: { 'vobiz-agent-console': {} }, csrf_token: 'TOKEN',
            utils: { escape_html: s => s }, show_alert: value => notices.push(value),
            msgprint: value => notices.push(value),
            call: async args => { request = args; return { message: reply }; },
            ui: { Dialog: function (config) {
                dialog = this;
                this.config = config;
                this.hidden = false;
                this.fields = Object.fromEntries(config.fields.map(f => [f.fieldname, { ...f }]));
                this.get_primary_btn = () => button;
                this.show = () => {};
                this.hide = () => { this.hidden = true; };
                this.set_value = () => {};
                this.set_df_property = (field, key, value) => { this.fields[field][key] = value; };
                this.$wrapper = {
                    on() {},
                    find: selector => ({
                        val(value) {
                            if (selector.includes('template-select')) return '0';
                            if (value === '') selectedFile = null;
                            return '';
                        },
                        get: () => ({ files: selectedFile ? [selectedFile] : [] }),
                        html() { return this; },
                        text() { return this; },
                        hide() { return this; },
                        attr() { return this; },
                    }),
                };
            } },
        },
    };
    vm.createContext(ctx);
    const page = path.join(apps, app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js');
    vm.runInContext(fs.readFileSync(page, 'utf8') + ';this.Console = VobizAgentConsole;', ctx);
    const obj = Object.create(ctx.Console.prototype);
    obj.refresh_inline_whatsapp = () => {};
    obj.setup_template_image_preview = dialog => ({ reset() { dialog.$wrapper.find('[data-wa-template-image]').val(''); } });
    obj.can_send_workdesk_whatsapp = () => true;

    await obj.upload_workdesk_whatsapp_file('CONV', {}, true, reference);
    assert.equal(fetched.url, '/api/method/vobiz_click_to_call.api.console.upload_whatsapp_media');
    assert.equal(fetched.options.body.values.reference_name, 'PATIENT');
    assert.equal(fetched.options.body.values.kind, 'image');
    assert.equal(fetched.options.headers['X-Frappe-CSRF-Token'], 'TOKEN');
    uploadResponse = { ok: false, json: async () => ({}) };
    await assert.rejects(() => obj.upload_workdesk_whatsapp_file('CONV', {}, true), /Upload failed/);
    uploadResponse = { ok: true, json: async () => ({ message: { result: { file_url: '/files/a.png' } } }) };
    await assert.rejects(() => obj.upload_workdesk_whatsapp_file('CONV', {}, true), /hosted media URL/);
    uploadResponse = { ok: true, json: async () => ({ message: { success: true, result: uploaded } }) };

    obj.open_workdesk_attachment_dialog(body, 'image');
    selectedFile = { name: 'image.png', type: 'image/png' };
    dialog.config.primary_action({ caption: 'Hello' });
    await new Promise(setImmediate);
    assert.equal(request.method, 'vobiz_click_to_call.api.console.send_whatsapp_media');
    assert.equal(request.args.media_url, providerUrl, 'Provider must receive hosted URL, not /files path');
    assert.equal(request.args.display_media_url, '/files/image.png');
    assert.equal(request.args.attachment_file, 'FILE');
    assert.equal(request.args.reference_name, 'PATIENT');
    assert.equal(dialog.hidden, true);

    const template = { name: 'welcome_image', header_format: 'IMAGE', body_preview: 'Hello' };
    obj.show_workdesk_template_dialog(body, 'CONV', [template]);
    assert.equal(dialog.fields.header_image.hidden, false);
    assert.equal(dialog.fields.header_values.label, 'Header Media URL');
    selectedFile = { name: 'image.png', type: 'image/png' };
    await dialog.config.primary_action({});
    assert.equal(request.method, 'vobiz_click_to_call.api.console.send_whatsapp_template');
    assert.equal(request.args.header_values[0], providerUrl);
    assert.equal(request.args.reference_name, 'PATIENT');
    assert.equal(dialog.hidden, true);

    obj.show_workdesk_template_dialog(body, 'CONV', [template]);
    await dialog.config.primary_action({});
    assert.equal(request.args.header_values, '', 'Blank header lets backend use approved template media');
    obj.show_workdesk_template_dialog(body, 'CONV', [template]);
    await dialog.config.primary_action({ header_values: providerUrl });
    assert.equal(request.args.header_values[0], providerUrl, 'Signed media URLs must not be split on commas');
    reply = { success: false, result: { sent: false, error: 'Image URL rejected' } };
    obj.show_workdesk_template_dialog(body, 'CONV', [template]);
    await dialog.config.primary_action({});
    assert.equal(dialog.hidden, false, 'Failed send must remain visible');
    assert.equal(notices.at(-1).message, 'Image URL rejected');

    obj.show_workdesk_template_dialog(body, 'CONV', [{ name: 'text', header_format: 'TEXT' }]);
    assert.equal(dialog.fields.header_image.hidden, true);
    console.log(`${app}: image upload, authorization context, template headers and failures passed`);
}

(async () => {
    await checkConsole('vobiz_click_to_call');
    await checkConsole('vobiz_system_call');
})().catch(err => { console.error(err); process.exitCode = 1; });
