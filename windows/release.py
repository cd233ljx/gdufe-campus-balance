# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Package a clean, committed build; never include arbitrary workspace files."""
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def main():
    if git('status', '--porcelain'):
        raise SystemExit('Commit source changes before packaging a release.')
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    commit = git('rev-parse', 'HEAD')
    app = ROOT / 'dist' / 'CardsClaim'
    for required in ('CardsClaim.exe', 'LICENSE', 'README.md', 'build-info.json', 'THIRD-PARTY-LICENSES/INDEX.txt'):
        if not (app / required).is_file():
            raise SystemExit(f'Missing build file: {required}; run windows/build.cmd first.')
    info = json.loads((app / 'build-info.json').read_text(encoding='utf-8'))
    if info['commit'] != commit or info['version'] != version or info['dirty']:
        raise SystemExit('Build does not match the clean current commit; rebuild first.')
    destination = ROOT / 'dist' / 'release'
    destination.mkdir(parents=True, exist_ok=True)
    binary = destination / f'CardsClaim-v{version}-windows-x64.zip'
    source = destination / f'CardsClaim-v{version}-source.zip'
    with zipfile.ZipFile(binary, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app.rglob('*')):
            if path.is_file() and path.relative_to(app).parts[0] != 'data':
                archive.write(path, Path('CardsClaim') / path.relative_to(app))
    # Git's archive contains precisely the committed source, with no caches,
    # accounts, logs, screenshots, old ZIPs, or untracked local captures.
    subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=zip',
                    f'--prefix=CardsClaim-v{version}/', '-o', str(source), commit], check=True)
    checksums = destination / 'SHA256SUMS.txt'
    checksums.write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
                                for path in (binary, source)), encoding='utf-8')
    for path in (binary, source, checksums):
        print(path)


if __name__ == '__main__':
    main()
