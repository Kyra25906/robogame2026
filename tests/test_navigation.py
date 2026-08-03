import math
import unittest

from robogame_core.models import Pose2D, Velocity2D
from robogame_core.navigation import GoToPoseController, OdometryIntegrator, normalize_angle


class NavigationTests(unittest.TestCase):
    def test_angle_normalization(self):
        self.assertAlmostEqual(normalize_angle(3.0 * math.pi), math.pi)

    def test_command_is_in_robot_frame(self):
        controller = GoToPoseController()
        command = controller.command(Pose2D(0.0, 0.0, math.pi / 2.0), Pose2D(1.0, 0.0, math.pi / 2.0))
        self.assertAlmostEqual(command.vx, 0.0, places=6)
        self.assertLess(command.vy, 0.0)

    def test_at_goal(self):
        controller = GoToPoseController()
        self.assertTrue(controller.at_goal(Pose2D(1.0, 2.0, 0.0), Pose2D(1.02, 2.01, 0.02)))

    def test_odometry_integrates_forward(self):
        integrator = OdometryIntegrator()
        pose = integrator.update(Velocity2D(1.0, 0.0, 0.0), 0.1)
        self.assertAlmostEqual(pose.x, 0.1)
        self.assertAlmostEqual(pose.y, 0.0)


if __name__ == "__main__":
    unittest.main()

