#!/usr/bin/env bash
# Stop only the separately managed d408 sharing stack; preserve gateway peers.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
[[ $(id -u) == 0 && $(hostname) == d408-4090 ]] || {
  echo 'Run with sudo on d408-4090.' >&2
  exit 1
}
exec 8>/run/d408-stop-sharing.lock
flock -n 8 || { echo 'A stop operation is already running.' >&2; exit 1; }
units=(d408-network-boot.service wg-quick@wg-d408.service d408-mihomo-proxy.service d408-mihomo-tun.service)
backup="/var/backups/d408-network/$(date -u +%Y%m%dT%H%M%SZ)-stop-$$"
install -d -m 0700 "$backup"
systemctl show "${units[@]}" --property=Id,ActiveState,SubState,UnitFileState > "$backup/services.txt"
cp /etc/d408-network/mode "$backup/network-mode"
ip -4 rule show > "$backup/rules.txt"
ip -4 route show table all > "$backup/routes.txt"
/usr/local/sbin/d408-network direct
systemctl disable --now "${units[@]}"
[[ $(cat /etc/d408-network/mode) == direct ]]
for unit in "${units[@]}"; do
  state=$(systemctl show "$unit" --property=ActiveState --value)
  startup=$(systemctl show "$unit" --property=UnitFileState --value)
  [[ $state == inactive && $startup == disabled ]] || {
    echo "Incomplete stop: $unit state=$state startup=$startup. Backup: $backup" >&2
    exit 1
  }
done
if ip link show wg-d408 >/dev/null 2>&1; then
  echo 'The sharing interface still exists.' >&2
  exit 1
fi
if ip -4 rule show | grep -Eq '^(10005|10010|10012|10015|10020):'; then
  echo 'Owned policy rules remain; inspect before claiming recovery.' >&2
  exit 1
fi
printf 'Stopped borrowing; automatic startup disabled. Backup: %s\n' "$backup"
ip -4 route get 1.1.1.1
