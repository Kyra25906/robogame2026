import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from tools.field_dashboard import DashboardController, SafetyStatus

class MechanismTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        app = DashboardController(Mock(), Mock(), Mock(), asyncio.get_running_loop())
        app.record = Mock()
        app.ros = Mock()
        app.ros.mechanism.return_value = {'success': True, 'error_code': 0}
        app.state.safety = SafetyStatus(received_at=time.monotonic(), communication_ok=True, physical_start=True)
        return app

    async def test_lift_passes_height_and_records_completion(self):
        app = self.make_app()
        result = await app.action('/api/mechanism/lift', {'height_m': 0.12})
        app.ros.mechanism.assert_called_once_with('lift', 3.0, height_m=0.12)
        self.assertTrue(result['ok'])
        self.assertEqual(app.state.last_mechanism_result['state'], '成功')
        self.assertFalse(app.mechanism_busy)

    async def test_invalid_heights_never_call_ros(self):
        app = self.make_app()
        for body in ({}, {'height_m': -1}, {'height_m': 'NaN'}, {'height_m': 'inf'}, {'height_m': None}):
            with self.assertRaises(ValueError):
                await app.action('/api/mechanism/lift', body)
        app.ros.mechanism.assert_not_called()

    async def test_busy_blocks_action_but_allows_stop(self):
        app = self.make_app()
        app.mechanism_busy = True
        with self.assertRaises(ValueError):
            await app.action('/api/mechanism/grab', {})
        await app.action('/api/mechanism/stop', {})
        app.ros.mechanism.assert_called_once_with('stop', 3.0)

    async def test_exception_releases_busy_and_records_failure(self):
        app = self.make_app()
        app.ros.mechanism.side_effect = RuntimeError('disconnected')
        result = await app.action('/api/mechanism/grab', {})
        self.assertFalse(result['ok'])
        self.assertFalse(app.mechanism_busy)
        self.assertEqual(app.state.last_mechanism_result['state'], '失败')

    async def test_timeout_is_not_success(self):
        app = self.make_app()
        app.ros.mechanism.return_value = {'success': False, 'error_code': 2002}
        await app.action('/api/mechanism/grab', {})
        self.assertEqual(app.state.last_mechanism_result['state'], '超时')
