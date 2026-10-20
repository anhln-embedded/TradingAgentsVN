const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('web/static/app.js', 'utf8');
function readFunction(name) {
  const start = source.indexOf('  function ' + name + '(');
  const next = source.indexOf('\n  function ', start + 1);
  return source.slice(start, next < 0 ? undefined : next);
}
const el = new Proxy({}, {get(obj, key) {return obj[key] ||= {textContent: '', innerHTML: '', style: {}, classList: {add() {}, remove() {}}};}});
const context = {el, state: {}, window: {}, escapeHtml: text => text, renderMarkdown: (el, text) => el.innerHTML = text, updateStopLossPriceLine() {}};
vm.createContext(context);
for (const name of ['formatVNDPrice', 'stripThinkingBlock', 'extractSectionValue', 'resetDecisionSummary', 'parseDecisionMetrics', 'renderPortfolioManagerThesis', 'applySignalBadge']) vm.runInContext(readFunction(name), context);
const bsr = '**Action**: Sell\n**Entry Price**: 30000\n**Stop Loss**: 28300\n**Position Sizing**: bán 30-40% vị thế\n**Price Target**: 28000';
const mbb = '**Action**: Buy\n**Entry Price**: 19200.0\n**Stop Loss**: 18600.0\n**Position Sizing**: 15% - 20% tổng danh mục (thăm dò trước 6% - 8% không margin)\n**Reasoning**: Mục tiêu chốt lời: 19.950 – 20.000 VND';
context.parseDecisionMetrics(bsr);
context.parseDecisionMetrics(mbb);
assert.equal(el.metricEntry.textContent, '19,200 VND');
assert.equal(el.metricStopLoss.textContent, '18,600 VND');
assert.equal(el.metricSizing.textContent, '15% - 20% tổng danh mục (thăm dò trước 6% - 8% không margin)');
assert.equal(el.pillTargetVal.textContent, '19,950 - 20,000 VND');
assert.equal(el.pillRatingVal.textContent, 'BUY');
assert.equal(el.signalVal.textContent, 'MUA (BUY)');
context.parseDecisionMetrics('**Action**: Hold');
for (const key of ['metricEntry', 'metricStopLoss', 'metricSizing', 'pillTargetVal', 'pillHorizonVal']) assert.equal(el[key].textContent, '--');
assert.equal(context.extractSectionValue('## Entry Price\n19200.0', ['Entry Price']), '19200.0');
console.log('PASS: BSR → MBB, inline/multiline fields, price range, sizing, missing-field reset');
const start = source.indexOf('  async function selectTicker(');
const end = source.indexOf('\n  // Timeframe switcher', start);
let resolveOld;
context.resetReportData = () => {context.state.viewRequest = (context.state.viewRequest || 0) + 1;};
context.resetAgentNodes = () => {};
context.updateCompanyIdentity = () => {};
context.loadMarketChart = () => {};
context.fetchLiveQuote = () => {};
context.document = {querySelectorAll: () => []};
let loaded = [];
context.loadPreExistingReport = async (id, updateDate) => loaded.push([id, updateDate]);
context.fetch = async () => ({ok: true, json: async () => ({reports: [
  {id: 'old', ticker: 'MBB', saved_at: 1},
  {id: 'latest', ticker: 'MBB', saved_at: 3},
  {id: 'bsr', ticker: 'BSR', saved_at: 5},
]})});
vm.runInContext(source.slice(start, end), context);
(async () => {
  await context.selectTicker('MBB');
  assert.deepEqual(loaded, [['latest', true]]);
  loaded = [];
  await context.selectTicker('FPT');
  assert.match(el.reasoningContent.innerHTML, /Chưa có báo cáo phân tích cho FPT/);
  context.fetch = () => new Promise(resolve => {resolveOld = resolve;});
  const pending = context.selectTicker('BSR');
  context.fetch = async () => ({ok: true, json: async () => ({reports: [{id: 'mbb', ticker: 'MBB', saved_at: 4}]})});
  await context.selectTicker('MBB');
  resolveOld({ok: true, json: async () => ({reports: [{id: 'bsr', ticker: 'BSR', saved_at: 8}]})});
  await pending;
  assert.deepEqual(loaded, [['mbb', true]]);
  console.log('PASS: latest report per ticker, no report, stale response ignored');
})().catch(err => {console.error(err); process.exitCode = 1;});
