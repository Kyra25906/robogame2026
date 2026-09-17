"""机械臂 0x20 通道的接线测试：协议编号、接口文件、桥接挂载。

本机（Windows，无 ROS 2）不能 import `robot_bridge.node`，所以对节点这部分沿用
项目既有做法：从源码做结构断言。**纯逻辑**由 `tests/test_arm.py` 覆盖，
固件一致性由 `tests/test_arm_firmware_sync.py` 覆盖，本文件只查“线有没有接上、
策略检查有没有排在发串口之前”。

证据边界：结构断言只能证明源码里存在这些调用与顺序，不证明 ROS 图跑起来、
服务可调用或固件有任何反应。
"""
import ast
import builtins
import pathlib
import re
import unittest

from robogame_core.arm import ArmJoint, ArmJointTarget, encode_arm_set_parameter
from robogame_core.arm import arm_joint_table_with_frozen_ranges
from robogame_core.serial_protocol import (
    MechanismCommand,
    MechanismOperation,
    decode_mechanism_command,
    encode_mechanism_command,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODE_PATH = ROOT / "ros2_ws" / "src" / "robot_bridge" / "robot_bridge" / "node.py"
SRV_PATH = ROOT / "ros2_ws" / "src" / "robogame_interfaces" / "srv" / "SetArmJoint.srv"
CMAKE_PATH = ROOT / "ros2_ws" / "src" / "robogame_interfaces" / "CMakeLists.txt"
INTEGRATION_PATH = ROOT / "tests" / "test_robot_bridge_mechanism_integration.py"


def field_names(block: str) -> list[str]:
    names = []
    for line in block.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        parts = entry.split()
        names.append(parts[1] if len(parts) > 1 else parts[0])
    return names


class ArmSetOpcodeTests(unittest.TestCase):
    def test_arm_set_is_the_next_free_opcode(self):
        # 4 在协议 V1 文档里留给 RETREAT，6 已被 HOME 占用，故取 7。
        self.assertEqual(int(MechanismOperation.ARM_SET), 7)
        self.assertEqual(
            sorted(int(item) for item in MechanismOperation), [1, 2, 3, 5, 6, 7]
        )

    def test_arm_set_travels_inside_the_unchanged_v1_payload(self):
        # parameter 字段本来就存在（i32），机械臂通道不需要改 0x20 的布局。
        table = arm_joint_table_with_frozen_ranges(
            {ArmJoint.BASE: (0.0, 90.0)}, evidence="unit test freeze"
        )
        parameter = encode_arm_set_parameter(
            ArmJointTarget(ArmJoint.BASE, 90.0), table=table
        )
        command = MechanismCommand(11, MechanismOperation.ARM_SET, parameter, 3000)
        encoded = encode_mechanism_command(command)
        self.assertEqual(len(encoded), 12)
        self.assertEqual(decode_mechanism_command(encoded), command)
        self.assertEqual(parameter, 90)


class ArmServiceInterfaceTests(unittest.TestCase):
    def test_service_file_freezes_request_and_response_fields(self):
        request, response = SRV_PATH.read_text(encoding="utf-8").split("---", 1)
        self.assertEqual(field_names(request), ["joint", "angle_deg", "timeout_s"])
        self.assertEqual(
            field_names(response), ["success", "error_code", "duration_s", "detail"]
        )

    def test_service_is_registered_for_rosidl_generation(self):
        self.assertIn('"srv/SetArmJoint.srv"', CMAKE_PATH.read_text(encoding="utf-8"))

    def test_handler_only_touches_declared_srv_fields(self):
        """字段名打错在无 ROS 环境下不会报错，只能在这里拦。"""
        request, response = SRV_PATH.read_text(encoding="utf-8").split("---", 1)
        declared = {
            "request": set(field_names(request)),
            "response": set(field_names(response)),
        }
        source = NODE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        handler = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_arm_set_joint":
                handler = ast.get_source_segment(source, node)
        self.assertIsNotNone(handler, "_arm_set_joint not found")
        for role in ("request", "response"):
            used = set(re.findall(rf"\b{role}\.(\w+)", handler))
            self.assertTrue(used, f"handler never touches {role}")
            self.assertLessEqual(
                used,
                declared[role],
                f"{role} fields used but not declared in SetArmJoint.srv: "
                f"{sorted(used - declared[role])}",
            )

    def test_pty_harness_fills_only_real_request_fields(self):
        # PTY 用例只在 POSIX + rclpy 环境执行；本机跑不到，所以静态检查请求字段名。
        harness = INTEGRATION_PATH.read_text(encoding="utf-8")
        used = set(re.findall(r"\brequest\.(\w+)", harness))
        self.assertLessEqual(
            used, {"command", "height_m", "joint", "angle_deg", "timeout_s"}
        )
        self.assertIn('request.joint = joint', harness)
        self.assertIn("request.angle_deg = angle_deg", harness)


class ArmBridgeWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = NODE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _method_source(self, name):
        for node in ast.walk(self.tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return ast.get_source_segment(self.source, node)
        self.fail(f"method {name} not found")

    def test_service_is_bound_to_the_arm_handler(self):
        self.assertIn(
            'SetArmJoint, "/arm/set_joint", self._arm_set_joint', self.source
        )
        # 与其他机构服务共用可重入回调组，否则并发调用会互相堵住。
        self.assertIn("callback_group=self._mechanism_callback_group", self.source)

    def test_arm_parameters_are_declared(self):
        self.assertIn('self.declare_parameter("mock_arm_success", True)', self.source)
        self.assertIn('self.declare_parameter("arm_joint_ranges", "")', self.source)
        self.assertIn(
            'self.declare_parameter("arm_joint_ranges_evidence", "")', self.source
        )

    def test_empty_configuration_keeps_every_joint_unfrozen(self):
        table_method = self._method_source("_build_arm_joint_table")
        self.assertIn('self.get_parameter("arm_joint_ranges")', table_method)
        self.assertIn('self.get_parameter("arm_joint_ranges_evidence")', table_method)
        self.assertIn("default_arm_joint_table()", table_method)
        self.assertIn("arm_joint_table_with_frozen_ranges(", table_method)
        self.assertIn("parse_arm_joint_ranges(text)", table_method)
        # 冻结值域必须带出处，否则启动就报错，不允许“默默用一组猜的角度”。
        self.assertIn("arm_joint_ranges_evidence must be set", table_method)

    def test_table_state_is_logged_at_startup(self):
        self.assertIn("self.arm_joint_table.describe()", self.source)
        self.assertIn("arm joint table:", self.source)

    def test_policy_check_happens_before_anything_touches_the_serial_line(self):
        handler = self._method_source("_arm_set_joint")
        mock_index = handler.index("if self.mock_mode:")
        encode_index = handler.index("encode_arm_set_parameter(")
        send_index = handler.index("_execute_real_mechanism(")
        # mock 分支必须先返回：mock 绝不编码、更不写串口。
        self.assertLess(mock_index, encode_index)
        # 编码（含策略校验）必须排在真正发串口之前。
        self.assertLess(encode_index, send_index)

    def test_real_path_reuses_one_shared_joint_table(self):
        handler = self._method_source("_arm_set_joint")
        self.assertGreaterEqual(handler.count("self.arm_joint_table"), 2)
        self.assertIn(
            "encode_arm_set_parameter(target, table=self.arm_joint_table)", handler
        )

    def test_mock_path_delegates_to_the_shared_mock_arm_model(self):
        handler = self._method_source("_arm_set_joint")
        self.assertIn("execute_mock_arm_set(", handler)
        self.assertIn("self.mock_arm_state", handler)
        self.assertIn('self.get_parameter("mock_arm_success")', handler)

    def test_rejections_use_the_shared_reason_to_code_mapping(self):
        handler = self._method_source("_arm_set_joint")
        self.assertIn("arm_rejection_error_code(exc)", handler)
        self.assertIn("ArmJointTarget(request.joint, float(request.angle_deg))", handler)
        # 本地拒绝走同一个填响应函数，避免各处字段漏填。
        self.assertGreaterEqual(self.source.count("self._reject_arm("), 2)

    def test_real_path_sends_the_firmware_operation_number(self):
        handler = self._method_source("_arm_set_joint")
        self.assertIn("ProtocolOperation.ARM_SET", handler)
        self.assertIn("parameter=parameter", handler)

    def test_mock_arm_state_is_created_for_mock_mode(self):
        self.assertIn("self.mock_arm_state = MockArmState()", self.source)

    def test_no_uppercase_constant_is_used_without_being_defined(self):
        """漏一个 import 在本机不会报错，只会在树莓派上变成 NameError。这里静态拦住。

        做法：收集全文所有“被赋值/被导入/函数或类名或参数”的绑定名，再检查所有
        全大写（常量风格）的读取名都在其中。属性写法 `self.ARM_X` 不算 Name，不受影响。
        """
        tree = ast.parse(self.source)
        bound = set(dir(builtins))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, (ast.arg, ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                bound.add(node.name if not isinstance(node, ast.arg) else node.arg)
            elif isinstance(node, ast.alias):
                bound.add(node.asname or node.name.split(".")[0])
            elif isinstance(node, ast.Global):
                bound.update(node.names)
        used = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and re.fullmatch(r"[A-Z][A-Z0-9_]*", node.id)
        }
        self.assertEqual(
            sorted(used - bound),
            [],
            "these uppercase names are read but never defined or imported in node.py",
        )


if __name__ == "__main__":
    unittest.main()
