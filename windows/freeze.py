# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Reproducible GUI build with application-local Tcl/Tk discovery."""
from pathlib import Path
import sys
import json
import subprocess
import tomllib

root = Path(__file__).resolve().parents[1]
if (root / 'dist' / 'gdufe-campus-balance' / 'data').exists():
    raise SystemExit('Back up and move dist/gdufe-campus-balance/data before rebuilding; build output will be replaced.')
version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip())
sys.path.insert(0, str(root))
from cardsclaim.desktop.gui import icon_image, configure_tk
from PyInstaller.__main__ import run

configure_tk()
(root / 'build').mkdir(exist_ok=True)
icon_image().save(root / 'build' / 'cardsclaim.ico')
run(['--noconfirm', '--clean', '--onedir', '--windowed', '--name', 'gdufe-campus-balance',
     '--icon', str(root / 'build' / 'cardsclaim.ico'), '--paths', str(root),
     '--collect-all', 'playwright', '--hidden-import', 'pystray._win32',
     str(root / 'windows' / 'entry.py')])
(root / 'dist' / 'gdufe-campus-balance' / 'build-info.json').write_text(
    json.dumps({'version': version, 'commit': commit, 'dirty': dirty,
                'python': sys.version.split()[0], 'platform': 'windows-x64'}, indent=2),
    encoding='utf-8')
