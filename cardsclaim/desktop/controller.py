# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Serialized desktop actions; old credentials survive cancelled setup."""
import asyncio
import copy
import threading
from datetime import datetime

from .app import account_ready, background, check, session, stop
from .model import accept, validate, TZ, query_all
from ..api import Campus


def default_config():
    return {'query_time': '22:00', 'items': ['electricity', 'tap_water'],
            'thresholds': {'electricity': '20', 'tap_water': '1', 'liwang': None},
            'liwang_basis': 'cash', 'params': {}}


class Controller:
    def __init__(self, store):
        self.store = store
        self.login_loop = None
        self.login_task = None
        self.cancel_requested = threading.Event()

    def ready(self):
        try:
            account_ready(self.store)
            return True
        except ValueError:
            return False

    def resume(self):
        if self.ready() and not self.store.running():
            background(self.store)

    def refresh(self):
        stop(self.store)
        try:
            with self.store.lock():
                account = account_ready(self.store)
                state = self.store.read('state', {})
                asyncio.run(check(self.store, account['config'], account['token'], state))
                # An opening/manual check also satisfies today's scheduled check
                # only when performed after the configured time.
                if datetime.now(TZ).strftime('%H:%M') >= account['config']['query_time']:
                    state['last_day'] = datetime.now(TZ).date().isoformat()
                    self.store.write('state', state)
        finally:
            self.resume()

    def prepare_login(self, cfg, discover, progress):
        from .login import local_login
        cfg = copy.deepcopy(validate(cfg, require_params=False))
        stop(self.store)
        try:
            with self.store.lock():
                async def execute():
                    self.login_loop = asyncio.get_running_loop()
                    self.login_task = asyncio.current_task()
                    try:
                        if self.cancel_requested.is_set():
                            raise asyncio.CancelledError()
                        async with session() as http:
                            return await local_login(cfg, Campus(http, cfg), discover=discover,
                                                     direct=True, progress=progress, auth_only=True,
                                                     draft=None,
                                                     checkpoint=lambda data: self.store.write('login-draft', dict(data, saved_at=datetime.now(TZ).isoformat()) if data else {}),
                                                     diagnostic=lambda data: self.store.write('login-status', dict(data, time=datetime.now(TZ).isoformat())))
                    finally:
                        self.login_task = None
                        self.login_loop = None
                return asyncio.run(execute())
        finally:
            self.resume()

    def cancel_login(self):
        self.cancel_requested.set()
        loop, task = self.login_loop, self.login_task
        if loop and task:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def setup_token(self):
        token = self.store.read('login-draft', {}).get('token') or self.store.read('account', {}).get('token')
        if not token:
            raise ValueError('请先登录学校账号')
        return token

    def load_rooms(self, cfg):
        from .catalog import Catalog
        token = self.setup_token()
        async def load():
            async with session() as http:
                return await Catalog(http, token).initial(cfg)
        return copy.deepcopy(cfg), asyncio.run(load())

    def load_choices(self, item, field, parents):
        from .catalog import Catalog
        token = self.setup_token()
        async def load():
            async with session() as http:
                return await Catalog(http, token).choices(item, field, parents)
        return asyncio.run(load())

    def finish_rooms(self, cfg):
        cfg = copy.deepcopy(validate(cfg))
        token = self.setup_token()
        async def verify():
            async with session() as http:
                return await query_all(Campus(http, cfg), cfg, token)
        balances = asyncio.run(verify())
        self.commit_login((cfg, token, balances))

    def commit_login(self, result):
        cfg, token, balances = result
        validate(cfg)
        stop(self.store)
        try:
            with self.store.lock():
                old = self.store.read('account', {}).get('config', {})
                state = self.store.read('state', {}) if old.get('params') == cfg.get('params') and old.get('items') == cfg.get('items') else {}
                self.store.write('account', {'config': cfg, 'token': token})
                accept(cfg, state, balances)
                if datetime.now(TZ).strftime('%H:%M') >= cfg['query_time']:
                    state['last_day'] = datetime.now(TZ).date().isoformat()
                self.store.write('state', state)
                self.store.write('login-draft', {})
        finally:
            self.resume()

    def save_settings(self, cfg):
        validate(cfg)
        stop(self.store)
        try:
            with self.store.lock():
                account = account_ready(self.store)
                account['config'] = copy.deepcopy(cfg)
                self.store.write('account', account)
                state = self.store.read('state', {})
                # Evaluate changed thresholds without discarding active episodes.
                if state.get('status') == 'ok' and all(k in state.get('balances', {}) for k in cfg['items']):
                    stamp = state.get('last_success')
                    accept(cfg, state, state['balances'])
                    state['last_success'] = stamp
                self.store.write('state', state)
        finally:
            self.resume()
