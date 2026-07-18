const API_BASE = '';

const app = {
    currentPage: 'dashboard',
    pipelineTimer: null,
    selectedDocs: new Set(),
    providers: {},
    ocrProviders: {},
    // 后端访问令牌（启动时从 /api/auth/token 获取）
    token: '',
    // 笔记工作台状态
    notes: [],
    currentNotePath: null,
    noteFilterDir: '',
    noteFilterTag: '',
    noteDirty: false,

    // 可选主题色
    ACCENTS: [
        { name: '靛蓝', value: '#4f46e5' },
        { name: '湛蓝', value: '#2563eb' },
        { name: '青色', value: '#0d9488' },
        { name: '翠绿', value: '#16a34a' },
        { name: '橙色', value: '#ea580c' },
        { name: '红色', value: '#dc2626' },
        { name: '紫色', value: '#9333ea' },
        { name: '玫红', value: '#db2777' },
    ],

    async init() {
        await this.loadToken();
        this.bindNav();
        this.bindTheme();
        this.bindWindowControls();
        this.bindSidebar();
        this.bindChat();
        this.bindSearch();
        this.bindNotes();
        this.bindDocuments();
        this.bindPipeline();
        this.bindSettings();
        this.bindAppearance();
        this.bindModal();
        this.loadStats();
        this.loadGreeting();
        this.loadLLMConfig();
        this.loadOCRConfig();
        this.loadProfile();
        this.loadPrompt();
        this.loadTags();
        this.navigate('dashboard');
    },

    navigate(page) {
        this.currentPage = page;
        document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.page === page));
        document.querySelectorAll('.page').forEach(el => el.classList.toggle('active', el.id === `page-${page}`));
        const titles = {
            dashboard: '概览', chat: '对话', search: '检索',
            inbox: '笔记', documents: '知识库文档', pipeline: '本体生成', settings: '设置'
        };
        document.getElementById('page-title').textContent = titles[page] || page;
        if (page === 'documents') this.loadDocuments();
        if (page === 'pipeline') this.refreshPipelineStatus();
        if (page === 'inbox') this.loadNotes();
    },

    bindNav() {
        document.querySelectorAll('.nav-item').forEach(el => {
            el.addEventListener('click', e => {
                e.preventDefault();
                this.navigate(el.dataset.page);
            });
        });
    },

    // ========== 主题（亮色/暗色 + 主题色） ==========
    _applyThemeAttr(isDark) {
        if (isDark) document.documentElement.setAttribute('data-theme', 'dark');
        else document.documentElement.removeAttribute('data-theme');
        const use = document.getElementById('theme-icon-use');
        if (use) use.setAttribute('href', isDark ? '#i-sun' : '#i-moon');
        const light = document.getElementById('theme-mode-light');
        const dark = document.getElementById('theme-mode-dark');
        if (light && dark) {
            light.classList.toggle('active', !isDark);
            dark.classList.toggle('active', isDark);
        }
        // 主题色在暗色下需要重新计算亮度
        this.applyAccent(localStorage.getItem('accent') || this.ACCENTS[0].value, false);
    },

    bindTheme() {
        const toggle = document.getElementById('theme-toggle');
        const saved = localStorage.getItem('theme');
        this._applyThemeAttr(saved === 'dark');
        if (toggle) {
            toggle.addEventListener('click', () => {
                const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
                this._applyThemeAttr(!isDark);
                localStorage.setItem('theme', !isDark ? 'dark' : 'light');
            });
        }
    },

    // 计算颜色变体
    _hexToRgb(hex) {
        const m = hex.replace('#', '');
        return [parseInt(m.slice(0, 2), 16), parseInt(m.slice(2, 4), 16), parseInt(m.slice(4, 6), 16)];
    },

    _mix(hex, target, ratio) {
        const [r, g, b] = this._hexToRgb(hex);
        const [tr, tg, tb] = this._hexToRgb(target);
        const mix = (a, t) => Math.round(a + (t - a) * ratio);
        return `rgb(${mix(r, tr)}, ${mix(g, tg)}, ${mix(b, tb)})`;
    },

    applyAccent(hex, persist = true) {
        const root = document.documentElement;
        const isDark = root.getAttribute('data-theme') === 'dark';
        const [r, g, b] = this._hexToRgb(hex);
        const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        const shown = isDark ? this._mix(hex, '#ffffff', 0.18) : hex;
        root.style.setProperty('--accent', shown);
        root.style.setProperty('--accent-hover', this._mix(shown.startsWith('#') ? shown : hex, isDark ? '#ffffff' : '#000000', isDark ? 0.22 : 0.14));
        root.style.setProperty('--accent-soft', `rgba(${r}, ${g}, ${b}, ${isDark ? 0.16 : 0.10})`);
        root.style.setProperty('--accent-text', luminance > 0.62 && !isDark ? '#1c2333' : '#ffffff');
        if (persist) localStorage.setItem('accent', hex);
        document.querySelectorAll('.accent-swatch').forEach(s =>
            s.classList.toggle('active', s.dataset.value === hex));
    },

    bindAppearance() {
        const wrap = document.getElementById('accent-swatches');
        if (wrap) {
            wrap.innerHTML = '';
            this.ACCENTS.forEach(a => {
                const btn = document.createElement('button');
                btn.className = 'accent-swatch';
                btn.dataset.value = a.value;
                btn.title = a.name;
                btn.style.background = a.value;
                btn.style.color = a.value;
                btn.addEventListener('click', () => this.applyAccent(a.value));
                wrap.appendChild(btn);
            });
        }
        const light = document.getElementById('theme-mode-light');
        const dark = document.getElementById('theme-mode-dark');
        if (light) light.addEventListener('click', () => {
            this._applyThemeAttr(false);
            localStorage.setItem('theme', 'light');
        });
        if (dark) dark.addEventListener('click', () => {
            this._applyThemeAttr(true);
            localStorage.setItem('theme', 'dark');
        });
        // 初始化主题色与模式卡片状态
        this.applyAccent(localStorage.getItem('accent') || this.ACCENTS[0].value, false);
        this._applyThemeAttr(document.documentElement.getAttribute('data-theme') === 'dark');
    },

    // ========== 窗口控制 ==========
    bindWindowControls() {
        const minimizeBtn = document.getElementById('window-minimize');
        const maximizeBtn = document.getElementById('window-maximize');
        const closeBtn = document.getElementById('window-close');
        const dragArea = document.getElementById('titlebar-drag');

        const getApi = () => (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
        let maximized = false;
        const updateMaxIcon = () => {
            if (!maximizeBtn) return;
            const use = maximizeBtn.querySelector('use');
            if (use) use.setAttribute('href', maximized ? '#i-restore' : '#i-max');
            maximizeBtn.title = maximized ? '还原' : '最大化';
        };

        if (minimizeBtn) {
            minimizeBtn.onclick = () => {
                const api = getApi();
                if (api && api.minimize_window) {
                    api.minimize_window();
                } else {
                    console.warn('pywebview api 不可用，无法最小化');
                }
            };
        }

        if (maximizeBtn) {
            maximizeBtn.onclick = () => {
                const api = getApi();
                if (api && api.maximize_window) {
                    api.maximize_window();
                    maximized = !maximized;
                    updateMaxIcon();
                } else {
                    console.warn('pywebview api 不可用，无法最大化');
                }
            };
        }

        if (closeBtn) {
            closeBtn.onclick = () => {
                const api = getApi();
                if (api && api.close_window) {
                    api.close_window();
                } else {
                    window.close();
                }
            };
        }

        // 标题栏双击最大化/还原
        if (dragArea) {
            dragArea.ondblclick = () => {
                const api = getApi();
                if (api && api.maximize_window) {
                    api.maximize_window();
                    maximized = !maximized;
                    updateMaxIcon();
                }
            };
        }
    },

    // ========== 侧边栏（折叠 / 笔记本 / 标签） ==========
    bindSidebar() {
        const sidebar = document.getElementById('sidebar');
        const btn = document.getElementById('sidebar-collapse');
        if (!sidebar || !btn) return;

        if (localStorage.getItem('sidebar_collapsed') === '1') {
            sidebar.classList.add('collapsed');
            btn.title = '展开侧边栏';
        }

        btn.addEventListener('click', () => {
            sidebar.classList.toggle('collapsed');
            const collapsed = sidebar.classList.contains('collapsed');
            localStorage.setItem('sidebar_collapsed', collapsed ? '1' : '0');
            btn.title = collapsed ? '展开侧边栏' : '折叠侧边栏';
        });

        // 笔记本目录筛选 → 跳转笔记页；台账入口 → 知识库文档页
        document.querySelectorAll('.side-link').forEach(el => {
            el.addEventListener('click', () => {
                if (el.dataset.goto) {
                    this.navigate(el.dataset.goto);
                    return;
                }
                this.noteFilterDir = el.dataset.dir || '';
                this.noteFilterTag = '';
                this.syncNoteFilterChips();
                this.navigate('inbox');
            });
        });
    },

    async loadTags() {
        const cloud = document.getElementById('tag-cloud');
        if (!cloud) return;
        const data = await this.api('/api/tags');
        if (!data.success || !data.data.length) {
            cloud.innerHTML = '<span class="tag-empty">暂无标签</span>';
            return;
        }
        cloud.innerHTML = '';
        data.data.slice(0, 12).forEach(t => {
            const chip = document.createElement('button');
            chip.className = 'tag-chip';
            chip.textContent = `#${t.name} ${t.count}`;
            chip.addEventListener('click', () => {
                this.noteFilterTag = t.name;
                this.noteFilterDir = '';
                this.navigate('inbox');
            });
            cloud.appendChild(chip);
        });
    },

    async loadToken() {
        try {
            const res = await fetch('/api/auth/token');
            const data = await res.json();
            if (data.success && data.data && data.data.token) {
                this.token = data.data.token;
            }
        } catch (e) {
            console.warn('获取访问令牌失败', e);
        }
    },

    async api(url, options = {}) {
        try {
            options.headers = Object.assign({}, options.headers);
            if (this.token) options.headers['X-Token'] = this.token;
            const method = (options.method || 'GET').toUpperCase();
            if (method !== 'GET' && !options.headers['Content-Type']) {
                options.headers['Content-Type'] = 'application/json';
                if (!options.body) options.body = '{}';
            }
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
                headers: { 'Content-Type': 'application/json', 'X-Token': this.token || '' },
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
                <div class="result-meta">类型：${this.escapeHtml(r.entity_type || '-')} | 来源：${this.escapeHtml(r.source || 'fts5')}</div>
                <div class="result-preview">${this.escapeHtml((r.content || '').slice(0, 200))}...</div>
            `;
            div.addEventListener('click', () => this.showDocDetail(r.path));
            resultsDiv.appendChild(div);
        });
    },

    // ========== 笔记工作台 ==========
    bindNotes() {
        document.getElementById('inbox-save').addEventListener('click', () => this.saveNote());
        document.getElementById('note-new').addEventListener('click', () => this.newNote());
        document.getElementById('note-refresh').addEventListener('click', () => this.loadNotes());
        document.getElementById('note-delete').addEventListener('click', () => this.deleteNote());
        document.getElementById('note-search').addEventListener('input', () => this.renderNoteList());

        const editor = document.getElementById('inbox-content');
        editor.addEventListener('input', () => {
            this.noteDirty = true;
            this.updateNoteStatus();
            if (document.getElementById('note-body-wrap').classList.contains('show-preview')) {
                this.renderPreview();
            }
        });
        document.getElementById('inbox-title').addEventListener('input', () => { this.noteDirty = true; });

        // 编辑 / 预览切换
        document.querySelectorAll('#note-view-toggle button').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('#note-view-toggle button').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const wrap = document.getElementById('note-body-wrap');
                if (btn.dataset.view === 'preview') {
                    wrap.classList.add('show-preview');
                    this.renderPreview();
                } else {
                    wrap.classList.remove('show-preview');
                }
            });
        });

        // 列表来源筛选 chips：全部 / 速记 / AI 提炼
        document.querySelectorAll('#note-filter-chips .chip').forEach(chip => {
            chip.addEventListener('click', () => {
                this.noteFilterDir = chip.dataset.dir || '';
                this.noteFilterTag = '';
                this.syncNoteFilterChips();
                this.loadNotes();
            });
        });

        // Markdown 工具栏
        document.querySelectorAll('.md-btn').forEach(btn => {
            btn.addEventListener('click', () => this.insertMarkdown(btn.dataset.md));
        });

        // 区域拖拽调整
        this.bindPaneResizer();
        const savedW = localStorage.getItem('note_list_width');
        if (savedW) document.getElementById('note-list-pane').style.width = savedW + 'px';

        // Ctrl+S 保存
        editor.addEventListener('keydown', e => {
            if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                e.preventDefault();
                this.saveNote();
            }
        });
    },

    bindPaneResizer() {
        const resizer = document.getElementById('pane-resizer');
        const pane = document.getElementById('note-list-pane');
        if (!resizer || !pane) return;
        let startX = 0, startW = 0;

        const onMove = e => {
            const w = Math.min(480, Math.max(180, startW + e.clientX - startX));
            pane.style.width = w + 'px';
        };
        const onUp = e => {
            resizer.classList.remove('dragging');
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            localStorage.setItem('note_list_width', parseInt(pane.style.width) || 260);
        };
        resizer.addEventListener('mousedown', e => {
            startX = e.clientX;
            startW = pane.offsetWidth;
            resizer.classList.add('dragging');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            e.preventDefault();
        });
    },

    async loadNotes() {
        let url = '/api/notes?';
        if (this.noteFilterTag) url += `tag=${encodeURIComponent(this.noteFilterTag)}&`;
        const data = await this.api(url);
        if (!data.success) return;
        this.notes = data.data.filter(n => this._matchNoteDir(n));
        this.renderNoteList();
        this.updateNotebookCounts();
    },

    updateNotebookCounts() {
        const all = this.notes.length;
        const inbox = this.notes.filter(n => n.dir.startsWith('00-Inbox')).length;
        const generated = all - inbox;
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        if (!this.noteFilterDir && !this.noteFilterTag) {
            set('nb-count-all', all);
            set('nb-count-inbox', inbox);
            set('nb-count-theory', generated);
        }
    },

    syncNoteFilterChips() {
        document.querySelectorAll('#note-filter-chips .chip').forEach(c =>
            c.classList.toggle('active', (c.dataset.dir || '') === this.noteFilterDir));
    },

    // 笔记来源：速记（用户手写） / AI 提炼（本体生成自动产出，分布于各知识层）
    _noteSource(dir) {
        return dir && dir.startsWith('00-Inbox')
            ? { label: '速记', cls: 'inbox' }
            : { label: 'AI 提炼', cls: 'ai' };
    },

    // 目录筛选匹配：generated 表示所有非速记箱的 AI 提炼内容
    _matchNoteDir(n) {
        if (!this.noteFilterDir) return true;
        if (this.noteFilterDir === 'generated') return !n.dir.startsWith('00-Inbox');
        return n.dir.startsWith(this.noteFilterDir);
    },

    renderNoteList() {
        const list = document.getElementById('note-list');
        const keyword = (document.getElementById('note-search').value || '').trim().toLowerCase();
        const notes = keyword
            ? this.notes.filter(n => n.name.toLowerCase().includes(keyword) || n.preview.toLowerCase().includes(keyword))
            : this.notes;
        list.innerHTML = '';
        if (!notes.length) {
            const hints = {
                '00-Inbox': '速记箱为空<br>点击右上角「+」写下第一条笔记',
                'generated': '暂无 AI 提炼笔记<br>在「本体生成」页点击「运行本体生成」后，<br>会自动从你的速记中提炼生成',
            };
            list.innerHTML = `<div class="note-list-empty">${hints[this.noteFilterDir] || '暂无笔记<br>点击右上角「+」新建一篇'}</div>`;
            return;
        }
        notes.forEach(n => {
            const src = this._noteSource(n.dir);
            const item = document.createElement('div');
            item.className = 'note-item' + (n.path === this.currentNotePath ? ' active' : '');
            item.innerHTML = `
                <div class="note-item-title">${this.escapeHtml(n.name)}</div>
                <div class="note-item-preview">${this.escapeHtml(n.preview || '(空笔记)')}</div>
                <div class="note-item-meta"><span class="note-src-badge ${src.cls}">${src.label}</span><span>${n.updated_at}</span>${n.tags.length ? `<span>${n.tags.map(t => '#' + this.escapeHtml(t)).join(' ')}</span>` : ''}</div>
            `;
            item.addEventListener('click', () => this.openNote(n.path));
            list.appendChild(item);
        });
    },

    async openNote(path) {
        const data = await this.api(`/api/notes/content?path=${encodeURIComponent(path)}`);
        if (!data.success) return;
        this.currentNotePath = data.data.path;
        document.getElementById('inbox-title').value = data.data.name;
        document.getElementById('inbox-content').value = data.data.content;
        this.noteDirty = false;
        document.getElementById('note-delete').style.display = '';
        this.renderNoteList();
        this.updateNoteStatus();
        if (document.getElementById('note-body-wrap').classList.contains('show-preview')) {
            this.renderPreview();
        }
    },

    newNote() {
        this.currentNotePath = null;
        document.getElementById('inbox-title').value = '';
        document.getElementById('inbox-content').value = '';
        this.noteDirty = false;
        document.getElementById('note-delete').style.display = 'none';
        this.renderNoteList();
        this.updateNoteStatus();
        document.getElementById('inbox-title').focus();
    },

    async saveNote() {
        const title = document.getElementById('inbox-title').value;
        const content = document.getElementById('inbox-content').value;
        if (!title.trim() && !content.trim()) {
            this.showToast('笔记内容为空', 'error');
            return;
        }
        this.setLoading('#inbox-save', true);
        const data = await this.api('/api/notes/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: this.currentNotePath, title, content })
        });
        this.setLoading('#inbox-save', false);
        if (data.success) {
            this.currentNotePath = data.path;
            this.noteDirty = false;
            document.getElementById('note-delete').style.display = '';
            document.getElementById('note-save-state').textContent = '已保存 ' + new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
            this.showToast('笔记已保存', 'success');
            this.loadNotes();
            this.loadTags();
        }
    },

    async deleteNote() {
        if (!this.currentNotePath) return;
        if (!confirm('确定删除这篇笔记吗？此操作不可恢复。')) return;
        const data = await this.api('/api/notes/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: this.currentNotePath })
        });
        if (data.success) {
            this.showToast('笔记已删除', 'success');
            this.newNote();
            this.loadNotes();
            this.loadTags();
        }
    },

    updateNoteStatus() {
        const content = document.getElementById('inbox-content').value;
        const pathEl = document.getElementById('note-current-path');
        if (this.currentNotePath) {
            const src = this._noteSource(this.currentNotePath);
            pathEl.textContent = `${src.label} · ${this.currentNotePath}`;
        } else {
            pathEl.textContent = '新笔记（保存到速记箱）';
        }
        const cjk = (content.match(/[\u4e00-\u9fff]/g) || []).length;
        const words = (content.replace(/[\u4e00-\u9fff]/g, ' ').match(/\S+/g) || []).length;
        document.getElementById('note-word-count').textContent = `${cjk + words} 字`;
        if (this.noteDirty) {
            document.getElementById('note-save-state').textContent = '未保存';
        }
    },

    insertMarkdown(type) {
        const editor = document.getElementById('inbox-content');
        const start = editor.selectionStart;
        const end = editor.selectionEnd;
        const selected = editor.value.slice(start, end);
        const lineStart = editor.value.lastIndexOf('\n', start - 1) + 1;
        let before = '', after = '', replacement = null, cursorOffset = 0;

        switch (type) {
            case 'bold': before = '**'; after = '**'; break;
            case 'italic': before = '*'; after = '*'; break;
            case 'code':
                replacement = `\n\`\`\`\n${selected || '代码'}\n\`\`\`\n`;
                cursorOffset = 5;
                break;
            case 'quote': before = '\n> '; break;
            case 'heading': before = '\n## '; break;
            case 'todo': before = '\n- [ ] '; break;
            case 'list': before = '\n- '; break;
        }

        if (replacement !== null) {
            editor.setRangeText(replacement, lineStart, end, 'end');
        } else {
            editor.setRangeText(before + selected + after, start, end, 'end');
        }
        editor.focus();
        this.noteDirty = true;
        this.updateNoteStatus();
        if (document.getElementById('note-body-wrap').classList.contains('show-preview')) {
            this.renderPreview();
        }
    },

    renderPreview() {
        const content = document.getElementById('inbox-content').value;
        document.getElementById('note-preview').innerHTML = this.renderMarkdown(content);
    },

    // 轻量 Markdown 渲染：标题/加粗/斜体/行内代码/代码块/引用/待办/列表/链接/分隔线
    renderMarkdown(src) {
        const escape = s => this.escapeHtml(s);
        const codeBlocks = [];
        // 先提取代码块，避免内部被二次处理
        let text = src.replace(/```(\w*)\n([\s\S]*?)(?:```|$)/g, (m, lang, code) => {
            codeBlocks.push(`<pre><code class="lang-${escape(lang)}">${escape(code.replace(/\n$/, ''))}</code></pre>`);
            return ` CODE${codeBlocks.length - 1} `;
        });

        const inline = s => {
            s = escape(s);
            s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
            s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
            // 链接 URL 协议白名单：仅 http/https/mailto，非法协议降级为纯文本
            s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, url) => {
                if (/^(https?:|mailto:)/i.test(url)) {
                    return `<a href="${url}" target="_blank" rel="noopener">${text}</a>`;
                }
                return text;
            });
            return s;
        };

        const lines = text.split('\n');
        const html = [];
        let listType = null; // ul | ol | task
        const closeList = () => {
            if (listType) { html.push(`</${listType === 'task' ? 'ul' : listType}>`); listType = null; }
        };

        for (const raw of lines) {
            const line = raw;
            const trimmed = line.trim();

            if (/^ CODE\d+ $/.test(trimmed)) {
                closeList();
                html.push(codeBlocks[parseInt(trimmed.slice(4, -1))]);
                continue;
            }
            if (!trimmed) { closeList(); continue; }

            const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
            if (heading) {
                closeList();
                const level = heading[1].length;
                html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
                continue;
            }
            if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
                closeList();
                html.push('<hr>');
                continue;
            }
            if (trimmed.startsWith('>')) {
                closeList();
                html.push(`<blockquote><p>${inline(trimmed.replace(/^>\s?/, ''))}</p></blockquote>`);
                continue;
            }
            const task = trimmed.match(/^[-*]\s+\[([ xX])\]\s+(.*)$/);
            if (task) {
                if (listType !== 'task') { closeList(); html.push('<ul>'); listType = 'task'; }
                const done = task[1].toLowerCase() === 'x';
                html.push(`<li class="task-item${done ? ' done' : ''}"><input type="checkbox" disabled${done ? ' checked' : ''}><span class="task-text">${inline(task[2])}</span></li>`);
                continue;
            }
            const ul = trimmed.match(/^[-*]\s+(.*)$/);
            if (ul) {
                if (listType !== 'ul') { closeList(); html.push('<ul>'); listType = 'ul'; }
                html.push(`<li>${inline(ul[1])}</li>`);
                continue;
            }
            const ol = trimmed.match(/^\d+[.)]\s+(.*)$/);
            if (ol) {
                if (listType !== 'ol') { closeList(); html.push('<ol>'); listType = 'ol'; }
                html.push(`<li>${inline(ol[1])}</li>`);
                continue;
            }
            closeList();
            html.push(`<p>${inline(trimmed)}</p>`);
        }
        closeList();
        return html.join('\n') || '<p style="color:var(--text-tertiary)">暂无内容</p>';
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
                <td>${this.escapeHtml(d.entity_type || '')}</td>
                <td>${this.escapeHtml(d.updated_at || '')}</td>
                <td><span class="badge ${d.deleted_at ? 'danger' : 'success'}">${d.deleted_at ? '回收站' : '正常'}</span></td>
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
        document.getElementById('pipeline-run').addEventListener('click', () => this.runPipeline('/api/pipeline/run', '本体生成'));
        document.getElementById('pipeline-run-reset').addEventListener('click', () => {
            if (!confirm('将清除所有速记的已处理标记并重新提炼，旧的手动修改可能被覆盖，确定继续吗？')) return;
            this.runPipeline('/api/pipeline/run', '本体生成（重置）', ['--no-git', '--no-ocr', '--reset']);
        });
        document.getElementById('pipeline-ocr').addEventListener('click', () => this.runPipeline('/api/ocr/run', 'OCR'));
        document.getElementById('pipeline-rebuild').addEventListener('click', () => this.runPipeline('/api/vector/rebuild', '向量索引重建'));
        document.getElementById('pipeline-refresh').addEventListener('click', () => this.refreshPipelineStatus());
        document.getElementById('watcher-start').addEventListener('click', () => this.watcherAction('/api/watcher/start', 'start'));
        document.getElementById('watcher-stop').addEventListener('click', () => this.watcherAction('/api/watcher/stop', 'stop'));
    },

    async runPipeline(url, name, options = ['--no-git', '--no-ocr']) {
        const data = await this.api(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ options })
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
            label.className = 'badge ' + (data.data.running ? 'success' : '');
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
        document.getElementById('crash-view-logs').addEventListener('click', () => this.viewCrashLogs());
        document.getElementById('metrics-refresh').addEventListener('click', () => this.refreshMetrics());
        this.loadTorchStatus();
    },

    async loadTorchStatus() {
        const el = document.getElementById('torch-status');
        const data = await this.api('/api/environment/status');
        if (!data.success) {
            el.textContent = '获取失败：' + (data.error || '未知错误');
            el.style.color = 'var(--danger)';
            return;
        }
        const d = data.data;
        const torch = d.torch || {};
        const ocr = d.ocr || {};
        const watchdog = d.watchdog || {};

        const rows = [
            ['PyTorch 版本', torch.torch_version || '-'],
            ['后端', torch.backend || '-'],
            ['CPU 可用', torch.cpu_available ? '是' : '否'],
            ['CUDA 可用', torch.cuda_available ? '是' : '否'],
            ['sentence-transformers', torch.sentence_transformers_available ? '是' : '否'],
            ['bge 模型文件', torch.bge_model_files_ok ? '完整' : '缺失'],
            ['bge 模型路径', torch.bge_model_path || '-'],
            ['OCR 模式', ocr.mode || '-'],
            ['OCR 可用', ocr.available ? '是' : '否'],
            ['OCR 版本', ocr.version || '-'],
            ['OCR 错误', ocr.error || '-'],
            ['watchdog 已安装', watchdog.installed ? '是' : '否'],
            ['watchdog 版本', watchdog.version || '-'],
            ['watchdog 运行中', watchdog.running ? '是' : '否'],
            ['watchdog 监控目录', watchdog.watch_dir || '-'],
            ['整体健康', d.healthy ? '健康' : '异常'],
        ];

        el.innerHTML = rows.map(([k, v]) => {
            if (v === '-') return '';
            return `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border)"><span>${this.escapeHtml(k)}</span><span>${this.escapeHtml(String(v))}</span></div>`;
        }).join('');
        el.style.color = d.healthy ? 'var(--success)' : 'var(--danger)';
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
        const buildField = (container, key, label, value) => {
            const group = document.createElement('div');
            group.className = 'form-group';
            const labelEl = document.createElement('label');
            labelEl.textContent = label;
            const input = document.createElement('input');
            input.className = 'input profile-field';
            input.dataset.key = key;
            input.value = value || '';
            group.appendChild(labelEl);
            group.appendChild(input);
            container.appendChild(group);
        };
        const personalBox = document.getElementById('profile-personal');
        personalBox.innerHTML = '';
        personal.forEach(([k, l]) => buildField(personalBox, k, l, p[k]));
        const companyBox = document.getElementById('profile-company');
        companyBox.innerHTML = '';
        company.forEach(([k, l]) => buildField(companyBox, k, l, p[k]));
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
        container.innerHTML = '';
        fields.data.forEach(f => {
            const group = document.createElement('div');
            group.className = 'form-group';
            const label = document.createElement('label');
            label.textContent = f.label;
            const desc = document.createElement('p');
            desc.style.cssText = 'color:var(--text-secondary);font-size:13px;margin:4px 0';
            desc.textContent = f.description;
            const textarea = document.createElement('textarea');
            textarea.className = 'input prompt-field';
            textarea.dataset.key = f.key;
            textarea.rows = f.rows;
            textarea.value = values.data[f.key] || '';
            group.appendChild(label);
            group.appendChild(desc);
            group.appendChild(textarea);
            container.appendChild(group);
        });
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
        return String(text ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
};

document.addEventListener('DOMContentLoaded', () => app.init());
