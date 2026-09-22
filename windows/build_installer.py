"""Build an offline per-user installer without touching portable user data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def compiler_path(explicit=None):
    candidates = [explicit, os.environ.get('ISCC'), shutil.which('ISCC'),
                  ROOT / 'build/tools/InnoSetup/ISCC.exe',
                  Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Inno Setup 6/ISCC.exe',
                  Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Inno Setup 6/ISCC.exe']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise SystemExit('Install Inno Setup 6.7.3, or pass --iscc PATH / set ISCC. https://jrsoftware.org/isdl.php')


def build(skip_freeze=False, iscc=None, test_product=False):
    compiler = compiler_path(iscc)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    stage = ROOT / 'dist/installer-stage'
    app = stage / 'gdufe-campus-balance'
    if not skip_freeze:
        subprocess.run([sys.executable, str(ROOT / 'windows/freeze.py'), '--distpath', str(stage)], check=True, cwd=ROOT)
        for name in ('README.md', 'LICENSE'):
            shutil.copyfile(ROOT / name, app / name)
        subprocess.run([sys.executable, str(ROOT / 'windows/licenses.py'), str(app)], check=True, cwd=ROOT)
    for name in ('gdufe-campus-balance.exe', 'build-info.json', 'THIRD-PARTY-LICENSES/INDEX.txt'):
        if not (app / name).is_file():
            raise SystemExit(f'Missing {name}; build without --skip-freeze first.')
    if (app / 'data').exists() or (app / 'installation.ini').exists():
        raise SystemExit('Staging contains runtime data or an installation marker; use a fresh staging directory.')
    (stage / 'installation.ini').write_text('[install]\n', encoding='utf-16')
    output = ROOT / ('build/installer-test' if test_product else 'dist/release')
    output.mkdir(parents=True, exist_ok=True)
    command = [compiler, f'/DAppVersion={version}', f'/DAppSource={app}', f'/DOutputPath={output}']
    if test_product:
        command += ['/DProductId=GDUFE.CampusBalance.InstallerTest', '/DProductName=GDUFE Campus Balance InstallerTest', '/DInstallerTest']
    subprocess.run([*command, str(ROOT / 'windows/installer.iss')], check=True, cwd=ROOT)
    installer = output / f'gdufe-campus-balance-v{version}-windows-x64-setup.exe'
    info = json.loads((app / 'build-info.json').read_text(encoding='utf-8'))
    info.update(sha256=hashlib.sha256(installer.read_bytes()).hexdigest(), artifact=installer.name,
                test_product=test_product)
    installer.with_suffix('.build.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    installer.with_suffix('.sha256').write_text(f'{info["sha256"]}  {installer.name}\n', encoding='utf-8')
    print(installer)
    return installer


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iscc')
    parser.add_argument('--skip-freeze', action='store_true', help='Recompile installer using existing staged build')
    parser.add_argument('--test-product', action='store_true', help='Isolate installer QA from the real application')
    options = parser.parse_args()
    build(options.skip_freeze, options.iscc, options.test_product)
