# Frontend Architecture: Design Considerations

This document explores the design trade-offs for deploying a chat widget in two modes:

1. **Embedded** - Component or iframe on third-party websites
2. **Standalone** - Full-page interface hosted directly

---

## Part 1: Embedded Deployment

### 1.1 Iframe vs. Embedded Component

#### Option A: Iframe Embedding

```html
<!-- Client adds this to their site -->
<iframe
  src="https://chat.publisher.com/widget?key=abc123"
  width="400"
  height="600"
  style="border: none;">
</iframe>
```

**Advantages:**

- Complete style isolation (client CSS cannot break widget)
- Security sandboxing (runs in separate origin context)
- Simple integration - clients just paste HTML
- No JavaScript conflicts with client's site
- Works regardless of client's tech stack
- Easy to update (changes deploy immediately)

**Disadvantages:**

- Fixed dimensions (responsive design is harder)
- Cross-origin communication requires `postMessage` API
- Cannot inherit client's fonts/design system
- Browser treats as separate page (memory overhead)
- Some browsers block third-party cookies in iframes
- Mobile UX can be awkward (scroll within scroll)

**When to use:**

- Clients have varying/unknown tech stacks
- Security isolation is important
- Minimal integration effort required
- Widget is self-contained (doesn't need to interact with host page)

---

#### Option B: Embedded Component (Script Tag)

```html
<!-- Client adds this to their site -->
<script src="https://chat.publisher.com/widget.js"></script>
<publisher-chat
  api-key="abc123"
  theme="dark"
  position="bottom-right">
</publisher-chat>
```

**Advantages:**

- Fully responsive (adapts to container)
- Can inherit client styles (if desired)
- Richer integration options (callbacks, events, APIs)
- Better mobile UX (native scrolling)
- Can access parent page context (with permission)
- Feels more "native" to the site

**Disadvantages:**

- CSS conflicts possible (must use Shadow DOM or careful scoping)
- JavaScript conflicts possible (global namespace pollution)
- More complex implementation
- Client must trust your JavaScript (security concern)
- Harder to debug issues on client sites
- Version management more complex

**When to use:**

- Deep integration with host page needed
- Responsive sizing required
- Premium/custom client experiences
- Clients want styling control

---

### 1.2 Technical Implementation Approaches

#### For Iframe Embedding

| Aspect | Approach |
|--------|----------|
| **Backend** | Any (FastAPI serves complete page) |
| **Frontend** | HTMX, React, or vanilla - doesn't matter to client |
| **Communication** | `postMessage` for parent ↔ iframe events |
| **Sizing** | Fixed, or use `postMessage` to request resize |
| **Auth** | URL parameter token or cookie (beware SameSite) |

**Parent-child communication pattern:**

```javascript
// In iframe (widget)
window.parent.postMessage({ type: 'widget-ready', height: 500 }, '*');

// In parent (client site)
window.addEventListener('message', (e) => {
  if (e.data.type === 'widget-ready') {
    iframe.style.height = e.data.height + 'px';
  }
});
```

---

#### For Embedded Component

| Aspect | Approach |
|--------|----------|
| **Implementation** | Web Components (native) or framework compiled to WC |
| **Style isolation** | Shadow DOM (recommended) or CSS namespacing |
| **Bundle** | Single JS file, ideally <50kb gzipped |
| **API calls** | Fetch to your backend (requires CORS) |
| **State** | Component-local or connects to your backend |

**Web Component example:**

```javascript
class PublisherChat extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  connectedCallback() {
    const apiKey = this.getAttribute('api-key');
    this.shadowRoot.innerHTML = `
      <style>/* Scoped styles */</style>
      <div class="chat-container">...</div>
    `;
  }
}
customElements.define('publisher-chat', PublisherChat);
```

---

### 1.3 Cross-Origin Considerations

| Concern | Iframe | Embedded Component |
|---------|--------|-------------------|
| **CORS** | Not needed (same-origin from iframe's perspective) | Required on all API endpoints |
| **Cookies** | May be blocked (third-party context) | Works normally |
| **localStorage** | Separate per iframe origin | Shared with host page |
| **Auth tokens** | Pass via URL or postMessage | Store in memory or host's storage |

**CORS configuration for embedded component:**

```python
# FastAPI
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or specific client domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

### 1.4 Mobile App Considerations

Both approaches work in mobile WebViews:

| Platform | Iframe | Embedded Component |
|----------|--------|-------------------|
| **iOS WKWebView** | ✅ Works | ✅ Works |
| **Android WebView** | ✅ Works | ✅ Works |
| **React Native WebView** | ✅ Works | ✅ Works |

**Mobile-specific concerns:**

- Touch event handling
- Virtual keyboard pushing content
- Safe area insets (notches)
- Deep linking from chat to native app features

---

## Part 2: Standalone Interface

### 2.1 Use Cases

- Direct link to chat (chat.publisher.com)
- Full-screen mobile experience
- Internal tools / admin testing
- Demo and sales presentations

### 2.2 Technical Approaches

#### Option A: Vanilla JS + Jinja2 Templates (Current Implementation) ✅

```text
Browser → FastAPI → Renders HTML → Returns complete page
         ↓
    Vanilla JS handles form submission
    Fetch API with ReadableStream for real-time streaming
```

**Advantages:**

- Zero framework dependencies (only marked.js for markdown)
- Stays in Python ecosystem (no Node.js build)
- Fast initial page load
- Full control over streaming behavior
- Simple to understand and modify
- ~50kb total JS (marked.js only)

**Disadvantages:**

- Manual DOM manipulation
- No component abstraction
- State management is ad-hoc

**This is our current implementation** - see `src/chat_frontend/`.

---

#### ~~Option B: HTMX + Jinja2~~ (Not Recommended for Chat)

We initially tried HTMX but found it **not suitable for streaming chat**:

**Why HTMX doesn't work well:**

1. **Buffering issue**: HTMX waits for the complete HTTP response before swapping content. Even with `StreamingResponse`, HTMX buffers everything client-side.

2. **No incremental rendering**: Status messages ("Searching...", "Thinking...") don't appear until the entire response completes.

3. **SSE complexity**: The HTMX SSE extension requires a different request model (GET with event stream) that doesn't fit the form-submit-then-stream pattern.

4. **Hybrid overhead**: We ended up needing vanilla JS for streaming anyway, making HTMX redundant (added 14kb for minimal benefit).

**When HTMX works well:**

- Traditional form submissions with HTML responses
- Partial page updates (load more, filters, tabs)
- Server-rendered UI with simple interactions
- **Not** real-time streaming or chat interfaces

---

#### Option C: Single Page Application (React/Vue/Svelte)

```text
Browser → Load JS bundle → API calls to FastAPI
                ↓
    Client-side rendering and state management
    Fetch/WebSocket for streaming
```

**Advantages:**

- Rich, app-like experience
- Client-side caching and optimistic updates
- Large ecosystem of components
- Can share code with embedded component
- Offline capability possible

**Disadvantages:**

- Larger bundle size (100kb+)
- Separate build pipeline (Node.js)
- More complex state management
- Two codebases to maintain

---

### 2.3 Streaming Responses

For LLM chat interfaces, **Fetch + ReadableStream** is the recommended approach:

| Approach | Streaming Method | Works for Chat? |
|----------|-----------------|-----------------|
| **Vanilla JS** | Fetch + ReadableStream | ✅ Yes - full control |
| **React/Vue** | Fetch + ReadableStream | ✅ Yes |
| **HTMX** | SSE extension | ⚠️ Awkward - requires GET, no request body |
| **WebSocket** | Bidirectional | ✅ Yes - also allows interruption |

**Current implementation (ndjson streaming):**

```python
# FastAPI endpoint
@router.get("/stream")
async def stream_response(message: str) -> StreamingResponse:
    def generate():
        yield '{"type": "status", "content": "Searching..."}\n'
        chunks = search(message)
        yield '{"type": "status", "content": "Thinking..."}\n'
        for token in llm_stream(message, chunks):
            yield json.dumps({"type": "token", "content": token}) + "\n"
        yield '{"type": "done"}\n'

    return StreamingResponse(generate(), media_type="application/x-ndjson")
```

```javascript
// Client-side consumption
const response = await fetch(`/chat/stream?message=${encoded}`);
const reader = response.body.getReader();
while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    // Parse ndjson lines and update UI
}
```

---

## Part 3: Recommendation Matrix

### Choose Iframe Embedding When

- ✅ Clients have diverse/unknown tech stacks
- ✅ Security isolation is paramount
- ✅ Minimal integration support desired
- ✅ Widget is self-contained

### Choose Embedded Component When

- ✅ Clients want styling/behavior customization
- ✅ Deep integration with host page needed
- ✅ Responsive sizing required
- ✅ Building premium/enterprise offering

### Choose Vanilla JS for Standalone When

- ✅ Building a streaming chat interface
- ✅ Want minimal dependencies
- ✅ Need full control over streaming behavior
- ✅ Simple requirements, no complex state

### ~~Choose HTMX for Standalone When~~ (Not for Chat)

- ⚠️ HTMX is **not recommended** for streaming chat interfaces
- ✅ Good for traditional server-rendered apps with simple interactions
- ✅ Good for partial page updates, forms, tabs
- ❌ Buffers responses - no real-time streaming

### Choose React SPA for Standalone When

- ✅ Already have React expertise
- ✅ Complex client-side features planned
- ✅ Want to share code with embedded component
- ✅ App-like experience is priority

---

## Part 4: Suggested Architecture

For maximum flexibility, build **one frontend** that works in all modes:

```text
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Backend                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │ /api/*      │  │ /stream     │  │ /static/*       │  │
│  │ REST APIs   │  │ SSE endpoint│  │ JS/CSS/assets   │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
│  Standalone  │  │    Iframe    │  │ Embedded Component│
│  /chat       │  │ /widget      │  │ /widget.js        │
│  Full page   │  │ Mini version │  │ Web Component     │
└──────────────┘  └──────────────┘  └──────────────────┘
```

**Shared core:**

- Chat UI components
- API client
- Streaming handler
- Message formatting

**Mode-specific:**

- Standalone: Full layout, navigation, sidebar
- Iframe: Compact layout, no navigation
- Embedded: Shadow DOM wrapper, event dispatching

---

## Part 5: Product Requirements (Known)

### Multi-Tenant Configuration

Each tenant (publisher) can have:

- **Custom system prompts** - different emphasis, tone, content focus
- **Possibly custom branding** - colors, logo (lower priority)
- **Separate analytics** - track engagement per tenant

**Implementation approach:**

```text
/widget?tenant=publisher-abc
        ↓
Backend looks up tenant config from DB
        ↓
Uses tenant-specific system prompt for LLM
```

Tenant config stored server-side, not exposed to browser.

---

### Authentication Strategy

**Requirement:** API key must not be visible to browser (prevent theft by page visitors)

#### Approach: Server-side tenant validation

```text
┌─────────────────────────────────────────┐
│  Client Website                         │
│                                         │
│  <iframe src="chat.you.com/widget      │
│           ?tenant=abc123">              │
│                                         │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│  Your Server                            │
│                                         │
│  1. Validate tenant ID exists           │
│  2. Check referrer/origin (optional)    │
│  3. Load tenant config                  │
│  4. No API key exposed to browser       │
│                                         │
└─────────────────────────────────────────┘
```

**Options for tenant validation:**

| Method | Security | Complexity |
|--------|----------|------------|
| Tenant ID only | Low - anyone can use ID | Simple |
| Tenant ID + allowed domains | Medium - domain allowlist | Medium |
| Signed token (JWT) | High - server generates, short-lived | More complex |

**Recommendation:** Start with tenant ID + domain allowlist. Upgrade to signed tokens if needed.

---

### Conversation Persistence

**Requirement:** Conversations persist across sessions

**Implementation:**

- Generate session ID on first visit (stored in cookie or localStorage)
- Link messages to session ID in database
- On return visit, load conversation history

**Considerations:**

| Storage | Iframe | Embedded Component |
|---------|--------|-------------------|
| Cookies | ⚠️ May be blocked (3rd party) | ✅ Works |
| localStorage | ✅ Works (iframe's origin) | ⚠️ Shared with host |
| Server-side only | ✅ Works everywhere | ✅ Works everywhere |

**Recommendation:** Server-side session with cookie fallback. Generate UUID, store in cookie if possible, otherwise pass in URL.

---

### Analytics (Current Scope)

Track for now:

- ✅ Content impressions (what was shown)
- ✅ URL clicks (what was clicked)
- ✅ Per-tenant breakdown

Future considerations:

- Message volume per tenant
- Response quality signals (thumbs up/down)
- Session duration
- Conversation completion rate

---

### Mobile

Not a priority currently. Both iframe and embedded component approaches will work in mobile WebViews when needed.

---

## Open Questions (Remaining)

1. **Domain allowlist:** Should tenants configure allowed domains, or start open?
2. **Rate limiting:** Per-tenant limits on API calls?
3. **Tenant onboarding:** How are new tenants created? Admin UI? API?
4. **Session expiry:** How long should conversation history persist?
