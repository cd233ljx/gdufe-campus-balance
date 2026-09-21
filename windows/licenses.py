# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Copy runtime dependency notices into the distribution."""
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
target = root / 'dist' / 'CardsClaim' / 'THIRD-PARTY-LICENSES'
target.mkdir(exist_ok=True)
names = ['aiohttp', 'aiohappyeyeballs', 'aiosignal', 'attrs', 'frozenlist',
         'greenlet', 'idna', 'multidict', 'playwright', 'propcache', 'pyee',
         'typing-extensions', 'yarl', 'pystray', 'Pillow', 'six', 'pyinstaller']
index = []
for name in names:
    dist = distribution(name)
    index.append(f'{dist.metadata["Name"]} {dist.version}')
    for entry in dist.files or []:
        if any(word in entry.name.lower() for word in ('license', 'copying', 'notice')):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                destination = target / name / str(entry).replace('..', '_')
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.exists():
    shutil.copyfile(python_license, target / 'Python-LICENSE.txt')
for source in (root / 'windows' / 'licenses').glob('*'):
    if source.is_file():
        shutil.copyfile(source, target / source.name)
tk_license = Path(sys.base_prefix) / 'tcl' / 'tk8.6' / 'license.terms'
if tk_license.exists():
    shutil.copyfile(tk_license, target / 'Tk-license.terms')
(target / 'INDEX.txt').write_text('\n'.join(index) + '\nPython ' + sys.version, encoding='utf-8')
