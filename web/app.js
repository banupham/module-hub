const state = {
  modules: [],
  repoPaths: {},
  profiles: [],
};

const repoKey = (id) => {
  if (id === 'tts_api' || id === 'tts_speaker') return 'tts';
  return id;
};

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function statusText(m) {
  if (m.online) {
    if (m.origin === 'managed') return 'ONLINE · HUB';
    if (m.origin === 'external') return 'ONLINE · EXTERNAL';
    return 'ONLINE';
  }
  if (m.port) return 'OFFLINE';
  return 'CHƯA CẤP PORT';
}

function renderModules() {
  const root = document.getElementById('modules');
  root.replaceChildren();

  for (const m of state.modules) {
    const card = el('article', 'module-card');
    const top = el('div', 'module-top');
    const titleWrap = el('div');
    titleWrap.append(el('h3', '', m.name));
    titleWrap.append(el('div', 'module-meta', `${m.id} · ${m.version || ''}`));
    const badge = el('span', `status ${m.online ? 'ok' : (m.port ? 'bad' : 'idle')}`, statusText(m));
    top.append(titleWrap, badge);
    card.append(top);

    const endpoint = el('div', 'endpoint-row');
    endpoint.append(el('span', 'label', 'Runtime'));
    endpoint.append(el('code', '', m.base_url || 'AUTO — Hub sẽ cấp khi cần'));
    if (m.port) endpoint.append(el('span', 'port-badge', `:${m.port}`));
    card.append(endpoint);

    const pathLabel = el('label', 'field');
    pathLabel.append(el('span', '', 'Thư mục repo'));
    const pathInput = document.createElement('input');
    pathInput.value = state.repoPaths[repoKey(m.id)] || m.cwd || '';
    pathInput.dataset.repoKey = repoKey(m.id);
    pathInput.className = 'repo-path';
    pathLabel.append(pathInput);
    card.append(pathLabel);

    const manual = el('div', 'manual-connect');
    const portInput = document.createElement('input');
    portInput.type = 'number';
    portInput.min = '1';
    portInput.max = '65535';
    portInput.placeholder = 'Port module chạy ngoài Hub';
    portInput.value = m.origin === 'external' && m.port ? m.port : '';
    const connect = el('button', 'small', m.origin === 'external' ? 'Kết nối lại' : 'Kết nối port');
    connect.onclick = async () => {
      try {
        if (!portInput.value) throw new Error('Nhập port của module đang chạy');
        await api(`/api/modules/${m.id}/connect`, {
          method: 'POST',
          body: JSON.stringify({ port: Number(portInput.value) }),
        });
        await refreshModules();
      } catch (err) { alert(err.message); }
    };
    manual.append(portInput, connect);
    card.append(manual);

    const actions = el('div', 'module-actions');
    if (m.managed) {
      const stop = el('button', 'danger small', 'Stop module');
      stop.onclick = async () => {
        try {
          await api(`/api/modules/${m.id}/stop`, { method: 'POST', body: '{}' });
          await refreshModules();
        } catch (err) { alert(err.message); }
      };
      actions.append(stop);
    }
    if (m.origin === 'external') {
      const detach = el('button', 'small', 'Ngắt kết nối');
      detach.onclick = async () => {
        try {
          await api(`/api/modules/${m.id}/disconnect`, { method: 'POST', body: '{}' });
          await refreshModules();
        } catch (err) { alert(err.message); }
      };
      actions.append(detach);
    }
    if (m.id === 'tts_speaker' && m.online && m.base_url) {
      const open = el('button', 'small', 'Mở giao diện loa');
      open.onclick = () => window.open(m.base_url, '_blank', 'noopener');
      actions.append(open);
    }
    if (m.id === 'stt' && m.online && m.base_url) {
      const open = el('button', 'small', 'Mở STT');
      open.onclick = () => window.open(m.base_url, '_blank', 'noopener');
      actions.append(open);
    }
    card.append(actions);
    root.append(card);
  }
}

async function refreshModules() {
  const data = await api('/api/modules');
  state.modules = data.modules || [];
  state.repoPaths = data.repo_paths || {};
  renderModules();
  const allocations = Object.entries(data.allocated_ports || {})
    .map(([k, v]) => `${k}:${v}`).join(' · ');
  document.getElementById('hubStatus').textContent = allocations ? `PORTS ${allocations}` : 'READY · PORT AUTO';
}

async function savePaths() {
  const repo_paths = {};
  document.querySelectorAll('.repo-path').forEach(input => {
    repo_paths[input.dataset.repoKey] = input.value.trim();
  });
  await api('/api/config/paths', {
    method: 'POST',
    body: JSON.stringify({ repo_paths }),
  });
  await refreshModules();
}

function toggleSourceFields() {
  const source = document.getElementById('sourceSelect').value;
  document.getElementById('tiktokUserWrap').classList.toggle('hidden', source !== 'tiktok');
  document.getElementById('sttSourceWrap').classList.toggle('hidden', source !== 'stt');
}

function applyProfile(profile) {
  if (!profile) return;
  document.getElementById('sourceSelect').value = profile.source || 'tiktok';
  document.getElementById('translateEnabled').checked = !!profile.translate;
  document.getElementById('targetLang').value = profile.target_lang || 'vi';
  document.getElementById('ttsEnabled').checked = profile.tts !== false;
  if (profile.stt_source) document.getElementById('sttSource').value = profile.stt_source;
  toggleSourceFields();
}

async function loadProfiles() {
  const data = await api('/api/profiles');
  state.profiles = data.profiles || [];
  const select = document.getElementById('profileSelect');
  select.replaceChildren();
  const custom = document.createElement('option');
  custom.value = '';
  custom.textContent = 'Tùy chỉnh';
  select.append(custom);
  state.profiles.forEach((p, i) => {
    const opt = document.createElement('option');
    opt.value = String(i);
    opt.textContent = p.name || p.id;
    select.append(opt);
  });
  select.onchange = () => {
    if (select.value !== '') applyProfile(state.profiles[Number(select.value)]);
  };
}

function pipelineConfig() {
  return {
    source: document.getElementById('sourceSelect').value,
    tiktok_username: document.getElementById('tiktokUsername').value.trim(),
    stt_source: document.getElementById('sttSource').value,
    stt_lang: 'en-US',
    stt_sensitivity: 'balanced',
    translate: document.getElementById('translateEnabled').checked,
    source_lang: 'auto',
    target_lang: document.getElementById('targetLang').value,
    translate_mode: 'advanced',
    tts: document.getElementById('ttsEnabled').checked,
  };
}

async function startPipeline() {
  const stateBox = document.getElementById('pipelineState');
  stateBox.textContent = 'Đang cấp port và khởi động module…';
  try {
    const data = await api('/api/pipeline/start', {
      method: 'POST',
      body: JSON.stringify({
        config: pipelineConfig(),
        auto_start: document.getElementById('autoStart').checked,
      }),
    });
    const ports = Object.entries(data.allocated_ports || {}).map(([k, v]) => `${k}:${v}`).join(' · ');
    stateBox.textContent = `RUNNING${ports ? ' · ' + ports : ''}`;
    stateBox.className = 'pipeline-state running';
    await refreshModules();
  } catch (err) {
    stateBox.textContent = `LỖI: ${err.message}`;
    stateBox.className = 'pipeline-state failed';
  }
}

async function stopPipeline() {
  try {
    await api('/api/pipeline/stop', { method: 'POST', body: '{}' });
    const box = document.getElementById('pipelineState');
    box.textContent = 'Pipeline đã dừng. Module có thể vẫn chạy để tái sử dụng.';
    box.className = 'pipeline-state';
  } catch (err) { alert(err.message); }
}

function renderEvents(events) {
  const root = document.getElementById('events');
  root.replaceChildren();
  if (!events.length) {
    root.append(el('div', 'empty', 'Chưa có event.'));
    return;
  }
  for (const item of events) {
    const row = el('div', `event ${item.kind || ''}`);
    const time = new Date(item.ts || Date.now()).toLocaleTimeString('vi-VN');
    row.append(el('span', 'event-time', time));
    row.append(el('span', 'event-kind', String(item.kind || 'event').toUpperCase()));
    row.append(el('span', 'event-message', item.message || ''));
    root.append(row);
  }
}

async function refreshEvents() {
  try {
    const data = await api('/api/events?limit=60');
    renderEvents(data.events || []);
  } catch (_) {}
}

async function refreshHubHealth() {
  try {
    const data = await api('/api/health');
    if (!Object.keys(data.allocated_ports || {}).length) {
      document.getElementById('hubStatus').textContent = `HUB :${data.port} · PORT AUTO`;
    }
  } catch (_) {
    document.getElementById('hubStatus').textContent = 'HUB OFFLINE';
  }
}

document.getElementById('refreshBtn').onclick = () => refreshModules().catch(err => alert(err.message));
document.getElementById('savePathsBtn').onclick = () => savePaths().catch(err => alert(err.message));
document.getElementById('sourceSelect').onchange = toggleSourceFields;
document.getElementById('startPipeline').onclick = startPipeline;
document.getElementById('stopPipeline').onclick = stopPipeline;

(async () => {
  toggleSourceFields();
  await Promise.all([refreshHubHealth(), refreshModules(), loadProfiles()]);
  await refreshEvents();
  setInterval(refreshEvents, 2000);
  setInterval(refreshModules, 5000);
})();
