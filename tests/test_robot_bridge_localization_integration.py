import os
import math
import time
import unittest


class RobotBridgeLocalizationIntegrationTests(unittest.TestCase):
    """Exercise V1 serial ODOM through robot_bridge and localization."""

    @classmethod
    def setUpClass(cls):
        cls._skip_reason = None
        if os.name != "posix":
            cls._skip_reason = "PTY integration requires a POSIX ROS 2 environment"
            return
        try:
            import pty  # noqa: F401
            import rclpy  # noqa: F401
            from localization.node import LocalizationNode  # noqa: F401
            from robot_bridge.node import RobotBridge  # noqa: F401
        except (ImportError, ModuleNotFoundError) as exc:
            cls._skip_reason = f"ROS 2 integration dependencies unavailable: {exc}"

    def setUp(self):
        if self._skip_reason:
            self.skipTest(self._skip_reason)

        import pty
        import rclpy
        from localization.node import LocalizationNode
        from nav_msgs.msg import Odometry
        from rclpy.executors import SingleThreadedExecutor
        from rcl_interfaces.msg import Log
        from robot_bridge.node import RobotBridge
        from robogame_core.serial_protocol import StreamDecoder

        self.master_fd, self.slave_fd = pty.openpty()
        os.set_blocking(self.master_fd, False)
        rclpy.init(args=[
            "--ros-args",
            "-p", "mock_mode:=false",
            "-p", f"serial_port:={os.ttyname(self.slave_fd)}",
        ])
        self.bridge = RobotBridge()
        self.localization = LocalizationNode()
        self.observer = rclpy.create_node("localization_integration_observer")
        self.pose_messages = []
        self.log_messages = []
        self.observer.create_subscription(
            Odometry, "/pose", self.pose_messages.append, 50
        )
        self.observer.create_subscription(
            Log, "/rosout", self.log_messages.append, 50
        )
        self.executor = SingleThreadedExecutor()
        for node in (self.bridge, self.localization, self.observer):
            self.executor.add_node(node)
        self.decoder = StreamDecoder()
        self.tx_sequence = 1000
        self.mcu_tick_ms = 100
        self.imu_tick_ms = 100

    def tearDown(self):
        if self._skip_reason:
            return
        for node in (self.bridge, self.localization, self.observer):
            self.executor.remove_node(node)
        self.executor.shutdown()
        self.bridge.shutdown_transport()
        self.bridge.destroy_node()
        self.localization.destroy_node()
        self.observer.destroy_node()
        import rclpy
        if rclpy.ok():
            rclpy.shutdown()
        os.close(self.master_fd)
        os.close(self.slave_fd)

    def _spin_until(self, predicate, timeout_s=2.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.01)
            if predicate():
                return True
        return False

    def _read_frames(self):
        try:
            data = os.read(self.master_fd, 4096)
        except BlockingIOError:
            data = b""
        return self.decoder.feed(data)

    def _write_frame(self, message_type, payload):
        from robogame_core.serial_protocol import encode_frame
        os.write(self.master_fd, encode_frame(message_type, self.tx_sequence, payload))
        self.tx_sequence += 1

    def _status_payload(self, boot_id, *, imu_valid=False):
        from robogame_core.serial_protocol import (
            STATUS_IMU_VALID,
            STATUS_PHYSICAL_START,
            StatusSample,
            encode_status,
        )
        flags = STATUS_PHYSICAL_START
        if imu_valid:
            flags |= STATUS_IMU_VALID
        return encode_status(StatusSample(100, flags, 0, 24000, boot_id))

    def _complete_handshake(self, boot_id=1, *, imu_valid=False):
        from robogame_core.serial_protocol import (
            MSG_TYPE_ACK,
            MSG_TYPE_HELLO,
            MSG_TYPE_STATUS,
            encode_ack,
        )
        hello = None

        def got_hello():
            nonlocal hello
            for frame in self._read_frames():
                if frame.message_type == MSG_TYPE_HELLO:
                    hello = frame
                    return True
            return False

        self.assertTrue(self._spin_until(got_hello), "bridge did not send HELLO")
        self._write_frame(MSG_TYPE_ACK, encode_ack(hello.sequence))
        self._write_frame(
            MSG_TYPE_STATUS,
            self._status_payload(boot_id, imu_valid=imu_valid),
        )
        self.assertTrue(self._spin_until(
            lambda: self.bridge._handshake_state == "READY"
            and self.bridge._communication_ok
        ))

    def _send_odom(self, vx, vy=0.0, wz=0.0):
        from robogame_core.serial_protocol import MSG_TYPE_ODOM, OdomSample, encode_odom
        self._write_frame(
            MSG_TYPE_ODOM,
            encode_odom(OdomSample(self.mcu_tick_ms, vx, vy, wz)),
        )
        self.mcu_tick_ms = (self.mcu_tick_ms + 20) & 0xFFFFFFFF

    def _send_imu(self, gyro_z_radps, *, valid=True):
        from robogame_core.serial_protocol import MSG_TYPE_IMU, ImuSample, encode_imu
        self._write_frame(
            MSG_TYPE_IMU,
            encode_imu(ImuSample(self.imu_tick_ms, gyro_z_radps, valid)),
        )
        self.imu_tick_ms = (self.imu_tick_ms + 10) & 0xFFFFFFFF

    def _keep_status_alive(self, duration_s, boot_id=1):
        from robogame_core.serial_protocol import MSG_TYPE_STATUS
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            self._write_frame(MSG_TYPE_STATUS, self._status_payload(boot_id))
            self.executor.spin_once(timeout_sec=0.04)

    def test_forward_and_reverse_odom_reach_pose(self):
        self._complete_handshake()
        self._send_odom(0.20)
        self.assertTrue(self._spin_until(lambda: any(
            msg.pose.pose.position.x > 0.02
            and abs(msg.twist.twist.linear.x - 0.20) < 1e-5
            for msg in self.pose_messages
        )), "forward wheel odometry did not reach /pose")

        forward_x = self.pose_messages[-1].pose.pose.position.x
        self._send_odom(-0.20)
        self.assertTrue(self._spin_until(lambda: any(
            msg.pose.pose.position.x < forward_x - 0.01
            and abs(msg.twist.twist.linear.x + 0.20) < 1e-5
            for msg in self.pose_messages
        )), "reverse wheel odometry did not reach /pose")

    def test_stale_odom_stops_pose_without_losing_status(self):
        self._complete_handshake()
        self._send_odom(0.25)
        self.assertTrue(self._spin_until(lambda: any(
            msg.pose.pose.position.x > 0.02 for msg in self.pose_messages
        )))

        self._keep_status_alive(0.45)
        self.assertTrue(self.bridge._communication_ok)
        self.assertEqual(self.pose_messages[-1].twist.twist.linear.x, 0.0)
        stopped_x = self.pose_messages[-1].pose.pose.position.x
        self._keep_status_alive(0.15)
        self.assertAlmostEqual(
            self.pose_messages[-1].pose.pose.position.x, stopped_x, places=4
        )

    def test_boot_id_change_allows_clean_pose_reset_and_new_session(self):
        from robogame_core.serial_protocol import MSG_TYPE_STATUS

        self._complete_handshake(boot_id=1)
        self._send_odom(1.0)
        self.assertTrue(self._spin_until(lambda: any(
            msg.pose.pose.position.x > 0.06 for msg in self.pose_messages
        )))

        messages_before_reset = len(self.pose_messages)
        self._write_frame(MSG_TYPE_STATUS, self._status_payload(2))
        self.assertTrue(self._spin_until(
            lambda: len(self.pose_messages) > messages_before_reset
            and abs(self.pose_messages[-1].pose.pose.position.x) < 0.001
            and self.localization._last_boot_id == 2
            and self.localization.prev_pose_x is not None
        ), "localization did not publish the MCU-reset pose")
        self.assertEqual(self.localization._last_boot_id, 2)
        self.assertAlmostEqual(self.localization.prev_pose_x, 0.0, places=3)

        self._complete_handshake(boot_id=2)
        self._send_odom(-0.20)
        self.assertTrue(self._spin_until(lambda: any(
            msg.twist.twist.linear.x < -0.19 for msg in self.pose_messages
        )), "new ODOM session did not reach localization")

    def test_valid_imu_yaw_rate_overrides_wheel_yaw_in_both_directions(self):
        self._complete_handshake(imu_valid=True)
        self._send_imu(0.60)
        self._send_odom(0.0, wz=0.20)
        self.assertTrue(self._spin_until(lambda: any(
            abs(msg.twist.twist.angular.z - 0.60) < 1e-5
            for msg in self.pose_messages
        )), "positive IMU yaw rate did not reach /pose")

        self._send_imu(-0.55)
        self._send_odom(0.0, wz=-0.15)
        self.assertTrue(self._spin_until(lambda: any(
            abs(msg.twist.twist.angular.z + 0.55) < 1e-5
            for msg in self.pose_messages
        )), "negative IMU yaw rate did not reach /pose")

    def test_status_invalid_imu_falls_back_to_wheel_yaw_rate(self):
        self._complete_handshake(imu_valid=False)
        self._send_imu(0.80)
        self._send_odom(0.0, wz=0.25)

        self.assertTrue(self._spin_until(lambda: any(
            abs(msg.twist.twist.angular.z - 0.25) < 1e-5
            for msg in self.pose_messages
        )), "invalid IMU did not fall back to wheel yaw rate")

    def test_stale_imu_falls_back_to_fresh_wheel_yaw_rate(self):
        from robogame_core.serial_protocol import MSG_TYPE_STATUS

        self._complete_handshake(imu_valid=True)
        self._send_imu(0.70)
        self._send_odom(0.0, wz=0.30)
        self.assertTrue(self._spin_until(lambda: any(
            abs(msg.twist.twist.angular.z - 0.70) < 1e-5
            for msg in self.pose_messages
        )))

        # robot_bridge declares a cached MCU IMU sample unavailable after 0.30 s.
        deadline = time.monotonic() + 0.38
        while time.monotonic() < deadline:
            self._write_frame(
                MSG_TYPE_STATUS,
                self._status_payload(1, imu_valid=True),
            )
            self._send_odom(0.0, wz=0.30)
            self.executor.spin_once(timeout_sec=0.04)

        self.assertTrue(self._spin_until(lambda: any(
            abs(msg.twist.twist.angular.z - 0.30) < 1e-5
            for msg in self.pose_messages[-10:]
        )), "stale IMU did not fall back to wheel yaw rate")

    def test_large_yaw_disagreement_is_reported_to_rosout(self):
        self._complete_handshake(imu_valid=True)
        self._send_imu(0.90)
        self._send_odom(0.0, wz=0.10)

        self.assertTrue(self._spin_until(lambda: any(
            msg.name == "localization" and "Yaw rate divergence" in msg.msg
            for msg in self.log_messages
        )), "wheel/IMU yaw disagreement was not reported to /rosout")
        self.assertTrue(self.pose_messages, "divergence produced no /pose output")
        pose = self.pose_messages[-1]
        self.assertTrue(all(math.isfinite(value) for value in (
            pose.pose.pose.position.x,
            pose.pose.pose.position.y,
            pose.pose.pose.orientation.z,
            pose.pose.pose.orientation.w,
            pose.twist.twist.angular.z,
        )), "yaw disagreement produced a non-finite /pose")


if __name__ == "__main__":
    unittest.main()
