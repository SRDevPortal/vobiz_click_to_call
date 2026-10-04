const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function setup() {
  const requests = [];
  const file = path.resolve(__dirname, '../vobiz_click_to_call',
    'page/vobiz_agent_console/vobiz_agent_console.js');
  const ctx = {frappe: {pages: {'vobiz-agent-console': {}}, show_alert() {},
    call(args) {requests.push(args); return Promise.resolve({message: {}});}}, __: value => value};
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(file, 'utf8') + '\nthis.ConsoleClass = VobizAgentConsole;', ctx);
  const obj = Object.create(ctx.ConsoleClass.prototype);
  obj.state = {softphone: {config: {}}};
  obj.get_softphone_tab_id = () => 'test-tab';
  obj.load = obj.update_workdesk_primary_action = () => {};
  const start = (row, choice) => obj.perform_start_call_for_row
    ? obj.perform_start_call_for_row(row, choice, true) : obj.start_call_for_row(row, choice);
  return {obj, requests, start};
}

test('private queue choice sends no masked or raw phone as destination', async () => {
  const t = setup();
  await t.start({doctype: 'CRM Lead', name: 'LEAD-1', phone: '******0101',
    phone_field: 'privacy:v1:opaque', phone_masked: true});
  const args = t.requests.at(-1).args;
  assert.equal(args.phone_field, 'privacy:v1:opaque');
  assert.equal(args.phone_number, null);
  assert.equal(args.reference_name, 'LEAD-1');
});

test('selected patient choice stays explicit', async () => {
  const t = setup();
  await t.start({doctype: 'Patient', name: 'PATIENT-1'}, {
    fieldname: 'privacy:v1:second-choice', number: '******0199', number_masked: true});
  const args = t.requests.at(-1).args;
  assert.equal(args.phone_field, 'privacy:v1:second-choice');
  assert.equal(args.phone_number, null);
  assert.equal(args.patient_phone_selected, 1);
});

test('unrestricted calling retains existing arguments', async () => {
  const t = setup();
  await t.start({doctype: 'CRM Lead', name: 'LEAD-1', phone: '2025550101', phone_field: 'mobile_no'});
  assert.equal(t.requests.at(-1).args.phone_number, '2025550101');
  assert.equal(t.requests.at(-1).args.phone_field, 'mobile_no');
});
