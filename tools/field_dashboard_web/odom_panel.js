/* 里程计标定面板：把「定距试验 + 尺量」变成可下结论的标度。
 *
 * 数据来源：snapshot.distance_result（本次试验）与 snapshot.odom_calibration（汇总）。
 * 三个问题分开显示，别混为一谈：
 *   ① 开环：命令速度×时间 ≈ 尺量位移？
 *   ② 自洽：里程计报的位移 ÷ 耗时 ≈ 命令速度？（**不用尺子**就能看出里程计被放大几倍）
 *   ③ 标度：尺量位移 ÷ 里程计位移
 * 只渲染 + 由 app.js 负责按钮请求；面板本身不驱动底盘。
 */
const ODOM_VERDICT_TONE = (o) => {
  if (!o) return 'unknown';
  if (o.consistency && o.consistency.suspicious) return 'bad';
  if (o.scale && o.scale.usable) return 'ok';
  return 'unknown';
};

function odomTrialRows(calibration) {
  const trials = (calibration && calibration.trials) || [];
  if (!trials.length) return '';
  return trials.map((t, index) => {
    const scale = t.scale_from_tape == null ? '—' : t.scale_from_tape.toFixed(4);
    const measured = t.measured_m == null ? '未尺量' : t.measured_m.toFixed(3) + ' m';
    const tone = t.completed === false ? 'bad' : (t.scale_from_tape == null ? 'unknown' : 'ok');
    return `<div class="chain-row ${tone}"><b>${index + 1}</b>`
      + `<span>里程计 ${t.odom_m.toFixed(3)} m / 耗时 ${t.elapsed_s.toFixed(2)} s`
      + ` / 命令 ${t.commanded_speed_mps.toFixed(3)} m·s⁻¹</span>`
      + `<em>尺量 ${measured} · 标度 ${scale}`
      + ` · 里程计等效速度 ${t.odom_speed_mps.toFixed(3)} m·s⁻¹`
      + `（是命令速度的 ${t.odom_over_command.toFixed(2)} 倍）`
      + ` · 开环比 ${t.open_loop_ratio == null ? '—' : t.open_loop_ratio.toFixed(3)}`
      + (t.completed === false ? ' · <b>本次未跑完，不计样本</b>' : '')
      + (t.note ? ' · ' + t.note : '')
      + `</em></div>`;
  }).join('');
}

/** 纯函数：把 snapshot 变成面板要显示的三段文字，便于 node 测试。 */
function odomPanelView(snapshot) {
  const s = snapshot || {};
  const live = s.distance_result || { state: '未开始' };
  const calibration = s.odom_calibration || null;
  const candidate = s.odom_trial_candidate || null;

  let liveText = `${live.state || '未知'}｜目标(里程计) ${live.target_m ?? '-'} m`
    + `｜里程计报告 ${live.progress_m ?? live.odom_m ?? '-'} m`
    + `｜横向偏离 ${live.lateral_m ?? '-'} m`;
  if (live.elapsed_s != null) liveText += `｜耗时 ${live.elapsed_s} s`;
  if (live.odom_speed_mps != null) liveText += `｜里程计等效速度 ${live.odom_speed_mps} m·s⁻¹`;
  if (live.commanded_speed_mps != null) liveText += `（命令 ${live.commanded_speed_mps}）`;
  if (live.detail) liveText += `\n${live.detail}`;
  liveText += candidate
    ? `\n本次已就绪：里程计 ${candidate.odom_m.toFixed(3)} m / ${candidate.elapsed_s.toFixed(2)} s`
      + ` → 尺量后点「记录本次」`
    : `\n没有可记录的完整试验（中途停止/超时的不算样本）`;

  const consistency = calibration && calibration.consistency;
  const consistencyText = consistency && consistency.median != null
    ? `里程计等效速度/命令速度 中位数 ${consistency.median}`
      + `（样本 ${consistency.ratios.length}）`
      + (consistency.suspicious
        ? ' ⚠️ 明显偏离 1：里程计自身不自洽，先查固件常量/丢帧/打滑'
        : ' ✓ 与命令时间自洽')
    : '里程计自洽性：样本不足';

  const scale = calibration && calibration.scale;
  const scaleText = scale
    ? `尺量标度 中位数 ${scale.median}（n=${scale.n}，波动 ${(scale.relative_spread * 100).toFixed(1)}%）`
      + (scale.usable ? ' ✓ 可用于定标度' : ' ✗ 样本不足或波动过大，先别乘系数')
    : '尺量标度：还没有尺量数据';

  const openLoop = calibration && calibration.open_loop;
  const openLoopText = openLoop
    ? `开环（命令速度×时间 vs 尺量）中位数比 ${openLoop.median}`
      + (openLoop.trustworthy ? ' ✓ 命令速度可信' : ' ⚠️ 偏差超 15%：命令速度或末段减速有问题')
    : '开环速度对照：还没有尺量数据';

  return {
    tone: ODOM_VERDICT_TONE(calibration),
    liveText,
    consistencyText,
    scaleText,
    openLoopText,
    verdict: (calibration && calibration.verdict) || '还没有数据',
    nextStep: (calibration && calibration.next_step) || '先跑一次定距测试',
    rowsHtml: odomTrialRows(calibration),
    canRecord: candidate != null,
  };
}

function renderOdom(s) {
  const view = odomPanelView(s);
  const set = (id, text) => { const node = document.getElementById(id); if (node) node.textContent = text; };
  set('odomLive', view.liveText);
  set('odomConsistency', view.consistencyText);
  set('odomScale', view.scaleText);
  set('odomOpenLoop', view.openLoopText);
  set('odomVerdict', view.verdict);
  set('odomNextStep', '下一步：' + view.nextStep);
  const panel = document.getElementById('odomTrials');
  if (panel) panel.innerHTML = view.rowsHtml;
  const verdict = document.getElementById('odomVerdict');
  if (verdict) verdict.className = 'route-live ' + view.tone;
  const button = document.getElementById('odomRecord');
  if (button) button.disabled = !view.canRecord;
}

if (typeof module !== 'undefined') module.exports = { odomPanelView, renderOdom, odomTrialRows };
