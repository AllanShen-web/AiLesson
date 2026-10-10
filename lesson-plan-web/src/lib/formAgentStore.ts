import type { FormInstance } from 'antd';
import type { FormField } from '@/lib/api';

/**
 * 交给助手的可填字段：模板字段 + 课时分组名（group）。
 * group 只在上下文里存活、不落库：两课时模板的字段名成对重复，而注册给助手的清单滤掉了
 * 「第X课时」标签和分隔符，模型看不到边界就会把第二课时当成第一课时的重复栏。
 * 分组计算与服务端 registry.ts 的 assignFieldGroups 同规则，由新增页算好附带。
 */
export type AgentFillableField = FormField & { group?: string };

// Module-level singleton - shared across all components
let _selectedFieldId: string | null = null;
let _availableFields: AgentFillableField[] = [];
let _formInstance: FormInstance | null = null;
let _formContext: Record<string, string> = {};
let _lessonPlanId: number | null = null;
let _isGenerating = false;
let _currentValues: Record<string, string> = {};
// Agent 跳转新增页时携带的基础信息预填：跨页存活，由新增页挂载后消费一次
let _pendingBasic: { topic?: string; subject?: string; grade?: string } | null = null;
let _listeners: Array<() => void> = [];

function notify() {
  _listeners.forEach((fn) => fn());
}

export const formAgentStore = {
  get selectedFieldId() { return _selectedFieldId; },
  get availableFields() { return _availableFields; },
  get agentMode() { return _availableFields.length > 0; },
  get formContext() { return _formContext; },
  get lessonPlanId() { return _lessonPlanId; },
  get isGenerating() { return _isGenerating; },
  get currentValues() { return _currentValues; },

  getSelectedFieldLabel(): string | null {
    const f = _availableFields.find((f) => f.id === _selectedFieldId);
    return f?.label || null;
  },

  getSelectedFieldType(): string | null {
    const f = _availableFields.find((f) => f.id === _selectedFieldId);
    return f?.type || null;
  },

  getSelectedFieldHasContent(): boolean {
    if (!_selectedFieldId || !_formInstance) return false;
    try {
      const val = _formInstance.getFieldValue(_selectedFieldId);
      return val !== undefined && val !== null && val !== '';
    } catch { return false; }
  },

  selectField(fieldId: string | null) {
    console.log('[Agent] selectField:', fieldId);
    _selectedFieldId = fieldId;
    notify();
  },

  registerFields(fields: AgentFillableField[]) {
    console.log('[Agent] registerFields:', fields.length);
    _availableFields = fields;
    notify();
  },

  setFormContext(ctx: Record<string, string>) {
    _formContext = ctx;
  },

  setLessonPlanId(id: number | null) {
    _lessonPlanId = id;
  },

  registerForm(form: FormInstance) {
    console.log('[Agent] registerForm called, form exists:', !!form);
    _formInstance = form;
  },

  fillFormField(fieldId: string, value: any) {
    console.log('[Agent] fillFormField:', { fieldId, valueLen: value?.length });
    // Dispatch custom event - create page listens and sets form value directly
    if (typeof window !== 'undefined') {
      window.dispatchEvent(
        new CustomEvent('agent:fillField', { detail: { fieldId, value } }),
      );
    }
  },

  setGenerating(val: boolean) {
    _isGenerating = val;
    notify();
  },

  setCurrentValues(values: Record<string, string>) {
    _currentValues = { ...values };
  },

  setPendingBasic(basic: { topic?: string; subject?: string; grade?: string }) {
    _pendingBasic = basic;
  },

  /** 取出并清空预填基础信息：只消费一次，避免下次手动进新增页被陈旧数据污染 */
  consumePendingBasic() {
    const b = _pendingBasic;
    _pendingBasic = null;
    return b;
  },

  clearFields() {
    _availableFields = [];
    _selectedFieldId = null;
    _formInstance = null;
    _formContext = {};
    _lessonPlanId = null;
    _isGenerating = false;
    _currentValues = {};
    _pendingBasic = null;
    notify();
  },

  subscribe(fn: () => void) {
    _listeners.push(fn);
    return () => { _listeners = _listeners.filter((l) => l !== fn); };
  },
};

export function getIsGenerating() {
  return _isGenerating;
}
