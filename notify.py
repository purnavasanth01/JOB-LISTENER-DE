"""
Notifications for the GERMAN listener: Web Push to the JOB_APP devices that tapped 🔔 while on the 🇩🇪 dashboard.

Own device list: `subs_de` in JOB_APP's sync/state.json. The US listener reads `subs` (a different list), so
a phone on the US dashboard never gets German alerts and the other way round. No Telegram here.
"""
import json
import os
import time
import urllib.error
import urllib.request

APP_URL = "https://purnavasanth01.github.io/JOB_APP/"
APP_STATE_API = "https://api.github.com/repos/purnavasanth01/JOB_APP/contents/sync/state.json?ref=data"
SUBS_KEY = "subs_de"
PUSH_PER_JOB_MAX = 8      # this many jobs get their own notification per run; the rest one summary


def load_app_state(log):
    """The app's synced state ({} when it does not exist yet or GitHub is unreachable)."""
    headers = {"Accept": "application/vnd.github.raw+json", "User-Agent": "job-listener-de"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    try:
        with urllib.request.urlopen(urllib.request.Request(APP_STATE_API, headers=headers), timeout=15) as r:
            return json.loads(r.read().decode("utf-8")) or {}
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log("app state: HTTP %s" % e.code)
    except Exception as e:  # noqa: BLE001 - the app state is optional, never fail the run for it
        log("app state: %s" % e)
    return {}


def app_blacklist(state):
    """Companies blacklisted on the GERMAN dashboard (the app stores them as "de:<key>"; US entries have no prefix)."""
    return {k[3:] for k, v in (state.get("blacklist") or {}).items()
            if k.startswith("de:") and isinstance(v, dict) and v.get("on")}


def subscriptions(state):
    return [v["sub"] for v in (state.get(SUBS_KEY) or {}).values()
            if isinstance(v, dict) and v.get("on", True) and isinstance(v.get("sub"), dict)]


def push_messages(alerts):
    msgs = []
    for a in alerts[:PUSH_PER_JOB_MAX]:
        top = bool(a.get("autostar"))
        loc = (a.get("location") or "").split(";")[0].strip()
        msgs.append({"title": "%s %s%s · %s" % ("👑" if top else "🇩🇪", a["company"],
                                                " #%s" % a["rank"] if a.get("rank") else "", a.get("role") or "Job"),
                     "body": a["title"] + ("\n📍 " + loc if loc else ""),
                     "tag": "de-job-%s" % a["hid"], "url": APP_URL, "top": top})
    rest = alerts[PUSH_PER_JOB_MAX:]
    if rest:
        names = sorted({a["company"] for a in rest})
        msgs.append({"title": "🇩🇪 %d more job%s" % (len(rest), "" if len(rest) == 1 else "s"),
                     "body": ", ".join(names[:6]) + (" +%d more" % (len(names) - 6) if len(names) > 6 else ""),
                     "tag": "de-summary-%d" % int(time.time()), "url": APP_URL, "top": False})
    return msgs


def web_push(alerts, state, log):
    """Send the alerts to every device on the German list. True if at least one push was accepted."""
    key = os.environ.get("VAPID_PRIVATE_KEY")
    subs = subscriptions(state)
    if not alerts:
        return False
    if not (key and subs):
        log("web push: nothing sent (%s)" % ("no VAPID_PRIVATE_KEY secret" if not key else "no device on the German list"))
        return False
    try:
        from py_vapid import Vapid
        from pywebpush import WebPushException, webpush
    except ImportError:
        log("web push: pywebpush is not installed")
        return False
    if "BEGIN" in key:                       # the secret is stored as PEM text; pywebpush wants a Vapid object
        key = Vapid.from_pem(key.replace("\\n", "\n").strip().encode())
    msgs, sent = push_messages(alerts), 0
    for sub in subs:
        for m in msgs:
            try:
                webpush(subscription_info=sub, data=json.dumps(m), vapid_private_key=key,
                        vapid_claims={"sub": "https://purnavasanth01.github.io"}, ttl=24 * 3600)
                sent += 1
            except WebPushException as e:
                code = getattr(e.response, "status_code", None)
                log("web push failed (HTTP %s)%s" % (code, ": device unsubscribed" if code in (404, 410) else ""))
                if code in (404, 410):
                    break
            except Exception as e:  # noqa: BLE001
                log("web push failed: %s" % e)
    log("web push: %d notification(s) to %d device(s)" % (sent, len(subs)))
    return sent > 0
