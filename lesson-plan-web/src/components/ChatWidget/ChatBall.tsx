'use client';

import React, { useState, useCallback, useRef } from 'react';
import AiAvatar from './AiAvatar';

interface ChatBallProps {
  onClick: () => void;
  agentMode?: boolean;
  /** 聊天窗口打开时收起入口（保留挂载，拖动位置不丢） */
  hidden?: boolean;
}

/** 悬浮球直径 */
const BALL = 52;
/** 吸附后距屏幕边框的间隙 */
const EDGE_GAP = 10;
/** 松手时距边框多少像素内才吸附 */
const SNAP_RANGE = 90;

/** agent 运行中：火山橙（红色太刺眼，橙色既表意"在干活"又压得住头像的棕色） */
const ACTIVE = '#fa8c16';

const clamp = (v: number, min: number, max: number) => Math.min(Math.max(v, min), max);

export default function ChatBall({ onClick, agentMode, hidden }: ChatBallProps) {
  const [pos, setPos] = useState({ bottom: 24, right: 24 });
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{
    startX: number;
    startY: number;
    startBottom: number;
    startRight: number;
    moved: boolean;
    mouseDown: boolean;
  } | null>(null);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        startBottom: pos.bottom,
        startRight: pos.right,
        moved: false,
        mouseDown: true,
      };
      setDragging(true);

      const handleMove = (ev: MouseEvent) => {
        if (!dragRef.current?.mouseDown) return;
        const dx = ev.clientX - dragRef.current.startX;
        const dy = ev.clientY - dragRef.current.startY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
          dragRef.current.moved = true;
        }
        if (dragRef.current.moved) {
          setPos({
            bottom: clamp(dragRef.current.startBottom - dy, 0, window.innerHeight - BALL),
            right: clamp(dragRef.current.startRight - dx, 0, window.innerWidth - BALL),
          });
        }
      };

      const handleUp = () => {
        const wasDrag = dragRef.current?.moved;
        dragRef.current = null;
        setDragging(false);
        document.removeEventListener('mousemove', handleMove);
        document.removeEventListener('mouseup', handleUp);

        if (wasDrag) {
          // 松手后吸到最近的一条竖边：贴着边框停，不半浮在正文上
          setPos((p) => {
            const toRight = p.right;
            const toLeft = window.innerWidth - BALL - p.right;
            const right =
              toRight <= SNAP_RANGE ? EDGE_GAP
                : toLeft <= SNAP_RANGE ? window.innerWidth - BALL - EDGE_GAP
                : p.right;
            return {
              right,
              bottom: clamp(p.bottom, EDGE_GAP, window.innerHeight - BALL - EDGE_GAP),
            };
          });
        } else {
          onClick();
        }
      };

      document.addEventListener('mousemove', handleMove);
      document.addEventListener('mouseup', handleUp);
    },
    [pos, onClick],
  );

  const ring = agentMode ? ACTIVE : '#fff';
  const glow = agentMode ? `0 4px 18px rgba(250, 140, 22, 0.38)` : `0 4px 16px rgba(0, 0, 0, 0.14)`;

  return (
    <div
      onMouseDown={handleMouseDown}
      style={{
        position: 'fixed',
        display: hidden ? 'none' : undefined,
        bottom: pos.bottom,
        right: pos.right,
        width: BALL,
        height: BALL,
        borderRadius: '50%',
        cursor: 'pointer',
        boxShadow: glow,
        // 拖动时跟手，松手吸附时才走动画
        transition: dragging ? 'none' : 'bottom 0.22s ease-out, right 0.22s ease-out',
        zIndex: 9999,
        userSelect: 'none',
        overflow: 'visible',
      }}
      onMouseEnter={(e) => {
        if (!dragRef.current?.mouseDown) (e.currentTarget as HTMLDivElement).style.transform = 'scale(1.1)';
      }}
      onMouseLeave={(e) => {
        if (!dragRef.current?.mouseDown) (e.currentTarget as HTMLDivElement).style.transform = 'scale(1)';
      }}
    >
      <div style={{ width: BALL, height: BALL, borderRadius: '50%', overflow: 'hidden', position: 'relative', zIndex: 1 }}>
        <AiAvatar size={BALL} fontSize={20} style={{ border: `3px solid ${ring}` }} />
      </div>
      {/* 扩散圆圈特效 - 只在 agent 运行中提示，默认态保持安静 */}
      {agentMode && (
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: BALL,
            height: BALL,
            borderRadius: '50%',
            border: '2px solid rgba(250, 140, 22, 0.55)',
            animation: 'chatPulse 2s infinite',
            pointerEvents: 'none',
          }}
        />
      )}
      <style>{`@keyframes chatPulse { 0% { transform: scale(1); opacity: 1; } 100% { transform: scale(1.5); opacity: 0; } }`}</style>
    </div>
  );
}
