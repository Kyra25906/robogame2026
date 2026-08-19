"""A2 / P0-1 启动竞态：上电握手期 communication_ok=False 不得立即判死。

背景（执行队列 2026-08-18 A2 节 + 第三轮复审方案 P0-1）：
- 真实 STM32 上电要先握手（HELLO->ACK）再解码首条 0x12 STATUS 才把
  communication_ok 置 True；`robot_bridge/node.py:199/224` 上电 `_communication_ok
  =False`，首条 STATUS 必带 False。
- 旧 `mission.py` 在**任何状态**下 `not communication_ok` 都立即
  fail(COMMUNICATION_ERROR)（P0-1）→ 真车上电即判死，车一动不动，
  现场排查烧掉整个「车+场地+两人+电控」时段。
- A0.1 已把 fake 升级为忠实时序（`mock_comm_ok_after_s`），本测试是
  「真正杀 P0-1」的 A2：新增 `WAIT_FOR_COMMUNICATION` 前置状态 +
  `startup_wait_timeout_s`（默认 15s，覆盖握手 3s×3 + 余量），
  超时才 fail(COMMUNICATION_ERROR)；运行时语义不变——离开启动等待后
  心跳丢失仍立即判失败。

验证（执行队列 A2 的验证条目）：
- False×N 帧 → True → 正常前进；
- 持续 False 到超时才 fail。
"""
import ast
import unittest
from pathlib import Path

from robogame_core.mission import MissionConfig, MissionMachine, MissionState
from robogame_core.models import MissionResult

MISSION_PATH = (
    Path(__file__).parents[1]
    / "ros2_ws"
    / "src"
    / "robogame_core"
    / "robogame_core"
    / "mission.py"
)
MANAGER_PATH = (
    Path(__file__).parents[1]
    / "ros2_ws"
    / "src"
    / "mission_manager"
    / "mission_manager"
    / "node.py"
)


class WaitForCommunicationTests(unittest.TestCase):
    """A2 行为：启动等待不判死、超时才 fail、离开后心跳丢失语义不变。"""

    def test_initial_state_is_wait_for_communication(self):
        machine = MissionMachine()
        self.assertEqual(machine.state, MissionState.WAIT_FOR_COMMUNICATION)
        self.assertEqual(machine.result, MissionResult.RUNNING)

    def test_false_frames_within_timeout_do_not_fail(self):
        # 上电时序：连续多帧 communication_ok=False（握手期），不判死。
        machine = MissionMachine(MissionConfig(startup_wait_timeout_s=15.0))
        machine.entered_at = 0.0
        for frame in range(30):  # 30 帧 @ 20Hz = 1.5s，远小于 15s
            state = machine.tick(now=0.05 * frame, communication_ok=False)
            self.assertEqual(state, MissionState.WAIT_FOR_COMMUNICATION)
            self.assertEqual(machine.result, MissionResult.RUNNING)
        self.assertEqual(machine.state, MissionState.WAIT_FOR_COMMUNICATION)

    def test_false_then_true_advances_normally(self):
        # 握手完成（False×N 帧 → True）→ 正常走完整个任务。
        machine = MissionMachine(MissionConfig(orange_target=1, purple_target=0))
        machine.entered_at = 0.0
        for frame in range(10):
            machine.tick(now=0.1 * frame, communication_ok=False)
        self.assertEqual(machine.state, MissionState.WAIT_FOR_COMMUNICATION)
        # 通信就绪 + 机制就绪 → SELF_CHECK → WAIT_FOR_PHYSICAL_START（同一拍）
        machine.tick(now=1.0, communication_ok=True, action_succeeded=True)
        self.assertEqual(machine.state, MissionState.WAIT_FOR_PHYSICAL_START)
        # 物理启动 → 完整任务直到 COMPLETE
        machine.tick(now=1.1, physical_start=True)
        self.assertEqual(machine.state, MissionState.GO_TO_ORANGE)
        for _ in range(6):
            machine.tick(now=1.2, action_succeeded=True)
        self.assertEqual(machine.state, MissionState.COMPLETE)
        self.assertEqual(machine.result, MissionResult.SUCCESS)

    def test_persistent_false_fails_at_timeout_boundary(self):
        # 持续 False：恰好等于超时值仍在等待（边界 `>` 不算超时），超过才 fail。
        machine = MissionMachine(MissionConfig(startup_wait_timeout_s=5.0))
        machine.entered_at = 0.0
        state = machine.tick(now=5.0, communication_ok=False)
        self.assertEqual(state, MissionState.WAIT_FOR_COMMUNICATION)
        state = machine.tick(now=5.1, communication_ok=False)
        self.assertEqual(state, MissionState.FAILED)
        self.assertEqual(machine.result, MissionResult.COMMUNICATION_ERROR)
        self.assertIn("startup window", machine.detail)

    def test_zero_timeout_fails_on_first_false_tick(self):
        # startup_wait_timeout_s=0：不允许等待，首个 False 帧即判死
        # （配置层会把 0 判为非法，这里是机器自身的兜底行为）。
        machine = MissionMachine(MissionConfig(startup_wait_timeout_s=0.0))
        machine.entered_at = 0.0
        machine.tick(now=1.0, communication_ok=False)
        self.assertEqual(machine.state, MissionState.FAILED)
        self.assertEqual(machine.result, MissionResult.COMMUNICATION_ERROR)

    def test_wait_state_skips_general_state_timeout_retry(self):
        # WAIT_FOR_COMMUNICATION 走的是 startup 超时语义（COMMUNICATION_ERROR），
        # 不是通用 state_timeout_s 的 TIMEOUT 重试语义。
        machine = MissionMachine(
            MissionConfig(state_timeout_s=1.0, startup_wait_timeout_s=10.0)
        )
        machine.entered_at = 0.0
        machine.tick(now=5.0, communication_ok=False)
        self.assertEqual(machine.state, MissionState.WAIT_FOR_COMMUNICATION)
        self.assertEqual(machine.retries, 0)
        self.assertEqual(machine.result, MissionResult.RUNNING)

    def test_heartbeat_loss_after_startup_still_fails_immediately(self):
        # 运行时语义不变：离开启动等待后（已进入任务），心跳丢失立即判失败。
        machine = MissionMachine(MissionConfig(orange_target=1, purple_target=0))
        machine.tick(action_succeeded=True)  # -> SELF_CHECK -> WAIT_FOR_PHYSICAL_START
        machine.tick(physical_start=True)    # -> GO_TO_ORANGE
        self.assertEqual(machine.state, MissionState.GO_TO_ORANGE)
        machine.tick(communication_ok=False)
        self.assertEqual(machine.state, MissionState.FAILED)
        self.assertEqual(machine.result, MissionResult.COMMUNICATION_ERROR)
        self.assertIn("heartbeat lost", machine.detail)

    def test_emergency_stop_during_wait_wins(self):
        # 急停优先级最高：启动等待期内急停仍然立即 SAFE_STOP。
        machine = MissionMachine(MissionConfig(startup_wait_timeout_s=15.0))
        machine.entered_at = 0.0
        machine.tick(now=3.0, communication_ok=False, emergency_stop=True)
        self.assertEqual(machine.state, MissionState.SAFE_STOP)
        self.assertEqual(machine.result, MissionResult.SAFETY_STOP)

    def test_default_startup_timeout_is_15s(self):
        self.assertEqual(MissionConfig().startup_wait_timeout_s, 15.0)

    def test_comm_ready_with_mechanism_ok_advances_in_same_tick(self):
        # 通信就绪 + 机制就绪（action_succeeded=True）同一拍走到
        # WAIT_FOR_PHYSICAL_START——与旧 SELF_CHECK 语义一致。
        machine = MissionMachine()
        state = machine.tick(communication_ok=True, action_succeeded=True)
        self.assertEqual(state, MissionState.WAIT_FOR_PHYSICAL_START)

    def test_comm_ready_but_mechanism_busy_stays_in_self_check(self):
        # 通信就绪但机制未就绪：停在 SELF_CHECK（等价旧行为），不误前进。
        machine = MissionMachine()
        state = machine.tick(communication_ok=True, action_succeeded=False)
        self.assertEqual(state, MissionState.SELF_CHECK)


class StartupWaitStructureTests(unittest.TestCase):
    """AST 结构断言：防止未来把启动等待"优化"回上电即判死。"""

    @classmethod
    def setUpClass(cls):
        cls.mission_source = MISSION_PATH.read_text(encoding="utf-8")
        cls.mission_tree = ast.parse(cls.mission_source)
        cls.manager_source = MANAGER_PATH.read_text(encoding="utf-8")
        cls.manager_tree = ast.parse(cls.manager_source)

    def _method_source(self, source, tree, name):
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return ast.get_source_segment(source, node)
        self.fail(f"method {name} not found")

    def test_wait_state_is_before_self_check_in_enum(self):
        # WAIT_FOR_COMMUNICATION 必须是前置状态（枚举中位于 SELF_CHECK 之前）。
        self.assertLess(
            self.mission_source.index("WAIT_FOR_COMMUNICATION"),
            self.mission_source.index("SELF_CHECK"),
        )

    def test_machine_starts_in_wait_for_communication(self):
        # 初始状态必须是 WAIT_FOR_COMMUNICATION（不是旧 SELF_CHECK）。
        self.assertIn(
            "state: MissionState = MissionState.WAIT_FOR_COMMUNICATION",
            self.mission_source,
        )

    def test_tick_has_startup_window_failure(self):
        tick = self._method_source(self.mission_source, self.mission_tree, "tick")
        self.assertIn("startup_wait_timeout_s", tick)
        self.assertIn("communication not ready within startup window", tick)
        # 启动等待分支必须在心跳丢失检查之前（等待期不判死）。
        self.assertLess(
            tick.index("startup_wait_timeout_s"),
            tick.index("hardware heartbeat lost"),
        )

    def test_heartbeat_loss_semantics_preserved(self):
        # 运行时语义不变：心跳丢失仍立即判 COMMUNICATION_ERROR。
        tick = self._method_source(self.mission_source, self.mission_tree, "tick")
        self.assertIn("hardware heartbeat lost", tick)
        self.assertIn("MissionResult.COMMUNICATION_ERROR", tick)

    def test_manager_declares_startup_timeout_param(self):
        # mission_manager 通过 defaults 字典声明参数（循环 declare_parameter），
        # 断言默认值存在且被读入 MissionConfig。
        self.assertIn('"startup_wait_timeout_s": 15.0', self.manager_source)
        self.assertIn(
            "startup_wait_timeout_s=float(self.get_parameter(\"startup_wait_timeout_s\").value)",
            self.manager_source,
        )

    def test_manager_handles_wait_state_before_self_check(self):
        # mission_manager 必须显式处理 WAIT_FOR_COMMUNICATION 分支，
        # 且位于 SELF_CHECK 分支之前。
        self.assertLess(
            self.manager_source.index("WAIT_FOR_COMMUNICATION"),
            self.manager_source.index("MissionState.SELF_CHECK"),
        )


if __name__ == "__main__":
    unittest.main()
