"""Administrator subscription overview and atomic in-place service changes.

Enrollment URLs, token digests and device identities are never rotated here.
Plan overrides are cloned per customer so shared plans and billing anchors stay intact.
"""
import datetime as dt
import json
import hashlib
import time

from .client_store import ClientStoreError
from .client_egress import validate_egress, validate_interface


class SubscriptionDashboard:
    @staticmethod
    def revision(customer, plan, grants):
        value = {'customer':dict(customer),'plan':dict(plan),'grants':sorted((dict(g) for g in grants),key=lambda g:g['id'])}
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def __init__(self, store):
        self.store = store
        with store._lock, store._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS commercial_service_changes(
                id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
                version INTEGER NOT NULL, changed_at INTEGER NOT NULL,
                previous TEXT NOT NULL, current TEXT NOT NULL,
                UNIQUE(customer_id,version))''')
            db.execute('CREATE INDEX IF NOT EXISTS usage_reports_time ON usage_reports(created_at)')
            db.execute('CREATE INDEX IF NOT EXISTS usage_reports_lease_time ON usage_reports(lease_id,created_at)')
            db.executescript('''CREATE TABLE IF NOT EXISTS commercial_customer_metadata(
                customer_id TEXT PRIMARY KEY REFERENCES customers(id),
                lifecycle TEXT NOT NULL DEFAULT 'active' CHECK(lifecycle IN ('active','archived','deleted')),
                tags TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '', changed_at INTEGER NOT NULL DEFAULT 0);
                CREATE TRIGGER IF NOT EXISTS commercial_customer_lifecycle_guard
                BEFORE UPDATE OF enabled ON customers
                WHEN NEW.enabled<>0 AND EXISTS(SELECT 1 FROM commercial_customer_metadata
                    WHERE customer_id=NEW.id AND lifecycle<>'active')
                BEGIN SELECT RAISE(ABORT,'请先将客户从归档或回收站恢复'); END;
            ''')

    @staticmethod
    def metadata(db, customer_id):
        row=db.execute('SELECT * FROM commercial_customer_metadata WHERE customer_id=?',(customer_id,)).fetchone()
        return {'lifecycle':row['lifecycle'] if row else 'active', 'tags':json.loads(row['tags']) if row else [],
                'notes':row['notes'] if row else '', 'lifecycle_changed_at':row['changed_at'] if row else 0}

    def snapshot(self, *, now=None, proxy=None, local_source_ids=()):
        now = int(time.time()) if now is None else now
        date = dt.datetime.fromtimestamp(now).astimezone()
        midnight = int(date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        proxy = proxy or {}
        with self.store._connect() as db:
            db.execute('BEGIN')
            customers = [dict(r) | self.metadata(db,r['id']) for r in db.execute('SELECT * FROM customers ORDER BY created_at DESC')]
            plans = {r['id']: dict(r) for r in db.execute('SELECT * FROM plans')}
            sources = [json.loads(r['configuration']) | {'id': r['id'], 'name': r['name']} for r in db.execute('SELECT * FROM sources')]
            grants = [dict(r) for r in db.execute('SELECT * FROM line_grants')]
            devices = [dict(r) for r in db.execute('SELECT id,customer_id,label,enabled,revoked_at,created_at,last_seen_at FROM devices')]
            leases = [dict(r) for r in db.execute('SELECT id,customer_id,device_id,grant_id,issued_at,expires_at,revoked_at FROM leases')]
            periods = [dict(r) for r in db.execute('SELECT * FROM usage_periods ORDER BY period_start DESC')]
            changes = [dict(r) for r in db.execute('SELECT * FROM commercial_service_changes ORDER BY changed_at DESC,version DESC')]
            # Today needs its predecessor; current rates need the latest pair.
            # Read today's indexed range and only two older rows per lease,
            # rather than sorting years of historical cumulative reports.
            reports = []
            today_totals = {}
            for lease in leases:
                pair = db.execute('''SELECT * FROM usage_reports WHERE lease_id=?
                    ORDER BY created_at DESC,rowid DESC LIMIT 2''', (lease['id'],)).fetchall()
                if not pair:
                    continue
                latest = dict(pair[0]); previous = pair[1] if len(pair) > 1 else None
                latest.update(customer_id=lease['customer_id'], latest=1,
                    drx=latest['rx_total']-(previous['rx_total'] if previous else 0),
                    dtx=latest['tx_total']-(previous['tx_total'] if previous else 0),
                    elapsed=latest['created_at']-previous['created_at'] if previous else None)
                reports.append(latest)
                if latest['created_at'] >= midnight:
                    predecessor = db.execute('''SELECT rx_total,tx_total FROM usage_reports
                        WHERE lease_id=? AND created_at<? ORDER BY created_at DESC,rowid DESC LIMIT 1''',
                        (lease['id'], midnight)).fetchone()
                    # Cumulative deltas telescope: no need to scan every report today.
                    amount = latest['rx_total']+latest['tx_total']
                    if predecessor:
                        amount -= predecessor['rx_total']+predecessor['tx_total']
                    cid = lease['customer_id']
                    today_totals[cid] = today_totals.get(cid, 0)+amount
            usage = {c['id']: self.store._usage_in_db(db, c['id'], plans[c['plan_id']]['period_seconds'], now) for c in customers}
        rows = []
        for customer in customers:
            cid = customer['id']; plan = plans[customer['plan_id']]
            meter = usage[cid] | {'quota_bytes': plan['quota_bytes'], 'remaining_bytes': None if plan['quota_bytes'] is None else max(0, plan['quota_bytes'] - usage[cid]['used_bytes'])}
            history = [p for p in periods if p['customer_id'] == cid]
            meter['total_bytes'] = sum(p['rx_bytes'] + p['tx_bytes'] for p in history)
            readings = [r for r in reports if r['customer_id'] == cid]
            meter['today_bytes'] = today_totals.get(cid, 0)
            meter['last_report_at'] = max((r['created_at'] for r in readings), default=None)
            own_devices = [d for d in devices if d['customer_id'] == cid]
            own_grants = []
            for grant in (g for g in grants if g['customer_id'] == cid):
                policy = json.loads(grant['egress_policy'])
                source = next((s for s in sources if s['id'] == policy['source_id']), None) if policy.get('source_id') else next((s for s in sources if s['endpoint'] == grant['endpoint'] and s['relay_interface'] == grant['relay_interface']), None)
                mode = policy.get('egress_mode', 'source_proxy')
                reasons = []
                if not grant['enabled']: reasons.append('线路停用')
                if grant['expires_at'] is not None and grant['expires_at'] <= now: reasons.append('线路到期')
                local = bool(source and source['id'] in local_source_ids)
                if mode == 'source_proxy' and (not local or not proxy.get('available')):
                    reasons.append('源机代理未就绪' if local else '未接入此源机代理状态检测')
                own_grants.append({'id': grant['id'], 'name': grant['alias'], 'source_id': source['id'] if source else None,
                    'source_name': source['name'] if source else grant['alias'], 'endpoint': grant['endpoint'],
                    'enabled': bool(grant['enabled']), 'expires_at': grant['expires_at'], 'egress_mode': mode,
                    'proxy_ready': local and bool(proxy.get('available')), 'available': not reasons, 'reasons': reasons})
            reasons = []
            if customer['lifecycle'] != 'active': reasons.append('已归档' if customer['lifecycle']=='archived' else '已移至回收站')
            if not customer['enabled']: reasons.append('客户停用')
            if not plan['enabled']: reasons.append('套餐停用')
            if meter['remaining_bytes'] == 0: reasons.append('本账期额度用完')
            if not own_grants or not any(g['available'] for g in own_grants): reasons.append('没有当前可用的源网授权')
            enabled_devices = {d['id'] for d in own_devices if d['enabled']}
            available_grants = {g['id'] for g in own_grants if g['available']}
            current_leases = [l for l in leases if l['customer_id'] == cid and l['revoked_at'] is None and l['expires_at'] > now
                              and l['device_id'] in enabled_devices and l['grant_id'] in available_grants and not reasons]
            lease_ids = {l['id'] for l in current_leases}
            rates = [r for r in readings if r['latest'] == 1 and r['lease_id'] in lease_ids and
                     0 <= now-r['created_at'] <= 30 and r['elapsed'] and 0 < r['elapsed'] <= 60]
            meter['download_bytes_per_second'] = sum(r['dtx']/r['elapsed'] for r in rates) if rates else (None if current_leases else 0)
            meter['upload_bytes_per_second'] = sum(r['drx']/r['elapsed'] for r in rates) if rates else (None if current_leases else 0)
            meter['measurement_status'] = 'no_reports' if meter['last_report_at'] is None else 'fresh' if now-meter['last_report_at'] <= 30 else 'stale'
            own_changes = [r for r in changes if r['customer_id'] == cid]
            rows.append({'id': cid, 'display_name': customer['display_name'], 'created_at': customer['created_at'],
                'enabled': bool(customer['enabled']), 'lifecycle':customer['lifecycle'], 'tags':customer['tags'],
                'notes':customer['notes'], 'lifecycle_changed_at':customer['lifecycle_changed_at'],
                'plan': plan, 'usage': meter, 'grants': own_grants,
                'devices': own_devices, 'leases': current_leases, 'active_lease_count': len(current_leases),
                'usable': not reasons, 'reasons': reasons, 'version': max((c['version'] for c in own_changes), default=0),
                'revision': self.revision(customer,plan,[g for g in grants if g['customer_id']==cid]),
                'history': [{k: (json.loads(c[k]) if k in ('previous', 'current') else c[k]) for k in ('version','changed_at','previous','current')} for c in own_changes[:10]],
                'usage_periods': history[:12]})
        return {'generated_at': now, 'accounting_day': date.date().isoformat(), 'accounting_timezone': date.tzname(),
            'customers': rows, 'summary': {'customers': len(rows), 'usable': sum(r['usable'] for r in rows),
                'active_leases': sum(r['active_lease_count'] for r in rows), 'today_bytes': sum(r['usage']['today_bytes'] for r in rows),
                'total_bytes': sum(r['usage']['total_bytes'] for r in rows)},
            'measurement_note': '服务端按 WireGuard 累计计数差量计量；中继与已签名客户端均可上报，重复样本不会重复计费。上行/下行从客户视角显示；最后上报超过30秒时标记计量滞后。'}

    def update(self, customer_id, payload, *, now=None):
        now = int(time.time()) if now is None else now
        allowed = {'expected_version','expected_revision','display_name','enabled','expires_at','quota_bytes','download_bps','upload_bps','max_devices','grants','restore_enrollment','lifecycle','tags','notes'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ClientStoreError('服务变更字段无效')
        if type(payload.get('expected_version')) is not int or payload['expected_version'] < 0:
            raise ClientStoreError('缺少服务版本，请刷新后重试')
        for key in ('quota_bytes','download_bps','upload_bps'):
            value = payload.get(key)
            if key in payload and value is not None and (type(value) is not int or not 1 <= value <= 2**53-1):
                raise ClientStoreError('额度和速度须为正整数；不限使用 null')
        if 'max_devices' in payload and (type(payload['max_devices']) is not int or not 1 <= payload['max_devices'] <= 100):
            raise ClientStoreError('设备上限须为1至100')
        for key in ('enabled','restore_enrollment'):
            if key in payload and type(payload[key]) is not bool: raise ClientStoreError('开关字段须为布尔值')
        if 'expires_at' in payload and payload['expires_at'] is not None and (type(payload['expires_at']) is not int or not 0 <= payload['expires_at'] <= now+3650*86400):
            raise ClientStoreError('到期时间无效或超过十年')
        if 'display_name' in payload and (not isinstance(payload['display_name'], str) or not 1 <= len(payload['display_name'].strip()) <= 120):
            raise ClientStoreError('客户名称须为1至120字符')
        if 'lifecycle' in payload and payload['lifecycle'] not in ('active','archived','deleted'):
            raise ClientStoreError('客户归档状态无效')
        if 'tags' in payload:
            tags=payload['tags']
            if not isinstance(tags,list) or len(tags)>20 or any(not isinstance(t,str) or not 1<=len(t.strip())<=30 or any(ord(ch)<32 or ord(ch)==127 for ch in t) for t in tags):
                raise ClientStoreError('标签最多20个，每个1至30字符，不允许控制字符')
            payload=payload | {'tags':list(dict.fromkeys(t.strip() for t in tags))}
        if 'notes' in payload and (not isinstance(payload['notes'],str) or len(payload['notes'])>4000 or any(ord(ch)<32 and ch not in '\t\r\n' or ord(ch)==127 for ch in payload['notes'])):
            raise ClientStoreError('备注最多4000字符，不允许非文本控制字符')
        with self.store._lock, self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            customer = db.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone()
            if not customer: raise ClientStoreError('客户不存在')
            customer=dict(customer) | self.metadata(db,customer_id)
            lifecycle=payload.get('lifecycle',customer['lifecycle'])
            if lifecycle!='active' and payload.get('enabled') is True:
                raise ClientStoreError('请先从归档或回收站恢复客户，再启用服务')
            version = db.execute('SELECT COALESCE(MAX(version),0) FROM commercial_service_changes WHERE customer_id=?', (customer_id,)).fetchone()[0]
            if version != payload['expected_version']: raise ClientStoreError('服务已被其他操作更新，请刷新后重试')
            plan = dict(db.execute('SELECT * FROM plans WHERE id=?', (customer['plan_id'],)).fetchone())
            grants = {g['id']: dict(g) for g in db.execute('SELECT * FROM line_grants WHERE customer_id=?', (customer_id,))}
            if payload.get('expected_revision') != self.revision(customer,plan,grants.values()):
                raise ClientStoreError('客户或线路已被其他操作更新，请刷新后重试')
            if 'max_devices' in payload:
                count = db.execute('SELECT COUNT(*) FROM devices WHERE customer_id=? AND enabled=1',(customer_id,)).fetchone()[0]
                if count > payload['max_devices']: raise ClientStoreError('请先停用多余设备，再降低设备上限；不会自动删除绑定')
            changes = payload.get('grants', [])
            if not isinstance(changes, list) or len(changes) > len(grants): raise ClientStoreError('线路选择无效')
            seen = set(); policies = {}
            sources = [json.loads(s['configuration']) for s in db.execute('SELECT configuration FROM sources')]
            for change in changes:
                if not isinstance(change, dict) or set(change) != {'id','enabled','egress_mode'} or type(change.get('enabled')) is not bool:
                    raise ClientStoreError('线路变更字段无效')
                gid = change['id']
                if not isinstance(gid, str) or gid not in grants or gid in seen: raise ClientStoreError('线路不属于此客户或重复')
                seen.add(gid); grant = grants[gid]
                if change['enabled'] and json.loads(grant['egress_policy']).get('migration_retired'):
                    raise ClientStoreError('迁移历史线路不能重新启用，请更换当前线路的源网')
                mode = change['egress_mode']
                current_mode = json.loads(grant['egress_policy']).get('egress_mode','source_proxy')
                if mode not in ('physical','source_proxy'): raise ClientStoreError('出口模式无效')
                if mode != current_mode or (mode == 'source_proxy' and change['enabled'] and not grant['enabled']):
                    source = next((s for s in sources if s['endpoint'] == grant['endpoint'] and s['relay_interface'] == grant['relay_interface']), None)
                    if not source: raise ClientStoreError('该线路未登记源网，请先登记再修改出口')
                    interface = validate_interface(source.get('proxy_interface','Meta') if mode == 'source_proxy' else source['egress_interface'])
                    egress = validate_egress(mode, source.get('egress_gateway','') if mode == 'physical' else '', grant['dns'])
                    if interface == grant['relay_interface']: raise ClientStoreError('出口与中继接口冲突')
                    if mode == 'physical' and source.get('egress_mode') != 'physical':
                        raise ClientStoreError('源网尚未登记物理接口/网关，请先在商业出口配置中保存物理配置')
                    if mode == 'source_proxy':
                        from .commercial_proxy import require_proxy, source_is_local
                        if not source_is_local(grant): raise ClientStoreError('该源网未接入代理状态检测')
                        require_proxy(interface)
                    policies[gid] = (interface, egress)
            if payload.get('restore_enrollment'):
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscription_addresses'").fetchone():
                    raise ClientStoreError('原开户地址未留存，不能原址恢复')
                token = db.execute('''SELECT e.* FROM enrollment_tokens e JOIN subscription_addresses a ON a.digest=e.digest WHERE a.customer_id=?''', (customer_id,)).fetchone()
                if not token or token['used_at'] is not None: raise ClientStoreError('只能恢复已留存且尚未兑换的原开户地址')
                if lifecycle!='active' or not payload.get('enabled',bool(customer['enabled'])): raise ClientStoreError('停用客户不能恢复开户地址')
                count = db.execute('SELECT COUNT(*) FROM devices WHERE customer_id=? AND enabled=1', (customer_id,)).fetchone()[0]
                if count >= payload.get('max_devices',plan['max_devices']): raise ClientStoreError('绑定设备已达上限')
                db.execute('UPDATE enrollment_tokens SET expires_at=? WHERE digest=?', (now+86400, token['digest']))
            previous = {'plan': plan, 'enabled': bool(customer['enabled']), 'display_name': customer['display_name'],
                        **{k:customer[k] for k in ('lifecycle','tags','notes','lifecycle_changed_at')},
                        'grants': [{k:g[k] for k in ('id','enabled','expires_at','egress_interface','egress_policy')} for g in grants.values()]}
            edited = any(key in payload and payload[key] != plan[key] for key in ('quota_bytes','download_bps','upload_bps','max_devices'))
            if edited:
                plan = plan | {k:payload[k] for k in ('quota_bytes','download_bps','upload_bps','max_devices') if k in payload}
                plan['id'] = self.store._id('plan'); plan['created_at'] = now
                db.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?)', tuple(plan[k] for k in ('id','name','download_bps','upload_bps','quota_bytes','period_seconds','max_devices','lease_seconds','enabled','created_at')))
            enabled = lifecycle=='active' and payload.get('enabled',bool(customer['enabled']))
            metadata={k:payload.get(k,customer[k]) for k in ('lifecycle','tags','notes')}
            changed_at=now if lifecycle!=customer['lifecycle'] else customer['lifecycle_changed_at']
            db.execute('''INSERT INTO commercial_customer_metadata VALUES(?,?,?,?,?)
                ON CONFLICT(customer_id) DO UPDATE SET lifecycle=excluded.lifecycle,tags=excluded.tags,
                notes=excluded.notes,changed_at=excluded.changed_at''',
                (customer_id,lifecycle,json.dumps(metadata['tags'],ensure_ascii=False),metadata['notes'],changed_at))
            db.execute('UPDATE customers SET display_name=?,plan_id=?,enabled=?,revoked_at=? WHERE id=?',
                       (payload.get('display_name',customer['display_name']).strip(), plan['id'], int(enabled), None if enabled else now, customer_id))
            if 'expires_at' in payload:
                db.execute('UPDATE line_grants SET expires_at=? WHERE customer_id=?', (payload['expires_at'], customer_id))
            invalidated = set()
            for change in changes:
                gid = change['id']
                db.execute('UPDATE line_grants SET enabled=? WHERE id=?', (int(change['enabled']),gid))
                if not change['enabled'] or gid in policies: invalidated.add(gid)
                if gid in policies:
                    interface, egress = policies[gid]
                    db.execute('UPDATE line_grants SET egress_interface=?,egress_policy=?,dns=?,allowed_ips=? WHERE id=?',
                        (interface,json.dumps(json.loads(grants[gid]['egress_policy']) | {k:egress[k] for k in ('egress_mode','egress_gateway')} | {'require_source_proxy':egress['egress_mode']=='source_proxy'}),
                         egress['dns'],'0.0.0.0/1,128.0.0.0/1',gid))
            usage = self.store._usage_in_db(db,customer_id,plan['period_seconds'],now)
            revoke_all = not enabled or (plan['quota_bytes'] is not None and usage['used_bytes'] >= plan['quota_bytes']) or \
                         ('expires_at' in payload and payload['expires_at'] is not None and payload['expires_at'] <= now)
            revoked = 0
            if revoke_all:
                revoked = db.execute('UPDATE leases SET revoked_at=? WHERE customer_id=? AND revoked_at IS NULL', (now,customer_id)).rowcount
            else:
                for gid in invalidated:
                    revoked += db.execute('UPDATE leases SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL',(now,gid)).rowcount
                if 'expires_at' in payload and payload['expires_at'] is not None:
                    db.execute('UPDATE leases SET expires_at=MIN(expires_at,?) WHERE customer_id=? AND revoked_at IS NULL',(payload['expires_at'],customer_id))
                if 'max_devices' in payload and payload['max_devices'] < previous['plan']['max_devices']:
                    # Existing bindings remain visible; reduction invalidates old
                    # sessions but must not silently delete device identities.
                    revoked += db.execute('UPDATE leases SET revoked_at=? WHERE customer_id=? AND revoked_at IS NULL',(now,customer_id)).rowcount
            current = {'plan': plan, 'enabled': enabled, 'display_name':payload.get('display_name',customer['display_name']).strip(),
                **metadata,'lifecycle_changed_at':changed_at,
                'grants':[dict(g) for g in db.execute('SELECT id,enabled,expires_at,egress_interface,egress_policy FROM line_grants WHERE customer_id=?',(customer_id,))]}
            db.execute('INSERT INTO commercial_service_changes VALUES(?,?,?,?,?,?)',(self.store._id('change'),customer_id,version+1,now,json.dumps(previous),json.dumps(current)))
        return {'ok':True,'customer_id':customer_id,'version':version+1,'subscription_address_changed':False,
                'usage_reset':False,'revoked_leases':revoked,'reconnect_required':bool(revoked),
                'enrollment_restored':bool(payload.get('restore_enrollment'))}
