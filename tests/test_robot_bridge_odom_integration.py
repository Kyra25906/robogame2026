import os
import time
import unittest


class RobotBridgeOdomIntegrationTests(unittest.TestCase):
    """Exercise MCU V1 ODOM -> robot_bridge -> /wheel_odom over a PTY."""

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
        from nav_msgs.msg import Odometry
        from rclpy.executors import SingleThreadedExecutor
        from robot_bridge.node import RobotBridge

        self.master_fd, self.slave_fd = pty.openpty()
        os.set_blocking(self.master_fd, False)
        rclpy.init(args=[
            "--ros-args",
            "-p", "mock_mode:=false",
            "-p", f"serial_port:={os.ttyname(self.slave_fd)}",
        ])
        self.bridge = RobotBridge()
        self.observer = rclpy.create_node("odom_integration_observer")
        self.odom_messages = []
        self.observer.create_subscription(
            Odometry, "/wheel_odom", self.odom_messages.append, 50
        )
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.bridge)
        self.executor.add_node(self.observer)
        self.decoder = self._new_decoder()
        self.tx_sequence = 1000
        self.mcu_tick_ms = 100

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

    @staticmethod
    def _new_decoder():
        from robogame_core.serial_protocol import StreamDecoder
        return StreamDecoder()

    def _read_frames(self):
        try:
            data = os.read(self.master_fd, 4096)
        except BlockingIOError:
            data = b""
        return self.decoder.feed(data)

    def _spin_until(self, predicate, timeout_s=2.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.02)
            if predicate():
                return True
        return False

    def _write_frame(self, message_type, payload):
        from robogame_core.serial_protocol import encode_frame
        os.write(
            self.master_fd,
            encode_frame(message_type, self.tx_sequence, payload),
        )
        self.tx_sequence += 1

    def _status_payload(self, boot_id):
        from robogame_core.serial_protocol import (
            STATUS_PHYSICAL_START,
            StatusSample,
            encode_status,
        )
        return encode_status(StatusSample(100, STATUS_PHYSICAL_START, 0, 24000, boot_id))

    def _complete_handshake(self, boot_id=1):
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
        self._write_frame(MSG_TYPE_STATUS, self._status_payload(boot_id))
        self.assertTrue(self._spin_until(
            lambda: self.bridge._handshake_state == "READY"
            and self.bridge._communication_ok
        ))

    def _send_odom(self, vx, vy, wz, *, mcu_tick_ms=None):
        from robogame_core.serial_protocol import MSG_TYPE_ODOM, OdomSample, encode_odom
        tick = self.mcu_tick_ms if mcu_tick_ms is None else mcu_tick_ms
        self._write_frame(MSG_TYPE_ODOM, encode_odom(OdomSample(tick, vx, vy, wz)))
        if mcu_tick_ms is None:
            self.mcu_tick_ms = (self.mcu_tick_ms + 20) & 0xFFFFFFFF

    @staticmethod
    def _velocity_matches(msg, vx, vy, wz, places=5):
        values = (
            msg.twist.twist.linear.x,
            msg.twist.twist.linear.y,
            msg.twist.twist.angular.z,
        )
        expected = (vx, vy, wz)
        return all(round(a - b, places) == 0 for a, b in zip(values, expected))

    def test_valid_v1_odom_is_published_on_wheel_odom(self):
        self._complete_handshake()
        self._send_odom(0.25, -0.10, 0.50)

        self.assertTrue(self._spin_until(lambda: any(
            self._velocity_matches(msg, 0.25, -0.10, 0.50)
            and msg.pose.covariance[0] > 0.0
            and msg.pose.covariance[35] > 0.0
            and msg.pose.covariance[14] == self.bridge.covariance["unavailable_variance"]
            and msg.twist.covariance[0] > 0.0
            and msg.twist.covariance[35] > 0.0
            and msg.twist.covariance[21] == self.bridge.covariance["unavailable_variance"]
            for msg in self.odom_messages
        )), "decoded ODOM velocity was not published")

    def test_stale_odom_zeros_velocity_while_status_stays_healthy(self):
        from robogame_core.serial_protocol import MSG_TYPE_STATUS

        self._complete_handshake()
        self._send_odom(0.30, 0.0, 0.0)
        self.assertTrue(self._spin_until(lambda: any(
            self._velocity_matches(msg, 0.30, 0.0, 0.0)
            for msg in self.odom_messages
        )))

        deadline = time.monotonic() + 0.45
        while time.monotonic() < deadline:
            self._write_frame(MSG_TYPE_STATUS, self._status_payload(1))
            self.executor.spin_once(timeout_sec=0.05)

        self.assertTrue(self.bridge._communication_ok)
        self.assertTrue(self._velocity_matches(self.odom_messages[-1], 0.0, 0.0, 0.0))
        self.assertEqual(
            self.odom_messages[-1].twist.covariance[0],
            self.bridge.covariance["unavailable_variance"],
        )
        stopped_x = self.odom_messages[-1].pose.pose.position.x
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            self._write_frame(MSG_TYPE_STATUS, self._status_payload(1))
            self.executor.spin_once(timeout_sec=0.05)
        self.assertAlmostEqual(
            self.odom_messages[-1].pose.pose.position.x, stopped_x, places=4
        )

    def test_boot_id_change_clears_old_odom_and_requires_new_handshake(self):
        from robogame_core.serial_protocol import MSG_TYPE_STATUS

        self._complete_handshake(boot_id=1)
        self._send_odom(0.40, 0.0, 0.0)
        self.assertTrue(self._spin_until(lambda: any(
            msg.pose.pose.position.x > 0.005 for msg in self.odom_messages
        )))

        messages_before_reset = len(self.odom_messages)
        self._write_frame(MSG_TYPE_STATUS, self._status_payload(2))
        self.assertTrue(self._spin_until(
            lambda: self.bridge._handshake_state == "HANDSHAKING"
        ))
        self.assertIsNone(self.bridge._latest_odom)
        self.assertIsNone(self.bridge.last_decoded_odom_rx)
        self.assertTrue(self._spin_until(
            lambda: len(self.odom_messages) > messages_before_reset
            and abs(self.odom_messages[-1].pose.pose.position.x) < 0.001
        ), "reset odometry was not published")

        self._complete_handshake(boot_id=2)
        self._send_odom(-0.20, 0.0, 0.0)
        self.assertTrue(self._spin_until(lambda: any(
            self._velocity_matches(msg, -0.20, 0.0, 0.0)
            for msg in self.odom_messages
        )))

    def test_bad_mcu_ticks_do_not_refresh_odom_and_gap_can_recover(self):
        self._complete_handshake()
        self._send_odom(0.10, 0.0, 0.0, mcu_tick_ms=100)
        self.assertTrue(self._spin_until(
            lambda: self.bridge._latest_odom is not None
        ))
        accepted_timestamp = self.bridge.last_decoded_odom_rx

        for bad_tick in (100, 90, 400):
            self._send_odom(0.90, 0.0, 0.0, mcu_tick_ms=bad_tick)
            self.executor.spin_once(timeout_sec=0.05)
            self.assertAlmostEqual(self.bridge._latest_odom.vx_mps, 0.10)
            self.assertEqual(self.bridge.last_decoded_odom_rx, accepted_timestamp)

        self._send_odom(0.20, 0.0, 0.0, mcu_tick_ms=420)
        self.assertTrue(self._spin_until(
            lambda: self.bridge._latest_odom is not None
            and abs(self.bridge._latest_odom.vx_mps - 0.20) < 1e-5
        ), "normal ODOM did not recover after one rejected gap sample")


if __name__ == "__main__":
    unittest.main()
