const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const web=path.join(__dirname,'../tools/field_dashboard_web');
function page(){
  const ids=[...fs.readFileSync(path.join(web,'index.html'),'utf8').matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
  const elements=Object.fromEntries(ids.map(id=>[id,{style:{},value:'',options:[],textContent:'',add(){}}]));
  Object.assign(elements.speed,{value:'0.10'});
  elements.distanceMeters.value='0.5';elements.distanceSpeed.value='0.05';
  let snapshot={mode:'OBSERVE',safety:{communication_ok:true,physical_start:true,age_s:.01},
    blockers:[],telemetry:{},processes:[],mechanism_services:{},distance_result:{state:'未开始'},
    drive_limits:{max_v:.8,max_vy:.4,max_w:.8}};
  const calls=[],listeners={},intervals=[];
  const context=vm.createContext({console,document:{hidden:false,
    querySelector(s){assert.ok(elements[s.slice(1)],'Missing element '+s);return elements[s.slice(1)]},
    querySelectorAll(){return []},addEventListener(k,f){listeners[k]=f}},
    window:{addEventListener(k,f){listeners[k]=f}},
    setTimeout(){},setInterval(f){intervals.push(f);return intervals.length},clearInterval(){},
    EventSource:class{},Option:class{},
    async fetch(url,options){
      if(options){calls.push([url,JSON.parse(options.body)]);if(url==='/api/distance/start')snapshot={...snapshot,mode:'DISTANCE'};
        if(url==='/api/distance/stop')snapshot={...snapshot,mode:'OBSERVE'};
        return {ok:true,json:async()=>({ok:true,token:'session'})};}
      return {ok:true,json:async()=>snapshot};
    }});
  vm.runInContext(fs.readFileSync(path.join(web,'line_diagnostics.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(web,'app.js'),'utf8'),context);
  return {context,elements,calls,listeners,intervals};
}
test('page renders declared elements and honors asymmetric manual speed limits',async()=>{
  const p=page();await vm.runInContext('refresh()',p.context);
  assert.equal(p.elements.speed.max,'0.8');
  p.elements.speed.value='0.8';
  assert.deepEqual(Array.from(vm.runInContext("vector('forward')",p.context)),[.8,0,0]);
  assert.deepEqual(Array.from(vm.runInContext("vector('left')",p.context)),[0,.4,0]);
});
test('start sends relative distance and heartbeat, blur requests stop',async()=>{
  const p=page();await vm.runInContext('refresh()',p.context);
  await p.elements.distanceStart.onclick();
  assert.deepEqual(p.calls[0],['/api/distance/start',{distance_m:.5,speed_mps:.05}]);
  await p.intervals[1]();
  assert.equal(p.calls[1][0],'/api/distance/heartbeat');
  p.listeners.blur();await new Promise(resolve=>setImmediate(resolve));
  assert.ok(p.calls.some(c=>c[0]==='/api/distance/stop'));
});
test('old server disables distance control',()=>{
  const p=page();
  vm.runInContext("renderDistance({mode:'OBSERVE',blockers:[]})",p.context);
  assert.equal(p.elements.distanceStart.disabled,true);
  assert.match(p.elements.distanceResult.textContent,/服务版本未更新/);
});
