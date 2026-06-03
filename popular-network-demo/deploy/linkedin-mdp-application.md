# LinkedIn Marketing Developer Platform — application copy

Paste-ready answers for the MDP / Advertising API access request at
developer.linkedin.com. Framing = **multi-tenant SaaS** (each customer connects
their own LinkedIn Ad Account via OAuth), the long-term-true model decided in
the integration plan. Every technical detail below matches the shipped code
(scopes, redirect URI, OAuth flow) so a reviewer who tests the live handshake
sees exactly what's described.

> **[FILL IN] before submitting** — facts only you have:
> - **Legal entity:** Amplify Media Group LLC, Windom, MN (confirm exact registered name)
> - **LinkedIn Page URL:** https://www.linkedin.com/company/<your-page-slug> (the app must be owned by this Page)
> - **App / Client ID:** (generated when you create the dev app — add after)
> - **Technical contact email:** (e.g. trevors@windomnews.com or dev@amplafai.com)
> - **Privacy / legal mailboxes:** privacy@ / legal@ / support@amplafai.com (set Mailgun forwarding)

---

## 1. Company overview

**Amplafai** (operated by Amplify Media Group LLC) is a US-based multi-tenant
SaaS marketing platform for small and mid-sized businesses, sold and supported
through local newspaper publishers. Each business customer gets an AI marketing
agent that drafts content, manages approvals, and — with the owner's explicit
permission and owner-set budget caps — plans and manages paid advertising
campaigns on the channels that fit their audience.

- Website: https://amplafai.com
- Product dashboard: https://dashboard.amplafai.com
- Privacy Policy: https://amplafai.com/privacy
- Terms of Service: https://amplafai.com/terms

## 2. What we want to build on the Marketing API

We are adding LinkedIn as a paid-advertising channel inside Amplafai. LinkedIn
is our first priority among ad platforms because a meaningful share of our
customers are B2B (e.g. SaaS, professional services) for whom LinkedIn reaches
actual decision-makers rather than a consumer audience.

For each business customer who chooses to connect LinkedIn, Amplafai will:
1. Let the business owner authorize Amplafai to access **their own** LinkedIn
   Ad Account via 3-legged OAuth (we never ask for credentials).
2. Read the ad accounts the authenticated member can manage.
3. Create and manage Sponsored Content campaigns within budgets the owner sets,
   subject to the owner's approval (or autonomous within owner-set monthly caps
   on our highest tier — always bounded, never exceeding the cap).
4. Pull campaign performance (impressions, clicks, spend) to report results
   back to the owner in their dashboard.

This is a per-tenant integration: every business connects and manages its own
LinkedIn Ad Account. Amplafai does not pool or co-mingle ad accounts across
customers.

## 3. API products & OAuth scopes requested (with justification)

We request the **Advertising API** product. Minimum scopes, each tied to a
concrete feature:

| Scope | Why we need it |
|-------|----------------|
| `r_ads` | Read the ad accounts and existing campaigns the authenticated member manages, to show the owner what they're connecting. |
| `rw_ads` | Create, update, and pause Sponsored Content campaigns the owner has approved (or that fall within owner-set autonomous caps). |
| `r_ads_reporting` | Retrieve impressions / clicks / spend so we can report campaign performance back to the business owner. |
| `r_basicprofile` | Display the authenticated member's name in the UI ("Connected as …") so the owner can confirm the correct account is linked. |

We are deliberately requesting the minimum scope set. We do not request
posting-as-member, messaging, connections, or any social-graph scopes.

## 4. OAuth flow (exactly as implemented)

1. In the dashboard's **Settings → Ad accounts**, the owner clicks **Connect
   LinkedIn**.
2. We redirect to LinkedIn's authorization endpoint with `response_type=code`,
   our client ID, the scopes above, and a CSRF `state` value.
3. The member reviews and grants consent on LinkedIn.
4. LinkedIn redirects back to our registered callback:
   **`https://dashboard.amplafai.com/api/integrations/linkedin/callback`**
5. We verify `state`, exchange the code for an access + refresh token over HTTPS,
   and store the tokens encrypted at rest, scoped to that single business tenant.
6. We use the refresh token to maintain access and never ask the member to
   re-authorize unnecessarily. Disconnecting in Settings revokes and deletes the
   stored tokens.

**Authorized redirect URL to register on the app:**
`https://dashboard.amplafai.com/api/integrations/linkedin/callback`

## 5. Data handling & privacy

- **Jurisdiction / regime:** US-only; CCPA-aligned. (No EU/GDPR processing.)
- **What we store:** OAuth access/refresh tokens, the connected ad account URN,
  campaign IDs we create, and aggregate performance metrics — all scoped to the
  owning business tenant and isolated from other tenants.
- **Retention:** advertising data retained while the account is connected and
  for up to 30 days after cancellation, then deleted.
- **No resale / no model training:** LinkedIn data is used solely to operate the
  connected customer's own campaigns and reporting. We do not sell it or use it
  to train models.
- **Subprocessors:** Anthropic (AI), DigitalOcean (hosting), Cloudflare (CDN/edge),
  Mailgun + Postmark (email), Stripe (billing). Listed in our Privacy Policy.
- **Security:** tokens encrypted at rest; all transport over TLS; per-tenant
  isolation enforced in the data layer.

## 6. Expected usage & scale

Initial pilot: a small number of business customers (single digits to low tens)
across Amplafai's launch publishers (Windom, Pipestone and nearby Minnesota
markets). API call volume is modest — campaign create/update on owner action,
plus a daily reporting sync per connected account. We will scale gradually as
the pilot expands and will stay within rate limits.

## 7. Member experience & control

- Connection is always owner-initiated and revocable from Settings.
- Nothing publishes or spends without either explicit owner approval or a spend
  that falls within a monthly cap the owner has set themselves.
- On our autonomous tier, the agent still **soft-falls to a proposal** for owner
  approval whenever an action would exceed the owner's cap — autonomous spend can
  never blow past the budget the owner set.

## 8. Compliance commitments

- We comply with the LinkedIn Marketing API Terms of Use and Platform Guidelines.
- Minimum-necessary scopes; no scope creep.
- We do not store or display LinkedIn data to anyone outside the owning business.
- We honor disconnect/deletion requests promptly (token revocation + data purge).

---

## 9. Short answers for common MDP review form fields

- **What does your application do?** A multi-tenant SaaS marketing platform where
  each business connects its own LinkedIn Ad Account to plan, run, and report on
  Sponsored Content campaigns through an AI agent, under owner-set budgets.
- **Who are your users?** US-based small/mid-sized businesses, onboarded via local
  newspaper publishers.
- **Will you manage ads on behalf of others?** Yes — as multi-tenant SaaS. Each
  customer authorizes access to their **own** ad account via OAuth; we never
  co-mingle accounts.
- **How do users authenticate?** 3-legged OAuth (Authorization Code), redirect
  URL `https://dashboard.amplafai.com/api/integrations/linkedin/callback`.
- **Privacy policy / terms:** https://amplafai.com/privacy ·
  https://amplafai.com/terms
- **Data retention:** while connected + 30 days post-cancellation.
