"""Client-only preferences; never change networking or subscription permissions."""
import json
import os
from pathlib import Path
import tempfile
import threading


class ClientPreferences:
    def __init__(self, data):
        self.path = Path(data) / 'client-preferences.json'
        self.lock = threading.Lock()
        self.enabled = True
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(value, dict) and isinstance(value.get('desktop_notifications'), bool):
                self.enabled = value['desktop_notifications']
        except (OSError, ValueError):
            pass

    def state(self):
        with self.lock:
            return {'desktop_notifications': self.enabled}

    def set_notifications(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('通知开关必须为布尔值')
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                                 prefix='client-preferences-', delete=False) as stream:
                    temporary = stream.name
                    json.dump({'desktop_notifications': enabled}, stream)
                os.replace(temporary, self.path)
                self.enabled = enabled
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            return {'desktop_notifications': self.enabled}
