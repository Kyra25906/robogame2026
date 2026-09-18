/*
 * 「真伪与实例核查」卡片的纯函数测试（`node --test tests/test_runtime_guard.js`）。
 *
 * 为什么单独测 JS：页面上的措辞与「哪个按钮被禁用」是给人看的那一层。
 * 后端判定正确、页面却把「模拟」显示成绿色，等于没做。
 * 这里只测纯函数与 DOM 副作用函数，不启动浏览器、不连 ROS。
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { runtimeGuardView, applyBlockedStarts } = require('../tools/field_dashboard_web/runtime_guard.js');

const realReport = (extra = {}) => ({
  verdict: 'ok',
  headline: '来源：真车（/robot/status 已解码 MCU V1 STATUS）；关键话题各只有一个发布者',
  expected_source: 'field',
  source: { kind: 'real', label: '真车（/robot/status 已解码 MCU V1 STATUS）', observed: ['field'], sample: 'decoded MCU V1 STATUS', sample_count: 42 },
  findings: [],
  topics: [
    { topic: '/robot/status', publishers: ['robot_bridge'], count: 1, critical: true, level: 'ok' },
    { topic: '/cmd_vel', publishers: ['line_follow_controller'], count: 1, critical: false, level: 'ok' },
  ],
  instances: { managed: [], external: [], scan_available: true },
  blocked_starts: [],
  scan_note: '',
  ...extra,
});

test('missing check is reported as a stale page, not as healthy', () => {
  const view = runtimeGuardView(undefined);
  assert.equal(view.available, false);
  assert.equal(view.tone, 'unknown');
  assert.match(view.headline, /版本过旧/);
});

test('a healthy real report keeps the real label and its samples', () => {
  const view = runtimeGuardView(realReport());
  assert.equal(view.tone, 'ok');
  assert.match(view.subtitle, /真车/);
  assert.match(view.subtitle, /期望来源 field/);
  assert.match(view.subtitle, /样本 42 条/);
  assert.match(view.subtitle, /decoded MCU V1 STATUS/);
});

test('mock is never rendered with a green tone', () => {
  const view = runtimeGuardView(realReport({
    verdict: 'warn',
    headline: '⚠️ 来源：模拟（/robot/status 来自 mock hardware）｜当前 /robot/status 来自 mock（假数据）',
    source: { kind: 'mock', label: '模拟（/robot/status 来自 mock hardware）', observed: ['mock'], sample: 'mock hardware', sample_count: 5 },
    findings: [{ level: 'warn', code: 'MOCK_OBSERVED', message: '当前 /robot/status 来自 mock（假数据）：通信正常等绿灯不可信，车上没接固件', evidence: "detail='mock hardware'", blocks: [] }],
  }));
  assert.equal(view.tone, 'unknown');
  assert.match(view.headline, /模拟/);
  assert.match(view.findingsHtml, /MOCK_OBSERVED/);
  assert.match(view.findingsHtml, /假数据/);
});

test('blocked findings carry their evidence and name the blocked processes', () => {
  const view = runtimeGuardView(realReport({
    verdict: 'block',
    headline: '⛔ 来源：真车（/robot/status 已解码 MCU V1 STATUS）｜/robot/status 有 2 个发布者：第二个实例正在跑',
    findings: [{
      level: 'block', code: 'DUP_PUBLISHER',
      message: '/robot/status 有 2 个发布者：第二个实例正在跑',
      evidence: '/robot/status → robot_bridge, robot_bridge_2',
      blocks: ['bridge'],
    }],
    blocked_starts: ['bridge'],
  }));
  assert.equal(view.tone, 'bad');
  assert.deepEqual(view.blockedStarts, ['bridge']);
  assert.match(view.findingsHtml, /阻断/);
  assert.match(view.findingsHtml, /robot_bridge_2/);
  assert.match(view.blockedText, /bridge/);
});

test('escapes page-hostile text from the backend', () => {
  const view = runtimeGuardView(realReport({
    findings: [{ level: 'warn', code: 'X', message: '<img src=x onerror=1>', evidence: 'a & b', blocks: [] }],
  }));
  assert.ok(!view.findingsHtml.includes('<img'));
  assert.match(view.findingsHtml, /&lt;img/);
});

test('an unavailable process table says it cannot rule out a second instance', () => {
  const view = runtimeGuardView(realReport({
    verdict: 'warn',
    instances: { managed: [], external: [], scan_available: false },
    scan_note: '本机是 Windows：没有 ps -eo',
  }));
  assert.match(view.scanText, /不可用/);
  assert.match(view.scanText, /不能.*认为没有第二个实例/);
  assert.match(view.scanText, /Windows/);
});

test('external instances are shown with pid and command line', () => {
  const view = runtimeGuardView(realReport({
    instances: {
      managed: [{ pid: 2000, node: 'robot_bridge', args: '/bin/sh -c ros2 run robot_bridge robot_bridge' }],
      external: [{ pid: 1241, node: 'mission_manager', args: 'python3 .../mission_manager' }],
      scan_available: true,
    },
  }));
  assert.match(view.instancesText, /pid 2000/);
  assert.match(view.instancesText, /pid 1241/);
  assert.match(view.instancesText, /真正的盲区/);
});

test('blocked starts disable exactly those buttons, and the whole base group', () => {
  const buttons = [
    { dataset: { start: 'bridge' }, disabled: false, title: '' },
    { dataset: { start: 'arm' }, disabled: false, title: '' },
  ];
  const group = { dataset: { group: 'base' }, disabled: false, title: '' };
  const previous = global.document;
  global.document = {
    querySelectorAll: (selector) => (selector === '[data-start]' ? buttons : [group]),
  };
  try {
    applyBlockedStarts(['bridge']);
    assert.equal(buttons[0].disabled, true, 'bridge 必须被禁用');
    assert.equal(buttons[1].disabled, false, 'arm 不该被牵连');
    assert.match(buttons[0].title, /已有另一个实例/);
    assert.equal(group.disabled, true, '「启动底盘链路」整组必须一起拦下');
  } finally {
    global.document = previous;
  }
});

test('no blocked starts leaves every button usable', () => {
  const buttons = [{ dataset: { start: 'bridge' }, disabled: true, title: '旧提示' }];
  const group = { dataset: { group: 'base' }, disabled: true, title: '旧提示' };
  const previous = global.document;
  global.document = {
    querySelectorAll: (selector) => (selector === '[data-start]' ? buttons : [group]),
  };
  try {
    applyBlockedStarts([]);
    // 启用由页面其它逻辑负责；这里只断言**不再被本卡片禁用/加提示**，
    // 否则一次误报会永久按住按钮，比不禁用更糟。
    assert.equal(buttons[0].title, '旧提示');
    assert.equal(group.title, '旧提示');
  } finally {
    global.document = previous;
  }
});
