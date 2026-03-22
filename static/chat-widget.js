/**
 * Publisher Chat Widget
 *
 * Embed with: <script src="https://yoursite.com/static/chat-widget.js"></script>
 *
 * Configuration via data attributes:
 *   data-position: "bottom-right" (default) | "bottom-left"
 *   data-color: Button/header color (default: "#1a1a2e")
 *   data-size: "normal" (default) | "large"
 */
(function() {
    'use strict';

    // Get config from script tag
    const script = document.currentScript;
    const baseUrl = script.src.substring(0, script.src.lastIndexOf('/static/'));

    const config = {
        position: script.dataset.position || 'bottom-right',
        color: script.dataset.color || '#1a1a2e',
        size: script.dataset.size || 'normal',
        chatUrl: baseUrl + '/chat'
    };

    // Size presets
    const sizes = {
        normal: { width: '380px', height: '520px' },
        large: { width: '450px', height: '600px' }
    };
    const size = sizes[config.size] || sizes.normal;

    // Position styles
    const positions = {
        'bottom-right': { bottom: '20px', right: '20px' },
        'bottom-left': { bottom: '20px', left: '20px' }
    };
    const pos = positions[config.position] || positions['bottom-right'];

    // Inject CSS
    const css = `
        #chat-widget-button {
            position: fixed;
            ${Object.entries(pos).map(([k, v]) => `${k}: ${v}`).join('; ')};
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: ${config.color};
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 28px;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
            z-index: 999998;
            transition: transform 0.2s, box-shadow 0.2s;
            user-select: none;
        }
        #chat-widget-button:hover {
            transform: scale(1.1);
            box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
        }
        #chat-widget-button.open {
            transform: rotate(90deg);
        }
        #chat-widget-modal {
            position: fixed;
            ${Object.entries(pos).map(([k, v]) => `${k}: calc(${v} + 70px)`).join('; ')};
            width: ${size.width};
            height: ${size.height};
            max-width: calc(100vw - 40px);
            max-height: calc(100vh - 100px);
            background: white;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
            z-index: 999999;
            overflow: hidden;
            display: none;
            flex-direction: column;
            transition: width 0.2s, height 0.2s, bottom 0.2s, right 0.2s, left 0.2s;
        }
        #chat-widget-modal.open {
            display: flex;
        }
        #chat-widget-modal.enlarged {
            width: calc(100vw - 40px) !important;
            height: calc(100vh - 40px) !important;
            bottom: 20px !important;
            ${config.position.includes('right') ? 'right: 20px !important' : 'left: 20px !important'};
            max-width: none;
            max-height: none;
        }
        #chat-widget-modal iframe {
            width: 100%;
            height: 100%;
            border: none;
        }
        @media (max-width: 480px) {
            #chat-widget-modal {
                width: calc(100vw - 20px);
                height: calc(100vh - 100px);
                ${config.position.includes('right') ? 'right: 10px' : 'left: 10px'};
                bottom: 80px;
                border-radius: 8px;
            }
            #chat-widget-modal.enlarged {
                width: calc(100vw - 20px) !important;
                height: calc(100vh - 30px) !important;
                bottom: 15px !important;
            }
            #chat-widget-button {
                ${config.position.includes('right') ? 'right: 10px' : 'left: 10px'};
                bottom: 10px;
            }
        }
    `;

    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);

    // Create button
    const button = document.createElement('div');
    button.id = 'chat-widget-button';
    button.innerHTML = '💬';
    button.setAttribute('role', 'button');
    button.setAttribute('aria-label', 'Open chat');
    document.body.appendChild(button);

    // Create modal
    const modal = document.createElement('div');
    modal.id = 'chat-widget-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-label', 'Chat window');
    document.body.appendChild(modal);

    // State
    let isOpen = false;
    let iframeLoaded = false;
    let isEnlarged = false;

    // Toggle chat
    function toggleChat() {
        isOpen = !isOpen;

        if (isOpen) {
            // Lazy load iframe on first open
            if (!iframeLoaded) {
                const iframe = document.createElement('iframe');
                iframe.src = config.chatUrl;
                iframe.title = 'Chat';
                modal.appendChild(iframe);
                iframeLoaded = true;
            }
            modal.classList.add('open');
            button.classList.add('open');
            button.innerHTML = '✕';
            button.setAttribute('aria-label', 'Close chat');
        } else {
            modal.classList.remove('open');
            button.classList.remove('open');
            button.innerHTML = '💬';
            button.setAttribute('aria-label', 'Open chat');
            // Reset to normal size when closing
            if (isEnlarged) {
                isEnlarged = false;
                modal.classList.remove('enlarged');
            }
        }
    }

    button.addEventListener('click', toggleChat);

    // Toggle enlarge
    function toggleEnlarge() {
        isEnlarged = !isEnlarged;
        modal.classList.toggle('enlarged', isEnlarged);
    }

    // Listen for messages from iframe
    window.addEventListener('message', function(event) {
        // Verify origin if needed
        // if (event.origin !== baseUrl) return;

        const data = event.data;
        if (!data || typeof data !== 'object') return;

        switch (data.type) {
            case 'chat-close':
            case 'chat-minimize':
                if (isOpen) toggleChat();
                break;
            case 'chat-open':
                if (!isOpen) toggleChat();
                break;
            case 'chat-toggle-size':
                toggleEnlarge();
                break;
        }
    });

    // Close on escape key
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && isOpen) {
            toggleChat();
        }
    });

    // Expose API for programmatic control
    window.ChatWidget = {
        open: function() { if (!isOpen) toggleChat(); },
        close: function() { if (isOpen) toggleChat(); },
        toggle: toggleChat,
        enlarge: function() { if (!isEnlarged) toggleEnlarge(); },
        restore: function() { if (isEnlarged) toggleEnlarge(); },
        toggleSize: toggleEnlarge
    };

})();
