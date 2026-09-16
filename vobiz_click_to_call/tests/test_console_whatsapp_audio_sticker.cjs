const { JSDOM } = require('jsdom');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function check(app) {
    const apps = path.resolve(__dirname, '../../..');
    const dom = new JSDOM('<body><div id="chat"><div data-wa-chat-list data-conversation="CONV"></div></div></body>', { url: 'http://localhost:8000/app/vobiz-agent-console', runScripts: 'outside-only' });
    const w = dom.window;
    w.eval(fs.readFileSync(path.join(apps, 'frappe/node_modules/jquery/dist/jquery.js'), 'utf8'));
    const $ = w.$;
    let dialog, notices = [], requests = [], uploads = [], allowed = true, uploadFail = false, sendFail = false, pauseCount = 0;
    let created = 0, revoked = [];
    w.URL.createObjectURL = () => `blob:preview-${++created}`;
    w.URL.revokeObjectURL = url => revoked.push(url);
    w.HTMLMediaElement.prototype.pause = function () { pauseCount++; };
    w.HTMLMediaElement.prototype.load = function () {};
    w.__ = s => s;
    w.frappe = {
        pages: { 'vobiz-agent-console': {} }, csrf_token: 'TOKEN',
        utils: { escape_html: s => String(s) },
        show_alert: value => notices.push(value), msgprint: value => notices.push(value),
        call: async request => { requests.push(request); return { message: sendFail ? { success: false, result: { error: 'Provider unavailable' } } : { success: true } }; },
        ui: { Dialog: function (config) {
            dialog = this;
            this.config = config;
            this.$wrapper = $('<div>').appendTo('body');
            config.fields.filter(f => f.fieldtype === 'HTML').forEach(f => this.$wrapper.append(f.options));
            const button = $('<button>').appendTo(this.$wrapper);
            this.get_primary_btn = () => button;
            this.show = () => {};
            this.hide = () => { this.hidden = true; this.$wrapper.trigger('hidden.bs.modal'); };
        } },
    };
    w.fetch = async (url, options) => {
        uploads.push({ url, options });
        if (uploadFail) return { ok: false, json: async () => ({ message: { message: 'Upload rejected' } }) };
        return { ok: true, json: async () => ({ message: { success: true, result: { provider_file_url: 'https://example.com/media', file_url: '/files/media', file: 'FILE' } } }) };
    };
    const source = fs.readFileSync(path.join(apps, app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js'), 'utf8');
    w.eval(source + ';window.Console = VobizAgentConsole;');
    const obj = Object.create(w.Console.prototype);
    const body = $('#chat').data('whatsapp-reference', { reference_doctype: 'Patient', reference_name: 'PATIENT' });
    obj.state = { active_workdesk_body: body };
    obj.can_send_workdesk_whatsapp = () => allowed;
    obj.refresh_inline_whatsapp = () => {};
    const choose = file => {
        const input = dialog.$wrapper.find('input[type=file]');
        Object.defineProperty(input.get(0), 'files', { configurable: true, value: file ? [file] : [] });
        input.trigger('change');
    };
    try {
        const menu = $(obj.workdesk_whatsapp_composer_html());
        assert.equal(menu.find('[data-wa-attach-action=audio]').length, 1);
        assert.equal(menu.find('[data-wa-attach-action=sticker]').length, 1);
        for (const [kind, type, name] of [['audio', 'audio/mpeg', 'voice.mp3'], ['sticker', 'image/webp', 'wave.webp']]) {
            obj.open_workdesk_attachment_dialog(body, kind);
            assert.equal(dialog.config.fields.some(f => f.fieldname === 'caption'), false);
            await dialog.config.primary_action();
            assert.match(notices.at(-1).message, /Choose a file/);
            const valid = new w.File(['fixture'], name, { type });
            choose(valid);
            assert.equal(dialog.$wrapper.find(kind === 'audio' ? 'audio[controls]' : 'img').length, 1);
            assert.equal(dialog.$wrapper.find('[autoplay]').length, 0);
            const previousURL = `blob:preview-${created}`;
            choose(valid);
            assert.ok(revoked.includes(previousURL), 'Replacing the selected file releases its preview');
            const beforeUploads = uploads.length;
            const sending = dialog.config.primary_action();
            assert.equal(dialog.get_primary_btn().prop('disabled'), true);
            dialog.config.primary_action();
            await sending;
            assert.equal(uploads.length, beforeUploads + 1, 'Double click only uploads once');
            assert.equal(uploads.at(-1).options.body.get('kind'), kind);
            assert.equal(uploads.at(-1).options.body.get('reference_name'), 'PATIENT');
            assert.equal(uploads.at(-1).options.headers['X-Frappe-CSRF-Token'], 'TOKEN');
            assert.equal(requests.at(-1).args.content_type, kind === 'audio' ? 'Audio' : 'Sticker');
            assert.equal(requests.at(-1).args.body, '');
            assert.equal(requests.at(-1).args.media_url, 'https://example.com/media');
            assert.equal(requests.at(-1).args.display_media_url, '/files/media');
            assert.equal(requests.at(-1).args.attachment_file, 'FILE');
            assert.equal(dialog.hidden, true);
            assert.equal(obj.whatsapp_attachment_dialog, null);
            assert.equal(dialog.$wrapper.find('[data-wa-attachment-preview]').children().length, 0);
        }
        assert.ok(pauseCount > 0);
        for (const [kind, file, error] of [
            ['audio', new w.File(['wav'], 'voice.wav', { type: 'audio/wav' }), /MP3/],
            ['sticker', new w.File(['png'], 'photo.png', { type: 'image/png' }), /WebP/],
            ['audio', new w.File([], 'empty.mp3', { type: 'audio/mpeg' }), /empty/],
        ]) {
            obj.open_workdesk_attachment_dialog(body, kind);
            choose(file);
            const count = uploads.length;
            await dialog.config.primary_action();
            assert.match(notices.at(-1).message, error);
            assert.equal(uploads.length, count);
            dialog.hide();
        }
        assert.match(obj.whatsapp_attachment_error({ name: 'big.mp3', type: 'audio/mpeg', size: 16 * 1024 * 1024 + 1 }, 'audio'), /16 MB/);
        assert.match(obj.whatsapp_attachment_error({ name: 'big.webp', type: 'image/webp', size: 500 * 1024 + 1 }, 'sticker'), /500 KB/);
        assert.equal(obj.whatsapp_attachment_error({ name: 'voice.m4a', type: '', size: 100 }, 'audio'), '');
        obj.open_workdesk_attachment_dialog(body, 'audio');
        choose(new w.File(['fixture'], 'voice.mp3', { type: 'audio/mpeg' }));
        uploadFail = true;
        let count = requests.length;
        await dialog.config.primary_action();
        assert.equal(requests.length, count);
        assert.equal(dialog.hidden, undefined);
        assert.equal(dialog.get_primary_btn().prop('disabled'), false);
        assert.match(notices.at(-1).message, /Upload rejected/);
        uploadFail = false;
        sendFail = true;
        await dialog.config.primary_action();
        assert.match(notices.at(-1).message, /Provider unavailable/);
        assert.equal(dialog.hidden, undefined);
        sendFail = false;
        const originalFetch = w.fetch;
        let releaseUpload;
        w.fetch = async (...args) => {
            const response = await originalFetch(...args);
            await new Promise(resolve => { releaseUpload = resolve; });
            return response;
        };
        count = requests.length;
        let pending = dialog.config.primary_action();
        await new Promise(setImmediate);
        dialog.hide();
        releaseUpload();
        await pending;
        assert.equal(requests.length, count, 'Closing the dialog during upload cancels sending');
        obj.open_workdesk_attachment_dialog(body, 'audio');
        choose(new w.File(['fixture'], 'voice.mp3', { type: 'audio/mpeg' }));
        pending = dialog.config.primary_action();
        await new Promise(setImmediate);
        allowed = false;
        releaseUpload();
        await pending;
        assert.equal(requests.length, count, 'Window expiry during upload blocks sending');
        assert.match(notices.at(-1).message, /approved template/);
        w.fetch = originalFetch;
        allowed = false;
        count = uploads.length;
        await dialog.config.primary_action();
        assert.equal(uploads.length, count, 'Window closes before sending');
        await assert.rejects(obj.send_workdesk_whatsapp_media(body, 'CONV', {}), /approved template/);
        obj.stop_whatsapp_sync();
        assert.equal(obj.whatsapp_attachment_dialog, null);
        const lastDialog = dialog;
        obj.open_workdesk_attachment_dialog(body, 'sticker');
        assert.equal(dialog, lastDialog, 'Closed window prevents opening attachments');
        assert.equal(revoked.length, created, 'Every preview object URL is released');
        console.log(`${app}: audio/sticker selection, preview, sending, validation, errors, duplicate protection, window and cleanup passed`);
    } finally { w.close(); }
}
(async () => { await check('vobiz_click_to_call'); await check('vobiz_system_call'); })().catch(error => { console.error(error); process.exitCode = 1; });
