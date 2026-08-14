import os
import struct
import tempfile
import time
import unittest


class RobotBridgeStopIntegrationTests(unittest.TestCase):
    """Exercise ROS service -> robot_bridge -> PTY serial without hardware."""

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
            from robogame_interfaces.srv import ExecuteMechanism  # noqa: F401
        except (ImportError, ModuleNotFoundError) as exc:
            cls._skip_reason = f"ROS 2 integration dependencies unavailable: {exc}"

    def test_stop_service_emits_zero_velocity_then_emergency_stop(self):
        if self._skip_reason:
            self.skipTest(self._skip_reason)

        import pty
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from robot_bridge.node import RobotBridge
        from robogame_core.serial_protocol import (
            MSG_TYPE_EMERGENCY_STOP,
            MSG_TYPE_VELOCITY,
            StreamDecoder,
        )
        from robogame_interfaces.srv import ExecuteMechanism

        master_fd, slave_fd = pty.openpty()
        slave_name = os.ttyname(slave_fd)
        os.set_blocking(master_fd, False)
        bridge = None
        client_node = None
        executor = None
        try:
            rclpy.init(args=[
                "--ros-args",
                "-p", "mock_mode:=false",
                "-p", f"serial_port:={slave_name}",
            ])
            bridge = RobotBridge()
            client_node = rclpy.create_node("stop_integration_client")
            client = client_node.create_client(ExecuteMechanism, "/chassis/stop")
            executor = SingleThreadedExecutor()
            executor.add_node(bridge)
            executor.add_node(client_node)

            self.assertTrue(client.wait_for_service(timeout_sec=2.0))
            request = ExecuteMechanism.Request()
            request.command = "STOP"
            request.timeout_s = 1.0
            future = client.call_async(request)
            deadline = time.monotonic() + 2.0
            while not future.done() and time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.05)

            self.assertTrue(future.done(), "STOP service timed out")
            response = future.result()
            self.assertIsNotNone(response)
            self.assertTrue(response.success, response.detail)
            self.assertEqual(response.error_code, 0)
            self.assertEqual((bridge.velocity.vx, bridge.velocity.vy, bridge.velocity.wz),
                             (0.0, 0.0, 0.0))

            raw = bytearray()
            read_deadline = time.monotonic() + 1.0
            while time.monotonic() < read_deadline:
                try:
                    chunk = os.read(master_fd, 4096)
                except BlockingIOError:
                    chunk = b""
                if chunk:
                    raw.extend(chunk)
                executor.spin_once(timeout_sec=0.01)

            frames = StreamDecoder().feed(bytes(raw))
            stop_frames = [
                frame for frame in frames
                if frame.message_type in (MSG_TYPE_VELOCITY, MSG_TYPE_EMERGENCY_STOP)
            ]
            self.assertGreaterEqual(len(stop_frames), 2, bytes(raw).hex())
            self.assertEqual(
                [frame.message_type for frame in stop_frames[:2]],
                [MSG_TYPE_VELOCITY, MSG_TYPE_EMERGENCY_STOP],
            )
            self.assertEqual(
                struct.unpack("<fffB", stop_frames[0].payload),
                (0.0, 0.0, 0.0, 0),
            )
            self.assertEqual(stop_frames[1].payload, b"")
        finally:
            if executor is not None:
                if bridge is not None:
                    executor.remove_node(bridge)
                if client_node is not None:
                    executor.remove_node(client_node)
                executor.shutdown()
            if bridge is not None:
                if bridge.serial is not None:
                    bridge.serial.close()
                bridge.destroy_node()
            if client_node is not None:
                client_node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
            os.close(master_fd)
            os.close(slave_fd)

    def test_serial_disconnect_reconnects_and_starts_fresh_handshake(self):
        if self._skip_reason:
            self.skipTest(self._skip_reason)

        import pty
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from robot_bridge.node import RobotBridge
        from robogame_core.serial_protocol import (
            MSG_TYPE_HELLO,
            MSG_TYPE_VELOCITY,
            StreamDecoder,
        )

        first_master, first_slave = pty.openpty()
        second_master = second_slave = None
        bridge = None
        executor = None
        with tempfile.TemporaryDirectory() as temp_dir:
            stable_port = os.path.join(temp_dir, "mcu_serial")
            os.symlink(os.ttyname(first_slave), stable_port)
            os.set_blocking(first_master, False)
            try:
                rclpy.init(args=[
                    "--ros-args",
                    "-p", "mock_mode:=false",
                    "-p", f"serial_port:={stable_port}",
                ])
                bridge = RobotBridge()
                executor = SingleThreadedExecutor()
                executor.add_node(bridge)

                # Removing the PTY master models a USB serial device disappearing.
                os.close(first_master)
                first_master = None
                disconnect_deadline = time.monotonic() + 2.0
                while bridge.serial is not None and time.monotonic() < disconnect_deadline:
                    executor.spin_once(timeout_sec=0.05)
                self.assertIsNone(bridge.serial, "bridge did not detect serial removal")
                self.assertFalse(bridge._communication_ok)
                self.assertEqual(bridge._handshake_state, "HANDSHAKING")
                self.assertEqual(
                    (bridge.velocity.vx, bridge.velocity.vy, bridge.velocity.wz),
                    (0.0, 0.0, 0.0),
                )

                second_master, second_slave = pty.openpty()
                os.set_blocking(second_master, False)
                replacement = stable_port + ".new"
                os.symlink(os.ttyname(second_slave), replacement)
                os.replace(replacement, stable_port)

                raw = bytearray()
                reconnect_deadline = time.monotonic() + 3.0
                while time.monotonic() < reconnect_deadline:
                    executor.spin_once(timeout_sec=0.05)
                    try:
                        chunk = os.read(second_master, 4096)
                    except BlockingIOError:
                        chunk = b""
                    if chunk:
                        raw.extend(chunk)
                    frames = StreamDecoder().feed(bytes(raw))
                    if any(frame.message_type == MSG_TYPE_HELLO for frame in frames):
                        break

                frames = StreamDecoder().feed(bytes(raw))
                self.assertTrue(
                    any(frame.message_type == MSG_TYPE_HELLO for frame in frames),
                    f"no HELLO after reconnect: {bytes(raw).hex()}",
                )
                self.assertFalse(
                    any(frame.message_type == MSG_TYPE_VELOCITY for frame in frames),
                    "non-zero command path must stay locked before fresh ACK/STATUS",
                )
                self.assertIsNotNone(bridge.serial)
                self.assertFalse(bridge._communication_ok)
                self.assertEqual(bridge._handshake_state, "HANDSHAKING")
            finally:
                if executor is not None:
                    if bridge is not None:
                        executor.remove_node(bridge)
                    executor.shutdown()
                if bridge is not None:
                    if bridge.serial is not None:
                        bridge.serial.close()
                    bridge.destroy_node()
                if rclpy.ok():
                    rclpy.shutdown()
                for descriptor in (
                    first_master, first_slave, second_master, second_slave
                ):
                    if descriptor is not None:
                        os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
