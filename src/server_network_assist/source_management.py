"""Source registration and explicit customer migration, independent of enrollment URLs."""
import ipaddress
import json
import time

from .client_store import ClientStoreError
from .client_egress import validate_egress, validate_interface


class SourceManagement:
    def __init__(self, store):
        self.store = store
        with store._lock, store._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS source_migrations(
                id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, old_grant_id TEXT NOT NULL,
                new_grant_id TEXT NOT NULL, source_id TEXT NOT NULL, created_at INTEGER NOT NULL)''')

    def _source(self, db, identifier, expected_revision):
        row = db.execute('SELECT * FROM sources WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ClientStoreError('源网不存在')
        record = json.loads(row['configuration']) | {'id':row['id'], 'name':row['name']}
        if not isinstance(expected_revision, str) or self.store.source_revision(record) != expected_revision:
            raise ClientStoreError('源网配置已变化，请刷新后重试')
        return record

    @staticmethod
    def references(grant, source):
        policy = json.loads(grant['egress_policy'])
        if policy.get('source_id'):
            return policy['source_id'] == source['id']
        return grant['endpoint'] == source['endpoint'] and grant['relay_interface'] == source['relay_interface']

    def set_enabled(self, identifier, enabled, expected_revision):
        if type(enabled) is not bool:
            raise ClientStoreError('源网开关字段无效')
        with self.store._lock, self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            source = self._source(db, identifier, expected_revision)
            if enabled:
                for row in db.execute('SELECT id,configuration FROM sources WHERE id!=?', (identifier,)):
                    config = json.loads(row['configuration'])
                    if not config.get('archived') and config['relay_interface'] == source['relay_interface'] and config['endpoint'] != source['endpoint']:
                        raise ClientStoreError('中继网卡标识与另一源网冲突，请先编辑为不同标识')
            source['enabled'] = enabled
            if enabled:
                source['archived'] = False
            configuration = {k:v for k,v in source.items() if k not in ('id','name')}
            db.execute('UPDATE sources SET configuration=? WHERE id=?', (json.dumps(configuration), identifier))
        return source | {'revision':self.store.source_revision(source)}

    def delete(self, identifier, expected_revision):
        with self.store._lock, self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            source = self._source(db, identifier, expected_revision)
            references = [g for g in db.execute('SELECT * FROM line_grants') if self.references(g, source)]
            if any(g['enabled'] for g in references):
                raise ClientStoreError('该源网仍有启用的客户线路，请先更换源网或停用对应线路')
            if references:
                source.update(enabled=False, archived=True)
                configuration = {k:v for k,v in source.items() if k not in ('id','name')}
                db.execute('UPDATE sources SET configuration=? WHERE id=?', (json.dumps(configuration), identifier))
            else:
                db.execute('DELETE FROM sources WHERE id=?', (identifier,))
        return {'id':identifier, 'removed':True, 'history_preserved':bool(references)}

    def migrate(self, grant_id, source_id, mode, expected_grant, expected_source_revision, *, now=None):
        """Retire the old grant, preserving relay ownership for its final usage reports.

        Rewriting a grant in place would incorrectly attribute an old lease to the new
        relay. A new grant avoids that while keeping the customer, device and URL.
        """
        now = int(time.time()) if now is None else now
        if mode not in ('physical','source_proxy') or not isinstance(expected_grant, dict):
            raise ClientStoreError('源网迁移参数无效')
        with self.store._lock, self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM line_grants WHERE id=?', (grant_id,)).fetchone()
            if not row:
                raise ClientStoreError('客户线路不存在')
            old = dict(row)
            if expected_grant != old:
                raise ClientStoreError('客户线路已变化，请刷新后重试')
            if not old['enabled'] or old['expires_at'] is not None and old['expires_at'] <= now:
                raise ClientStoreError('请先恢复有效客户线路，再更换源网')
            customer = db.execute('SELECT enabled FROM customers WHERE id=?', (old['customer_id'],)).fetchone()
            if not customer or not customer['enabled']:
                raise ClientStoreError('客户已停用，不能更换源网')
            source = self._source(db, source_id, expected_source_revision)
            if not source.get('enabled', True) or source.get('archived', False):
                raise ClientStoreError('目标源网已停用或移除')
            other = db.execute('SELECT * FROM line_grants WHERE customer_id=? AND id!=?',
                (old['customer_id'], grant_id)).fetchall()
            if any(g['enabled'] and self.references(g, source) for g in other):
                raise ClientStoreError('此客户已具有目标源网线路，请选择已有线路')
            interface = validate_interface(source.get('proxy_interface','Meta') if mode == 'source_proxy' else source['egress_interface'])
            if interface == source['relay_interface']:
                raise ClientStoreError('出口与中继接口冲突')
            if mode == 'physical' and source.get('egress_mode','source_proxy') != 'physical':
                raise ClientStoreError('目标源网尚未配置物理出口')
            if mode == 'source_proxy':
                from .commercial_proxy import require_proxy, source_is_local
                if not source_is_local(source):
                    raise ClientStoreError('目标源网尚未接入代理状态检测')
                require_proxy(interface)
            egress = validate_egress(mode, source.get('egress_gateway','') if mode == 'physical' else '', source['dns'])
            policy = json.dumps({k:egress[k] for k in ('egress_mode','egress_gateway')} |
                {'source_id':source_id, 'require_source_proxy':mode == 'source_proxy'})
            old_policy = json.loads(old['egress_policy'])
            same = (old['endpoint'] == source['endpoint'] and old['relay_public_key'] == source['relay_public_key']
                and old['relay_interface'] == source['relay_interface'] and old['egress_interface'] == interface
                and old['dns'] == egress['dns'] and old_policy == json.loads(policy))
            if same:
                return {'grant':old, 'changed':False, 'reconnect_required':False}
            allocated = {r[0] for r in db.execute('SELECT allocated_address FROM line_grants WHERE endpoint=?', (source['endpoint'],))}
            hosts = iter(ipaddress.ip_network(source['address_pool']).hosts())
            next(hosts, None)
            address = next((str(host)+'/32' for host in hosts if str(host)+'/32' not in allocated), None)
            if address is None:
                raise ClientStoreError('目标源网客户地址池已满')
            alias = source['name']
            if any(g['alias'] == alias for g in other):
                alias += ' · ' + self.store._id('line')
            new_id = self.store._id('grant')
            db.execute('UPDATE leases SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL', (now, grant_id))
            retired_policy = json.dumps(old_policy | {'migration_retired':True})
            db.execute('UPDATE line_grants SET enabled=0,alias=?,egress_policy=? WHERE id=?',
                (old['alias']+' · 历史 '+grant_id, retired_policy, grant_id))
            db.execute('INSERT INTO line_grants VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (new_id, old['customer_id'], alias, old['tunnel'], source['endpoint'], 1,
                 source['relay_public_key'], address, egress['dns'], source['allowed_ips'], source['mtu'],
                 source['relay_interface'], interface, old['expires_at'], now, policy))
            db.execute('INSERT INTO source_migrations VALUES(?,?,?,?,?,?)',
                (self.store._id('migration'), old['customer_id'], grant_id, new_id, source_id, now))
            grant = dict(db.execute('SELECT * FROM line_grants WHERE id=?', (new_id,)).fetchone())
        return {'grant':grant, 'changed':True, 'reconnect_required':True, 'old_grant_id':grant_id}
