# Secure Widget Authentication System - Design Document

## Overview

Add API key authentication for the chat widget that is secure (key never visible in browser) and supports per-customer customizations.

## Security Model

- **Widget ID** (public): Embedded in script tag, identifies the customer
- **API Key** (secret): Stored server-side only, used for admin operations
- **Origin Validation**: Server checks `Origin` header against allowed domains
- **Rate Limiting**: Per-widget request limits
- **IP Logging**: Track IPs for abuse detection

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│  Customer's Website (mysite.com)                                │
│  <script src="https://chat.server/static/chat-widget.js"       │
│          data-widget-id="wid_abc123">                           │
│  </script>                                                      │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  iframe src="/chat?widget_id=wid_abc123"                │   │
│  │  [Origin header: https://mysite.com]                    │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Chat Server                                                     │
│                                                                 │
│  1. Extract widget_id from request                              │
│  2. Look up widget config in database                           │
│  3. Validate Origin header matches allowed_domains              │
│  4. Check rate limit                                            │
│  5. If valid: serve customized chat                             │
│  6. If invalid: reject (403 Forbidden)                          │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  widgets table                                           │   │
│  │  - widget_id: "wid_abc123"                              │   │
│  │  - api_key: "sk_secret..." (never sent to browser)      │   │
│  │  - allowed_domains: ["mysite.com", "www.mysite.com"]    │   │
│  │  - config: {color, welcome_message, features...}        │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Security Analysis

### What This Protects Against

1. **Unauthorized embedding** - Competitors can't put your widget on their site
2. **Phishing attacks** - Attackers can't create fake versions on malicious domains
3. **Casual abuse** - Most attackers won't bother scripting around protections
4. **API key exposure** - Secret key never leaves the server

### Why Origin Validation Works

The `Origin` header is a "forbidden header" in browsers - JavaScript cannot modify or spoof it. This is enforced by all modern browsers as part of CORS security.

| Scenario | Can Spoof Origin? | Protection |
|----------|-------------------|------------|
| Attacker embeds widget on `evil-site.com` | ❌ No | Browser sends real origin, server rejects |
| Attacker uses curl/Python scripts | ✅ Yes | Rate limiting + IP logging detect abuse |
| Attacker inspects Widget ID in DevTools | N/A | Widget ID is public, not a secret |

### The Scripted Attack Scenario

**Q: Can't a hacker just spoof the Origin header?**

**A: Only from outside a browser.** They could run:
```bash
curl -H "Origin: https://legitimate-site.com" https://your-server.com/chat/stream
```

But this is acceptable because:
1. **Rate limiting per IP** - Scripts get blocked after X requests/hour
2. **IP logging** - Detects and enables blocking of abuse patterns
3. **No sensitive data exposed** - Attacker can only chat, not access user data
4. **Cost is limited** - They consume API tokens but gain nothing valuable

### Defense in Depth

| Layer | Protects Against |
|-------|------------------|
| Origin validation | Unauthorized website embedding |
| Rate limiting | Scripted abuse, token exhaustion |
| IP logging | Pattern detection, blocking repeat offenders |
| Widget enable/disable | Quick response to abuse |

### For Higher Security (If Needed)

If protecting against sophisticated attackers or sensitive data:

1. **CAPTCHA** on first message - proves human
2. **Proof-of-work challenge** - computational cost deters bots
3. **Server-side session tokens** - issued by customer's backend
4. **Signed embeds** - time-limited tokens with HMAC signatures

For a publisher chatbot serving public content, the base security model (Origin + rate limiting + IP logging) is typically sufficient.

## Database Schema

### New Table: `widgets`

```sql
CREATE TABLE widgets (
    id INTEGER PRIMARY KEY,
    widget_id TEXT UNIQUE NOT NULL,     -- Public ID: "wid_abc123"
    api_key TEXT UNIQUE NOT NULL,       -- Secret: "sk_..." (for admin ops)
    name TEXT NOT NULL,                 -- Friendly name
    allowed_domains TEXT NOT NULL,      -- JSON array: ["mysite.com", "www.mysite.com"]
    config TEXT,                        -- JSON: {color, welcome_message, content_types}
    rate_limit INTEGER DEFAULT 100,     -- Requests per hour
    enabled BOOLEAN DEFAULT 1,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
```

### New Table: `widget_requests`

```sql
CREATE TABLE widget_requests (
    id INTEGER PRIMARY KEY,
    widget_id TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    origin TEXT,
    timestamp TEXT NOT NULL,
    status TEXT                         -- "allowed", "rate_limited", "invalid_origin"
);
```

## Implementation Steps

### 1. Create Widget Module

**File:** `src/modules/widgets/__init__.py`

Functions:
- `create_widget(name, allowed_domains, config)` → returns widget_id, api_key
- `get_widget(widget_id)` → widget config or None
- `validate_origin(widget_id, origin)` → bool
- `check_rate_limit(widget_id)` → bool
- `log_request(widget_id, ip, user_agent, origin, status)`
- `update_widget(api_key, ...)` → update config (requires secret key)

### 2. Add Validation Middleware

**File:** `src/chat_frontend/routes.py`

Add to `/chat` and `/chat/stream`:

```python
widget_id = request.query_params.get("widget_id")
origin = request.headers.get("origin")

if widget_id:
    widget = get_widget(widget_id)
    if not widget or not widget["enabled"]:
        return JSONResponse({"error": "Invalid widget"}, status_code=403)

    if not validate_origin(widget_id, origin):
        log_request(widget_id, ip, ua, origin, "invalid_origin")
        return JSONResponse({"error": "Unauthorized domain"}, status_code=403)

    if not check_rate_limit(widget_id):
        log_request(widget_id, ip, ua, origin, "rate_limited")
        return JSONResponse({"error": "Rate limit exceeded"}, status_code=429)

    log_request(widget_id, ip, ua, origin, "allowed")
    # Pass widget config to template/response
```

### 3. Update Widget Script

**File:** `static/chat-widget.js`

Add `data-widget-id` attribute:

```javascript
const config = {
    widgetId: script.dataset.widgetId || null,
    // ... existing config
};

// Pass widget_id to iframe URL
const chatUrl = baseUrl + '/chat' + (config.widgetId ? `?widget_id=${config.widgetId}` : '');
```

### 4. Update Chat Templates

**File:** `src/chat_frontend/templates/chat.html`

- Accept widget config from server
- Apply custom colors, welcome message
- Pass widget_id in stream requests

### 5. Add Admin Endpoints

**File:** `src/admin_frontend/routes.py`

- `GET /admin/api/widgets` - List all widgets
- `POST /admin/api/widgets` - Create widget (returns widget_id + api_key)
- `GET /admin/api/widgets/{widget_id}/stats` - Request stats

### 6. CLI Script for Widget Management

**File:** `scripts/manage_widgets.py`

```bash
uv run python scripts/manage_widgets.py create --name "My Site" --domains mysite.com,www.mysite.com
# Output: Widget ID: wid_abc123, API Key: sk_secret...

uv run python scripts/manage_widgets.py list
uv run python scripts/manage_widgets.py disable wid_abc123
```

## Files to Create/Modify

| File | Action |
|------|--------|
| `src/modules/widgets/__init__.py` | Create - widget CRUD, validation |
| `src/modules/widgets/models.py` | Create - database operations |
| `src/chat_frontend/routes.py` | Modify - add validation |
| `src/chat_frontend/templates/chat.html` | Modify - accept config |
| `src/chat_frontend/templates/base.html` | Modify - dynamic colors |
| `static/chat-widget.js` | Modify - add widget_id param |
| `src/admin_frontend/routes.py` | Modify - widget management endpoints |
| `scripts/manage_widgets.py` | Create - CLI management |
| `scripts/init_db.py` | Modify - add new tables |

## Customer Usage

After implementation, customers embed widgets like this:

```html
<script
  src="https://your-server.com/static/chat-widget.js"
  data-widget-id="wid_abc123">
</script>
```

## Customization Options (Future)

Per-widget configuration stored in `config` JSON field:

```json
{
  "color": "#1a1a2e",
  "welcome_message": "Hi! How can I help?",
  "content_types": ["articles", "events"],
  "show_sources": true
}
```

## Future Enhancements

- Customer self-service portal for widget management
- API key rotation
- Webhook notifications for events
- Custom system prompts per widget
- Analytics dashboard per widget
- White-label branding options
