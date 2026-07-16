const API_BASE = '';

const app = {
    currentPage: 'dashboard',
    pipelineTimer: null,
    selectedDocs: new Set(),
    providers: {},
    ocrProviders: {},

    init() {
        this.bindNav();
        this.bindTheme();
        this.bindWindowControls();
        this.bindChat();
        this.bindSearch();
        this.bindInbox();
        this.bindDocuments();
        this.bindPipeline();
        this.bindSettings();
        this.bindModal();
        this.loadStats();
        this.loadGreeting();
        this.loadLLMConfig();
        this.loadOCRConfig();
        this.loadProfile();
        this.loadPrompt();
        this.navigate('dashboard');
    },

    navigate(page) {
        this.currentPage = page;
        document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.page === page));
        document.querySelectorAll('.page').forEach(el => el.classList.toggle('active', el.id === `page-${page}`));
        const titles = {
            dashboard: '概览', chat: '对话', search: '检索',
            inbox: '速记', documents: '文档管理', pipeline: '管线', settings: '设置'
        };
        document.getElementById('page-title').textContent = titles[page] || page;
        if (page === 'documents') this.loadDocuments();
        if (page === 'pipeline') this.refreshPipelineStatus();
    },

    bindNav() {
        document.querySelectorAll('.nav-item').forEach(el => {
            el.addEventListener('click', e => {
                e.preventDefault();
                this.navigate(el.dataset.page);
            });
        });
    },

    bindTheme() {
        const toggle = document.getElementById('theme-toggle');
        const saved = localStorage.getItem('theme');
        if (saved === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
        toggle.textContent = saved === 'dark' ? '☀️' : '🌙';
        toggle.addEventListener('click', () => {
            const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
            if (isDark) {
                document.documentElement.removeAttribute('data-theme');
                localStorage.setItem('theme', 'light');
                toggle.textContent = '🌙';
            } else {
                document.documentElement.setAttribute('data-theme', 'dark');
                localStorage.setItem('theme', 'dark');
                toggle.textContent = '☀️';
            }
        });
    },

    bindWindowControls() {
        const minimizeBtn = document.getElementById('window-minimize');
        const maximizeBtn = document.getElementById('window-maximize');
        const closeBtn = document.getElementById('window-close');
        const dragArea = document.getElementById('titlebar-drag');

        const api = window.pywebview && window.pywebview.api;

        if (minimizeBtn) {
            minimizeBtn.addEventListener('click', () => {
                if (api && api.minimize_window) api.minimize_window();
            });
        }

        if (maximizeBtn) {
            maximizeBtn.addEventListener('click', () => {
                if (api && api.maximize_window) api.maximize_window();
            });
        }

        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                if (api && api.close_window) {
                    api.close_window();
                } else {
                    window.close();
                }
            });
        }

        // 标题栏双击最大化/还原
        if (dragArea) {
            dragArea.addEventListener('dblclick', () => {
                if (api && api.maximize_window) api.maximize_window();
            });
        }
    },

    async api(url, options = {}) {
        try {
            const res = await fetch(url, options);
            const data = await res.json();
            if (!data.success && data.error) {
                this.showToast(data.error, 'error');
            }
            return data;
        } catch (e) {
            this.showToast('网络请求失败：' + e.message, 'error');
            return { success: false, error: e.message };
        }
    },

    showToast(message, type = 'info', duration = 3000) {
        const container = document.getElementById('toast-container');
        const div = document.createElement('div');
        div.className = `toast ${type}`;
        div.textContent = message;
        container.appendChild(div);
        setTimeout(() => div.remove(), duration);
    },

    setLoading(selector, loading) {
        const btn = typeof selector === 'string' ? document.querySelector(selector) : selector;
        if (!btn) return;
        if (loading) {
            btn.classList.add('loading');
            btn.disabled = true;
        } else {
            btn.classList.remove('loading');
            btn.disabled = false;
        }
    },

    async loadStats() {
        const data = await this.api('/api/stats');
        if (data.success) {
            document.getElementById('stat-inbox').textContent = data.data.inbox_total;
            document.getElementById('stat-docs').textContent = data.data.document_count;
            document.getElementById('stat-deleted').textContent = data.data.deleted_count;
        }
    },

    async loadGreeting() {
        const data = await this.api('/api/greeting');
        if (data.success) {
            document.getElementById('greeting-text').textContent = data.data;
        }
    },

    // ========== Chat ==========
    bindChat() {
        const send = document.getElementById('chat-send');
        const input = document.getElementById('chat-input');
        send.addEventListener('click', () => this.sendChat());
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendChat();
            }
        });
        document.getElementById('chat-clear').addEventListener('click', () => this.clearChat());
        this.loadChatHistory();
    },

    getChatSessionId() {
        let sid = localStorage.getItem('chat_session_id');
        if (!sid) {
            sid = 'session-' + Date.now();
            localStorage.setItem('chat_session_id', sid);
        }
        return sid;
    },

    async loadChatHistory() {
        const sid = this.getChatSessionId();
        const data = await this.api(`/api/chat/history?session_id=${encodeURIComponent(sid)}`);
        if (!data.success) return;
        const history = document.getElementById('chat-history');
        history.innerHTML = '';
        data.data.forEach(m => this.appendChatMessage(m.role, m.content));
    },

    async clearChat() {
        const sid = this.getChatSessionId();
        await this.api('/api/chat/history/clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sid })
        });
        document.getElementById('chat-history').innerHTML = '';
        localStorage.removeItem('chat_session_id');
    },

    async sendChat() {
        const input = document.getElementById('chat-input');
        const q = input.value.trim();
        if (!q) return;
        input.value = '';
        const type = document.getElementById('chat-type').value;
        const useRag = document.getElementById('chat-use-rag').checked;
        const sessionId = this.getChatSessionId();
        this.appendChatMessage('user', q);
        const thinking = this.appendChatMessage('assistant', '正在检索并生成回答...');
        this.setLoading('#chat-send', true);

        // 优先尝试 SSE 流式输出，失败则 fallback 到非流式接口
        const streamOk = await this.sendChatStream(q, type, useRag, sessionId, thinking);
        if (!streamOk) {
            await this.sendChatNonStream(q, type, useRag, sessionId, thinking);
        }
        this.setLoading('#chat-send', false);
    },

    async sendChatStream(q, type, useRag, sessionId, thinking) {
        if (!window.EventSource) return false;
        try {
            const resp = await fetch('/api/chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ q, type, use_rag: useRag, session_id: sessionId })
            });
            if (!resp.ok || !resp.body) return false;

            thinking.remove();
            const div = this.appendChatMessage('assistant', '');
            const bubble = div.querySelector('.message-bubble');
            const reader = resp.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';
            let sources = [];
            let mode = 'rag';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n\n');
                buffer = lines.pop() || '';
                for (const line of lines) {
                    const dataLine = line.trim();
                    if (!dataLine.startsWith('data:')) continue;
                    try {
                        const chunk = JSON.parse(dataLine.slice(5).trim());
                        if (chunk.delta) {
                            bubble.innerHTML = this.escapeHtml((bubble.textContent || '') + chunk.delta);
                        }
                        if (chunk.sources) sources = chunk.sources;
                        if (chunk.mode) mode = chunk.mode;
                        if (chunk.done) {
                            if (sources.length) {
                                const src = document.createElement('div');
                                src.className = 'message-sources';
                                src.textContent = '来源：' + sources.slice(0, 5).join('、');
                                bubble.appendChild(src);
                            }
                            return true;
                        }
                    } catch (e) {
                        console.warn('SSE chunk parse error', e);
                    }
                }
            }
            return true;
        } catch (e) {
            console.warn('SSE 流式请求失败，将 fallback', e);
            return false;
        }
    },

    async sendChatNonStream(q, type, useRag, sessionId, thinking) {
        const data = await this.api('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ q, type, use_rag: useRag, session_id: sessionId })
        });
        thinking.remove();
        if (data.success) {
            const result = data.data;
            const div = this.appendChatMessage('assistant', result.answer);
            if (result.sources && result.sources.length) {
                const src = document.createElement('div');
                src.className = 'message-sources';
                src.textContent = '来源：' + result.sources.slice(0, 5).join('、');
                div.querySelector('.message-bubble').appendChild(src);
            }
        } else {
            this.appendChatMessage('assistant', '出错了：' + (data.error || '未知错误'));
        }
    },

    appendChatMessage(role, text) {
        const history = document.getElementById('chat-history');
        const div = document.createElement('div');
        div.className = `message ${role}`;
        div.innerHTML = `<div class="message-bubble">${this.escapeHtml(text)}</div>`;
        history.appendChild(div);
        history.scrollTop = history.scrollHeight;
        return div;
    },

    // ========== Search ==========
    bindSearch() {
        document.getElementById('search-btn').addEventListener('click', () => this.doSearch());
        document.getElementById('search-input').addEventListener('keydown', e => {
            if (e.key === 'Enter') this.doSearch();
        });
    },

    async doSearch() {
        const q = document.getElementById('search-input').value.trim();
        const type = document.getElementById('search-type').value;
        if (!q) return;
        const resultsDiv = document.getElementById('search-results');
        resultsDiv.innerHTML = '<div class="result-item">搜索中...</div>';
        this.setLoading('#search-btn', true);

        const data = await this.api(`/api/search?q=${encodeURIComponent(q)}&type=${type}&top_k=10`);
        this.setLoading('#search-btn', false);
        resultsDiv.innerHTML = '';
        if (!data.success) {
            resultsDiv.innerHTML = `<div class="result-item">搜索失败：${data.error}</div>`;
            return;
        }
        if (!data.data.length) {
            resultsDiv.innerHTML = '<div class="result-item">未找到相关知识。</div>';
            return;
        }
        data.data.forEach((r, i) => {
            const div = document.createElement('div');
            div.className = 'result-item';
            div.innerHTML = `
                <div class="result-title">
                    <span>${this.escapeHtml(r.path || '')}</span>
                    <span>${r.rrf_score ? r.rrf_score.toFixed(4) : ''}</span>
                </div>
                <div class="result-meta">类型：${r.entity_type || '-'} | 来源：${r.source || 'fts5'}</div>
                <div class="result-preview">${this.escapeHtml((r.content || '').slice(0, 200))}...</div>
            `;
            div.addEventListener('click', () => this.showDocDetail(r.path));
            resultsDiv.appendChild(div);
        });
    },

    // ========== Inbox ==========
    bindInbox() {
        document.getElementById('inbox-save').addEventListener('click', async () => {
            const title = document.getElementById('inbox-title').value;
            const content = document.getElementById('inbox-content').value;
            this.setLoading('#inbox-save', true);
            const data = await this.api('/api/inbox', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, content })
            });
            this.setLoading('#inbox-save', false);
            if (data.success) {
                document.getElementById('inbox-title').value = '';
                document.getElementById('inbox-content').value = '';
                this.showToast('已投递到：' + data.path, 'success');
            }
        });
    },

    // ========== Documents ==========
    bindDocuments() {
        ['doc-keyword', 'doc-type', 'doc-show-deleted'].forEach(id => {
            document.getElementById(id).addEventListener('input', () => this.loadDocuments());
            document.getElementById(id).addEventListener('change', () => this.loadDocuments());
        });
        document.getElementById('doc-refresh').addEventListener('click', () => this.loadDocuments());
        document.getElementById('doc-select-all').addEventListener('change', e => {
            document.querySelectorAll('.doc-checkbox').forEach(cb => {
                cb.checked = e.target.checked;
                this.toggleDoc(cb.dataset.path, cb.checked);
            });
        });
        document.getElementById('doc-soft-delete').addEventListener('click', () => this.docAction('/soft_delete', '软删除'));
        document.getElementById('doc-restore').addEventListener('click', () => this.docAction('/restore', '恢复'));
        document.getElementById('doc-hard-delete').addEventListener('click', () => this.docAction('/hard_delete', '永久删除'));
        document.getElementById('doc-purge').addEventListener('click', async () => {
            if (!confirm('将永久删除回收站中的所有文档，此操作不可逆！')) return;
            const data = await this.api('/api/documents/purge', { method: 'POST' });
            alert(data.message || data.error);
            this.loadDocuments();
        });
    },

    async loadDocuments() {
        const keyword = document.getElementById('doc-keyword').value;
        const type = document.getElementById('doc-type').value;
        const includeDeleted = document.getElementById('doc-show-deleted').checked;
        const url = `/api/documents?keyword=${encodeURIComponent(keyword)}&type=${type}&include_deleted=${includeDeleted}`;
        const data = await this.api(url);
        const tbody = document.getElementById('doc-table-body');
        tbody.innerHTML = '';
        this.selectedDocs.clear();
        document.getElementById('doc-select-all').checked = false;

        if (!data.success) {
            tbody.innerHTML = `<tr><td colspan="6">加载失败：${data.error}</td></tr>`;
            return;
        }
        data.data.forEach(d => {
            const tr = document.createElement('tr');
            if (d.deleted_at) tr.classList.add('deleted');
            tr.innerHTML = `
                <td><input type="checkbox" class="doc-checkbox" data-path="${this.escapeHtml(d.path)}"></td>
                <td>${this.escapeHtml(d.path)}</td>
                <td>${d.entity_type || ''}</td>
                <td>${d.updated_at || ''}</td>
                <td>${d.deleted_at ? '回收站' : '正常'}</td>
                <td>${this.escapeHtml(d.source || '')}</td>
            `;
            tr.querySelector('.doc-checkbox').addEventListener('change', e => this.toggleDoc(d.path, e.target.checked));
            tr.addEventListener('dblclick', () => this.showDocDetail(d.path));
            tbody.appendChild(tr);
        });
        document.getElementById('doc-footer').textContent = `当前显示 ${data.data.length} 条`;
    },

    toggleDoc(path, checked) {
        if (checked) this.selectedDocs.add(path);
        else this.selectedDocs.delete(path);
    },

    async docAction(action, name) {
        if (this.selectedDocs.size === 0) {
            this.showToast('请先选择文档', 'error');
            return;
        }
        if (!confirm(`确定要对 ${this.selectedDocs.size} 篇文档执行「${name}」吗？`)) return;
        let lastMsg = '';
        for (const path of this.selectedDocs) {
            const data = await this.api(`/api/documents/${encodeURIComponent(path)}${action}`, { method: 'POST' });
            lastMsg = data.message || data.error;
        }
        this.showToast(lastMsg, 'success');
        this.selectedDocs.clear();
        this.loadDocuments();
    },

    async showDocDetail(path) {
        const data = await this.api(`/api/documents/${encodeURIComponent(path)}`);
        if (!data.success) return;
        document.getElementById('modal-title').textContent = data.data.path;
        document.getElementById('modal-body-text').textContent = data.data.content || '(无内容)';
        document.getElementById('modal').classList.add('active');
    },

    // ========== Pipeline ==========
    bindPipeline() {
        document.getElementById('pipeline-run').addEventListener('click', () => this.runPipeline('/api/pipeline/run', '管线'));
        document.getElementById('pipeline-ocr').addEventListener('click', () => this.runPipeline('/api/ocr/run', 'OCR'));
        document.getElementById('pipeline-rebuild').addEventListener('click', () => this.runPipeline('/api/vector/rebuild', '向量索引重建'));
        document.getElementById('pipeline-refresh').addEventListener('click', () => this.refreshPipelineStatus());
        document.getElementById('watcher-start').addEventListener('click', () => this.watcherAction('/api/watcher/start', 'start'));
        document.getElementById('watcher-stop').addEventListener('click', () => this.watcherAction('/api/watcher/stop', 'stop'));
    },

    async runPipeline(url, name) {
        const data = await this.api(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ options: ['--no-git', '--no-ocr'] })
        });
        if (data.success) {
            this.startPipelinePolling();
            this.setStatus('busy', `${name}运行中...`);
            this.showToast(`${name}已启动`, 'info');
        }
    },

    startPipelinePolling() {
        if (this.pipelineTimer) return;
        const logs = document.getElementById('pipeline-logs');
        this.pipelineTimer = setInterval(async () => {
            const data = await this.api('/api/pipeline/logs');
            if (data.success) {
                logs.textContent = data.data.output;
                logs.scrollTop = logs.scrollHeight;
                if (!data.data.running) {
                    clearInterval(this.pipelineTimer);
                    this.pipelineTimer = null;
                    this.setStatus('ready', '运行完成');
                    this.refreshPipelineStatus();
                }
            }
        }, 1000);
    },

    async refreshPipelineStatus() {
        const data = await this.api('/api/pipeline/status');
        if (data.success) {
            const s = data.data.stats;
            document.getElementById('pipeline-status').textContent =
                `Inbox 已处理：${s.inbox_total}  |  已索引文档：${s.document_count}`;
        }
        this.loadStats();
        this.refreshWatcherStatus();
    },

    async watcherAction(url, action) {
        const data = await this.api(url, { method: 'POST' });
        this.showToast(data.message || data.error, data.success ? 'success' : 'error');
        this.refreshWatcherStatus();
    },

    async refreshWatcherStatus() {
        const data = await this.api('/api/watcher/status');
        if (data.success) {
            const label = document.getElementById('watcher-status');
            label.textContent = data.data.running ? '运行中' : '未开启';
        }
    },

    setStatus(state, text) {
        document.getElementById('status-dot').className = 'status-dot' + (state === 'busy' ? ' busy' : '');
        document.getElementById('status-text').textContent = text;
    },

    // ========== Settings ==========
    bindSettings() {
        document.querySelectorAll('.settings-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                document.querySelectorAll('.settings-panel').forEach(p => p.classList.remove('active'));
                document.getElementById(`settings-${tab.dataset.settings}`).classList.add('active');
            });
        });

        document.getElementById('llm-provider').addEventListener('change', () => this.onLLMProviderChange());
        document.getElementById('llm-save').addEventListener('click', () => this.saveLLMConfig());
        document.getElementById('llm-test').addEventListener('click', () => this.testLLMConfig());
        document.getElementById('llm-clear').addEventListener('click', () => this.clearLLMConfig());

        document.getElementById('profile-save').addEventListener('click', () => this.saveProfile());
        document.getElementById('profile-clear').addEventListener('click', () => this.clearProfile());

        document.getElementById('prompt-save').addEventListener('click', () => this.savePrompt());

        document.getElementById('ocr-provider').addEventListener('change', () => this.onOCRProviderChange());
        document.getElementById('ocr-save').addEventListener('click', () => this.saveOCRConfig());
        document.getElementById('ocr-test').addEventListener('click', () => this.testOCRConfig());

        document.getElementById('backup-export').addEventListener('click', () => this.exportConfig());
        document.getElementById('backup-import').addEventListener('click', () => this.importConfig());
        document.getElementById('backup-dir').addEventListener('click', () => this.backupDir());

        // 诊断面板
        document.getElementById('crash-save').addEventListener('click', () => this.saveCrashConfig());
        document.getElementById('crash-view-logs').addEventListener('click', () => this.viewCrashLogs());
        document.getElementById('metrics-refresh').addEventListener('click', () => this.refreshMetrics());
        this.loadCrashConfig();
        this.loadTorchStatus();
    },

    async loadTorchStatus() {
        const el = document.getElementById('torch-status');
        const data = await this.api('/api/torch/status');
        if (data.success) {
            const d = data.data;
            el.textContent = `torch ${d.torch_version || '-'} | 后端: ${d.backend} | bge 模型: ${d.bge_model_files_ok ? '完整' : '缺失'} | 状态: ${d.healthy ? '健康' : '异常'}`;
            el.style.color = d.healthy ? 'var(--success)' : 'var(--danger)';
        } else {
            el.textContent = '获取失败：' + (data.error || '未知错误');
        }
    },

    async loadCrashConfig() {
        const data = await this.api('/api/config/crash');
        if (data.success) {
            document.getElementById('crash-reporting-enabled').checked = data.data.crash_reporting_enabled;
        }
    },

    async saveCrashConfig() {
        const enabled = document.getElementById('crash-reporting-enabled').checked;
        const data = await this.api('/api/config/crash', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ crash_reporting_enabled: enabled })
        });
        const el = document.getElementById('crash-status');
        if (data.success) {
            el.textContent = '已保存';
            el.style.color = 'var(--success)';
            this.showToast('崩溃上报设置已保存', 'success');
        } else {
            el.textContent = '保存失败：' + (data.error || '');
            el.style.color = 'var(--danger)';
        }
    },

    async viewCrashLogs() {
        const box = document.getElementById('crash-logs');
        const data = await this.api('/api/crash/logs');
        box.style.display = 'block';
        if (data.success && data.data.length) {
            box.textContent = data.data.map(l => `=== ${l.file} ===\n${l.content}`).join('\n\n');
        } else {
            box.textContent = '暂无崩溃日志。';
        }
    },

    async refreshMetrics() {
        const box = document.getElementById('metrics-list');
        const data = await this.api('/api/metrics?limit=50');
        if (data.success && data.data.length) {
            box.textContent = data.data.map(m =>
                `${m.created_at} | ${m.event} | ${m.duration_ms ?? '-'}ms | ${m.success ? 'OK' : 'FAIL'}${m.error_msg ? ' | ' + m.error_msg : ''}`
            ).join('\n');
        } else {
            box.textContent = '暂无指标数据。';
        }
    },

    async loadLLMConfig() {
        const providers = await this.api('/api/config/providers');
        if (providers.success) {
            this.providers = providers.data;
            const select = document.getElementById('llm-provider');
            select.innerHTML = '<option value="">请选择</option>' + Object.keys(this.providers).map(p =>
                `<option value="${p}">${p}</option>`
            ).join('');
        }
        const data = await this.api('/api/config/llm');
        if (data.success) {
            document.getElementById('llm-provider').value = data.data.provider || '';
            document.getElementById('llm-api-key').value = data.data.api_key || '';
            document.getElementById('llm-base-url').value = data.data.base_url || '';
            document.getElementById('llm-model').value = data.data.model || '';
        }
    },

    onLLMProviderChange() {
        const p = document.getElementById('llm-provider').value;
        if (this.providers[p]) {
            document.getElementById('llm-base-url').value = this.providers[p].base_url;
            document.getElementById('llm-model').value = this.providers[p].default_model;
        }
    },

    async saveLLMConfig() {
        const cfg = {
            provider: document.getElementById('llm-provider').value,
            api_key: document.getElementById('llm-api-key').value,
            base_url: document.getElementById('llm-base-url').value,
            model: document.getElementById('llm-model').value,
        };
        const data = await this.api('/api/config/llm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cfg)
        });
        this.showStatus('llm-status', data);
    },

    async testLLMConfig() {
        const cfg = {
            provider: document.getElementById('llm-provider').value,
            api_key: document.getElementById('llm-api-key').value,
            base_url: document.getElementById('llm-base-url').value,
        };
        const data = await this.api('/api/config/llm/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cfg)
        });
        this.showStatus('llm-status', data);
    },

    clearLLMConfig() {
        document.getElementById('llm-provider').value = '';
        document.getElementById('llm-api-key').value = '';
        document.getElementById('llm-base-url').value = '';
        document.getElementById('llm-model').value = '';
        this.saveLLMConfig();
    },

    async loadProfile() {
        const data = await this.api('/api/config/profile');
        if (!data.success) return;
        const p = data.data;
        const personal = [
            ['personal_name', '姓名'], ['personal_role', '职位/角色'], ['personal_dept', '部门'],
            ['personal_email', '邮箱'], ['personal_phone', '电话']
        ];
        const company = [
            ['company_name', '公司名称'], ['company_industry', '行业'], ['company_scale', '规模'],
            ['company_products', '产品/服务'], ['company_address', '地址'], ['company_website', '网址']
        ];
        document.getElementById('profile-personal').innerHTML = personal.map(([k, l]) => `
            <div class="form-group"><label>${l}</label><input class="input profile-field" data-key="${k}" value="${this.escapeHtml(p[k] || '')}"></div>
        `).join('');
        document.getElementById('profile-company').innerHTML = company.map(([k, l]) => `
            <div class="form-group"><label>${l}</label><input class="input profile-field" data-key="${k}" value="${this.escapeHtml(p[k] || '')}"></div>
        `).join('');
        document.getElementById('profile-extra').value = p.extra_context || '';
    },

    async saveProfile() {
        const profile = {};
        document.querySelectorAll('.profile-field').forEach(el => profile[el.dataset.key] = el.value);
        profile.extra_context = document.getElementById('profile-extra').value;
        const data = await this.api('/api/config/profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(profile)
        });
        alert(data.success ? '用户信息已保存' : '保存失败：' + data.error);
    },

    async clearProfile() {
        if (!confirm('确定清空用户信息吗？')) return;
        await this.api('/api/config/profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        this.loadProfile();
    },

    async loadPrompt() {
        const fields = await this.api('/api/config/prompt/fields');
        const values = await this.api('/api/config/prompt');
        if (!fields.success || !values.success) return;
        const container = document.getElementById('prompt-fields');
        container.innerHTML = fields.data.map(f => `
            <div class="form-group">
                <label>${f.label}</label>
                <p style="color:var(--text-secondary);font-size:13px;margin:4px 0">${f.description}</p>
                <textarea class="input prompt-field" data-key="${f.key}" rows="${f.rows}">${this.escapeHtml(values.data[f.key] || '')}</textarea>
            </div>
        `).join('');
    },

    async savePrompt() {
        const prompt = {};
        document.querySelectorAll('.prompt-field').forEach(el => prompt[el.dataset.key] = el.value);
        const data = await this.api('/api/config/prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(prompt)
        });
        alert(data.success ? '提示词已保存' : '保存失败：' + data.error);
    },

    async loadOCRConfig() {
        const providers = await this.api('/api/config/ocr/providers');
        if (providers.success) {
            this.ocrProviders = providers.data;
            const select = document.getElementById('ocr-provider');
            select.innerHTML = Object.keys(this.ocrProviders).map(p =>
                `<option value="${p}">${p}</option>`
            ).join('');
        }
        const data = await this.api('/api/config/ocr');
        if (!data.success) return;
        document.getElementById('ocr-mode').value = data.data.mode || 'api';
        document.getElementById('ocr-provider').value = data.data.provider || 'baidu';
        this.onOCRProviderChange(data.data.credentials || {});
    },

    onOCRProviderChange(savedCreds = {}) {
        const p = document.getElementById('ocr-provider').value;
        const cfg = this.ocrProviders[p];
        if (!cfg) return;
        const container = document.getElementById('ocr-credentials');
        container.innerHTML = cfg.fields.map(([k, l]) => `
            <div class="form-group">
                <label>${l}</label>
                <input class="input ocr-cred" data-key="${k}" value="${this.escapeHtml(savedCreds[k] || '')}">
            </div>
        `).join('');
    },

    async saveOCRConfig() {
        const credentials = {};
        document.querySelectorAll('.ocr-cred').forEach(el => credentials[el.dataset.key] = el.value);
        const cfg = {
            mode: document.getElementById('ocr-mode').value,
            provider: document.getElementById('ocr-provider').value,
            credentials
        };
        const data = await this.api('/api/config/ocr', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cfg)
        });
        this.showStatus('ocr-status', data);
    },

    async testOCRConfig() {
        const credentials = {};
        document.querySelectorAll('.ocr-cred').forEach(el => credentials[el.dataset.key] = el.value);
        const data = await this.api('/api/config/ocr/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider: document.getElementById('ocr-provider').value,
                credentials
            })
        });
        this.showStatus('ocr-status', data);
    },

    async exportConfig() {
        const path = document.getElementById('backup-export-path').value.trim();
        if (!path) return;
        const data = await this.api('/api/config/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        });
        this.showStatus('backup-status', data);
    },

    async importConfig() {
        const path = document.getElementById('backup-import-path').value.trim();
        if (!path) return;
        if (!confirm('导入会覆盖当前配置，是否继续？')) return;
        const data = await this.api('/api/config/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        });
        this.showStatus('backup-status', data);
    },

    async backupDir() {
        const path = document.getElementById('backup-dir-path').value.trim();
        if (!path) return;
        const data = await this.api('/api/config/backup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        });
        this.showStatus('backup-status', data);
    },

    showStatus(id, data) {
        const el = document.getElementById(id);
        el.textContent = data.message || data.error || '';
        el.style.color = data.success ? 'var(--success)' : 'var(--danger)';
        if (data.message || data.error) {
            this.showToast(data.message || data.error, data.success ? 'success' : 'error');
        }
    },

    bindModal() {
        document.getElementById('modal-close').addEventListener('click', () => {
            document.getElementById('modal').classList.remove('active');
        });
        document.getElementById('modal').addEventListener('click', e => {
            if (e.target.id === 'modal') document.getElementById('modal').classList.remove('active');
        });
    },

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
};

document.addEventListener('DOMContentLoaded', () => app.init());
