"""Issue one physical and one source-proxy subscription for D321.

Run only on the authorized C201-4090 commercial source. Credentials are read
there and enrollment URLs are written to a mode-0600 file. Standard output
contains no credential, token, enrollment URL, private key, or management key.
"""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://10.20.32.13:9182")
    parser.add_argument("--source-endpoint", default="10.20.32.13:51910")
    args = parser.parse_args()

    admin_base = "http://10.201.250.1:9180/subscription-admin/"
    origin = "http://10.201.250.1:9180"
    credentials = json.loads(Path(
        "/home/a/.local/share/server-network-assist-commercial/data/initial-login.json"
    ).read_text(encoding="utf-8"))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )

    def call(path, payload=None, csrf=""):
        request = urllib.request.Request(
            admin_base + "api/" + path,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={
                "Origin": origin,
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf,
            },
        )
        with opener.open(request, timeout=25) as response:
            return json.load(response)

    session = call("login", {
        "username": credentials["username"],
        "key": credentials["key"],
        "remember": False,
    })
    try:
        service = call("client-service")
        sources = [row for row in service.get("sources", [])
                   if row.get("endpoint") == args.source_endpoint and row.get("enabled", True)]
        if len(sources) != 1:
            raise RuntimeError("Expected exactly one enabled D321 source endpoint")
        source = sources[0]
        proxy = service.get("source_proxy", {})
        if not proxy.get("available"):
            raise RuntimeError("Source proxy is unavailable; no subscriptions were issued")

        expires_at = int(time.time()) + 30 * 24 * 60 * 60
        issued = []
        specifications = (
            ("D321 物理出口 20260919", "physical"),
            ("D321 源机代理出口 20260919", "source_proxy"),
        )
        for name, mode in specifications:
            existing = [row for row in service.get("customers", [])
                        if row.get("display_name") == name]
            if len(existing) > 1:
                raise RuntimeError("More than one customer has the requested D321 name")
            payload = {
                "action": "subscription-generate",
                "name": name,
                "quota_gb": None,
                "source_ids": [source["id"]],
                "proxy_source_ids": [source["id"]] if mode == "source_proxy" else [],
                "base_url": args.base_url,
                "download_bps": None,
                "upload_bps": None,
                "expires_at": expires_at,
            }
            if existing:
                status = next((row for row in service.get("subscription_addresses", [])
                               if row.get("customer_id") == existing[0]["id"]), {})
                if status.get("status") != "ready":
                    raise RuntimeError("Existing D321 enrollment address is no longer redeemable")
                result = call("client-service/action", {
                    "action": "subscription-view", "id": existing[0]["id"],
                }, session["csrf"])
            else:
                result = call("client-service/action", payload, session["csrf"])
            issued.append({
                "name": name,
                "mode": mode,
                "url": result["url"],
                "customer_id": result["customer_id"],
                "expires_at": result.get("expires_at", expires_at),
            })

        persisted = call("client-service")
        dashboard = call("client-service/dashboard")
        for row in issued:
            customer = next(item for item in dashboard["customers"]
                            if item["id"] == row["customer_id"])
            grants = [item for item in persisted["grants"]
                      if item["customer_id"] == row["customer_id"]]
            if len(grants) != 1:
                raise RuntimeError("Issued customer has an unexpected grant count")
            policy = json.loads(grants[0]["egress_policy"])
            if policy.get("egress_mode") != row["mode"]:
                raise RuntimeError("Persisted egress mode does not match the request")
            if customer["plan"]["quota_bytes"] is not None:
                raise RuntimeError("Issued customer is not unlimited traffic")
            if customer["plan"]["download_bps"] is not None or customer["plan"]["upload_bps"] is not None:
                raise RuntimeError("Issued customer is not unlimited speed")

        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps({"subscriptions": issued}, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(args.output)
        os.chmod(args.output, 0o600)
        print(json.dumps({
            "ok": True,
            "count": len(issued),
            "modes": [row["mode"] for row in issued],
            "expires_at": expires_at,
            "unlimited_traffic": True,
            "unlimited_speed": True,
            "secret_output_written": True,
        }))
    finally:
        call("logout", {}, session["csrf"])


if __name__ == "__main__":
    main()
