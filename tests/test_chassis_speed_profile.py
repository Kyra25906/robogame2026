"""Host-compile the actual firmware kinematics, limiter and ramp functions."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / 'Four_Motor_PID_Test_1/Four_Motor_PID_Test'


def extract(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class ChassisSpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('gcc')
        if not compiler:
            raise unittest.SkipTest('Host gcc required')
        temp = tempfile.TemporaryDirectory(prefix='chassis_speed_')
        cls.addClassCleanup(temp.cleanup)
        directory = Path(temp.name)
        header = re.sub(r'^#include[^\n]*', '', (FW/'Core/Inc/chassis.h').read_text(encoding='utf-8'), flags=re.M)
        chassis = re.sub(r'^#include[^\n]*', '', (FW/'Core/Src/chassis.c').read_text(encoding='utf-8'), flags=re.M)
        control = (FW/'Core/Src/chassis_control.c').read_text(encoding='utf-8')
        main = (FW/'Core/Src/main.c').read_text(encoding='utf-8')
        protocol = (FW/'Core/Src/rpi_protocol.c').read_text(encoding='utf-8')
        prefix = r'''
#include <stdint.h>
#include <stdlib.h>
#include <assert.h>
#include <math.h>
#include <string.h>
'''
        support = r'''
typedef struct { float vx,vy,wz; uint8_t enable; } RPI_VelocityCommand;
static RPI_VelocityCommand chassis_rpi_command, rpi_velocity_command;
static float chassis_rpi_vx, chassis_rpi_vy, chassis_rpi_wz;
static uint32_t chassis_rpi_ramp_tick;
static uint8_t chassis_rpi_ramp_active;
static float rpi_vx_limit=CHASSIS_RPI_VX_LIMIT_MPS;
static float rpi_vy_limit=CHASSIS_RPI_VY_LIMIT_MPS;
static float rpi_wz_limit=CHASSIS_RPI_WZ_LIMIT_RADPS;
static uint8_t rpi_limits_ready=1, rpi_protocol_fault, rpi_velocity_pending;
static float RPI_ReadF32LE(const uint8_t *p){float f;memcpy(&f,p,4);return f;}
static int RPI_FloatIsFinite(float f){return isfinite(f);}
static float RPI_Abs(float f){return fabsf(f);}
static void RPI_ClearVelocityCommand(void){memset(&rpi_velocity_command,0,sizeof(rpi_velocity_command));rpi_velocity_pending=0;}
static void RPI_MarkControlLinkAlive(void){}
static volatile float base_target_rpm[4];
'''
        normalize_macros = '\n'.join(re.findall(r'^#define MOTOR_(?:TEST_TARGET_RPM_MAX|TARGET_ZERO_RPM)\s+[^\n]+', main, re.M))
        functions = '\n'.join(extract(control, sig) for sig in (
            'static void ChassisControl_ResetRpiRamp(',
            'static float ChassisControl_RampValue(',
            'static void ChassisControl_UpdateRpiRamp('))
        functions += extract(main, 'static void Motor_NormalizeTargets(')
        functions += extract(protocol, 'static void RPI_HandleCmdVel(')
        harness = r'''
static void send(float x,float y,float w,uint8_t en){
 uint8_t p[13];memcpy(p,&x,4);memcpy(p+4,&y,4);memcpy(p+8,&w,4);p[12]=en;
 rpi_protocol_fault=0;rpi_velocity_pending=0;RPI_HandleCmdVel(p,13);
}
int main(int argc,char **argv){
 assert(argc==2);int scenario=atoi(argv[1]);volatile float rpm[4];float x,y,w;
 if(scenario==1){
  assert(Chassis_SetVelocityPhysical(.8f,0,0,rpm));
  for(int i=0;i<4;i++)base_target_rpm[i]=rpm[i];
  Motor_NormalizeTargets();
  assert(Chassis_GetBodyVelocity(base_target_rpm,&x,&y,&w));
  assert(fabsf(x-.8f)<.0001f);assert(fabsf(y)<.0001f);assert(fabsf(w)<.0001f);
  assert(fabsf(rpm[0])>127 && fabsf(rpm[0])<128);
 }else if(scenario==2){
  Chassis_SetVelocityPhysical(0,.4f,0,rpm);Chassis_GetBodyVelocity(rpm,&x,&y,&w);
  assert(fabsf(y-.4f)<.0001f);
  Chassis_SetVelocityPhysical(0,0,1,rpm);Chassis_GetBodyVelocity(rpm,&x,&y,&w);
  assert(fabsf(w-1)<.0001f);
 }else if(scenario==3){
  Chassis_SetVelocityPhysical(.8f,.4f,1,rpm);
  for(int i=0;i<4;i++)assert(fabsf(rpm[i])<=CHASSIS_RPI_MAX_WHEEL_RPM+.0001f);
 }else if(scenario==4){
  Chassis_SetCommand(1,0,0,rpm);
  for(int i=0;i<4;i++)assert(fabsf(fabsf(rpm[i])-100)<.0001f);
  Chassis_SetCommand(0,1,0,rpm);
  for(int i=0;i<4;i++)assert(fabsf(fabsf(rpm[i])-80)<.0001f);
 }else if(scenario==5){
  send(.8f,.4f,1,1);assert(!rpi_protocol_fault && rpi_velocity_pending);
  send(.801f,0,0,1);assert(rpi_protocol_fault && !rpi_velocity_pending);
  send(0,.401f,0,1);assert(rpi_protocol_fault);
  send(0,0,1.01f,1);assert(rpi_protocol_fault);
  send(NAN,0,0,1);assert(rpi_protocol_fault);
  send(0,0,0,0);assert(!rpi_protocol_fault && rpi_velocity_pending);
 }else if(scenario==6){
  ChassisControl_ResetRpiRamp();chassis_rpi_command.vx=.8f;
  ChassisControl_UpdateRpiRamp(1000);assert(chassis_rpi_vx==0);
  for(int tick=1010;tick<=2000;tick+=10)ChassisControl_UpdateRpiRamp(tick);
  assert(fabsf(chassis_rpi_vx-.8f)<.0001f);
  chassis_rpi_command.vx=0;ChassisControl_UpdateRpiRamp(2010);assert(chassis_rpi_vx==0);
  chassis_rpi_command.vx=.8f;ChassisControl_UpdateRpiRamp(4010);assert(chassis_rpi_vx<=.041f);
  ChassisControl_ResetRpiRamp();assert(chassis_rpi_vx==0 && chassis_rpi_ramp_active==0);
 }else {assert(0);}
 return 0;
}
'''
        src = directory/'check.c'
        src.write_text(prefix+header+chassis+support+normalize_macros+'\n'+functions+harness, encoding='utf-8')
        cls.exe = directory/'check.exe'
        result = subprocess.run([compiler, '-std=c99', str(src), '-lm', '-o', str(cls.exe)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def test_forward_survives_all_wheel_limits(self): self.run_case(1)
    def test_lateral_and_yaw_limits(self): self.run_case(2)
    def test_combined_motion_rescales_wheels(self): self.run_case(3)
    def test_remote_control_profile_unchanged(self): self.run_case(4)
    def test_protocol_accepts_limits_rejects_excess_and_nan(self): self.run_case(5)
    def test_ramp_and_immediate_zero(self): self.run_case(6)

    def run_case(self, case):
        result = subprocess.run([str(self.exe), str(case)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
