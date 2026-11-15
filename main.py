#!/usr/bin/env python3
"""
JOB-LISTENER-DE: alerts for German student jobs in MATERIALS SCIENCE
(Praktikum, Werkstudent, studentische Hilfskraft / HiWi, Studentenjob).

Fully separate from the US listener: own folder, own state, own notification list (`subs_de`).

Part 1 reads the company feeds in targets.json (Bosch, Continental, Airbus, Valeo, 3M, Dow, Jülich, Max Planck).
Stepstone / Xing / Indeed / Glassdoor / LinkedIn block scripts, so they come later (email alerts).

One run = read every feed, keep NEW jobs that pass the filters, save state, send push notifications.
The FIRST time a feed is read, its open jobs fill the dashboard silently (no notification flood).

  python3 main.py --state-dir state                 normal run
  python3 main.py --dry-run                         read + filter + print; save and send nothing
  python3 main.py --dry-run --only workday:ag       test one source
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import notify
import rules
import sources

ROOT = os.path.dirname(os.path.abspath(__file__))
CSV_FIELDS = ["found_at", "company", "rank", "title", "role", "location", "tags", "layer", "source", "posted", "url",
              "reason", "evidence"]
DETAIL_BUDGET = 150         # job descriptions read per run; the rest wait for the next run
DETAIL_LANES = 6            # websites read in parallel; each website still gets one request at a time
RUN_BUDGET = 240            # seconds; after this, remaining descriptions wait for the next run
DUP_WINDOW_DAYS = 45        # same company + same title alerted within this window = duplicate
SEEN_KEEP_DAYS = 180
MAX_CSV_ROWS = {"alerts.csv": 20000, "dropped.csv": 5000}


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class State:
    FILES = {"seen": {}, "health": {}, "meta": {}, "pending": []}

    def __init__(self, path):
        self.path = path
        os.makedirs(path, exist_ok=True)
        for name, default in self.FILES.items():
            p = os.path.join(path, name + ".json")
            if os.path.exists(p):
                with open(p) as f:
                    setattr(self, name, json.load(f))   # a corrupt state file must stop the run, never re-seed silently
            else:
                setattr(self, name, type(default)())

    def save(self):
        for name in self.FILES:
            p = os.path.join(self.path, name + ".json")
            with open(p + ".tmp", "w") as f:
                json.dump(getattr(self, name), f, separators=(",", ":"), sort_keys=True)
            os.replace(p + ".tmp", p)

    def append_csv(self, name, rows):
        if not rows:
            return
        p = os.path.join(self.path, name)
        new = not os.path.exists(p)
        with open(p, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerows(rows)
        cap = MAX_CSV_ROWS.get(name, 5000)
        with open(p) as f:
            lines = f.readlines()
        if len(lines) > cap + 1:
            with open(p, "w") as f:
                f.writelines([lines[0]] + lines[-cap:])


class Run:
    def __init__(self, args):
        self.args = args
        self.t0 = time.time()
        self.now = utcnow()
        self.now_s = iso(self.now)
        self.logs = []
        self.st = State(args.state_dir)
        with open(os.path.join(ROOT, "targets.json")) as f:
            self.targets = json.load(f)["companies"]
        for t in self.targets:
            t["cid"] = rules.norm_company(t["name"])
        self.by_cid = {t["cid"]: t for t in self.targets}
        self.rows = {"alerts.csv": [], "dropped.csv": []}
        self.alerts, self.groups = [], {}
        self.app_state, self.blacklist = {}, set()
        self.alerted = {}                # (cid, title key) -> time of an earlier alert (45-day duplicate check)
        for e in self.st.seen.values():
            if e.get("o") == "alert":
                self.alerted[(e["c"], e["ti"])] = e["t"]
        self.details_used = 0

    def log(self, msg):
        line = "%s %s" % (utcnow().strftime("%H:%M:%S"), msg)
        self.logs.append(line)
        print(line, flush=True)

    # ---------------- reading the feeds
    def fetch_all(self):
        specs = [(spec, t) for t in self.targets for spec in t.get("sources") or []]
        if self.args.only:
            specs = [(s, t) for s, t in specs if sources.source_id(s).startswith(self.args.only)]

        def one(spec, target):
            sid = sources.source_id(spec)
            h = self.st.health.setdefault(sid, {})
            started = time.time()
            try:
                return sid, target, sources.LISTERS[spec["type"]](spec, h, target["name"]), None, time.time() - started
            except Exception as e:  # noqa: BLE001 - one broken source must never stop the others
                return sid, target, None, e, time.time() - started

        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda st: one(*st), specs))
        for sid, target, jobs, err, secs in results:
            h = self.st.health.setdefault(sid, {})
            if err is None:
                h.update(ok_at=self.now_s, fails=0, count=len(jobs))
                h.pop("last_err", None)
            else:
                h["fails"] = h.get("fails", 0) + 1
                h["last_err"] = ("%s: %s" % (type(err).__name__, err))[:200]
            self.log("%-44s %s (%.1fs)" % (sid, ("%d student jobs" % len(jobs)) if err is None else
                                           "ERROR %s" % str(err)[:90], secs))
        return results

    # ---------------- bookkeeping
    def record(self, j, outcome, reason=None):
        e = {"t": self.now_s, "c": j["cid"], "ti": rules.title_key(j["title"])[:100], "o": outcome}
        if reason:
            e["r"] = reason[:60]
        self.st.seen[j["key"]] = e

    def row(self, j, role="", tags=(), reason="", location=None):
        t = j.get("target")
        return {"found_at": self.now.strftime("%Y-%m-%d %H:%M"), "company": j["company"],
                "rank": t["rank"] if t else "", "title": j["title"], "role": role,
                "location": location if location is not None else "; ".join(j.get("locations") or [])[:120],
                "tags": ", ".join(tags), "layer": "DE", "source": j["source"], "posted": j.get("posted") or "",
                "url": j.get("url") or "", "reason": reason, "evidence": ""}

    def drop(self, j, reason, role=""):
        self.record(j, "drop", reason)
        self.rows["dropped.csv"].append(self.row(j, role, reason=reason))

    def defer(self, j):
        self.st.pending.append({k: v for k, v in j.items() if k != "target"})

    # ---------------- the pipeline
    def process(self, results):
        cands = []
        waiting = {p["key"]: p for p in self.st.pending}      # jobs that waited for a description last run
        for sid, target, jobs, err, _ in results:
            if err is not None or jobs is None:
                continue
            h = self.st.health[sid]
            first = not h.get("seeded")           # first look at this feed: fill the dashboard, but stay silent
            for j in jobs:
                if j["key"] in self.st.seen:
                    continue
                silent = first or bool(waiting.get(j["key"], {}).get("silent"))   # stays silent while waiting
                j.update(source=sid, cid=target["cid"], company=target["name"], target=target, silent=silent)
                cands.append(j)
            h["seeded"] = True
            if first:
                self.log("first look at %s: %d open student jobs, no notifications for these" % (sid, len(jobs)))
        known = {c["key"] for c in cands}
        for p in self.st.pending:                 # jobs that waited for a description last run
            if p["key"] not in self.st.seen and p["key"] not in known:
                p["target"] = self.by_cid.get(p["cid"])
                cands.append(p)
        self.st.pending = []
        self.evaluate(cands)

    def evaluate(self, cands):
        cands.sort(key=lambda j: (j["target"] or {}).get("rank", 999))
        ready = []
        for j in cands:                           # 1) title checks (free)
            if j["key"] in self.st.seen or any(r["key"] == j["key"] for r in ready):
                continue
            reason = rules.title_drop(j["title"]) or ("other field (purchasing / IT / HR / logistics ...)"
                                                       if rules.other_field(j["title"]) else None)
            if reason:
                self.drop(j, reason)
                continue
            if rules.app_company_key(j["company"]) in self.blacklist:    # 🚫 on the German dashboard (double-confirmed)
                self.drop(j, "blacklisted in app")
                continue
            j["title_terms"] = rules.material_terms(j["title"])
            ready.append(j)
        need = [j for j in ready if not j["title_terms"] and j.get("desc") is None and j.get("detail")]
        fetched = self.fetch_descriptions(need)   # 2) descriptions, only for jobs whose title is not clear
        since = iso(self.now - timedelta(days=DUP_WINDOW_DAYS))
        for j in ready:                           # 3) materials-science check
            role = rules.student_kind(j["title"])
            terms, desc, via = j["title_terms"], j.get("desc"), "title"
            if not terms:
                if desc is None and j.get("detail"):
                    got = fetched.get(j["key"])
                    if got is None:               # over budget / out of time: next run
                        self.defer(j)
                        continue
                    desc, err = got
                    if err:
                        fails = self.st.meta.setdefault("detail_fails", {})
                        fails[j["key"]] = fails.get(j["key"], 0) + 1
                        self.log("description failed (%d) %s: %s" % (fails[j["key"]], j["key"], err))
                        if fails[j["key"]] < 3:
                            self.defer(j)
                            continue
                        desc = ""
                terms, via = rules.material_terms(desc or ""), "description"
                if len(terms) < 2:
                    self.drop(j, "not materials science", role)
                    continue
            tkey = rules.title_key(j["title"])[:100]
            gkey = (j["cid"], tkey)
            if gkey in self.groups:               # same title, another location -> one alert
                self.groups[gkey]["count"] += 1
                self.record(j, "alert")
                continue
            if self.alerted.get(gkey, "") >= since:
                self.record(j, "dup")
                continue
            hid = hashlib.sha1(j["key"].encode()).hexdigest()[:10]
            t = j["target"]
            tags = ["🧪 " + ", ".join(terms)] + (["via description"] if via == "description" else [])
            a = {"hid": hid, "company": j["company"], "rank": t["rank"] if t else None, "autostar": bool(t and t.get("autostar")),
                 "title": j["title"], "role": role, "location": "; ".join(dict.fromkeys(j.get("locations") or []))[:120],
                 "silent": bool(j.get("silent")), "count": 1}
            self.groups[gkey] = a
            self.alerts.append(a)
            self.alerted[gkey] = self.now_s
            self.record(j, "alert")
            self.rows["alerts.csv"].append(self.row(j, role, tags, location=a["location"]))

    def fetch_descriptions(self, jobs):
        """-> {key: (desc, error)}. Websites run in parallel, but each website's jobs go one after another."""
        jobs = jobs[:DETAIL_BUDGET]
        lanes = {}
        for j in jobs:
            lanes.setdefault(j["source"], []).append(j)
        deadline = self.t0 + RUN_BUDGET
        out = {}

        def lane(js):
            for j in js:
                if time.time() > deadline:
                    return
                try:
                    out[j["key"]] = (sources.fetch_detail(j)[0], None)
                except Exception as e:  # noqa: BLE001
                    out[j["key"]] = (None, e)
                time.sleep(sources.PAUSE)
        if lanes:
            with ThreadPoolExecutor(max_workers=DETAIL_LANES) as ex:
                list(ex.map(lane, lanes.values()))
        self.details_used += len(out)
        return out

    # ---------------- notifications and saving
    def deliver(self):
        batch = [a for a in self.alerts if not a["silent"]]
        if not batch:
            return
        if self.args.dry_run or self.args.no_notify:
            self.log("ALERTS (not sent): %d" % len(batch))
            for a in batch:
                self.log("  • %s #%s | %s | %s | %s" % (a["company"], a["rank"], a["role"], a["title"], a["location"]))
            return
        if notify.web_push(batch, self.app_state, self.log):
            self.log("sent %d alert(s)" % len(batch))

    def finish(self):
        if self.args.dry_run:
            self.log("dry run: state not saved")
            return
        cutoff = iso(self.now - timedelta(days=SEEN_KEEP_DAYS))
        self.st.seen = {k: e for k, e in self.st.seen.items() if e.get("t", "") >= cutoff}
        self.st.meta["last_run"] = self.now_s
        for name, rows in self.rows.items():
            self.st.append_csv(name, rows)
        self.st.save()
        with open(os.path.join(self.st.path, "last_run.log"), "w") as f:
            f.write("\n".join(self.logs) + "\n")

    def go(self):
        self.app_state = {} if self.args.dry_run else notify.load_app_state(self.log)   # JOB_APP: blacklist + devices
        self.blacklist = notify.app_blacklist(self.app_state)
        if self.blacklist:
            self.log("app blacklist (German dashboard): %d compan%s" % (len(self.blacklist), "y" if len(self.blacklist) == 1 else "ies"))
        results = self.fetch_all()
        self.process(results)
        self.deliver()
        self.log("done in %.1fs: %d new jobs (%d silent first-look), %d dropped, %d waiting for a description, "
                 "%d descriptions read, %d jobs known" % (
                     time.time() - self.t0, len(self.alerts), sum(1 for a in self.alerts if a["silent"]),
                     len(self.rows["dropped.csv"]), len(self.st.pending), self.details_used, len(self.st.seen)))
        self.finish()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", default=os.path.join(ROOT, "state"))
    ap.add_argument("--dry-run", action="store_true", help="read + filter + print; send nothing, save nothing")
    ap.add_argument("--no-notify", action="store_true", help="save state but do not send notifications")
    ap.add_argument("--only", help="only sources whose id starts with this, e.g. workday:ag or smartrecruiters")
    args = ap.parse_args()
    try:
        Run(args).go()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
