'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Button, Input, Spin, Tag, message, Tooltip } from 'antd';
import {
  ArrowLeftOutlined, PlusOutlined, DownloadOutlined, PlayCircleOutlined,
  RobotOutlined, SaveOutlined, MenuFoldOutlined, MenuUnfoldOutlined, FilePptOutlined,
} from '@ant-design/icons';
import SlideList, { moveSlide } from './SlideList';
import PropertyPanel from './PropertyPanel';
import PptPresent from './PptPresent';
import SlideContent, { SLIDE_W, SLIDE_H } from './SlideContent';
import {
  pptApi, exportPpt, createSlide,
  DEFAULT_PPT_THEME,
  type PptItem, type PptDoc, type PptSlide,
} from '@/lib/ppt';
import { pptPreviewStore } from '@/lib/pptPreviewStore';
import { pptAgentStore } from '@/lib/pptAgentStore';
import { useMobile } from '@/hooks/useMobile';
import { ROUTES } from '@/constants/routes';

export default function PptEditor() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const isMobile = useMobile();
  const id = searchParams.get('id') ? Number(searchParams.get('id')) : null;

  const [item, setItem] = useState<PptItem | null>(null);
  const [doc, setDoc] = useState<PptDoc | null>(null);
  const [current, setCurrent] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string>('');
  const [presenting, setPresenting] = useState(false);
  const [generating, setGenerating] = useState(false);
  /** 生成阶段：outline = 大纲已出等待用户在对话中确认；slides = 逐页生成中 */
  const [genPhase, setGenPhase] = useState<'idle' | 'outline' | 'slides' | 'done'>('idle');
  /** 两侧面板可见性：折叠后画布独占宽度，预览更大 */
  const [showList, setShowList] = useState(true);
  const [showPanel, setShowPanel] = useState(true);
  /** 画布容器元素（回调 ref，挂载后触发放缩计算）与缩放比 */
  const [canvasEl, setCanvasEl] = useState<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(0.5);
  const mobileWrapRef = useRef<HTMLDivElement>(null);
  const [mobileW, setMobileW] = useState(0);
  const lastSavedRef = useRef('');
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 画布等比缩放：以容器实测宽高为准（元素挂载后再计算，避免初始 0.5 卡死）
  useEffect(() => {
    if (!canvasEl) return;
    const calc = () => {
      const w = canvasEl.clientWidth;
      const h = canvasEl.clientHeight;
      if (w > 0 && h > 0) {
        setScale(Math.max(0.2, Math.min((w - 40) / SLIDE_W, (h - 40) / SLIDE_H)));
      }
    };
    calc();
    const ro = new ResizeObserver(calc);
    ro.observe(canvasEl);
    return () => ro.disconnect();
  }, [canvasEl]);

  // 移动端只读视图的容器宽度测量（用于画布等比缩放）
  useEffect(() => {
    const el = mobileWrapRef.current;
    if (!el || !isMobile) return;
    const calc = () => setMobileW(el.clientWidth);
    calc();
    const ro = new ResizeObserver(calc);
    ro.observe(el);
    return () => ro.disconnect();
  }, [isMobile]);

  useEffect(() => { document.title = 'PPT 编辑器'; }, []);

  // ── 加载记录 ──
  useEffect(() => {
    if (!id) { setLoading(false); return; }
    let cancelled = false;
    pptApi.getById(id)
      .then((data) => {
        if (cancelled) return;
        setItem(data);
        const loaded = data.doc && Array.isArray(data.doc.slides)
          ? { ...data.doc, theme: { ...DEFAULT_PPT_THEME, ...(data.doc.theme || {}) } }
          : { title: data.title, theme: DEFAULT_PPT_THEME, slides: [] };
        setDoc(loaded);
        lastSavedRef.current = JSON.stringify(loaded);
      })
      .catch(() => message.error('加载失败'))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id]);

  // ── 向 AI 助手注册当前编辑的 PPT ──
  useEffect(() => {
    pptAgentStore.setPptId(id);
    return () => pptAgentStore.setPptId(null);
  }, [id]);

  // ── 实时预览：AI 生成期间逐页合并进画布 ──
  useEffect(() => {
    if (!id) return;
    const applyPreview = () => {
      const p = pptPreviewStore.getState();
      if (p.pptId !== id) return;
      setGenPhase(p.phase);
      setGenerating(p.generating);
      if (p.generating && p.slides.length > 0) {
        setDoc((d) => (d ? { ...d, slides: p.slides } : d));
      }
      if (p.done) {
        // 生成完成：拉取服务端规范数据，重置预览状态
        pptPreviewStore.reset();
        pptApi.getById(id)
          .then((data) => {
            const loaded = data.doc && Array.isArray(data.doc.slides)
              ? { ...data.doc, theme: { ...DEFAULT_PPT_THEME, ...(data.doc.theme || {}) } }
              : { title: data.title, theme: DEFAULT_PPT_THEME, slides: [] };
            setDoc(loaded);
            setItem(data);
            lastSavedRef.current = JSON.stringify(loaded);
          })
          .catch(() => {});
      }
    };
    applyPreview();
    return pptPreviewStore.subscribe(applyPreview);
  }, [id]);

  // ── 防抖自动保存 ──
  useEffect(() => {
    if (!id || !doc) return;
    const snapshot = JSON.stringify(doc);
    if (snapshot === lastSavedRef.current) return;
    setSaving(true);
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      try {
        await pptApi.update(id, { doc, title: doc.title });
        lastSavedRef.current = snapshot;
        setSavedAt(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }));
      } catch {
        message.error('保存失败');
      } finally {
        setSaving(false);
      }
    }, 800);
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, [doc, id]);

  const updateSlide = useCallback((patch: Partial<PptSlide>) => {
    setDoc((d) => {
      if (!d) return d;
      const slides = [...d.slides];
      slides[current] = { ...slides[current], ...patch };
      return { ...d, slides };
    });
  }, [current]);

  const updateTheme = useCallback((patch: Partial<PptDoc['theme']>) => {
    setDoc((d) => (d ? { ...d, theme: { ...d.theme, ...patch } } : d));
  }, []);

  const handleReorder = useCallback((from: number, to: number) => {
    setDoc((d) => (d ? { ...d, slides: moveSlide(d.slides, from, to) } : d));
  }, []);

  const handleAdd = useCallback(() => {
    setDoc((d) => {
      if (!d) return d;
      const slides = [...d.slides];
      slides.splice(current + 1, 0, createSlide('title-bullets'));
      return { ...d, slides };
    });
    setCurrent((c) => c + 1);
  }, [current]);

  const handleDuplicate = useCallback((index: number) => {
    setDoc((d) => {
      if (!d) return d;
      const slides = [...d.slides];
      slides.splice(index + 1, 0, JSON.parse(JSON.stringify(slides[index])));
      return { ...d, slides };
    });
    setCurrent((c) => (c > index ? c + 1 : c));
  }, []);

  const handleRemove = useCallback((index: number) => {
    setDoc((d) => {
      if (!d || d.slides.length <= 1) {
        message.warning('至少保留一页');
        return d;
      }
      const slides = d.slides.filter((_, i) => i !== index);
      return { ...d, slides };
    });
    setCurrent((c) => Math.max(0, c > index ? c - 1 : c));
  }, []);

  const handleMove = useCallback((direction: -1 | 1) => {
    const to = current + direction;
    setDoc((d) => (d ? { ...d, slides: moveSlide(d.slides, current, to) } : d));
    setCurrent(to);
  }, [current]);

  const handleExport = async () => {
    if (!id || !doc) return;
    try {
      await exportPpt(id, doc.title);
    } catch {
      message.error('导出失败');
    }
  };

  // ── 移动端：只读浏览 + 导出 ──
  if (isMobile) {
    const slide = doc?.slides?.[Math.min(current, (doc?.slides?.length || 1) - 1)];
    return (
      <div style={{ padding: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <Button icon={<ArrowLeftOutlined />} onClick={() => router.push(ROUTES.PPT)} />
          <span style={{ fontWeight: 600 }}>{doc?.title || 'PPT'}</span>
        </div>
        {loading || !doc ? <Spin /> : slide ? (
          <>
            <div
              ref={mobileWrapRef}
              style={{ width: '100%', border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden', position: 'relative' }}
            >
              <div style={{ width: mobileW, height: SLIDE_H * (mobileW / SLIDE_W), position: 'relative' }}>
                <div style={{
                  width: SLIDE_W, height: SLIDE_H,
                  transform: `scale(${mobileW / SLIDE_W})`, transformOrigin: 'top left',
                }}>
                  <SlideContent slide={slide} theme={doc.theme} stage={doc.stage} />
                </div>
              </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12 }}>
              <Button disabled={current === 0} onClick={() => setCurrent((c) => c - 1)}>上一页</Button>
              <span>{current + 1} / {doc.slides.length}</span>
              <Button disabled={current >= doc.slides.length - 1} onClick={() => setCurrent((c) => c + 1)}>下一页</Button>
            </div>
            <Button type="primary" block icon={<DownloadOutlined />} style={{ marginTop: 12 }} onClick={handleExport}>
              导出 PPTX
            </Button>
            <p style={{ color: '#999', fontSize: 12, marginTop: 12, textAlign: 'center' }}>
              在线编辑功能请在 PC 端使用
            </p>
          </>
        ) : <p style={{ color: '#999' }}>暂无页面</p>}
      </div>
    );
  }

  if (loading || !doc) {
    return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spin size="large" /></div>;
  }

  const slide = doc.slides[Math.min(current, doc.slides.length - 1)];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', margin: '-24px' }}>
      {/* 顶栏 */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
        borderBottom: '1px solid #f0f0f0', background: '#fff', flexShrink: 0,
      }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => router.push(ROUTES.PPT)} />
        <Input
          variant="borderless"
          value={doc.title}
          onChange={(e) => setDoc((d) => (d ? { ...d, title: e.target.value } : d))}
          style={{ maxWidth: 320, fontWeight: 600 }}
          placeholder="PPT 标题"
        />
        {item?.status && (
          <Tag color={item.status === 'done' ? 'green' : 'orange'}>
            {item.status === 'done' ? '已完成' : '大纲'}
          </Tag>
        )}
        {item?.lessonPlanId ? <Tag>来源教案</Tag> : null}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: '#999', display: 'flex', alignItems: 'center', gap: 4 }}>
          <SaveOutlined />
          {saving ? '保存中…' : savedAt ? `已保存 ${savedAt}` : '未修改'}
        </span>
        <Tooltip title={showList ? '隐藏页面列表' : '显示页面列表'}>
          <Button
            icon={showList ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
            onClick={() => setShowList((v) => !v)}
          />
        </Tooltip>
        <Tooltip title={showPanel ? '隐藏属性面板' : '显示属性面板'}>
          <Button
            icon={showPanel ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setShowPanel((v) => !v)}
          />
        </Tooltip>
        <Button icon={<PlusOutlined />} onClick={handleAdd}>新增页面</Button>
        <Button icon={<PlayCircleOutlined />} onClick={() => setPresenting(true)}>演示</Button>
        <Button type="primary" icon={<DownloadOutlined />} onClick={handleExport}>导出 PPTX</Button>
      </div>

      {/* AI 生成中提示条：区分「等待确认」与「逐页生成」两个阶段 */}
      {generating && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '8px 16px',
          background: genPhase === 'outline' ? 'rgba(250, 140, 22, 0.08)' : 'rgba(102,126,234,0.08)',
          color: genPhase === 'outline' ? '#d46b08' : '#667eea',
          fontSize: 13, flexShrink: 0,
        }}>
          {genPhase === 'outline' ? <FilePptOutlined /> : <RobotOutlined spin />}
          {genPhase === 'outline'
            ? '大纲已生成，请在对话中确认后开始逐页生成（当前展示为大纲占位预览，暂不可编辑）'
            : 'AI 正在逐页生成，对话中可实时查看进度；生成期间暂不可编辑'}
        </div>
      )}

      {/* 三栏主体：页面列表 + 画布 + 属性面板（两侧可折叠，画布始终居中填满） */}
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        {showList && (
          <SlideList
            slides={doc.slides}
            theme={doc.theme}
            stage={doc.stage}
            docTitle={doc.title}
            current={current}
            onSelect={setCurrent}
            onReorder={handleReorder}
            onAdd={handleAdd}
            onDuplicate={handleDuplicate}
            onRemove={handleRemove}
          />
        )}

        {/* 画布：flex 居中，按容器实测尺寸等比缩放，占满可用空间 */}
        <div
          ref={setCanvasEl}
          style={{
            flex: 1, minWidth: 0, background: '#f5f6f8',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: 20, overflow: 'hidden',
          }}
        >
          {slide ? (
            <div style={{
              width: SLIDE_W * scale, height: SLIDE_H * scale,
              boxShadow: '0 4px 24px rgba(0,0,0,0.12)', borderRadius: 4, overflow: 'hidden',
              background: '#fff', position: 'relative', flexShrink: 0,
            }}>
              <div style={{
                width: SLIDE_W, height: SLIDE_H,
                transform: `scale(${scale})`, transformOrigin: 'top left',
              }}>
                <SlideContent
                  slide={slide}
                  theme={doc.theme}
                  stage={doc.stage}
                  editing={!generating}
                  onChange={updateSlide}
                  pageNo={current + 1}
                  docTitle={doc.title}
                />
              </div>
            </div>
          ) : (
            <div style={{ color: '#999', fontSize: 14, textAlign: 'center' }}>
              暂无页面，点击左上角「新增页面」<br />或在对话中让 AI 助手生成
            </div>
          )}
        </div>

        {showPanel && slide && (
          <PropertyPanel
            slide={slide}
            theme={doc.theme}
            index={current}
            total={doc.slides.length}
            onSlideChange={updateSlide}
            onThemeChange={updateTheme}
            onMove={handleMove}
            onDuplicate={() => handleDuplicate(current)}
            onRemove={() => handleRemove(current)}
            onPresent={() => setPresenting(true)}
          />
        )}
      </div>

      {presenting && (
        <PptPresent
          slides={doc.slides}
          theme={doc.theme}
          stage={doc.stage}
          docTitle={doc.title}
          startIndex={current}
          onClose={() => setPresenting(false)}
        />
      )}
    </div>
  );
}
