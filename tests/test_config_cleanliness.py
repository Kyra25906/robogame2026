"""A5（P2-5）验证：配置无重复键、无重复导入。

背景（执行队列 2026-08-18 A5）：`robot.yaml:11-12` 有重复的
`max_mcu_sample_gap_ms` 键；`robot_bridge/node.py` 曾疑似重复导入
`validate_mcu_tick`（当前代码核实仅一处导入）。

本测试：
1. 扫描 robogame_bringup/config 下所有 yaml，断言「同一节点段内」没有重复键
   （注意：不同节点段各有自己的 status_stale_s 等，是合法同名参数，不算重复）；
2. 断言 `validate_mcu_tick` 在 robot_bridge 源码中仅导入一次。
"""
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "ros2_ws/src/robogame_bringup/config"
BRIDGE_PATH = PROJECT_ROOT / "ros2_ws/src/robot_bridge/robot_bridge/node.py"


def duplicate_keys_per_node(yaml_text: str) -> list[tuple[str, str]]:
    """返回 [(节点段, 重复键名)]。按节点段分组，避免跨节点同名误报。"""
    duplicates: list[tuple[str, str]] = []
    current_node = None
    seen: dict[str, dict[str, int]] = {}
    for line in yaml_text.splitlines():
        node_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*$", line)
        if node_match:
            current_node = node_match.group(1)
            seen.setdefault(current_node, {})
            continue
        if current_node is None:
            continue
        key_match = re.match(r"^\s{2,}([A-Za-z_][A-Za-z0-9_]*):", line)
        if key_match:
            key = key_match.group(1)
            count = seen[current_node].get(key, 0)
            seen[current_node][key] = count + 1
    for node, keys in seen.items():
        for key, count in keys.items():
            if count > 1:
                duplicates.append((node, key))
    return sorted(duplicates)


class DuplicateConfigKeyTests(unittest.TestCase):
    def test_robot_yaml_has_no_duplicate_keys(self):
        text = (CONFIG_DIR / "robot.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            duplicate_keys_per_node(text), [],
            f"robot.yaml has duplicate keys: {duplicate_keys_per_node(text)}",
        )

    def test_all_bringup_configs_have_no_duplicate_keys(self):
        for path in sorted(CONFIG_DIR.glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            duplicates = duplicate_keys_per_node(text)
            self.assertEqual(
                duplicates, [], f"{path.name} has duplicate keys: {duplicates}"
            )


class DuplicateImportTests(unittest.TestCase):
    def test_validate_mcu_tick_imported_exactly_once(self):
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        imports = [
            line.strip()
            for line in source.splitlines()
            if "validate_mcu_tick" in line and line.strip().startswith("from")
        ]
        self.assertEqual(
            len(imports), 1,
            f"validate_mcu_tick should be imported once, found: {imports}",
        )
        self.assertEqual(imports, ["from robogame_core.mcu_time import validate_mcu_tick"])


if __name__ == "__main__":
    unittest.main()
