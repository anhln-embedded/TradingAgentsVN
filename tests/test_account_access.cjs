const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = Object.fromEntries(['accountQuota', 'btnOpenConfig', 'btnOpenGit', 'adminLink'].map(id => [id, {}]));
const calls = [];
let role = 'user';
const window = {fetch: async (url, options) => {
  calls.push({url, options});
  return {ok: true, status: 200, json: async () => ({user: {email: 'alice@example.com', role}, csrf: 'session-csrf',
    quota: {remaining: 8, reserved: 1}})};
}};
const context = {window, document: {getElementById: id => elements[id], addEventListener() {}},
  location: {href: 'http://localhost/', origin: 'http://localhost', assign() {}}, URL, Headers, console};
vm.runInNewContext(fs.readFileSync('web/static/account.js', 'utf8'), context);
(async () => {
  await window.TradingAccount.ready;
  assert.equal(elements.btnOpenConfig.hidden, true);
  assert.equal(elements.btnOpenGit.hidden, true);
  assert.equal(elements.adminLink.hidden, true);
  assert.match(elements.accountQuota.textContent, /8 lượt còn lại/);
  await window.fetch('/api/runs', {method: 'POST', headers: {'Content-Type': 'application/json'}});
  assert.equal(calls.at(-1).options.headers.get('X-CSRF-Token'), 'session-csrf');
  await window.fetch('https://another.example/', {method: 'POST'});
  assert.equal(calls.at(-1).options.headers, undefined, 'Never send CSRF credentials to another origin');
  role = 'superadmin';
  await window.TradingAccount.refresh();
  assert.equal(elements.btnOpenConfig.hidden, false);
  assert.equal(elements.adminLink.hidden, false);
  console.log('PASS: role-specific controls, remaining quota, CSRF requests, external-origin isolation');
})().catch(error => {console.error(error); process.exitCode = 1;});
