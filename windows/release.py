# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Package a clean, committed build; never include arbitrary workspace files."""
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def main():
    if git('status', '--porcelain'):
        raise SystemExit('Commit source changes before packaging a release.')
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    commit = git('rev-parse', 'HEAD')
    app = ROOT / 'dist' / 'installer-stage' / 'gdufe-campus-balance'
    for required in ('gdufe-campus-balance.exe', 'LICENSE', 'README.md', 'build-info.json', 'THIRD-PARTY-LICENSES/INDEX.txt'):
        if not (app / required).is_file():
            raise SystemExit(f'Missing build file: {required}; run windows/build-installer.cmd first.')
    info = json.loads((app / 'build-info.json').read_text(encoding='utf-8'))
    if info['commit'] != commit or info['version'] != version or info['dirty']:
        raise SystemExit('Build does not match the clean current commit; rebuild first.')
    destination = ROOT / 'dist' / 'release'
    destination.mkdir(parents=True, exist_ok=True)
    installer = destination / f'gdufe-campus-balance-v{version}-windows-x64-setup.exe'
    receipt = installer.with_suffix('.build.json')
    if not installer.exists() or not receipt.exists():
        raise SystemExit('Missing installer; run windows/build-installer.cmd first.')
    installer_info = json.loads(receipt.read_text(encoding='utf-8'))
    if (installer_info.get('commit') != commit or installer_info.get('version') != version
            or installer_info.get('dirty') or installer_info.get('test_product')
            or installer_info.get('sha256') != hashlib.sha256(installer.read_bytes()).hexdigest()):
        raise SystemExit('Installer does not match the clean current commit; rebuild first.')
    source = destination / f'gdufe-campus-balance-v{version}-source.zip'
    # Git's archive contains precisely the committed source, with no caches,
    # accounts, logs, screenshots, old ZIPs, or untracked local captures.
    subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=zip',
                    f'--prefix=gdufe-campus-balance-v{version}/', '-o', str(source), commit], check=True)
    checksums = destination / 'SHA256SUMS.txt'
    checksums.write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
                                for path in (installer, source)), encoding='utf-8')
    for path in (installer, source, checksums):
        print(path)


if __name__ == '__main__':
    main()
