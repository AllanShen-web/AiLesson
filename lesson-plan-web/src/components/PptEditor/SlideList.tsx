'use client';

import {
  DndContext, closestCenter, PointerSensor, useSensor, useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove, SortableContext, useSortable, verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Tooltip } from 'antd';
import { CopyOutlined, DeleteOutlined, PlusOutlined, HolderOutlined } from '@ant-design/icons';
import SlideContent, { SLIDE_W, SLIDE_H } from './SlideContent';
import type { PptSlide, PptTheme, PptStage } from '@/lib/ppt';

const THUMB_W = 176;
const THUMB_SCALE = THUMB_W / SLIDE_W;

function SortableThumb({
  slide, index, current, theme, stage, docTitle, onSelect, onDuplicate, onRemove,
}: {
  slide: PptSlide;
  index: number;
  current: number;
  theme?: PptTheme;
  stage?: PptStage;
  docTitle?: string;
  onSelect: () => void;
  onDuplicate: () => void;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: index });
  const active = index === current;

  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
        marginBottom: 12,
      }}
    >
      <div
        onClick={onSelect}
        style={{
          position: 'relative',
          width: THUMB_W,
          border: active ? '2px solid #667eea' : '2px solid #e5e7eb',
          borderRadius: 6,
          overflow: 'hidden',
          cursor: 'pointer',
          background: '#fff',
        }}
      >
        {/* 页码角标 */}
        <span style={{
          position: 'absolute', top: 2, left: 4, zIndex: 2,
          fontSize: 10, color: '#999', background: 'rgba(255,255,255,0.85)',
          borderRadius: 3, padding: '0 4px', lineHeight: '16px',
        }}>
          {index + 1}
        </span>
        {/* 缩放渲染的幻灯片 */}
        <div style={{ width: THUMB_W, height: SLIDE_H * THUMB_SCALE, position: 'relative' }}>
          <div style={{
            width: SLIDE_W, height: SLIDE_H,
            transform: `scale(${THUMB_SCALE})`, transformOrigin: 'top left',
            pointerEvents: 'none',
          }}>
            <SlideContent slide={slide} theme={theme} stage={stage} pageNo={index + 1} docTitle={docTitle} />
          </div>
        </div>
        {/* 悬停操作条 */}
        <div
          style={{
            position: 'absolute', right: 2, bottom: 2, display: 'flex', gap: 2,
            background: 'rgba(255,255,255,0.92)', borderRadius: 4, padding: 2,
          }}
        >
          <Tooltip title="复制本页">
            <CopyOutlined style={{ fontSize: 12, color: '#666', padding: 2 }} onClick={(e) => { e.stopPropagation(); onDuplicate(); }} />
          </Tooltip>
          <Tooltip title="删除本页">
            <DeleteOutlined style={{ fontSize: 12, color: '#ff4d4f', padding: 2 }} onClick={(e) => { e.stopPropagation(); onRemove(); }} />
          </Tooltip>
        </div>
      </div>
      {/* 拖拽把手 */}
      <div
        {...attributes}
        {...listeners}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          marginTop: 2, cursor: 'grab', color: '#ccc', fontSize: 12,
        }}
      >
        <HolderOutlined />
      </div>
    </div>
  );
}

interface SlideListProps {
  slides: PptSlide[];
  theme?: PptTheme;
  stage?: PptStage;
  docTitle?: string;
  current: number;
  onSelect: (index: number) => void;
  onReorder: (from: number, to: number) => void;
  onAdd: () => void;
  onDuplicate: (index: number) => void;
  onRemove: (index: number) => void;
}

/** 左侧幻灯片缩略图列表：点选、拖拽排序、悬停增删复制 */
export default function SlideList({
  slides, theme, stage, docTitle, current, onSelect, onReorder, onAdd, onDuplicate, onRemove,
}: SlideListProps) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = Number(active.id);
    const to = Number(over.id);
    if (Number.isNaN(from) || Number.isNaN(to)) return;
    onReorder(from, to);
    if (current === from) onSelect(to);
    else if (current > from && current <= to) onSelect(current - 1);
    else if (current < from && current >= to) onSelect(current + 1);
  };

  return (
    <div style={{ width: THUMB_W + 16, flexShrink: 0, height: '100%', overflowY: 'auto', padding: '12px 8px' }}>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={slides.map((_, i) => i)} strategy={verticalListSortingStrategy}>
          {slides.map((slide, i) => (
            <SortableThumb
              key={i}
              slide={slide}
              index={i}
              current={current}
              theme={theme}
              stage={stage}
              docTitle={docTitle}
              onSelect={() => onSelect(i)}
              onDuplicate={() => onDuplicate(i)}
              onRemove={() => onRemove(i)}
            />
          ))}
        </SortableContext>
      </DndContext>
      <div
        onClick={onAdd}
        style={{
          width: THUMB_W, height: 40, border: '1px dashed #d9d9d9', borderRadius: 6,
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
          color: '#666', cursor: 'pointer', fontSize: 13,
        }}
      >
        <PlusOutlined /> 新增页面
      </div>
    </div>
  );
}

/** 供外部（工具栏）引用的重排工具：数组移动 */
export function moveSlide(slides: PptSlide[], from: number, to: number): PptSlide[] {
  if (from === to || from < 0 || to < 0 || from >= slides.length || to >= slides.length) return slides;
  return arrayMove(slides, from, to);
}
