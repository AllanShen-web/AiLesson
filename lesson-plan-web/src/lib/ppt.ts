import { api } from './api';
import { API_BASE } from './env';

/** 学段：primary 小学 / middle 中学 / college 大学（含高职），驱动视觉风格与内容密度 */
export type PptStage = 'primary' | 'middle' | 'college';

/** 单页版式：基础 6 种 + 富版式 5 种（大数字/图表/时间线/对比/课堂活动） */
export type PptSlideLayout =
  | 'cover'
  | 'toc'
  | 'title-bullets'
  | 'two-column'
  | 'section'
  | 'end'
  | 'big-number'
  | 'chart'
  | 'timeline'
  | 'compare'
  | 'activity';

/** 大数字页 */
export interface PptBigNumber {
  value: string;
  unit?: string;
  label?: string;
}

/** 图表数据 */
export interface PptChart {
  type: 'bar' | 'line' | 'pie';
  categories: string[];
  series: { name: string; data: number[] }[];
}

/** 时间线/流程步骤 */
export interface PptTimelineStep {
  title: string;
  desc?: string;
}

/** 对比页两侧 */
export interface PptCompareSide {
  title: string;
  bullets: string[];
}
export interface PptCompare {
  left: PptCompareSide;
  right: PptCompareSide;
}

/** 课堂活动 */
export interface PptActivity {
  name: string;
  steps: string[];
}

export interface PptSlide {
  layout: PptSlideLayout;
  title: string;
  /** 要点列表；two-column 版式下前半为左栏、后半为右栏 */
  bullets: string[];
  /** 正文兜底（bullets 为空时使用） */
  content?: string;
  /** 演讲者备注 */
  notes?: string;
  bigNumber?: PptBigNumber;
  chart?: PptChart;
  timeline?: PptTimelineStep[];
  compare?: PptCompare;
  activity?: PptActivity;
}

export interface PptTheme {
  primary: string;
  bg: string;
  titleColor: string;
  textColor: string;
  font: string;
  /** 辅助色（装饰、编号底），缺省时由 primary 提亮派生 */
  accent?: string;
  /** 装饰风格：band 色块 / card 卡片 / plain 简约 */
  style?: 'plain' | 'band' | 'card';
}

/** 幻灯片文档：AI 生成与在线编辑的共同数据源 */
export interface PptDoc {
  title: string;
  theme: PptTheme;
  slides: PptSlide[];
  /** 学段，决定视觉风格与内容密度；缺省按中学 */
  stage?: PptStage;
}

export interface PptOutlineSection {
  title: string;
  points: string[];
  /** 该页建议版式 */
  layout?: PptSlideLayout;
}

export interface PptOutline {
  sections: PptOutlineSection[];
}

/** outline = 仅大纲；done = 页面内容已生成 */
export type PptStatus = 'outline' | 'done';

/** 来源：generated 从零生成；imported 导入外部模板改写 */
export type PptSource = 'generated' | 'imported';

export interface PptItem {
  id: number;
  title: string;
  subject: string;
  grade: string;
  lessonPlanId: number | null;
  status: PptStatus;
  outline: PptOutline;
  doc: PptDoc;
  isPublic: boolean;
  userId: number;
  creatorName: string;
  source?: PptSource;
  originalName?: string;
  /** 仅导入记录：解析出的模板结构（用于页数等） */
  structure?: { slideCount: number; slides: any[] };
  /** 仅导入记录：是否已产生改写结果（二进制本身不随接口返回） */
  hasWorking?: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface PptPageResult {
  items: PptItem[];
  total: number;
  page: number;
  pageSize: number;
}

/** 改写报告：产物记录 + 覆盖率，字段与后端 RewriteReport 一致 */
export interface RewriteReport {
  id: number;
  title: string;
  pages: number;
  templatePages: number;
  applied: number;
  deletedSlides: number[];
  watermarkSlots: number;
  echoedFixed: number;
  clearedSlots: number;
  pagePlanFailedBatches: number;
  notEditableTexts: number;
  unrewritten: { target: string; text: string }[];
  blanked: { target: string; text: string }[];
}

/** 列表查询：分页与排序走后端统一的 query 口径；用 `type` 才赋得进 `api.get` 的参数形状 */
export type PptQuery = {
  subject?: string;
  grade?: string;
  keyword?: string;
  sortField?: 'createdAt' | 'updatedAt';
  sortOrder?: 'asc' | 'desc';
  page?: number;
  pageSize?: number;
};

/** 后台改写任务：进度活在服务进程里，前端轮询它把等待过程显示出来 */
export interface RewriteTask {
  id: string;
  pptId: number;
  lessonPlanId: number;
  /** queued 是已提交但在排队：改写串行跑，一次只动一份课件 */
  status: 'queued' | 'running' | 'done' | 'error';
  stage: string;
  done: number;
  total: number;
  createdAt: string;
  report?: RewriteReport;
  error?: string;
}

/** 还没结束的任务：正在跑，或者在队列里等。轮询要不要继续、卡片要不要转圈都看这个 */
export const isPendingTask = (task: RewriteTask) => task.status === 'queued' || task.status === 'running';

/** 主题缺省值，与后端 DEFAULT_PPT_THEME 保持一致 */
export const DEFAULT_PPT_THEME: PptTheme = {
  primary: '2563EB',
  bg: 'FFFFFF',
  titleColor: '1E3A8A',
  textColor: '334155',
  font: '微软雅黑',
  accent: '93C5FD',
  style: 'band',
};

/** 内置主题预设：与后端 PPT_THEME_PRESETS 保持一致（AI 生成与手动切换共用） */
export const PPT_THEME_PRESETS: { key: string; name: string; theme: PptTheme }[] = [
  {
    key: 'ocean',
    name: '海洋蓝',
    theme: { primary: '2563EB', accent: '93C5FD', bg: 'FFFFFF', titleColor: '1E3A8A', textColor: '334155', font: '微软雅黑', style: 'band' },
  },
  {
    key: 'forest',
    name: '青苗绿',
    theme: { primary: '059669', accent: 'A7F3D0', bg: 'F0FDF4', titleColor: '064E3B', textColor: '1F2937', font: '微软雅黑', style: 'card' },
  },
  {
    key: 'sunset',
    name: '暖阳橙',
    theme: { primary: 'EA580C', accent: 'FDBA74', bg: 'FFF7ED', titleColor: '7C2D12', textColor: '292524', font: '微软雅黑', style: 'band' },
  },
  {
    key: 'grape',
    name: '葡萄紫',
    theme: { primary: '7C3AED', accent: 'DDD6FE', bg: 'FAF5FF', titleColor: '4C1D95', textColor: '1F2937', font: '微软雅黑', style: 'card' },
  },
  {
    key: 'candy',
    name: '糖果粉',
    theme: { primary: 'DB2777', accent: 'FBCFE8', bg: 'FDF2F8', titleColor: '831843', textColor: '3F3F46', font: '微软雅黑', style: 'band' },
  },
  {
    key: 'ink',
    name: '水墨黑',
    theme: { primary: '374151', accent: 'D1D5DB', bg: 'FFFFFF', titleColor: '111827', textColor: '374151', font: '微软雅黑', style: 'plain' },
  },
];

export const LAYOUT_LABELS: Record<PptSlideLayout, string> = {
  cover: '封面',
  toc: '目录',
  'title-bullets': '标题要点',
  'two-column': '双栏',
  section: '章节页',
  end: '结束页',
  'big-number': '大数字',
  chart: '图表页',
  timeline: '时间线流程',
  compare: '对比页',
  activity: '课堂活动',
};

/** 新建空白页时的默认版式序列引导（编辑器“新增页面”用） */
export function createSlide(layout: PptSlideLayout, title = ''): PptSlide {
  return { layout, title, bullets: [], content: '', notes: '' };
}

export const pptApi = {
  /** 课件库：我建的加上别人公开的 */
  getList: (query?: PptQuery) => api.get<PptPageResult>('/ppt', query),
  getMy: (query?: PptQuery) => api.get<PptItem[]>('/ppt/mine', query),
  getPublic: () => api.get<PptItem[]>('/ppt/public'),
  getById: (id: number) => api.get<PptItem>(`/ppt/${id}`),
  create: (data: { title: string; subject?: string; grade?: string; lessonPlanId?: number; doc?: PptDoc }) =>
    api.post<PptItem>('/ppt', data),
  update: (id: number, data: { title?: string; subject?: string; grade?: string; doc?: PptDoc; isPublic?: boolean }) =>
    api.put<PptItem>(`/ppt/${id}`, data),
  remove: (id: number) => api.del<{ id: number }>(`/ppt/${id}`),
  copy: (id: number) => api.post<PptItem>(`/ppt/${id}/copy`),
  togglePublic: (id: number) => api.patch<PptItem>(`/ppt/${id}/public`),
  /** 发起一次模板改写：接口当场只回任务，改写本身在服务端后台跑 */
  startRewrite: (id: number, lessonPlanId: number) =>
    api.post<RewriteTask>(`/ppt/${id}/ai-rewrite`, { lessonPlanId }),
  /** 我发起过的改写任务，新→旧；产物落地后 report 里带新课件记录 id */
  listRewriteTasks: () => api.get<RewriteTask[]>('/ppt/rewrite-tasks'),
};

/** 导出、下载、预览图回的是二进制，不能走 `api` 的 JSON 解析，只自己带令牌 */
async function fetchBinary(path: string, failMessage: string, init?: RequestInit): Promise<Response> {
  const token = typeof window !== 'undefined' ? sessionStorage.getItem('token') : null;
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(failMessage);
  return res;
}

/** 把拿到的字节交给一个临时 <a> 触发下载；objectURL 用完立刻回收 */
async function saveBlob(res: Response, fileName: string): Promise<void> {
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
}

/** 导出 .pptx 并触发浏览器下载 */
export async function exportPpt(id: number, title: string): Promise<void> {
  await saveBlob(await fetchBinary(`/ppt/${id}/export`, '导出失败'), `${title || '幻灯片'}.pptx`);
}

/** 下载当前 pptx（导入记录下载改写结果，从零生成记录实时导出） */
export async function downloadPptFile(id: number, title: string): Promise<void> {
  await saveBlob(await fetchBinary(`/ppt/${id}/download`, '下载失败'), `${title || '幻灯片'}.pptx`);
}

/** 上传外部 .pptx 模板，返回导入记录（含解析结构） */
export async function importPptTemplate(file: File): Promise<PptItem> {
  const formData = new FormData();
  formData.append('file', file);
  const token = typeof window !== 'undefined' ? sessionStorage.getItem('token') : null;
  const res = await fetch(`${API_BASE}/ppt/import-template`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  const json = await res.json();
  if (!res.ok || json.code !== 0) throw new Error(json.message || '导入失败');
  return json.data as PptItem;
}

/**
 * 轮询后台改写任务：onTasks 每次拿到最新列表，返回 true 表示还要继续问。
 * 返回列表由调用方判断（是否还有自己发起的任务在跑），这里只管定时与停止。
 */
export function startRewritePolling(onTasks: (tasks: RewriteTask[]) => boolean, interval = 2000): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const tick = async () => {
    if (stopped) return;
    let keep = true;
    try {
      keep = onTasks(await pptApi.listRewriteTasks());
    } catch {
      // 服务瞬时不可用不算结束：任务还在后台跑，接着问
    }
    if (stopped) return;
    if (keep) timer = setTimeout(tick, interval);
  };
  timer = setTimeout(tick, interval);
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}

/** 拉取某页真实渲染预览图，返回可显示的 objectURL；revision=original 看模板外壳，working 看改写产物 */
export async function getPptPreviewUrl(
  id: number,
  page: number,
  revision: 'original' | 'working',
): Promise<string> {
  const res = await fetchBinary(`/ppt/${id}/preview/${page}?revision=${revision}`, '预览加载失败');
  return URL.createObjectURL(await res.blob());
}
