# Popular Network — Marketing Dashboard

The marketing dashboard a Tier 2 / Tier 3 small business owner sees when they log into Popular Network. Built as `dashboard.html` (a single-file React app loaded via CDN — no build step) backed by a FastAPI + SQLite server.

## Running

```bash
# first time only
uv sync

# every time
uv run python -m app.main
#  -- or --
./start.bat        # Windows
./start.sh         # Linux/Mac
```

Then open <http://localhost:8765>.

The DB seeds **Quadd.ai** (Trevor Slette's real business, with a voice brief synthesized from a 23-minute interview — see `data/voice-brief/quadd_ai.json`) on first boot. The seed lives in `app/seed.py` and only runs if the `businesses` table is empty — it's safe to restart the server repeatedly. If you want to switch back to the original Westbrook Auto seed, the prior version is preserved in git history (last on master before the C.1 + Quadd pivot commit).

## Repo layout

```
popular-network-demo/
├── dashboard.html          ← single-file React/CDN frontend (~2,700 lines)
├── app/
│   ├── main.py             ← FastAPI app + uvicorn entry point
│   ├── db.py               ← SQLAlchemy engine + session factory
│   ├── models.py           ← ORM models (Business, Post, Approval, …)
│   ├── seed.py             ← Quadd.ai Day-1 seed (voice brief in data/voice-brief/)
│   └── agent/
│       └── system_prompt.py ← assembles role + voice brief + DB context for Claude
│   ├── routers/            ← bootstrap, posts, approvals, reviews, …
│   └── scripts/
│       └── smoke_bootstrap.py   ← end-to-end TestClient smoke
├── data/
│   └── popular_network.db  ← SQLite (gitignored)
├── docs/                   ← strategy + build briefs
└── pyproject.toml
```

## Verifying it works

```bash
uv run python -m app.scripts.smoke_bootstrap
```

Should print `PASS  Phase A smoke green ✓`.

## How the frontend talks to the backend

`dashboard.html`'s `App` component calls `fetch('/api/bootstrap')` on mount and pushes the result through a React `DashboardContext`. Every view (`HomeView`, `ComposeView`, `CalendarView`, etc.) opens with:

```js
const { business, stats, attention, weekRecap, posts, approvals, performance, reviews, marketingPlan, chat, settings } = useDashboard();
```

These destructured names shadow the module-level mock-data `const`s, which still live near the top of the file and are bundled into `FALLBACK_DATA` — used when the backend is unreachable, with a visible banner explaining the degraded state.

## Phase roadmap

This is **Phase A** of the plan at `~/.claude/plans/ok-we-need-a-tender-pillow.md`:

- **A — Foundation.** ✅ FastAPI + SQLite + `/api/bootstrap` + dashboard wired to fetch
- **B — Wire actions.** Approve/edit/reject in Approvals, save drafts in Compose, edit settings, respond to reviews, edit marketing plan
- **C — AI Agent.** Real Claude-backed chat with `draft_post` + `propose_boost` tools
- **D — Phase 1.5.** Reach-tier configurator, cross-territory revenue, Local-First Algorithm transparency
- **E — Tier 4 inventory.** Feed connectors (DealerCenter / vAuto / TractorHouse / MLS) + Inventory tab
- **F — Billing & Tier 3 chatbot.** Usage panel + chatbot-conversation preview
