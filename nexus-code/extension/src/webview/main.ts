import { marked } from 'marked';

// @ts-ignore
const vscode = acquireVsCodeApi();

// Expose globally for inline onclick handlers
(window as any).vscode = vscode;
(window as any).openLink = (url: string) => vscode.postMessage({ type: 'openLink', url });
(window as any).saveKey = saveKey;
(window as any).deleteKey = deleteKey;
(window as any).updateSetting = updateSetting;

// ─── Model Catalogue ────────────────────────────────────────────────────────
const PROVIDER_MODELS: Record<string, { label: string; models: { value: string; label: string }[] }> = {
  openai: {
    label: 'OpenAI',
    models: [
      { value: 'gpt-4o', label: 'GPT-4o' },
      { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
      { value: 'gpt-4.1', label: 'GPT-4.1' },
      { value: 'o3-mini', label: 'o3-mini' },
    ],
  },
  anthropic: {
    label: 'Anthropic',
    models: [
      { value: 'claude-sonnet-4-5', label: 'Claude Sonnet 4.5' },
      { value: 'claude-opus-4', label: 'Claude Opus 4' },
      { value: 'claude-haiku-3-5', label: 'Claude Haiku 3.5' },
    ],
  },
  google: {
    label: 'Google AI',
    models: [
      { value: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash' },
      { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
    ],
  },
  fireworks: {
    label: 'Fireworks AI 🔥',
    models: [
      { value: 'fireworks_ai/llama-4-maverick-instruct-basic', label: 'Llama 4 Maverick' },
      { value: 'fireworks_ai/llama-v3p1-405b-instruct', label: 'Llama 3.1 405B' },
      { value: 'fireworks_ai/mixtral-8x22b-instruct', label: 'Mixtral 8x22B' },
    ],
  },
  deepseek: {
    label: 'DeepSeek',
    models: [
      { value: 'deepseek-chat', label: 'DeepSeek Chat' },
      { value: 'deepseek-coder', label: 'DeepSeek Coder' },
    ],
  },
};

// ─── DOM References ──────────────────────────────────────────────────────────
const viewChat = document.getElementById('view-chat') as HTMLDivElement;
const viewSettings = document.getElementById('view-settings') as HTMLDivElement;
const messagesContainer = document.getElementById('messages-container') as HTMLDivElement;
const welcomeScreen = document.getElementById('welcome-screen') as HTMLDivElement;
const messageInput = document.getElementById('message-input') as HTMLTextAreaElement;
const sendBtn = document.getElementById('send-btn') as HTMLButtonElement;
const charCount = document.getElementById('char-count') as HTMLSpanElement;
const modelSelector = document.getElementById('model-selector') as HTMLSelectElement;
const cacheIndicator = document.getElementById('cache-indicator') as HTMLSpanElement;
const costIndicator = document.getElementById('cost-indicator') as HTMLSpanElement;

// ─── State ───────────────────────────────────────────────────────────────────
let configuredProviders: Set<string> = new Set();
let streamingBuffer = '';
let currentStreamingMessageId: string | null = null;
let lastRenderTime = 0;
const RENDER_INTERVAL_MS = 50;
let userScrolledUp = false;

// ─── Navigation ──────────────────────────────────────────────────────────────
document.getElementById('btn-settings')!.addEventListener('click', () => showView('settings'));
document.getElementById('btn-back')!.addEventListener('click', () => showView('chat'));
document.getElementById('welcome-go-settings')!.addEventListener('click', () => showView('settings'));
document.getElementById('btn-new-chat')!.addEventListener('click', () => {
  vscode.postMessage({ type: 'newChat' });
  messagesContainer.innerHTML = '';
  clearStatus();
  showView('chat');
});

function showView(view: 'chat' | 'settings') {
  viewChat.classList.toggle('active', view === 'chat');
  viewSettings.classList.toggle('active', view === 'settings');
}

// ─── Tabs ────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const target = (btn as HTMLElement).dataset.tab!;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(target)!.classList.add('active');
  });
});

// ─── Composer ────────────────────────────────────────────────────────────────
messageInput.addEventListener('input', () => {
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 180) + 'px';
  charCount.textContent = `${messageInput.value.length}`;
});

messageInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

sendBtn.addEventListener('click', sendMessage);

messagesContainer.addEventListener('scroll', () => {
  const isAtBottom = messagesContainer.scrollHeight - messagesContainer.scrollTop <= messagesContainer.clientHeight + 10;
  userScrolledUp = !isAtBottom;
});

window.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'l') {
    e.preventDefault();
    messagesContainer.innerHTML = '';
    clearStatus();
  }
});

// ─── Send Message ─────────────────────────────────────────────────────────────
function sendMessage() {
  const text = messageInput.value.trim();
  if (!text) return;
  if (configuredProviders.size === 0) {
    showView('settings');
    return;
  }

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

  messagesContainer.insertAdjacentHTML('beforeend', `
    <div id="${id}" class="message assistant">
      <div class="message-role">⚡ Nexus-Code <span class="status-badge pulsing" style="margin-left:6px;">thinking…</span></div>
      <div class="message-content markdown-body" id="${id}-content"></div>
    </div>
  `);
  scrollToBottom();
}

function appendMessage(role: 'user' | 'assistant', text: string) {
  const id = 'msg-' + Date.now();
  const roleName = role === 'user' ? '👤 You' : '⚡ Nexus-Code';
  messagesContainer.insertAdjacentHTML('beforeend', `
    <div id="${id}" class="message ${role}">
      <div class="message-role">${roleName}</div>
      <div class="message-content markdown-body">${processMarkdown(marked.parse(text) as string)}</div>
    </div>
  `);
  scrollToBottom();
}

function scrollToBottom() {
  if (!userScrolledUp) {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }
}

function clearStatus() {
  cacheIndicator.textContent = '';
  costIndicator.textContent = '';
}

// ─── Markdown ─────────────────────────────────────────────────────────────────
function processMarkdown(html: string): string {
  const div = document.createElement('div');
  div.innerHTML = html;
  div.querySelectorAll('pre').forEach(pre => {
    const codeEl = pre.querySelector('code');
    const codeText = codeEl?.innerText || '';
    const lang = (codeEl?.className || '').replace(/hljs\s+language-/, '').trim() || 'text';
    const safe = encodeURIComponent(codeText);

    const wrapper = document.createElement('div');
    wrapper.className = 'code-block-wrapper';
    wrapper.innerHTML = `
      <div class="code-header">
        <span>${lang}</span>
        <div class="code-actions">
          <button class="code-action-btn" onclick="navigator.clipboard.writeText(decodeURIComponent('${safe}')).then(()=>{this.textContent='✓ Copied';setTimeout(()=>{this.textContent='📋 Copy'},1800)})">📋 Copy</button>
          <button class="code-action-btn" onclick="(window as any).vscode.postMessage({type:'applyEdit',code:decodeURIComponent('${safe}')})">▶ Apply</button>
        </div>
      </div>
    `;
    pre.parentNode?.insertBefore(wrapper, pre);
    wrapper.appendChild(pre);
  });
  return div.innerHTML;
}

// ─── API Key Management ───────────────────────────────────────────────────────
function saveKey(provider: string) {
  const input = document.getElementById(`key-${provider}`) as HTMLInputElement;
  const key = input.value.trim();
  if (!key) { input.focus(); return; }
  vscode.postMessage({ type: 'saveApiKey', alias: provider, key, provider });
  input.value = '';
  input.placeholder = '●●●●●●●●●●●●●●●● (saved)';
}

function deleteKey(provider: string) {
  vscode.postMessage({ type: 'deleteApiKey', alias: provider });
}

function updateSetting(key: string, value: any) {
  vscode.postMessage({ type: 'updateSetting', key, value });
}

function updateProviderCard(provider: string, isConfigured: boolean) {
  const statusEl = document.getElementById(`status-${provider}`);
  const deleteBtn = document.getElementById(`delete-${provider}`) as HTMLButtonElement;
  const input = document.getElementById(`key-${provider}`) as HTMLInputElement;

  if (!statusEl) return;

  if (isConfigured) {
    statusEl.textContent = 'Configured ✓';
    statusEl.className = 'provider-status configured';
    if (deleteBtn) deleteBtn.style.display = 'inline-block';
    input.placeholder = '●●●●●●●●●●●●●●●● (saved — paste new key to update)';
    configuredProviders.add(provider);
  } else {
    const placeholders: Record<string, string> = {
      openai: 'sk-proj-...',
      anthropic: 'sk-ant-api03-...',
      google: 'AIza...',
      fireworks: 'fw_...',
      deepseek: 'sk-...',
    };
    statusEl.textContent = 'Not configured';
    statusEl.className = 'provider-status not-configured';
    if (deleteBtn) deleteBtn.style.display = 'none';
    input.placeholder = placeholders[provider] || 'Paste API key...';
    configuredProviders.delete(provider);
  }
}

function rebuildModelSelector(providers: string[]) {
  modelSelector.innerHTML = '';

  if (providers.length === 0) {
    modelSelector.innerHTML = '<option value="">— Add a provider key first —</option>';
    return;
  }

  providers.forEach(p => {
    const catalogue = PROVIDER_MODELS[p];
    if (!catalogue) return;
    const group = document.createElement('optgroup');
    group.label = catalogue.label;
    catalogue.models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.value;
      opt.textContent = m.label;
      group.appendChild(opt);
    });
    modelSelector.appendChild(group);
  });
}

// ─── Message Handler ──────────────────────────────────────────────────────────
window.addEventListener('message', (event) => {
  const msg = event.data;

  switch (msg.type) {

    case 'initialize': {
      const aliases: { alias: string; provider: string }[] = msg.keyAliases || [];
      const configured = aliases.map(a => a.provider || a.alias);

      // Reset all cards
      Object.keys(PROVIDER_MODELS).forEach(p => updateProviderCard(p, false));

      // Mark configured ones
      configured.forEach(p => updateProviderCard(p, true));

      // Rebuild model selector
      rebuildModelSelector([...configuredProviders]);

      // Show/hide welcome screen
      const hasKeys = configuredProviders.size > 0;
      welcomeScreen.style.display = hasKeys ? 'none' : 'flex';
      messagesContainer.style.display = hasKeys ? 'flex' : 'none';

      // Set selected model
      if (msg.selectedModel && modelSelector.querySelector(`option[value="${msg.selectedModel}"]`)) {
        modelSelector.value = msg.selectedModel;
      }
      break;
    }

    case 'streamDelta': {
      streamingBuffer += msg.delta;
      const now = Date.now();
      if (now - lastRenderTime > RENDER_INTERVAL_MS && currentStreamingMessageId) {
        const el = document.getElementById(`${currentStreamingMessageId}-content`);
        if (el) {
          el.innerHTML = processMarkdown(marked.parse(streamingBuffer) as string);
          scrollToBottom();
        }
        const badge = document.querySelector(`#${currentStreamingMessageId} .status-badge`);
        if (badge) badge.textContent = 'streaming…';
        lastRenderTime = now;
      }
      break;
    }

    case 'responseComplete': {
      if (currentStreamingMessageId) {
        const el = document.getElementById(`${currentStreamingMessageId}-content`);
        if (el) {
          el.innerHTML = processMarkdown(marked.parse(msg.response?.response_text || streamingBuffer) as string);
          scrollToBottom();
        }
        const badge = document.querySelector(`#${currentStreamingMessageId} .status-badge`);
        if (badge) badge.remove();
      } else if (msg.response?.response_text) {
        appendMessage('assistant', msg.response.response_text);
      }

      currentStreamingMessageId = null;
      streamingBuffer = '';

      const r = msg.response;
      if (r) {
        cacheIndicator.textContent = r.cache_hit ? `⚡ Cache Hit (${r.cache_tier})` : '';
        costIndicator.textContent = `$${(r.cost_estimate_usd || 0).toFixed(4)}`;
      }
      break;
    }

    case 'error': {
      if (currentStreamingMessageId) {
        const badge = document.querySelector(`#${currentStreamingMessageId} .status-badge`);
        if (badge) { badge.className = 'status-badge'; badge.textContent = 'error'; }
        const el = document.getElementById(`${currentStreamingMessageId}-content`);
        if (el) el.insertAdjacentHTML('beforeend', `<p style="color:var(--error-fg);margin-top:6px;">${msg.message}</p>`);
      } else {
        appendMessage('assistant', `**Error:** ${msg.message}`);
      }
      currentStreamingMessageId = null;
      streamingBuffer = '';
      break;
    }

    case 'statusUpdate': {
      if (currentStreamingMessageId) {
        const badge = document.querySelector(`#${currentStreamingMessageId} .status-badge`);
        if (badge) badge.textContent = msg.status;
      }
      break;
    }

    case 'toolExecution': {
      let containerId = `tools-${currentStreamingMessageId}`;
      let containerEl = document.getElementById(containerId);
      if (!currentStreamingMessageId) {
        currentStreamingMessageId = 'msg-' + Date.now();
        messagesContainer.insertAdjacentHTML('beforeend', `
          <div id="${currentStreamingMessageId}" class="message assistant">
            <div class="message-role">⚡ Nexus-Code <span class="status-badge">executing tools…</span></div>
            <div class="message-content" id="${currentStreamingMessageId}-content"></div>
          </div>
        `);
        containerId = `tools-${currentStreamingMessageId}`;
      }
      if (!containerEl) {
        const parent = document.getElementById(`${currentStreamingMessageId}-content`);
        if (parent) {
          parent.insertAdjacentHTML('beforeend', `<div id="${containerId}" style="margin-top:8px;border:1px solid var(--border-color);border-radius:4px;padding:8px;"></div>`);
          containerEl = document.getElementById(containerId);
        }
      }
      if (containerEl) {
        const rows = msg.tools.map((t: any) => {
          const icon = t.status === 'completed' ? '✅' : t.status === 'error' ? '❌' : '🔄';
          return `<div style="display:flex;gap:8px;align-items:center;font-size:12px;margin-bottom:4px;">${icon} <code>${t.name}</code> <span style="color:var(--description)">${t.status}</span></div>`;
        }).join('');
        containerEl.innerHTML = `<details open><summary style="cursor:pointer;font-weight:600;margin-bottom:6px;font-size:12px;">Tools</summary>${rows}</details>`;
        scrollToBottom();
      }
      break;
    }
  }
});

// ─── Signal Ready ─────────────────────────────────────────────────────────────
vscode.postMessage({ type: 'webviewReady' });
