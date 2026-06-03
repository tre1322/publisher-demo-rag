# amplafai.com /privacy + /terms — Cloudflare Worker

`amplafai.com` is a **GoDaddy Website Builder (Airo)** site, proxied through
Cloudflare. There is **no Cloudflare Pages project** and no git repo for the
apex — the homepage is edited in GoDaddy's builder.

Because the apex is **proxied** (orange cloud), Cloudflare sees requests before
they reach GoDaddy. This Worker intercepts **only** `/privacy` and `/terms`
(see `routes` in `wrangler.toml`) and serves the legal pages from the edge. The
GoDaddy homepage and every other path pass through untouched.

This gives LinkedIn's Marketing Developer Platform application the two reachable
URLs it requires:
- https://amplafai.com/privacy
- https://amplafai.com/terms

## Files
- `worker.js` — AUTO-GENERATED. Serves the two pages from embedded base64.
- `build-worker.py` — regenerates `worker.js` from `../amplafai-privacy.html`
  and `../amplafai-terms.html`. Re-run after editing the source HTML.
- `wrangler.toml` — Worker name + the two scoped zone routes.

## Verified locally
`wrangler dev --local` → `/privacy` and `/terms` (and trailing-slash forms)
return HTTP 200, `text/html; charset=utf-8`, byte-exact HTML.

## Deploy
Needs Cloudflare auth on the `amplafai.com` account — ONE of:

**A. Interactive login (browser, one-time):**
```
cd deploy/cloudflare-worker
wrangler login          # opens a browser; approve
wrangler deploy
```

**B. Scoped API token (no browser):**
Create a token at dash.cloudflare.com → My Profile → API Tokens with:
- Account → Workers Scripts → Edit
- Zone → Workers Routes → Edit  (zone: amplafai.com)
Then:
```
cd deploy/cloudflare-worker
CLOUDFLARE_API_TOKEN=xxxx wrangler deploy
```

## Verify after deploy
```
curl -s -o /dev/null -w "%{http_code}\n" https://amplafai.com/privacy   # → 200
curl -s -o /dev/null -w "%{http_code}\n" https://amplafai.com/terms     # → 200
curl -sI https://amplafai.com/privacy | grep -i x-served-by             # amplafai-legal-worker
curl -s -o /dev/null -w "%{http_code}\n" https://amplafai.com/          # → 200 (GoDaddy, untouched)
```

## Updating the pages later
Edit `../amplafai-privacy.html` / `../amplafai-terms.html`, then:
```
uv run python build-worker.py && wrangler deploy
```
