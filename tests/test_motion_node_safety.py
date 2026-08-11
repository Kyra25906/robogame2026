import ast
import unittest
from pathlib import Path


class MotionNodeSafetyStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = (
            Path(__file__).resolve().parents[1]
            / "ros2_ws" / "src" / "motion_control" / "motion_control" / "node.py"
        )
        cls.tree = ast.parse(path.read_text(encoding="utf-8"))

    def _function(self, name):
        return next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def test_status_failure_forwards_mechanism_fault(self):
        function = self._function("_status_failure")
        call = next(
            node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "control_safety_result"
        )
        self.assertIn("mechanism_fault", {keyword.arg for keyword in call.keywords})

    def test_every_finish_path_stops_before_clearing_goal(self):
        function = self._function("_finish")
        calls_stop = [
            index for index, statement in enumerate(function.body)
            if isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr == "_stop"
        ]
        clears_goal = [
            index for index, statement in enumerate(function.body)
            if isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Attribute) and target.attr == "goal"
                for target in statement.targets
            )
        ]
        self.assertTrue(calls_stop and clears_goal)
        self.assertLess(calls_stop[0], clears_goal[0])


if __name__ == "__main__":
    unittest.main()
