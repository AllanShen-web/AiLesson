'use client';

import { useEffect, useState } from 'react';
import { Button } from 'antd';
import { LeftOutlined, RightOutlined, CloseOutlined } from '@ant-design/icons';
import SlideContent, { SLIDE_W, SLIDE_H } from './SlideContent';
import type { PptSlide, PptTheme, PptStage } from '@/lib/ppt';

interface PptPresentProps {
  slides: PptSlide[];
  theme?: PptTheme;
  stage?: PptStage;
  docTitle?: string;
  startIndex?: number;
  onClose: () => void;
}

/** 全屏演示模式：键盘 ←/→ 翻页，Esc 退出 */
export default function PptPresent({ slides, theme, stage, docTitle, startIndex = 0, onClose }: PptPresentProps) {
  const [current, setCurrent] = useState(startIndex);
  const [scale, setScale] = useState(1);

  // 按视口尺寸计算 16:9 画布的最大缩放
  useEffect(() => {
    const calc = () => {
      const s = Math.min(window.innerWidth / SLIDE_W, window.innerHeight / SLIDE_H) * 0.94;
      setScale(s);
    };
    calc();
    window.addEventListener('resize', calc);
    return () => window.removeEventListener('resize', calc);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowRight' || e.key === ' ') setCurrent((c) => Math.min(c + 1, slides.length - 1));
      else if (e.key === 'ArrowLeft') setCurrent((c) => Math.max(c - 1, 0));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [slides.length, onClose]);

  if (slides.length === 0) return null;
  const slide = slides[Math.min(current, slides.length - 1)];

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 2000, background: '#000',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        width: SLIDE_W, height: SLIDE_H,
        transform: `scale(${scale})`, transformOrigin: 'center',
        boxShadow: '0 8px 40px rgba(0,0,0,0.5)', position: 'relative',
      }}>
        <SlideContent slide={slide} theme={theme} stage={stage} pageNo={current + 1} docTitle={docTitle} />
      </div>

      {/* 控制条 */}
      <div style={{
        position: 'absolute', bottom: 16, left: '50%', transform: 'translateX(-50%)',
        display: 'flex', alignItems: 'center', gap: 12,
        background: 'rgba(255,255,255,0.12)', borderRadius: 24, padding: '6px 16px',
      }}>
        <Button
          type="text"
          icon={<LeftOutlined />}
          disabled={current === 0}
          onClick={() => setCurrent((c) => Math.max(c - 1, 0))}
          style={{ color: '#fff' }}
        />
        <span style={{ color: '#fff', fontSize: 13 }}>{current + 1} / {slides.length}</span>
        <Button
          type="text"
          icon={<RightOutlined />}
          disabled={current >= slides.length - 1}
          onClick={() => setCurrent((c) => Math.min(c + 1, slides.length - 1))}
          style={{ color: '#fff' }}
        />
      </div>
      <Button
        type="text"
        icon={<CloseOutlined />}
        onClick={onClose}
        style={{ position: 'absolute', top: 16, right: 16, color: '#fff' }}
      />
    </div>
  );
}
