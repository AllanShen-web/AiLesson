'use client';

import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Spin, Empty } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
import { renderAsync } from 'docx-preview';
import { DocViewer, type DocType } from 'omni-doc-viewer/react';
import { detectCategory, type PreviewSource } from './types';

const DOCX_PREVIEW_STYLES = `
  .docx-preview-container table {
    table-layout: fixed !important;
    border-collapse: collapse !important;
  }
  .docx-preview-container table td,
  .docx-preview-container table th {
    vertical-align: top;
    word-break: break-word;
  }
  .docx-preview-container {
    font-family: SimSun, "宋体", "Songti SC", serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #000;
  }
  .docx-preview-container section {
    background: #fff !important;
    box-shadow: none !important;
    margin: 0 !important;
  }
`;

const DOC_VIEWER_TYPE: Record<string, DocType> = {
  pdf: 'pdf',
  pptx: 'pptx',
  image: 'image',
};

/** 直接读文本渲染的格式 */
const READ_AS_TEXT = new Set(['text', 'markdown']);

interface FilePreviewProps {
  source: PreviewSource;
  /** 预览区高度，默认 520 */
  height?: number;
}

/**
 * 公共文件预览组件。
 *
 * 调用方只提供「怎么拿到文件内容」（url 或 blob），格式分派与三态处理都在组件内完成，
 * 资料库与聊天窗口共用，避免同一份 docx/pdf 渲染逻辑维护两遍。
 *
 * 注意：不含弹窗外壳。聊天窗口用 Portal 挂 body（要穿透自身 stacking context），
 * 资料库用 antd Modal，两者容器诉求不同，弹窗由各自搭建。
 */
export default function FilePreview({ source, height = 520 }: FilePreviewProps) {
  const category = detectCategory(source);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const docxContainerRef = useRef<HTMLDivElement>(null);

  // 取文件内容：blob 直接用；url 带 token fetch 成 blob（绕开 attachment 响应头）
  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';

    const load = async () => {
      setLoading(true);
      setFailed(false);
      setBlob(null);
      setText(null);

      try {
        if (source.kind === 'blob') {
          if (READ_AS_TEXT.has(category)) {
            if (!cancelled) setText(await source.blob.text());
          } else if (!cancelled) {
            setBlob(source.blob);
          }
          if (!cancelled) setLoading(false);
          return;
        }

        const token = typeof window !== 'undefined' ? window.sessionStorage.getItem('token') : null;
        const res = await fetch(source.url, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        if (READ_AS_TEXT.has(category)) {
          if (!cancelled) setText(await res.text());
        } else {
          const b = await res.blob();
          objectUrl = URL.createObjectURL(b);
          if (!cancelled) setBlob(b);
        }
      } catch {
        if (!cancelled) setFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [source, category]);

  // docx 用 docx-preview 渲染，并固定表格布局，让预览忠实还原 tblGrid 列宽
  useEffect(() => {
    if (category !== 'docx' || !blob || loading) return;
    const container = docxContainerRef.current;
    if (!container) return;
    let cancelled = false;

    renderAsync(blob, container, undefined, {
      className: 'docx-preview-page',
      inWrapper: false,
      ignoreWidth: false,
    }).catch(() => {
      if (!cancelled) setFailed(true);
    });

    return () => { cancelled = true; };
  }, [category, blob, loading]);

  if (category === 'unsupported') {
    return (
      <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
        <FileTextOutlined style={{ fontSize: 40, marginBottom: 12, display: 'block' }} />
        该格式暂不支持预览，请下载后查看
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 60 }}>
        <Spin description="加载中..." />
      </div>
    );
  }

  if (failed) {
    return (
      <div style={{ textAlign: 'center', padding: 60, color: '#ff4d4f' }}>
        预览加载失败，请重试或下载后查看
      </div>
    );
  }

  if (category === 'docx') {
    return (
      <>
        {/*
          docx 预览样式。生成侧已显式写入 columnWidths + layout:fixed，
          浏览器默认却会按内容自动分配列宽（把跨列单元格拉宽），
          导致预览与 Word 打开效果不一致，这里固定表格布局忠实还原文件。
        */}
        <style>{DOCX_PREVIEW_STYLES}</style>
        <div
          ref={docxContainerRef}
          className="docx-preview-container"
          style={{ background: '#fff', padding: 16, borderRadius: 8, overflow: 'auto', minHeight: 200 }}
        />
      </>
    );
  }

  if (category === 'pdf' || category === 'pptx' || category === 'image') {
    if (!blob) return <Empty description="无内容" />;
    return (
      <DocViewer
        source={blob}
        type={DOC_VIEWER_TYPE[category]}
        // 连续滚动浏览，不用箭头逐页翻
        pagination={false}
        height={height}
        loading={<div style={{ textAlign: 'center', padding: 60, color: '#999' }}>渲染中...</div>}
        errorFallback={() => (
          <div style={{ textAlign: 'center', padding: 60, color: '#ff4d4f' }}>
            预览渲染失败，请下载后查看
          </div>
        )}
      />
    );
  }

  if (category === 'markdown') {
    return (
      <div className="chat-markdown" style={{ background: '#fff', padding: 16, borderRadius: 8, minHeight: 200 }}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text || ''}</ReactMarkdown>
      </div>
    );
  }

  // 纯文本
  return (
    <pre
      style={{
        margin: 0, background: '#fff', padding: 16, borderRadius: 8,
        fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        minHeight: 200,
      }}
    >
      {text || ''}
    </pre>
  );
}