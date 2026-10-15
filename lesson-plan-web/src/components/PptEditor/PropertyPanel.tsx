'use client';

import { useMemo } from 'react';
import { Select, Input, Space, Typography, Divider, ColorPicker } from 'antd';
import {
  ArrowUpOutlined, ArrowDownOutlined, CopyOutlined, DeleteOutlined, PlayCircleOutlined,
} from '@ant-design/icons';
import { LAYOUT_LABELS, PPT_THEME_PRESETS, type PptSlide, type PptSlideLayout, type PptTheme } from '@/lib/ppt';

const { Text } = Typography;
const { TextArea } = Input;

const FONTS = ['微软雅黑', '宋体', '黑体', '楷体', 'Arial'];

/** 主题色值统一转为 #RRGGBB 供 ColorPicker 使用 */
function toHash(v: string | undefined, fallback: string): string {
  return v && /^[0-9A-Fa-f]{6}$/.test(v) ? `#${v.toUpperCase()}` : fallback;
}

/** ColorPicker 回调值转回不带 # 的大写十六进制 */
function fromHash(hex: string): string {
  return hex.replace('#', '').toUpperCase();
}

interface PropertyPanelProps {
  slide: PptSlide;
  theme: PptTheme;
  index: number;
  total: number;
  onSlideChange: (patch: Partial<PptSlide>) => void;
  onThemeChange: (patch: Partial<PptTheme>) => void;
  onMove: (direction: -1 | 1) => void;
  onDuplicate: () => void;
  onRemove: () => void;
  onPresent: () => void;
}

/** 右侧属性面板：版式 / 主题 / 备注 / 页面操作 */
export default function PropertyPanel({
  slide, theme, index, total, onSlideChange, onThemeChange, onMove, onDuplicate, onRemove, onPresent,
}: PropertyPanelProps) {
  const layoutOptions = useMemo(
    () => (Object.keys(LAYOUT_LABELS) as PptSlideLayout[]).map((k) => ({ value: k, label: LAYOUT_LABELS[k] })),
    [],
  );

  return (
    <div style={{ width: 260, flexShrink: 0, height: '100%', overflowY: 'auto', padding: 16, borderLeft: '1px solid #f0f0f0' }}>
      <Text strong>页面版式</Text>
      <Select
        value={slide.layout}
        options={layoutOptions}
        onChange={(v) => onSlideChange({ layout: v })}
        style={{ width: '100%', marginTop: 8 }}
      />

      <Divider style={{ margin: '16px 0' }} />

      <Text strong>主题配色</Text>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
        {PPT_THEME_PRESETS.map((p) => (
          <div
            key={p.key}
            title={p.name}
            onClick={() => onThemeChange(p.theme)}
            style={{
              width: 28, height: 28, borderRadius: 6, cursor: 'pointer',
              background: `#${p.theme.primary}`,
              border: theme.primary.toUpperCase() === p.theme.primary.toUpperCase() ? '2px solid #333' : '2px solid transparent',
            }}
          />
        ))}
      </div>
      <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>主色</Text>
          <ColorPicker
            value={toHash(theme.primary, '#2563EB')}
            onChange={(c) => onThemeChange({ primary: fromHash(c.toHexString()) })}
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>辅助色</Text>
          <ColorPicker
            value={toHash(theme.accent, '#93C5FD')}
            onChange={(c) => onThemeChange({ accent: fromHash(c.toHexString()) })}
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>背景</Text>
          <ColorPicker
            value={toHash(theme.bg, '#FFFFFF')}
            onChange={(c) => onThemeChange({ bg: fromHash(c.toHexString()) })}
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>标题字色</Text>
          <ColorPicker
            value={toHash(theme.titleColor, '#1E3A8A')}
            onChange={(c) => onThemeChange({ titleColor: fromHash(c.toHexString()) })}
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>正文字色</Text>
          <ColorPicker
            value={toHash(theme.textColor, '#334155')}
            onChange={(c) => onThemeChange({ textColor: fromHash(c.toHexString()) })}
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>字体</Text>
          <Select
            value={theme.font}
            options={FONTS.map((f) => ({ value: f, label: f }))}
            onChange={(v) => onThemeChange({ font: v })}
            style={{ width: 130 }}
            size="small"
          />
        </div>
      </div>

      <Divider style={{ margin: '16px 0' }} />

      <Text strong>演讲者备注</Text>
      <TextArea
        value={slide.notes || ''}
        onChange={(e) => onSlideChange({ notes: e.target.value })}
        placeholder="备注仅供演讲者查看，不影响页面展示"
        rows={3}
        style={{ marginTop: 8 }}
      />

      <Divider style={{ margin: '16px 0' }} />

      <Text strong>页面操作</Text>
      <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <Space size={4} wrap>
          <button
            type="button"
            disabled={index === 0}
            onClick={() => onMove(-1)}
            title="上移"
            style={btnStyle}
          >
            <ArrowUpOutlined />
          </button>
          <button
            type="button"
            disabled={index === total - 1}
            onClick={() => onMove(1)}
            title="下移"
            style={btnStyle}
          >
            <ArrowDownOutlined />
          </button>
          <button type="button" onClick={onDuplicate} title="复制本页" style={btnStyle}>
            <CopyOutlined />
          </button>
          <button type="button" onClick={onPresent} title="演示" style={btnStyle}>
            <PlayCircleOutlined />
          </button>
          <button type="button" onClick={onRemove} title="删除本页" style={{ ...btnStyle, color: '#ff4d4f' }}>
            <DeleteOutlined />
          </button>
        </Space>
      </div>
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  border: '1px solid #d9d9d9',
  background: '#fff',
  borderRadius: 6,
  padding: '4px 10px',
  cursor: 'pointer',
  fontSize: 13,
  color: '#666',
};
