import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from cardsclaim.desktop import updater


def release(version='0.6.0', body=b'installer'):
    name = f'gdufe-campus-balance-v{version}-windows-x64-setup.exe'
    return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False,
            'assets': [{'name': name, 'browser_download_url': f'https://github.com/{updater.REPO}/releases/download/v{version}/{name}',
                        'digest': 'sha256:' + hashlib.sha256(body).hexdigest(), 'size': len(body)}]}


class UpdateTests(unittest.TestCase):
    def test_semantic_versions_and_downgrade(self):
        self.assertGreater(updater.version_tuple('0.10.0'), updater.version_tuple('0.9.0'))
        for version in ('0.6.0', '0.7.0'):
            self.assertIsNone(updater.parse_release(release(), version))
        with self.assertRaises(ValueError):
            updater.version_tuple('v0.6.0-beta')

    def test_release_rejects_unknown_assets_or_digest(self):
        for field, value in [('browser_download_url', 'https://evil.invalid/setup.exe'), ('digest', ''), ('size', -1), ('name', '../setup.exe')]:
            data = release()
            data['assets'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                updater.parse_release(data, '0.5.0')
        for field in ('draft', 'prerelease'):
            data = release()
            data[field] = True
            with self.assertRaises(ValueError):
                updater.parse_release(data, '0.5.0')

    def test_check_uses_public_latest_release(self):
        with patch.object(updater, 'open_url', return_value=io.BytesIO(json.dumps(release()).encode())) as request:
            result = updater.check_update('0.5.0')
        self.assertEqual(result['version'], '0.6.0')
        self.assertTrue(request.call_args.args[0].endswith('/releases/latest'))

    def test_download_success_and_progress(self):
        data = updater.parse_release(release(), '0.5.0')
        progress = []
        with tempfile.TemporaryDirectory() as folder, patch.object(updater, 'open_url', return_value=io.BytesIO(b'installer')):
            result = updater.download(data, folder, threading.Event(), lambda a, b: progress.append((a, b)))
            self.assertEqual(result.read_bytes(), b'installer')
            self.assertEqual(progress[-1], (9, 9))
            self.assertFalse(list(Path(folder).glob('*.part')))

    def test_corrupt_truncated_oversize_and_cancelled_never_install(self):
        data = updater.parse_release(release(), '0.5.0')
        for body, cancel in [(b'installeX', False), (b'half', False), (b'installer-extra', False), (b'installer', True)]:
            with self.subTest(body=body, cancel=cancel), tempfile.TemporaryDirectory() as folder:
                event = threading.Event()
                if cancel:
                    event.set()
                with patch.object(updater, 'open_url', return_value=io.BytesIO(body)), self.assertRaises(ValueError):
                    updater.download(data, folder, event)
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_redirect_rejects_http_or_unrelated_host(self):
        redirect = updater.SafeRedirect()
        for url in ('http://github.com/file', 'https://evil.invalid/file'):
            with self.assertRaises(ValueError):
                redirect.redirect_request(None, None, 302, '', {}, url)

    def test_apply_waits_then_installs_and_relaunches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / '中文安装路径'
            target.mkdir()
            (target / updater.EXE).write_bytes(b'old')
            (target / 'installation.ini').write_text('[install]')
            artifact = root / 'setup.exe'
            artifact.write_bytes(b'installer')
            path = root / 'plan.json'
            path.write_text(json.dumps(dict(target=str(target), artifact=str(artifact), parent=123,
                                           sha256=hashlib.sha256(b'installer').hexdigest())))
            with patch.object(updater, 'wait_parent') as wait, patch.object(updater.subprocess, 'run') as run, patch.object(updater.subprocess, 'Popen') as launch:
                run.return_value.returncode = 0
                launch.return_value.pid = 456
                updater.apply_plan(path)
                wait.assert_called_once_with(123)
                self.assertIn(f'/DIR={target}', run.call_args.args[0])
                self.assertIn('/NORESTART', run.call_args.args[0])
                launch.assert_called_once()
            self.assertEqual((target / updater.EXE).read_bytes(), b'old')

    def test_changed_download_does_not_run_installer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / updater.EXE).write_bytes(b'old')
            (root / 'setup.exe').write_bytes(b'changed')
            path = root / 'plan.json'
            path.write_text(json.dumps(dict(target=str(root), artifact=str(root / 'setup.exe'), sha256='0'*64)))
            with patch.object(updater.ctypes.windll.user32, 'MessageBoxW'), patch.object(updater.subprocess, 'run') as run, self.assertRaises(ValueError):
                updater.apply_plan(path)
            run.assert_not_called()
