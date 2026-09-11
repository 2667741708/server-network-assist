from __future__ import annotations

import argparse
import os
from pathlib import Path

from aiohttp import web
from cryptography.fernet import Fernet

from . import __version__
from .app import create_app
from .legacy import initialize
from .ssh_import import import_aliases


def init_data(data: Path, master_key: Path) -> None:
    if (data / "credentials.json").exists():
        raise SystemExit(f"Data directory is already initialized: {data}")
    if master_key.exists():
        raise SystemExit(f"Master key already exists: {master_key}")
    initialize(data)
    master_key.parent.mkdir(parents=True, exist_ok=True)
    master_key.write_bytes(Fernet.generate_key() + b"\n")
    master_key.chmod(0o600)
    print(f"Vault key created: {master_key}")
    print(f"One-time credentials: {data / 'initial-login.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="server-network-assist")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a new local data directory")
    serve = commands.add_parser("serve", help="run the web console")
    import_ssh = commands.add_parser("import-ssh", help="import explicit aliases from an OpenSSH config")
    for command in (init, serve):
        command.add_argument("--data", type=Path, default=Path("/var/lib/server-network-assist"))
        command.add_argument("--master-key", type=Path)
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=9180)
    serve.add_argument("--origin", default=os.environ.get("PANEL_ORIGIN", ""))
    import_ssh.add_argument("--data", type=Path, default=Path("/var/lib/server-network-assist"))
    import_ssh.add_argument("--master-key", type=Path)
    import_ssh.add_argument("--config", type=Path, default=Path.home() / ".ssh" / "config")
    import_ssh.add_argument("--known-hosts", type=Path, default=Path.home() / ".ssh" / "known_hosts")
    import_ssh.add_argument("--group", default="SSH 组网")
    import_ssh.add_argument("--alias", action="append", required=True, dest="aliases")
    args = parser.parse_args()
    key = args.master_key or args.data / "master.key"
    if args.command == "init":
        init_data(args.data, key)
        return
    if args.command == "import-ssh":
        if not (args.data / "credentials.json").is_file() or not key.is_file():
            raise SystemExit("Data directory is not initialized; run `server-network-assist init` first")
        from .app import State
        imported = import_aliases(State(args.data, key), args.config, args.aliases,
                                  args.group, args.known_hosts)
        for host in imported:
            print(f"Imported {host['name']}: {host['username']}@{host['address']}:{host['port']}")
        return
    if not (args.data / "credentials.json").is_file() or not key.is_file():
        raise SystemExit("Data directory is not initialized; run `server-network-assist init` first")
    if args.bind not in ("127.0.0.1", "::1", "localhost") and not args.origin:
        raise SystemExit("Refusing a non-loopback bind without --origin (use the exact HTTPS public origin)")
    if args.origin:
        os.environ["PANEL_ORIGIN"] = args.origin.rstrip("/")
    web.run_app(create_app(args.data, key), host=args.bind, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
