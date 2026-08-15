import os
import time
import unittest


class RobotBridgeImuIntegrationTests(unittest.TestCase):
    """Exercise MCU V1 IMU -> robot_bridge -> /imu/data over a PTY."""

    @classmethod
    def setUpClass(cls):
        cls._skip_reason = None
        if os.name != "posix":
            cls._skip_reason = "PTY integration requires a POSIX ROS 2 environment"
            return
        try:
            import pty  # noqa: F401
            import rclpy  # noqa: F401
            from robot_bridge.node import RobotBridge  # noqa: F401
        except (ImportError, ModuleNotFoundError) as exc:
            cls._skip_reason = f"ROS 2 integration dependencies unavailable: {exc}"

    def setUp(self):
        if self._skip_reason:
            self.skipTest(self._skip_reason)

        import pty
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from robot_bridge.node import RobotBridge
        from sensor_msgs.msg import Imu

        self.master_fd, self.slave_fd = pty.openpty()
        os.set_blocking(self.master_fd, False)
        rclpy.init(args=[
            "--ros-args",
            "-p", "mock_mode:=false",
            "-p", f"serial_port:={os.ttyname(self.slave_fd)}",
        ])
        self.bridge = RobotBridge()
        self.observer = rclpy.create_node("imu_integration_observer")
        self.imu_messages = []
        self.observer.create_subscription(Imu, "/imu/data", self.imu_messages.append, 50)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.bridge)
        self.executor.add_node(self.observer)
        from robogame_core.serial_protocol import StreamDecoder
        self.decoder = StreamDecoder()
        self.tx_sequence = 1000

    def tearDown(self):
        if self._skip_reason:
            return
        self.executor.remove_node(self.bridge)
        self.executor.remove_node(self.observer)
        self.executor.shutdown()
        self.bridge.shutdown_transport()
        self.bridge.destroy_node()
        self.observer.destroy_node()
        import rclpy
        if rclpy.ok():
            rclpy.shutdown()
        os.close(self.master_fd)
        os.close(self.slave_fd)

    def _spin_until(self, predicate, timeout_s=2.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.02)
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

    def _status_payload(self):
        from robogame_core.serial_protocol import (
            STATUS_IMU_VALID,
            STATUS_PHYSICAL_START,
            StatusSample,
            encode_status,
        )
        return encode_status(StatusSample(
            100, STATUS_PHYSICAL_START | STATUS_IMU_VALID, 0, 24000, 1
        ))

    def _complete_handshake(self):
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
        self._write_frame(MSG_TYPE_STATUS, self._status_payload())
        self.assertTrue(self._spin_until(
            lambda: self.bridge._handshake_state == "READY"
            and self.bridge._communication_ok
        ))

    def _send_imu(self, gyro_z_radps, valid):
        from robogame_core.serial_protocol import MSG_TYPE_IMU, ImuSample, encode_imu
        self._write_frame(
            MSG_TYPE_IMU,
            encode_imu(ImuSample(100, gyro_z_radps, valid)),
        )

    def test_valid_v1_imu_is_published_with_angular_velocity(self):
        self._complete_handshake()
        self._send_imu(0.75, True)

        self.assertTrue(self._spin_until(lambda: any(
            abs(msg.angular_velocity.z - 0.75) < 1e-5
            and msg.angular_velocity_covariance[0] > 0.0
            and msg.angular_velocity_covariance[4] > 0.0
            and msg.angular_velocity_covariance[8] > 0.0
            for msg in self.imu_messages
        )), "decoded IMU angular velocity was not published")

    def test_invalid_imu_is_explicitly_marked_unavailable(self):
        self._complete_handshake()
        messages_before = len(self.imu_messages)
        self._send_imu(1.25, False)

        self.assertTrue(self._spin_until(lambda: any(
            msg.angular_velocity.z == 0.0
            and msg.angular_velocity_covariance[0] == -1.0
            for msg in self.imu_messages[messages_before:]
        )), "invalid IMU was not marked unavailable")

    def test_stale_imu_is_unavailable_while_status_stays_healthy(self):
        from robogame_core.serial_protocol import MSG_TYPE_STATUS

        self._complete_handshake()
        self._send_imu(-0.60, True)
        self.assertTrue(self._spin_until(lambda: any(
            abs(msg.angular_velocity.z + 0.60) < 1e-5
            for msg in self.imu_messages
        )))

        deadline = time.monotonic() + 0.45
        while time.monotonic() < deadline:
            self._write_frame(MSG_TYPE_STATUS, self._status_payload())
            self.executor.spin_once(timeout_sec=0.05)

        self.assertTrue(self.bridge._communication_ok)
        self.assertEqual(self.imu_messages[-1].angular_velocity.z, 0.0)
        self.assertEqual(
            self.imu_messages[-1].angular_velocity_covariance[0], -1.0
        )


if __name__ == "__main__":
    unittest.main()
