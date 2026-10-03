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
