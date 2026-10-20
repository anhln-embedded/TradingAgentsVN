const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('web/static/app.js', 'utf8');
const start = source.indexOf('  async function syncActiveRun()');
const end = source.indexOf('\n  await syncActiveRun();', start);
const sync = source.slice(start, end);
const saved = {id: 'existing-run', ticker: 'MBB', trade_date: '2026-10-15', analysts: ['market'], snapshot: {}};
let resumed;
const context = {
  el: {tickerInput: {}, tradeDateInput: {}},
  state: {currentTicker: 'BSR'}, console,
  fetch: async () => ({ok: true, json: async () => ({run: saved})}),
  startAnalysis: async run => {resumed = run; context.state.currentRunId = run.id;},
};
vm.createContext(context);
vm.runInContext(sync, context);
(async () => {
  await context.syncActiveRun();
  assert.deepEqual(resumed, saved);
  assert.equal(context.el.tickerInput.value, 'MBB');
  assert.equal(context.el.tradeDateInput.value, saved.trade_date);
  resumed = null;
  await context.syncActiveRun();
  assert.equal(resumed, null, 'Do not reconnect an already displayed run');
  context.state.currentRunId = null;
  context.state.isAnalyzing = true;
  await context.syncActiveRun();
  assert.equal(resumed, null, 'Do not replace a running view');
  context.state.isAnalyzing = false;
  context.fetch = async () => ({ok: true, json: async () => ({run: null})});
  await context.syncActiveRun();
  assert.equal(resumed, null);
  console.log('PASS: server-owned session discovery, F5 restore, duplicate/active guards, empty workspace');
})().catch(error => {console.error(error); process.exitCode = 1;});
