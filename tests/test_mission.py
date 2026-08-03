import unittest

from robogame_core.mission import MissionConfig, MissionMachine, MissionState
from robogame_core.models import MissionResult


class MissionTests(unittest.TestCase):
    def start(self, machine):
        machine.tick(action_succeeded=True)
        machine.tick(physical_start=True)
        self.assertEqual(machine.state, MissionState.GO_TO_ORANGE)

    def test_single_cube_mission(self):
        machine = MissionMachine(MissionConfig(orange_target=1, purple_target=0))
        self.start(machine)
        for _ in range(5):
            machine.tick(action_succeeded=True)
        self.assertEqual(machine.state, MissionState.VERIFY_BUILD)
        machine.tick(action_succeeded=True)
        self.assertEqual(machine.state, MissionState.COMPLETE)
        self.assertEqual(machine.result, MissionResult.SUCCESS)
        self.assertEqual(machine.cargo.total, 0)

    def test_roof_tower_order_and_capacity(self):
        machine = MissionMachine(MissionConfig(orange_target=2, purple_target=1))
        self.start(machine)
        visited = []
        while machine.state not in {MissionState.COMPLETE, MissionState.FAILED}:
            visited.append(machine.state)
            machine.tick(action_succeeded=True)
        self.assertEqual(machine.result, MissionResult.SUCCESS)
        self.assertEqual(visited.count(MissionState.PICK_ORANGE), 2)
        self.assertEqual(visited.count(MissionState.PLACE_ORANGE), 2)
        self.assertLess(visited.index(MissionState.PLACE_ORANGE), visited.index(MissionState.PLACE_PURPLE))
        self.assertEqual(machine.cargo.total, 0)

    def test_emergency_stop_is_terminal(self):
        machine = MissionMachine()
        machine.tick(emergency_stop=True)
        self.assertEqual(machine.state, MissionState.SAFE_STOP)
        self.assertEqual(machine.result, MissionResult.SAFETY_STOP)

    def test_timeout_has_finite_retries(self):
        machine = MissionMachine(MissionConfig(max_retries=2, state_timeout_s=1.0))
        machine.entered_at = 0.0
        machine.tick(now=2.0)
        self.assertEqual(machine.retries, 1)
        machine.tick(now=4.0)
        self.assertEqual(machine.retries, 2)
        machine.tick(now=6.0)
        self.assertEqual(machine.state, MissionState.FAILED)


if __name__ == "__main__":
    unittest.main()
