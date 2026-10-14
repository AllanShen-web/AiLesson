// PPT 实时预览状态：Agent 生成过程中的大纲/逐页事件中转站。
// 提到模块级单例，是因为 ChatWidget（事件来源）与 PPT 编辑器页（消费方）
// 是两棵不相邻的组件树，需要跨组件共享“当前正在生成的 PPT”状态。
import type { PptOutline, PptSlide } from './ppt';

/** 生成阶段：idle 无任务 / outline 大纲已出待确认 / slides 逐页生成中 / done 已完成 */
export type PptGenPhase = 'idle' | 'outline' | 'slides' | 'done';

export interface PptPreviewState {
  /** 正在生成的 PPT 记录编号 */
  pptId: number | null;
  title: string;
  /** 大纲阶段产物；页面内容未生成完时用于占位预览 */
  outline: PptOutline | null;
  /** 已收到的页面（含大纲占位页），下标即页码 */
  slides: PptSlide[];
  /** 是否正在生成中 */
  generating: boolean;
  /** 当前阶段：编辑器据此区分“等待确认”与“正在生成”提示语 */
  phase: PptGenPhase;
  /** 本次生成是否已全部完成 */
  done: boolean;
}

const INITIAL_STATE: PptPreviewState = {
  pptId: null,
  title: '',
  outline: null,
  slides: [],
  generating: false,
  phase: 'idle',
  done: false,
};

let _state: PptPreviewState = INITIAL_STATE;
let _listeners: Array<() => void> = [];

function setState(next: Partial<PptPreviewState>) {
  _state = { ..._state, ...next };
  _listeners.forEach((fn) => fn());
}

/** 大纲到达：用大纲页占位，随后逐页替换为真实内容；此阶段等待用户确认 */
function beginOutline(pptId: number, title: string, outline: PptOutline | null) {
  const placeholders: PptSlide[] = (outline?.sections || []).map((s, i) => ({
    layout: i === 0 ? 'cover' : 'title-bullets',
    title: s.title,
    bullets: s.points || [],
  }));
  setState({
    pptId,
    title,
    outline,
    slides: placeholders,
    generating: true,
    phase: 'outline',
    done: false,
  });
}

function pushSlide(pptId: number, index: number, slide: PptSlide) {
  if (_state.pptId !== pptId) return;
  const slides = [..._state.slides];
  slides[index] = slide;
  setState({ slides, phase: 'slides' });
}

function finish(pptId: number) {
  if (_state.pptId !== pptId) return;
  setState({ generating: false, phase: 'done', done: true });
}

export const pptPreviewStore = {
  getState(): PptPreviewState {
    return _state;
  },

  beginOutline,
  pushSlide,
  finish,

  reset() {
    _state = INITIAL_STATE;
    _listeners.forEach((fn) => fn());
  },

  subscribe(fn: () => void) {
    _listeners.push(fn);
    return () => { _listeners = _listeners.filter((l) => l !== fn); };
  },
};
