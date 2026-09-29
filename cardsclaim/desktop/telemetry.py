"""Optional, minimal usage statistics. All queued data stays DPAPI-encrypted."""
import json
import os
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta

from .model import TZ
from .updater import current_version
from .store import installation


ENDPOINT = 'https://metrics.utopiacd.online'
KINDS = frozenset({
    'participation_started', 'active_open', 'setup_complete', 'feature_selected',
    'query_success_manual', 'query_success_automatic', 'alert_delivered_popup',
    'alert_delivered_email',
})
FEATURES = frozenset({'', 'electricity', 'tap_water', 'liwang', 'network'})
MAX_QUEUE = 500


def _post(path, data):
    payload = json.dumps(data, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    request = urllib.request.Request(ENDPOINT + path, data=payload,
        headers={'Content-Type': 'application/json', 'User-Agent': 'GDUFE-Campus-Toolbox-Metrics'}, method='POST')
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 204:
            raise ValueError('statistics endpoint did not acknowledge request')


class Telemetry:
    def __init__(self, store, sender=_post, auto_flush=True):
        self.store = store
        self.sender = sender
        self.auto_flush = auto_flush
        # The installer's interactive usage-statistics task may be deselected.
        # An existing in-app choice always wins over a later installer run.
        if not os.environ.get('CARDSCLAIM_DATA_DIR'):
            try:
                if installation().get('telemetryoptin') == 'yes' and store.read('telemetry') is None:
                    self.choose(True)
            except (OSError, ValueError, TypeError, KeyError):
                pass

    def status(self):
        try:
            state = self.store.read('telemetry', {})
            return {'prompted': bool(state.get('prompted')), 'enabled': bool(state.get('enabled'))}
        except (OSError, ValueError, TypeError):
            return {'prompted': False, 'enabled': False}

    def choose(self, enabled):
        """Record an explicit decision. Turning off queues deletion of the old ID."""
        try:
            with self.store.lock('telemetry.lock'):
                state = self.store.read('telemetry', {})
                prior = bool(state.get('enabled'))
                state['prompted'] = True
                if enabled and not prior:
                    state['device_id'] = uuid.uuid4().hex
                    state['enabled'] = True
                    state['queue'] = []
                    self._append(state, 'participation_started', '')
                    for item in self.store.read('account', {}).get('config', {}).get('items', []):
                        if item in FEATURES and item:
                            self._append(state, 'feature_selected', item)
                elif not enabled:
                    if prior and state.get('device_id'):
                        state.setdefault('pending_delete', []).append(state['device_id'])
                    state['enabled'] = False
                    state.pop('device_id', None)
                    state['queue'] = []
                self.store.write('telemetry', state)
            if self.auto_flush:
                self.flush_async()
            return True
        except (OSError, ValueError, TypeError):
            return False

    def _append(self, state, kind, feature):
        today = datetime.now(TZ).date()
        queue = [e for e in state.get('queue', [])
                 if e.get('day', '') >= (today - timedelta(days=29)).isoformat()]
        event = {'event_id': uuid.uuid4().hex, 'device_id': state['device_id'],
                 'day': today.isoformat(), 'version': current_version(),
                 'kind': kind, 'feature': feature}
        queue.append(event)
        state['queue'] = queue[-MAX_QUEUE:]

    def record(self, kind, feature=''):
        if kind not in KINDS or feature not in FEATURES:
            return False
        try:
            with self.store.lock('telemetry.lock'):
                state = self.store.read('telemetry', {})
                if not state.get('enabled') or not state.get('device_id'):
                    return False
                self._append(state, kind, feature)
                self.store.write('telemetry', state)
            return True
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def flush(self):
        """Failed requests leave encrypted data for a later retry."""
        try:
            with self.store.lock('telemetry.lock'):
                state = self.store.read('telemetry', {})
                deletes = list(dict.fromkeys(state.get('pending_delete', [])))
                for device_id in deletes:
                    self.sender('/v1/delete', {'device_id': device_id})
                    state['pending_delete'].remove(device_id)
                    self.store.write('telemetry', state)
                if not state.get('enabled'):
                    return True
                cutoff = (datetime.now(TZ).date() - timedelta(days=29)).isoformat()
                queue = [e for e in state.get('queue', []) if e.get('day', '') >= cutoff]
                if len(queue) != len(state.get('queue', [])):
                    state['queue'] = queue
                    self.store.write('telemetry', state)
                if queue:
                    batch = queue[:50]
                    self.sender('/v1/events', {'events': batch})
                    ids = {e['event_id'] for e in batch}
                    state['queue'] = [e for e in queue if e['event_id'] not in ids]
                    self.store.write('telemetry', state)
            return True
        except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
            return False

    def flush_async(self):
        threading.Thread(target=self.flush, daemon=True).start()
