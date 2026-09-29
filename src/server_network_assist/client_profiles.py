"""Private saved registrations, with a compatible single selected registration."""
import hashlib
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit


class SavedSubscriptions:
    def __init__(self, online):
        self.online = online
        self.path = online.data / 'customer-online-subscriptions.json'
        self.lock = threading.RLock()

    @staticmethod
    def identifier(record):
        return hashlib.sha256((record['base_url'] + '\0' + record['device_id']).encode()).hexdigest()[:24]

    @staticmethod
    def validate(record):
        required = ('base_url', 'device_id', 'customer_id', 'signing_private_key', 'wireguard_private_key')
        if not isinstance(record, dict) or any(not isinstance(record.get(key), str) or not record[key] for key in required):
            raise ValueError('保存的订阅身份无效，请保留文件并联系管理员')
        from .client_online import parse_enrollment_url
        from .client_crypto import b64url_decode
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        import base64
        try:
            parse_enrollment_url(record['base_url'] + '/#enroll=validation-placeholder')
            Ed25519PrivateKey.from_private_bytes(b64url_decode(record['signing_private_key']))
            X25519PrivateKey.from_private_bytes(base64.b64decode(record['wireguard_private_key'], validate=True))
        except (ValueError, TypeError):
            raise ValueError('保存的订阅地址或设备密钥无效') from None

    def _load(self):
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            rows = value['subscriptions']
            if value.get('schema_version') != 1 or not isinstance(rows, list):
                raise ValueError()
            ids = set()
            for row in rows:
                self.validate(row['record'])
                if row['id'] != self.identifier(row['record']) or row['id'] in ids:
                    raise ValueError()
                ids.add(row['id'])
            return rows
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError('订阅列表读取失败，未覆盖历史数据，请保留文件并联系管理员') from None

    def _save(self, rows):
        self.online._write_private(self.path, {'schema_version': 1, 'subscriptions': rows})

    def _migrate(self):
        rows = self._load()
        if self.online.configured():
            record = self.online._record()
            self.validate(record)
            identifier = self.identifier(record)
            existing = next((row for row in rows if row['id'] == identifier), None)
            if existing and any(existing['record'][key] != record[key] for key in ('signing_private_key', 'wireguard_private_key')):
                raise ValueError('服务返回重复设备编号和不同密钥，未覆盖已保存订阅')
            if not existing:
                rows.append({'id': identifier, 'label': '原有订阅', 'record': record})
                self._save(rows)
        return rows

    def remember(self, record, label=None):
        self.validate(record)
        with self.lock:
            rows = self._migrate()
            identifier = self.identifier(record)
            existing = next((row for row in rows if row['id'] == identifier), None)
            if existing and any(existing['record'][key] != record[key] for key in ('signing_private_key', 'wireguard_private_key')):
                raise ValueError('服务返回重复设备编号和不同密钥，未覆盖已保存订阅')
            if not existing:
                rows.append({'id': identifier, 'label': (label or urlsplit(record['base_url']).hostname or '订阅')[:120],
                             'record': record})
                self._save(rows)
            return identifier

    def remember_catalog(self, catalog):
        """Persist the selected subscription's public exit types only.

        Device keys and enrollment material remain inside ``record``.  The
        public list exposes only normalized route modes so the desktop client
        can label saved subscriptions even while another subscription is
        selected or temporarily offline.
        """
        routes = catalog.get('routes', []) if isinstance(catalog, dict) else []
        modes = sorted({row.get('egress_mode') for row in routes if isinstance(row, dict)
                        and row.get('egress_mode') in ('physical', 'source_proxy')})
        if not modes or not self.online.configured():
            return
        with self.lock:
            rows = self._migrate()
            selected = self.identifier(self.online._record())
            row = next((item for item in rows if item['id'] == selected), None)
            if row is not None and row.get('egress_modes') != modes:
                row['egress_modes'] = modes
                self._save(rows)

    def import_legacy(self, root=None):
        """Explicit import, scoped to this user's standard customer directory."""
        if root is None:
            from .client_paths import customer_data
            root = customer_data()
        root = Path(root).resolve()
        with self.lock:
            before = {row['id'] for row in self._migrate()}
            paths = [self.online.path]
            # Installer data can live in LocalAppData while the previous portable
            # client lived in ProgramData. Root is still this user's fixed SID
            # directory; every imported source is checked below for containment.
            if root.is_dir():
                paths.extend(root.glob('customer-online-service.json'))
                paths.extend(root.glob('*/customer-online-service.json'))
            imported = skipped = 0
            for path in sorted(set(paths))[:128]:
                try:
                    if path != self.online.path and not path.resolve().is_relative_to(root):
                        skipped += 1
                        continue
                    if not path.is_file():
                        continue
                    record = json.loads(path.read_text(encoding='utf-8-sig'))
                    self.validate(record)
                    identifier = self.identifier(record)
                    if identifier in before:
                        skipped += 1
                        continue
                    self.remember(record, '旧版 · ' + path.parent.name)
                    before.add(identifier); imported += 1
                except (OSError, ValueError, KeyError, TypeError):
                    skipped += 1
            return {'ok': True, 'imported': imported, 'skipped': skipped, 'subscriptions': self.list()}

    def list(self):
        with self.lock:
            rows = self._migrate()
            selected = self.identifier(self.online._record()) if self.online.configured() else None
            return [{'id': row['id'], 'label': row.get('label', '订阅'),
                     'provider': urlsplit(row['record']['base_url']).hostname,
                     'customer_id': row['record']['customer_id'], 'selected': row['id'] == selected,
                     'enrolled_at': row['record'].get('enrolled_at'),
                     'egress_modes': [mode for mode in row.get('egress_modes', [])
                                      if mode in ('physical', 'source_proxy')]} for row in rows]

    def get(self, identifier):
        with self.lock:
            row = next((row for row in self._migrate() if row['id'] == identifier), None)
            if row is None:
                raise ValueError('订阅不存在，请刷新列表')
            return row

    def find_enrollment(self, base_url, digest):
        with self.lock:
            return next((row for row in self._migrate() if row['record']['base_url'] == base_url
                         and row['record'].get('enrollment_token_digest') == digest), None)

    def select(self, identifier):
        # The panel must first recover its owned network, using the old identity.
        with self.lock:
            row = self.get(identifier)
            self.online._write_private(self.online.path, row['record'])
            self.online.lease_path.unlink(missing_ok=True)
            return self.list()

    def remove(self, identifier):
        with self.lock:
            self.get(identifier)
            selected = self.identifier(self.online._record()) if self.online.configured() else None
            if identifier == selected:
                self.online.path.unlink(missing_ok=True)
                self.online.lease_path.unlink(missing_ok=True)
            self._save([row for row in self._load() if row['id'] != identifier])
            return self.list()
