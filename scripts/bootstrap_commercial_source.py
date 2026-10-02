#!/usr/bin/env python3
"""Deploy a dedicated Linux commercial WireGuard source without changing fleet tunnels."""
import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import sys


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--owner', default='a')
    parser.add_argument('--endpoint', default='10.20.32.13:51910')
    parser.add_argument('--interface', default='sna-commercial')
    parser.add_argument('--pool', default='10.213.40.0/24')
    parser.add_argument('--port', type=int, default=51910)
    parser.add_argument('--egress-mode', choices=('source_proxy', 'physical'), default='physical')
    parser.add_argument('--egress-interface', default='')
    parser.add_argument('--egress-gateway', default='')
    parser.add_argument('--dns', default='223.5.5.5,1.1.1.1')
    parser.add_argument('--data', type=Path, default=Path('/home/a/.local/share/server-network-assist-commercial/data'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Run with sudo to deploy WireGuard, forwarding and traffic control')
    if args.interface != 'sna-commercial':
        raise ValueError('This bootstrap reserves the dedicated sna-commercial interface')
    pool = ipaddress.ip_network(args.pool, strict=True)
    account = pwd.getpwnam(args.owner)
    config = Path('/etc/wireguard') / (args.interface + '.conf')
    if config.exists():
        raise RuntimeError('Dedicated configuration already exists; inspect before reinstalling')
    # Keep pip-managed dependencies separate from Debian-owned Python packages.
    runtime = Path('/opt/server-network-assist-relay/venv')
    runtime_python = runtime / 'bin/python'
    if not runtime_python.exists():
        run([sys.executable, '-m', 'venv', '--without-pip', str(runtime)])
    if Path(sys.prefix) != runtime:
        run([sys.executable, '-m', 'pip', '--python', str(runtime_python), 'install', str(args.wheel.resolve())])
        run([str(runtime_python), str(Path(__file__).resolve()), *sys.argv[1:]])
        return
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from server_network_assist.client_store import ClientStore
    from server_network_assist.client_egress import validate_egress, validate_interface
    egress = validate_egress(args.egress_mode, args.egress_gateway, args.dns)
    if args.egress_mode == 'physical' and not args.egress_interface:
        raise ValueError('Physical bootstrap requires --egress-interface')
    if args.egress_interface:
        validate_interface(args.egress_interface)
    prepared_key = args.data.parent / 'sna-commercial.key'
    key = (X25519PrivateKey.from_private_bytes(base64.b64decode(prepared_key.read_text().strip(), validate=True))
           if prepared_key.exists() else X25519PrivateKey.generate())
    private = base64.b64encode(key.private_bytes_raw()).decode()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f'[Interface]\nPrivateKey = {private}\nAddress = {next(pool.hosts())}/{pool.prefixlen}\nListenPort = {args.port}\n', encoding='utf-8')
    config.chmod(0o600)
    run(['sysctl', '-w', 'net.ipv4.ip_forward=1'])
    Path('/etc/sysctl.d/80-sna-commercial.conf').write_text('net.ipv4.ip_forward=1\n', encoding='ascii')
    run(['systemctl', 'enable', '--now', 'wg-quick@' + args.interface])
    route = json.loads(run(['ip','-j','route','get','1.1.1.1'], capture_output=True).stdout)[0]
    source = {'id':'c201-4090-commercial', 'name':'C201-4090 商业源网', 'endpoint':args.endpoint,
              'relay_public_key':public, 'address_pool':str(pool), 'relay_interface':args.interface,
              'egress_interface':args.egress_interface or route['dev'], **egress}
    user_env = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{account.pw_uid}')
    def user_service(action):
        run(['runuser','-u',args.owner,'--','systemctl','--user',action,'sna-commercial'], env=user_env)
    token = secrets.token_urlsafe(48)
    token_file = Path('/etc/server-network-assist-relay.token')
    token_file.write_text(token, encoding='ascii')
    token_file.chmod(0o600)
    env_file = Path(account.pw_dir) / '.config/sna-commercial.env'
    env_file.write_text("SNA_RELAY_TOKENS='" + json.dumps({args.interface:token}, separators=(',',':')) + "'\n" +
        'SNA_RELAY_CUSTOMER_SUBNET=' + str(pool) + '\nSNA_RELAY_MANAGEMENT_SUBNETS=10.201.250.0/24,10.20.0.0/16\n', encoding='ascii')
    env_file.chmod(0o600)
    os.chown(env_file, account.pw_uid, account.pw_gid)
    unit = Path(account.pw_dir) / '.config/systemd/user/sna-commercial.service'
    unit_text = unit.read_text(encoding='utf-8')
    if 'EnvironmentFile=' not in unit_text:
        unit.write_text(unit_text.replace('[Service]', '[Service]\nEnvironmentFile=' + str(env_file)), encoding='utf-8')
        os.chown(unit, account.pw_uid, account.pw_gid)
    user_service('stop')
    try:
        ClientStore(args.data / 'commercial-service.sqlite3').save_source(source)
        for path in args.data.glob('commercial-service.sqlite3*'):
            os.chown(path, account.pw_uid, account.pw_gid)
    finally:
        run(['runuser','-u',args.owner,'--','systemctl','--user','daemon-reload'], env=user_env)
        user_service('start')
    installer = Path(__file__).with_name('install_client_relay.py')
    run([sys.executable, str(installer), '--control-url','http://127.0.0.1:9182', '--token-file',str(token_file),
         '--wireguard-interface',args.interface,'--relay-id',args.interface,'--interval','10'])
    print(json.dumps({'ok':True, 'source':source}, ensure_ascii=False))


if __name__ == '__main__':
    main()
