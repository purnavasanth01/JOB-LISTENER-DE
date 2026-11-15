"""
Readers for the German listener. Each lister returns student-looking jobs only:

  key, company, title, locations[list], url, posted, desc (str|None), detail (list|None)

`detail` tells fetch_detail() how to read the full description later (only for NEW jobs that need it).
Only PUBLIC job feeds are used (the ones companies publish for this purpose). No login, no bot-protection tricks.
"""
import email.utils
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import timezone
from urllib.parse import quote

import http_client as http
from rules import clean_text, student_kind

PAUSE = 0.25            # politeness gap between requests to the same host
FACET_TTL = 24 * 3600
SR_QUERIES = ["Werkstudent", "Praktikum", "Praktikant", "Pflichtpraktikum", "Internship", "Working student", "Hilfskraft"]
WD_QUERIES = ["Werkstudent", "Praktikum", "Praktikant", "Internship", "Working student", "Intern", "Student"]
WD_PAGE = 20
WD_MAX_PAGES = 5


def _job(key, company, title, locations, url, posted=None, desc=None, detail=None):
    return {"key": key, "company": company, "title": (title or "").strip(), "locations": [l for l in locations if l],
            "url": url, "posted": posted, "desc": desc, "detail": detail}


# ---------------------------------------------------------------- SmartRecruiters (Bosch, Continental)

def list_smartrecruiters(spec, st, company):
    co = spec["company"]
    base = "https://api.smartrecruiters.com/v1/companies/%s/postings" % co
    out = {}
    for q in SR_QUERIES:
        offset = 0
        while offset < 500:
            d = http.get_json("%s?country=de&limit=100&offset=%d&q=%s" % (base, offset, quote(q)))
            time.sleep(PAUSE)
            for j in d.get("content") or []:
                if j["id"] in out or not student_kind(j.get("name")):
                    continue
                loc = (j.get("location") or {}).get("fullLocation")
                out[j["id"]] = _job("sr:%s:%s" % (co.lower(), j["id"]), company, j.get("name"), [loc],
                                    "https://jobs.smartrecruiters.com/%s/%s" % (co, j["id"]),
                                    posted=j.get("releasedDate"), detail=["sr", co, j["id"]])
            offset += 100
            if offset >= (d.get("totalFound") or 0):
                break
    return list(out.values())


def detail_sr(co, jid):
    d = http.get_json("https://api.smartrecruiters.com/v1/companies/%s/postings/%s" % (co, jid))
    secs = ((d.get("jobAd") or {}).get("sections") or {})
    return clean_text(" ".join((s or {}).get("text", "") for s in secs.values())), None


# ---------------------------------------------------------------- Workday (Airbus, Valeo, 3M, Dow)

def _walk_facets(facets, out, param=None):
    for f in facets or []:
        p = f.get("facetParameter") or param
        for v in f.get("values") or []:
            if "facetParameter" in v:
                _walk_facets([v], out, p)
            elif p and v.get("id") and v.get("descriptor"):
                out.append((p, v["descriptor"], v["id"]))


def _germany_facet(api, st):
    """Workday feeds are worldwide. Find the 'Germany' country facet once a day (its param name differs per company)."""
    fc = st.get("wd_de")
    if fc and time.time() - fc.get("at", 0) < FACET_TTL:
        return fc["param"], fc["id"]
    d = http.post_json(api + "/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    vals = []
    _walk_facets(d.get("facets"), vals)
    hit = next(((p, i) for p, desc, i in vals if desc == "Germany" and re.search(r"country", p, re.I)), None)
    if not hit:
        raise ValueError("no Germany country facet found (Workday layout changed?)")
    st["wd_de"] = {"at": time.time(), "param": hit[0], "id": hit[1]}
    time.sleep(PAUSE)
    return hit


def _wd_req_id(path, bullets):
    m = re.search(r"_([A-Za-z0-9-]+)$", path or "")
    return (bullets or [None])[0] or (m.group(1) if m else None)


def list_workday(spec, st, company):
    host, tenant, site = spec["host"], spec["tenant"], spec["site"]
    api = "https://%s/wday/cxs/%s/%s" % (host, tenant, site)
    web = "https://%s/%s" % (host, site)
    param, gid = _germany_facet(api, st)
    jobs = {}
    for q in WD_QUERIES:
        offset, total = 0, None
        for _ in range(WD_MAX_PAGES):
            d = http.post_json(api + "/jobs", {"appliedFacets": {param: [gid]}, "limit": WD_PAGE, "offset": offset,
                                               "searchText": q})
            time.sleep(PAUSE)
            if total is None:
                total = d.get("total") or 0
            posts = d.get("jobPostings") or []
            for p in posts:
                path = p.get("externalPath") or ""
                rid = _wd_req_id(path, p.get("bulletFields"))
                if not rid or not student_kind(p.get("title")):
                    continue
                key = "wd:%s:%s" % (tenant.lower(), str(rid).lower())
                if key not in jobs:
                    jobs[key] = _job(key, company, p.get("title"), [p.get("locationsText")], web + path,
                                     posted=p.get("postedOn"), detail=["workday", api + path])
            offset += WD_PAGE
            if not posts or offset >= total:
                break
    return list(jobs.values())


def detail_workday(url):
    d = http.get_json(url)
    ji = d.get("jobPostingInfo") or {}
    locs = [ji.get("location")] + list(ji.get("additionalLocations") or [])
    return clean_text(ji.get("jobDescription")), [l for l in locs if l]


# ---------------------------------------------------------------- softgarden JSON feed (Forschungszentrum Jülich)

def _sg_location(item):
    loc = item.get("jobLocation")
    locs = loc if isinstance(loc, list) else [loc]
    out = []
    for l in locs:
        a = (l or {}).get("address") if isinstance(l, dict) else None
        if isinstance(a, dict):
            out.append(", ".join(x for x in (a.get("addressLocality"), a.get("addressCountry")) if isinstance(x, str)))
    return out


def parse_softgarden(feed, company):
    out = []
    for el in feed.get("dataFeedElement") or []:
        it = el.get("item") or {}
        title, url = it.get("title"), it.get("url")
        if not title or not url or not student_kind(title):
            continue
        out.append(_job("sg:" + re.sub(r"[?#].*$", "", url), company, title, _sg_location(it) or ["Jülich"], url,
                        posted=it.get("datePosted") or el.get("dateModified"),
                        desc=clean_text(it.get("description")) or None))
    return out


def list_softgarden(spec, st, company):
    return parse_softgarden(http.get_json("https://%s/jobs.feed.json" % spec["host"]), company)


# ---------------------------------------------------------------- RSS (Max Planck Society job board)

def parse_rss(xml_text, company):
    out = []
    for it in ET.fromstring(xml_text).iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link or not student_kind(title):
            continue
        posted = None
        try:
            posted = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc) \
                .strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            pass
        out.append(_job("rss:" + re.sub(r"[?#].*$", "", link), company, title, ["Germany"], link, posted=posted,
                        desc=clean_text(it.findtext("description")) or None))
    return out


def list_rss(spec, st, company):
    return parse_rss(http.request(spec["url"]), company)


# ---------------------------------------------------------------- registry

LISTERS = {"smartrecruiters": list_smartrecruiters, "workday": list_workday, "softgarden": list_softgarden,
           "rss": list_rss}


def source_id(spec):
    t = spec["type"]
    ident = spec.get("company") or spec.get("tenant") or spec.get("host") or re.sub(r"^https?://([^/]+).*", r"\1", spec.get("url", ""))
    return "%s:%s" % (t, ident)


def fetch_detail(job):
    """-> (description, locations|None) for a job whose list entry has no description."""
    d = job["detail"]
    if d[0] == "sr":
        return detail_sr(d[1], d[2])
    if d[0] == "workday":
        return detail_workday(d[1])
    raise ValueError("unknown detail type %r" % (d[0],))
