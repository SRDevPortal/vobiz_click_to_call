const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const file = path.resolve(__dirname, '../public/js/availability.js');
// Expose closure controls only inside this VM; production has no test exports.
const source = fs.readFileSync(file, 'utf8').replace(/\}\)\(\);\s*$/, `
    window.testActivity = {
        record: recordActivity,
        configure: (status, mapped = true) => {
            trackingActivity = true;
            idleInactive = false;
            currentAvailability = {is_mapped: mapped, availability_status: status,
                                   idle_auto_offline_enabled: false};
        },
        interval: ACTIVITY_HEARTBEAT_MS,
    };
})();`);
const calls = [];
let throwOnCall = false;
const ctx = {
    window: {location: {pathname: '/app/user'}},
    document: {visibilityState: 'visible'},
    frappe: {
        get_route: () => ['List', 'User', 'List'],
        call: opts => {
            if (throwOnCall) throw new Error('transport setup failed');
            calls.push(opts);
        },
    },
    $: () => ({on() {}}), // Do not trigger application startup in the unit harness.
};
ctx.window.frappe = ctx.frappe;
vm.createContext(ctx);
vm.runInContext(source, ctx);
const activity = ctx.window.testActivity;
assert.equal(activity.interval, 60000, 'Presence refresh interval stays unchanged');
activity.configure('Available');
activity.record();
activity.record();
activity.record();
assert.equal(calls.length, 1, 'Route/heartbeat bursts share the outstanding request');
assert.equal(calls[0].args.route, 'List/User/List');
calls[0].always({}, 'success');
activity.record();
assert.equal(calls.length, 2, 'Successful completion allows the next heartbeat');
calls[1].always({}, 'error');
activity.record();
assert.equal(calls.length, 3, 'A failed request cannot permanently disable heartbeats');
calls[2].always({}, 'error');
throwOnCall = true;
assert.throws(() => activity.record(), /transport setup failed/);
throwOnCall = false;
activity.record();
assert.equal(calls.length, 4, 'Synchronous failures also release the in-flight guard');
calls[3].always({}, 'success');
ctx.document.visibilityState = 'hidden';
activity.record();
ctx.document.visibilityState = 'visible';
activity.configure('Away');
activity.record();
activity.configure('Available', false);
activity.record();
assert.equal(calls.length, 4, 'Hidden, away and unmapped sessions remain excluded');
console.log('Activity request concurrency checks passed');
