#!/usr/bin/env python3
"""piholectl: manage a Pi-hole v6 through its built-in web API.

Plain Python standard library only. Run on the machine that can reach Pi-hole.

Commands:
  status                 show blocking state and today's stats
  pause [SECONDS]        pause blocking (forever if no seconds given)
  resume                 turn blocking back on
  block DOMAIN           add DOMAIN to the exact deny list
  allow DOMAIN           add DOMAIN to the exact allow list
  export                 print current blocklists in blocklists.txt format
  sync [--dry-run]       make Pi-hole's blocklists match blocklists.txt in the repo

Settings (environment variables, all optional):
  PIHOLE_URL             default http://192.168.1.2:8081
  PIHOLE_PASSWORD_FILE   default ~/.piholectl-password (holds a Pi-hole app password)
  BLOCKLISTS_URL         default: blocklists.txt on main in Klutzstapo/grok-pihole
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

PIHOLE_URL = os.environ.get("PIHOLE_URL", "http://192.168.1.2:8081").rstrip("/")
PASSWORD_FILE = os.path.expanduser(os.environ.get("PIHOLE_PASSWORD_FILE", "~/.piholectl-password"))
BLOCKLISTS_URL = os.environ.get(
    "BLOCKLISTS_URL",
    "https://raw.githubusercontent.com/Klutzstapo/grok-pihole/main/blocklists.txt",
)
SYNC_COMMENT = "managed by grok-pihole"


class Api:
    def __init__(self):
        try:
            with open(PASSWORD_FILE) as f:
                password = f.read().strip()
        except OSError:
            sys.exit(f"Can't read the Pi-hole app password from {PASSWORD_FILE}")
        res = self.call("POST", "/api/auth", {"password": password}, auth=False)
        session = res.get("session", {})
        if not session.get("valid"):
            sys.exit("Pi-hole rejected the password: " + str(session.get("message", "")))
        self.sid = session.get("sid")

    def call(self, method, path, body=None, auth=True):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(PIHOLE_URL + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if auth and self.sid:
            req.add_header("X-FTL-SID", self.sid)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                raw = r.read().decode()
        except urllib.error.HTTPError as e:
            sys.exit(f"{method} {path} failed: {e.code} {e.read().decode()[:300]}")
        except urllib.error.URLError as e:
            sys.exit(f"Can't reach Pi-hole at {PIHOLE_URL}: {e.reason}")
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {"text": raw}

    sid = None

    def logout(self):
        if self.sid:
            try:
                self.call("DELETE", "/api/auth")
            except SystemExit:
                pass


def block_lists(api):
    return [l["address"] for l in api.call("GET", "/api/lists?type=block").get("lists", [])]


def repo_lists():
    with urllib.request.urlopen(BLOCKLISTS_URL, timeout=60) as r:
        text = r.read().decode()
    urls = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line and line not in urls:
            urls.append(line)
    return urls


def cmd_status(api, args):
    b = api.call("GET", "/api/dns/blocking")
    s = api.call("GET", "/api/stats/summary")
    q = s.get("queries", {})
    g = s.get("gravity", {})
    print(f"Blocking: {b.get('blocking')}" + (f" (timer {b['timer']}s)" if b.get("timer") else ""))
    print(f"Queries today: {q.get('total')}  blocked: {q.get('blocked')}  ({q.get('percent_blocked', 0):.1f}%)")
    print(f"Domains on blocklists: {g.get('domains_being_blocked')}")


def cmd_pause(api, args):
    body = {"blocking": False, "timer": int(args[0]) if args else None}
    api.call("POST", "/api/dns/blocking", body)
    print("Blocking paused" + (f" for {args[0]} seconds" if args else ""))


def cmd_resume(api, args):
    api.call("POST", "/api/dns/blocking", {"blocking": True, "timer": None})
    print("Blocking resumed")


def add_domain(api, args, kind):
    if len(args) != 1:
        sys.exit("Give exactly one domain")
    api.call("POST", f"/api/domains/{kind}/exact", {"domain": args[0], "comment": SYNC_COMMENT})
    print(f"Added {args[0]} to the {kind} list")


def cmd_export(api, args):
    print("# Pi-hole blocklists, one address per line")
    for url in block_lists(api):
        print(url)


def cmd_sync(api, args):
    dry = "--dry-run" in args
    wanted = repo_lists()
    if not wanted:
        sys.exit("blocklists.txt has no lists, so nothing was changed (refusing to remove everything)")
    current = block_lists(api)
    to_add = [u for u in wanted if u not in current]
    to_remove = [u for u in current if u not in wanted]
    for u in to_add:
        print(("Would add " if dry else "Adding ") + u)
    for u in to_remove:
        print(("Would remove " if dry else "Removing ") + u)
    if not to_add and not to_remove:
        print("Already in sync")
        return
    if dry:
        return
    for u in to_add:
        api.call("POST", "/api/lists?type=block", {"address": u, "comment": SYNC_COMMENT, "enabled": True})
    for u in to_remove:
        api.call("DELETE", "/api/lists/" + urllib.parse.quote(u, safe="") + "?type=block")
    print("Updating gravity (this can take a minute)...")
    api.call("POST", "/api/action/gravity")
    print("Done")


COMMANDS = {
    "status": cmd_status,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "block": lambda api, a: add_domain(api, a, "deny"),
    "allow": lambda api, a: add_domain(api, a, "allow"),
    "export": cmd_export,
    "sync": cmd_sync,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    api = Api()
    try:
        COMMANDS[sys.argv[1]](api, sys.argv[2:])
    finally:
        api.logout()


if __name__ == "__main__":
    main()
