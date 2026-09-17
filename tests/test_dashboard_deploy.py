from pathlib import Path
import tempfile
import unittest
from tools.deploy_dashboard_distance import digest, validate_files, ensure_idle, restore_files


class DeploymentTests(unittest.TestCase):
    def test_preflight_rejects_changed_live_file_and_tampered_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'root'
            payload = Path(directory)/'payload'
            root.mkdir(); payload.mkdir()
            (root/'app.py').write_text('old')
            (payload/'app.py').write_text('new')
            manifest = {'payload': {'app.py': digest(payload/'app.py')}, 'baseline': {'app.py': digest(root/'app.py')}}
            validate_files(root, payload, manifest)
            (root/'app.py').write_text('changed by another operator')
            with self.assertRaisesRegex(RuntimeError, 'Live file changed'):
                validate_files(root, payload, manifest)
            (payload/'app.py').write_text('corrupted')
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                validate_files(root, payload, manifest)

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'root'; root.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'outside'):
                validate_files(root, root, {'payload': {'../outside': ''}, 'baseline': {}})

    def snapshot(self):
        t = {}
        for k in ('velocity_vx','velocity_vy','velocity_wz'):
            t[k] = 0.; t[k+'_age_s'] = .1
        return {'mode':'OBSERVE','telemetry':t,'processes':[], 'mechanism_busy':False}

    def test_idle_requires_fresh_finite_zero_velocity(self):
        ensure_idle(self.snapshot())
        for key,value in [('velocity_vx',.1),('velocity_vy',float('nan')),('velocity_wz_age_s',2.)]:
            s = self.snapshot(); s['telemetry'][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError): ensure_idle(s)

    def test_active_tasks_reject_deployment(self):
        for mode in ('MANUAL','DISTANCE','LINE'):
            s = self.snapshot(); s['mode'] = mode
            with self.assertRaises(RuntimeError): ensure_idle(s)
        s = self.snapshot(); s['processes'] = [{'name':'motion','running':True}]
        with self.assertRaises(RuntimeError): ensure_idle(s)

    def test_rollback_restores_replaced_files_without_deleting_added_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'root'; backup = Path(directory)/'backup'
            root.mkdir(); backup.mkdir()
            (root/'old.py').write_text('new'); (backup/'old.py').write_text('original')
            (root/'added.py').write_text('new module')
            restore_files(root, backup, {'payload': {'old.py':'','added.py':''}})
            self.assertEqual((root/'old.py').read_text(),'original')
            self.assertTrue((root/'added.py').exists())
