// Requires jsdom; run with NODE_PATH pointing to its node_modules directory.
const { JSDOM } = require('jsdom');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function check(app) {
    const apps = path.resolve(__dirname, '../../..');
    const dom = new JSDOM('<body><div id="chat"></div></body>', {
        url: 'http://localhost:8000/app/vobiz-agent-console', runScripts: 'outside-only', pretendToBeVisual: true,
    });
    const w = dom.window;
    w.eval(fs.readFileSync(path.join(apps, 'frappe/node_modules/jquery/dist/jquery.js'), 'utf8'));
    const $ = w.$;
    w.__ = s => s;
    let dialog;
    w.frappe = {
        pages: { 'vobiz-agent-console': {} }, get_route: () => ['vobiz-agent-console'],
        utils: { escape_html: value => String(value || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]) },
        datetime: { str_to_user: value => value },
        ui: { Dialog: function (config) {
            dialog = this;
            this.$wrapper = $('<div>').appendTo('body');
            config.fields.forEach(field => this.$wrapper.append(field.options));
            this.show = () => {};
            this.hide = () => { this.hidden = true; this.$wrapper.trigger('hidden.bs.modal'); };
        } },
    };
    const source = fs.readFileSync(path.join(apps, app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js'), 'utf8');
    w.eval(source + ';window.Console = VobizAgentConsole;');
    const obj = Object.create(w.Console.prototype);
    const body = $('#chat').data('whatsapp-reference', { reference_doctype: 'Patient', reference_name: 'PAT & ONE' });
    obj.state = { active_workdesk_body: body };
    const messages = [
        { name: 1, direction: 'Inbound', content_type: 'Image', media_url: 'https://media.example/photo.png', body: '<script>caption</script>' },
        { name: 2, direction: 'Outbound', content_type: 'Template', media_content_type: 'Image', media_url: 'https://media.example/opaque?signature=a,b', body: 'Template image' },
        { name: 3, direction: 'Inbound', content_type: 'Image', media_url: '/private/files/image.png' },
        { name: 4, direction: 'Inbound', content_type: 'Audio', media_url: 'https://media.example/voice.ogg' },
        { name: 5, direction: 'Inbound', content_type: 'Video', media_url: 'https://media.example/video.mp4' },
        { name: 6, direction: 'Inbound', content_type: 'Document', media_url: 'https://media.example/report.pdf' },
    ];
    body.html(obj.workdesk_whatsapp_messages_html(messages, { conversation: 'CONV' }));
    try {
        assert.equal(body.find('[data-wa-image-view]').length, 3, 'Includes extensionless image templates');
        assert.equal(body.find('audio[controls]').length, 1);
        assert.equal(body.find('video[controls]').length, 1);
        assert.equal(body.find('audio').attr('preload'), 'none');
        assert.equal(body.find('audio').attr('autoplay'), undefined);
        assert.equal(obj.workdesk_whatsapp_media_html({ content_type: 'Image', media_url: 'javascript:alert(1)' }), '');
        const $document = body.find('[data-wa-message="6"]');
        assert.equal($document.find('.vobiz-wa-media-link').attr('href'), messages[5].media_url);
        const $download = $document.find('[data-wa-media-download]');
        obj.prepare_whatsapp_media_download(body, { currentTarget: $download.get(0) });
        const downloadURL = new URL($download.attr('href'), w.location.origin);
        assert.equal(downloadURL.searchParams.get('message'), '6');
        assert.equal(downloadURL.searchParams.get('download'), '1');
        assert.equal(downloadURL.searchParams.get('reference_name'), 'PAT & ONE');

        const images = body.find('[data-wa-image-view]');
        obj.open_whatsapp_media_viewer(body, images.get(1));
        const root = dialog.$wrapper;
        const image = () => root.find('[data-wa-viewer-stage] img');
        const click = selector => root.find(selector).trigger('click');
        assert.equal(root.find('[data-wa-viewer-count]').text(), '2 / 3');
        assert.equal(image().attr('src'), messages[1].media_url);
        image().trigger('load');
        assert.equal(root.find('[data-wa-viewer-status]').text(), '');
        click('[data-wa-viewer-zoom-in]');
        assert.equal(obj.whatsapp_media_viewer.scale, 1.25);
        for (let i = 0; i < 20; i++) click('[data-wa-viewer-zoom-in]');
        assert.equal(obj.whatsapp_media_viewer.scale, 4);
        assert.equal(root.find('[data-wa-viewer-zoom-in]').prop('disabled'), true);
        click('[data-wa-viewer-reset]');
        assert.equal(obj.whatsapp_media_viewer.scale, 1);
        for (let i = 0; i < 10; i++) click('[data-wa-viewer-zoom-out]');
        assert.equal(obj.whatsapp_media_viewer.scale, 0.5);

        click('[data-wa-viewer-next]');
        assert.equal(image().attr('src'), '/private/files/image.png');
        assert.equal(root.find('[data-wa-viewer-download]').attr('href'), '/private/files/image.png');
        assert.equal(obj.whatsapp_media_viewer.scale, 1, 'Changing images resets zoom');
        root.trigger($.Event('keydown', { key: 'ArrowRight' }));
        assert.equal(root.find('[data-wa-viewer-count]').text(), '1 / 3');
        assert.equal(root.find('[data-wa-viewer-caption]').text(), '<script>caption</script>');
        assert.equal(root.find('script').length, 0);
        image().trigger('error');
        const proxy = new URL(image().attr('src'), w.location.origin);
        assert.equal(proxy.searchParams.get('message'), '1');
        assert.equal(proxy.searchParams.get('reference_name'), 'PAT & ONE');
        assert.equal(proxy.searchParams.has('download'), false);
        image().trigger('error');
        assert.match(root.find('[data-wa-viewer-status]').text(), /could not be loaded/);
        assert.equal(image().css('display'), 'none');
        root.trigger($.Event('keydown', { key: 'Escape' }));
        assert.equal(obj.whatsapp_media_viewer, null);
        assert.equal(dialog.hidden, true);
        assert.equal(image().length, 0);

        obj.open_whatsapp_media_viewer(body, images.get(0));
        let paused = 0;
        body.find('[data-wa-playback]').each((_, media) => { media.pause = () => paused++; });
        obj.stop_whatsapp_sync();
        assert.equal(obj.whatsapp_media_viewer, null, 'Leaving chat closes viewer');
        assert.equal(paused, 2, 'Leaving chat pauses audio and video');
        assert.equal(dialog.hidden, true);
        console.log(`${app}: image/template viewer, zoom, navigation, download access context, load recovery, audio/video and lifecycle passed`);
    } finally { obj.close_whatsapp_media_viewer(); w.close(); }
}
(async () => {
    await check('vobiz_click_to_call');
    await check('vobiz_system_call');
})().catch(error => { console.error(error); process.exitCode = 1; });
