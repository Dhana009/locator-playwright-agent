/**
 * Hermes Playwright Co-pilot — Overlay UI
 * Injected into Playwright browser via CDP.
 * Vanilla JS only. No frameworks. No imports.
 */
(function () {
  'use strict';

  if (window.__hermesOverlayLoaded) return;
  window.__hermesOverlayLoaded = true;

  /* ── Constants ─────────────────────────── */
  const WS_URL = 'ws://localhost:7777';
  const RECONNECT_DELAY = 2000;
  const MAX_RECONNECT = 10;

  /* ── State ──────────────────────────────── */
  const S = {
    tab: 'steps',
    dock: 'right',
    connected: false,
    pickMode: false,
    paused: false,
    ws: null,
    reconnectCount: 0,
    reconnectTimer: null,
    steps: [],
    code: '',
    pendingConfirm: null,
    selectedElement: null,
    statusMsg: null,
    errorMsg: null,
    hoveredTarget: null,
    highlightEl: null,
  };

  /* ── Restore state from sessionStorage ──── */
  function restoreState() {
    try {
      const raw = sessionStorage.getItem('__hermes');
      if (!raw) return;
      const saved = JSON.parse(raw);
      S.steps = saved.steps || [];
      S.code  = saved.code  || '';
      S.dock  = saved.dock  || 'right';
    } catch (_) {}
  }

  function saveState() {
    try {
      sessionStorage.setItem('__hermes', JSON.stringify({
        steps: S.steps,
        code:  S.code,
        dock:  S.dock,
      }));
    } catch (_) {}
  }

  /* ── DOM helpers ────────────────────────── */
  function el(id) { return document.getElementById(id); }

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      Object.entries(attrs).forEach(([k, v]) => {
        if (k === 'cls') node.className = v;
        else if (k === 'html') node.innerHTML = v;
        else if (k === 'txt') node.textContent = v;
        else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v);
      });
    }
    children.forEach(c => {
      if (c == null) return;
      if (typeof c === 'string') node.appendChild(document.createTextNode(c));
      else node.appendChild(c);
    });
    return node;
  }

  /* ── Build Panel HTML ───────────────────── */
  function buildPanel() {
    const root = h('div', { id: 'hermes-root' });

    const panel = h('div', { id: 'hermes-panel' });

    /* Header */
    const header = h('div', { id: 'hermes-header' },
      h('span', { id: 'hermes-logo', txt: 'HERMES' }),
      h('span', { id: 'hermes-mode-badge', txt: 'Interactive' }),
      h('div', { id: 'hermes-header-actions' },
        mkIconBtn('←', 'Dock left',   () => setDock('left')),
        mkIconBtn('↓', 'Dock bottom', () => setDock('bottom')),
        mkIconBtn('⧉', 'Float',       () => setDock('float')),
        mkIconBtn('→', 'Dock right',  () => setDock('right')),
      )
    );

    /* Tabs */
    const tabs = h('div', { id: 'hermes-tabs' },
      mkTab('steps', 'Steps'),
      mkTab('code',  'Code'),
    );

    /* Content */
    const content = h('div', { id: 'hermes-content' });

    /* Controls row */
    const controls = h('div', { id: 'hermes-controls' },
      mkToolBtn('pick-btn', '◎ Pick', togglePickMode),
      mkCtrlBtn('pause-btn', '⏸ Pause', false, togglePause),
      mkCtrlBtn('stop-btn',  '■ Stop',  true,  doStop),
      h('div', { id: 'hermes-conn-status' },
        h('div', { id: 'hermes-conn-dot', cls: 'off' }),
        h('span', { id: 'hermes-conn-text', txt: 'Connecting...' })
      )
    );

    /* Chat input */
    const chatArea = h('div', { id: 'hermes-chat-area' },
      h('div', { id: 'hermes-chat-row' },
        h('input', {
          id: 'hermes-chat-input',
          type: 'text',
          placeholder: 'Click Pick, select element, then type intent...',
          onkeydown: (e) => { if (e.key === 'Enter') sendIntent(); }
        }),
        h('button', {
          id: 'hermes-send-btn',
          txt: 'Send',
          onclick: sendIntent
        })
      )
    );

    panel.append(header, tabs, content, controls, chatArea);
    root.appendChild(panel);
    document.body.appendChild(root);

    /* Highlight layer (behind panel) */
    const hlLayer = h('div', { id: 'hermes-highlight-layer' });
    document.body.appendChild(hlLayer);

    applyDock(S.dock);
    render();
  }

  function mkIconBtn(icon, title, onclick) {
    return h('button', { cls: 'hermes-icon-btn', title, html: icon, onclick });
  }

  function mkTab(id, label) {
    return h('div', {
      id: 'hermes-tab-' + id,
      cls: 'hermes-tab' + (S.tab === id ? ' active' : ''),
      txt: label,
      onclick: () => switchTab(id)
    });
  }

  function mkToolBtn(id, html, onclick) {
    return h('button', {
      id: 'hermes-' + id,
      cls: 'hermes-tool-btn',
      html,
      onclick
    });
  }

  function mkCtrlBtn(id, label, isStop, onclick) {
    return h('button', {
      id: 'hermes-' + id,
      cls: 'hermes-ctrl-btn' + (isStop ? ' stop' : ''),
      html: label,
      onclick
    });
  }

  /* ── Render ─────────────────────────────── */
  function render() {
    const content = el('hermes-content');
    if (!content) return;

    if (S.tab === 'steps') renderSteps(content);
    else if (S.tab === 'code') renderCode(content);

    updateTabBadges();
  }

  function renderSteps(container) {
    container.innerHTML = '';

    /* Pick mode banner */
    if (S.pickMode) {
      container.appendChild(
        h('div', { cls: 'hermes-pick-banner',
          txt: 'Pick mode on — hover and click any element on page' })
      );
    }

    /* Status message */
    if (S.statusMsg) {
      container.appendChild(
        h('div', { cls: 'hermes-status-msg' },
          h('div', { cls: 'hermes-status-dot' }),
          h('span', { txt: S.statusMsg })
        )
      );
    }

    /* Error message */
    if (S.errorMsg) {
      const errDiv = h('div', { cls: 'hermes-error-msg' });
      errDiv.appendChild(h('div', { txt: S.errorMsg.message }));
      if (S.errorMsg.suggestion) {
        errDiv.appendChild(
          h('div', { cls: 'hermes-error-suggestion',
            txt: S.errorMsg.suggestion })
        );
      }
      container.appendChild(errDiv);
    }

    /* Pending confirmation */
    if (S.pendingConfirm) {
      const pc = S.pendingConfirm;
      const card = h('div', { cls: 'hermes-confirm-card' },
        h('div', { cls: 'hermes-confirm-label', txt: 'Confirm action' }),
        h('div', { cls: 'hermes-confirm-understood', txt: pc.understood }),
        h('div', { cls: 'hermes-confirm-locator', txt: pc.locator }),
        h('div', { cls: 'hermes-confirm-actions' },
          h('div', { cls: 'hermes-btn-yes', txt: 'Yes, proceed',
            onclick: () => sendConfirm(true) }),
          h('div', { cls: 'hermes-btn-correct', txt: 'Correct me',
            onclick: () => sendConfirm(false) })
        )
      );
      container.appendChild(card);
    }

    /* Steps list */
    if (S.steps.length === 0 && !S.pickMode &&
        !S.pendingConfirm && !S.statusMsg) {
      container.appendChild(
        h('div', { cls: 'hermes-empty' },
          h('div', { cls: 'hermes-empty-icon', txt: '○' }),
          h('div', { txt: 'Click Pick to select an element\nthen type what you want to do' })
        )
      );
      return;
    }

    const icons = {
      click: '▶', fill: '✎', assert: '✓',
      navigate: '→', select: '▾', upload: '↑',
      hover: '◎', scroll: '↕', wait: '◷'
    };

    S.steps.forEach((step, i) => {
      const icon = icons[step.action] || '▶';
      const statusCls = step.status === 'ok' ? 'ok' :
                        step.status === 'running' ? 'running' : 'fail';
      const statusIcon = step.status === 'ok' ? '✓' :
                         step.status === 'running' ? '…' : '⚠';

      container.appendChild(
        h('div', { cls: 'hermes-step' },
          h('span', { cls: 'hermes-step-num', txt: String(i + 1) }),
          h('span', { cls: 'hermes-step-icon', html: icon }),
          h('div', { cls: 'hermes-step-body' },
            h('div', { cls: 'hermes-step-label', txt: step.label }),
            h('div', { cls: 'hermes-step-loc', txt: step.locator })
          ),
          h('span', {
            cls: 'hermes-step-status ' + statusCls,
            html: statusIcon
          })
        )
      );
    });
  }

  function renderCode(container) {
    container.innerHTML = '';

    if (!S.code && S.steps.length === 0) {
      container.appendChild(
        h('div', { cls: 'hermes-empty' },
          h('div', { txt: 'No steps recorded yet.\nCode appears here as you record.' })
        )
      );
      return;
    }

    const code = S.code || buildCodeFromSteps();
    const block = h('pre', { cls: 'hermes-code-block', txt: code });
    const actions = h('div', { cls: 'hermes-code-actions' },
      h('button', {
        cls: 'hermes-copy-btn',
        txt: 'Copy all',
        onclick: () => {
          navigator.clipboard && navigator.clipboard.writeText(code);
          const btn = container.querySelector('.hermes-copy-btn');
          if (btn) { btn.textContent = 'Copied!';
            setTimeout(() => { btn.textContent = 'Copy all'; }, 1500); }
        }
      })
    );

    container.appendChild(block);
    container.appendChild(actions);
  }

  function buildCodeFromSteps() {
    if (S.steps.length === 0) return '';
    let lines = [
      "import { test, expect } from '@playwright/test'",
      '',
      '// === LOCATORS ===',
    ];
    S.steps.forEach(step => {
      if (step.varName && step.locatorExpr) {
        lines.push(`const ${step.varName} = ${step.locatorExpr}`);
      }
    });
    lines.push('', '// === TEST ===');
    lines.push("test('recorded flow', async ({ page }) => {");
    S.steps.forEach(step => {
      if (step.codeLine) lines.push('  ' + step.codeLine);
    });
    lines.push('})');
    return lines.join('\n');
  }

  function updateTabBadges() {
    const stepsTab = el('hermes-tab-steps');
    if (stepsTab && S.steps.length > 0) {
      stepsTab.textContent = '';
      stepsTab.appendChild(
        document.createTextNode('Steps ')
      );
      stepsTab.appendChild(
        h('span', { cls: 'hermes-tab-badge',
          txt: String(S.steps.length) })
      );
    }
  }

  /* ── Tab Switching ──────────────────────── */
  function switchTab(tab) {
    S.tab = tab;
    ['steps', 'code'].forEach(t => {
      const tabEl = el('hermes-tab-' + t);
      if (tabEl) tabEl.className = 'hermes-tab' + (t === tab ? ' active' : '');
    });
    render();
  }

  /* ── Docking ────────────────────────────── */
  function setDock(pos) {
    applyDock(pos);
    S.dock = pos;
    saveState();
  }

  function applyDock(pos) {
    ['right', 'left', 'bottom', 'float'].forEach(p => {
      document.body.classList.remove('hermes-dock-' + p);
    });
    document.body.classList.add('hermes-dock-' + pos);
  }

  /* ── Pick Mode ──────────────────────────── */
  function togglePickMode() {
    S.pickMode = !S.pickMode;
    const btn = el('hermes-pick-btn');
    if (btn) btn.className = 'hermes-tool-btn' + (S.pickMode ? ' active' : '');
    if (!S.pickMode) clearHighlight();
    updateChatPlaceholder();
    render();
  }

  function updateChatPlaceholder() {
    const input = el('hermes-chat-input');
    if (!input) return;
    if (S.pickMode) {
      input.placeholder = 'Hover and click element on page first...';
    } else if (S.selectedElement) {
      input.placeholder = 'Type intent: click, fill with..., assert visible...';
    } else {
      input.placeholder = 'Click Pick, select element, then type intent...';
    }
  }

  /* ── Element Picker Events ──────────────── */
  function onMouseOver(e) {
    if (!S.pickMode) return;
    const target = e.target;
    if (target.id && target.id.startsWith('hermes-')) return;
    if (el('hermes-root') && el('hermes-root').contains(target)) return;
    if (S.hoveredTarget === target) return;
    S.hoveredTarget = target;
    showHighlight(target, 'hover');
  }

  function onMouseOut(e) {
    if (!S.pickMode) return;
    if (S.hoveredTarget === e.target) {
      S.hoveredTarget = null;
    }
  }

  function onPageClick(e) {
    if (!S.pickMode) return;
    if (el('hermes-root') && el('hermes-root').contains(e.target)) return;
    e.preventDefault();
    e.stopPropagation();

    const target = e.target;
    const data = captureElementData(target);
    S.selectedElement = data;
    S.pickMode = false;

    const btn = el('hermes-pick-btn');
    if (btn) btn.className = 'hermes-tool-btn';

    showHighlight(target, 'selected');
    updateChatPlaceholder();

    const input = el('hermes-chat-input');
    if (input) input.focus();

    console.log('[HERMES] element_selected:',
      JSON.stringify(data));
    wsMsg({ type: 'element_selected', payload: data });

    S.statusMsg = null;
    render();
  }

  function captureElementData(target) {
    const rect = target.getBoundingClientRect();
    const computed = window.getComputedStyle(target);
    const parent = target.parentElement;

    return {
      tag: target.tagName.toLowerCase(),
      text: (target.innerText || target.textContent || '').trim().slice(0, 100),
      id: target.id || '',
      role: target.getAttribute('role') || '',
      aria_label: target.getAttribute('aria-label') || '',
      data_testid: target.getAttribute('data-testid') || '',
      data_cy: target.getAttribute('data-cy') || '',
      data_qa: target.getAttribute('data-qa') || '',
      placeholder: target.getAttribute('placeholder') || '',
      name: target.getAttribute('name') || '',
      type: target.getAttribute('type') || '',
      class: (target.getAttribute('class') || '').split(' ')[0] || '',
      href: target.getAttribute('href') || '',
      value: target.value || '',
      bounding_box: {
        x: Math.round(rect.left),
        y: Math.round(rect.top),
        w: Math.round(rect.width),
        h: Math.round(rect.height)
      },
      parent_tag: parent ? parent.tagName.toLowerCase() : '',
      parent_id: parent ? (parent.id || '') : '',
    };
  }

  /* ── Highlight Layer ────────────────────── */
  function showHighlight(target, state) {
    const layer = el('hermes-highlight-layer');
    if (!layer) return;

    if (S.highlightEl) {
      S.highlightEl.remove();
      S.highlightEl = null;
    }

    const rect = target.getBoundingClientRect();
    const layerRect = layer.getBoundingClientRect();

    const div = h('div', { cls: 'hermes-highlight ' + state });
    div.style.left   = (rect.left - layerRect.left - 2) + 'px';
    div.style.top    = (rect.top  - layerRect.top  - 2) + 'px';
    div.style.width  = (rect.width  + 4) + 'px';
    div.style.height = (rect.height + 4) + 'px';

    layer.appendChild(div);
    S.highlightEl = div;
  }

  function showHighlightByCoords(coords, state) {
    const layer = el('hermes-highlight-layer');
    if (!layer || !coords) return;

    if (S.highlightEl) {
      S.highlightEl.remove();
      S.highlightEl = null;
    }

    const div = h('div', { cls: 'hermes-highlight ' + state });
    div.style.left   = coords.x + 'px';
    div.style.top    = coords.y + 'px';
    div.style.width  = coords.w + 'px';
    div.style.height = coords.h + 'px';

    layer.appendChild(div);
    S.highlightEl = div;
  }

  function flashHighlight(coords) {
    showHighlightByCoords(coords, 'flash');
    setTimeout(() => clearHighlight(), 700);
  }

  function clearHighlight() {
    if (S.highlightEl) {
      S.highlightEl.remove();
      S.highlightEl = null;
    }
  }

  /* ── Controls ───────────────────────────── */
  function togglePause() {
    S.paused = !S.paused;
    const btn = el('hermes-pause-btn');
    if (btn) btn.innerHTML = S.paused ? '▶ Resume' : '⏸ Pause';
    wsMsg({ type: S.paused ? 'pause' : 'resume', payload: {} });
  }

  function doStop() {
    S.paused = false;
    S.pickMode = false;
    S.pendingConfirm = null;
    S.statusMsg = null;
    clearHighlight();
    const pauseBtn = el('hermes-pause-btn');
    if (pauseBtn) pauseBtn.innerHTML = '⏸ Pause';
    const pickBtn = el('hermes-pick-btn');
    if (pickBtn) pickBtn.className = 'hermes-tool-btn';
    wsMsg({ type: 'command', payload: { command: '/stop' } });
    render();
  }

  /* ── Chat / Intent ──────────────────────── */
  function sendIntent() {
    const input = el('hermes-chat-input');
    if (!input) return;
    const msg = input.value.trim();
    if (!msg) return;
    input.value = '';

    console.log('[HERMES] user_intent:', msg);
    wsMsg({
      type: 'user_intent',
      payload: {
        message: msg,
        element: S.selectedElement || {},
        mode: 'interactive'
      }
    });

    S.statusMsg = 'Understanding intent...';
    S.errorMsg = null;
    S.pendingConfirm = null;
    render();
  }

  function sendConfirm(confirmed) {
    wsMsg({ type: 'user_confirm', payload: { confirmed } });
    if (!confirmed) {
      S.pendingConfirm = null;
      S.statusMsg = null;
      clearHighlight();
      render();
    }
  }

  /* ── WebSocket ──────────────────────────── */
  function wsMsg(obj) {
    if (S.ws && S.ws.readyState === WebSocket.OPEN) {
      S.ws.send(JSON.stringify(obj));
    }
  }

  function connectWS() {
    if (S.reconnectTimer) {
      clearTimeout(S.reconnectTimer);
      S.reconnectTimer = null;
    }

    try {
      const ws = new WebSocket(WS_URL);
      S.ws = ws;

      ws.onopen = () => {
        S.connected = true;
        S.reconnectCount = 0;
        setConnStatus(true);
        wsMsg({
          type: 'reconnect',
          payload: { url: window.location.href }
        });
      };

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          handleMessage(msg);
        } catch (_) {}
      };

      ws.onclose = () => {
        S.connected = false;
        S.ws = null;
        setConnStatus(false);
        scheduleReconnect();
      };

      ws.onerror = () => {
        S.connected = false;
        setConnStatus(false);
      };
    } catch (_) {
      scheduleReconnect();
    }
  }

  function scheduleReconnect() {
    if (S.reconnectCount >= MAX_RECONNECT) return;
    S.reconnectCount++;
    S.reconnectTimer = setTimeout(connectWS, RECONNECT_DELAY);
  }

  function setConnStatus(connected) {
    const dot  = el('hermes-conn-dot');
    const text = el('hermes-conn-text');
    if (dot)  dot.className  = connected ? '' : 'off';
    if (text) text.textContent = connected ? 'Connected' : 'Disconnected';
  }

  /* ── Message Handlers ───────────────────── */
  function handleMessage(msg) {
    console.log('[HERMES] received:', msg.type,
      JSON.stringify(msg.payload).slice(0, 100));
    const type = msg.type;
    const p    = msg.payload || {};

    if (type === 'thinking' || type === 'status') {
      S.statusMsg = p.message || '';
      S.errorMsg  = null;
      render();
    }

    else if (type === 'confirmation_request') {
      S.pendingConfirm = {
        understood: p.understood || '',
        locator:    p.locator    || '',
        action:     p.action     || '',
        element:    p.element_name || '',
      };
      S.statusMsg = null;
      S.errorMsg  = null;
      if (p.highlight && p.highlight.w) {
        showHighlightByCoords(p.highlight, 'searching');
      }
      switchTab('steps');
      render();
    }

    else if (type === 'step_recorded') {
      S.pendingConfirm = null;
      S.statusMsg = null;
      S.errorMsg  = null;
      S.selectedElement = null;
      S.steps.push({
        action:     p.action      || 'click',
        label:      p.element
                    ? `${p.action} ${p.element}`
                    : p.generated_line || 'step',
        locator:    p.locator     || '',
        varName:    '',
        locatorExpr:'',
        codeLine:   p.generated_line || '',
        status:     'ok',
      });
      if (p.highlight) flashHighlight(p.highlight);
      else clearHighlight();
      updateChatPlaceholder();
      saveState();
      render();
    }

    else if (type === 'code_update') {
      if (p.full_code) S.code = p.full_code;
      else if (p.new_line) {
        const step = S.steps[S.steps.length - 1];
        if (step) step.codeLine = p.new_line;
      }
      if (S.tab === 'code') render();
    }

    else if (type === 'error') {
      S.pendingConfirm = null;
      S.statusMsg = null;
      S.errorMsg = {
        message:    p.message    || 'An error occurred',
        suggestion: p.suggestion || '',
      };
      clearHighlight();
      render();
    }

    else if (type === 'session_ready') {
      S.statusMsg = null;
      render();
    }

    else if (type === 'reconnect_confirmed') {
      S.statusMsg = null;
      render();
    }

    else if (type === 'highlight_element') {
      if (p.coords) showHighlightByCoords(p.coords, p.state || 'selected');
    }

    else if (type === 'clear_highlight') {
      clearHighlight();
    }

    else if (type === 'cancelled') {
      S.pendingConfirm = null;
      S.statusMsg = p.message || null;
      clearHighlight();
      render();
    }

    else if (type === 'script_generated') {
      if (p.code) S.code = p.code;
      S.statusMsg = p.message || 'Script generated';
      render();
      setTimeout(() => {
        S.statusMsg = null;
        render();
      }, 3000);
    }

    else if (type === 'pong') {
      /* heartbeat ok */
    }
  }

  /* ── Heartbeat ──────────────────────────── */
  function startHeartbeat() {
    setInterval(() => {
      if (S.connected) wsMsg({ type: 'ping', payload: {} });
    }, 30000);
  }

  /* ── Navigation detect ──────────────────── */
  let lastUrl = window.location.href;
  function watchNavigation() {
    setInterval(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        clearHighlight();
        S.hoveredTarget = null;
      }
    }, 500);
  }

  /* ── Register page events ───────────────── */
  function registerPageEvents() {
    document.addEventListener('mouseover', onMouseOver, true);
    document.addEventListener('mouseout',  onMouseOut,  true);
    document.addEventListener('click',     onPageClick, true);
  }

  /* ── Init ───────────────────────────────── */
  function init() {
    restoreState();
    buildPanel();
    registerPageEvents();
    connectWS();
    startHeartbeat();
    watchNavigation();
    applyDock(S.dock);
  }

  if (document.readyState === 'loading') {
    document.addEventListener(
      'DOMContentLoaded', init
    );
  } else {
    init();
  }

})();
