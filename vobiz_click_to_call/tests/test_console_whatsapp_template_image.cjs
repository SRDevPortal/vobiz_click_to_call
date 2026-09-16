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
    let dialog, uploads = 0, sends = [], objects = 0;
    const revoked = [];
    w.URL.createObjectURL = file => 'blob:local-' + (++objects);
    w.URL.revokeObjectURL = url => revoked.push(url);
    w.frappe = {
        pages: { 'vobiz-agent-console': {} },
        utils: { escape_html: value => String(value || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]) },
        msgprint: value => { throw new Error(value.message); },
        call: async args => { sends.push(args); return { message: { success: true, result: { sent: true } } }; },
        ui: { Dialog: function (config) {
            dialog = this;
            this.config = config;
            this.fields = Object.fromEntries(config.fields.map(field => [field.fieldname, { ...field }]));
            this.$wrapper = $('<div>').appendTo('body');
            for (const field of config.fields) {
                if (field.fieldtype === 'HTML') this.$wrapper.append(field.options);
                else this.$wrapper.append($('<div>').attr('data-fieldname', field.fieldname).append('<textarea>'));
            }
            const button = $('<button>').appendTo(this.$wrapper);
            this.get_primary_btn = () => button;
            this.set_df_property = (name, key, value) => { this.fields[name][key] = value; };
            this.get_value = name => this.$wrapper.find(`[data-fieldname="${name}"] textarea`).val();
            this.set_value = (name, value) => this.$wrapper.find(`[data-fieldname="${name}"] textarea`).val(value);
            this.show = () => {};
            this.hide = () => this.$wrapper.trigger('hidden.bs.modal');
        } },
    };
    const source = fs.readFileSync(path.join(apps, app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js'), 'utf8');
    w.eval(source + ';window.Console = VobizAgentConsole;');
    const obj = Object.create(w.Console.prototype);
    obj.refresh_inline_whatsapp = () => {};
    obj.upload_workdesk_whatsapp_file = async () => { uploads++; return { provider_file_url: 'https://media.example/uploaded.png' }; };
    const body = $('#chat').data('whatsapp-reference', { reference_doctype: 'Patient', reference_name: 'PAT' });
    body.find('[data-wa-chat-list]').data('wa-window-state', { state: { can_send_free_form: false } });
    const approved = 'https://media.example/approved.png?signature=a,b';
    const templates = [
        { name: 'photo', header_format: 'IMAGE', header_media_url: approved, body_preview: 'Hello {{1}}', body_variable_count: 1 },
        { name: 'text', body_preview: 'Text only' },
        { name: 'missing', header_format: 'IMAGE', body_preview: 'Needs image' },
    ];
    try {
        obj.show_workdesk_template_dialog(body, 'CONV', templates);
        const preview = () => dialog.$wrapper.find('[data-wa-template-image-preview]');
        const img = () => preview().find('img');
        const status = () => preview().find('[data-wa-template-image-status]').text();
        const header = dialog.$wrapper.find('[data-fieldname="header_values"] textarea');
        const fileInput = dialog.$wrapper.find('[data-wa-template-image]').get(0);
        const setFile = file => Object.defineProperty(fileInput, 'files', { configurable: true, value: file ? [file] : [] });
        assert.equal(img().attr('src'), approved);
        assert.match(status(), /Loading/);
        img().trigger('load');
        assert.equal(status(), '');
        assert.equal(uploads, 0, 'Preview never uploads approved media');
        assert.equal(sends.length, 0);

        header.val('https://media.example/replacement.png?signature=c,d').trigger('input');
        assert.equal(img().attr('src'), header.val());
        assert.match(preview().find('[data-wa-template-image-source]').text(), /Replacement image URL/);
        const staleImage = img();
        setFile(new w.File(['test'], 'replacement.png', { type: 'image/png' }));
        $(fileInput).trigger('change');
        assert.equal(img().attr('src'), 'blob:local-1', 'Selected file takes precedence over typed URL');
        staleImage.trigger('error');
        assert.match(status(), /Loading/, 'Late failure from old image cannot overwrite current preview');
        img().trigger('load');
        assert.equal(status(), '');
        assert.match(preview().find('[data-wa-template-image-source]').text(), /replacement.png/);
        dialog.$wrapper.find('[data-wa-template-variable]').val('Amit').trigger('input');
        assert.equal(img().attr('src'), 'blob:local-1', 'Editing variables retains the image preview');
        assert.equal(objects, 1);
        assert.equal(uploads, 0, 'Local preview does not upload the file');
        assert.equal(sends.length, 0);

        setFile(null); // Browser clears its FileList when the reset button clears the input.
        preview().find('[data-wa-template-image-reset]').trigger('click');
        assert.equal(header.val(), '');
        assert.equal(img().attr('src'), approved);
        assert.deepEqual(revoked, ['blob:local-1']);
        assert.equal(dialog.$wrapper.find('[data-wa-template-variable]').val(), 'Amit', 'Resetting image keeps entered variables');

        img().trigger('error');
        assert.equal(img().css('display'), 'none');
        assert.match(status(), /could not be loaded/);
        header.val('javascript:alert(1)').trigger('input');
        assert.equal(img().length, 0);
        assert.match(status(), /valid public image URL/);
        header.val('').trigger('input');
        assert.equal(img().attr('src'), approved);

        dialog.$wrapper.find('[data-wa-template-select]').val('1').trigger('change');
        assert.equal(preview().css('display'), 'none');
        assert.equal(img().length, 0, 'Text templates have no leftover image');
        dialog.$wrapper.find('[data-wa-template-select]').val('2').trigger('change');
        assert.match(status(), /No approved image/);
        setFile(new w.File(['test'], 'bad.txt', { type: 'text/plain' }));
        $(fileInput).trigger('change');
        assert.equal(img().length, 0);
        assert.match(status(), /Choose an image file/);

        setFile(new w.File(['test'], 'send.png', { type: 'image/png' }));
        $(fileInput).trigger('change');
        assert.equal(img().attr('src'), 'blob:local-2');
        await dialog.config.primary_action({});
        assert.equal(uploads, 1, 'Upload occurs only after Send Template');
        assert.deepEqual(Array.from(sends[0].args.header_values), ['https://media.example/uploaded.png']);
        assert.ok(revoked.includes('blob:local-2'), 'Closing dialog releases the local image URL');
        assert.equal(img().length, 0);
        console.log(`${app}: approved image, URL/file replacement, reset, load errors, stale events, template switching and upload timing passed`);
    } finally { w.close(); }
}
(async () => {
    await check('vobiz_click_to_call');
    await check('vobiz_system_call');
})().catch(error => { console.error(error); process.exitCode = 1; });
