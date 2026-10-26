const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function setup(app) {
  const requests = [];
  const ctx = {
    document: {addEventListener() {}},
    frappe: {pages: {'vobiz-agent-console': {}}, show_alert() {},
      utils: {escape_html: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;')},
      call(args) {requests.push(args); return Promise.resolve({message: {}});}},
    __: value => value
  };
  vm.createContext(ctx);
  const file = path.resolve(__dirname, '../../../', app, app, app, 'page/vobiz_agent_console/vobiz_agent_console.js');
  vm.runInContext(fs.readFileSync(file, 'utf8') + '\nthis.ConsoleClass = VobizAgentConsole;', ctx);
  const obj = Object.create(ctx.ConsoleClass.prototype);
  obj.state = {softphone: {config: {}}};
  obj.matching_active_call = () => null;
  obj.load = obj.update_workdesk_primary_action = () => {};
  obj.get_softphone_tab_id = () => 'test-tab';
  return {obj, requests};
}

for (const app of ['vobiz_click_to_call', 'vobiz_system_call']) {
  for (const masked of [false, true]) {
    test(`${app}: selected Phone reaches Start Call (${masked ? 'masked' : 'full'})`, async () => {
      const {obj, requests} = setup(app);
      const choices = [
        {fieldname: masked ? 'privacy:v1:mobile' : 'mobile_no', number: masked ? '******3210' : '9876543210', label: 'Mobile Number', number_masked: masked},
        {fieldname: masked ? 'privacy:v1:phone' : 'phone', number: masked ? '******3211' : '9876543211', label: 'Phone Number', number_masked: masked},
      ];
      const row = {doctype: 'CRM Lead', name: 'TEST-LEAD', call_phone_choices: choices};
      obj.select_workdesk_phone(row, 1);
      assert.equal(row.phone, choices[1].number);
      const html = obj.workdesk_phone_selector_html(row);
      assert.ok(html.includes('value="1" selected'));
      assert.ok(html.includes('Phone Number'));
      await obj.handle_workdesk_primary_action(row);
      const args = requests.at(-1).args;
      assert.equal(args.phone_field, choices[1].fieldname);
      assert.equal(args.phone_number, masked ? null : choices[1].number);
      assert.equal(args.reference_name, row.name);
      if (masked) assert.ok(!JSON.stringify(args).includes('9876543211'));
    });
  }

  test(`${app}: one choice, missing choice, escaping and active-call lock`, () => {
    const {obj} = setup(app);
    const row = {call_phone_choices: [{fieldname: 'phone', number: '9876543211', label: '<Phone>'}]};
    obj.select_workdesk_phone(row, -1);
    assert.equal(row.phone_field, 'phone');
    assert.ok(obj.workdesk_phone_selector_html(row).includes('&lt;Phone>'));
    assert.equal(obj.workdesk_phone_selector_html({}), '');
    const properties = {};
    const chain = {prop(key, value) {properties[key] = value; return this;}, toggleClass() {return this;}, attr() {return this;}, html() {return this;}};
    const selector = {prop(key, value) {properties['selector_' + key] = value; return this;}};
    obj.state.active_workdesk_body = {length: 1, find(query) {return query === '[data-workdesk-phone]' ? selector : chain;}};
    obj.is_terminal_status = status => status === 'Completed';
    obj.update_workdesk_header_call_action(row, {name: 'CALL', status: 'Connected'});
    assert.equal(properties.selector_disabled, true);
    obj.update_workdesk_header_call_action(row, {name: 'CALL', status: 'Completed'});
    assert.equal(properties.selector_disabled, false);
    // The selector is locked while the existing call's Stop button remains usable.
    let stopped;
    obj.matching_active_call = () => ({name: 'CALL', status: 'Connected'});
    obj.cancel_call_log = name => {stopped = name;};
    obj.handle_workdesk_primary_action(row);
    assert.equal(stopped, 'CALL');
  });
}
