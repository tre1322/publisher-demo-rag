# Vanilla JS Frontend Documentation

A lightweight chat interface using vanilla JavaScript with streaming support. No framework dependencies - just Jinja2 templates and marked.js for Markdown rendering.

## Location

- **Source**: `src/chat_frontend/`
- **URL**: `http://localhost:7860/chat`

---

## File Structure

```
src/chat_frontend/
├── __init__.py          # Exports router
├── routes.py            # FastAPI routes
└── templates/
    ├── base.html        # Base template
    └── chat.html        # Chat page
```

---

## Routes

### `GET /chat`

Renders the chat page.

```python
@router.get("", response_class=HTMLResponse)
async def chat_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("chat.html", {"request": request})
```

### `GET /chat/stream`

Streams the assistant response as ndjson.

```python
@router.get("/stream")
async def stream_response(message: str) -> StreamingResponse:
    # Returns streaming ndjson response
```

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `message` | string | User's message (URL-encoded) |

**Response Format:** See [CHAT_SERVER.md](./CHAT_SERVER.md#streaming-response-format)

---

## Templates

### `base.html`

Base template with:
- Meta viewport for responsive design
- marked.js CDN include
- Basic CSS reset and layout styles
- Header with title
- Block placeholders: `title`, `extra_styles`, `content`, `scripts`

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Publisher Chat{% endblock %}</title>
    <script src="https://unpkg.com/marked@15.0.4/marked.min.js"></script>
    <style>/* Base styles */</style>
</head>
<body>
    <header><h1>Publisher News Assistant</h1></header>
    {% block content %}{% endblock %}
    {% block scripts %}{% endblock %}
</body>
</html>
```

### `chat.html`

Chat interface extending base.html:
- Message container with auto-scroll
- Input form with text field and submit button
- Inline CSS for chat-specific styling
- JavaScript for streaming and Markdown rendering

---

## CSS Classes

| Class | Description |
|-------|-------------|
| `.container` | Max-width centered container |
| `.chat-container` | White card with shadow |
| `.messages` | Scrollable message list |
| `.message` | Individual message bubble |
| `.message.user` | Dark blue, right-aligned |
| `.message.assistant` | Light gray, left-aligned |
| `.message.status` | Yellow, italic (Searching/Thinking) |
| `.input-area` | Form container with border |
| `.input-form` | Flexbox form layout |

---

## JavaScript

### Dependencies

- **marked.js** (v15.0.4) - Markdown to HTML conversion

### Initialization

```javascript
// Configure marked for GitHub-flavored markdown
marked.setOptions({
    breaks: true,  // Convert \n to <br>
    gfm: true      // GitHub-flavored markdown
});

// Custom renderer for links - always open in new tab
const renderer = new marked.Renderer();
renderer.link = function(href, title, text) {
    if (typeof href === 'object') {
        // New marked.js API
        const token = href;
        return `<a href="${token.href}" target="_blank" rel="noopener">${token.text}</a>`;
    }
    // Old API fallback
    return `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;
};
marked.setOptions({ renderer });
```

### Core Functions

#### `escapeHtml(text)`

Prevents XSS in user input:

```javascript
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
```

#### `addMessage(role, content, id)`

Adds a message to the chat:

```javascript
function addMessage(role, content, id = null) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    if (id) div.id = id;
    div.innerHTML = content;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
}
```

#### `streamResponse(message)`

Fetches streaming response and updates UI:

```javascript
async function streamResponse(message) {
    const statusEl = addMessage('status', 'Searching...', 'status');
    const responseEl = addMessage('assistant', '', 'response');
    let accumulated = '';

    const response = await fetch(`/chat/stream?message=${encodeURIComponent(message)}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const text = decoder.decode(value);
        const lines = text.split('\n').filter(line => line.trim());

        for (const line of lines) {
            const data = JSON.parse(line);

            if (data.type === 'status') {
                statusEl.textContent = data.content;
            } else if (data.type === 'token') {
                accumulated += data.content;
                responseEl.innerHTML = marked.parse(accumulated);
            } else if (data.type === 'done') {
                statusEl.remove();
            } else if (data.type === 'error') {
                statusEl.remove();
                responseEl.textContent = 'Error: ' + data.content;
            }
        }
    }
}
```

### Form Handler

```javascript
form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const message = input.value.trim();
    if (!message) return;

    // Disable form while processing
    input.disabled = true;
    sendBtn.disabled = true;

    // Add user message (escaped)
    addMessage('user', escapeHtml(message));

    // Clear and stream
    input.value = '';
    await streamResponse(message);

    // Re-enable form
    input.disabled = false;
    sendBtn.disabled = false;
    input.focus();
});
```

---

## Streaming Flow

```
1. User submits form
   ↓
2. Form disabled, user message added to UI
   ↓
3. fetch('/chat/stream?message=...')
   ↓
4. ReadableStream reader created
   ↓
5. Loop: read chunks from stream
   │
   ├─ {"type": "status", "content": "Searching..."}
   │   → Update status element text
   │
   ├─ {"type": "status", "content": "Thinking..."}
   │   → Update status element text
   │
   ├─ {"type": "token", "content": "Hello"}
   │   → Accumulate + re-render Markdown
   │
   ├─ {"type": "done"}
   │   → Remove status element
   │
   └─ {"type": "error", "content": "..."}
       → Remove status, show error
   ↓
6. Form re-enabled, focus restored
```

---

## Why Vanilla JS?

We initially tried HTMX but found it unsuitable for streaming chat:

1. **HTMX buffers responses** - waits for complete response before swapping
2. **No incremental rendering** - status messages don't appear during processing
3. **SSE complexity** - requires different request pattern
4. **Minimal benefit** - ended up needing JS anyway for streaming

Vanilla JS with `fetch()` + `ReadableStream` gives full control over streaming behavior with minimal code (~100 lines).

See `docs/specs/FRONTEND_ARCHITECTURE.md` for detailed comparison.

---

## Differences from Gradio Frontend

| Feature | Gradio | Vanilla JS |
|---------|--------|------------|
| Sidebar | Yes (articles, ads) | No |
| Conversation persistence | Yes (database) | No (session only) |
| URL tracking | Yes | No |
| Analytics | Yes | No |
| Streaming | Yes | Yes |
| Markdown | Yes | Yes |

The vanilla JS frontend is a minimal implementation suitable for:
- Embedding in iframes
- Lightweight deployments
- Development/testing
- Custom integrations
