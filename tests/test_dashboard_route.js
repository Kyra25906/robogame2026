const {test} = require('node:test');
const assert = require('node:assert/strict');
const {routePanelView, routeRowHtml, routeLiveView} = require('../tools/field_dashboard_web/route_panel.js');

const row = (over = {}) => ({
  index: 0, id: 'S01_LINE_START', role: 'LINE', kind: 'LINE_FOLLOW',
  label: '启动区出线巡线到 N02', from: 'W01', to: 'N02', heading: 'y+',
  max_speed_mps: 0.2, exit: 'JUNCTION_TURN', exit_ref: 'N02', evidence: 'measured', ...over,
});
const route = (over = {}) => ({
  available: true, version: 'b1-test', source_note: '来源：现场实测',
  segments: [row(), row({index: 1, id: 'S03_RAMP_APPROACH', role: 'LINE', exit: 'ODOM_DISTANCE', evidence: 'estimated'})],
  turns: [{segment_id: 'S01_LINE_START', ref: 'N02', direction: 'RIGHT', target_yaw_rad: 0, passthrough_junctions: 0}],
  passthrough: [{segment_id: 'S02_LINE_MAIN', ref: 'N03'}],
  cargo: {orange: 3, purple: 0, layers: 2, layout: '2+1'},
  speed_limits: {max_vx: 0.3, max_vy: 0.3, max_wz: 1.0},
  ...over,
});

test('unavailable payload explains itself instead of showing an empty table', () => {
  const view = routePanelView({available: false, reason: '缺少 PyYAML'});
  assert.equal(view.available, false);
  assert.match(view.summary, /缺少 PyYAML/);
  assert.equal(view.rowsHtml, '');
  const missing = routePanelView(undefined);
  assert.equal(missing.available, false);
  assert.match(missing.summary, /route/);
});

test('summary counts estimated segments and shows cargo plan', () => {
  const view = routePanelView(route());
  assert.equal(view.available, true);
  assert.match(view.summary, /2 段/);
  assert.match(view.summary, /1 段依赖未标定里程计/);
  assert.match(view.summary, /3 橙/);
  assert.match(view.summary, /2 层/);
  assert.match(view.summary, /0\.3\/0\.3\/1/);
});

test('estimated rows are visually distinct from measured rows', () => {
  assert.match(routeRowHtml(row()), /chain-row ok/);
  assert.match(routeRowHtml(row({evidence: 'estimated'})), /chain-row unknown/);
});

test('work and turn segments show that they do not drive the chassis', () => {
  const html = routeRowHtml(row({role: 'WORK', max_speed_mps: 0}));
  assert.match(html, /不驱动底盘/);
  assert.match(html, /作业/);
});

test('rows show exit criteria and the referenced node', () => {
  const html = routeRowHtml(row({exit: 'LINE_END', exit_ref: 'W02'}));
  assert.match(html, /线尽头/);
  assert.match(html, /@W02/);
});

test('turns and passthrough crossings are both listed', () => {
  const view = routePanelView(route());
  assert.match(view.turnsText, /转向点 N02/);
  assert.match(view.turnsText, /RIGHT/);
  assert.match(view.turnsText, /直行通过 N03/);
  assert.match(view.turnsText, /来源：现场实测/);
});

/* B2：实时段进度（来自 /mission/route） */

const live = (over = {}) => JSON.stringify({
  state: 'ROUTE_RUNNING', phase: 'MOVING', segment_index: 2, segments_completed: 2,
  segment_id: 'S03_RAMP_APPROACH', segment_label: 'N06 → 坡前 N07',
  segment_count: 13, next_segment_id: 'S04_RAMP_UP', work_count: 0, work_required: null,
  active_source: 'line_follow', retries: 0, route_error: '', ...over,
});

test('live route line shows段/授权/下一步', () => {
  const view = routeLiveView(live(), 0.1);
  assert.equal(view.available, true);
  assert.match(view.text, /第 2\/13 段/);
  assert.match(view.text, /S03_RAMP_APPROACH/);
  assert.match(view.text, /底盘授权 line_follow/);
  assert.match(view.text, /下一步 S04_RAMP_UP/);
});

test('stale live route line is flagged as stale', () => {
  const view = routeLiveView(live(), 5.0);
  assert.equal(view.available, true);
  assert.match(view.text, /已过期/);
});

test('missing live route data explains that the task layer is not running the route', () => {
  for (const empty of [undefined, '', '   ']) {
    const view = routeLiveView(empty, 0);
    assert.equal(view.available, false);
    assert.match(view.text, /mission_manager/);
  }
});

test('garbage live payload is not silently trusted', () => {
  const view = routeLiveView('not json', 0);
  assert.equal(view.available, false);
  assert.match(view.text, /解析失败/);
});

test('route error is surfaced instead of a normal-looking line', () => {
  const view = routeLiveView(live({route_error: '缺少 survey 段'}), 0);
  assert.equal(view.available, false);
  assert.match(view.text, /缺少 survey 段/);
  assert.match(view.text, /不会退回演示流程/);
});

test('work segments show pick progress', () => {
  const view = routeLiveView(
    live({segment_id: 'S06_PICK3', phase: 'WORKING', work_count: 1, work_required: 3}), 0.1,
  );
  assert.match(view.text, /作业 1\/3/);
});

test('panel view carries the live line and the placement warning', () => {
  const view = routePanelView(route({placement_warning: '要求 [0.1, 0.1, 0.2]'}), routeLiveView(live(), 0.1));
  assert.match(view.liveText, /第 2\/13 段/);
  assert.match(view.turnsText, /放置高度核对/);
  assert.match(view.turnsText, /0\.1, 0\.1, 0\.2/);
});

test('turn phase is shown on the live line (B3)', () => {
  const view = routeLiveView(live({segment_id: 'S01_LINE_START'}), 0.1, 'TURNING');
  assert.match(view.text, /转弯阶段 TURNING/);
  assert.doesNotMatch(routeLiveView(live(), 0.1, '').text, /转弯阶段/);
});

test('turn phase falls back to the mission payload when line diag is missing', () => {
  const view = routeLiveView(live({turn_phase: 'SETTLE'}), 0.1, '');
  assert.match(view.text, /转弯阶段 SETTLE/);
});

test('ramp state and per-segment speed limit are shown (B3)', () => {
  const view = routeLiveView(
    live({segment_id: 'S04_RAMP_UP', line_limit_mps: 0.15, ramp_decision: 'SLIPPING'}), 0.1,
  );
  assert.match(view.text, /本段限速 0\.15 m\/s/);
  assert.match(view.text, /坡道 SLIPPING/);
  const normal = routeLiveView(live({line_limit_mps: 0.2, ramp_decision: 'NORMAL'}), 0.1);
  assert.match(normal.text, /坡道正常/);
  const flat = routeLiveView(live({line_limit_mps: 0.2, ramp_decision: ''}), 0.1);
  assert.doesNotMatch(flat.text, /坡道/);
});

test('work step progress is shown (B3 抓取/搭建序列)', () => {
  const view = routeLiveView(
    live({
      segment_id: 'S06_PICK3', phase: 'WORKING', work_count: 1, work_required: 3,
      work_step: '第 3/5 步：抓第 2 块', work_step_kind: 'PICK',
    }), 0.1,
  );
  assert.match(view.text, /作业 1\/3/);
  assert.match(view.text, /第 3\/5 步：抓第 2 块/);
  assert.doesNotMatch(routeLiveView(live({work_step: ''}), 0.1).text, /第 \d+\/\d+ 步/);
});

test('degradation is flagged so nobody mistakes it for normal progress (B4)', () => {
  const view = routeLiveView(live({degradations: 1, segment_id: 'S12_BUILD_2LAYER'}), 0.1);
  assert.match(view.text, /已降级 1 次/);
  assert.doesNotMatch(routeLiveView(live({degradations: 0}), 0.1).text, /已降级/);
});

test('match clock and round count are shown (B4)', () => {
  const view = routeLiveView(live({match_remaining_s: 245.0, rounds: 1}), 0.1);
  assert.match(view.text, /剩余 4:05/);
  assert.doesNotMatch(view.text, /共 1 趟/, "只有一趟时不必显示趟数");
  assert.match(routeLiveView(live({rounds: 2}), 0.1).text, /共 2 趟/);
  assert.doesNotMatch(routeLiveView(live({match_remaining_s: null}), 0.1).text, /剩余/);
});
