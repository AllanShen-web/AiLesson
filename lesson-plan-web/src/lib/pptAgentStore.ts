// PPT 编辑器页面向 AI 助手注册当前编辑的 PPT 编号。
// ChatWidget 发送消息时读取并随请求带给后端，让助手知道用户正在编辑哪份 PPT
// （仿 formAgentStore 的 lessonPlanId：模块级单例，发送时读取，无需响应式）。

let _pptId: number | null = null;

export const pptAgentStore = {
  get pptId() {
    return _pptId;
  },

  setPptId(id: number | null) {
    _pptId = id;
  },
};
