#!/usr/bin/env python3
"""Buffer cadence audit — counts SCHEDULED posts per brand x platform x ISO-week
and flags any (brand, platform, week) below the cadence floor.

Operator directive 2026-06-24: >=2-3 posts/platform/week per brand (IG, TikTok, X
each), for BOTH Calsanova and Scythene. This script is the mechanical check for
that floor (read-only; safe to run anytime or on a weekly cron).

Tokens: env BUFFER_API_TOKEN (Calsanova) + BUFFER_API_TOKEN_SCYTHENE (Scythene),
else parsed from ~/.claude/notes/buffer-creds.md.

Usage: python3 queue_audit.py [--floor 2] [--weeks 4]
Exit 0 always (report); prints SHORT lines for any below-floor cell.
"""
from __future__ import annotations
import argparse, json, os, re, sys, urllib.request, datetime, collections

ORGS = {"calsanova": "69d1c0f10f822245c9a6cf75", "scythene": "69d1f2811c3d1fa55c0ff9c6"}
CHANNELS = {
    "69d1ea5b031bfa423ccf11e5": ("calsanova", "instagram"),
    "69d1eaee031bfa423ccf12be": ("calsanova", "twitter"),
    "69d1ef84031bfa423ccf2495": ("calsanova", "tiktok"),
    "69d1f3a0031bfa423ccf2e82": ("scythene", "instagram"),
    "69d1f504031bfa423ccf3152": ("scythene", "twitter"),
    "69d1f3cb031bfa423ccf2ed2": ("scythene", "tiktok"),
}
PLATFORMS = ["instagram", "tiktok", "twitter"]
CREDS = os.path.expanduser("~/.claude/notes/buffer-creds.md")


def tokens():
    cal = os.environ.get("BUFFER_API_TOKEN")
    scy = os.environ.get("BUFFER_API_TOKEN_SCYTHENE")
    if cal and scy:
        return cal, scy
    try:
        txt = open(CREDS).read()
        cal = cal or (re.search(r"BUFFER_API_TOKEN=(\S+)", txt) or [None, None])[1]
        scy = scy or (re.search(r"BUFFER_API_TOKEN_SCYTHENE=(\S+)", txt) or [None, None])[1]
    except OSError:
        pass
    return cal, scy


def fetch(org_id, token):
    q = ("query($o: OrganizationId!){ posts(input:{organizationId:$o, "
         "filter:{status:[scheduled]}, sort:[{field:dueAt,direction:asc}]}, first:60)"
         "{ edges{ node{ dueAt channelId } } } }")
    body = json.dumps({"query": q, "variables": {"o": org_id}}).encode()
    req = urllib.request.Request("https://api.buffer.com/graphql", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (queue_audit)"})
    out = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return [e["node"] for e in out.get("data", {}).get("posts", {}).get("edges", [])]


def fetch_errors(org_id, token):
    """Recent FAILED (status=error) posts + their error message. Delivery-failure
    detection — the cadence audit counts scheduled, not delivered, so a channel
    can silently error (e.g. Instagram 'Invalid Credentials' token expiry) while
    the queue reads 'compliant'. Added 2026-08-09 after IG auth expiry went
    unnoticed until Buffer emailed."""
    q = ("query($o: OrganizationId!){ posts(input:{organizationId:$o, "
         "filter:{status:[error]}, sort:[{field:dueAt,direction:desc}]}, first:15)"
         "{ edges{ node{ dueAt channelService error { message } } } } }")
    body = json.dumps({"query": q, "variables": {"o": org_id}}).encode()
    req = urllib.request.Request("https://api.buffer.com/graphql", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (queue_audit)"})
    out = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return [e["node"] for e in out.get("data", {}).get("posts", {}).get("edges", [])]


def isoweek(due):
    d = datetime.datetime.fromisoformat(due.replace("Z", "+00:00"))
    y, w, _ = d.date().isocalendar()
    return f"{y}-W{w:02d}"


def _report_failures(now, toks):
    """Print recent (last 14d) delivery failures across both brands, if any."""
    cutoff = (now - datetime.timedelta(days=14)).date().isoformat()
    fails = []
    for brand, org in ORGS.items():
        tok = toks.get(brand)
        if not tok:
            continue
        try:
            for n in fetch_errors(org, tok):
                due = (n.get("dueAt") or "")[:10]
                if due >= cutoff:
                    msg = ((n.get("error") or {}).get("message") or "").strip()
                    fails.append((brand, n.get("channelService", "?"), due, msg))
        except Exception:
            pass
    if fails:
        print("DELIVERY FAILURES (last 14 days — posts that ERRORED, not just scheduled):")
        for b, ch, due, msg in fails:
            print(f"  {b} {ch} {due}: {msg[:110]}")
        print("  -> 'Invalid Credentials' = reconnect that channel in Buffer (Settings->Channels). Media/other = retry.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", type=int, default=2)
    ap.add_argument("--weeks", type=int, default=4)
    ap.add_argument("--failures-only", action="store_true",
                    help="Skip cadence; only report recent delivery failures (fast, for the every-session hook).")
    args = ap.parse_args()
    cal, scy = tokens()
    toks = {"calsanova": cal, "scythene": scy}

    now = datetime.datetime.now(datetime.UTC)
    this_y, this_w, _ = now.date().isocalendar()
    future_weeks = []
    for i in range(args.weeks):
        d = now.date() + datetime.timedelta(weeks=i)
        y, w, _ = d.isocalendar()
        future_weeks.append(f"{y}-W{w:02d}")

    if args.failures_only:
        _report_failures(now, toks)
        return

    counts = collections.defaultdict(int)  # (brand, platform, week) -> n
    for brand, org in ORGS.items():
        tok = toks.get(brand)
        if not tok:
            print(f"WARN: no token for {brand}", file=sys.stderr)
            continue
        for n in fetch(org, tok):
            ch = CHANNELS.get(n["channelId"])
            if not ch or not n.get("dueAt"):
                continue
            b, plat = ch
            counts[(b, plat, isoweek(n["dueAt"]))] += 1

    short = []
    print(f"=== Cadence audit (floor={args.floor}/platform/week) — {now.date()} ===")
    for brand in ORGS:
        print(f"\n{brand.upper()}")
        for wk in future_weeks:
            cells = []
            for plat in PLATFORMS:
                c = counts[(brand, plat, wk)]
                mark = "" if c >= args.floor else "  <-- SHORT"
                cells.append(f"{plat[:2].upper()}={c}{mark}")
                if c < args.floor and wk != f"{this_y}-W{this_w:02d}":
                    short.append((brand, plat, wk, c))
            print(f"  {wk}: " + "  ".join(cells))
    if short:
        print("\nBELOW FLOOR (future weeks):")
        for b, p, wk, c in short:
            print(f"  {b} {p} {wk}: {c}/{args.floor} — schedule {args.floor - c} more")
    else:
        print("\nAll future weeks meet the floor. (Current partial week not flagged.)")

    _report_failures(now, toks)


if __name__ == "__main__":
    main()
