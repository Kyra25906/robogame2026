"""Host fault injection for the real USB startup functions; no hardware access."""
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FW = ROOT / "Four_Motor_PID_Test_1/Four_Motor_PID_Test"


def function(source, name, return_type="USBD_StatusTypeDef"):
    start = source.index(return_type + " " + name + "(")
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class FirmwareUsbStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("gcc")
        if not compiler:
            raise unittest.SkipTest("Host gcc is required for firmware fault injection")
        cls.temp = tempfile.TemporaryDirectory(prefix="usb_startup_")
        cls.addClassCleanup(cls.temp.cleanup)
        directory = pathlib.Path(cls.temp.name)
        app = (FW / "USB_DEVICE/App/usb_device.c").read_text(encoding="utf-8")
        app = re.sub(r'^#include[^\n]*', '', app, flags=re.M)
        ll = function((FW / "USB_DEVICE/Target/usbd_conf.c").read_text(encoding="utf-8"), "USBD_LL_Init")
        abort = function((FW / "USB_DEVICE/Target/usbd_conf.c").read_text(encoding="utf-8"), "USBD_LL_AbortInit", "void")
        source = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
typedef int USBD_StatusTypeDef;
enum { USBD_OK=0, USBD_BUSY=1, USBD_FAIL=2, HAL_OK=0, DEVICE_FS=0,
       USB_OTG_FS=1, PCD_SPEED_FULL=1, PCD_PHY_EMBEDDED=1, DISABLE=0,
       OTG_FS_IRQn=67, HAL_PCD_STATE_RESET=0 };
#define USE_HAL_PCD_REGISTER_CALLBACKS 0
typedef struct { void *pData; void *pClassData; int id; } USBD_HandleTypeDef;
typedef struct {
  void *pData; int Instance,State;
  struct { int dev_endpoints,speed,dma_enable,phy_itface,Sof_enable,
    low_power_enable,lpm_enable,vbus_sensing_enable,use_dedicated_ep1; } Init;
} PCD_HandleTypeDef;
PCD_HandleTypeDef hpcd_USB_OTG_FS;
int FS_Desc, USBD_CDC, USBD_Interface_fops_FS;
static int fail_stage, fail_hal, status_to_return, stage_calls, hal_calls, aborted;
static int hal_step(void) { return ++hal_calls == fail_hal ? 1 : HAL_OK; }
int HAL_PCD_Init(PCD_HandleTypeDef *p) { (void)p; return hal_step(); }
int HAL_PCDEx_SetRxFiFo(PCD_HandleTypeDef *p,int n) { (void)p;(void)n;return hal_step(); }
int HAL_PCDEx_SetTxFiFo(PCD_HandleTypeDef *p,int n,int size) {
  (void)p;(void)n;(void)size;return hal_step();
}
USBD_StatusTypeDef USBD_LL_Init(USBD_HandleTypeDef *p);
static int step(void) { return ++stage_calls == fail_stage ? status_to_return : USBD_OK; }
int USBD_Init(USBD_HandleTypeDef *p,void *d,int id) {
  (void)d; p->id=id; int s=step(); return s ? s : USBD_LL_Init(p);
}
int USBD_RegisterClass(USBD_HandleTypeDef *p,void *c) { (void)p;(void)c;return step(); }
int USBD_CDC_RegisterInterface(USBD_HandleTypeDef *p,void *i) { (void)p;(void)i;return step(); }
int USBD_Start(USBD_HandleTypeDef *p) {
  /* Simulate state created just before a partial start fails. */
  p->pClassData=p; return step();
}
void HAL_NVIC_DisableIRQ(int irq) { assert(irq==OTG_FS_IRQn);assert(aborted==0);aborted=1; }
#define __HAL_RCC_USB_OTG_FS_FORCE_RESET() do { assert(aborted==1);aborted=2; } while(0)
#define __HAL_RCC_USB_OTG_FS_RELEASE_RESET() do { assert(aborted==2);aborted=3; } while(0)
void HAL_PCD_MspDeInit(PCD_HandleTypeDef *p) { assert(p==&hpcd_USB_OTG_FS);assert(aborted==3);aborted=4; }
void HAL_NVIC_ClearPendingIRQ(int irq) { assert(irq==OTG_FS_IRQn);assert(aborted==4);aborted=5; }
'''
        source += abort + "\n" + ll + "\n" + app
        source += r'''
int main(int argc,char **argv) {
  assert(argc==4);
  fail_stage=atoi(argv[1]); fail_hal=atoi(argv[2]); status_to_return=atoi(argv[3]);
  int result=MX_USB_DEVICE_Init();
  if (fail_stage || fail_hal) {
    assert(result==(fail_hal ? USBD_FAIL : status_to_return));
    assert(usb_init_stage==(fail_hal ? 1 : fail_stage));
    assert(stage_calls==(fail_hal ? 1 : fail_stage));
    assert(aborted==5 && hpcd_USB_OTG_FS.State==HAL_PCD_STATE_RESET);
    assert(hUsbDeviceFS.pClassData==NULL && hUsbDeviceFS.pData==NULL);
    if(fail_hal) assert(hal_calls==fail_hal);
  } else {
    assert(result==USBD_OK && usb_init_stage==5);
    assert(stage_calls==4 && hal_calls==4 && aborted==0);
    assert(hUsbDeviceFS.pClassData!=NULL);
  }
  return 0;
}
'''
        cfile = directory / "startup.c"
        cfile.write_text(source, encoding="utf-8")
        cls.exe = directory / "startup.exe"
        subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", str(cfile), "-o", str(cls.exe)], check=True, capture_output=True, text=True)

    def run_case(self, stage=0, hal=0, status=2):
        subprocess.run([str(self.exe), str(stage), str(hal), str(status)], check=True, timeout=5, capture_output=True)

    def test_success(self):
        self.run_case()

    def test_stack_init_failure(self):
        self.run_case(stage=1)

    def test_class_failure(self):
        self.run_case(stage=2)

    def test_interface_failure(self):
        self.run_case(stage=3)

    def test_partial_start_failure(self):
        self.run_case(stage=4)

    def test_busy_is_not_success(self):
        self.run_case(stage=4, status=1)

    def test_hal_init_failure_propagates(self):
        self.run_case(hal=1)

    def test_rx_fifo_failure(self):
        self.run_case(hal=2)

    def test_tx0_fifo_failure(self):
        self.run_case(hal=3)

    def test_tx1_fifo_failure(self):
        self.run_case(hal=4)

    def test_protocol_ready_before_transport(self):
        source = (FW / "Core/Src/main.c").read_text(encoding="utf-8")
        self.assertEqual(source.count("RPI_Init();"), 1)
        init = source.index("RPI_Init();")
        self.assertLess(init, source.index("MX_USB_DEVICE_Init()"))
        self.assertLess(init, source.index("RPI_LinkInit();"))

    def test_usb_fault_uses_existing_rearm_gate(self):
        source = (FW / "Core/Src/main.c").read_text(encoding="utf-8")
        self.assertRegex(source, r'if \(MX_USB_DEVICE_Init\(\) != USBD_OK\)\s*\{[^}]*startup_fault \|= STARTUP_FAULT_USB;')
        self.assertRegex(source, r'if \(startup_fault != 0U\)\s*\{\s*rearm_block_reason = 1U;\s*return;')


if __name__ == "__main__":
    unittest.main()
