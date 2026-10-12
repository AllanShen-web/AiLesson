'use client';

import React, { useEffect, useRef, useState } from 'react';
import {
  SearchOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  ReadOutlined,
  DownloadOutlined,
  EditOutlined,
  LoadingOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  DownOutlined,
  RightOutlined,
  RobotOutlined,
  FilePptOutlined,
  PictureOutlined,
} from '@ant-design/icons';

export interface ProgressStep {
  /** 步骤类型，决定图标与文案 */
  kind: 'thinking' | 'searchPlans' | 'readPlan' | 'searchFiles' | 'readFile' | 'ragSearch' | 'export' | 'fillField' | 'pptOutline' | 'pptSlide' | 'generic';
  /** 展示给用户的文案，已去掉内部 id */
  label: string;
  /** 是否仍在执行 */
  pending?: boolean;
  /** 执行失败 */
  failed?: boolean;
}

interface AgentProgressProps {
  steps: ProgressStep[];
  /** 整个请求是否还在进行中 */
  running: boolean;
}

const STEP_ICON: Record<ProgressStep['kind'], React.ReactNode> = {
  thinking: <RobotOutlined />,
  searchPlans: <SearchOutlined />,
  readPlan: <ReadOutlined />,
  searchFiles: <SearchOutlined />,
  readFile: <FolderOpenOutlined />,
  ragSearch: <SearchOutlined style={{ color: '#52c41a' }} />,
  export: <DownloadOutlined />,
  fillField: <EditOutlined />,
  pptOutline: <FilePptOutlined />,
  pptSlide: <PictureOutlined />,
  generic: <FileTextOutlined />,
};

/**
 * Agent 的处理过程面板。
 *
 * 设计要点：把「思考 + 工具调用」从消息流里剥离出来单独折叠展示，
 * 只有模型真正输出正文（token 流）时才用打字光标，
 * 避免工具等待期出现一个意义不明的闪烁光标。
 */
export default function AgentProgress({ steps, running }: AgentProgressProps) {
  const [expanded, setExpanded] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  // 整篇生成会有 40+ 步，列表定高只留最近 6 条，运行时自动跟到最新一条
  useEffect(() => {
    if (running && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [steps.length, running]);

  if (steps.length === 0) return null;

  const doneCount = steps.filter((s) => !s.pending).length;
  const failedCount = steps.filter((s) => s.failed).length;
  // 进行中默认展开，让用户看到正在发生什么；结束后收起，只留一行摘要
  const isOpen = running ? true : expanded;
  const summary = running
    ? steps.some((s) => s.pending)
      ? steps.find((s) => s.pending)?.label || '正在处理...'
      : '正在整理结果...'
    : failedCount > 0
      ? `${failedCount} 个步骤失败，共 ${doneCount} 步`
      : `已完成 ${doneCount} 个步骤`;

  return (
    <div style={{ marginBottom: 10, maxWidth: 320 }}>
      <div
        onClick={() => !running && setExpanded((v) => !v)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          padding: '5px 10px',
          borderRadius: 8,
          background: running ? 'rgba(102,126,234,0.06)' : '#f7f8fa',
          border: `1px solid ${running ? 'rgba(102,126,234,0.18)' : '#ececec'}`,
          fontSize: 12,
          color: '#666',
          cursor: running ? 'default' : 'pointer',
          userSelect: 'none',
          maxWidth: '100%',
        }}
      >
        {running ? (
          <LoadingOutlined style={{ color: '#667eea', fontSize: 12 }} spin />
        ) : failedCount > 0 ? (
          <CloseCircleFilled style={{ color: '#ff4d4f', fontSize: 12 }} />
        ) : (
          <CheckCircleFilled style={{ color: '#52c41a', fontSize: 12 }} />
        )}
        <span
          style={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {summary}
        </span>
        {!running &&
          (isOpen ? (
            <DownOutlined style={{ fontSize: 10, color: '#bbb' }} />
          ) : (
            <RightOutlined style={{ fontSize: 10, color: '#bbb' }} />
          ))}
      </div>

      {isOpen && (
        <div
          ref={listRef}
          style={{
            marginTop: 6,
            padding: '8px 10px 8px 8px',
            borderRadius: 8,
            background: '#fafbfc',
            border: '1px solid #ececec',
            // 每行 24px（12px 字号 ×1.5 行高 + 上下 3px 内边距），6 行 + 上内边距
            maxHeight: 152,
            overflowY: 'auto',
            overscrollBehavior: 'contain',
          }}
        >
          {steps.map((step, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 8,
                padding: '3px 0',
                fontSize: 12,
                lineHeight: 1.5,
                color: step.pending ? '#667eea' : step.failed ? '#ff4d4f' : '#888',
              }}
            >
              <span
                style={{
                  marginTop: 2,
                  fontSize: 12,
                  flexShrink: 0,
                  color: step.pending ? '#667eea' : step.failed ? '#ff4d4f' : '#bbb',
                }}
              >
                {step.pending ? <LoadingOutlined spin /> : STEP_ICON[step.kind]}
              </span>
              <span style={{ wordBreak: 'break-word' }}>
                {step.label}
                {step.failed && <span style={{ color: '#ff4d4f' }}>（失败）</span>}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
