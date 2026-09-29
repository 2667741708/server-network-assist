"""Administrator CLI for the commercial customer network service.

The command intentionally emits machine-readable JSON.  Secret-bearing fields are
redacted at the presentation boundary so routine terminal capture and support logs
cannot disclose bearer tokens or private keys.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence, TextIO


SECRET_FIELDS = frozenset({
    'access_token', 'api_key', 'authorization', 'bearer_token', 'private_key',
    'refresh_token', 'secret', 'subscription_token', 'token',
})


def _redact(value: Any) -> Any:
    """Return a JSON-safe copy with credentials removed from every nesting level."""
    if isinstance(value, dict):
        return {
            str(key): ('[REDACTED]' if _secret_field(str(key)) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _secret_field(key: str) -> bool:
    normalized = key.lower()
    return (normalized in SECRET_FIELDS or normalized.endswith('_token')
            or normalized.endswith('_private_key'))


def _json(output: TextIO, value: Any) -> None:
    print(json.dumps(_redact(value), ensure_ascii=False, sort_keys=True), file=output)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError('必须是正整数')
    return parsed


def _enrollment_ttl(value: str) -> int | None:
    if value.strip().lower() == 'never':
        return None
    match = re.fullmatch(r'([1-9][0-9]*)([smhd]?)', value.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError('令牌期限须为秒数、如 30d 的时长，或 never')
    return int(match.group(1)) * {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}[match.group(2)]


def _store_path(value: str | None = None) -> Path:
    configured = value or os.environ.get('SNA_CLIENT_SERVICE_DB')
    return Path(configured) if configured else Path('data/client-service.sqlite3')


def _write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(value + '\n', encoding='utf-8')
    if os.name != 'nt':
        temporary.chmod(0o600)
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='server-network-assist-service-admin',
        description='管理客户、线路、套餐、短期租约、撤销和计量数据',
    )
    parser.add_argument('--database', help='商业服务 SQLite 数据库路径；也可用 SNA_CLIENT_SERVICE_DB')
    groups = parser.add_subparsers(dest='resource', required=True)
    source = groups.add_parser('source', help='源网节点配置')
    source_sub = source.add_subparsers(dest='action', required=True)
    source_sub.add_parser('list')
    source_save = source_sub.add_parser('save')
    source_save.add_argument('--file', type=Path, required=True)
    subscription = groups.add_parser('subscription', help='订阅签发')
    subscription_sub = subscription.add_subparsers(dest='action', required=True)
    generate = subscription_sub.add_parser('generate')
    generate.add_argument('--name', required=True)
    generate.add_argument('--quota-gb', required=True, help='1–100000，或 unlimited')
    generate.add_argument('--validity-days', type=int, default=30, help='1–3650 天')
    generate.add_argument('--start-on-enrollment', action='store_true',
                          help='服务有效期从首次成功导入时开始')
    generate.add_argument('--required-grants', type=_positive_int,
                          help='首次导入前必须配置的授权线路数')
    generate.add_argument('--access-mode', choices=('wireguard', 'public_proxy'), default='wireguard')
    generate.add_argument('--source', action='append', default=[])
    generate.add_argument('--group', help='Existing customer group ID; grouping never grants a line')
    generate.add_argument('--egress', action='append', choices=('source_physical', 'source_proxy'),
                          help='Repeat to issue both administrator-declared source egress choices')
    generate.add_argument('--base-url', required=True)
    generate.add_argument('--download-bps', type=_positive_int, default=50000000)
    generate.add_argument('--upload-bps', type=_positive_int, default=10000000)
    generate.add_argument('--unlimited-speed', action='store_true', help='不设置客户上传或下载限速')
    generate.add_argument('--unlimited-download', action='store_true', help='仅下载不限速')
    generate.add_argument('--unlimited-upload', action='store_true', help='仅上传不限速')
    generate.add_argument('--output', type=Path, required=True)
    existing = subscription_sub.add_parser('issue-existing', help='为已有套餐、分组和线路的客户签发一次性订阅地址')
    existing.add_argument('--customer', required=True)
    existing.add_argument('--base-url', required=True)
    existing.add_argument('--ttl', type=_positive_int, default=900)
    existing.add_argument('--output', type=Path, required=True)

    plan = groups.add_parser('plan', help='套餐管理')
    plan_sub = plan.add_subparsers(dest='action', required=True)
    plan_sub.add_parser('list', help='列出套餐')
    create_plan = plan_sub.add_parser('create', help='创建套餐')
    create_plan.add_argument('--id', required=True)
    create_plan.add_argument('--name', required=True)
    create_plan.add_argument('--quota-bytes', type=_positive_int)
    create_plan.add_argument('--download-bps', type=_positive_int)
    create_plan.add_argument('--upload-bps', type=_positive_int)
    create_plan.add_argument('--max-devices', type=_positive_int, default=1)
    create_plan.add_argument('--lease-seconds', type=_positive_int, required=True)
    create_plan.add_argument('--period-seconds', type=_positive_int, default=30 * 86400)

    group = groups.add_parser('group', help='客户分组元数据，不自动授权线路')
    group_sub = group.add_subparsers(dest='action', required=True)
    group_sub.add_parser('list')
    create_group = group_sub.add_parser('create')
    create_group.add_argument('--id')
    create_group.add_argument('--name', required=True)
    assign_group = group_sub.add_parser('assign')
    assign_group.add_argument('--customer', required=True)
    assign_group.add_argument('--group', required=True)

    line = groups.add_parser('line', help='出口线路管理')
    line_sub = line.add_subparsers(dest='action', required=True)
    line_sub.add_parser('list', help='列出线路')
    create_line = line_sub.add_parser('create', help='创建线路')
    create_line.add_argument('--id')
    create_line.add_argument('--customer', required=True)
    create_line.add_argument('--name', required=True, dest='alias')
    create_line.add_argument('--endpoint', required=True)
    create_line.add_argument('--tunnel', required=True)
    create_line.add_argument('--relay-public-key', default='')
    create_line.add_argument('--allocated-address', default='')
    create_line.add_argument('--dns', default='1.1.1.1')
    create_line.add_argument('--allowed-ips', default='0.0.0.0/0,::/0')
    create_line.add_argument('--mtu', type=_positive_int, default=1420)
    create_line.add_argument('--relay-interface', default='wg0')
    create_line.add_argument('--egress-interface', default='')
    create_line.add_argument('--egress-policy', choices=('source_physical', 'source_proxy'))
    create_line.add_argument('--transport-policy', choices=('campus_physical', 'physical_source_reachable', 'public'))
    create_line.add_argument('--expires-at', type=int)

    customer = groups.add_parser('customer', help='客户开户与状态管理')
    customer_sub = customer.add_subparsers(dest='action', required=True)
    customer_sub.add_parser('list', help='列出客户')
    create_customer = customer_sub.add_parser('create', help='客户开户')
    create_customer.add_argument('--id', required=True)
    create_customer.add_argument('--name', required=True)
    create_customer.add_argument('--plan', required=True)
    create_customer.add_argument('--group')
    customer_term = customer_sub.add_parser('term', help='查询首次导入起算的服务期限')
    customer_term.add_argument('customer_id')
    enrollment = customer_sub.add_parser('enrollment-token', help='生成一次性设备开户注册令牌')
    enrollment.add_argument('customer_id')
    enrollment.add_argument('--ttl', type=_enrollment_ttl, default=900,
                            help='自定义秒数或 30d/12h 等时长；never 表示一次性令牌不自动过期')
    enrollment.add_argument('--output', type=Path, required=True,
                            help='令牌写入此权限受限文件；不会显示在终端')
    for action, help_text in (
        ('suspend', '暂停客户并立即撤销活动租约'),
        ('resume', '恢复客户'),
        ('revoke', '永久撤销客户及其活动租约'),
    ):
        command = customer_sub.add_parser(action, help=help_text)
        command.add_argument('customer_id')
        command.add_argument('--reason')

    grant = groups.add_parser('grant', help='客户线路授权管理')
    grant_sub = grant.add_subparsers(dest='action', required=True)
    issue = grant_sub.add_parser('issue', help='向客户授予套餐和线路')
    issue.add_argument('--customer', required=True)
    issue.add_argument('--name', required=True, dest='alias')
    issue.add_argument('--endpoint', required=True)
    issue.add_argument('--tunnel', required=True)
    issue.add_argument('--relay-public-key', default='')
    issue.add_argument('--allocated-address', default='')
    issue.add_argument('--dns', default='1.1.1.1')
    issue.add_argument('--allowed-ips', default='0.0.0.0/0,::/0')
    issue.add_argument('--mtu', type=_positive_int, default=1420)
    issue.add_argument('--relay-interface', default='wg0')
    issue.add_argument('--egress-interface', default='')
    issue.add_argument('--egress-policy', choices=('source_physical', 'source_proxy'),
                       help='租约出口策略；Cloud 托管代理使用 source_proxy')
    issue.add_argument('--transport-policy', choices=('campus_physical', 'physical_source_reachable', 'public'),
                       help='客户端到接入节点的可达性要求；Cloud 公网接入使用 public')
    issue.add_argument('--expires-at', type=int)
    issue.add_argument('--note')
    list_grants = grant_sub.add_parser('list', help='列出客户授权')
    list_grants.add_argument('--customer')
    revoke = grant_sub.add_parser('revoke', help='撤销授权并立即终止相关租约')
    revoke.add_argument('grant_id')
    for action in ('enable', 'disable'):
        toggle = grant_sub.add_parser(action, help='启用或暂停一条线路授权')
        toggle.add_argument('grant_id')

    device = groups.add_parser('device', help='绑定设备查询')
    device_sub = device.add_subparsers(dest='action', required=True)
    list_devices = device_sub.add_parser('list')
    list_devices.add_argument('--customer')
    for action in ('enable', 'disable'):
        toggle = device_sub.add_parser(action, help='启用或暂停一台设备')
        toggle.add_argument('device_id')

    lease = groups.add_parser('lease', help='短期租约查询与撤销')
    lease_sub = lease.add_subparsers(dest='action', required=True)
    list_leases = lease_sub.add_parser('list')
    list_leases.add_argument('--customer')
    list_leases.add_argument('--active-only', action='store_true')
    revoke_lease = lease_sub.add_parser('revoke')
    revoke_lease.add_argument('lease_id')

    usage = groups.add_parser('usage', help='真实计量查询')
    usage_sub = usage.add_subparsers(dest='action', required=True)
    show_usage = usage_sub.add_parser('show')
    show_usage.add_argument('--customer')
    return parser


def _dispatch(store: Any, args: argparse.Namespace) -> Any:
    """Dispatch after argument validation. ClientStore supplies the persistence API."""
    if args.resource == 'source':
        if args.action == 'list':
            return store.list_sources()
        return store.save_source(json.loads(args.file.read_text(encoding='utf-8-sig')))
    if args.resource == 'subscription':
        from .client_online import parse_enrollment_url
        base, _ = parse_enrollment_url(args.base_url.rstrip('/') + '/#enroll=validation-token-1234')
        if args.action == 'issue-existing':
            import time
            customer = store.customer(args.customer)
            now = int(time.time())
            grants = [row for row in store.list_grants(customer_id=args.customer)
                      if row['enabled'] and row.get('revoked_at') is None and
                      (row.get('expires_at') is None or row['expires_at'] > now)]
            if not grants:
                raise ValueError('客户没有有效线路授权，不能签发可连接的订阅地址')
            token = store.create_enrollment_token(args.customer, ttl=args.ttl)
            _write_secret(args.output, base + '/#enroll=' + token)
            return {'customer_id': args.customer, 'plan_id': customer['plan_id'],
                    'group_id': customer.get('group_id'), 'grant_ids': [row['id'] for row in grants],
                    'output': str(args.output)}
        result = store.generate_monthly_subscription(args.name,
            None if args.quota_gb == 'unlimited' else int(args.quota_gb), args.source,
            access_mode=args.access_mode, egress_modes=args.egress, group_id=args.group,
            download_bps=None if args.unlimited_speed or args.unlimited_download else args.download_bps,
            upload_bps=None if args.unlimited_speed or args.unlimited_upload else args.upload_bps,
            validity_days=args.validity_days,
            start_on_enrollment=args.start_on_enrollment,
            required_grants=args.required_grants)
        token = result.pop('token')
        _write_secret(args.output, base + '/#enroll=' + token)
        return result | {'output': str(args.output)}
    if args.resource == 'plan':
        if args.action == 'list':
            return store.list_plans()
        return store.create_plan(
            args.name, plan_id=args.id, quota_bytes=args.quota_bytes,
            download_bps=args.download_bps, upload_bps=args.upload_bps,
            max_devices=args.max_devices, lease_seconds=args.lease_seconds,
            period_seconds=args.period_seconds,
        )
    if args.resource == 'group':
        if args.action == 'list':
            return store.list_groups()
        if args.action == 'create':
            return store.create_group(args.name, group_id=args.id)
        return store.set_customer_group(args.customer, args.group)
    if args.resource == 'line':
        if args.action == 'list':
            return store.list_grants()
        return store.grant_line(
            args.customer, args.alias, args.tunnel, args.endpoint,
            relay_public_key=args.relay_public_key, allocated_address=args.allocated_address,
            dns=args.dns, allowed_ips=args.allowed_ips, mtu=args.mtu,
            relay_interface=args.relay_interface, egress_interface=args.egress_interface,
            egress_policy=args.egress_policy, transport_policy=args.transport_policy,
            grant_id=args.id, expires_at=args.expires_at,
        )
    if args.resource == 'customer':
        if args.action == 'list':
            return store.list_customers()
        if args.action == 'create':
            return store.create_customer(args.name, args.plan, customer_id=args.id,
                                         group_id=args.group)
        if args.action == 'term':
            return store.customer_service_term(args.customer_id)
        if args.action == 'enrollment-token':
            token = store.create_enrollment_token(args.customer_id, ttl=args.ttl)
            _write_secret(args.output, token)
            return {'output': str(args.output)}
        if args.action == 'resume':
            store.set_customer_enabled(args.customer_id, True)
        elif args.action == 'suspend':
            store.set_customer_enabled(args.customer_id, False)
        else:
            store.revoke('customer', args.customer_id)
        return store.customer(args.customer_id)
    if args.resource == 'grant':
        if args.action == 'issue':
            return store.grant_line(
                args.customer, args.alias, args.tunnel, args.endpoint,
                relay_public_key=args.relay_public_key, allocated_address=args.allocated_address,
                dns=args.dns, allowed_ips=args.allowed_ips, mtu=args.mtu,
                relay_interface=args.relay_interface, egress_interface=args.egress_interface,
                egress_policy=args.egress_policy, transport_policy=args.transport_policy,
                expires_at=args.expires_at,
            )
        if args.action == 'list':
            return store.list_grants(customer_id=args.customer)
        if args.action in ('enable', 'disable'):
            store.set_grant_enabled(args.grant_id, args.action == 'enable')
            return store.line_grant(args.grant_id)
        store.revoke('grant', args.grant_id)
        return {'grant_id': args.grant_id, 'revoked': True}
    if args.resource == 'device':
        if args.action == 'list':
            return store.list_devices(customer_id=args.customer)
        store.set_device_enabled(args.device_id, args.action == 'enable')
        return store.device(args.device_id)
    if args.resource == 'lease':
        if args.action == 'list':
            rows = store.list_leases(customer_id=args.customer)
            if args.active_only:
                rows = [row for row in rows if row.get('revoked_at') is None]
            return rows
        store.revoke('lease', args.lease_id)
        return {'lease_id': args.lease_id, 'revoked': True}
    if not args.customer:
        raise ValueError('用量查询必须提供 --customer')
    return store.usage(args.customer)


def run(argv: Sequence[str] | None = None, *, store: Any = None,
        output: TextIO = sys.stdout, error: TextIO = sys.stderr) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if store is None:
        from .client_store import ClientStore
        store = ClientStore(_store_path(args.database))
    try:
        result = _dispatch(store, args)
    except (KeyError, ValueError) as exc:
        _json(error, {'ok': False, 'error': str(exc)})
        return 2
    _json(output, {'ok': True, 'result': result})
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == '__main__':
    main()
