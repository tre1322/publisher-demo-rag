# Chat Widget Integration Guide

Embed the Publisher News Assistant as a floating chat widget on any website.

## Quick Start

Add this script tag to your website, just before the closing `</body>` tag:

```html
<script src="https://your-server.com/static/chat-widget.js"></script>
```

Replace `your-server.com` with your deployment URL (e.g., your Digital Ocean app URL).

That's it! A floating chat button will appear in the bottom-right corner.

---

## Configuration Options

Customize the widget using data attributes on the script tag:

```html
<script
  src="https://your-server.com/static/chat-widget.js"
  data-position="bottom-right"
  data-color="#1a1a2e"
  data-size="normal">
</script>
```

### Available Options

| Attribute | Values | Default | Description |
|-----------|--------|---------|-------------|
| `data-position` | `bottom-right`, `bottom-left` | `bottom-right` | Corner placement |
| `data-color` | Any CSS color | `#1a1a2e` | Button and header color |
| `data-size` | `normal`, `large` | `normal` | Initial chat window size |

### Size Presets

| Size | Dimensions |
|------|------------|
| `normal` | 380px × 520px |
| `large` | 450px × 600px |
| `enlarged` | Near fullscreen (via enlarge button) |

---

## WordPress Integration

### Method 1: Theme Customizer (Recommended)

Many themes include a place for custom scripts:

1. Go to **Appearance → Customize**
2. Look for **Additional CSS/Scripts** or **Footer Scripts**
3. Paste the script tag
4. Click **Publish**

### Method 2: Insert Headers and Footers Plugin

1. Install the **Insert Headers and Footers** plugin (by WPCode)
2. Go to **Settings → Insert Headers and Footers**
3. Paste the script tag in the **Scripts in Footer** section
4. Save changes

### Method 3: Edit Theme Files

Edit your theme's `footer.php` file:

1. Go to **Appearance → Theme File Editor**
2. Select `footer.php` from the file list
3. Add the script tag before `</body>`:

```php
<!-- Publisher Chat Widget -->
<script src="https://your-server.com/static/chat-widget.js"></script>

<?php wp_footer(); ?>
</body>
</html>
```

4. Click **Update File**

> **Note:** Theme updates may overwrite this. Consider using a child theme.

### Method 4: Child Theme (Most Durable)

Create a child theme to preserve customizations:

1. Create `functions.php` in your child theme
2. Add this code:

```php
<?php
function add_chat_widget_script() {
    wp_enqueue_script(
        'publisher-chat-widget',
        'https://your-server.com/static/chat-widget.js',
        array(),
        '1.0',
        true  // Load in footer
    );
}
add_action('wp_enqueue_scripts', 'add_chat_widget_script');
```

### Method 5: Custom HTML Block (Page-Specific)

To add the widget to specific pages only:

1. Edit the page in the Block Editor
2. Add a **Custom HTML** block at the bottom
3. Paste the script tag
4. Publish

---

## JavaScript API

Control the widget programmatically after it loads:

```javascript
// Open the chat
ChatWidget.open();

// Close the chat
ChatWidget.close();

// Toggle open/closed
ChatWidget.toggle();

// Expand to near-fullscreen
ChatWidget.enlarge();

// Restore to normal size
ChatWidget.restore();

// Toggle between normal and enlarged
ChatWidget.toggleSize();
```

### Example: Open Chat on Button Click

```html
<button onclick="ChatWidget.open()">Chat with us!</button>
```

### Example: Open Chat After Delay

```html
<script>
  // Open chat after 30 seconds on the page
  setTimeout(function() {
    if (typeof ChatWidget !== 'undefined') {
      ChatWidget.open();
    }
  }, 30000);
</script>
```

### Example: Open Chat on Scroll

```html
<script>
  let chatOpened = false;
  window.addEventListener('scroll', function() {
    if (!chatOpened && window.scrollY > 500) {
      ChatWidget.open();
      chatOpened = true;
    }
  });
</script>
```

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Escape` | Close the chat window |

---

## Styling Customization

### Match Your Brand Colors

```html
<script
  src="https://your-server.com/static/chat-widget.js"
  data-color="#your-brand-color">
</script>
```

### Override Styles with CSS

Add custom CSS to fine-tune appearance:

```css
/* Make the button larger */
#chat-widget-button {
  width: 70px !important;
  height: 70px !important;
  font-size: 32px !important;
}

/* Adjust button position */
#chat-widget-button {
  bottom: 30px !important;
  right: 30px !important;
}

/* Custom shadow */
#chat-widget-modal {
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.3) !important;
}

/* Hide on specific pages (add class to body) */
body.no-chat #chat-widget-button,
body.no-chat #chat-widget-modal {
  display: none !important;
}
```

---

## Mobile Behavior

The widget automatically adapts on mobile devices (< 480px width):

- Chat window expands to nearly full screen
- Button repositions closer to screen edge
- Touch-friendly tap targets

---

## Troubleshooting

### Widget Doesn't Appear

1. **Check the console** - Open browser DevTools (F12) and look for errors
2. **Verify the URL** - Ensure the script URL is accessible
3. **Check for conflicts** - Other scripts might interfere

### Chat Window is Blank

1. **CORS issues** - The chat server must be publicly accessible
2. **Mixed content** - If your site is HTTPS, the chat server must also be HTTPS
3. **Server down** - Verify the chat server is running

### Button Appears But Chat Won't Open

1. **JavaScript error** - Check console for errors
2. **Z-index conflict** - Other elements might be overlapping

```css
/* Force widget above other elements */
#chat-widget-button,
#chat-widget-modal {
  z-index: 999999 !important;
}
```

### Styling Conflicts

If your site's CSS affects the widget:

```css
/* Reset common conflicts */
#chat-widget-button,
#chat-widget-modal,
#chat-widget-modal * {
  box-sizing: border-box;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
```

---

## Security Considerations

- The widget loads in an iframe, isolating it from your page
- Session data is stored in the user's browser (localStorage)
- Communication between iframe and parent uses postMessage
- No sensitive data from your site is accessible to the widget

---

## Browser Support

- Chrome 60+
- Firefox 55+
- Safari 11+
- Edge 79+
- Mobile browsers (iOS Safari, Chrome for Android)

---

## Complete Example

Full integration with all options:

```html
<!DOCTYPE html>
<html>
<head>
  <title>My Website</title>
</head>
<body>
  <!-- Your page content -->
  <h1>Welcome to My Site</h1>

  <!-- Custom button to open chat -->
  <button onclick="ChatWidget.open()">Need Help?</button>

  <!-- Chat Widget (place before </body>) -->
  <script
    src="https://your-server.com/static/chat-widget.js"
    data-position="bottom-right"
    data-color="#2563eb"
    data-size="normal">
  </script>

  <!-- Optional: Auto-open after delay -->
  <script>
    setTimeout(function() {
      if (typeof ChatWidget !== 'undefined' && !sessionStorage.getItem('chatOpened')) {
        ChatWidget.open();
        sessionStorage.setItem('chatOpened', 'true');
      }
    }, 60000); // 1 minute
  </script>
</body>
</html>
```

---

## WordPress Complete Example

Using the WPCode plugin with all features:

```html
<!-- Publisher Chat Widget -->
<script
  src="https://your-server.com/static/chat-widget.js"
  data-position="bottom-right"
  data-color="#0073aa"
  data-size="normal">
</script>

<style>
/* Optional: Hide on admin pages */
body.wp-admin #chat-widget-button,
body.wp-admin #chat-widget-modal {
  display: none !important;
}
</style>
```
