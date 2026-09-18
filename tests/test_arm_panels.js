/*
 * 「关节微调」与「姿态示教」两个面板的纯函数测试
 * （`node --test tests/test_arm_panels.js`）。
 *
 * 为什么单独测 JS：后端判定正确、页面却把"我们发出去的角度"显示成测量值，
 * 或者把 9010 的错误码原样丢给人看，等于这套东西白做。
 * 这里只测纯函数与措辞，不启动浏览器、不连 ROS。
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { jogView, errorHint } = require('../tools/field_dashboard_web/arm_jog_panel.js');
const { poseView } = require('../tools/field_dashboard_web/arm_pose_panel.js');

const jogSnapshot = (extra = {}) => ({
  available: true,
  limits_deg: [0, 270],
  joints: [
    { id: 0, label: '腰/云盘', safe_pose_deg: 131 },
    { id: 1, label: '肩', safe_pose_deg: 158 },
  ],
  step_range: [1, 10],
  period_range: [0.1, 1],
  heartbeat_timeout_s: 0.6,
  note: '机械臂无位置反馈：这里的「当前角度」是我们**发出去的目标角度**累加值，不是读回来的。',
  session: null,
  ...extra,
});

test('no session yet says the range and that holding is what moves it', () => {
  const view = jogView(jogSnapshot());
  assert.equal(view.available, true);
  assert.match(view.status, /0～270/);
  assert.match(view.status, /按住按钮才会动/);
});

test('an active session shows steps, next target and heartbeat age', () => {
  const view = jogView(jogSnapshot({
    active: true,
    session: {
      active: true, joint: 1, joint_label: '肩', start_deg: 158, last_target_deg: 168,
      steps_done: 2, heartbeat_age_s: 0.2, steps: [
        { index: 1, angle_deg: 163, success: true },
        { index: 2, angle_deg: 168, success: true },
      ],
    },
  }));
  assert.equal(view.tone, 'ok');
  assert.match(view.status, /正在微调 肩/);
  assert.match(view.status, /第|已走 2 步/);
  assert.equal(view.targetText, '158° → 168°');
  assert.match(view.stepsText, /168°/);
});

test('a failed step surfaces the error code and the固定 hint for it', () => {
  const view = jogView(jogSnapshot({
    session: {
      active: false, joint: 1, joint_label: '肩', start_deg: 158, last_target_deg: 158,
      steps_done: 1, heartbeat_age_s: 9, stop_reason: '关节 1 到 163° 失败：code=9010',
      steps: [{ index: 1, angle_deg: 163, success: false, error_code: 9010, detail: 'not frozen' }],
    },
  }));
  assert.match(view.hintText, /arm_joint_ranges/);
  assert.match(view.status, /已停止/);
});

test('the panel always repeats that the angle is not measured', () => {
  assert.match(jogView(jogSnapshot()).hintText, /没有位置反馈|无位置反馈/);
  assert.match(errorHint(9012), /爪子不走 ARM_SET/);
  assert.match(errorHint(3020), /3020|预算时间内|超时/);
});

test('a stale page shows an explicit stale notice, not healthy numbers', () => {
  const view = jogView(null);
  assert.equal(view.available, false);
  assert.match(view.status, /版本过旧/);
});

const poseSnapshot = (extra = {}) => ({
  available: true,
  file: '/home/rg26/robogame_arm_poses.json',
  names: ['安全姿态', '右侧抓取位'],
  poses: {
    安全姿态: { description: '安全姿态：0=131°、1=158°', note: '非实测', order: [0, 1] },
    右侧抓取位: { description: '右侧抓取位：1=150°、2=120°', note: '第一次示教', order: [1, 2] },
  },
  limits_deg: [0, 270],
  commanded: { commanded_deg: { 0: 131, 1: 150 }, order: [0, 1] },
  replay: null,
  note: '示教记的是我们下发过的目标角度，不是测量值',
  ...extra,
});

test('pose view lists poses and marks the builtin safe pose', () => {
  const view = poseView(poseSnapshot());
  assert.equal(view.rows.length, 2);
  assert.equal(view.rows.find((row) => row.name === '安全姿态').isSafe, true);
  assert.equal(view.rows.find((row) => row.name === '右侧抓取位').isSafe, false);
});

test('pose view shows the commanded targets and says they are not measured', () => {
  const view = poseView(poseSnapshot());
  assert.match(view.status, /不是测量值/);
  assert.match(view.status, /1→150°/);
  assert.match(view.fileText, /robogame_arm_poses\.json/);
});

test('an active replay reports progress; a finished one reports why it stopped', () => {
  const active = poseView(poseSnapshot({
    replay: { name: '右侧抓取位', active: true, steps_done: 1, plan: [1, 2], elapsed_s: 0.8, steps: [] },
  }));
  assert.equal(active.tone, 'ok');
  assert.match(active.replayText, /第 1\/2 步/);

  const failed = poseView(poseSnapshot({
    replay: {
      name: '右侧抓取位', active: false, steps_done: 1, plan: [1, 2], elapsed_s: 1.2,
      stop_reason: '关节 2 到 120° 失败：code=3020',
      steps: [{ index: 0, joint: 2, angle_deg: 120, success: false, error_code: 3020, detail: 'timeout' }],
    },
  }));
  assert.match(failed.replayText, /3020/);
  assert.match(failed.replayText, /失败/);
});

test('a corrupt pose file is surfaced instead of hidden', () => {
  const view = poseView(poseSnapshot({ error: '姿态表读取失败：格式错误' }));
  assert.match(view.noticeText, /读取失败/);
});

test('stale page for poses is explicit too', () => {
  const view = poseView(undefined);
  assert.equal(view.available, false);
  assert.match(view.status, /版本过旧/);
});
