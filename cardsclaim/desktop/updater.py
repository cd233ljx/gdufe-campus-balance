"""Explicit, verified updates from this project's public GitHub releases."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request

REPO = 'cd233ljx/gdufe-campus-balance'
EXE = 'gdufe-campus-balance.exe'
MAX_DOWNLOAD = 512 * 1024 * 1024


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('版本信息格式无效，暂不更新。')
    return tuple(map(int, value.removeprefix('v').split('.')))


def current_version():
    if getattr(sys, 'frozen', False):
        return json.loads((Path(sys.executable).parent / 'build-info.json').read_text('utf-8'))['version']
    return tomllib.loads((Path(__file__).resolve().parents[2] / 'pyproject.toml').read_text('utf-8'))['project']['version']


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit
        parsed = urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in ('github.com', 'api.github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
            raise ValueError('更新下载被重定向到未知地址，已停止。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'GDUFE-Campus-Toolbox-Updater', 'Accept': 'application/vnd.github+json'})
    return urllib.request.build_opener(SafeRedirect()).open(request, timeout=20)


def parse_release(data, current):
    version = data.get('tag_name', '')
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('发布信息不是正式版本。')
    if version_tuple(version) <= version_tuple(current):
        return None
    suffix = 'windows-x64-setup.exe'
    name = f'gdufe-campus-balance-{version}-{suffix}'
    assets = [a for a in data.get('assets', []) if a.get('name') == name]
    if len(assets) != 1:
        raise ValueError('新版下载文件尚未准备好，请稍后重试。')
    asset = assets[0]
    expected_url = f'https://github.com/{REPO}/releases/download/{version}/{name}'
    digest = asset.get('digest', '')
    if asset.get('browser_download_url') != expected_url or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('新版缺少有效的下载地址或 SHA256 校验信息。')
    size = asset.get('size')
    if type(size) is not int or not 0 < size <= MAX_DOWNLOAD:
        raise ValueError('新版下载文件大小无效。')
    return dict(version=version.removeprefix('v'), name=name, url=expected_url,
                sha256=digest[7:], size=size,
                notes=str(data.get('body') or '此版本未提供更新说明。')[:12000])


def check_update(current):
    try:
        with open_url(f'https://api.github.com/repos/{REPO}/releases/latest') as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('更新信息过大。')
        return parse_release(json.loads(raw), current)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('无法连接 GitHub 检查更新，请检查网络后重试。') from error


def download(release, folder, cancelled, progress=lambda done, total: None):
    destination = Path(folder) / release['name']
    partial = destination.with_suffix(destination.suffix + '.part')
    digest = hashlib.sha256()
    total = 0
    try:
        with open_url(release['url']) as response, partial.open('wb') as output:
            while True:
                if cancelled.is_set():
                    raise ValueError('已取消下载，当前版本继续运行。')
                block = response.read(256 * 1024)
                if not block:
                    break
                total += len(block)
                if total > release['size']:
                    raise ValueError('下载大小与发布记录不一致，已停止更新。')
                output.write(block)
                digest.update(block)
                progress(total, release['size'])
        if total != release['size'] or digest.hexdigest() != release['sha256']:
            raise ValueError('下载不完整或校验失败，当前版本未修改，请重试。')
        if cancelled.is_set():
            raise ValueError('已取消下载，当前版本继续运行。')
        partial.replace(destination)
        return destination
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('下载失败，当前版本未修改，请检查网络后重试。') from error
    finally:
        partial.unlink(missing_ok=True)


def prepare(release, cancelled, progress):
    from .store import installation
    if not getattr(sys, 'frozen', False) or not installation():
        raise ValueError('请先安装正式安装版；源码和旧便携版不能直接覆盖更新。')
    folder = Path(tempfile.mkdtemp(prefix='GDUFE-update-'))
    artifact = download(release, folder, cancelled, progress)
    current = Path(sys.executable).resolve().parent
    helper = folder / 'helper'
    helper.mkdir()
    shutil.copy2(current / EXE, helper / EXE)
    shutil.copytree(current / '_internal', helper / '_internal')
    plan = dict(release, target=str(current), artifact=str(artifact), parent=os.getpid())
    path = folder / 'plan.json'
    path.write_text(json.dumps(plan), encoding='utf-8')
    if cancelled.is_set():
        raise ValueError('已取消更新，当前版本继续运行。')
    return helper / EXE, path


def launch(prepared):
    helper, plan = prepared
    return subprocess.Popen([str(helper), '--apply-update', str(plan)], cwd=helper.parent,
                            creationflags=subprocess.CREATE_NO_WINDOW)


def wait_parent(pid):
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise ValueError('无法确认原程序已退出，已停止更新。')
    try:
        if kernel.WaitForSingleObject(handle, 180000) != 0:
            raise ValueError('原程序尚未退出，已停止更新。')
    finally:
        kernel.CloseHandle(handle)
    time.sleep(1)  # Allow the windowed PyInstaller bootloader to release its EXE.


def apply_plan(path):
    path = Path(path).resolve()
    try:
        plan = json.loads(path.read_text('utf-8'))
        target, artifact = Path(plan['target']).resolve(), Path(plan['artifact']).resolve()
        if not (target / EXE).is_file() or not artifact.is_relative_to(path.parent):
            raise ValueError('更新计划路径无效。')
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != plan['sha256']:
            raise ValueError('更新文件在下载后发生变化，已停止。')
        wait_parent(plan['parent'])
        if not (target / 'installation.ini').is_file():
            raise ValueError('安装信息缺失，请手动运行安装器。')
        result = subprocess.run([str(artifact), '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', f'/DIR={target}'])
        if result.returncode:
            raise ValueError('安装未完成，请重新打开原程序重试，或手动运行下载的安装器。')
        process = subprocess.Popen([str(target / EXE)], cwd=target)
        path.with_name('result.json').write_text(json.dumps({'ok': True, 'pid': process.pid}), encoding='utf-8')
    except Exception:
        message = '更新未完成。原有数据已保留，请重新打开原程序，或从 GitHub 下载新版。\n更新文件位于：' + str(path.parent)
        ctypes.windll.user32.MessageBoxW(None, message, '广财校园工具箱 · 更新', 0x10)
        raise
