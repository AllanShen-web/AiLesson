/** 只列代码里真正跳转用的路径；侧边栏那几个页面由数据库 menus 表下发，不走常量 */
export const ROUTES = {
  LOGIN: '/login',
  /** 登录后的落地页 */
  LESSON_PLANS: '/lesson-plans',
  LESSON_PLAN_CREATE: '/lesson-plans/create',
  PPT: '/ppt',
  PPT_EDITOR: '/ppt/editor',
  PROFILE: '/profile',
  TEMPLATE_MANAGEMENT: '/template-management',
  TEMPLATE_CREATE: '/template-management/create',
  BLESS: '/bless',
} as const;

export const PUBLIC_ROUTES = [ROUTES.LOGIN, ROUTES.BLESS];

/**
 * Word 导入的模板草稿在 sessionStorage 里的键：
 * 模板列表页识别完写入，编辑器读它渲染草稿，保存成功即删——确认前不落库。
 */
export const TEMPLATE_IMPORT_DRAFT_KEY = 'template-import-draft';
