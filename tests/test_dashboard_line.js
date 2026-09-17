const {test}=require('node:test');
const assert=require('node:assert/strict');
const {lineDiagnostics}=require('../tools/field_dashboard_web/line_diagnostics.js');
const sample=()=>({line_sensor:[0,1024,2048,4095,0,0,0,0],line_sensor_age_s:0,
  line_analog_valid:true,line_status:'state=ON_LINE dev=0.000 stale=False',line_status_age_s:0,
  line_cmd:{vx:.2,vy:0,wz:0},line_cmd_age_s:0,
  published_cmd:{vx:.2,vy:0,wz:0},published_cmd_age_s:0,
  line_sources:['robot_bridge'],line_sources_age_s:0});
test('missing and stale readings are not healthy',()=>{
  assert.match(lineDiagnostics({}).diagnosis,/尚未收到/);
  assert.match(lineDiagnostics({...sample(),line_sensor_age_s:1}).diagnosis,/已过期/);
});
test('invalid analog data explains controller rejection',()=>{
  assert.match(lineDiagnostics({...sample(),line_analog_valid:false}).diagnosis,/不会使用/);
});
test('raw bars use 4095 full scale',()=>{
  const d=lineDiagnostics(sample());
  assert.ok(d.heights[1]>24 && d.heights[1]<26);
  assert.equal(d.heights[3],100);
  assert.match(d.diagnosis,/尚不能判定/);
});
test('mock and mixed publishers cannot be hardware evidence',()=>{
  const d=lineDiagnostics({...sample(),line_sources:['robot_bridge','line_sensor_mock']});
  assert.match(d.diagnosis,/模拟数据/);assert.match(d.diagnosis,/多个发布者/);
});
test('zero published velocity and stale velocity are distinct',()=>{
  const s={...sample(),published_cmd:{vx:0,vy:0,wz:0}};
  assert.match(lineDiagnostics(s).diagnosis,/发布速度为零/);
  assert.match(lineDiagnostics({...s,published_cmd_age_s:2}).diagnosis,/速度观测缺失或已过期/);
});
test('fresh status may still report stale sensor',()=>{
  assert.match(lineDiagnostics({...sample(),line_status:'state=LOST stale=True'}).diagnosis,/控制器报告/);
});
test('chain table always shows all six links',()=>{
  const d=lineDiagnostics(sample());
  const rows=(d.chainHtml.match(/chain-row/g)||[]).length;
  assert.equal(rows,6,'传感器→有效性→控制器→门控→算法输出→实际发布，六段都要在页面上');
  for(const label of ['① 传感器读数','② 模拟量有效性','③ 控制器','④ 门控放行','⑤ 算法输出','⑥ 实际发布']){
    assert.match(d.chainHtml,new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
  }
});
test('controller gate evidence is surfaced per layer',()=>{
  const blocked={...sample(),line_diag:{blocked:true,reasons:['STM32 通信不可用 (communication_ok=false)'],status_ready:true,communication_ok:false,mechanism_fault:false,reading_stale:false}};
  const d=lineDiagnostics(blocked);
  assert.match(d.diagnosis,/门控未放行/);
  assert.match(d.diagnosis,/communication_ok=false/);
  assert.match(d.chainHtml,/被拦住/);
  assert.equal(d.blocking,true);
});
test('slow frame rate is reported as frame loss',()=>{
  const d=lineDiagnostics({...sample(),line_hz:12.5});
  assert.match(d.diagnosis,/帧率偏低/);
});
test('mcu tick anomalies are reported before other conclusions',()=>{
  const d=lineDiagnostics({...sample(),line_tick_anomalies:7});
  assert.match(d.diagnosis,/mcu_tick 跳变或倒退 7 次/);
  assert.match(d.chainHtml,/异常 7 次/);
});
test('legacy controller without diag still renders the chain',()=>{
  const d=lineDiagnostics({...sample(),line_diag:undefined});
  assert.equal(d.blocking,null,'没有门控证据时不能声称"已放行"，只能标未知');
  assert.match(d.chainHtml,/控制器未发布门控证据/);
});
