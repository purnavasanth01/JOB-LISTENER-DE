# JOB-LISTENER-DE

German student jobs in **materials science** (Werkstoffwissenschaft / Materialwissenschaft / Werkstofftechnik):
**Praktikum**, **Werkstudent**, **studentische Hilfskraft (HiWi)**, **Studentenjob**.

Fully separate from the US listener (`job-listener`): own code, own state, own notification list. Nothing is shared.

```
cron-job.org (every 3 min, backup GitHub timer every 15 min) ──▶ GitHub Actions ──▶ main.py
   reads company feeds (targets.json) ──▶ filters (rules.py) ──▶ new jobs only
        ├──▶ state branch: alerts.csv (the 🇩🇪 dashboard reads it), dropped.csv, last_run.log, health.json
        └──▶ Web Push to the devices on the German list (`subs_de` in JOB_APP sync/state.json)
```

## What it reads today (part 1)

| Source | Companies | Status |
|---|---|---|
| SmartRecruiters | Bosch (`BoschGroup`), Continental (`Continental`) | ✅ |
| Workday | Airbus (`ag`), Valeo (`valeo`), 3M (`3m`), Dow (`dow`) | ✅ Airbus, Valeo · 3M + Dow need Workday's `wd1` servers (down on 2026-11-13) |
| softgarden JSON | Forschungszentrum Jülich | ✅ (few jobs) |
| RSS | Max Planck Society | ✅ (mostly PhD, few student jobs) |

`targets.json` lists all 62 employers from the research (rank order). Only the 8 with a `sources` entry are read; the
others are names only until their feeds are found.

**Not read (they block bots):** Stepstone, Xing, Indeed, Glassdoor, LinkedIn. Planned: their own email alerts.

## Filters (`rules.py`)

A job is kept when it is a **student job** AND about **materials science**:
a materials word in the title, or at least 2 different materials words in the description.
Dropped: apprenticeships, dual study, trainee, PhD, thesis-only, senior roles, and titles about purchasing / IT / HR /
logistics. Every drop is saved with its reason in `dropped.csv`.

## First run

The first time a feed is read, its open matching jobs fill the dashboard **silently** (no notification flood). Only jobs
that appear later are pushed. Jobs that need their description read are processed over the next runs (150 per run).

## Commands

```bash
python3 -m unittest discover -s tests                 # tests
python3 main.py --dry-run                             # read + filter + print, save/send nothing
python3 main.py --dry-run --only workday:ag           # one source
python3 main.py --state-dir state --no-notify         # save state, no notifications
```

## Setup on GitHub (once)

1. New **public** repo `JOB-LISTENER-DE`, push this folder to `main`.
2. Repo → Settings → Secrets → Actions → add `VAPID_PRIVATE_KEY` (the same value as in the US repo).
3. Repo → Settings → Actions → General → Workflow permissions: **Read and write**.
4. Actions → `listen-de` → Run workflow (first run creates the `state` branch).
5. **cron-job.org** job, every 3 minutes (a second job next to the US one):
   - URL `https://api.github.com/repos/purnavasanth01/JOB-LISTENER-DE/actions/workflows/listen.yml/dispatches`
   - Method `POST`, body `{"ref":"main"}`
   - Headers: `Authorization: Bearer <fine-grained token: JOB-LISTENER-DE only, Actions read+write>`,
     `Accept: application/vnd.github+json`, `Content-Type: application/json`
   - Turn on cron-job.org's failure email.
   (Without this the backup timer still runs it every ~15 minutes.)

## Notifications

A device gets German alerts only if it tapped 🔔 in the app **while the 🇩🇪 dashboard was open** (that saves it in
`subs_de`). The US listener reads `subs` and never sees these devices.
