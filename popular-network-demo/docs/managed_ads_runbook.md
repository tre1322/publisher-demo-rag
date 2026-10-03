# Managed ad budgets: per-client runbook (Phase 4)

Until Meta, Google and LinkedIn approve Amplafai's API access (Phase 5), Amplafai runs a client's
paid campaigns **by hand, inside the client's own ad account**. The platform charges the client's card
directly; no ad money passes through Amplafai. The dashboard never claims a change the platform hasn't
made: a campaign says *Waiting for launch* or *Pause requested* until someone here confirms it.

Managed campaigns come with **Tier 3 (Marketing Agent + Concierge) and Tier 4**. Our promise to owners
is **within one business day** for launches, pauses, restarts and cancellations (`PROMISE` in
`app/managed_ads.py`).

> Platform menus move. The click paths below were written in October 2026; if a screen looks
> different, follow the intent of the step.

## 1. Set up a new client (once)

1. **Get access to their ad accounts.** Amplafai works in the client's accounts, never its own.
   - **Meta:** in Meta Business Suite, the client adds Amplafai's business as a partner on their ad
     account (Business settings → Accounts → Ad accounts → Assign partners), with permission to manage
     campaigns. Or Amplafai requests access from its own Business settings.
   - **Google Ads:** link the client's account to Amplafai's manager (MCC) account; the client accepts
     the request under Admin → Access and security → Managers.
   - **LinkedIn:** the client adds the Amplafai person as *Campaign manager* on their ad account
     (Account settings → Manage access).
2. **Set a spending limit inside the platform.** This is the real protection in this phase. The
   dashboard's monthly caps only learn about spend when results are uploaded, so they can warn but
   can't stop spending in time.
   - **Meta:** set an *account spending limit* (Billing & payments → Payment settings) a little above the
     owner's monthly caps.
   - **Google Ads / LinkedIn:** every campaign gets an end date and a total (lifetime) budget where
     the platform offers one; otherwise a daily budget × days that matches the dashboard.
3. **The owner sets their monthly cap per platform** in Ads & Spend. That cap is what the 80% / 100%
   alerts measure.

## 2. Launch a campaign

A request appears in the admin console under **Ad requests** (and an email goes to `ADS_OPS_EMAIL`,
or `ALERT_EMAIL` if that isn't set) when the owner creates or approves a campaign.

1. In the platform, inside the client's account: create the campaign with a **lifetime budget equal to
   the dashboard's total** and an **end date = launch day + the campaign's days**. Use the audience hint
   from the request; ask the owner if it's blank.
2. **Name it exactly as in the dashboard.** Matching uses the campaign ID first, the name second.
3. Copy the platform's **campaign ID** (Meta: Campaign ID column; Google: Campaign ID; LinkedIn: the
   number in the campaign URL) into the request and press **Mark launched**. The owner's row turns
   *Live*, with start and end dates.
4. Can't launch (rejected ad, missing payment method)? Use **Can't launch…** and say why. The campaign
   is cancelled (nothing was spent), and the owner sees your note.

## 3. Pause, restart, cancel

The owner's buttons (and the AI agent's, and a cap reaching 100%) create requests. The campaign keeps
running until you make the change in the platform and press **Done in Ads Manager**. If the owner
withdraws a request first, you get a *Withdrawn* email; nothing to do.

Requests turn red as **Overdue** after one business day.

## 4. Upload results (every day or two, per client, per platform)

In the admin console, on the client's card, under **Ad results**:

1. Download a campaign report **broken down by day**, including the **Campaign ID** column, covering at
   least the days since the last upload (overlapping days are replaced, never double-counted):
   - **Meta Ads Manager:** Campaigns → date range → Breakdown → By time → Day → Reports → Export table
     data (.csv). Add *Campaign ID* under Columns → Customize columns if it isn't there.
   - **Google Ads:** Campaigns → Segment → Time → Day → Download → .csv. Google's "Excel .csv" (UTF-16,
     tabs) works too.
   - **LinkedIn Campaign Manager:** Campaigns → Export → time breakdown *Daily* → CSV.
2. Pick the platform, choose the file, press **Preview**. Check the columns it used, the before → after
   spend per campaign, and any rows it couldn't match (for example, the client's own campaigns that
   Amplafai doesn't manage are listed and skipped, never guessed).
3. Press **Import these numbers.** The owner sees the platform's numbers to the cent, labelled with the
   source and the last day covered.

Files in another currency, or with one row per campaign for a whole date range, are refused with
instructions; spend has to land on the right day to count toward the right month.

## 5. Cap alerts

After each upload, each platform's spend for the current month is compared with the owner's cap:

- **80%:** the owner (owners and editors) and Amplafai get an email. Nothing else changes.
- **100%:** a second email, and a **pause request** opens for every campaign still running on that
  platform. Pause them, or call the owner; if they raise the cap they can press *Keep running*.

Each level emails once per month. Client emails go out from production only.

## Settings on the server

| Setting | What it does |
| --- | --- |
| `ADS_OPS_EMAIL` | Where new requests and cap alerts go (comma-separated). Falls back to `ALERT_EMAIL`. |
| `ENVIRONMENT=production` | Required for owner-facing cap alert emails (already set on the droplet). |

## Phase 5: when a platform's API is connected

Once a platform's API access is approved and a client's account is connected (LinkedIn by the
owner's sign-in; Meta by Amplafai linking it, below), the dashboard talks to the platform directly. The safety rules:

- **Created paused.** An approved campaign is created on the platform **paused**. It says *Ready to
  turn on* until the owner (or Amplafai, from the client's dashboard) presses **Turn on**. The AI agent
  can create campaigns within the owner's cap but can never turn one on.
- **Pause, restart, cancel go straight to the platform.** If the platform refuses or can't be reached,
  the change falls back to a request in **Ad requests** for Amplafai to do by hand, and the owner is
  told so.
- **Real spend is read from the platform every 3 hours** (the last 7 days each time; revised days
  replace old numbers). At 100% of a monthly cap, running campaigns on that platform are paused through
  the API immediately.
- **Pause all paid ads.** The owner's button in Ads & Spend, a per-client button on the admin card, and
  **Pause paid ads for every client** at the top of Ad requests. Everything running stops (through the
  API, or as hand requests); nothing can start, restart or be turned on until someone allows paid ads
  again, and nothing restarts on its own after that. Pausing works even when a client's billing is
  overdue.
- **Every change is logged** under *Platform activity* in the admin console, failures included.
- **Tokens are encrypted.** Ad-account sign-in tokens are stored encrypted with `TOKEN_ENCRYPTION_KEY`
  (server `.env`, never in the database or its backups). Connecting a real ad account is refused until
  the key is set. Losing the key means every client reconnects; keep a copy somewhere safe.

## Meta through its API (Phase 5c)

Built and switched off. Until the server has `META_ACCESS_TOKEN` and `META_APP_SECRET`, every Meta
campaign runs by hand exactly as in sections 2 to 4. Nothing about a client changes until Amplafai
links their ad account (step B).

### A. Switch it on (Trevor, once Meta approves the app)

**Amplafai's Meta business is the Citizen Publishing business portfolio** (Trevor's decision, Oct 3,
2026). Wherever this runbook says "Amplafai's business" for Meta, it means that portfolio, and that is
the name clients see when they add Amplafai as a partner, so tell them to expect it.

Prerequisites: a Business-type Meta app (named Amplafai) owned by the Citizen Publishing portfolio,
Business Verification of Citizen Publishing, and App Review approval for advanced access to `ads_management` and `ads_read` (Meta requires that for
managing other businesses' ad accounts).

1. **System user.** In the Citizen Publishing portfolio's Business settings → Users → System users, add an Admin system user
   (e.g. "Amplafai server"). Add the app to it (Assign assets → Apps).
2. **Token.** Generate a token for that system user and the Amplafai app with: `ads_management`,
   `ads_read`, `business_management`, `pages_read_engagement`, `pages_show_list`, `pages_manage_ads`.
   Choose **Never expires** if Meta offers it. If Meta only allows 60-day tokens for Amplafai's
   business, set a reminder for day 50; when it lapses, Meta campaigns fall back to the hand queue
   and the admin log says "needs to be renewed".
3. **Require app secret.** In the app dashboard → App settings → Advanced, turn on *Require app
   secret*. Every call the server makes is signed with it (`appsecret_proof`), so the token alone is
   useless to anyone who copies it.
4. **Server.** `ssh root@157.230.61.250`, then `nano /opt/publisher-demo-rag/popular-network-demo/.env`,
   add `META_ACCESS_TOKEN=...` and `META_APP_SECRET=...`, save, and ask Claude to restart the
   dashboard. (`META_GRAPH_VERSION` defaults to v26.0; set it only when Meta retires that version.)
5. **First real campaign on Amplafai's own account.** Link Amplafai as a "client" (step B), boost one
   of Amplafai's own Page posts at $5/day for 2 days, turn it on, let the 3-hourly sync run, and
   check the dashboard matches Ads Manager to the cent. Then cancel it.

Meta keeps a new app on its *Limited* Marketing API tier (development only) until it has made 500+
API calls in 15 days with under 15% errors; then it can move to *Full*. Ordinary use (links, syncs,
the test campaign) counts toward that.

### B. Link each client (Amplafai staff)

1. The client adds Amplafai's Meta business (it appears as **Citizen Publishing**; give them its business
   ID from Business settings → Business info) as a **partner** on their ad account (permission to manage
   campaigns) **and on their Facebook Page** (permission to create ads), as in section 1.
2. In Amplafai's Business settings, give the "Amplafai server" system user access to the client's
   shared ad account and Page. *(Meta's docs describe assigning a system user to an ad account; that
   this works for partner-shared assets is the standard agency setup but wasn't confirmed in Meta's
   docs. Check it on the first client.)*
3. Admin console → the client's card → **Meta API**: ad account ID, Facebook Page ID, Instagram
   account ID (optional; adds Instagram placements), radius. **Check and link** confirms Amplafai can
   see the account and Page, that the account is active and bills in US dollars, finds the town on
   Meta's map (or asks for a ZIP code), and shows the account's spending limit. If it says there's no
   spending limit, set one in Meta a little above the owner's caps.
4. The client should also have automatic posting linked (Phase 5b): Meta campaigns boost a post
   that's already on their Facebook Page.

### C. What a Meta campaign is

- Created **paused**: campaign (Awareness, Reach) → ad set (lifetime budget = days × daily budget,
  end date, the town plus radius, ages 18+, Facebook, plus Instagram if linked, Advantage+ audience
  off) → ad (the owner's own published post, unchanged).
- The radius comes from the audience hint when it says "within N miles" (kept to Meta's 10–50), or
  the radius set when linking.
- **Turn on** sets the end date to turn-on day + the campaign's days and starts it. Pause / restart
  act on the campaign; restart keeps the original end date. Cancel archives it on Meta.
- If Meta refuses any step of building it, the half-built campaign is deleted on Meta and the owner
  sees Meta's reason.
- **Goes to the hand queue instead** (Ad requests, as in section 2): the post isn't on Facebook yet,
  the post is on a different Page from the linked one, or there's no post. The reason is in the
  *Platform activity* log.

### D. When something goes wrong

| What the owner or the log says | What it means / what to do |
|---|---|
| "Amplafai's Meta connection needs to be renewed" | Meta refused the token. Generate a new one (A.2), update `.env`, restart. |
| "Meta is limiting how fast Amplafai can make changes" | Rate limit. The change became a hand request; do it in Ads Manager. |
| "doesn't have permission … partner" | The client's partner access or the system user's assignment is missing (B.1–B.2). |
| "This campaign has no ad on Meta yet" | Add the ad in Ads Manager, then press Turn on again. |
| Sync failures in Platform activity | Spend for that client isn't updating; upload an export (section 4) until it's fixed. |

**Stop using the API** on the card (or the owner's **Disconnect** in Settings) only stops Amplafai's
server from calling Meta for that client; it doesn't remove partner access in Meta. Campaigns
already on Meta keep running, and changes to them go to the hand queue.

## Automatic posting (Phase 5b, Tier 2 and up)

Approved posts publish themselves through Ayrshare, the posting service (decision 4). Tier 1 keeps copy
and paste.

1. **Switch it on once (Trevor):** sign up for an Ayrshare plan that allows multiple client profiles
   (Launch: up to 10 clients; Business: up to 30), copy the API key from the Ayrshare dashboard, and add
   `AYRSHARE_API_KEY=...` to the server's `.env`. Then ask Claude to restart the dashboard.
2. **Each client links their accounts once:** Settings → Connections → **Link Facebook, Instagram &
   Google**. It opens Ayrshare's secure page in a new tab (the link is valid for 5 minutes; press the
   button again if it expires). Amplafai can do it with the owner on a call.
3. **From then on:** approving a post queues it for its planned day (around 10am Central), or right
   away if that day is today. The calendar shows *Posts automatically on…*, then *Posted* with a link
   to the live post, or what went wrong with **Try again**.
4. **Never posted automatically:** the website (no posting connection), Instagram posts without a
   photo, and platforms the owner hasn't linked. Each says so on the post, so nothing silently
   disappears.

The posting service's profile key for each client is encrypted like ad tokens and left out of data
exports. If Ayrshare rate-limits a client, posts wait 30 minutes instead of retrying immediately.
