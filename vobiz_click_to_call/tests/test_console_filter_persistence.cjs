const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function setup(app, storage = new Map(), user = 'agent-a') {
  const requests = [];
  const events = {};
  const button = {
    val() { return ''; }, off() { return this; },
    one(name, cb) { events[name] = cb; return this; },
    popover() { const cb = events['shown.bs.popover']; delete events['shown.bs.popover']; if (cb) button.ready = cb(); return this; }
  };
  const ctx = {
    document: { addEventListener() {} },
    window: {localStorage: {
      getItem: k => storage.get(k) ?? null,
      setItem: (k, v) => storage.set(k, v),
      removeItem: k => storage.delete(k)
    }},
    frappe: {
      pages: {'vobiz-agent-console': {}}, session: {user},
      model: {with_doctype: (dt, cb) => cb()},
      ui: {FilterGroup: class {
        constructor(opts) { Object.assign(this, opts); this.rows = []; }
        clear_filters() {this.rows = [];}
        hide_popover() {this.on_change();}
        async add_filters(rows) {
          // Native controls emit changes while they are being constructed.
          for (const row of rows) {this.rows.push(row); this.on_change(); await Promise.resolve();}
        }
        get_filters() {return this.rows;}
      }},
      call(method, args) {
        const pending = {args};
        requests.push(pending);
        return {then(cb) {pending.respond = cb; return {always(done) {pending.done = done;}};}};
      }
    },
    __: value => value
  };
  vm.createContext(ctx);
  const file = path.resolve(__dirname, '../../../', app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js');
  vm.runInContext(fs.readFileSync(file, 'utf8') + '\nthis.ConsoleClass = VobizAgentConsole;', ctx);
  const obj = Object.create(ctx.ConsoleClass.prototype);
  obj.state = {queue_meta: obj.default_queue_meta(), queue_filters: [], queue_source_filter: '',
    queue_page: 1, selected_queue_keys: new Set()};
  obj.page = {main: {find() {return button;}}};
  obj.render_filter_button = () => {};
  obj.is_console_visible = () => true;
  for (const method of ['prune_selected_queue_keys', 'refresh_browser_softphone_call',
    'render_availability', 'render_queue', 'render_dispositions', 'render_manual_disposition_visibility',
    'render_active_call', 'refresh_workdesk_live_call', 'render_auto_toggle',
    'refresh_auto_dial_current', 'render_auto_live', 'maybe_continue_auto_dial', 'restore_workdesk_dialog']) {
    obj[method] = () => {};
  }
  return {obj, ctx, storage, requests, button};
}
const rows = [['CRM Lead', 'status', '=', 'Fresh'], ['CRM Lead', 'lead_owner', '=', 'agent-a']];

for (const app of ['vobiz_click_to_call', 'vobiz_system_call']) {
  test(app + ': saved filters survive a new console and clear stays cleared', () => {
    const a = setup(app);
    a.obj.reset_filter_group_if_doctype_changed();
    a.obj.apply_queue_filters(rows);
    const b = setup(app, a.storage);
    assert.equal(b.obj.load_saved_queue_source(), 'CRM Lead');
    assert.equal(b.obj.reset_filter_group_if_doctype_changed(), true);
    assert.equal(JSON.stringify(b.obj.state.queue_filters), JSON.stringify(rows));
    b.obj.apply_queue_filters([]);
    const c = setup(app, a.storage);
    assert.equal(c.obj.reset_filter_group_if_doctype_changed(), false);
    assert.equal(c.obj.state.queue_filters.length, 0);
  });

  test(app + ': saved filters are isolated by user and queue, even with same doctype', () => {
    const a = setup(app);
    a.obj.reset_filter_group_if_doctype_changed();
    a.obj.apply_queue_filters(rows);
    const other = setup(app, a.storage, 'agent-b');
    other.obj.reset_filter_group_if_doctype_changed();
    assert.equal(other.obj.state.queue_filters.length, 0);
    a.obj.state.queue_meta = {source: 'Discontinued', doctype: 'CRM Lead'};
    a.obj.reset_filter_group_if_doctype_changed();
    assert.equal(a.obj.state.queue_filters.length, 0);
    a.obj.apply_queue_filters([['CRM Lead', 'status', '=', 'Lost']]);
    a.obj.state.queue_meta = a.obj.default_queue_meta();
    a.obj.reset_filter_group_if_doctype_changed();
    assert.equal(JSON.stringify(a.obj.state.queue_filters), JSON.stringify(rows));
  });

  test(app + ': broken or unavailable storage never prevents filtering', () => {
    const a = setup(app);
    const scope = JSON.stringify(['CRM Lead', 'CRM Lead']);
    a.storage.set(a.obj.queue_filter_storage_key(scope), '{broken');
    a.obj.reset_filter_group_if_doctype_changed();
    assert.equal(a.obj.state.queue_filters.length, 0);
    a.ctx.window.localStorage.getItem = () => {throw Error('denied');};
    a.ctx.window.localStorage.setItem = () => {throw Error('denied');};
    assert.equal(a.obj.load_saved_queue_source(), '');
    a.obj.apply_queue_filters(rows);
    assert.equal(a.requests.length, 1);
  });

  test(app + ': first load refetches restored filters before showing unfiltered rows', () => {
    const a = setup(app);
    a.storage.set(a.obj.queue_filter_storage_key(JSON.stringify(['CRM Lead', 'CRM Lead'])), JSON.stringify(rows));
    let rendered = 0;
    a.obj.render_queue = () => rendered++;
    a.obj.load();
    a.requests[0].respond({message: {queue: [], queue_meta: a.obj.default_queue_meta()}});
    assert.equal(rendered, 0);
    a.requests[0].done();
    assert.equal(a.requests.length, 2);
    assert.equal(a.requests[1].args.filters, JSON.stringify(rows));
    a.requests[1].respond({message: {queue: [], queue_meta: a.obj.default_queue_meta()}});
    a.requests[1].done();
    assert.equal(rendered, 1);
    assert.equal(a.requests.length, 2);
  });

  test(app + ': filters applied during refresh cause a new request and discard stale results', () => {
    const a = setup(app);
    a.obj.reset_filter_group_if_doctype_changed();
    let rendered = 0;
    a.obj.render_queue = () => rendered++;
    a.obj.load();
    a.obj.apply_queue_filters(rows);
    a.requests[0].respond({message: {queue: [], queue_meta: a.obj.default_queue_meta()}});
    a.requests[0].done();
    assert.equal(rendered, 0);
    assert.equal(a.requests[1].args.filters, JSON.stringify(rows));
  });

  test(app + ': restoring native filter controls does not overwrite saved filters', async () => {
    const a = setup(app);
    a.obj.reset_filter_group_if_doctype_changed();
    a.obj.apply_queue_filters(rows);
    const before = a.storage.get(a.obj.queue_filter_storage_key(a.obj.state.saved_filter_scope));
    a.obj.open_filter_popover();
    await a.button.ready;
    assert.equal(JSON.stringify(a.obj.state.filter_group.get_filters()), JSON.stringify(rows));
    assert.equal(a.storage.get(a.obj.queue_filter_storage_key(a.obj.state.saved_filter_scope)), before);
    assert.equal(a.obj.restoring_queue_filters, false);
    a.obj.state.filter_group.clear_filters();
    a.obj.state.filter_group.on_change();
    assert.equal(a.obj.state.queue_filters.length, 0);
    assert.equal(a.storage.has(a.obj.queue_filter_storage_key(a.obj.state.saved_filter_scope)), false);
  });
}

