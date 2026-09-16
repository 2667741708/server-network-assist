"""Administrator CLI for the commercial customer network service.

The command intentionally emits machine-readable JSON.  Secret-bearing fields are
redacted at the presentation boundary so routine terminal capture and support logs
cannot disclose bearer tokens or private keys.
"""
from __future__ import annotations

import argparse
import json
import os
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


def _store_path(value: str | None = None) -> Path:
    configured = value or os.environ.get('SNA_CLIENT_SERVICE_DB')
    return Path(configured) if configured else Path('data/client-service.sqlite3')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='server-network-assist-service-admin',
        description='管理客户、线路、套餐、短期租约、撤销和计量数据',
    )
    parser.add_argument('--database', help='商业服务 SQLite 数据库路径；也可用 SNA_CLIENT_SERVICE_DB')
    groups = parser.add_subparsers(dest='resource', required=True)

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
    create_line.add_argument('--expires-at', type=int)

    customer = groups.add_parser('customer', help='客户开户与状态管理')
    customer_sub = customer.add_subparsers(dest='action', required=True)
    customer_sub.add_parser('list', help='列出客户')
    create_customer = customer_sub.add_parser('create', help='客户开户')
    create_customer.add_argument('--id', required=True)
    create_customer.add_argument('--name', required=True)
    create_customer.add_argument('--plan', required=True)
    enrollment = customer_sub.add_parser('enrollment-token', help='生成一次性设备开户注册令牌')
    enrollment.add_argument('customer_id')
    enrollment.add_argument('--ttl', type=_positive_int, default=900)
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
    issue.add_argument('--expires-at', type=int)
    issue.add_argument('--note')
    list_grants = grant_sub.add_parser('list', help='列出客户授权')
    list_grants.add_argument('--customer')
    revoke = grant_sub.add_parser('revoke', help='撤销授权并立即终止相关租约')
    revoke.add_argument('grant_id')

    device = groups.add_parser('device', help='绑定设备查询')
    device_sub = device.add_subparsers(dest='action', required=True)
    list_devices = device_sub.add_parser('list')
    list_devices.add_argument('--customer')

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
    if args.resource == 'plan':
        if args.action == 'list':
            return store.list_plans()
        return store.create_plan(
            args.name, plan_id=args.id, quota_bytes=args.quota_bytes,
            download_bps=args.download_bps, upload_bps=args.upload_bps,
            max_devices=args.max_devices, lease_seconds=args.lease_seconds,
            period_seconds=args.period_seconds,
        )
    if args.resource == 'line':
        if args.action == 'list':
            return store.list_grants()
        return store.grant_line(
            args.customer, args.alias, args.tunnel, args.endpoint,
            relay_public_key=args.relay_public_key, allocated_address=args.allocated_address,
            dns=args.dns, allowed_ips=args.allowed_ips, mtu=args.mtu,
            relay_interface=args.relay_interface, egress_interface=args.egress_interface,
            grant_id=args.id, expires_at=args.expires_at,
        )
    if args.resource == 'customer':
        if args.action == 'list':
            return store.list_customers()
        if args.action == 'create':
            return store.create_customer(args.name, args.plan, customer_id=args.id)
        if args.action == 'enrollment-token':
            token = store.create_enrollment_token(args.customer_id, ttl=args.ttl)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.output.with_suffix(args.output.suffix + '.tmp')
            temporary.write_text(token + '\n', encoding='utf-8')
            if os.name != 'nt':
                temporary.chmod(0o600)
            temporary.replace(args.output)
            return {'output': str(args.output), 'token': token}
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
                expires_at=args.expires_at,
            )
        if args.action == 'list':
            return store.list_grants(customer_id=args.customer)
        store.revoke('grant', args.grant_id)
        return {'grant_id': args.grant_id, 'revoked': True}
    if args.resource == 'device':
        return store.list_devices(customer_id=args.customer)
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
