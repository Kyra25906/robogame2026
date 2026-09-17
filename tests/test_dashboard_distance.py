import asyncio
import math
import time
import unittest
from unittest.mock import AsyncMock, Mock

from tools.distance_trial import DistanceTrial
from tools.field_dashboard import DashboardController, SafetyStatus, RosFacade
from types import SimpleNamespace


class TrialTests(unittest.TestCase):
    def test_legacy_line_does_not_fabricate_valid_sensor(self):
        facade = RosFacade.__new__(RosFacade)
        facade.controller = Mock()
        facade._legacy_line(SimpleNamespace(data=[0.1, 0.9]))
        values = facade.controller.update_telemetry.call_args.args[0]
        self.assertEqual(values['line_interface'], 'legacy')
        self.assertNotIn('line_analog_valid', values)
        self.assertNotIn('line_sensor', values)

    def trial(self, start=(2., 3., math.pi / 2)):
        return DistanceTrial(start, .5, .05, 10., 10.2, 'test', 1)

    def test_relative_heading_and_finish(self):
        t = self.trial()
        command, done = t.step((2., 3.25, math.pi / 2), 10.3)
        self.assertAlmostEqual(t.progress, .25)
        self.assertGreater(command[0], 0)
        self.assertFalse(done)
        self.assertEqual(t.step((2., 3.5, math.pi / 2), 10.4), ((0., 0., 0.), True))

    def test_bad_numbers_and_limits(self):
        for d, s in [(float('nan'), .05), (.5, float('inf')), (2, .05), (.5, .3), (-1, .05)]:
            with self.subTest(d=d, s=s), self.assertRaises(ValueError):
                DistanceTrial((0, 0, 0), d, s, 0, 0, '', 1)

    def test_heartbeat_timeout_and_path_errors(self):
        for pose, now in [((2., 3., math.pi/2), 11), ((2.2, 3.1, math.pi/2), 10.3),
                          ((2., 3.1, 0), 10.3), ((2., 2.9, math.pi/2), 10.3),
                          ((float('nan'), 3, 0), 10.3)]:
            with self.subTest(pose=pose, now=now), self.assertRaises(ValueError):
                self.trial().step(pose, now)

    def test_overall_timeout_even_with_heartbeat(self):
        t = self.trial()
        t.heartbeat_at = 40
        with self.assertRaisesRegex(ValueError, '执行超时'):
            t.step(t.start, 40)

    def test_frozen_feedback_stops_before_overall_timeout(self):
        t = self.trial()
        t.heartbeat_at = 12.1
        with self.assertRaisesRegex(ValueError, '没有前进反馈'):
            t.step(t.start, 12.1)

    def test_slowdown_and_closed_loop_simulation(self):
        t = self.trial((0., 0., 0.))
        x = 0.
        speeds = []
        for n in range(500):
            now = 10 + n * .05
            t.heartbeat_at = now
            cmd, done = t.step((x, 0., 0.), now)
            speeds.append(cmd[0])
            x += cmd[0] * .05
            if done:
                break
        self.assertTrue(done)
        self.assertLessEqual(abs(x - .5), .011)
        self.assertLessEqual(max(speeds), .05)
        self.assertEqual(speeds[-1], 0)


class DashboardDistanceTests(unittest.IsolatedAsyncioTestCase):
    def app(self):
        console = Mock()
        console.items = {}
        console.start = AsyncMock()
        console.stop = AsyncMock()
        app = DashboardController(console, Mock(), Mock(), asyncio.get_running_loop())
        app.record = Mock()
        app.ros = Mock()
        app.ros.publishers.return_value = []
        app.ros.mechanism.return_value = {'success': True}
        self.feed(app)
        return app

    def feed(self, app, x=2., y=3., yaw=math.pi/2, valid=True):
        app.state.safety = SafetyStatus(received_at=time.monotonic(), communication_ok=True, physical_start=True, boot_id=1)
        app.update_telemetry({'distance_pose_x': x, 'distance_pose_y': y, 'pose_yaw': yaw, 'odom_feedback_valid': valid})

    async def start(self, app):
        return await app.action('/api/distance/start', {'distance_m': .5, 'speed_mps': .05})

    async def test_success_publishes_zero_and_records_progress(self):
        app = self.app()
        await self.start(app)
        self.feed(app, y=3.5)
        app.distance_tick(time.monotonic())
        app.ros.drive.assert_called_with(0., 0., 0.)
        self.assertEqual(app.state.mode, 'OBSERVE')
        self.assertEqual(app.distance_result['state'], '里程计目标已到达')
        self.assertAlmostEqual(app.distance_result['progress_m'], .5)

    async def test_stop_all_cancels_future_ticks(self):
        app = self.app()
        await self.start(app)
        await app.action('/api/control/stop-all', {})
        self.assertIsNone(app.distance_trial)
        app.ros.drive.reset_mock()
        app.distance_tick(time.monotonic())
        app.ros.drive.assert_not_called()

    async def test_stop_all_stops_sources_even_if_mechanism_fails(self):
        app = self.app()
        app.console.items = {n: SimpleNamespace(running=True) for n in ('motion', 'arm', 'line')}
        app.state.mode = 'LINE'
        app.ros.mechanism.side_effect = lambda name, timeout: {'success': name == 'chassis_stop'}
        result = await app.action('/api/control/stop-all', {})
        self.assertEqual([c.args[0] for c in app.console.stop.await_args_list], ['motion', 'arm', 'line'])
        self.assertTrue(result['chassis']['success'])
        self.assertFalse(result['mechanism']['success'])
        self.assertFalse(result['ok'])
        self.assertEqual(app.state.mode, 'OBSERVE')

    async def test_stop_all_without_ros_still_stops_processes(self):
        app = self.app()
        app.ros = None
        app.console.items = {'line': SimpleNamespace(running=True)}
        result = await app.action('/api/control/stop-all', {})
        app.console.stop.assert_awaited_once_with('line')
        self.assertFalse(result['ok'])
        self.assertFalse(app.stopping)

    async def test_stop_during_manual_acquire_does_not_reacquire(self):
        app = self.app()
        task = asyncio.create_task(app.action('/api/control/manual/acquire', {}))
        await asyncio.sleep(.03)
        await app.action('/api/control/stop-all', {})
        with self.assertRaisesRegex(ValueError, '取消'):
            await task
        self.assertEqual(app.state.mode, 'OBSERVE')

    async def test_fast_profile_clamps_axes_independently(self):
        app = self.app()
        app.max_drive_speed = .8
        app.state.acquire_manual(now=time.monotonic(), conflicting_publishers=[])
        result = await app.action('/api/control/drive', {'vx': 2, 'vy': -2, 'wz': 2, 'sequence': 1})
        self.assertEqual(result['applied'], {'vx': .8, 'vy': -.4, 'wz': .8})

    async def test_cancel_and_disconnect_stop(self):
        for action in ('/api/distance/stop', '/api/control/manual/release', 'disconnect'):
            app = self.app()
            await self.start(app)
            if action == 'disconnect':
                app.distance_trial.heartbeat_at -= 1
                self.feed(app)
                app.distance_tick(time.monotonic())
            else:
                await app.action(action, {})
            self.assertIsNone(app.distance_trial)
            app.ros.drive.assert_called_with(0., 0., 0.)

    async def test_feedback_missing_stale_invalid_and_reboot_stop(self):
        for failure in ('stale', 'invalid', 'reboot', 'estop', 'nan'):
            app = self.app()
            await self.start(app)
            self.feed(app)
            if failure == 'stale': app.telemetry_times['pose_yaw'] -= 1
            if failure == 'invalid': app.state.telemetry['odom_feedback_valid'] = False
            if failure == 'reboot': app.state.safety.boot_id = 2
            if failure == 'estop': app.state.safety.emergency_stop = True
            if failure == 'nan': app.state.telemetry['pose_yaw'] = float('nan')
            app.distance_tick(time.monotonic())
            self.assertIsNone(app.distance_trial, failure)
            app.ros.drive.assert_called_with(0., 0., 0.)

    async def test_busy_and_missing_pose_reject_without_motion(self):
        for failure in ('manual', 'mechanism', 'pose'):
            app = self.app()
            if failure == 'manual': app.state.mode = 'MANUAL'
            if failure == 'mechanism': app.mechanism_busy = True
            if failure == 'pose': app.telemetry_times.clear()
            with self.assertRaises(ValueError): await self.start(app)
            app.ros.drive.assert_not_called()

    async def test_active_trial_blocks_other_motion_and_wrong_heartbeat(self):
        app = self.app()
        r = await self.start(app)
        for path in ('/api/line/start', '/api/line/stop', '/api/process/line/stop',
                     '/api/process/motion/start', '/api/control/manual/acquire', '/api/mechanism/grab'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await app.action(path, {})
        with self.assertRaises(ValueError):
            await app.action('/api/distance/heartbeat', {'token': 'wrong'})
        await app.action('/api/distance/heartbeat', {'token': r['token']})
        self.assertEqual(app.state.mode, 'DISTANCE')

    async def test_external_publisher_rejects_and_releases(self):
        app = self.app()
        app.ros.publishers.return_value = ['external']
        with self.assertRaisesRegex(ValueError, 'external'): await self.start(app)
        self.assertEqual(app.state.mode, 'OBSERVE')
        self.assertIsNone(app.distance_trial)

    async def test_stop_during_preparation_cannot_resume(self):
        app = self.app()
        task = asyncio.create_task(self.start(app))
        await asyncio.sleep(.03)
        await app.action('/api/distance/stop', {})
        with self.assertRaisesRegex(ValueError, '取消'): await task
        self.assertIsNone(app.distance_trial)
        self.assertEqual(app.state.mode, 'OBSERVE')


if __name__ == '__main__':
    unittest.main()
