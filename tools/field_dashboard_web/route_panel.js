/* B1 全流程路线只读面板。
 *
 * 数据来源：snapshot.route（tools/field_dashboard_core.build_route_payload）。
 * 只显示计划与判据，不下发任何命令——真正的段推进在 mission_manager（B2）里。
 * `available:false` 时必须显式说明原因，不能显示一份看起来正常的空表。
 */
const ROUTE_ROLE_TEXT = {
  LINE: '巡线', RAMP_UP: '上坡', RAMP_DOWN: '下坡',
  SHIFT: '平移', WORK: '作业', TURN: '原地转向',
};
const ROUTE_EXIT_TEXT = {
  JUNCTION_TURN: '路口转向完成', LINE_END: '线尽头', ODOM_DISTANCE: '位移达标',
  STOP_POINT: '到停车点', POSE_TOLERANCE: '位姿到位', YAW_TARGET: '航向到位',
  WORK_DONE: '作业次数达标',
};
const ROUTE_HEADING_TEXT = { 'x+': '+x', 'x-': '-x', 'y+': '+y', 'y-': '-y', free: '非轴向' };

function routeRowHtml(row) {
  const role = ROUTE_ROLE_TEXT[row.role] || row.role;
  const exit = ROUTE_EXIT_TEXT[row.exit] || row.exit;
  const ref = row.exit_ref ? ' @' + row.exit_ref : '';
  const speed = row.max_speed_mps > 0 ? row.max_speed_mps.toFixed(2) + ' m/s' : '不驱动底盘';
  // 只有坐标来自实测才标绿；estimated 明确画成「未验证」，避免把计划当结论。
  const tone = row.evidence === 'measured' ? 'ok' : 'unknown';
  return `<div class="chain-row ${tone}"><b>${row.index + 1}</b>`
    + `<span>${row.id}｜${role}｜${row.label}</span>`
    + `<em>${row.from} → ${row.to}（${ROUTE_HEADING_TEXT[row.heading] || row.heading}）· ${speed}`
    + ` · 退出：${exit}${ref} · 可信度：${row.evidence}</em></div>`;
}

/** 纯函数：把 /mission/route 的原始字符串变成「车现在在哪一段」的一行说明。 */
function routeLiveView(raw, ageS, turnPhase) {
  if (typeof raw !== 'string' || !raw.trim()) {
    const turn = turnPhase ? `｜转弯阶段 ${turnPhase}` : '';
    return { available: false, text: '任务层未上报段进度（/mission/route 未收到）——路线模式下应由 mission_manager 以 10Hz 上报；没有它说明任务层没在跑路线模式。' + turn };
  }
  let data;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    return { available: false, text: '段进度解析失败（/mission/route 内容不是 JSON）：' + raw.slice(0, 120) };
  }
  if (data.route_error) {
    return { available: false, text: '路线不可用：' + data.route_error + '（任务会判失败并保持停车，不会退回演示流程）' };
  }
  const stale = typeof ageS === 'number' && ageS > 2.0;
  const total = data.segment_count == null ? '?' : data.segment_count;
  const done = data.segments_completed == null ? '?' : data.segments_completed;
  // B4：比赛剩余时间（6 分钟时钟）——到点任务会安全停车
  const remaining = typeof data.match_remaining_s === 'number'
    ? `｜剩余 ${Math.floor(data.match_remaining_s / 60)}:${String(Math.floor(data.match_remaining_s % 60)).padStart(2, '0')}`
    : '';
  const rounds = data.rounds && data.rounds > 1 ? `｜共 ${data.rounds} 趟` : '';
  const work = data.work_required
    ? `｜作业 ${data.work_count}/${data.work_required}`
    : '';
  // B3：作业段的动作序列进度（抓/放 + 之间的车体微移）
  const workStep = data.work_step ? `｜${data.work_step}` : '';
  // B3：转弯阶段（来自巡线节点的结构化状态，若缺则用任务层上报的）
  const turnFinal = turnPhase || data.turn_phase || '';
  const turnLine = turnFinal ? `｜转弯阶段 ${turnFinal}` : '';
  const limit = data.line_limit_mps == null ? '' : `｜本段限速 ${data.line_limit_mps} m/s`;
  const ramp = data.ramp_decision && data.ramp_decision !== 'NORMAL'
    ? `｜⚠️ 坡道 ${data.ramp_decision}`
    : (data.ramp_decision === 'NORMAL' ? '｜坡道正常' : '');
  // B4 开赛门：标定不可用时**不能**只显示「一切正常」——上电自主模式下
  // 这是唯一能在赛前看出来的前置条件，必须显式写在这里。
  const blockers = Array.isArray(data.readiness_blockers) ? data.readiness_blockers : [];
  const ready = data.line_calibration_ready === true;
  const readyLine = blockers.length
    ? `｜⛔ 不能开赛：${blockers[0]}`
    : (ready ? '｜✅ 巡线标定可用' : '');
  // B3/B4：车上载货（抓/放成功后由任务层记账）。记账自相矛盾时**必须显眼**：
  // 这个数会影响降级决策（有存货→先去搭建），数不可信时人要知道。
  const cargo = typeof data.cargo_onboard === 'number'
    ? `｜载货 ${data.cargo_onboard} 块`
    : '';
  const cargoWarn = data.cargo_valid === false ? '｜⚠️ 载货簿记不可信' : '';
  const text = `第 ${done}/${total} 段｜当前 ${data.segment_id || '-'}（${data.segment_label || '-'}）`
    + `｜阶段 ${data.phase || '-'}｜状态 ${data.state || '-'}`
    + `｜底盘授权 ${data.active_source || 'none'}${work}${workStep}${turnLine}${limit}${ramp}${rounds}${remaining}`
    + readyLine + cargo + cargoWarn
    + (data.retries ? `｜重试 ${data.retries}` : '')
    // B4：降级（重试耗尽后跳过/撤退）必须显眼——否则现场会以为任务在正常推进
    + (data.degradations ? `｜⚠️ 已降级 ${data.degradations} 次` : '')
    + `｜下一步 ${data.next_segment_id || '（已完成）'}`
    + (stale ? `｜⚠️ 该上报已过期 ${ageS.toFixed(1)}s，下面显示的是旧值` : '');
  return { available: true, stale: stale, text };
}

/** 纯函数：把 snapshot.route 变成 {summary, rowsHtml, turnsText, liveText}，便于 node 测试。 */
function routePanelView(route, live) {
  const liveView = live || { available: false, text: '' };
  const warningText = (route && route.available === true && route.placement_warning)
    ? '\n⚠️ 放置高度核对：' + route.placement_warning
    : '';
  if (!route || route.available !== true) {
    return {
      available: false,
      summary: '路线数据不可用：' + ((route && route.reason) || 'snapshot 未包含 route 字段（网页服务版本过旧？）'),
      rowsHtml: '',
      turnsText: '',
      liveText: liveView.text,
      warningText: '',
    };
  }
  const estimated = route.segments.filter(r => r.evidence !== 'measured').length;
  const cargo = route.cargo || {};
  const limits = route.speed_limits || {};
  const summary = `版本 ${route.version}｜${route.segments.length} 段`
    + `（其中 ${estimated} 段依赖未标定里程计/航向，标 estimated）`
    + `｜载货 ${cargo.orange} 橙${cargo.purple ? ' + ' + cargo.purple + ' 紫' : ''}`
    + ` / 搭 ${cargo.layers} 层（${cargo.layout}）`
    + `｜限幅 ${limits.max_vx}/${limits.max_vy}/${limits.max_wz}`;
  const turnLines = (route.turns || []).map(t =>
    `转向点 ${t.ref}（段 ${t.segment_id}）：${t.direction}，`
    + `目标航向 ${t.target_yaw_rad == null ? '-' : t.target_yaw_rad.toFixed(4)} rad，`
    + `略过 ${t.passthrough_junctions} 个直行路口`);
  const passLines = (route.passthrough || []).map(p =>
    `直行通过 ${p.ref}（段 ${p.segment_id}）——不转向、不停车`);
  return {
    available: true,
    summary,
    rowsHtml: route.segments.map(routeRowHtml).join(''),
    turnsText: [route.source_note, ...turnLines, ...passLines].join('\n') + warningText,
    liveText: liveView.text,
    warningText,
  };
}

function renderRoute(s) {
  const panel = document.querySelector('#routePanel');
  const summary = document.querySelector('#routeSummary');
  const turns = document.querySelector('#routeTurns');
  const live = document.querySelector('#routeLive');
  if (!panel || !summary || !turns) return;
  const telemetry = (s && s.telemetry) || {};
  const turnPhase = (telemetry.line_diag && telemetry.line_diag.turn_phase) || '';
  const liveView = routeLiveView(telemetry.mission_route, telemetry.mission_route_age_s, turnPhase);
  const view = routePanelView(s && s.route, liveView);
  panel.innerHTML = view.rowsHtml;
  summary.textContent = view.summary;
  turns.textContent = view.turnsText;
  if (live) {
    live.textContent = view.liveText;
    live.className = liveView.available && !liveView.stale ? 'route-live ok' : 'route-live bad';
  }
}

if (typeof module !== 'undefined') module.exports = { routePanelView, routeRowHtml, routeLiveView, renderRoute };
