'use client';

import { Input } from 'antd';
import { PlusOutlined, CloseOutlined } from '@ant-design/icons';
import type {
  PptSlide,
  PptTheme,
  PptStage,
  PptChart,
  PptBigNumber,
  PptTimelineStep,
  PptCompare,
  PptActivity,
} from '@/lib/ppt';
import { DEFAULT_PPT_THEME } from '@/lib/ppt';

/** 设计稿尺寸（16:9），实际展示时按 scale 缩放；1 英寸 = 96px，与后端 ppt-from-doc 对齐 */
export const SLIDE_W = 960;
export const SLIDE_H = 540;
const MARGIN_X = 58;
const CONTENT_W = SLIDE_W - MARGIN_X * 2;

// ── 颜色工具（与后端 ppt-from-doc.ts 保持一致） ──

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function mix(a: string, b: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
  return `#${c(r1 + (r2 - r1) * t)}${c(g1 + (g2 - g1) * t)}${c(b1 + (b2 - b1) * t)}`.toUpperCase();
}

function tint(hex: string, t = 0.9): string {
  return mix(hex, 'FFFFFF', t);
}

type ResolvedTheme = Required<PptTheme>;

function resolveTheme(theme?: Partial<PptTheme> | null): ResolvedTheme {
  const t = { ...DEFAULT_PPT_THEME, ...(theme || {}) };
  const valid = (v: string | undefined, f: string) =>
    v && /^[0-9A-Fa-f]{6}$/.test(v) ? `#${v.toUpperCase()}` : f;
  const primary = valid(t.primary, `#${DEFAULT_PPT_THEME.primary}`);
  const validStyles: PptTheme['style'][] = ['plain', 'band', 'card'];
  return {
    primary,
    bg: valid(t.bg, `#${DEFAULT_PPT_THEME.bg}`),
    titleColor: valid(t.titleColor, `#${DEFAULT_PPT_THEME.titleColor}`),
    textColor: valid(t.textColor, `#${DEFAULT_PPT_THEME.textColor}`),
    font: t.font || DEFAULT_PPT_THEME.font,
    accent: valid(t.accent, mix(primary, '#FFFFFF', 0.55)),
    style: validStyles.includes(t.style) ? (t.style as Required<PptTheme>['style']) : 'band',
  };
}

// ── 文本测量（与后端估算口径一致：中文 1 字宽、英文数字 0.55） ──

function textW(text: string, fontPx: number): number {
  let units = 0;
  for (const ch of text) {
    units += ch.charCodeAt(0) > 0x2e80 ? 1 : 0.55;
  }
  return units * fontPx;
}

function estLines(text: string, fontPx: number, widthPx: number): number {
  return Math.max(1, Math.ceil(Math.max(textW(text, fontPx), 0.01) / Math.max(widthPx, 10)));
}

/** 标题字号档位（px），超宽自动降档 */
function fitTitleSize(title: string): number {
  for (const size of [35, 32, 29, 27, 24]) {
    if (textW(title, size) <= CONTENT_W - 115) return size;
  }
  return 24;
}

/** 学段设计参数（px，1in=96，与后端 getStageDesign 对应） */
interface StageDesign {
  radius: number;
  chipRadius: number;
  deco: number;
  bodySize: number;
  dense: boolean;
}
function getStageDesign(stage?: PptStage): StageDesign {
  if (stage === 'primary') return { radius: 15, chipRadius: 23, deco: 1, bodySize: 16, dense: false };
  if (stage === 'college') return { radius: 6, chipRadius: 8, deco: 0.4, bodySize: 14, dense: true };
  return { radius: 10, chipRadius: 13, deco: 0.7, bodySize: 15, dense: false };
}

/** 装饰圆：右上 / 左下 */
function DecoCircles({ theme, onDark }: { theme: ResolvedTheme; onDark: boolean }) {
  return (
    <>
      <div style={{
        position: 'absolute', left: 797, top: -106, width: 250, height: 250,
        borderRadius: '50%', background: theme.accent, opacity: onDark ? 0.45 : 0.3,
      }} />
      <div style={{
        position: 'absolute', left: -86, top: 425, width: 192, height: 192,
        borderRadius: '50%', background: theme.accent, opacity: onDark ? 0.3 : 0.18,
      }} />
    </>
  );
}

/** 编号小圆 */
function NumberChip({ n, theme, size = 33 }: { n: number; theme: ResolvedTheme; size?: number }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%', background: theme.primary,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      color: '#fff', fontSize: size > 36 ? 17 : 15, fontWeight: 700, flexShrink: 0,
      fontFamily: theme.font,
    }}>
      {n}
    </div>
  );
}

/** 图表示意渲染：bar 柱状 / line 折线(SVG) / pie 饼图(conic-gradient)，数据驱动、无图片依赖 */
function ChartFigure({ chart, t, width, height }: { chart: PptChart; t: ResolvedTheme; width: number; height: number }) {
  const colors = [t.primary, t.accent, mix(t.primary, '#000000', 0.25), tint(t.primary, 0.5)];
  const showLegend = chart.series.length > 1 && chart.type !== 'pie';
  const legendH = showLegend ? 20 : 0;
  const figH = height - legendH;

  const legend = showLegend ? (
    <div style={{ display: 'flex', gap: 14, height: legendH, alignItems: 'center', paddingLeft: 34, fontSize: 11, color: t.textColor, fontFamily: t.font }}>
      {chart.series.map((s, si) => (
        <span key={si} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 10, height: 10, background: colors[si % colors.length], borderRadius: 2 }} />
          {s.name}
        </span>
      ))}
    </div>
  ) : null;

  let body: React.ReactNode = null;

  if (chart.type === 'pie') {
    const vals = chart.series[0]?.data ?? [];
    const total = vals.reduce((a, b) => a + b, 0) || 1;
    let acc = 0;
    const segs = vals.map((v, i) => {
      const start = (acc / total) * 100;
      acc += v;
      const end = (acc / total) * 100;
      return `${colors[i % colors.length]} ${start}% ${end}%`;
    });
    const d = Math.min(figH - 10, width * 0.44);
    body = (
      <div style={{ display: 'flex', alignItems: 'center', gap: 18, width, height: figH, fontFamily: t.font }}>
        <div style={{ width: d, height: d, borderRadius: '50%', background: `conic-gradient(${segs.join(',')})` }} />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 7, fontSize: 13, color: t.textColor }}>
          {chart.categories.map((c, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, background: colors[i % colors.length] }} />
              <span style={{ flex: 1 }}>{c}</span>
              <span style={{ fontWeight: 600 }}>{vals[i]}</span>
            </div>
          ))}
        </div>
      </div>
    );
  } else if (chart.type === 'bar') {
    const allVals = chart.series.flatMap((s) => s.data);
    const max = Math.max(...allVals, 1);
    const labelH = 22;
    const plotH = figH - labelH;
    body = (
      <div style={{ width, height: figH, fontFamily: t.font }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', width, height: plotH, gap: 8, paddingLeft: 8 }}>
          {chart.categories.map((c, ci) => (
            <div key={ci} style={{ flex: 1, display: 'flex', alignItems: 'flex-end', justifyContent: 'center', height: '100%', gap: 3 }}>
              {chart.series.map((s, si) => (
                <div
                  key={si}
                  title={`${s.name}:${s.data[ci]}`}
                  style={{
                    width: Math.max(7, 36 / chart.series.length),
                    height: (s.data[ci] / max) * (plotH - 8),
                    background: colors[si % colors.length],
                    borderRadius: '3px 3px 0 0',
                  }}
                />
              ))}
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, paddingLeft: 8, height: labelH }}>
          {chart.categories.map((c, ci) => (
            <div key={ci} style={{ flex: 1, textAlign: 'center', fontSize: 11, color: t.textColor, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>
              {c}
            </div>
          ))}
        </div>
      </div>
    );
  } else {
    const allVals = chart.series.flatMap((s) => s.data);
    const max = Math.max(...allVals, 1);
    const min = Math.min(...allVals, 0);
    const W = width;
    const H = figH;
    const padL = 36, padB = 22, padT = 10, padR = 10;
    const innerW = W - padL - padR;
    const innerH = H - padT - padB;
    const xPos = (i: number) => padL + (chart.categories.length <= 1 ? innerW / 2 : (i * innerW) / (chart.categories.length - 1));
    const yPos = (v: number) => padT + innerH - ((v - min) / (max - min || 1)) * innerH;
    body = (
      <svg width={W} height={H} fontFamily={t.font}>
        {[0, 0.25, 0.5, 0.75, 1].map((g, i) => {
          const y = padT + innerH - g * innerH;
          const val = (min + g * (max - min)).toFixed(max < 10 ? 1 : 0);
          return (
            <g key={i}>
              <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="#E5E7EB" />
              <text x={padL - 6} y={y + 4} fontSize={10} fill={t.textColor} textAnchor="end">{val}</text>
            </g>
          );
        })}
        {chart.series.map((s, si) => {
          const pts = s.data.map((v, i) => `${xPos(i)},${yPos(v)}`).join(' ');
          return (
            <g key={si}>
              <polyline points={pts} fill="none" stroke={colors[si % colors.length]} strokeWidth={2.5} />
              {s.data.map((v, i) => (
                <circle key={i} cx={xPos(i)} cy={yPos(v)} r={3.5} fill="#FFFFFF" stroke={colors[si % colors.length]} strokeWidth={2} />
              ))}
            </g>
          );
        })}
        {chart.categories.map((c, i) => (
          <text key={i} x={xPos(i)} y={H - 6} fontSize={10} fill={t.textColor} textAnchor="middle">{c}</text>
        ))}
      </svg>
    );
  }

  return (
    <div style={{ width, height, display: 'flex', flexDirection: 'column' }}>
      {legend}
      {body}
    </div>
  );
}

interface SlideContentProps {
  slide: PptSlide;
  theme?: Partial<PptTheme> | null;
  /** 编辑模式：文本变为可编辑输入框，要点行显示增删按钮 */
  editing?: boolean;
  /** 编辑回调：整体替换（标题/要点/备注） */
  onChange?: (patch: Partial<PptSlide>) => void;
  /** 演示/缩略图模式下由外部提供的页码与文档标题（页脚用） */
  pageNo?: number;
  docTitle?: string;
  /** 学段（来自 PptDoc.stage），驱动圆角/装饰/字号密度 */
  stage?: PptStage;
}

/**
 * 单页幻灯片渲染器：编辑器画布、缩略图、演示模式共用。
 * 版式与配色与后端 ppt-from-doc.ts 逐像素对齐（1in = 96px）。
 */
export default function SlideContent({ slide, theme, editing = false, onChange, pageNo, docTitle, stage }: SlideContentProps) {
  const t = resolveTheme(theme);
  const onDark = t.style !== 'plain';
  const design = getStageDesign(stage);
  const { TextArea } = Input;

  // ── 可编辑标题 ──
  const titleInput = (fontSize: number, align: 'left' | 'center', color: string, top: number, height: number) =>
    editing ? (
      <Input
        variant="borderless"
        value={slide.title}
        placeholder="请输入标题"
        onChange={(e) => onChange?.({ title: e.target.value })}
        style={{
          position: 'absolute', left: MARGIN_X, right: MARGIN_X, top, height,
          fontSize, fontWeight: 700, color, textAlign: align, padding: 0, fontFamily: t.font,
        }}
      />
    ) : (
      <div style={{
        position: 'absolute', left: MARGIN_X, right: MARGIN_X, top, height,
        fontSize, fontWeight: 700, color, textAlign: align, fontFamily: t.font,
        lineHeight: `${height}px`, overflow: 'hidden',
      }}>
        {slide.title}
      </div>
    );

  // ── 要点卡片列表（band 浅底 / card 白底描边） ──
  const bulletCards = (items: string[], top: number, left: number, width: number, availH: number, startIndex = 1) => {
    if (items.length === 0 && !editing) return null;
    const textW2 = width - 50 - 19;
    const gap = 13;
    const sizes = [20, 19, 17, 16, 15, 13];
    let layout: { h: number; size: number }[] = [];
    for (const size of sizes) {
      const lineH = size * 1.45;
      layout = items.map((b) => ({ h: Math.max(50, estLines(b, size, textW2) * lineH + 23), size }));
      const total = layout.reduce((a, c) => a + c.h, 0) + gap * Math.max(items.length - 1, 0);
      if (total <= availH) break;
    }
    return (
      <div style={{ position: 'absolute', top, left, width, display: 'flex', flexDirection: 'column', gap }}>
        {items.map((b, i) => (
          <div key={i} style={{
            minHeight: layout[i]?.h ?? 50,
            borderRadius: 9,
            background: t.style === 'card' ? '#FFFFFF' : tint(t.primary, 0.9),
            border: t.style === 'card' ? `1px solid ${tint(t.primary, 0.6)}` : 'none',
            boxShadow: t.style === 'card' ? '0 2px 6px rgba(0,0,0,0.10)' : 'none',
            display: 'flex', alignItems: 'center', gap: 12, padding: '6px 14px 6px 12px',
          }}>
            <NumberChip n={startIndex + i} theme={t} />
            {editing ? (
              <Input
                variant="borderless"
                value={b}
                placeholder="要点内容"
                onChange={(e) => {
                  const bullets = [...slide.bullets];
                  bullets[i] = e.target.value;
                  onChange?.({ bullets });
                }}
                style={{ flex: 1, fontSize: layout[i]?.size ?? 16, color: t.textColor, padding: 0, fontFamily: t.font }}
              />
            ) : (
              <span style={{ fontSize: layout[i]?.size ?? 16, color: t.textColor, lineHeight: 1.4, wordBreak: 'break-word', fontFamily: t.font }}>{b}</span>
            )}
            {editing && (
              <CloseOutlined
                style={{ color: '#bbb', fontSize: 12, flexShrink: 0, cursor: 'pointer' }}
                onClick={() => onChange?.({ bullets: slide.bullets.filter((_, idx) => idx !== i) })}
              />
            )}
          </div>
        ))}
        {editing && (
          <div
            style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#999', fontSize: 14, cursor: 'pointer', padding: '4px 2px' }}
            onClick={() => onChange?.({ bullets: [...slide.bullets, ''] })}
          >
            <PlusOutlined /> 添加要点
          </div>
        )}
      </div>
    );
  };

  // ── 内容页页脚 ──
  const footer = (index: number) => (
    <>
      <div style={{ position: 'absolute', left: MARGIN_X, top: 500, width: CONTENT_W, height: 1, background: tint(t.primary, 0.75) }} />
      <div style={{ position: 'absolute', left: MARGIN_X, top: 506, fontSize: 12, color: tint(t.primary, 0.45), fontFamily: t.font }}>
        {docTitle || ''}
      </div>
      <div style={{ position: 'absolute', right: MARGIN_X, top: 504, width: 40, textAlign: 'right', fontSize: 13, fontWeight: 700, color: t.primary, fontFamily: t.font }}>
        {index}
      </div>
    </>
  );

  // ── 富版式通用页眉（标题/短杠/水印/页脚/角装饰） ──
  const contentHeader = () => (
    <>
      {titleInput(fitTitleSize(slide.title), 'left', t.titleColor, 38, 60)}
      <div style={{ position: 'absolute', left: MARGIN_X, top: 107, width: 67, height: 6, background: t.primary }} />
      <div style={{ position: 'absolute', right: MARGIN_X, top: 44, fontSize: 12, color: tint(t.primary, 0.55), fontFamily: t.font }}>智慧教案</div>
      {footer(pageNo ?? 1)}
      {design.deco > 0.5 && (
        <div style={{ position: 'absolute', left: SLIDE_W - 115, top: -77, width: 173, height: 173, borderRadius: '50%', background: t.accent, opacity: 0.16 }} />
      )}
    </>
  );

  // ── 浅色块 + 圆点要点（可编辑增删），用于大数字补充/图表解读/对比两侧 ──
  const dotList = (
    items: string[],
    top: number,
    left: number,
    width: number,
    height: number,
    onMutate: (bullets: string[]) => void,
  ) => (
    <div style={{
      position: 'absolute', top, left, width, height,
      borderRadius: design.radius, background: tint(t.primary, 0.93),
      padding: '13px 16px', overflow: 'hidden', fontFamily: t.font,
    }}>
      {items.map((b, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 9 }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: t.primary, marginTop: Math.max(3, (design.bodySize - 7) / 2), flexShrink: 0 }} />
          {editing ? (
            <Input
              variant="borderless"
              value={b}
              placeholder="要点"
              onChange={(e) => { const nb = [...items]; nb[i] = e.target.value; onMutate(nb); }}
              style={{ flex: 1, fontSize: design.bodySize, color: t.textColor, padding: 0 }}
            />
          ) : (
            <span style={{ flex: 1, fontSize: design.bodySize, color: t.textColor, lineHeight: 1.4, wordBreak: 'break-word' }}>{b}</span>
          )}
          {editing && (
            <CloseOutlined
              style={{ color: '#bbb', fontSize: 11, marginTop: 4 }}
              onClick={() => onMutate(items.filter((_, idx) => idx !== i))}
            />
          )}
        </div>
      ))}
      {editing && (
        <div
          style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#999', fontSize: 13, cursor: 'pointer' }}
          onClick={() => onMutate([...items, ''])}
        >
          <PlusOutlined /> 添加
        </div>
      )}
    </div>
  );

  // ── 圆点行（无背景块），供浅色块内复用（图表解读/对比） ──
  const innerDotRows = (items: string[], onMutate: (bullets: string[]) => void) => (
    <>
      {items.map((b, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 9 }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: t.primary, marginTop: Math.max(3, (design.bodySize - 7) / 2), flexShrink: 0 }} />
          {editing ? (
            <Input
              variant="borderless"
              value={b}
              placeholder="要点"
              onChange={(e) => { const nb = [...items]; nb[i] = e.target.value; onMutate(nb); }}
              style={{ flex: 1, fontSize: design.bodySize, color: t.textColor, padding: 0 }}
            />
          ) : (
            <span style={{ flex: 1, fontSize: design.bodySize, color: t.textColor, lineHeight: 1.4, wordBreak: 'break-word' }}>{b}</span>
          )}
          {editing && (
            <CloseOutlined
              style={{ color: '#bbb', fontSize: 11, marginTop: 4 }}
              onClick={() => onMutate(items.filter((_, idx) => idx !== i))}
            />
          )}
        </div>
      ))}
      {editing && (
        <div
          style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#999', fontSize: 13, cursor: 'pointer' }}
          onClick={() => onMutate([...items, ''])}
        >
          <PlusOutlined /> 添加
        </div>
      )}
    </>
  );

  switch (slide.layout) {
    // ── 封面 ──
    case 'cover':
      return (
        <div style={{ position: 'absolute', inset: 0, background: onDark ? t.primary : t.bg, overflow: 'hidden', fontFamily: t.font }}>
          {onDark ? (
            <>
              <DecoCircles theme={t} onDark />
              {/* 顶部小标签 */}
              <div style={{
                position: 'absolute', left: MARGIN_X, top: 48, width: 144, height: 38,
                borderRadius: 19, background: t.accent, opacity: 0.9,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <span style={{ fontSize: 16, fontWeight: 700, color: '#fff' }}>智慧课件</span>
              </div>
              {titleInput(textW(slide.title, 50) > CONTENT_W - 57 ? 40 : 50, 'center', '#FFFFFF', 192, 106)}
              <div style={{ position: 'absolute', left: SLIDE_W / 2 - 57, top: 307, width: 115, height: 6, background: '#FFFFFF', opacity: 0.7 }} />
              {(editing || slide.bullets[0]) && (
                editing ? (
                  <TextArea
                    variant="borderless"
                    value={slide.bullets[0] || ''}
                    placeholder="副标题（可选）"
                    onChange={(e) => onChange?.({ bullets: [e.target.value, ...slide.bullets.slice(1)] })}
                    style={{
                      position: 'absolute', left: 115, right: 115, top: 328, height: 56,
                      fontSize: 21, color: '#FFFFFF', textAlign: 'center', resize: 'none', padding: 0,
                    }}
                  />
                ) : (
                  <div style={{
                    position: 'absolute', left: 115, right: 115, top: 328, height: 40,
                    fontSize: 21, color: 'rgba(255,255,255,0.85)', textAlign: 'center', lineHeight: '40px',
                  }}>
                    {slide.bullets[0]}
                  </div>
                )
              )}
              {(editing || slide.bullets.length > 1) && (
                editing ? (
                  <TextArea
                    variant="borderless"
                    value={slide.bullets.slice(1).join('\n')}
                    placeholder="补充信息（如 科目 · 年级，每行一条）"
                    onChange={(e) => onChange?.({ bullets: [slide.bullets[0] || '', ...e.target.value.split('\n')] })}
                    style={{
                      position: 'absolute', left: 115, right: 115, top: 440, height: 56,
                      fontSize: 17, color: 'rgba(255,255,255,0.75)', textAlign: 'center', resize: 'none', padding: 0,
                    }}
                  />
                ) : (
                  <div style={{
                    position: 'absolute', left: MARGIN_X, right: MARGIN_X, top: 456,
                    fontSize: 17, color: 'rgba(255,255,255,0.75)', textAlign: 'center',
                  }}>
                    {slide.bullets.slice(1).join('　·　')}
                  </div>
                )
              )}
            </>
          ) : (
            <>
              <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 13, background: t.primary }} />
              <DecoCircles theme={t} onDark={false} />
              {titleInput(48, 'center', t.titleColor, 192, 106)}
              <div style={{ position: 'absolute', left: SLIDE_W / 2 - 57, top: 307, width: 115, height: 6, background: t.primary }} />
              {(editing || slide.bullets[0]) && (
                editing ? (
                  <TextArea
                    variant="borderless"
                    value={slide.bullets[0] || ''}
                    placeholder="副标题（可选）"
                    onChange={(e) => onChange?.({ bullets: [e.target.value, ...slide.bullets.slice(1)] })}
                    style={{
                      position: 'absolute', left: 115, right: 115, top: 328, height: 56,
                      fontSize: 21, color: t.textColor, textAlign: 'center', resize: 'none', padding: 0,
                    }}
                  />
                ) : (
                  <div style={{
                    position: 'absolute', left: 115, right: 115, top: 328, height: 40,
                    fontSize: 21, color: t.textColor, textAlign: 'center', lineHeight: '40px',
                  }}>
                    {slide.bullets[0]}
                  </div>
                )
              )}
            </>
          )}
        </div>
      );

    // ── 目录 ──
    case 'toc': {
      const entries = slide.bullets;
      const cols = entries.length > 6 ? 2 : 1;
      const rows = Math.max(1, Math.ceil(entries.length / cols));
      const colW = cols === 2 ? (CONTENT_W - 38) / 2 : CONTENT_W;
      const cardH = Math.min(60, (SLIDE_H - 149 - 48) / rows - 11);
      return (
        <div style={{ position: 'absolute', inset: 0, background: t.bg, overflow: 'hidden', fontFamily: t.font }}>
          {titleInput(35, 'left', t.titleColor, 40, 60)}
          <div style={{ position: 'absolute', left: MARGIN_X, top: 107, width: 86, height: 7, background: t.primary }} />
          <DecoCircles theme={t} onDark={false} />
          {entries.map((b, i) => {
            const col = Math.floor(i / rows);
            const row = i % rows;
            return (
              <div key={i} style={{
                position: 'absolute',
                left: MARGIN_X + col * (colW + 38),
                top: 149 + row * (cardH + 11),
                width: colW, height: cardH, borderRadius: 10,
                background: tint(t.primary, 0.92),
                display: 'flex', alignItems: 'center', gap: 12, padding: '0 14px 0 13px',
              }}>
                <NumberChip n={i + 1} theme={t} />
                {editing ? (
                  <Input
                    variant="borderless"
                    value={b}
                    placeholder="目录项"
                    onChange={(e) => {
                      const bullets = [...slide.bullets];
                      bullets[i] = e.target.value;
                      onChange?.({ bullets });
                    }}
                    style={{ flex: 1, fontSize: 19, color: t.textColor, padding: 0 }}
                  />
                ) : (
                  <span style={{ fontSize: 19, color: t.textColor, wordBreak: 'break-word' }}>{b}</span>
                )}
                {editing && (
                  <CloseOutlined
                    style={{ color: '#bbb', fontSize: 12, cursor: 'pointer', flexShrink: 0 }}
                    onClick={() => onChange?.({ bullets: slide.bullets.filter((_, idx) => idx !== i) })}
                  />
                )}
              </div>
            );
          })}
          {editing && entries.length === 0 && (
            <div
              style={{ position: 'absolute', left: MARGIN_X, top: 149, color: '#999', fontSize: 14, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}
              onClick={() => onChange?.({ bullets: [''] })}
            >
              <PlusOutlined /> 添加目录项
            </div>
          )}
        </div>
      );
    }

    // ── 章节页 ──
    case 'section':
      return (
        <div style={{ position: 'absolute', inset: 0, background: onDark ? t.primary : tint(t.primary, 0.93), overflow: 'hidden', fontFamily: t.font }}>
          <DecoCircles theme={t} onDark={onDark} />
          <div style={{
            position: 'absolute', right: 38, top: 58, width: 211, textAlign: 'right',
            fontSize: 96, fontWeight: 700, lineHeight: 1,
            color: onDark ? 'rgba(255,255,255,0.25)' : t.accent,
          }}>
            {String(pageNo ?? 1).padStart(2, '0')}
          </div>
          <div style={{
            position: 'absolute', left: MARGIN_X, top: 251, width: 96, height: 7,
            background: onDark ? 'rgba(255,255,255,0.75)' : t.primary,
          }} />
          {titleInput(40, 'left', onDark ? '#FFFFFF' : t.titleColor, 274, 90)}
          {(editing || slide.bullets[0]) && (
            <div style={{
              position: 'absolute', left: MARGIN_X, top: 365, right: 96,
              fontSize: 19, color: onDark ? 'rgba(255,255,255,0.8)' : t.textColor,
            }}>
              {editing ? (
                <Input
                  variant="borderless"
                  value={slide.bullets[0] || ''}
                  placeholder="章节导语（可选）"
                  onChange={(e) => onChange?.({ bullets: [e.target.value, ...slide.bullets.slice(1)] })}
                  style={{ fontSize: 19, color: onDark ? 'rgba(255,255,255,0.8)' : t.textColor, padding: 0 }}
                />
              ) : slide.bullets[0]}
            </div>
          )}
        </div>
      );

    // ── 结束页 ──
    case 'end':
      return (
        <div style={{ position: 'absolute', inset: 0, background: onDark ? t.primary : t.bg, overflow: 'hidden', fontFamily: t.font }}>
          {onDark ? (
            <>
              <DecoCircles theme={t} onDark />
              {titleInput(45, 'center', '#FFFFFF', 226, 96)}
              <div style={{ position: 'absolute', left: SLIDE_W / 2 - 57, top: 336, width: 115, height: 6, background: '#FFFFFF', opacity: 0.7 }} />
              {(editing || slide.bullets[0]) && (
                <div style={{ position: 'absolute', left: 115, right: 115, top: 357, textAlign: 'center' }}>
                  {editing ? (
                    <Input
                      variant="borderless"
                      value={slide.bullets[0] || ''}
                      placeholder="结束语（可选）"
                      onChange={(e) => onChange?.({ bullets: [e.target.value, ...slide.bullets.slice(1)] })}
                      style={{ fontSize: 19, color: 'rgba(255,255,255,0.8)', textAlign: 'center', padding: 0 }}
                    />
                  ) : (
                    <span style={{ fontSize: 19, color: 'rgba(255,255,255,0.8)' }}>{slide.bullets[0]}</span>
                  )}
                </div>
              )}
            </>
          ) : (
            <>
              <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 13, background: t.primary }} />
              <DecoCircles theme={t} onDark={false} />
              {titleInput(45, 'center', t.titleColor, 226, 96)}
            </>
          )}
        </div>
      );

    // ── 大数字页 ──
    case 'big-number': {
      const bn: PptBigNumber = slide.bigNumber || { value: '' };
      const leftW = 384;
      const numSize = bn.value.length <= 3 ? 96 : bn.value.length <= 5 ? 70 : 52;
      const rightX = 480;
      const rightW = 422;
      const hasLabel = !!(bn.label || (editing && true));
      return (
        <div style={{ position: 'absolute', inset: 0, background: t.bg, overflow: 'hidden', fontFamily: t.font }}>
          {contentHeader()}
          <div style={{
            position: 'absolute', left: MARGIN_X, top: 144, width: leftW, height: 343,
            borderRadius: design.radius, background: tint(t.primary, 0.92),
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            {editing ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
                <Input
                  variant="borderless"
                  value={bn.value}
                  placeholder="数字"
                  onChange={(e) => onChange?.({ bigNumber: { ...bn, value: e.target.value } })}
                  style={{ width: 280, fontSize: numSize > 70 ? 50 : numSize, fontWeight: 700, color: t.primary, textAlign: 'center', padding: 0 }}
                />
                <Input
                  variant="borderless"
                  value={bn.unit || ''}
                  placeholder="单位（可选）"
                  onChange={(e) => onChange?.({ bigNumber: { ...bn, unit: e.target.value } })}
                  style={{ width: 170, fontSize: 20, fontWeight: 700, color: t.primary, textAlign: 'center', padding: 0 }}
                />
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'center' }}>
                <span style={{ fontSize: numSize, fontWeight: 700, color: t.primary }}>{bn.value}</span>
                {bn.unit && <span style={{ fontSize: 24, fontWeight: 700, color: t.primary, marginLeft: 6 }}>{bn.unit}</span>}
              </div>
            )}
          </div>
          {editing ? (
            <Input
              variant="borderless"
              value={bn.label || ''}
              placeholder="一句话说明（可选）"
              onChange={(e) => onChange?.({ bigNumber: { ...bn, label: e.target.value } })}
              style={{ position: 'absolute', left: rightX, top: 146, width: rightW, height: 46, fontSize: 19, fontWeight: 700, color: t.titleColor, padding: 0 }}
            />
          ) : bn.label ? (
            <div style={{ position: 'absolute', left: rightX, top: 146, width: rightW, height: 46, fontSize: 19, fontWeight: 700, color: t.titleColor, display: 'flex', alignItems: 'center' }}>{bn.label}</div>
          ) : null}
          {dotList(slide.bullets, hasLabel ? 202 : 150, rightX, rightW, 487 - (hasLabel ? 202 : 150), (bullets) => onChange?.({ bullets }))}
        </div>
      );
    }

    // ── 图表页 ──
    case 'chart': {
      const ch: PptChart = slide.chart || { type: 'bar', categories: [], series: [] };
      const rightX = 638;
      const rightW = 264;
      return (
        <div style={{ position: 'absolute', inset: 0, background: t.bg, overflow: 'hidden', fontFamily: t.font }}>
          {contentHeader()}
          <div style={{ position: 'absolute', left: MARGIN_X, top: 144 }}>
            <ChartFigure chart={ch} t={t} width={547} height={343} />
          </div>
          <div style={{
            position: 'absolute', left: rightX, top: 144, width: rightW, height: 343,
            borderRadius: design.radius, background: tint(t.primary, 0.93), padding: '14px 16px',
          }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: t.titleColor, marginBottom: 8 }}>数据解读</div>
            {innerDotRows(slide.bullets, (bullets) => onChange?.({ bullets }))}
          </div>
        </div>
      );
    }

    // ── 时间线 / 流程页 ──
    case 'timeline': {
      const steps: PptTimelineStep[] = slide.timeline || [];
      const n = steps.length;
      const setStep = (i: number, patch: Partial<PptTimelineStep>) =>
        onChange?.({ timeline: steps.map((s, idx) => (idx === i ? { ...s, ...patch } : s)) });
      const removeStep = (i: number) => onChange?.({ timeline: steps.filter((_, idx) => idx !== i) });
      const addStep = () => onChange?.({ timeline: [...steps, { title: '', desc: '' }] });

      if (n >= 1 && n <= 5) {
        const trackY = 274;
        const lineStart = 87;
        const slotW = 786 / n;
        return (
          <div style={{ position: 'absolute', inset: 0, background: t.bg, overflow: 'hidden', fontFamily: t.font }}>
            {contentHeader()}
            {editing && (
              <div style={{ position: 'absolute', left: MARGIN_X, top: 120, fontSize: 13, color: t.primary, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5 }} onClick={addStep}>
                <PlusOutlined /> 加步骤
              </div>
            )}
            <div style={{ position: 'absolute', left: lineStart, top: trackY - 1, width: 786, height: 2, background: tint(t.primary, 0.5) }} />
            {steps.map((st, i) => {
              const cx = lineStart + slotW * i + slotW / 2;
              return (
                <div key={i}>
                  {editing ? (
                    <Input
                      variant="borderless"
                      value={st.title}
                      placeholder="步骤标题"
                      onChange={(e) => setStep(i, { title: e.target.value })}
                      style={{ position: 'absolute', left: cx - slotW / 2 + 5, top: 156, width: slotW - 10, height: 56, fontSize: 15, fontWeight: 700, color: t.titleColor, textAlign: 'center', padding: 0 }}
                    />
                  ) : (
                    <div style={{ position: 'absolute', left: cx - slotW / 2 + 5, top: 156, width: slotW - 10, height: 56, fontSize: 15, fontWeight: 700, color: t.titleColor, textAlign: 'center', display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>{st.title}</div>
                  )}
                  <div style={{ position: 'absolute', left: cx - 17, top: trackY - 17, width: 35, height: 35, borderRadius: '50%', background: t.primary, border: `2px solid ${t.bg}`, color: '#fff', fontSize: 13, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    {i + 1}
                  </div>
                  {editing ? (
                    <TextArea
                      variant="borderless"
                      value={st.desc || ''}
                      placeholder="说明"
                      onChange={(e) => setStep(i, { desc: e.target.value })}
                      style={{ position: 'absolute', left: cx - slotW / 2 + 8, top: 301, width: slotW - 16, height: 100, fontSize: 12, color: t.textColor, textAlign: 'center', resize: 'none', padding: 0 }}
                    />
                  ) : st.desc ? (
                    <div style={{ position: 'absolute', left: cx - slotW / 2 + 8, top: 301, width: slotW - 16, height: 105, fontSize: 12, color: t.textColor, textAlign: 'center', lineHeight: 1.3 }}>{st.desc}</div>
                  ) : null}
                  {editing && (
                    <CloseOutlined style={{ position: 'absolute', left: cx + 14, top: 296, color: '#bbb', fontSize: 11 }} onClick={() => removeStep(i)} />
                  )}
                </div>
              );
            })}
          </div>
        );
      }

      // 纵向（>5 步）
      const lineX = 82;
      const rowH = Math.min(69, Math.floor(316 / Math.max(n, 1)));
      return (
        <div style={{ position: 'absolute', inset: 0, background: t.bg, overflow: 'hidden', fontFamily: t.font }}>
          {contentHeader()}
          {editing && (
            <div style={{ position: 'absolute', left: MARGIN_X, top: 120, fontSize: 13, color: t.primary, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5 }} onClick={addStep}>
              <PlusOutlined /> 加步骤
            </div>
          )}
          <div style={{ position: 'absolute', left: lineX - 1, top: 168, width: 2, height: 288, background: tint(t.primary, 0.5) }} />
          {steps.map((st, i) => {
            const y = 163 + rowH * i;
            return (
              <div key={i}>
                <div style={{ position: 'absolute', left: lineX - 15, top: y, width: 31, height: 31, borderRadius: '50%', background: t.primary, color: '#fff', fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{i + 1}</div>
                {editing ? (
                  <Input variant="borderless" value={st.title} onChange={(e) => setStep(i, { title: e.target.value })}
                    style={{ position: 'absolute', left: 116, top: y - 2, width: 300, height: 33, fontSize: 15, fontWeight: 700, color: t.titleColor, padding: 0 }} />
                ) : (
                  <div style={{ position: 'absolute', left: 116, top: y - 2, width: 300, height: 33, fontSize: 15, fontWeight: 700, color: t.titleColor, display: 'flex', alignItems: 'center' }}>{st.title}</div>
                )}
                {editing ? (
                  <Input variant="borderless" value={st.desc || ''} placeholder="说明" onChange={(e) => setStep(i, { desc: e.target.value })}
                    style={{ position: 'absolute', left: 116, top: y + 28, width: 700, height: rowH - 26, fontSize: 12, color: t.textColor, padding: 0 }} />
                ) : st.desc ? (
                  <div style={{ position: 'absolute', left: 116, top: y + 28, width: 700, height: rowH - 26, fontSize: 12, color: t.textColor }}>{st.desc}</div>
                ) : null}
                {editing && <CloseOutlined style={{ position: 'absolute', left: 420, top: y + 6, color: '#bbb', fontSize: 11 }} onClick={() => removeStep(i)} />}
              </div>
            );
          })}
        </div>
      );
    }

    // ── 对比页 ──
    case 'compare': {
      const cp: PptCompare = slide.compare || {
        left: { title: '', bullets: [] },
        right: { title: '', bullets: [] },
      };
      const gap = 67;
      const colW = 388;
      const rightColor = mix(t.primary, '#000000', 0.22);
      const setSideTitle = (side: 'left' | 'right', title: string) =>
        onChange?.({ compare: { ...cp, [side]: { ...cp[side], title } } });
      const setSideBullets = (side: 'left' | 'right', bullets: string[]) =>
        onChange?.({ compare: { ...cp, [side]: { ...cp[side], bullets } } });
      const sides = [
        { data: cp.left, x: MARGIN_X, color: t.primary, side: 'left' as const },
        { data: cp.right, x: MARGIN_X + colW + gap, color: rightColor, side: 'right' as const },
      ];
      return (
        <div style={{ position: 'absolute', inset: 0, background: t.bg, overflow: 'hidden', fontFamily: t.font }}>
          {contentHeader()}
          {sides.map(({ data, x, color, side }) => (
            <div key={side}>
              <div style={{ position: 'absolute', left: x, top: 144, width: colW, height: 53, borderRadius: design.radius, background: color, overflow: 'hidden' }}>
                {editing ? (
                  <Input variant="borderless" value={data.title} placeholder="栏目标题"
                    onChange={(e) => setSideTitle(side, e.target.value)}
                    style={{ width: colW, height: 53, fontSize: 17, fontWeight: 700, color: '#fff', textAlign: 'center', padding: 0 }} />
                ) : (
                  <div style={{ width: colW, height: 53, fontSize: 17, fontWeight: 700, color: '#fff', textAlign: 'center', lineHeight: '53px' }}>{data.title}</div>
                )}
              </div>
              <div style={{
                position: 'absolute', left: x, top: 211, width: colW, height: 276,
                borderRadius: design.radius, background: tint(color, 0.94), padding: '14px 16px',
              }}>
                {innerDotRows(data.bullets, (bullets) => setSideBullets(side, bullets))}
              </div>
            </div>
          ))}
          <div style={{ position: 'absolute', left: 480 - 26, top: 145, width: 52, height: 52, borderRadius: '50%', background: t.bg, border: `2px solid ${t.primary}`, color: t.primary, fontSize: 15, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            VS
          </div>
        </div>
      );
    }

    // ── 课堂活动页 ──
    case 'activity': {
      const act: PptActivity = slide.activity || { name: '', steps: [] };
      const listTop = 232;
      const listH = 250;
      const slots = act.steps.length + (editing ? 1 : 0);
      const cardGap = 24;
      const cardW = slots > 0 ? (844 - cardGap * (slots - 1)) / slots : 844;
      const setName = (name: string) => onChange?.({ activity: { ...act, name } });
      const setStep = (i: number, text: string) =>
        onChange?.({ activity: { ...act, steps: act.steps.map((s, idx) => (idx === i ? text : s)) } });
      const removeStep = (i: number) =>
        onChange?.({ activity: { ...act, steps: act.steps.filter((_, idx) => idx !== i) } });
      const addStep = () => onChange?.({ activity: { ...act, steps: [...act.steps, ''] } });
      return (
        <div style={{ position: 'absolute', inset: 0, background: t.bg, overflow: 'hidden', fontFamily: t.font }}>
          {contentHeader()}
          <div style={{ position: 'absolute', left: MARGIN_X, top: 144, width: 844, height: 69, borderRadius: design.radius, background: t.primary }} />
          <div style={{ position: 'absolute', left: 78, top: 160, width: 120, height: 38, borderRadius: 20, background: 'rgba(255,255,255,0.22)', color: '#fff', fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            课堂活动
          </div>
          {editing ? (
            <Input variant="borderless" value={act.name} placeholder="活动名称，如：小组讨论 / 实验探究"
              onChange={(e) => setName(e.target.value)}
              style={{ position: 'absolute', left: 216, top: 144, width: 666, height: 69, fontSize: 19, fontWeight: 700, color: '#fff', padding: 0 }} />
          ) : (
            <div style={{ position: 'absolute', left: 216, top: 144, width: 666, height: 69, fontSize: 19, fontWeight: 700, color: '#fff', display: 'flex', alignItems: 'center' }}>{act.name}</div>
          )}
          {act.steps.map((stp, i) => {
            const x = MARGIN_X + i * (cardW + cardGap);
            return (
              <div key={i} style={{
                position: 'absolute', left: x, top: listTop, width: cardW, height: listH,
                borderRadius: design.radius, background: '#FFFFFF',
                border: `1px solid ${tint(t.primary, 0.6)}`,
                boxShadow: '0 2px 6px rgba(0,0,0,0.10)', padding: '0 12px',
              }}>
                <div style={{ position: 'absolute', left: cardW / 2 - 21, top: 19, width: 42, height: 42, borderRadius: '50%', background: t.primary, color: '#fff', fontSize: 16, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  {i + 1}
                </div>
                {editing ? (
                  <TextArea variant="borderless" value={stp} placeholder="步骤说明"
                    onChange={(e) => setStep(i, e.target.value)}
                    style={{ position: 'absolute', left: 12, top: 75, width: cardW - 24, height: listH - 90, fontSize: 13, color: t.textColor, resize: 'none', padding: 0, textAlign: 'center' }} />
                ) : (
                  <div style={{ position: 'absolute', left: 12, top: 75, width: cardW - 24, height: listH - 90, fontSize: 13, color: t.textColor, textAlign: 'center', lineHeight: 1.4, overflow: 'hidden' }}>{stp}</div>
                )}
                {editing && (
                  <CloseOutlined style={{ position: 'absolute', right: 8, top: 8, color: '#bbb', fontSize: 11 }} onClick={() => removeStep(i)} />
                )}
              </div>
            );
          })}
          {editing && (
            <div
              style={{
                position: 'absolute', left: MARGIN_X + act.steps.length * (cardW + cardGap), top: listTop,
                width: cardW, height: listH, borderRadius: design.radius, border: `1.5px dashed ${tint(t.primary, 0.4)}`,
                color: tint(t.primary, 0.35), display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 6, cursor: 'pointer', fontSize: 13,
              }}
              onClick={addStep}
            >
              <PlusOutlined style={{ fontSize: 20 }} /> 添加步骤
            </div>
          )}
        </div>
      );
    }

    // ── 标题 + 要点卡片 / 双栏 ──
    case 'two-column': {
      const half = Math.ceil(slide.bullets.length / 2);
      const colW = (CONTENT_W - 38) / 2;
      return (
        <div style={{ position: 'absolute', inset: 0, background: t.bg, overflow: 'hidden', fontFamily: t.font }}>
          {titleInput(fitTitleSize(slide.title), 'left', t.titleColor, 38, 60)}
          <div style={{ position: 'absolute', left: MARGIN_X, top: 107, width: 67, height: 6, background: t.primary }} />
          <div style={{ position: 'absolute', right: MARGIN_X, top: 44, fontSize: 12, color: tint(t.primary, 0.55) }}>智慧教案</div>
          {bulletCards(slide.bullets.slice(0, half), 136, MARGIN_X, colW, 351, 1)}
          {bulletCards(slide.bullets.slice(half), 136, MARGIN_X + colW + 38, colW, 351, half + 1)}
          {footer(pageNo ?? 1)}
        </div>
      );
    }

    case 'title-bullets':
    default:
      return (
        <div style={{ position: 'absolute', inset: 0, background: t.bg, overflow: 'hidden', fontFamily: t.font }}>
          {titleInput(fitTitleSize(slide.title), 'left', t.titleColor, 38, 60)}
          <div style={{ position: 'absolute', left: MARGIN_X, top: 107, width: 67, height: 6, background: t.primary }} />
          <div style={{ position: 'absolute', right: MARGIN_X, top: 44, fontSize: 12, color: tint(t.primary, 0.55) }}>智慧教案</div>
          {slide.bullets.length > 0 || editing ? (
            bulletCards(slide.bullets, 136, MARGIN_X, CONTENT_W, 351, 1)
          ) : (
            <div style={{
              position: 'absolute', left: MARGIN_X, top: 136, width: CONTENT_W, height: 351,
              borderRadius: 12, background: tint(t.primary, 0.94),
              padding: '19px 29px', fontSize: 20, color: t.textColor, lineHeight: 1.7,
              whiteSpace: 'pre-wrap', fontFamily: t.font,
            }}>
              {slide.content || '（本页暂无内容）'}
            </div>
          )}
          {footer(pageNo ?? 1)}
        </div>
      );
  }
}
