// Offline checks for price provenance and history rendering; no browser or network.
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = {};
const context = {
  $: id => elements[id] || (elements[id] = {value: '1'}),
  fmt: x => String(x), date: x => String(x), esc: x => String(x),
  busy: false, historySignature: ''
};
vm.createContext(context);
vm.runInContext(script.slice(script.indexOf('function seoulToday('), script.indexOf('function renderHistory(items)')), context);
assert.strictEqual(context.completionDate('2026-10-26', 5), '2026-10-31');
assert.strictEqual(context.completionDate('2026-10-26', 1), '2026-10-27');
assert.strictEqual(context.completionDate('2026-10-26', 0), '2026-10-26');
assert.strictEqual(context.completionDate('2026-12-31', 1), '2027-01-01');
assert.strictEqual(context.completionDate('2028-02-28', 1), '2028-02-29');
assert.strictEqual(context.completionDate('2026-02-30', 5), null);
assert.strictEqual(context.completionDate('2026-10-26', null), null);
assert.strictEqual(context.seoulToday(new Date('2026-10-26T15:00:00Z')), '2026-10-27');
context.seoulToday = () => '2026-10-26';
context.data = {target: 'root', items: {root: {name: '완제품', recipes: [{durationDays: 5}]}}};
context.lastResult = null;
context.renderSchedule();
assert(elements.craftFinish.textContent.includes('31일'));
assert(elements.durationSource.textContent.includes('페이지 소요기간 5일'));
assert(!html.includes('id="craftDays"'));
assert(!html.includes('gersang-duration'));
context.data.items.root.recipes[0].durationDays = null;
context.renderSchedule();
assert(elements.craftFinish.textContent.includes('자동 확인'));
context.data.items.root.recipes = [{method: '빠른 제작', durationDays: 3}, {method: '느린 제작', durationDays: 5}];
context.lastResult = {crafts: [{name: '완제품', method: '느린 제작'}]};
context.renderSchedule();
assert(elements.craftFinish.textContent.includes('31일'));
vm.runInContext(script.slice(script.indexOf('function comparison(r)'), script.indexOf('async function calculate()')), context);
context.comparison({feasible: true, total: 100, fees: 5, comparison: {
  available: true, source: 'archived', sampleCount: 2, observedAt: 'saved',
  price: 200, profit: 100, revenue: 200, returnPercent: 100
}});
assert.strictEqual(elements.quoteNotice.hidden, false);
assert(elements.quoteNotice.textContent.includes('수량은 가중하지'));
assert(elements.quoteNotice.textContent.includes('부정확한 참고 예상치'));
assert(elements.comparison.innerHTML.includes('과거 기록 평균'));
assert(elements.comparison.innerHTML.includes('약 200'));
assert(elements.comparison.innerHTML.includes('class="quote-time">마지막 조회: saved'));
context.comparison({feasible: true, total: 100, fees: 0, comparison: {
  available: true, source: 'cached', price: 200, profit: 100, revenue: 200, returnPercent: 100
}});
assert.strictEqual(elements.quoteNotice.hidden, true);
assert(!elements.comparison.innerHTML.includes('과거 기록 평균'));
assert(!elements.comparison.innerHTML.includes('class="quote-time"'));
context.comparison({comparison: {available: false, archiveChecked: true}});
assert(elements.comparison.innerHTML.includes('기록 없음'));
vm.runInContext(script.slice(script.indexOf('function renderHistory(items)'), script.indexOf('function remember()')), context);
context.renderHistory(['A', 'B', 'C', 'D', 'E']);
assert.strictEqual((elements.recentSearches.innerHTML.match(/data-recent=/g) || []).length, 5);
context.renderHistory([]);
assert.strictEqual(elements.recentSearches.innerHTML, '');
context.document = {querySelector: () => null};
context.CSS = {escape: value => value};
context.data = {target: 'root', items: {
  root: {name: '완제품', recipes: [{materials: {material: 30}, output: 1}]},
  material: {name: '없는재료', recipes: [], offers: [], fallbackQuote: {price: 900}, fallbackCheckedAt: 'checked'}
}};
context.saved = {}; context.choices = {}; context.collapsed = new Set();
context.lastResult = {feasible: false}; context.shortDate = () => 'saved';
vm.runInContext(script.slice(script.indexOf('function output(k)'), script.indexOf('function renderData(d)')), context);
context.renderTree();
assert(elements.inventory.innerHTML.includes('(※매물부족)'));
assert(elements.inventory.innerHTML.includes('900 냥 / 개 · 참고가'));
assert(elements.inventory.innerHTML.includes('계산 불가'));
assert(!elements.inventory.innerHTML.includes('계산 중'));
console.log('Offline UI checks passed.');
