"""One event per completed installer run, without a persistent device ID."""
import threading
import urllib.error
import uuid
from datetime import datetime, timedelta

from .model import TZ
from .telemetry import _post
from .updater import current_version


MAX_PENDING = 100


class InstallationCounter:
    def __init__(self, store, sender=_post):
        self.store = store
        self.sender = sender

    def record_completion(self):
        """Persist before sending so a failed request can be retried later."""
        try:
            with self.store.lock('install-counts.lock'):
                state = self.store.read('install-counts', {})
                cutoff = (datetime.now(TZ).date() - timedelta(days=29)).isoformat()
                queue = [e for e in state.get('queue', []) if e.get('day', '') >= cutoff]
                queue.append({'event_id': uuid.uuid4().hex,
                              'day': datetime.now(TZ).date().isoformat(),
                              'version': current_version()})
                self.store.write('install-counts', {'queue': queue[-MAX_PENDING:]})
            return True
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def flush(self):
        try:
            with self.store.lock('install-counts.lock'):
                state = self.store.read('install-counts', {})
                cutoff = (datetime.now(TZ).date() - timedelta(days=29)).isoformat()
                queue = [e for e in state.get('queue', []) if e.get('day', '') >= cutoff]
                if len(queue) != len(state.get('queue', [])):
                    state['queue'] = queue
                    self.store.write('install-counts', state)
                for event in queue[:50]:
                    self.sender('/v1/install', event)
                    state['queue'] = state['queue'][1:]
                    self.store.write('install-counts', state)
            return True
        except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
            return False

    def flush_async(self):
        threading.Thread(target=self.flush, daemon=True).start()
