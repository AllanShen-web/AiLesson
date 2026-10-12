// AI 助手悬浮框的 UI 状态：可见性 + 尺寸档位。
// 提到模块级单例，是因为 AppLayout 需要读 mode 来决定正文区是否给停靠面板让位，
// 而 ChatWindow 又同时是这两处状态的真正持有者——二者需共享，故做状态提升。
export type ChatMode = 'float' | 'dock' | 'wide';

const MODE_STORAGE_KEY = 'chat-window-mode';

function isChatMode(v: string): v is ChatMode {
  return v === 'float' || v === 'dock' || v === 'wide';
}

let _visible = false;
let _mode: ChatMode | null = null;
let _listeners: Array<() => void> = [];

// 静态导出会在 Node 里预渲染，模块顶层不能碰 localStorage；首次访问（只发生在浏览器）时才读档位记忆。
// 之后 _mode 恒为具体值，快照稳定，useSyncExternalStore 不会因返回值每次变化而无限重渲染。
function currentMode(): ChatMode {
  if (_mode === null) {
    _mode = 'float';
    if (typeof window !== 'undefined') {
      const saved = window.localStorage.getItem(MODE_STORAGE_KEY);
      if (saved && isChatMode(saved)) _mode = saved;
    }
  }
  return _mode;
}

/** 外部页面预设的代发消息（如教案列表的「生成 PPT」），打开聊天后由 ChatWidget 消费 */
let _pending: { message: string; lessonPlanId?: number } | null = null;

function notify() {
  _listeners.forEach((fn) => fn());
}

export const chatUiStore = {
  get visible() { return _visible; },
  get mode() { return currentMode(); },

  // 供正文侧读取：停靠分栏时要收窄页面留白，满栏时正文直接隐藏
  isDocked() { return _visible && currentMode() === 'dock'; },
  isWide() { return _visible && currentMode() === 'wide'; },

  toggleVisible() {
    _visible = !_visible;
    notify();
  },

  setVisible(v: boolean) {
    _visible = v;
    notify();
  },

  // 切换尺寸档位；从头部按钮调用时窗口本就可见，无需改 visible。
  // 档位写进 localStorage 永久记忆，下次打开（含刷新后）默认按上次选择展示。
  setMode(m: ChatMode) {
    if (currentMode() === m) return;
    _mode = m;
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(MODE_STORAGE_KEY, m);
      } catch {}
    }
    notify();
  },

  /**
   * 打开聊天并自动发送一条预设消息（可附教案上下文）。
   * 用于外部页面快捷入口（如教案列表的「生成文件」），
   * 与聊天窗内的快捷提示 chip 一样点击即发送。
   * pending 每次赋新对象：窗口已经开着时再点也能被订阅到，否则消息会积压到下次开关才发。
   */
  requestSend(message: string, lessonPlanId?: number) {
    _pending = { message, lessonPlanId };
    _visible = true;
    notify();
  },

  get pending() { return _pending; },

  clearPending() {
    if (!_pending) return;
    _pending = null;
    notify();
  },

  subscribe(fn: () => void) {
    _listeners.push(fn);
    return () => { _listeners = _listeners.filter((l) => l !== fn); };
  },
};
