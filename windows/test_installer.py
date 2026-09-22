"""Exercise real install/upgrade/uninstall with a separately named QA product.

Run build_installer.py --test-product first. All data is synthetic. The real
application's registry entries, shortcuts, installation and data are untouched.
"""
import configparser
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
import winreg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cardsclaim.desktop.store import DesktopStore

NAME = 'GDUFE Campus Balance InstallerTest'
KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\GDUFE.CampusBalance.InstallerTest_is1'
RUN = r'Software\Microsoft\Windows\CurrentVersion\Run'


def reg_value(key, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except FileNotFoundError:
        return None


def known_folder(csidl):
    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer):
        raise OSError('Cannot locate Windows folder')
    return Path(buffer.value)


def main():
    import tomllib
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    package = ROOT / f'build/installer-test/gdufe-campus-balance-v{version}-windows-x64-setup.exe'
    receipt = json.loads(package.with_suffix('.build.json').read_text())
    assert receipt['test_product'] and receipt['sha256'] == hashlib.sha256(package.read_bytes()).hexdigest()
    base = ROOT / 'build/installer-qa'
    base.mkdir(exist_ok=True)
    app = base / '中文 安装目录'
    desktop = known_folder(0x10) / f'{NAME}.lnk'
    menu = known_folder(0x02) / f'{NAME}.lnk'
    data = known_folder(0x1c) / NAME / 'data'
    assert not reg_value(KEY, 'InstallLocation'), 'Uninstall previous QA product before rerunning.'
    assert not desktop.exists() and not menu.exists(), 'QA shortcuts already exist.'
    assert not data.exists(), 'QA data already exists; preserve and inspect it before rerunning.'
    reports = []

    def run(exe, *args, expected=0):
        result = subprocess.run([str(exe), *args], timeout=120)
        assert result.returncode == expected, (exe.name, result.returncode)

    def install(*args):
        run(package, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
            f'/DIR={app}', f'/LOG={base / (str(len(reports)) + "-install.log")}', *args)

    def uninstall(*args):
        uninstaller = Path(reg_value(KEY, 'UninstallString').strip('"'))
        assert uninstaller.parent == app
        run(uninstaller, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', f'/LOG={base / "uninstall.log"}', *args)
        deadline = time.monotonic() + 20
        while uninstaller.exists() and time.monotonic() < deadline:
            time.sleep(.2)

    install()
    exe = app / 'gdufe-campus-balance.exe'
    assert exe.exists() and desktop.exists() and menu.exists()
    assert reg_value(KEY, 'DisplayVersion') == version
    assert reg_value(RUN, NAME) == f'"{exe}" --startup'
    parser = configparser.ConfigParser()
    parser.read(app / 'installation.ini', encoding='utf-16')
    assert Path(parser['install']['DataDir']) == data
    reports.append('fresh install: Chinese path, both default tasks, HKCU uninstall, marker')

    # Launch the actual installed program once: AppData storage must work
    # without an inherited Python path, and without displaying the setup guide.
    env = os.environ.copy()
    env.pop('CARDSCLAIM_DATA_DIR', None)
    process = subprocess.Popen([str(exe), '--startup'], cwd=base, env=env)
    try:
        deadline = time.monotonic() + 25
        while not (data / 'window.lock').exists() and time.monotonic() < deadline:
            assert process.poll() is None, 'Installed application exited early'
            time.sleep(.2)
        assert (data / 'window.lock').exists()
        installed = DesktopStore(data)
        installed.write('preferences', {'guide_seen': True, 'synthetic_test': True})
        installed.write('email', {'enabled': False, 'code': 'synthetic-code'})
        installed.write('history-2026-09-22', [])
        # The running app mutex must prevent a silent upgrade, not kill the app.
        run(package, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', f'/DIR={app}', expected=1)
        assert process.poll() is None
        reports.append('installed EXE: independent user data, upgrade blocked while running')
    finally:
        # PyInstaller's windowed bootloader has a child process. Terminate only
        # this tracked QA process tree, never processes found by application name.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=True, capture_output=True)
        process.wait(timeout=20)

    preserved = {p.name: p.read_bytes() for p in data.iterdir() if p.is_file()}
    # Exercise the bundled update helper, real installer, and automatic restart.
    update = base / ('update-' + str(time.time_ns()))
    update.mkdir()
    helper = update / 'helper'
    helper.mkdir()
    shutil.copy2(exe, helper / exe.name)
    shutil.copytree(app / '_internal', helper / '_internal')
    artifact = update / package.name
    shutil.copy2(package, artifact)
    parent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'], creationflags=subprocess.CREATE_NO_WINDOW)
    plan = update / 'plan.json'
    plan.write_text(json.dumps({'target': str(app), 'artifact': str(artifact), 'parent': parent.pid,
                               'sha256': receipt['sha256']}), encoding='utf-8')
    runner = subprocess.Popen([str(helper / exe.name), '--apply-update', str(plan)], creationflags=subprocess.CREATE_NO_WINDOW)
    parent.wait(timeout=15)
    assert runner.wait(timeout=120) == 0
    result = json.loads((update / 'result.json').read_text('utf-8'))
    assert result['ok']
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with installed.lock('window.lock'):
                    pass
            except ValueError:
                break
            time.sleep(.2)
        else:
            raise AssertionError('Updated application did not restart')
        assert installed.read('email')['code'] == 'synthetic-code'
        assert installed.read('preferences')['synthetic_test']
        assert (data / 'history-2026-09-22').read_bytes() == preserved['history-2026-09-22']
        reports.append('in-app update helper: waits for exit, verifies package, installs, restarts, preserves data')
    finally:
        subprocess.run(['taskkill', '/PID', str(result['pid']), '/T', '/F'], check=True, capture_output=True)
    preserved = {p.name: p.read_bytes() for p in data.iterdir() if p.is_file()}
    install('/TASKS=')
    assert not desktop.exists() and menu.exists() and reg_value(RUN, NAME) is None
    for name, value in preserved.items():
        assert (data / name).read_bytes() == value
    reports.append('upgrade: unchecked options remove desktop/autostart, encrypted data preserved')
    uninstall()
    deadline = time.monotonic() + 20
    while exe.exists() and time.monotonic() < deadline:
        time.sleep(.2)
    assert not exe.exists() and not desktop.exists() and not menu.exists()
    assert reg_value(KEY, 'DisplayVersion') is None and reg_value(RUN, NAME) is None
    for name, value in preserved.items():
        assert (data / name).read_bytes() == value
    reports.append('uninstall: program/shortcuts/registry removed, data retained by default')

    install()
    for name, value in preserved.items():
        assert (data / name).read_bytes() == value
    reports.append('reinstall: retained data survives')
    # This switch exists only in the isolated QA build; production silent
    # uninstall always preserves data. Exercise the real deletion callback.
    assert data == known_folder(0x1c) / NAME / 'data' and NAME.endswith('InstallerTest')
    uninstall('/TESTPURGEDATA=1')
    assert not data.exists()
    reports.append('uninstall clear-data choice: synthetic user data removed')
    (base / 'report.json').write_text(json.dumps({'ok': True, 'checks': reports}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
