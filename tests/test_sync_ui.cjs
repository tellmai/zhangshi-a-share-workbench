// Offline UI behavior checks. All API calls and clocks are controlled in memory.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function harness() {
  const nodes = new Map(), timers = new Map();
  let timerId = 0;
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      innerHTML: '', textContent: '', hidden: false, disabled: false, attrs: {}, listeners: {}, closed: 0,
      setAttribute(k, v) {this.attrs[k] = v;},
      addEventListener(k, fn) {this.listeners[k] = fn;},
      close() {this.closed++;}, showModal() {this.opened = true;},
      classList: {add() {}, remove() {}, toggle() {}},
    });
    return nodes.get(selector);
  };
  const clock = {now: Date.parse('2026-10-24T12:02:05+08:00')};
  class ControlledDate extends Date {static now() {return clock.now;}}
  class TestFormData {constructor(target) {this.data = target.values;} get(key) {return this.data[key];}}
  const context = vm.createContext({
    document: {querySelector: node, querySelectorAll: () => [], addEventListener() {}},
    window: {addEventListener() {}}, location: {hash: '#quant'}, localStorage: {getItem: () => null},
    Date: ControlledDate, FormData: TestFormData, URL,
    setTimeout(fn, delay) {const id = ++timerId; timers.set(id, {fn, delay}); return id;},
    clearTimeout(id) {timers.delete(id);}, requestAnimationFrame() {},
  });
  vm.runInContext(source.replace(/\nboot\(\);\s*$/, '\n'), context);
  const run = code => vm.runInContext(code, context);
  return {context, node, nodes, timers, clock, run};
}
function running(overrides = {}) {
  return {id: 'sync-1', state: 'running', stage: 'history', progress: 64,
    message: '补齐前复权历史 · 合成股票', started_at: '2026-10-24T12:00:00+08:00', ended_at: null,
    detail: {source: 'sina', attempt: 2, attempt_total: 2, completed: 48, total: 120, unit: '只', succeeded: 47, failed: 1},
    ...overrides};
}
function assign(h, job) {
  h.context.job = job;
  h.run('state.status={job,csrf:"test",today:"2026-10-24"};');
}

async function main() {
  {
    const h = harness(); assign(h, running());
    h.run('renderSyncProgress()');
    const panel = h.node('#sync-progress');
    assert.equal(panel.hidden, false);
    assert.match(panel.className, /is-running/);
    assert.match(panel.innerHTML, /role="progressbar".*aria-valuenow="64"/);
    assert.match(panel.innerHTML, /已处理 <b>48 \/ 120<\/b> 只 · 成功 47 · 失败 1/);
    assert.match(panel.innerHTML, /第 2 \/ 2 轮，本轮进度重新计算/);
    assert.match(panel.innerHTML, /2分05秒/);
    assert.equal(h.node('#sync-button').disabled, true);
    assert.equal(h.node('#sync-form button[type="submit"]').disabled, true);
    assert.equal(h.node('#sync-button').attrs['aria-busy'], 'true');
    h.clock.now += 8000;
    const tick = [...h.timers.values()].find(t => t.delay === 1000);
    tick.fn();
    assert.equal(h.node('#sync-elapsed').textContent, '2分13秒');
    assert.match(panel.innerHTML, /aria-valuenow="64"/); // Time never fabricates progress.
    assign(h, running({progress: 100}));
    assert.match(h.run('jobHTML()'), /aria-valuenow="99"/);
    console.log('PASS: shared progress displays real stage counts, retry round, elapsed time, accessible percent and busy controls');
  }
  {
    const h = harness(); assign(h, running());
    h.run(`state.analysis={market:{active_count:10},meta:{mode:'live',trade_date:'2026-10-23',source:'合成来源'},quality:{},config:{style:'trend'}};
      renderFilters=renderTable=drawAll=analysisBusy=function(){};
      overviewView=sectorsView=stocksView=ladderView=dataView=function(){return 'page-content';};
      function reviewView(){return 'review-content';} function quantView(){return 'quant-content';}`);
    for (const view of ['overview', 'sectors', 'stocks', 'ladder', 'quant', 'review', 'data']) {
      h.context.location.hash = '#' + view;
      h.run('render()');
      assert.equal(h.node('#sync-progress').hidden, false);
      assert.match(h.node('#sync-progress').innerHTML, /aria-valuenow="64"/);
      assert.doesNotMatch(h.node('#content').innerHTML, /sync-progress/);
    }
    const index = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
    assert(index.indexOf('id="sync-progress"') < index.indexOf('id="content"'));
    assert(!source.includes('${sourceHubHTML()}${jobHTML()}'));
    console.log('PASS: progress survives every page change and is not duplicated inside the data page');
  }
  {
    const h = harness(); assign(h, {state: 'idle'});
    let resolve, posts = 0;
    h.context.send = () => {posts++; return new Promise(done => {resolve = done;});};
    h.context.pollCount = 0;
    h.run('api=send;poll=function(){pollCount++;};');
    h.context.event = {preventDefault() {}, target: {values: {date: '2026-10-23', industry_limit: '12', theme_limit: '8'}}};
    const pending = h.run('startSync(event)');
    await h.run('startSync(event)');
    assert.equal(posts, 1);
    assert.equal(h.node('#sync-button').disabled, true);
    assert.match(h.node('#sync-progress').innerHTML, /正在提交更新请求/);
    assert.equal(h.node('#sync-dialog').closed, 1);
    assert.equal(h.context.location.hash, '#quant');
    resolve({job: running({progress: 0, stage: 'connecting'})});
    await pending;
    assert.equal(h.run('state.status.job.id'), 'sync-1');
    assert.equal(h.context.pollCount, 1);
    assert.equal(h.context.location.hash, '#quant');
    h.run('openSync()');
    assert.notEqual(h.node('#sync-dialog').opened, true);
    console.log('PASS: submission shows progress immediately, keeps the current page and prevents duplicate updates');
  }
  {
    const h = harness(); assign(h, running());
    h.run('state.view="quant";api=async function(){throw new Error("synthetic offline");};');
    await h.run('poll()');
    assert.equal(h.run('state.status.job.progress'), 64);
    assert.match(h.node('#sync-progress').innerHTML, /进度连接中断，正在自动重连/);
    assert.equal(h.node('#sync-button').disabled, true);
    assert([...h.timers.values()].some(t => t.delay === 3000));
    h.context.finished = {job: running({state: 'done', progress: 100, ended_at: '2026-10-24T12:02:05+08:00', message: '快照已保存'})};
    h.context.reloads = 0; h.context.quantReloads = 0;
    h.run('api=async function(){return finished;};load=async function(){reloads++;return true;};function loadQuant(){quantReloads++;}');
    await h.run('poll()'); await h.run('poll()');
    assert.equal(h.context.reloads, 1);
    assert.equal(h.context.quantReloads, 1);
    assert.equal(h.run('state.syncConnectionError'), null);
    assert.equal(h.node('#sync-button').disabled, false);
    assert.match(h.node('#sync-progress').innerHTML, /aria-valuenow="100"/);
    assert.doesNotMatch(h.node('#sync-progress').innerHTML, /正在自动重连/);
    assert([...h.timers.values()].some(t => t.delay === 5000));
    h.clock.now += 3600000;
    assert.equal(h.run('syncElapsed(state.status.job)'), '2分05秒');
    console.log('PASS: temporary status failures retry automatically and completion refreshes results once with a frozen elapsed time');
  }
  {
    const h = harness(); assign(h, running({state: 'error', progress: 75, ended_at: '2026-10-24T12:02:05+08:00', message: '来源错误 <img src=x onerror=evil>'}));
    h.run('renderSyncProgress()');
    assert.match(h.node('#sync-progress').className, /is-error/);
    assert.match(h.node('#sync-progress').innerHTML, /aria-valuenow="75"/);
    assert.match(h.node('#sync-progress').innerHTML, /来源错误 &lt;img/);
    assert.match(h.node('#sync-progress').innerHTML, /重新更新/);
    assert.equal(h.node('#sync-button').disabled, false);
    h.run('state.syncDismissed=syncJobKey(state.status.job);renderSyncProgress();');
    assert.equal(h.node('#sync-progress').hidden, true);
    assign(h, running({id: 'sync-2'}));
    h.run('renderSyncProgress()');
    assert.equal(h.node('#sync-progress').hidden, false);
    console.log('PASS: failures retain details and partial progress; dismissing an old result never hides the next update');
  }
  {
    const h = harness(); assign(h, running({id: 'submitting', stage: 'connecting', progress: 0}));
    let resolve;
    h.context.waitStatus = () => new Promise(done => {resolve = done;});
    h.run('api=waitStatus;state.syncStarting=true;renderSyncProgress();');
    const pending = h.run('poll()');
    resolve({job: {state: 'idle'}});
    await pending;
    assert.equal(h.run('state.status.job.id'), 'submitting');
    assert.equal(h.node('#sync-button').disabled, true);
    h.run('renderSyncProgress()');
    assert.equal(h.node('#sync-button').disabled, true);
    console.log('PASS: a late status response cannot overwrite an update being submitted');
  }
  {
    const h = harness();
    let resolve;
    h.context.first = {job: running()};
    h.context.waitFinish = () => new Promise(done => {resolve = done;});
    h.context.requests = 0; h.context.reloads = 0;
    h.run('api=async function(){return requests++===0?first:waitFinish();};load=async function(){reloads++;return true;};');
    await h.run('boot()');
    assert.equal(h.run('state.syncHandled'), null);
    assert.match(h.node('#sync-progress').innerHTML, /aria-valuenow="64"/);
    resolve({job: running({state: 'done', progress: 100, ended_at: '2026-10-24T12:02:05+08:00'})});
    await new Promise(setImmediate);
    assert.equal(h.context.reloads, 2); // Initial read plus the resumed job's completion.
    assert.equal(h.node('#sync-button').disabled, false);
    console.log('PASS: refreshing during an update resumes progress and refreshes the analysis after completion');
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
