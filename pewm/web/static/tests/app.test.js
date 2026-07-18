import { describe, expect, it, beforeEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const APP_JS = readFileSync(join(__dirname, '../js/app.js'), 'utf-8');

// ========== 公共工具：在 jsdom 中加载 app.js ==========
function loadApp() {
  // app.js 末尾应自动调用 app.init()，但我们在 jsdom 里手动控制
  const script = new Function(APP_JS + '\nreturn app;');
  return script();
}

function setupDOM() {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <button id="theme-toggle">🌙</button>
    <button id="chat-send"></button>
    <textarea id="chat-input"></textarea>
    <div id="chat-history"></div>
    <button id="chat-clear"></button>
    <select id="chat-type"></select>
    <input type="checkbox" id="chat-use-rag" checked>
    <button class="loading-test"></button>
  `;
}

// ========== 主题切换 ==========
describe('主题切换', () => {
  beforeEach(() => {
    setupDOM();
    document.documentElement.removeAttribute('data-theme');
    localStorage.clear();
  });

  it('浅色 → 深色切换并写入 localStorage', () => {
    const app = loadApp();
    const toggle = document.getElementById('theme-toggle');
    app.bindTheme();
    toggle.click();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(localStorage.getItem('theme')).toBe('dark');
  });

  it('深色 → 浅色切换并写入 localStorage', () => {
    const app = loadApp();
    document.documentElement.setAttribute('data-theme', 'dark');
    localStorage.setItem('theme', 'dark');
    const toggle = document.getElementById('theme-toggle');
    app.bindTheme();
    toggle.click();
    expect(document.documentElement.getAttribute('data-theme')).toBe(null);
    expect(localStorage.getItem('theme')).toBe('light');
  });
});

// ========== api() 封装 ==========
describe('api() 封装', () => {
  beforeEach(() => {
    setupDOM();
    vi.restoreAllMocks();
  });

  it('成功请求返回 data 且不弹 toast', async () => {
    const app = loadApp();
    global.fetch = vi.fn().mockResolvedValue({
      json: async () => ({ success: true, data: { x: 1 } }),
    });
    const res = await app.api('/api/stats');
    expect(res.success).toBe(true);
    expect(res.data.x).toBe(1);
    expect(document.querySelectorAll('.toast').length).toBe(0);
  });

  it('success=false 时弹出 error toast', async () => {
    const app = loadApp();
    global.fetch = vi.fn().mockResolvedValue({
      json: async () => ({ success: false, error: '出错了' }),
    });
    await app.api('/api/stats');
    const toasts = document.querySelectorAll('.toast.error');
    expect(toasts.length).toBe(1);
    expect(toasts[0].textContent).toContain('出错了');
  });

  it('网络异常时返回 success:false 并弹 toast', async () => {
    const app = loadApp();
    global.fetch = vi.fn().mockRejectedValue(new Error('网络断开'));
    const res = await app.api('/api/stats');
    expect(res.success).toBe(false);
    expect(res.error).toContain('网络断开');
    expect(document.querySelectorAll('.toast.error').length).toBe(1);
  });
});

// ========== showToast ==========
describe('showToast', () => {
  beforeEach(setupDOM);

  it('追加 toast 并在容器内可见', () => {
    const app = loadApp();
    app.showToast('测试消息', 'success', 10000);
    const toasts = document.querySelectorAll('.toast.success');
    expect(toasts.length).toBe(1);
    expect(toasts[0].textContent).toBe('测试消息');
  });
});

// ========== setLoading ==========
describe('setLoading', () => {
  beforeEach(setupDOM);

  it('设置 loading 态并禁用按钮', () => {
    const app = loadApp();
    const btn = document.querySelector('.loading-test');
    app.setLoading(btn, true);
    expect(btn.classList.contains('loading')).toBe(true);
    expect(btn.disabled).toBe(true);
    app.setLoading(btn, false);
    expect(btn.classList.contains('loading')).toBe(false);
    expect(btn.disabled).toBe(false);
  });
});

// ========== escapeHtml ==========
describe('escapeHtml', () => {
  beforeEach(setupDOM);

  it('转义 HTML 特殊字符', () => {
    const app = loadApp();
    expect(app.escapeHtml('<script>alert(1)</script>')).toBe(
      '&lt;script&gt;alert(1)&lt;/script&gt;'
    );
  });

  it('转义双引号和单引号（防属性注入 XSS）', () => {
    const app = loadApp();
    expect(app.escapeHtml(`" onmouseover="alert(1)'`)).toBe(
      '&quot; onmouseover=&quot;alert(1)&#39;'
    );
  });
});

// ========== renderMarkdown 链接协议白名单 ==========
describe('renderMarkdown 链接安全', () => {
  beforeEach(setupDOM);

  it('http/https/mailto 链接正常渲染', () => {
    const app = loadApp();
    const html = app.renderMarkdown('[站点](https://example.com) 和 [邮件](mailto:a@b.com)');
    expect(html).toContain('<a href="https://example.com"');
    expect(html).toContain('<a href="mailto:a@b.com"');
  });

  it('非法协议（javascript:）降级为纯文本', () => {
    const app = loadApp();
    const html = app.renderMarkdown('[点我](javascript:alert(1))');
    expect(html).not.toContain('javascript:');
    expect(html).not.toContain('<a href');
  });
});

// ========== getChatSessionId ==========
describe('getChatSessionId', () => {
  beforeEach(() => {
    setupDOM();
    localStorage.clear();
  });

  it('首次生成并缓存 session id', () => {
    const app = loadApp();
    const sid1 = app.getChatSessionId();
    const sid2 = app.getChatSessionId();
    expect(sid1).toBe(sid2);
    expect(sid1).toMatch(/^session-/);
    localStorage.removeItem('chat_session_id');
    // mock Date.now 返回不同时间戳，确保生成新 id
    const realNow = Date.now;
    Date.now = () => realNow() + 1000;
    const sid3 = app.getChatSessionId();
    Date.now = realNow;
    expect(sid3).not.toBe(sid1);
  });
});

// ========== 窗口控制绑定 ==========
describe('窗口控制', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <button id="window-minimize"></button>
      <button id="window-maximize"></button>
      <button id="window-close"></button>
      <div id="titlebar-drag"></div>
    `;
  });

  it('点击最小化调用 pywebview api', () => {
    const app = loadApp();
    const minimize = vi.fn();
    window.pywebview = { api: { minimize_window: minimize } };
    app.bindWindowControls();
    document.getElementById('window-minimize').click();
    expect(minimize).toHaveBeenCalled();
  });

  it('无 pywebview 时关闭按钮 fallback 到 window.close', () => {
    const app = loadApp();
    window.pywebview = undefined;
    const closeSpy = vi.spyOn(window, 'close').mockImplementation(() => {});
    app.bindWindowControls();
    document.getElementById('window-close').click();
    expect(closeSpy).toHaveBeenCalled();
    closeSpy.mockRestore();
  });
});
