import os
import tempfile
import threading
import time
import unittest


class RobotBridgeMechanismIntegrationTests(unittest.TestCase):
    """Exercise ROS service -> V1 0x20/0x21 over a Linux PTY."""

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
            from robogame_interfaces.srv import SetLiftHeight  # noqa: F401
        except (ImportError, ModuleNotFoundError) as exc:
            cls._skip_reason = f"ROS 2 integration dependencies unavailable: {exc}"

    def _run_case(
        self,
        *,
        service_name,
        command=None,
        height_m=None,
        timeout_s=1.0,
        behavior="success",
        secondary_service_name=None,
        secondary_command=None,
        complete_primary_after_secondary=False,
    ):
        if self._skip_reason:
            self.skipTest(self._skip_reason)

        import pty
        import rclpy
        from rclpy.executors import MultiThreadedExecutor
        from robot_bridge.node import RobotBridge
        from robogame_core.serial_protocol import (
            MSG_TYPE_ACK,
            MSG_TYPE_HELLO,
            MSG_TYPE_MECHANISM_COMMAND,
            MSG_TYPE_MECHANISM_STATUS,
            MSG_TYPE_STATUS,
            STATUS_EMERGENCY_STOP,
            STATUS_PHYSICAL_START,
            MechanismState,
            MechanismStatus,
            StatusSample,
            StreamDecoder,
            decode_mechanism_command,
            encode_ack,
            encode_frame,
            encode_mechanism_status,
            encode_status,
        )
        from robogame_interfaces.srv import ExecuteMechanism, SetLiftHeight

        master_fd, slave_fd = pty.openpty()
        os.set_blocking(master_fd, False)
        slave_name = os.ttyname(slave_fd)
        stop_event = threading.Event()
        received_commands = []
        mcu_sequence = 1000
        mcu_boot_id = 1
        held_command = None
        complete_held_command = threading.Event()

        def send_frame(message_type, payload=b""):
            nonlocal mcu_sequence
            os.write(master_fd, encode_frame(message_type, mcu_sequence, payload))
            mcu_sequence = (mcu_sequence + 1) & 0xFFFF

        def send_status(flags=STATUS_PHYSICAL_START, boot_id=None):
            now = time.monotonic()
            active_boot_id = mcu_boot_id if boot_id is None else boot_id
            send_frame(
                MSG_TYPE_STATUS,
                encode_status(
                    StatusSample(
                        int(now * 1000) & 0xFFFFFFFF,
                        flags,
                        0,
                        24000,
                        active_boot_id,
                    )
                ),
            )

        def send_mechanism_status(mechanism_command, state, error_code=0):
            send_frame(
                MSG_TYPE_MECHANISM_STATUS,
                encode_mechanism_status(
                    MechanismStatus(
                        mechanism_command.command_id,
                        mechanism_command.operation,
                        state,
                        error_code,
                        20,
                    )
                ),
            )

        def fake_mcu():
            nonlocal mcu_boot_id, held_command
            decoder = StreamDecoder()
            last_status = 0.0
            while not stop_event.is_set():
                try:
                    chunk = os.read(master_fd, 4096)
                except BlockingIOError:
                    chunk = b""
                for frame in decoder.feed(chunk):
                    if frame.message_type == MSG_TYPE_HELLO:
                        send_frame(MSG_TYPE_ACK, encode_ack(frame.sequence))
                        continue
                    if frame.message_type != MSG_TYPE_MECHANISM_COMMAND:
                        continue
                    mechanism_command = decode_mechanism_command(frame.payload)
                    received_commands.append((frame.sequence, mechanism_command))
                    attempt = len(received_commands)

                    if behavior == "silence":
                        continue
                    if behavior == "drop_first" and attempt == 1:
                        continue
                    if behavior == "ack_reject":
                        send_frame(MSG_TYPE_ACK, encode_ack(frame.sequence, 7))
                        continue

                    send_frame(MSG_TYPE_ACK, encode_ack(frame.sequence))
                    if behavior == "status_silence":
                        continue
                    send_mechanism_status(mechanism_command, MechanismState.ACCEPTED)
                    send_mechanism_status(mechanism_command, MechanismState.RUNNING)
                    if behavior == "hold_running" and held_command is None:
                        held_command = mechanism_command
                    elif behavior == "failed":
                        send_mechanism_status(
                            mechanism_command, MechanismState.FAILED, error_code=42
                        )
                    elif behavior == "emergency_stop":
                        send_status(STATUS_PHYSICAL_START | STATUS_EMERGENCY_STOP)
                    elif behavior == "boot_restart":
                        mcu_boot_id = 2
                        send_status(STATUS_PHYSICAL_START)
                    else:
                        send_mechanism_status(
                            mechanism_command, MechanismState.SUCCEEDED
                        )

                if held_command is not None and complete_held_command.is_set():
                    send_mechanism_status(held_command, MechanismState.SUCCEEDED)
                    held_command = None

                now = time.monotonic()
                if now - last_status >= 0.05:
                    send_status()
                    last_status = now
                time.sleep(0.005)

        bridge = client_node = executor = spin_thread = None
        mcu_thread = threading.Thread(target=fake_mcu, daemon=True)
        try:
            rclpy.init(
                args=[
                    "--ros-args",
                    "-p",
                    "mock_mode:=false",
                    "-p",
                    f"serial_port:={slave_name}",
                ]
            )
            bridge = RobotBridge()
            client_node = rclpy.create_node("mechanism_integration_client")
            service_type = SetLiftHeight if height_m is not None else ExecuteMechanism
            client = client_node.create_client(service_type, service_name)
            executor = MultiThreadedExecutor(num_threads=3)
            executor.add_node(bridge)
            executor.add_node(client_node)
            mcu_thread.start()
            spin_thread = threading.Thread(target=executor.spin, daemon=True)
            spin_thread.start()

            self.assertTrue(client.wait_for_service(timeout_sec=2.0))
            ready_deadline = time.monotonic() + 2.0
            while (
                (bridge._handshake_state != "READY" or not bridge._communication_ok)
                and time.monotonic() < ready_deadline
            ):
                time.sleep(0.01)
            self.assertEqual(bridge._handshake_state, "READY")
            self.assertTrue(bridge._communication_ok)

            request = service_type.Request()
            if height_m is None:
                request.command = command
            else:
                request.height_m = height_m
            request.timeout_s = timeout_s
            future = client.call_async(request)
            secondary_future = None
            if secondary_service_name is not None:
                command_deadline = time.monotonic() + 2.0
                while not received_commands and time.monotonic() < command_deadline:
                    time.sleep(0.01)
                self.assertTrue(received_commands, "primary command did not reach fake MCU")
                secondary_client = client_node.create_client(
                    ExecuteMechanism, secondary_service_name
                )
                self.assertTrue(secondary_client.wait_for_service(timeout_sec=2.0))
                secondary_request = ExecuteMechanism.Request()
                secondary_request.command = secondary_command
                secondary_request.timeout_s = timeout_s
                secondary_future = secondary_client.call_async(secondary_request)
                secondary_deadline = time.monotonic() + 2.0
                while (
                    not secondary_future.done()
                    and time.monotonic() < secondary_deadline
                ):
                    time.sleep(0.01)
                self.assertTrue(
                    secondary_future.done(), "secondary mechanism service did not finish"
                )
                if complete_primary_after_secondary:
                    complete_held_command.set()
            deadline = time.monotonic() + max(2.0, timeout_s + 1.0)
            while not future.done() and time.monotonic() < deadline:
                time.sleep(0.01)

            self.assertTrue(future.done(), "mechanism service did not reach a terminal result")
            self.assertIsNotNone(future.result())
            secondary_response = (
                secondary_future.result() if secondary_future is not None else None
            )
            return future.result(), received_commands, secondary_response
        finally:
            stop_event.set()
            if mcu_thread.is_alive():
                mcu_thread.join(timeout=1.0)
            if executor is not None:
                executor.shutdown(timeout_sec=1.0)
            if spin_thread is not None:
                spin_thread.join(timeout=1.0)
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

    def test_grab_release_home_and_stop_encode_expected_operations(self):
        from robogame_core.serial_protocol import MechanismOperation

        cases = (
            ("/gripper/grab", "GRAB", MechanismOperation.GRAB),
            ("/gripper/release", "RELEASE", MechanismOperation.RELEASE),
            ("/mechanism/home", "HOME", MechanismOperation.HOME),
            ("/mechanism/stop", "STOP", MechanismOperation.STOP),
        )
        for service_name, command_text, expected_operation in cases:
            with self.subTest(service=service_name):
                response, received, _ = self._run_case(
                    service_name=service_name, command=command_text
                )
                self.assertTrue(response.success, response.detail)
                self.assertEqual(response.error_code, 0)
                self.assertEqual(len(received), 1)
                self.assertEqual(received[0][1].operation, expected_operation)
                self.assertEqual(received[0][1].parameter, 0)
                self.assertEqual(received[0][1].timeout_ms, 1000)

    def test_lift_converts_metres_to_millimetres(self):
        from robogame_core.serial_protocol import MechanismOperation

        response, received, _ = self._run_case(
            service_name="/lift/set_height", height_m=0.123, timeout_s=1.5
        )
        self.assertTrue(response.success, response.detail)
        self.assertEqual(len(received), 1)
        command = received[0][1]
        self.assertEqual(command.operation, MechanismOperation.LIFT_ABS)
        self.assertEqual(command.parameter, 123)
        self.assertEqual(command.timeout_ms, 1500)

    def test_ack_rejection_is_returned_to_ros_client(self):
        response, received, _ = self._run_case(
            service_name="/gripper/grab", command="GRAB", behavior="ack_reject"
        )
        self.assertFalse(response.success)
        self.assertEqual(response.error_code, 7)
        self.assertEqual(len(received), 1)

    def test_lost_first_attempt_retries_same_command_id_with_new_sequence(self):
        response, received, _ = self._run_case(
            service_name="/gripper/grab", command="GRAB", behavior="drop_first"
        )
        self.assertTrue(response.success, response.detail)
        self.assertEqual(len(received), 2)
        self.assertNotEqual(received[0][0], received[1][0])
        self.assertEqual(received[0][1].command_id, received[1][1].command_id)

    def test_mcu_failed_status_preserves_error_code(self):
        response, received, _ = self._run_case(
            service_name="/gripper/grab", command="GRAB", behavior="failed"
        )
        self.assertFalse(response.success)
        self.assertEqual(response.error_code, 42)
        self.assertEqual(len(received), 1)

    def test_response_silence_retries_three_times_then_fails(self):
        response, received, _ = self._run_case(
            service_name="/gripper/grab", command="GRAB", behavior="silence"
        )
        self.assertFalse(response.success)
        self.assertEqual(response.error_code, 9003)
        self.assertEqual(len(received), 3)
        self.assertEqual(len({item[1].command_id for item in received}), 1)

    def test_status_silence_after_ack_retries_then_fails(self):
        response, received, _ = self._run_case(
            service_name="/gripper/grab",
            command="GRAB",
            behavior="status_silence",
        )
        self.assertFalse(response.success)
        self.assertEqual(response.error_code, 9003)
        self.assertEqual(len(received), 3)
        self.assertEqual(len({item[1].command_id for item in received}), 1)

    def test_emergency_status_cancels_active_command(self):
        response, received, _ = self._run_case(
            service_name="/gripper/grab",
            command="GRAB",
            behavior="emergency_stop",
        )
        self.assertFalse(response.success)
        self.assertEqual(response.error_code, 9001)
        self.assertEqual(len(received), 1)

    def test_boot_id_change_cancels_active_command(self):
        response, received, _ = self._run_case(
            service_name="/gripper/grab",
            command="GRAB",
            behavior="boot_restart",
        )
        self.assertFalse(response.success)
        self.assertEqual(response.error_code, 9004)
        self.assertEqual(len(received), 1)

    def test_second_motion_is_rejected_busy_without_reaching_serial(self):
        primary, received, secondary = self._run_case(
            service_name="/gripper/grab",
            command="GRAB",
            behavior="hold_running",
            secondary_service_name="/gripper/release",
            secondary_command="RELEASE",
            complete_primary_after_secondary=True,
        )
        self.assertTrue(primary.success, primary.detail)
        self.assertIsNotNone(secondary)
        self.assertFalse(secondary.success)
        self.assertEqual(secondary.error_code, 6)
        self.assertEqual(len(received), 1)

    def test_mechanism_stop_cancels_primary_and_completes_independently(self):
        from robogame_core.serial_protocol import MechanismOperation

        primary, received, stop_response = self._run_case(
            service_name="/gripper/grab",
            command="GRAB",
            behavior="hold_running",
            secondary_service_name="/mechanism/stop",
            secondary_command="STOP",
        )
        self.assertFalse(primary.success)
        self.assertEqual(primary.error_code, 8)
        self.assertIsNotNone(stop_response)
        self.assertTrue(stop_response.success, stop_response.detail)
        self.assertEqual(stop_response.error_code, 0)
        self.assertEqual(len(received), 2)
        self.assertEqual(received[0][1].operation, MechanismOperation.GRAB)
        self.assertEqual(received[1][1].operation, MechanismOperation.STOP)
        self.assertNotEqual(
            received[0][1].command_id, received[1][1].command_id
        )

    def test_disconnect_cancels_old_command_and_reconnect_starts_new_session(self):
        if self._skip_reason:
            self.skipTest(self._skip_reason)

        import pty
        import rclpy
        from rclpy.executors import MultiThreadedExecutor
        from robot_bridge.node import RobotBridge
        from robogame_core.serial_protocol import (
            MSG_TYPE_ACK,
            MSG_TYPE_HELLO,
            MSG_TYPE_MECHANISM_COMMAND,
            MSG_TYPE_MECHANISM_STATUS,
            MSG_TYPE_STATUS,
            STATUS_PHYSICAL_START,
            MechanismOperation,
            MechanismState,
            MechanismStatus,
            StatusSample,
            StreamDecoder,
            decode_mechanism_command,
            encode_ack,
            encode_frame,
            encode_mechanism_status,
            encode_status,
        )
        from robogame_interfaces.srv import ExecuteMechanism

        first_master, first_slave = pty.openpty()
        second_master = second_slave = None
        first_stop = threading.Event()
        second_stop = threading.Event()
        first_command_seen = threading.Event()
        first_commands = []
        second_commands = []
        bridge = client_node = executor = spin_thread = None
        first_thread = second_thread = None

        def fake_mcu(master_fd, stop_event, commands, hold_command):
            decoder = StreamDecoder()
            sequence = 2000
            last_status = 0.0

            def send(message_type, payload=b""):
                nonlocal sequence
                os.write(master_fd, encode_frame(message_type, sequence, payload))
                sequence = (sequence + 1) & 0xFFFF

            try:
                while not stop_event.is_set():
                    try:
                        chunk = os.read(master_fd, 4096)
                    except BlockingIOError:
                        chunk = b""
                    for frame in decoder.feed(chunk):
                        if frame.message_type == MSG_TYPE_HELLO:
                            send(MSG_TYPE_ACK, encode_ack(frame.sequence))
                        elif frame.message_type == MSG_TYPE_MECHANISM_COMMAND:
                            command = decode_mechanism_command(frame.payload)
                            commands.append(command)
                            first_command_seen.set()
                            send(MSG_TYPE_ACK, encode_ack(frame.sequence))
                            for state in (
                                MechanismState.ACCEPTED,
                                MechanismState.RUNNING,
                            ):
                                send(
                                    MSG_TYPE_MECHANISM_STATUS,
                                    encode_mechanism_status(
                                        MechanismStatus(
                                            command.command_id,
                                            command.operation,
                                            state,
                                            0,
                                            10,
                                        )
                                    ),
                                )
                            if not hold_command:
                                send(
                                    MSG_TYPE_MECHANISM_STATUS,
                                    encode_mechanism_status(
                                        MechanismStatus(
                                            command.command_id,
                                            command.operation,
                                            MechanismState.SUCCEEDED,
                                            0,
                                            20,
                                        )
                                    ),
                                )
                    now = time.monotonic()
                    if now - last_status >= 0.05:
                        send(
                            MSG_TYPE_STATUS,
                            encode_status(
                                StatusSample(
                                    int(now * 1000) & 0xFFFFFFFF,
                                    STATUS_PHYSICAL_START,
                                    0,
                                    24000,
                                    1,
                                )
                            ),
                        )
                        last_status = now
                    time.sleep(0.005)
            except OSError:
                # Closing a PTY master is the disconnect stimulus for this test.
                return

        def wait_ready(timeout_s):
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if bridge._handshake_state == "READY" and bridge._communication_ok:
                    return True
                time.sleep(0.01)
            return False

        with tempfile.TemporaryDirectory() as temp_dir:
            stable_port = os.path.join(temp_dir, "mcu_serial")
            os.symlink(os.ttyname(first_slave), stable_port)
            os.set_blocking(first_master, False)
            try:
                rclpy.init(
                    args=[
                        "--ros-args",
                        "-p",
                        "mock_mode:=false",
                        "-p",
                        f"serial_port:={stable_port}",
                    ]
                )
                bridge = RobotBridge()
                client_node = rclpy.create_node("mechanism_reconnect_client")
                client = client_node.create_client(
                    ExecuteMechanism, "/gripper/grab"
                )
                executor = MultiThreadedExecutor(num_threads=3)
                executor.add_node(bridge)
                executor.add_node(client_node)
                first_thread = threading.Thread(
                    target=fake_mcu,
                    args=(first_master, first_stop, first_commands, True),
                    daemon=True,
                )
                first_thread.start()
                spin_thread = threading.Thread(target=executor.spin, daemon=True)
                spin_thread.start()
                self.assertTrue(client.wait_for_service(timeout_sec=2.0))
                self.assertTrue(wait_ready(2.0), "first MCU session did not become ready")

                first_request = ExecuteMechanism.Request()
                first_request.command = "GRAB"
                first_request.timeout_s = 2.0
                first_future = client.call_async(first_request)
                self.assertTrue(
                    first_command_seen.wait(timeout=2.0),
                    "GRAB did not reach the first MCU session",
                )
                self.assertEqual(first_commands[0].operation, MechanismOperation.GRAB)

                first_stop.set()
                os.close(first_master)
                first_master = None
                disconnect_deadline = time.monotonic() + 2.0
                while not first_future.done() and time.monotonic() < disconnect_deadline:
                    time.sleep(0.01)
                self.assertTrue(first_future.done(), "active command survived disconnect")
                first_response = first_future.result()
                self.assertFalse(first_response.success)
                self.assertEqual(first_response.error_code, 9003)

                second_master, second_slave = pty.openpty()
                os.set_blocking(second_master, False)
                replacement = stable_port + ".new"
                os.symlink(os.ttyname(second_slave), replacement)
                os.replace(replacement, stable_port)
                second_thread = threading.Thread(
                    target=fake_mcu,
                    args=(second_master, second_stop, second_commands, False),
                    daemon=True,
                )
                second_thread.start()
                self.assertTrue(wait_ready(4.0), "replacement MCU session did not re-handshake")

                second_client = client_node.create_client(
                    ExecuteMechanism, "/gripper/release"
                )
                self.assertTrue(second_client.wait_for_service(timeout_sec=2.0))
                second_request = ExecuteMechanism.Request()
                second_request.command = "RELEASE"
                second_request.timeout_s = 1.0
                second_future = second_client.call_async(second_request)
                second_deadline = time.monotonic() + 2.0
                while not second_future.done() and time.monotonic() < second_deadline:
                    time.sleep(0.01)
                self.assertTrue(second_future.done(), "new-session RELEASE timed out")
                second_response = second_future.result()
                self.assertTrue(second_response.success, second_response.detail)
                self.assertEqual(len(second_commands), 1)
                self.assertEqual(
                    second_commands[0].operation, MechanismOperation.RELEASE
                )
                self.assertNotEqual(
                    first_commands[0].command_id, second_commands[0].command_id
                )
            finally:
                first_stop.set()
                second_stop.set()
                for thread in (first_thread, second_thread):
                    if thread is not None and thread.is_alive():
                        thread.join(timeout=1.0)
                if executor is not None:
                    executor.shutdown(timeout_sec=1.0)
                if spin_thread is not None:
                    spin_thread.join(timeout=1.0)
                if bridge is not None:
                    if bridge.serial is not None:
                        bridge.serial.close()
                    bridge.destroy_node()
                if client_node is not None:
                    client_node.destroy_node()
                if rclpy.ok():
                    rclpy.shutdown()
                for descriptor in (
                    first_master,
                    first_slave,
                    second_master,
                    second_slave,
                ):
                    if descriptor is not None:
                        os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
