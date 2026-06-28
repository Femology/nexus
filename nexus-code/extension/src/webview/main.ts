import { marked } from 'marked';
import hljs from 'highlight.js';

// Get VS Code API
// @ts-ignore
const vscode = acquireVsCodeApi();

// DOM Elements
const messagesContainer = document.getElementById('messages-container') as HTMLDivElement;
const messageInput = document.getElementById('message-input') as HTMLTextAreaElement;
const sendBtn = document.getElementById('send-btn') as HTMLButtonElement;
const charCount = document.getElementById('char-count') as HTMLSpanElement;
const modelSelector = document.getElementById('model-selector') as HTMLSelectElement;

// Settings Panel Elements
const settingsPanel = document.getElementById('settings-panel') as HTMLDivElement;
const toggleSettingsBtn = document.getElementById('toggle-settings-btn') as HTMLButtonElement;
const closeSettingsBtn = document.getElementById('close-settings-btn') as HTMLButtonElement;
const apiKeyList = document.getElementById('api-key-list') as HTMLDivElement;
const newKeyAlias = document.getElementById('new-key-alias') as HTMLInputElement;
const newKeyValue = document.getElementById('new-key-value') as HTMLInputElement;
const newKeyProvider = document.getElementById('new-key-provider') as HTMLSelectElement;
const saveKeyBtn = document.getElementById('save-key-btn') as HTMLButtonElement;
const settingStreaming = document.getElementById('setting-streaming') as HTMLInputElement;
const settingTerminal = document.getElementById('setting-terminal') as HTMLInputElement;
const settingTabs = document.getElementById('setting-tabs') as HTMLInputElement;
const saveSettingsBtn = document.getElementById('save-settings-btn') as HTMLButtonElement;
const newChatBtn = document.getElementById('new-chat-btn') as HTMLButtonElement;

// Status Bar Elements
const cacheIndicator = document.getElementById('cache-indicator') as HTMLSpanElement;
const savingsIndicator = document.getElementById('savings-indicator') as HTMLSpanElement;
const costIndicator = document.getElementById('cost-indicator') as HTMLSpanElement;

// State
let streamingBuffer = '';
let currentStreamingMessageId: string | null = null;
let lastRenderTime = 0;
const RENDER_INTERVAL_MS = 50;
let userScrolledUp = false;

// Setup Marked to use highlight.js
marked.setOptions({
  highlight: function (code, lang) {
    const language = hljs.getLanguage(lang) ? lang : 'plaintext';
    return hljs.highlight(code, { language }).value;
  },
  langPrefix: 'hljs language-',
});

// Event Listeners
toggleSettingsBtn.addEventListener('click', () => {
  settingsPanel.classList.toggle('open');
});

closeSettingsBtn.addEventListener('click', () => {
  settingsPanel.classList.remove('open');
});

messageInput.addEventListener('input', () => {
  // Auto-resize textarea
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + 'px';
  charCount.textContent = `${messageInput.value.length} chars`;
});

messageInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

sendBtn.addEventListener('click', sendMessage);

newChatBtn.addEventListener('click', () => {
  vscode.postMessage({ type: 'newChat' });
  messagesContainer.innerHTML = '';
  settingsPanel.classList.remove('open');
  clearStatus();
});

saveKeyBtn.addEventListener('click', () => {
  const alias = newKeyAlias.value.trim();
  const key = newKeyValue.value.trim();
  const provider = newKeyProvider.value;
  if (alias && key) {
    vscode.postMessage({ type: 'saveApiKey', alias, key, provider });
    newKeyAlias.value = '';
    newKeyValue.value = '';
  }
});

saveSettingsBtn.addEventListener('click', () => {
  vscode.postMessage({ type: 'updateSetting', key: 'nexuscode.streamingEnabled', value: settingStreaming.checked });
  vscode.postMessage({ type: 'updateSetting', key: 'nexuscode.terminalContextEnabled', value: settingTerminal.checked });
  vscode.postMessage({ type: 'updateSetting', key: 'nexuscode.maxOpenTabsContext', value: parseInt(settingTabs.value, 10) });
});

messagesContainer.addEventListener('scroll', () => {
  const isAtBottom = messagesContainer.scrollHeight - messagesContainer.scrollTop <= messagesContainer.clientHeight + 10;
  userScrolledUp = !isAtBottom;
});

function sendMessage() {
  const text = messageInput.value.trim();
  if (!text) return;
  if (sendBtn.disabled) return;

  const stream = settingStreaming.checked;
  const modelAlias = modelSelector.value;

  vscode.postMessage({ type: 'sendMessage', text, modelAlias, stream });

  appendMessage('user', text);
  messageInput.value = '';
  messageInput.style.height = 'auto';
  charCount.textContent = '0 chars';
  sendBtn.disabled = true;
  userScrolledUp = false;

  // Add a placeholder for assistant response
  const id = 'msg-' + Date.now();
  currentStreamingMessageId = id;
  streamingBuffer = '';
  
  const html = `
    <div id="${id}" class="message">
      <div class="message-header">
        <div class="message-role">🤖 Nexus-Code</div>
        <div class="status-badge pulsing-dot">thinking...</div>
      </div>
      <div class="message-content markdown-body" id="${id}-content"></div>
    </div>
  `;
  messagesContainer.insertAdjacentHTML('beforeend', html);
  scrollToBottom();
}

function appendMessage(role: 'user' | 'assistant', text: string) {
  const id = 'msg-' + Date.now();
  const roleName = role === 'user' ? '👤 You' : '🤖 Nexus-Code';
  
  const html = `
    <div id="${id}" class="message">
      <div class="message-header">
        <div class="message-role">${roleName}</div>
      </div>
      <div class="message-content markdown-body">
        ${processMarkdown(marked.parse(text) as string)}
      </div>
    </div>
  `;
  messagesContainer.insertAdjacentHTML('beforeend', html);
  scrollToBottom();
}

function scrollToBottom() {
  if (!userScrolledUp) {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }
}

function processMarkdown(html: string): string {
    // Inject copy buttons
    const div = document.createElement('div');
    div.innerHTML = html;
    const pres = div.querySelectorAll('pre');
    pres.forEach(pre => {
        const btn = document.createElement('button');
        btn.className = 'copy-btn';
        btn.textContent = 'Copy';
        btn.onclick = () => {
            const code = pre.querySelector('code')?.innerText || '';
            navigator.clipboard.writeText(code);
            btn.textContent = 'Copied!';
            setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
        };
        // Can't attach event listener easily via string, so we'll do it post-render instead
        // Actually, inline onclick is easiest for webviews without a framework
        const codeText = encodeURIComponent(pre.querySelector('code')?.innerText || '');
        const btnHtml = `<button class="copy-btn" onclick="navigator.clipboard.writeText(decodeURIComponent('${codeText}')).then(() => { this.textContent = 'Copied!'; setTimeout(() => { this.textContent = 'Copy'; }, 2000); })">Copy</button>`;
        pre.insertAdjacentHTML('afterbegin', btnHtml);
    });
    return div.innerHTML;
}

// Global shortcut
window.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'l') {
        e.preventDefault();
        vscode.postMessage({ type: 'newChat' });
        messagesContainer.innerHTML = '';
        clearStatus();
    }
});

function clearStatus() {
  cacheIndicator.textContent = '';
  savingsIndicator.textContent = '';
  costIndicator.textContent = '';
}

// Handle incoming messages
window.addEventListener('message', (event) => {
  const message = event.data;

  switch (message.type) {
    case 'initialize':
      // Populate models
      modelSelector.innerHTML = message.models.map((m: string) => `<option value="${m}">${m}</option>`).join('');
      if (message.selectedModel) {
        modelSelector.value = message.selectedModel;
      }
      
      // Populate settings
      settingStreaming.checked = message.settings.streamingEnabled;
      settingTerminal.checked = message.settings.terminalContextEnabled;
      settingTabs.value = message.settings.maxOpenTabsContext.toString();

      // Populate keys
      apiKeyList.innerHTML = message.keyAliases.map((a: string) => `
        <div class="api-key-item">
          <span>${a}</span>
          <button class="btn btn-secondary" onclick="deleteKey('${a}')">Delete</button>
        </div>
      `).join('');
      break;

    case 'streamDelta':
      streamingBuffer += message.delta;
      const now = Date.now();
      if (now - lastRenderTime > RENDER_INTERVAL_MS) {
        if (currentStreamingMessageId) {
          const contentEl = document.getElementById(`${currentStreamingMessageId}-content`);
          if (contentEl) {
            contentEl.innerHTML = processMarkdown(marked.parse(streamingBuffer) as string);
            scrollToBottom();
          }
          const badgeEl = document.querySelector(`#${currentStreamingMessageId} .status-badge`);
          if (badgeEl) {
            badgeEl.textContent = 'streaming...';
          }
        }
        lastRenderTime = now;
      }
      break;

    case 'responseComplete':
      if (currentStreamingMessageId) {
        const contentEl = document.getElementById(`${currentStreamingMessageId}-content`);
        if (contentEl) {
          contentEl.innerHTML = processMarkdown(marked.parse(message.response.response_text || streamingBuffer) as string);
          scrollToBottom();
        }
        const badgeEl = document.querySelector(`#${currentStreamingMessageId} .status-badge`);
        if (badgeEl) {
          badgeEl.className = 'status-badge';
          badgeEl.textContent = new Date().toLocaleTimeString();
        }
      } else if (message.response.response_text) {
        // Fallback if not streaming
        appendMessage('assistant', message.response.response_text);
      }
      
      currentStreamingMessageId = null;
      streamingBuffer = '';
      sendBtn.disabled = false;

      // Update status bar
      const r = message.response;
      if (r) {
        if (r.cache_hit) {
          cacheIndicator.textContent = `⚡ Cache Hit (${r.cache_tier})`;
        } else {
          cacheIndicator.textContent = '';
        }
        savingsIndicator.textContent = `Saved ${r.pre_compression_tokens - r.post_compression_tokens} tokens (${(r.compression_ratio * 100).toFixed(1)}%)`;
        costIndicator.textContent = `$${(r.cost_estimate_usd || 0).toFixed(4)}`;
      }
      break;

    case 'toolExecution':
      let toolContainerId = `tools-${currentStreamingMessageId}`;
      let containerEl = document.getElementById(toolContainerId);
      
      // If no message id, just append a new assistant message box for tools
      if (!currentStreamingMessageId) {
         currentStreamingMessageId = 'msg-' + Date.now();
         const html = `
          <div id="${currentStreamingMessageId}" class="message">
            <div class="message-header">
              <div class="message-role">🤖 Nexus-Code</div>
              <div class="status-badge">executing tools...</div>
            </div>
            <div class="message-content" id="${currentStreamingMessageId}-content"></div>
          </div>
        `;
        messagesContainer.insertAdjacentHTML('beforeend', html);
        toolContainerId = `tools-${currentStreamingMessageId}`;
      }

      if (!containerEl) {
         // Create the tools container
         const parent = document.getElementById(`${currentStreamingMessageId}-content`);
         if (parent) {
            parent.insertAdjacentHTML('beforeend', `<div id="${toolContainerId}" class="tools-container" style="margin-top: 8px; border: 1px solid var(--vscode-widget-border); border-radius: 4px; padding: 8px;"></div>`);
            containerEl = document.getElementById(toolContainerId);
         }
      }

      if (containerEl) {
         const toolsHTML = message.tools.map((t: any) => {
            let icon = '🔄';
            if (t.status === 'completed') icon = '✅';
            if (t.status === 'error') icon = '❌';
            return `<div style="display: flex; align-items: center; gap: 8px; font-size: 0.9em; margin-bottom: 4px;">
               <span>${icon}</span>
               <code>${t.name}</code>
               <span style="color: var(--vscode-descriptionForeground)">${t.status}</span>
            </div>`;
         }).join('');
         
         containerEl.innerHTML = `<details open><summary style="cursor:pointer; font-weight:bold; margin-bottom:8px;">Tools Execution</summary>${toolsHTML}</details>`;
         scrollToBottom();
      }
      break;

    case 'error':
      if (currentStreamingMessageId) {
        const badgeEl = document.querySelector(`#${currentStreamingMessageId} .status-badge`);
        if (badgeEl) {
          badgeEl.className = 'status-badge error';
          badgeEl.textContent = 'error';
        }
        const contentEl = document.getElementById(`${currentStreamingMessageId}-content`);
        if (contentEl) {
          contentEl.innerHTML += `<p style="color: var(--vscode-errorForeground)">${message.message}</p>`;
        }
      } else {
        appendMessage('assistant', `**Error:** ${message.message}`);
      }
      sendBtn.disabled = false;
      currentStreamingMessageId = null;
      streamingBuffer = '';
      break;
      
    case 'statusUpdate':
       if (currentStreamingMessageId) {
          const badgeEl = document.querySelector(`#${currentStreamingMessageId} .status-badge`);
          if (badgeEl) {
             badgeEl.textContent = message.status;
          }
       }
       break;
  }
});

// Expose deleteKey to global scope for inline onclick handler
(window as any).deleteKey = function(alias: string) {
  vscode.postMessage({ type: 'deleteApiKey', alias });
};

// Signal ready
vscode.postMessage({ type: 'webviewReady' });
