// Source-level UI contracts. Uses synthetic records and no browser/network access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness() {
  const nodes = new Map();
  const buttons = Array.from({length: 5}, () => ({disabled: false}));
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      innerHTML: '', textContent: '', hidden: false, className: '',
      setAttribute() {}, addEventListener() {},
      classList: {add() {}, remove() {}, toggle() {}},
    });
    return nodes.get(selector);
  };
  const context = vm.createContext({
    document: {querySelector: node, querySelectorAll: () => buttons, addEventListener() {}},
    window: {addEventListener() {}}, location: {hash: '#sectors'},
    localStorage: {getItem: () => null},
    setTimeout: () => 0, clearTimeout() {}, requestAnimationFrame() {}, URL,
  });
  const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
  vm.runInContext(source.replace(/\nboot\(\);\s*$/, '\n'), context);
  vm.runInContext("state.view='sectors'; render=function() {};", context);
  const run = code => vm.runInContext(code, context);
  return {context, nodes, buttons, node, run};
}

function fixture(style) {
  const selected = {balanced: ['A', 'B'], relay: ['B'], trend: ['A', 'C', 'D']}[style];
  const sectors = [
    {id: 'I0', name: '演示行业甲', score: 70, members: ['A', 'B', 'C']},
    {id: 'I1', name: '演示行业乙', score: 60, members: ['B', 'D']},
  ].map(s => ({...s, type: 'industry', classification: '合成测试分类', avg_pct: 2, breadth: 80,
    limit_up: 1, amount_yuan: 1e9, coverage: 100, available_count: s.members.length, member_count: s.members.length}));
  return {
    config: {style, sector_type: 'industry'}, candidates: selected, sectors,
    meta: {sector_universe_complete: true},
    stocks: ['A', 'B', 'C', 'D'].map((code, i) => ({code, name: `演示股票${code}`, selected: selected.includes(code), score: 80-i, role: '趋势强势'})),
  };
}

async function main() {
  const h = harness();
  const assign = style => {h.context.fixture = fixture(style); h.run('state.analysis=fixture;');};
  assign('balanced');
  let html = h.run('sectorsView()');
  assert.match(html, /综合共振<span> · <\/span><strong>2<\/strong>只观察候选/);
  assert.match(h.run('sectorCandidatePreview(state.analysis.sectors[0])'), /查看板块候选（2）/);
  assert.match(html, /演示股票A/);
  assign('relay');
  html = h.run('sectorsView()');
  assert.match(html, /连板接力<span> · <\/span><strong>1<\/strong>只观察候选/);
  assert.match(h.run('sectorCandidatePreview(state.analysis.sectors[0])'), /查看板块候选（1）/);
  assert.doesNotMatch(html, /演示股票A/);
  assign('trend');
  assert.match(h.run('sectorsView()'), /趋势短线<span> · <\/span><strong>3<\/strong>只观察候选/);
  assert.equal(h.run('sectorCandidates(state.analysis.sectors[0]).length'), 2);
  console.log('PASS: sector page shows mode-specific totals, constituent candidates and stock previews');

  assign('balanced');
  let resolve;
  h.context.nextRequest = new Promise(done => {resolve = done;});
  h.run('api=function() {return nextRequest;};');
  const switching = h.run('changeStyle("relay")');
  assert(h.buttons.every(button => button.disabled));
  assert.match(h.node('#analysis-feedback').innerHTML, /正在切换到连板接力/);
  assert.equal(h.run('state.analysis.config.style'), 'balanced');
  resolve(fixture('relay'));
  await switching;
  assert(h.buttons.every(button => !button.disabled));
  assert.equal(h.run('state.analysis.config.style'), 'relay');
  assert.match(h.node('#analysis-feedback').innerHTML, /已切换到连板接力 · 1只观察候选/);
  console.log('PASS: in-flight recalculation is visible; controls recover after success');

  h.run('api=async function() {throw new Error("合成网络错误");};');
  await h.run('changeStyle("trend")');
  assert.equal(h.run('state.analysis.config.style'), 'relay');
  assert(h.buttons.every(button => !button.disabled));
  assert.match(h.node('#analysis-feedback').innerHTML, /计算失败.*已保留上一模式/);
  console.log('PASS: failed switch preserves prior results and displays the failure');

  h.context.calls = 0;
  h.run('api=async function() {calls++; throw new Error("should not request");};');
  await h.run('changeStyle("relay")');
  assert.equal(h.context.calls, 0);
  assert.match(h.node('#analysis-feedback').innerHTML, /当前已是连板接力/);
  console.log('PASS: clicking the active mode explains the current selection');

  const responses = [];
  h.context.enqueue = () => new Promise(done => responses.push(done));
  h.run('api=function() {return enqueue();};');
  const old = h.run('load({style:"balanced"})');
  const latest = h.run('load({style:"trend"})');
  responses[1](fixture('trend'));
  await latest;
  responses[0](fixture('balanced'));
  await old;
  assert.equal(h.run('state.analysis.config.style'), 'trend');
  assert.match(h.node('#analysis-feedback').innerHTML, /趋势短线/);
  console.log('PASS: late responses cannot overwrite the latest mode');
}

main().catch(error => {console.error(error); process.exitCode=1;});
