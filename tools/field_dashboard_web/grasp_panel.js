/* 抓取对准面板：只解释证据 + 跑同源仿真，不下发任何速度命令。 */
function graspPhaseName(phase) {
  return { TURN: '① 原地转向（前进为 0）', APPROACH: '② 沿接近轴前进', READY: '③ 到位，可抓' }[phase] || phase || '未知';
}
function renderGrasp(s) {
  const el = document.getElementById('graspLive');
  if (!el) return;
  const g = s.grasp || {};
  const t = (s.telemetry || {});
  const age = Number.isFinite(t.cube_forward_m_age_s) ? t.cube_forward_m_age_s : null;
  const fresh = age !== null && age <= 0.5;
  const params = `目标距离 ${g.target_distance_m ?? '-'} m · 横向容差 ${g.cross_tolerance_m ?? '-'} m · 相机偏角 ${((g.camera_yaw_offset_rad ?? 0) * 57.2958).toFixed(1)}°（接近轴固定为车头轴：侧向够取由云盘完成）`;
  if (g.source !== 'cubes') {
    el.innerHTML = `<div class="blocker"><b>${g.message || '抓取对准数据未就绪'}</b><code>检测话题 /cubes 未收到数据；${params}</code></div>`;
    return;
  }
  const bearingDeg = ((g.bearing_rad ?? 0) * 57.2958).toFixed(1);
  const stale = fresh ? '' : `<div class="blocker"><b>检测已过期（${age === null ? '无年龄' : age.toFixed(2) + ' s'}），下面是旧值</b><code>不要据此判断当前能否抓取</code></div>`;
  el.innerHTML = `${stale}
  <div class="status-grid">
    <div class="status ${g.valid ? 'ok' : 'bad'}"><b>相位</b><br>${graspPhaseName(g.phase)}</div>
    <div class="status ${g.ready_to_grab ? 'ok' : 'unknown'}"><b>可抓</b><br>${g.ready_to_grab ? '是' : '否'}</div>
    <div class="status ok"><b>转向命令</b><br>wz=${g.angular_z ?? '-'} rad/s</div>
    <div class="status ok"><b>前进命令</b><br>vx=${g.linear_x ?? '-'} m/s</div>
    <div class="status ok"><b>横移命令</b><br>vy=${g.linear_y ?? 0}（真车恒 0）</div>
  </div>
  <pre>检测：前向 ${g.forward_m} m，画面横向(右正) ${g.lateral_right_m} m，置信度 ${g.confidence ?? '-'}，颜色 ${g.color ?? '-'}，检测数 ${g.detections ?? '-'}（${age === null ? '年龄未知' : age.toFixed(2) + ' s 前'}）
接近坐标系：沿轴 ${g.along_m} m，横向偏差 ${g.cross_m} m（正=偏左），方位角 ${bearingDeg}°
安全不变式：横向偏差超容差时 vx 必须为 0（上面两行可直接对照）</pre>`;
}
async function graspSimulate() {
  const num = id => Number(document.getElementById(id).value);
  const body = {
    start_x_m: num('graspStartX'), start_y_m: num('graspStartY'),
    start_yaw_rad: num('graspStartYaw') * Math.PI / 180,
    target_x_m: num('graspTargetX'), target_y_m: num('graspTargetY'),
    camera_yaw_offset_rad: num('graspCameraYaw') * Math.PI / 180,
    target_distance_m: num('graspTargetDistance'),
    cross_tolerance_m: num('graspCrossTolerance'),
    dt_s: num('graspDt'),
  };
  const out = document.getElementById('graspSimResult');
  out.textContent = '正在跑…';
  try {
    const r = await api('/api/grasp/simulate', body);
    drawGrasp(r);
    const phases = Object.entries(r.phase_counts || {}).map(([k, v]) => `${graspPhaseName(k)}×${v}`).join('，');
    out.textContent = `${r.converged ? '✅ 收敛到可抓' : '❌ 未收敛'}（${r.stop_reason}）
步数 ${r.steps}（${r.duration_s} s，dt=${body.dt_s} s）
阶段统计：${phases}
最终：沿轴 ${r.final.along_m} m，横向偏差 ${r.final.cross_m} m，位置 (${r.final.x_m}, ${r.final.y_m}) m
安全：横向超容差却命令前进的次数 = ${r.safety_violations}；横移速度恒 0 = ${r.lateral_velocity_always_zero}`;
    if (r.safety_violations !== 0) toast('仿真出现安全违规：偏离状态下命令了前进', true);
  } catch (e) {
    out.textContent = '仿真失败：' + e.message;
    toast(e.message, true);
  }
}
function drawGrasp(r) {
  const canvas = document.getElementById('graspCanvas');
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height, pad = 34;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(0, 0, W, H);
  const pts = (r.trace || []).map(p => [p.x_m, p.y_m]).concat([[r.target.x_m, r.target.y_m]]);
  if (!pts.length) return;
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 0.1), spanY = Math.max(maxY - minY, 0.1);
  const scale = Math.min((W - 2 * pad) / spanX, (H - 2 * pad) / spanY);
  const toPx = (x, y) => [pad + (x - minX) * scale, H - pad - (y - minY) * scale];
  ctx.strokeStyle = '#1e293b';
  for (let i = 0; i <= 10; i++) {
    const gx = pad + i * (W - 2 * pad) / 10, gy = pad + i * (H - 2 * pad) / 10;
    ctx.beginPath(); ctx.moveTo(gx, pad); ctx.lineTo(gx, H - pad); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(pad, gy); ctx.lineTo(W - pad, gy); ctx.stroke();
  }
  const [tx, ty] = toPx(r.target.x_m, r.target.y_m);
  ctx.strokeStyle = '#f87171';
  ctx.beginPath(); ctx.arc(tx, ty, Math.max(3, (r.params.cross_tolerance_m || 0.018) * scale), 0, 2 * Math.PI); ctx.stroke();
  ctx.fillStyle = '#f87171';
  ctx.beginPath(); ctx.arc(tx, ty, 4, 0, 2 * Math.PI); ctx.fill();
  ctx.fillText('目标', tx + 8, ty - 6);
  const colour = { TURN: '#fbbf24', APPROACH: '#60a5fa', READY: '#34d399' };
  let previous = null;
  for (const p of r.trace || []) {
    const [px, py] = toPx(p.x_m, p.y_m);
    if (previous) {
      ctx.strokeStyle = colour[p.phase] || '#94a3b8';
      ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.moveTo(previous[0], previous[1]); ctx.lineTo(px, py); ctx.stroke();
    }
    previous = [px, py];
  }
  const first = r.trace && r.trace[0];
  if (first) {
    const [sx, sy] = toPx(first.x_m, first.y_m);
    ctx.fillStyle = '#e2e8f0';
    ctx.beginPath(); ctx.arc(sx, sy, 5, 0, 2 * Math.PI); ctx.fill();
    ctx.strokeStyle = '#e2e8f0';
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(sx + 26 * Math.cos(-first.yaw_rad), sy + 26 * Math.sin(-first.yaw_rad));
    ctx.stroke();
    ctx.fillText('起点/朝向', sx + 8, sy + 16);
  }
  ctx.fillStyle = '#94a3b8';
  ctx.fillText(`黄=转向 蓝=前进 绿=到位 · 比例 1 格≈${(spanX / 10).toFixed(2)} m（横向）`, pad, pad - 10);
}
if (typeof document !== 'undefined') {
  const button = document.getElementById('graspSim');
  if (button) button.onclick = graspSimulate;
}
if (typeof module !== 'undefined') module.exports = { renderGrasp, graspPhaseName };
