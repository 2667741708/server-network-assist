"""Independent local expiry executor for a customer relay.

This process never contacts the control plane.  Run it as a separate service
or scheduled task from the relay synchronizer so a network outage or a crashed
sync process cannot keep an already-known lease active.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import time

from .client_relay import RelayManager
from .windows_client_relay import WindowsRelayManager


def manager_for(platform_name: str, data: Path):
    if platform_name == "windows":
        return WindowsRelayManager(data)
    if platform_name == "linux":
        return RelayManager(data)
    return WindowsRelayManager(data) if platform.system() == "Windows" else RelayManager(data)


def run(manager, *, once: bool, interval: int) -> int:
    while True:
        # Errors are fatal for this worker.  Continuing after corrupt state or
        # an unverified cleanup would falsely claim that expiry is enforced.
        manager.expire()
        if once:
            return 0
        time.sleep(interval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--platform", choices=("auto", "linux", "windows"), default="auto")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args(argv)
    if not 1 <= args.interval <= 3600:
        raise ValueError("interval must be between 1 and 3600 seconds")
    manager = manager_for(args.platform, args.data)
    result = run(manager, once=args.once, interval=args.interval)
    if args.once:
        print(json.dumps({"ok": True, "mode": "local-expiry"}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
