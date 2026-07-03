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
const newChatBtn = document.getElementById('new-chat-btn') as HTMLButtonElement;

// Status Bar Elements
const cacheIndicator = document.getElementById('cache-indicator') as HTMLSpanElement;
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
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + 'px';
  charCount.textContent = `${messageInput.value.length}`;
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

messagesContainer.addEventListener('scroll', () => {
  const isAtBottom = messagesContainer.scrollHeight - messagesContainer.scrollTop <= messagesContainer.clientHeight + 10;
  userScrolledUp = !isAtBottom;
});

function sendMessage() {
  const text = messageInput.value.trim();
  if (!text) return;

  const modelAlias = modelSelector.value;
  vscode.postMessage({ type: 'sendMessage', text, modelAlias, stream: true });

  appendMessage('user', text);
  messageInput.value = '';
  messageInput.style.height = 'auto';
  charCount.textContent = '0';
  userScrolledUp = false;

  const id = 'msg-' + Date.now();
  currentStreamingMessageId = id;
  streamingBuffer = '';
  
  const html = `
    <div id="${id}" class="message assistant">
      <div class="message-role">🤖 Nexus-Code <span class="status-badge pulsing-dot" style="font-weight:normal;opacity:0.7;margin-left:8px;">thinking...</span></div>
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
    <div id="${id}" class="message ${role}">
      <div class="message-role">${roleName}</div>
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
    const div = document.createElement('div');
    div.innerHTML = html;
    const pres = div.querySelectorAll('pre');
    pres.forEach(pre => {
        const codeElement = pre.querySelector('code');
        const codeText = codeElement?.innerText || '';
        const lang = codeElement?.className.replace('hljs language-', '') || 'text';
        
        const wrapper = document.createElement('div');
        wrapper.className = 'code-block-wrapper';
        
        const safeCodeText = encodeURIComponent(codeText);
        
        wrapper.innerHTML = `
            <div class="code-header">
                <span>${lang}</span>
                <div class="code-actions">
                    <button class="code-action-btn" onclick="navigator.clipboard.writeText(decodeURIComponent('${safeCodeText}')).then(() => { this.innerHTML = '✓ Copied'; setTimeout(() => { this.innerHTML = '📋 Copy'; }, 2000); })">📋 Copy</button>
                    <button class="code-action-btn apply-btn" onclick="vscode.postMessage({ type: 'sendMessage', text: 'Apply this code: \\n\`\`\`\\n' + decodeURIComponent('${safeCodeText}') + '\\n\`\`\`', modelAlias: document.getElementById('model-selector').value, stream: true })">▶ Apply to Editor</button>
                </div>
            </div>
        `;
        
        pre.parentNode?.insertBefore(wrapper, pre);
        wrapper.appendChild(pre);
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
  costIndicator.textContent = '';
}

// Expose vscode to global scope for inline onclicks
(window as any).vscode = vscode;

// Handle incoming messages
window.addEventListener('message', (event) => {
  const message = event.data;

  switch (message.type) {
    case 'initialize':
      modelSelector.innerHTML = message.models.map((m: string) => `<option value="${m}">${m}</option>`).join('');
      if (message.selectedModel) {
        modelSelector.value = message.selectedModel;
      }
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
        if (badgeEl) badgeEl.remove();
      } else if (message.response.response_text) {
        appendMessage('assistant', message.response.response_text);
      }
      
      currentStreamingMessageId = null;
      streamingBuffer = '';

      const r = message.response;
      if (r) {
        if (r.cache_hit) {
          cacheIndicator.textContent = `⚡ Cache Hit (${r.cache_tier})`;
        } else {
          cacheIndicator.textContent = '';
        }
        costIndicator.textContent = `$${(r.cost_estimate_usd || 0).toFixed(4)}`;
      }
      break;

    case 'toolExecution':
      let toolContainerId = `tools-${currentStreamingMessageId}`;
      let containerEl = document.getElementById(toolContainerId);
      
      if (!currentStreamingMessageId) {
         currentStreamingMessageId = 'msg-' + Date.now();
         const html = `
          <div id="${currentStreamingMessageId}" class="message assistant">
            <div class="message-role">🤖 Nexus-Code <span class="status-badge">executing tools...</span></div>
            <div class="message-content" id="${currentStreamingMessageId}-content"></div>
          </div>
        `;
        messagesContainer.insertAdjacentHTML('beforeend', html);
        toolContainerId = `tools-${currentStreamingMessageId}`;
      }

      if (!containerEl) {
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

// Signal ready
vscode.postMessage({ type: 'webviewReady' });
