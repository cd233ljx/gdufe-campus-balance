# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Standard-library bootstrap for the Windows source distribution."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
os.chdir(root)
if os.name != 'nt' or sys.version_info < (3, 12):
    raise SystemExit('Requires Windows 10/11 and Python 3.12 or newer.')
venv = root / '.venv-windows'
python = venv / 'Scripts' / 'python.exe'
if not python.exists():
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
lock = root / 'requirements.lock'
digest = hashlib.sha256(lock.read_bytes()).hexdigest()
marker = venv / 'cardsclaim-ready'
if not marker.exists() or marker.read_text() != digest:
    print('Installing dependencies for the first launch. No extra browser download is needed.', flush=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '--require-hashes', '-r', str(lock)], check=True)
    marker.write_text(digest)
subprocess.Popen([str(python.with_name('pythonw.exe')), '-m', 'cardsclaim.desktop.app'],
                 creationflags=subprocess.CREATE_NO_WINDOW)
