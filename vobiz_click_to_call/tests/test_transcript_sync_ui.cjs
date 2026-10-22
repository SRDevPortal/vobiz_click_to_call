const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
const path = require("node:path");
const root = path.join(__dirname, "..");

test("call-log list button preserves existing handler and manager restriction", () => {
    let prior = 0, added = 0;
    const frappe = {listview_settings: {"Vobiz Call Log": {onload: () => prior++}},
        user: {has_role: () => true}, set_route() {}};
    vm.runInNewContext(fs.readFileSync(path.join(root,"public/js/transcript_sync_list.js"),"utf8"), {frappe, __: x => x});
    const view = {page: {add_inner_button() {added++;}}};
    frappe.listview_settings["Vobiz Call Log"].onload(view);
    assert.equal(prior,1); assert.equal(added,1);
    frappe.user.has_role = () => false;
    frappe.listview_settings["Vobiz Call Log"].onload(view);
    assert.equal(prior,2); assert.equal(added,1);
});

test("run form exposes appropriate start/pause/resume controls", () => {
    let handlers;
    const frappe = {ui:{form:{on(name, value) {handlers=value;}}}};
    vm.runInNewContext(fs.readFileSync(path.join(root,"vobiz_click_to_call/doctype/vobiz_transcript_sync/vobiz_transcript_sync.js"),"utf8"), {frappe, __:x=>x});
    for (const [status, expected, absent] of [
        ["Draft","Start Sync","Pause"],["Running","Pause","Start Sync"],
        ["Paused","Resume","Pause"],["Failed","Resume","Pause"],
    ]) {
        const buttons=[];
        handlers.refresh({doc:{status}, is_new:()=>false, set_df_property(){},set_intro(){},
            add_custom_button(label) {buttons.push(label);}});
        assert.ok(buttons.includes(expected)); assert.ok(!buttons.includes(absent));
        assert.ok(buttons.includes("View Results")); assert.ok(buttons.includes("Refresh Progress"));
    }
});
