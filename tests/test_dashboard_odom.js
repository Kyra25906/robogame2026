const {test} = require('node:test');
const assert = require('node:assert/strict');
const {odomPanelView, odomTrialRows} = require('../tools/field_dashboard_web/odom_panel.js');

const trial = (over = {}) => ({
  target_m: 0.5, odom_m: 0.5, elapsed_s: 10.4, commanded_speed_mps: 0.05,
  measured_m: 0.49, note: '', completed: true,
  odom_speed_mps: 0.048, odom_over_command: 0.96,
  open_loop_expected_m: 0.52, scale_from_tape: 0.98, open_loop_ratio: 0.94, ...over,
});

const calibration = (over = {}) => ({
  count: 3, completed_count: 3, measured_count: 3,
  trials: [trial(), trial(), trial()],
  consistency: {ratios: [0.96, 0.96, 0.96], median: 0.96, suspicious: false},
  scale: {n: 3, values: [0.98, 1.0, 1.02], mean: 1.0, median: 1.0, stdev: 0.016, relative_spread: 0.016, usable: true},
  open_loop: {n: 3, values: [0.94, 0.96, 0.98], median: 0.96, stdev: 0.016, relative_spread: 0.017, trustworthy: true},
  verdict: '尺量标度 1.0000（n=3，波动 1.6%）',
  next_step: '可以按里程计做距离判据',
  ...over,
});

test('no data yet: panel says what to do and blocks recording', () => {
  const view = odomPanelView({});
  assert.equal(view.tone, 'unknown');
  assert.match(view.liveText, /未开始/);
  assert.match(view.liveText, /没有可记录的完整试验/);
  assert.match(view.consistencyText, /样本不足/);
  assert.match(view.scaleText, /还没有尺量数据/);
  assert.equal(view.canRecord, false);
  assert.equal(view.rowsHtml, '');
});

test('completed trial enables recording', () => {
  const view = odomPanelView({
    distance_result: {
      state: '里程计目标已到达', target_m: 0.5, progress_m: 0.5, lateral_m: 0.0,
      elapsed_s: 10.4, odom_speed_mps: 0.048, commanded_speed_mps: 0.05,
      detail: '请尺量实际位移',
    },
    odom_trial_candidate: {target_m: 0.5, odom_m: 0.5, elapsed_s: 10.4, commanded_speed_mps: 0.05, completed: true},
  });
  assert.equal(view.canRecord, true);
  assert.match(view.liveText, /已就绪/);
  assert.match(view.liveText, /耗时 10.4 s/);
  assert.match(view.liveText, /里程计等效速度 0.048/);
});

test('inconsistent odometry turns the verdict red without any tape data', () => {
  const view = odomPanelView({
    odom_calibration: calibration({
      measured_count: 0,
      consistency: {ratios: [9.5], median: 9.5, suspicious: true},
      scale: null,
      verdict: '里程计自身不自洽：等效速度是命令速度的 9.52 倍',
      next_step: '先解决这个再谈标度',
    }),
  });
  assert.equal(view.tone, 'bad');
  assert.match(view.consistencyText, /不自洽/);
  assert.match(view.verdict, /不自洽/);
});

test('usable scale turns the verdict green', () => {
  const view = odomPanelView({odom_calibration: calibration()});
  assert.equal(view.tone, 'ok');
  assert.match(view.scaleText, /可用于定标度/);
  assert.match(view.openLoopText, /命令速度可信/);
});

test('unusable scale is not presented as usable', () => {
  const view = odomPanelView({
    odom_calibration: calibration({scale: {n: 2, values: [0.9, 1.1], mean: 1.0, median: 1.0, stdev: 0.1, relative_spread: 0.1, usable: false}}),
  });
  assert.equal(view.tone, 'unknown');
  assert.match(view.scaleText, /先别乘系数/);
});

test('trial rows show tape value, scale and the uncompleted marker', () => {
  const html = odomTrialRows(calibration({
    trials: [trial(), trial({measured_m: null, scale_from_tape: null, open_loop_ratio: null, completed: false})],
  }));
  assert.match(html, /chain-row ok/);
  assert.match(html, /chain-row bad/);
  assert.match(html, /尺量 0\.490 m/);
  assert.match(html, /未尺量/);
  assert.match(html, /不计样本/);
  assert.match(html, /是命令速度的 0\.96 倍/);
});

test('verdict and next step always have a fallback', () => {
  const view = odomPanelView({odom_calibration: {}});
  assert.ok(view.verdict.length > 0);
  assert.ok(view.nextStep.length > 0);
});
