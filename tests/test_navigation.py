import math
import unittest

from robogame_core.models import Pose2D, Velocity2D
from robogame_core.navigation import (
    ControllerConfig,
    GoToPoseController,
    OdometryIntegrator,
    limit_velocity_rate,
    normalize_angle,
    move_toward,
    pose_is_finite,
)


class NavigationTests(unittest.TestCase):
    def test_angle_normalization(self):
        self.assertAlmostEqual(normalize_angle(3.0 * math.pi), math.pi)

    def test_rotation_uses_short_path_across_pi_boundary(self):
        controller = GoToPoseController()
        _, _, yaw_error = controller.errors(
            Pose2D(0.0, 0.0, math.radians(179.0)),
            Pose2D(0.0, 0.0, math.radians(-179.0)),
        )
        self.assertAlmostEqual(yaw_error, math.radians(2.0), places=6)

    def test_command_is_in_robot_frame(self):
        controller = GoToPoseController()
        command = controller.command(Pose2D(0.0, 0.0, math.pi / 2.0), Pose2D(1.0, 0.0, math.pi / 2.0))
        self.assertAlmostEqual(command.vx, 0.0, places=6)
        self.assertLess(command.vy, 0.0)

    def test_at_goal(self):
        controller = GoToPoseController()
        self.assertTrue(controller.at_goal(Pose2D(1.0, 2.0, 0.0), Pose2D(1.02, 2.01, 0.02)))

    def test_at_goal_always_commands_zero_velocity(self):
        controller = GoToPoseController()
        command = controller.command(
            Pose2D(1.0, 2.0, 0.0), Pose2D(1.02, 2.01, 0.02)
        )
        self.assertEqual(command, Velocity2D(0.0, 0.0, 0.0))

    def test_command_never_exceeds_axis_limits(self):
        config = ControllerConfig(max_vx=0.3, max_vy=0.2, max_wz=0.4)
        command = GoToPoseController(config).command(
            Pose2D(-10.0, 20.0, math.pi), Pose2D(10.0, -20.0, 0.0)
        )
        self.assertLessEqual(abs(command.vx), config.max_vx)
        self.assertLessEqual(abs(command.vy), config.max_vy)
        self.assertLessEqual(abs(command.wz), config.max_wz)

    def test_odometry_integrates_forward(self):
        integrator = OdometryIntegrator()
        pose = integrator.update(Velocity2D(1.0, 0.0, 0.0), 0.1)
        self.assertAlmostEqual(pose.x, 0.1)
        self.assertAlmostEqual(pose.y, 0.0)

    def test_slow_radius_reduces_speed_near_goal(self):
        controller = GoToPoseController(ControllerConfig(
            kx=10.0,
            max_vx=1.0,
            position_tolerance=0.01,
            slow_radius=1.0,
        ))
        command = controller.command(Pose2D(0.0, 0.0, 0.0), Pose2D(0.1, 0.0, 0.0))
        self.assertAlmostEqual(command.vx, 0.2)

    def test_velocity_rate_limit_is_axis_specific(self):
        command = limit_velocity_rate(
            Velocity2D(0.0, 0.0, 0.0),
            Velocity2D(1.0, -1.0, 3.0),
            dt=0.1,
            max_ax=0.5,
            max_ay=0.4,
            max_awz=1.0,
        )
        self.assertAlmostEqual(command.vx, 0.05)
        self.assertAlmostEqual(command.vy, -0.04)
        self.assertAlmostEqual(command.wz, 0.1)

    def test_velocity_rate_limit_does_not_move_when_dt_is_zero(self):
        previous = Velocity2D(0.1, -0.2, 0.3)
        command = limit_velocity_rate(
            previous, Velocity2D(1.0, 1.0, 1.0),
            dt=0.0, max_ax=1.0, max_ay=1.0, max_awz=1.0,
        )
        self.assertEqual(command, previous)

    def test_velocity_rate_limit_handles_direction_reversal_without_jump(self):
        command = limit_velocity_rate(
            Velocity2D(0.4, -0.3, 0.2),
            Velocity2D(-0.4, 0.3, -0.2),
            dt=0.1, max_ax=0.5, max_ay=0.4, max_awz=1.0,
        )
        self.assertAlmostEqual(command.vx, 0.35)
        self.assertAlmostEqual(command.vy, -0.26)
        self.assertAlmostEqual(command.wz, 0.1)

    def test_move_toward_never_overshoots_target(self):
        self.assertEqual(move_toward(0.0, 0.1, 1.0), 0.1)
        self.assertEqual(move_toward(0.0, -0.1, 1.0), -0.1)
        with self.assertRaises(ValueError):
            move_toward(0.0, 1.0, -0.1)

    def test_velocity_rate_limits_must_be_positive(self):
        with self.assertRaises(ValueError):
            limit_velocity_rate(
                Velocity2D(0.0, 0.0, 0.0),
                Velocity2D(1.0, 0.0, 0.0),
                dt=0.1, max_ax=0.0, max_ay=1.0, max_awz=1.0,
            )

    def test_pose_finite_check_rejects_nan_and_infinity(self):
        self.assertTrue(pose_is_finite(Pose2D(1.0, 2.0, 0.5)))
        self.assertFalse(pose_is_finite(Pose2D(math.nan, 2.0, 0.5)))
        self.assertFalse(pose_is_finite(Pose2D(1.0, math.inf, 0.5)))
        self.assertFalse(pose_is_finite(Pose2D(1.0, 2.0, -math.inf)))

    def test_odometry_ignores_unsafe_time_steps(self):
        integrator = OdometryIntegrator(Pose2D(1.0, 2.0, 0.3))
        velocity = Velocity2D(1.0, 1.0, 1.0)
        for dt in (-0.1, 0.0, 0.5001):
            with self.subTest(dt=dt):
                self.assertEqual(integrator.update(velocity, dt), Pose2D(1.0, 2.0, 0.3))

    def test_controller_rejects_invalid_configuration(self):
        with self.assertRaises(ValueError):
            ControllerConfig(slow_radius=0.0)
        with self.assertRaises(ValueError):
            ControllerConfig(max_vx=math.nan)
        with self.assertRaises(ValueError):
            ControllerConfig(kx=-0.1)


if __name__ == "__main__":
    unittest.main()
