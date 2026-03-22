# Frontend Components

This document covers the frontend components: Chat UI, Admin Dashboard, Embeddable Widget, and Demo Integration.

## Chat Frontend

**Location**: `src/chat_frontend/`

### Routes (`routes.py`)

| Route | Method | Description |
|-------|--------|-------------|
| `/chat` | GET | Render chat page |
| `/chat/stream` | GET | Stream response |
| `/chat/history` | GET | Get session history |

See [API Reference](API.md#chat-api) for details.

### Templates

#### Base Template (`templates/base.html`)

Shared layout for all chat pages.

**Features**:
- Inter font family (Google Fonts)
- CSS framework toggle (Pico CSS ↔ Bulma)
- Theme persistence via localStorage
- Iframe detection for embedded mode
- Dual headers (full for standalone, compact for iframe)

**CSS Framework Switching**:
```javascript
// Toggle between Pico and Bulma
function setTheme(theme) {
    if (theme === 'bulma') {
        picoCSS.disabled = true;
        bulmaCSS.disabled = false;
    } else {
        picoCSS.disabled = false;
        bulmaCSS.disabled = true;
    }
    localStorage.setItem('chat-theme', theme);
}
```

**Iframe Detection**:
```javascript
const inIframe = window.self !== window.top;
if (inIframe) {
    document.body.classList.add('in-iframe');
}
```

When in iframe:
- Full header is hidden
- Compact header with minimize button is shown
- Minimize button sends `postMessage` to parent

#### Chat Template (`templates/chat.html`)

Main chat interface.

**Structure**:
```html
<div class="chat-container">
    <div class="chat-box">
        <div class="messages" id="messages">
            <!-- Messages rendered here -->
        </div>
        <div class="input-area">
            <form class="input-form">
                <input type="text" id="message-input">
                <button type="submit">Send</button>
            </form>
        </div>
    </div>
</div>
```

**CSS Layout** (for iframe embedding):
```css
/* Fill iframe height exactly */
html, body {
    height: 100%;
    overflow: hidden;
}

.chat-container {
    height: 100vh;
    display: flex;
    flex-direction: column;
}

/* When in iframe, use flex instead of fixed height */
.in-iframe .chat-container {
    height: auto;
    flex: 1;
    min-height: 0;
}

.messages {
    flex: 1;
    overflow-y: auto;
    min-height: 0;  /* Critical for flex scroll */
}

.input-area {
    flex-shrink: 0;  /* Never shrink */
}
```

### JavaScript Functionality

**Session Management**:
```javascript
// Generate or restore session ID
const sessionId = localStorage.getItem('chat-session') || crypto.randomUUID();
localStorage.setItem('chat-session', sessionId);
```

**History Loading**:
```javascript
async function loadHistory() {
    const response = await fetch(`/chat/history?session_id=${sessionId}`);
    const data = await response.json();

    if (data.messages && data.messages.length > 0) {
        // Remove welcome message
        welcomeMessage?.remove();

        // Render historical messages
        for (const msg of data.messages) {
            if (msg.role === 'user') {
                addMessage('user', escapeHtml(msg.content));
            } else {
                addMessage('assistant', marked.parse(msg.content));
            }
        }
    }
}
```

**Streaming Response**:
```javascript
async function streamResponse(message) {
    const responseEl = addMessage('assistant', '<span class="loading-dots">Searching</span>');
    let accumulated = '';

    const response = await fetch(`/chat/stream?message=${encodedMessage}&session_id=${sessionId}`);
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
                responseEl.innerHTML = `<span class="loading-dots">${data.content}</span>`;
            } else if (data.type === 'token') {
                accumulated += data.content;
                responseEl.innerHTML = marked.parse(sanitizePartialHtml(accumulated));
            } else if (data.type === 'done') {
                responseEl.innerHTML = marked.parse(accumulated);
            }
        }
    }
}
```

**Markdown Rendering**:
- Uses `marked.js` library (v15.0.4)
- GitHub-flavored markdown enabled
- Custom link renderer opens links in new tab:
```javascript
renderer.link = function(href, title, text) {
    return `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;
};
```

**HTML Sanitization** (during streaming):
```javascript
function sanitizePartialHtml(text) {
    // Hide incomplete HTML tags (e.g., "<a href=..." without ">")
    const lastOpen = text.lastIndexOf('<');
    if (lastOpen !== -1) {
        const lastClose = text.lastIndexOf('>');
        if (lastClose < lastOpen) {
            return text.substring(0, lastOpen);
        }
    }
    return text;
}
```

---

## Admin Dashboard

**Location**: `src/admin_frontend/`

### Authentication

HTTP Basic Authentication:
- Username: `admin`
- Password: `ADMIN_PASSWORD` env var (default: `admin`)

```python
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials):
    if not (
        secrets.compare_digest(credentials.username, "admin") and
        secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    ):
        raise HTTPException(status_code=401)
```

### Template (`templates/admin.html`)

Uses **Tabler** CSS framework (v1.0.0).

**Structure**:
```html
<div class="page-wrapper">
    <!-- Sidebar Navigation -->
    <aside class="navbar navbar-vertical">
        <nav class="nav">
            <a href="#queries" data-tab="queries">Query Analytics</a>
            <a href="#conversations" data-tab="conversations">Conversations</a>
            <a href="#export" data-tab="export">Export</a>
            <a href="#engagement" data-tab="engagement">Engagement</a>
            <a href="#database" data-tab="database">Database</a>
        </nav>
    </aside>

    <!-- Main Content -->
    <div class="page-body">
        <!-- Stats Cards -->
        <div class="row">
            <div class="col">Total Conversations: <span id="stat-conversations"></span></div>
            <div class="col">Total Messages: <span id="stat-messages"></span></div>
            <!-- ... -->
        </div>

        <!-- Tab Content -->
        <div id="tab-queries" class="tab-pane active">...</div>
        <div id="tab-conversations" class="tab-pane">...</div>
        <!-- ... -->
    </div>
</div>
```

### Tab Functionality

**5 Tabs**:

1. **Query Analytics**
   - Most common user queries
   - Word frequency analysis
   - Configurable limit and top_n

2. **Conversations**
   - Recent conversations list
   - Session ID, start time, message count, duration
   - Message preview (first user + assistant messages)

3. **Export**
   - Export conversations to JSON
   - Downloads to `data/conversations_export.json`

4. **Engagement**
   - Total impressions, clicks, CTR
   - CTR breakdown by content type
   - Top clicked/shown content

5. **Database Browser**
   - Table selector dropdown
   - Paginated data view
   - All 7 tables browsable

**Lazy Loading**:
```javascript
const loadedTabs = new Set();

function switchTab(tabName) {
    // Show/hide tab content
    document.querySelectorAll('.tab-pane').forEach(el => {
        el.classList.toggle('active', el.id === `tab-${tabName}`);
    });

    // Load data if not already loaded
    if (!loadedTabs.has(tabName)) {
        loadedTabs.add(tabName);
        loadTabData(tabName);
    }
}
```

**Pagination**:
```javascript
let currentPage = 1;
let currentTable = 'articles';

async function loadTableData() {
    const response = await fetch(
        `/admin/api/table/${currentTable}?page=${currentPage}&page_size=25`
    );
    const data = await response.json();
    renderTable(data.columns, data.rows);
    updatePagination(data.page, data.total_pages);
}

function nextPage() {
    if (currentPage < totalPages) {
        currentPage++;
        loadTableData();
    }
}
```

---

## Embeddable Widget

**Location**: `static/chat-widget.js`

A self-contained JavaScript file that publishers can embed with a single script tag.

### Integration

**Basic**:
```html
<script src="https://yoursite.com/static/chat-widget.js"></script>
```

**With Configuration**:
```html
<script
    src="https://yoursite.com/static/chat-widget.js"
    data-position="bottom-right"
    data-color="#1a1a2e"
    data-size="normal"
></script>
```

### Configuration Options

| Attribute | Values | Default | Description |
|-----------|--------|---------|-------------|
| `data-position` | `bottom-right`, `bottom-left` | `bottom-right` | Button/modal position |
| `data-color` | Any CSS color | `#1a1a2e` | Button and header color |
| `data-size` | `normal`, `large` | `normal` | Modal size |

**Size Presets**:
- `normal`: 380px × 520px
- `large`: 450px × 600px

### How It Works

1. **Self-Execution**: Script runs immediately on load (IIFE pattern)

2. **Configuration Reading**:
   ```javascript
   const script = document.currentScript;
   const config = {
       position: script.dataset.position || 'bottom-right',
       color: script.dataset.color || '#1a1a2e',
       size: script.dataset.size || 'normal',
       chatUrl: baseUrl + '/chat'
   };
   ```

3. **CSS Injection**: Styles injected via `<style>` element

4. **DOM Creation**:
   - Floating button (60px circle)
   - Modal container (fixed position)

5. **Lazy Iframe Loading**: iframe created on first open only

6. **Event Handling**:
   - Click: toggle open/close
   - Escape key: close
   - postMessage: respond to iframe messages

### PostMessage API

**From iframe to parent**:
```javascript
// In chat.html (minimize button)
window.parent.postMessage({ type: 'chat-minimize' }, '*');
```

**Widget listens for**:
| Message Type | Action |
|--------------|--------|
| `chat-close` | Close modal |
| `chat-minimize` | Close modal |
| `chat-open` | Open modal |

### Programmatic Control

```javascript
// Open chat
ChatWidget.open();

// Close chat
ChatWidget.close();

// Toggle
ChatWidget.toggle();
```

### Responsive Design

On mobile (< 480px):
- Modal: full viewport width minus 20px
- Button: moved to 10px from edge
- Border radius reduced

```css
@media (max-width: 480px) {
    #chat-widget-modal {
        width: calc(100vw - 20px);
        height: calc(100vh - 100px);
    }
}
```

---

## Demo Integration

**Location**: `src/mock_integrations/`

### Newspaper Demo (`/demo/newspaper`)

A newspaper-style page demonstrating chat integration options.

**Template**: `templates/newspaper.html`

### Embed Modes

The demo offers 4 embed modes, switchable via toggle buttons:

#### 1. Floating Widget (Default)

Uses `chat-widget.js`:
```html
<script src="/static/chat-widget.js"></script>
```

Widget visibility controlled by body class:
```css
body:not(.embed-floating) #chat-widget-button,
body:not(.embed-floating) #chat-widget-modal {
    display: none !important;
}
```

#### 2. Sidebar

Fixed right sidebar with iframe:
```html
<div class="chat-sidebar" id="chat-sidebar">
    <iframe src="/chat" title="Chat Assistant"></iframe>
</div>
```

```css
.chat-sidebar {
    position: fixed;
    top: 0;
    right: 0;
    width: 400px;
    height: 100vh;
}

.chat-sidebar.active {
    display: block;
}

/* Main content shifts when sidebar active */
.main-content.sidebar-active {
    margin-right: 420px;
}
```

#### 3. Inline

Chat embedded in page flow:
```html
<div class="chat-inline" id="chat-inline">
    <div class="chat-inline-header">Ask Our AI Assistant</div>
    <iframe src="/chat" title="Chat Assistant"></iframe>
</div>
```

```css
.chat-inline {
    max-width: 800px;
    margin: 40px auto;
    height: 500px;
}
```

#### 4. None

All chat UI hidden.

### Mode Switching

```javascript
function setEmbedMode(mode) {
    // Update button states
    toggleButtons.forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });

    // Update body class
    document.body.classList.remove('embed-floating', 'embed-sidebar', 'embed-inline', 'embed-none');
    document.body.classList.add('embed-' + mode);

    // Hide all embeds
    chatSidebar.classList.remove('active');
    chatInline.classList.remove('active');
    mainContent.classList.remove('sidebar-active');

    // Close widget if switching away from floating
    if (mode !== 'floating' && window.ChatWidget) {
        ChatWidget.close();
    }

    // Show selected embed
    if (mode === 'sidebar') {
        chatSidebar.classList.add('active');
        mainContent.classList.add('sidebar-active');
    } else if (mode === 'inline') {
        chatInline.classList.add('active');
    }

    // Persist preference
    localStorage.setItem('embed-mode', mode);
}
```

### Layout Structure

```
┌─────────────────────────────────────────────────────────────┐
│  Demo Controls: [Floating] [Sidebar] [Inline] [None]        │
├─────────────────────────────────────────────────────────────┤
│                      MASTHEAD                               │
│                   The Daily Tribune                         │
│              Your Trusted Source for Local News             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              HERO ARTICLE                            │   │
│  │  Title, Author, Date, Summary                       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  MORE STORIES                                               │
│  ┌────────┐  ┌────────┐  ┌────────┐                       │
│  │ Card 1 │  │ Card 2 │  │ Card 3 │  ...                  │
│  └────────┘  └────────┘  └────────┘                       │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │         INLINE CHAT (when active)                   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                                              [💬] (floating)
```

---

## Styling Guidelines

### CSS Framework Selection

| Framework | Use Case | Loaded From |
|-----------|----------|-------------|
| Pico CSS | Chat interface (default) | CDN |
| Bulma | Chat interface (alternate) | CDN |
| Tabler | Admin dashboard | CDN |

### Color Scheme

| Color | Hex | Usage |
|-------|-----|-------|
| Primary | `#1a1a2e` | Headers, buttons, user messages |
| Assistant | `#f0f0f0` | Assistant message background |
| Link | `#2563eb` | Clickable links |
| Background | `#f8f8f8` | Page background |

### Typography

- **Font Family**: Inter (Google Fonts)
- **Weights**: 400, 500, 600, 700
- **Headings**: Playfair Display (newspaper demo only)

### Responsive Breakpoints

| Breakpoint | Target |
|------------|--------|
| < 480px | Mobile phones |
| < 768px | Tablets |
| ≥ 768px | Desktop |
