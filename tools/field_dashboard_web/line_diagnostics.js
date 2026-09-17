/* 只解释观测证据，不发送控制命令。年龄阈值用于面板提示。 */
function lineDiagnostics(t) {
  const fresh = (key, limit=.5) => Number.isFinite(t[key+'_age_s']) && t[key+'_age_s'] <= limit;
  const age = key => Number.isFinite(t[key+'_age_s']) ? t[key+'_age_s'].toFixed(2)+' s 前' : '未收到';
  const velocity = key => {
    const v=t[key];
    return v ? `vx=${v.vx.toFixed(3)} m/s，vy=${v.vy.toFixed(3)} m/s，wz=${v.wz.toFixed(3)} rad/s（${age(key)}）` : '未收到';
  };
  const sourcesFresh=fresh('line_sources', 2.5);
  const sources=t.line_sources||[];

  /* 控制器发布的逐层门控证据（#diag#）；旧版控制器没有，退回纯文本推断。 */
  const diag=(t.line_diag && typeof t.line_diag==='object') ? t.line_diag : null;
  const reasons=(diag && Array.isArray(diag.reasons)) ? diag.reasons.filter(Boolean) : [];

  let diagnosis='已有观测数据；请对照左右移线结果，尚不能判定真车巡线正常';
  if (!t.line_sensor) diagnosis='尚未收到巡线数据：检查 bridge、STM32 巡线遥测及传感器';
  else if (!fresh('line_sensor', .25)) diagnosis='巡线读数已过期（面板参考阈值 0.25 s），下面保留的是旧值';
  else if (t.line_analog_valid!==true) diagnosis='模拟量无效：当前巡线控制器不会使用这帧数据';
  else if (!fresh('line_status')) diagnosis='读数正在更新，但控制器状态未收到或已过期';
  else if (/stale=True/.test(t.line_status)) diagnosis='控制器报告传感器数据过期';
  else if (diag && diag.blocked===true) diagnosis='读数正常，但控制器门控未放行：'+(reasons[0]||'blocked=true');
  else if (!fresh('line_cmd') || !fresh('published_cmd')) diagnosis='速度观测缺失或已过期，请检查巡线控制器和速度发布';
  else if (Object.values(t.line_cmd).some(v=>Math.abs(v)>1e-6) && Object.values(t.published_cmd).every(v=>Math.abs(v)<=1e-6)) diagnosis='算法要求运动，但观测到发布速度为零：检查状态门控、控制源及其他发布者';

  /* 新增链路段：帧率异常与 mcu tick 跳变——这两条此前网页看不到。 */
  const hz=Number.isFinite(t.line_hz)?t.line_hz:null;
  const anomalies=Number.isFinite(t.line_tick_anomalies)?t.line_tick_anomalies:null;
  if (fresh('line_sensor',.25) && hz!==null && hz>0 && hz<20)
    diagnosis=`巡线帧率偏低（${hz.toFixed(1)} Hz，固件按 20 ms 上送，应接近 50 Hz）：中间有丢帧。`+diagnosis;
  if (fresh('line_sensor',.25) && anomalies)
    diagnosis=`观测到 mcu_tick 跳变或倒退 ${anomalies} 次：有帧在到达树莓派之前就已丢失。`+diagnosis;

  if (sourcesFresh && sources.some(name=>/mock/i.test(name))) diagnosis='检测到模拟数据发布者，不能作为真车传感器验收。'+diagnosis;
  if (sourcesFresh && sources.length>1) diagnosis='巡线数据有多个发布者，需排查混流。'+diagnosis;
  if(t.line_interface==='legacy') diagnosis='现场使用旧版巡线消息，缺少有效标志和 MCU 时间戳；需升级接口后再验收巡线。底盘定距不依赖巡线消息。';

  /* 链路表：每一行对应一个真实断点，网页上一眼看出断在哪。 */
  const yn=v=>v===true?'是':v===false?'否':'未知';
  const num=(v,d=1)=>Number.isFinite(v)?v.toFixed(d):'—';
  const cmd=key=>{const v=t[key];return v?`vx=${num(v.vx,3)} wz=${num(v.wz,3)}`:'未收到';};
  const freshStatus=fresh('line_status');
  const chain=[
    {step:'① 传感器读数 /line_sensor', ok:t.line_sensor?fresh('line_sensor',.25):null,
     detail:t.line_sensor?`${age('line_sensor')}｜帧率 ${hz===null?'—':num(hz)} Hz｜mcu_tick=${t.line_mcu_tick_ms??'—'}（异常 ${anomalies??'—'} 次）`:'从未收到'},
    {step:'② 模拟量有效性', ok:t.line_analog_valid===true?true:(t.line_analog_valid===false?false:null),
     detail:`analog_valid=${yn(t.line_analog_valid)}｜控制器累计丢弃 ${t.line_invalid_frames??'—'} 帧`},
    {step:'③ 控制器 /line_follow/status', ok:freshStatus?true:null,
     detail:freshStatus?(t.line_status||'').split('#diag#')[0].trim():'未收到或已过期（巡线进程是否启动？）'},
    {step:'④ 门控放行', ok:diag&&typeof diag.blocked==='boolean'?!diag.blocked:null,
     detail:diag?(diag.blocked?('被拦住：'+reasons.join('；')):'已放行（无状态门控拦截）'):'控制器未发布门控证据'},
    {step:'⑤ 算法输出 /line_follow/cmd', ok:fresh('line_cmd')?true:null, detail:cmd('line_cmd')},
    {step:'⑥ 实际发布 /cmd_vel', ok:fresh('published_cmd')?true:null, detail:cmd('published_cmd')},
  ];

  const details=[
    `巡线数据来源：${sourcesFresh ? (sources.join('、')||'未发现发布者') : '来源信息未收到或已过期'}（节点名不保证数据真实性）`,
    `读数：${age('line_sensor')}；MCU tick=${t.line_mcu_tick_ms??'—'}；analog_valid=${t.line_analog_valid??'未知'}`,
    `通道原始值（0～4095）：${(t.line_sensor||[]).join(', ')||'未收到'}`,
    `控制器：${t.line_status||'未收到'}（${age('line_status')}）`,
    `算法输出：${velocity('line_cmd')}`,
    `发布速度：${velocity('published_cmd')}`,
    `速度发布者：${fresh('cmd_sources',2.5) ? (t.cmd_sources||[]).join('、') : '未知或已过期'}（/cmd_vel 为共享话题，不能仅凭最近值归因）`,
  ].join('\n');

  const chainHtml=chain.map(row=>{
    const tone=row.ok===true?'ok':row.ok===false?'bad':'unknown';
    const mark=row.ok===true?'✓':row.ok===false?'✗':'?';
    return `<div class="chain-row ${tone}"><b>${mark}</b><span>${row.step}</span><em>${row.detail}</em></div>`;
  }).join('');

  return {
    diagnosis, details, chainHtml,
    blocking:diag&&typeof diag.blocked==='boolean'?diag.blocked:null,
    stale:!fresh('line_sensor',.25),
    heights:(t.line_sensor||[]).map(v=>Math.max(2,Math.min(100,v/4095*100))),
  };
}
if (typeof module!=='undefined') module.exports={lineDiagnostics};
