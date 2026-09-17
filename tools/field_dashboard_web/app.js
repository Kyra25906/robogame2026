const $=s=>document.querySelector(s);let snapshot={},seq=0,driveTimer=null,paused=false,logs=[];
async function api(path,body={}){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();if(!r.ok||j.ok===false)throw Error(j.error||j.result?.detail||(j.chassis?`底盘停止请求：${j.chassis.success?'已发送':'失败'}；机构停止请求：${j.mechanism.success?'已完成':'失败（机构未接入时也会出现）'}；进程详情已写入日志`:'操作失败'));return j}
function toast(message,bad=false){const e=$('#toast');e.textContent=message;e.style.display='block';e.style.borderLeft=`5px solid ${bad?'#ef4444':'#22c55e'}`;setTimeout(()=>e.style.display='none',3500)}
function statusBox(label,value,good){return `<div class="status ${value===null?'unknown':good?'ok':'bad'}"><b>${label}</b><br>${value===null?'未知':value}</div>`}
function render(s){snapshot=s;const names={grab:'/gripper/grab',release:'/gripper/release',home:'/mechanism/home',stop:'/mechanism/stop',lift:'/lift/set_height'};$('#mechServices').textContent=Object.entries(names).map(([k,v])=>v+'：'+(s.mechanism_services?.[k]===true?'可用':s.mechanism_services?.[k]===false?'不可用':'未知（ROS 未连接）')).join('\n');document.querySelectorAll('[data-mech]').forEach(b=>{const k=b.dataset.mech;b.disabled=s.mechanism_services?.[k]!==true||(k!=='stop'&&(s.mechanism_busy||s.mode!=='OBSERVE'||s.blockers.length>0));});$('#mode').textContent=s.mode;const x=s.safety;$('#safety').innerHTML=statusBox('通信',x.communication_ok?'正常':'异常',x.communication_ok)+statusBox('物理授权',x.physical_start?'已授权':'未授权',x.physical_start)+statusBox('急停',x.emergency_stop?'触发':'未触发',!x.emergency_stop)+statusBox('机构',x.mechanism_fault?'故障':'正常',!x.mechanism_fault)+statusBox('状态年龄',x.age_s==null?null:x.age_s.toFixed(3)+'s',x.age_s!=null&&x.age_s<=.3)+statusBox('电池',x.battery_voltage?.toFixed(2)+'V',true);$('#blockers').innerHTML=s.blockers.map(b=>`<div class="blocker"><b>${b.message}</b><br><code>${b.evidence}</code></div>`).join('');$('#processes').innerHTML=s.processes.map(p=>`<div class="process"><span>${p.name}</span><span>${p.running?'运行 PID '+p.pid:p.exit_code==null?'未启动':'退出 '+p.exit_code}</span><button data-${p.running?'stop':'start'}="${p.name}">${p.running?'停止':'启动'}</button></div>`).join('');$('#mechanism').textContent=`夹爪：${x.gripper_closed?'闭合':'张开'}  方块：${x.cube_present?'检测到':'未检测到'}\nIMU：${x.imu_valid?'有效':x.calibrating?'标定中':'无效'}  boot_id=${x.boot_id}\n\n${JSON.stringify(s.last_mechanism_result||'暂无动作结果',null,2)}`;const t=s.telemetry;$('#telemetry').innerHTML=`位姿：${t.pose_x??'-'}, ${t.pose_y??'-'}<br>速度：${t.velocity_vx??'-'}, ${t.velocity_vy??'-'}, ${t.velocity_wz??'-'}<br>频率：odom ${t.wheel_odom_hz??0} Hz（${t.wheel_odom_age_s??'-'}s 前） / IMU ${t.imu_hz??0} Hz<br>巡线：${t.line_status??'-'}`;const ld=lineDiagnostics(t);$('#lineDiagnosis').textContent=ld.diagnosis;$('#lineDetails').textContent=ld.details;$('#lineBars').style.opacity=ld.stale?'.35':'1';$('#lineBars').innerHTML=ld.heights.map((h,i)=>`<i style="height:${h}%" title="通道 ${i+1}: ${t.line_sensor[i]}"></i>`).join('');const _lc=document.querySelector('#lineChain');if(_lc)_lc.innerHTML=ld.chainHtml;bindDynamic()}
async function refresh(){try{const s=await (await fetch('/api/snapshot',{cache:'no-store'})).json();render(s);renderDistance(s);renderGrasp(s);renderCalibration(s);renderRoute(s);renderOdom(s)}catch(e){$('#lineDiagnosis').textContent='网页状态连接失败：以下为旧值，不能判断当前车辆状态';$('#lineBars').style.opacity='.35';toast('状态连接失败：'+e.message,true)}}
function bindDynamic(){document.querySelectorAll('[data-start]').forEach(b=>b.onclick=()=>act(`/api/process/${b.dataset.start}/start`));document.querySelectorAll('[data-stop]').forEach(b=>b.onclick=()=>act(`/api/process/${b.dataset.stop}/stop`))}
async function act(path,body={}){try{await api(path,body);toast('操作已受理');await refresh()}catch(e){toast(e.message,true);await refresh()}}
document.querySelectorAll('[data-group]').forEach(b=>b.onclick=async()=>{for(const n of ['bridge','localization','motion'])await act(`/api/process/${n}/start`)});document.querySelectorAll('[data-mech]').forEach(b=>b.onclick=()=>{const k=b.dataset.mech;if(k==='lift'&&!$('#liftHeight').value.trim()){toast('请填写高度（米）',true);return}act(`/api/mechanism/${k}`,k==='lift'?{height_m:Number($('#liftHeight').value)}:{})});$('#acquire').onclick=()=>act('/api/control/manual/acquire');$('#release').onclick=()=>act('/api/control/manual/release');$('#stopAll').onclick=()=>act('/api/control/stop-all');$('#lineStart').onclick=()=>act('/api/line/start');$('#lineStop').onclick=()=>act('/api/line/stop');$('#speed').oninput=e=>$('#speedValue').textContent=Number(e.target.value).toFixed(2);
function vector(kind){const v=Number($('#speed').value),side=Math.min(v,snapshot.drive_limits?.max_vy??.3),w=Math.min(.8,v*2.5);return {forward:[v,0,0],back:[-v,0,0],left:[0,side,0],right:[0,-side,0],ccw:[0,0,w],cw:[0,0,-w],stop:[0,0,0]}[kind]}
function begin(kind){end();const send=()=>{const [vx,vy,wz]=vector(kind);api('/api/control/drive',{vx,vy,wz,sequence:++seq}).catch(e=>{end();toast(e.message,true)})};send();driveTimer=setInterval(send,100)}function end(){if(driveTimer){clearInterval(driveTimer);driveTimer=null}if(snapshot.mode==='MANUAL')api('/api/control/drive',{vx:0,vy:0,wz:0,sequence:++seq}).catch(()=>{})}
document.querySelectorAll('[data-drive]').forEach(b=>{b.onpointerdown=e=>{e.preventDefault();begin(b.dataset.drive)};b.onpointerup=end;b.onpointerleave=end});const keys={w:'forward',s:'back',a:'left',d:'right',q:'ccw',e:'cw'};let key=null;window.onkeydown=e=>{if(keys[e.key.toLowerCase()]&&!key){key=e.key.toLowerCase();begin(keys[key])}};window.onkeyup=e=>{if(e.key.toLowerCase()===key){key=null;end()}};window.onblur=end;
function showLogs(){const f=$('#logFilter').value,q=$('#logSearch').value.toLowerCase();$('#logs').textContent=logs.filter(x=>(!f||x.name===f)&&(!q||x.line.toLowerCase().includes(q))).map(x=>`[${x.name}] ${x.line}`).join('\n');if(!paused)$('#logs').scrollTop=$('#logs').scrollHeight}
$('#pause').onclick=()=>{paused=!paused;$('#pause').textContent=paused?'继续滚动':'暂停滚动'};$('#clear').onclick=()=>{logs=[];showLogs()};$('#logFilter').onchange=showLogs;$('#logSearch').oninput=showLogs;
const es=new EventSource('/api/events');es.onmessage=e=>{const x=JSON.parse(e.data);if(x.type==='log'){logs.push(x);if(logs.length>3000)logs.shift();if(![...$('#logFilter').options].some(o=>o.value===x.name))$('#logFilter').add(new Option(x.name,x.name));showLogs()}if(x.type==='process'||x.type==='control'||x.type==='mechanism')refresh()};es.onerror=()=>toast('实时连接中断，手动控制将自动停车',true);setInterval(refresh,1000);refresh();

let distanceToken=null,distancePending=false,distanceHeartbeatBusy=false,distanceCancelled=false;
function renderDistance(s){
  const max=s.drive_limits?.max_v??0.3;
  $('#speed').max=String(max);
  if(Number($('#speed').value)>max)$('#speed').value=String(max);
  $('#speedValue').textContent=Number($('#speed').value).toFixed(2);
  const r=s.distance_result||{state:'服务版本未更新，请先更新并重启树莓派网页服务'};
  $('#distanceResult').textContent=`${r.state}\n目标：${r.target_m??'-'} m\n已前进：${r.progress_m??'-'} m\n横向偏离：${r.lateral_m??'-'} m\n${r.detail||''}`;
  $('#distanceStart').disabled=distancePending||!s.distance_result||s.mode!=='OBSERVE'||s.mechanism_busy||s.blockers.length>0;
  if(s.mode!=='DISTANCE'&&!distancePending)distanceToken=null;
}
$('#distanceStart').onclick=async()=>{
  if(distancePending)return;
  distancePending=true;distanceCancelled=false;$('#distanceStart').disabled=true;
  try{
    const r=await api('/api/distance/start',{distance_m:Number($('#distanceMeters').value),speed_mps:Number($('#distanceSpeed').value)});
    if(distanceCancelled||document.hidden){await api('/api/distance/stop');return;}
    distanceToken=r.token;toast('定距已启动');
  }catch(e){toast(e.message,true)}finally{distancePending=false;await refresh()}
};
function stopDistance(){distanceCancelled=true;distanceToken=null;return act('/api/distance/stop')}
$('#distanceStop').onclick=stopDistance;
/* 里程计标定：把本次定距结果与尺量值配成一条样本（服务端负责校验与汇总）。 */
$('#odomRecord').onclick=()=>{
  const measured=$('#odomMeasured').value.trim();
  if(!measured){toast('请先填写尺量位移（米）',true);return}
  act('/api/odom/record',{measured_m:Number(measured),note:$('#odomNote').value.trim()});
};
$('#odomReset').onclick=()=>act('/api/odom/reset');
window.addEventListener('blur',()=>{if(distanceToken||distancePending)stopDistance()});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&(distanceToken||distancePending))stopDistance()});
setInterval(async()=>{
  if(!distanceToken||distanceHeartbeatBusy)return;
  distanceHeartbeatBusy=true;
  try{await api('/api/distance/heartbeat',{token:distanceToken})}
  catch(e){distanceToken=null;toast(e.message,true)}
  finally{distanceHeartbeatBusy=false}
},100);

/* 黑白标定：按住采集，松开结束；两组齐了再应用。全部状态来自 snapshot.line_calibration。 */
function calibStatusText(c){
  const v=c.verdict||{};
  const head='白底 '+c.white_frames+' 帧 / '+c.white_seconds.toFixed(1)+' s，黑线 '+c.black_frames+' 帧 / '+c.black_seconds.toFixed(1)+' s';
  let tone='';
  if(c.capturing) tone='采集中（'+c.capturing+'）';
  else if(v.ok) tone='校验通过：'+(v.detail||'');
  else tone='未通过：'+(v.detail||'');
  const applied=(c.applied&&c.applied.state)?('｜已推送：'+(c.applied.detail||c.applied.state)):'';
  const note=(v.ok&&v.note)?('｜'+v.note):'';
  return head+'\n'+tone+note+applied+'\n下一步：'+(c.next_step||'');
}
function renderCalibration(s){
  const el=document.querySelector('#calibStatus'); if(!el) return;
  const c=s.line_calibration; if(!c){ el.textContent='标定：面板未提供状态'; return; }
  el.textContent=calibStatusText(c);
  const apply=document.querySelector('#calibApply');
  if(apply) apply.disabled=!c.ready||!!c.capturing;
}
function bindCalibration(){
  const hold=(id,target)=>{
    const b=document.querySelector(id); if(!b) return;
    b.addEventListener('pointerdown',async e=>{e.preventDefault();b.setPointerCapture?.(e.pointerId);
      try{await act('/api/line/calibrate/start',{target});toast('开始采集'+target)}catch(err){}});
    const stop=async()=>{try{await act('/api/line/calibrate/capture',{target});toast('已结束采集'+target)}catch(err){}};
    b.addEventListener('pointerup',stop); b.addEventListener('pointercancel',stop);
  };
  hold('#calibWhite','white'); hold('#calibBlack','black');
  const apply=document.querySelector('#calibApply');
  if(apply) apply.onclick=()=>act('/api/line/calibrate/apply',{});
  const reset=document.querySelector('#calibReset');
  if(reset) reset.onclick=()=>act('/api/line/calibrate/reset',{});
}
document.addEventListener('DOMContentLoaded',bindCalibration);
