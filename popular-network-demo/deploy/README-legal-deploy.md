# Deploying Amplafai legal pages to Cloudflare Pages

The two files `amplafai-privacy.html` and `amplafai-terms.html` in this folder are ready-to-deploy static legal pages, drafted for:

- **Jurisdiction:** US-only (CCPA, no GDPR)
- **Retention:** 30 days post-cancellation
- **Entity:** Amplify Media Group LLC, Windom MN
- **Subprocessors listed:** Anthropic, Postmark, DigitalOcean, Cloudflare, Stripe, and the ad-platform APIs

## Where they need to land

LinkedIn's MDP application form will reject URLs that 404. Both pages need to be reachable at:

- `https://amplafai.com/privacy`
- `https://amplafai.com/terms`

(NOT `/privacy.html` — strip the extension. See deployment options below.)

## Two deployment patterns

### Option A — folders (recommended)

In your Cloudflare Pages repo, create:

```
privacy/
  index.html    ← copy amplafai-privacy.html here, rename to index.html
terms/
  index.html    ← copy amplafai-terms.html here, rename to index.html
```

Cloudflare Pages automatically serves `index.html` for directory paths, so `/privacy` resolves to `/privacy/index.html` with no config.

### Option B — _redirects rewrite

If your Pages repo prefers flat files, drop a `_redirects` file at the repo root:

```
/privacy   /privacy.html   200
/terms     /terms.html     200
```

Status `200` (not 301/302) is a rewrite — the URL stays `/privacy` in the browser, but the content comes from `/privacy.html`.

## After deployment

1. Push the change to your Pages repo
2. Wait ~30 seconds for the CF Pages build
3. Verify in a browser: open `https://amplafai.com/privacy` and `https://amplafai.com/terms`
4. Both should render with no 404
5. Now you can submit the LinkedIn MDP application using those URLs

## What's NOT in scope of this deploy

- A "Privacy" / "Terms" link in the amplafai.com footer (UX nice-to-have — add at your leisure)
- A privacy@amplafai.com / legal@amplafai.com mailbox (the docs reference these — set up Mailgun forwarding rules to your real inbox)
- Lawyer review (these are solid templates but should be reviewed before scaling beyond pilot)
