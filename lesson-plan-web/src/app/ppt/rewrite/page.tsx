'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  Button, Typography, Tag, Spin, message, Select, Card, Empty, Progress,
} from 'antd';
import {
  ArrowLeftOutlined, DownloadOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import {
  pptApi, getPptPreviewUrl, downloadPptFile, startRewritePolling, isPendingTask,
  type PptItem, type RewriteReport, type RewriteTask,
} from '@/lib/ppt';
import { lessonPlanApi, type LessonPlanItem } from '@/lib/api';

const { Title, Text } = Typography;

type Rev = 'original' | 'working';

interface ShapeBox {
  text: string;
  left: number | null;
  top: number | null;
  width: number | null;
  height: number | null;
}

/**
 * 文字版兜底预览：渲染环境没有 LibreOffice/PowerPoint 时 PNG 出不来，就用已解析好的
 * structure 按文字框位置画一页（坐标单位是英寸）。只保文字和位置，不保配色、图片、母版。
 * 解析器只读页内 xfrm、不继承母版位置，所以套母版默认位置的占位符是没有坐标的，
 * 那批文字另列在页面框下方，不然改写后的正文会整块看不见。
 * 左侧缩略图不放文字：中文最小字号会把小框撑成乱码，那里只留页码。
 */
function TextSlide({ shapes }: { shapes: ShapeBox[] }) {
  const withText = shapes.filter((s) => s.text.trim());
  // 四个数齐了才按坐标画：缺宽或高会算出 0% 宽的框，文字等于被吞掉，不如另列
  const isPositioned = (s: ShapeBox) =>
    s.left !== null && s.top !== null && s.width !== null && s.height !== null;
  const boxes = withText.filter(isPositioned);
  const loose = withText.filter((s) => !isPositioned(s));

  if (withText.length === 0) {
    return (
      <div
        style={{
          aspectRatio: '16/9',
          background: '#fff',
          border: '1px solid #eee',
          borderRadius: 6,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#bbb',
          fontSize: 14,
        }}
      >
        该页没有文字内容
      </div>
    );
  }

  // structure 没记页面尺寸。按 16:9 课件（13.334x7.5 英寸）兜底，内容超出才撑大：
  // 纯按文字框范围推的话，一旦只有标题带坐标、正文套母版位置，整页会被压成一条横带
  const right = Math.max(0, ...boxes.map((s) => (s.left ?? 0) + (s.width ?? 0)));
  const bottom = Math.max(0, ...boxes.map((s) => (s.top ?? 0) + (s.height ?? 0)));
  const pageW = Math.max(13.334, right);
  const pageH = Math.max(7.5, bottom);

  return (
    <div>
      {boxes.length > 0 && (
        <div
          style={{
            position: 'relative',
            width: '100%',
            aspectRatio: `${pageW}/${pageH}`,
            background: '#fff',
            border: '1px solid #eee',
            borderRadius: 6,
            overflow: 'hidden',
          }}
        >
          {boxes.map((s, i) => (
            <div
              key={i}
              style={{
                position: 'absolute',
                left: `${((s.left ?? 0) / pageW) * 100}%`,
                top: `${((s.top ?? 0) / pageH) * 100}%`,
                width: `${((s.width ?? 0) / pageW) * 100}%`,
                height: `${((s.height ?? 0) / pageH) * 100}%`,
                fontSize: 13,
                lineHeight: 1.3,
                color: '#333',
                overflow: 'hidden',
                whiteSpace: 'pre-wrap',
              }}
            >
              {s.text}
            </div>
          ))}
        </div>
      )}

      {loose.length > 0 && (
        <div
          style={{
            marginTop: boxes.length > 0 ? 10 : 0,
            background: '#fafafa',
            border: '1px solid #f0f0f0',
            borderRadius: 6,
            padding: '8px 12px',
          }}
        >
          <div style={{ fontSize: 12, color: '#999', marginBottom: 6 }}>以下文字框没有位置信息（套用母版默认位置）</div>
          {loose.map((s, i) => (
            <div key={i} style={{ fontSize: 13, color: '#333', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>
              {s.text}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RewriteInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const id = Number(sp.get('id'));

  const [loading, setLoading] = useState(true);
  const [ppt, setPpt] = useState<PptItem | null>(null);
  const [lessons, setLessons] = useState<LessonPlanItem[]>([]);
  const [selectedLesson, setSelectedLesson] = useState<number | null>(null);

  const [urls, setUrls] = useState<Record<number, string>>({});
  const urlsRef = useRef<Record<number, string>>({});
  /** 预览请求已全部返回：没拿到图的页才切文字兜底，否则加载中就会闪一版文字预览 */
  const [previewDone, setPreviewDone] = useState(false);
  const [current, setCurrent] = useState(1);
  const [task, setTask] = useState<RewriteTask | null>(null);
  const [report, setReport] = useState<RewriteReport | null>(null);
  const stopPollingRef = useRef<null | (() => void)>(null);

  const running = task !== null && isPendingTask(task);

  const pages = ppt?.structure?.slideCount || 0;
  const hasWorking = !!ppt?.hasWorking;
  // 模板记录只有原始视图；改写产物是另一条记录，它的原始文件仍是未动的模板，看它没有意义
  const rev: Rev = hasWorking ? 'working' : 'original';

  const loadPreviews = useCallback(
    async (r: Rev) => {
      if (!ppt) return;
      const total = ppt.structure?.slideCount || 0;
      setPreviewDone(false);
      Object.values(urlsRef.current).forEach((u) => URL.revokeObjectURL(u));
      urlsRef.current = {};
      const entries = await Promise.all(
        Array.from({ length: total }, async (_, i) => {
          const p = i + 1;
          try {
            return [p, await getPptPreviewUrl(id, p, r)] as [number, string];
          } catch {
            // 单页渲染失败不连带清空其余缩略图
            return null;
          }
        }),
      );
      const map = Object.fromEntries(entries.filter((e): e is [number, string] => e !== null));
      urlsRef.current = map;
      setUrls(map);
      setPreviewDone(true);
    },
    [id, ppt],
  );

  // 初始加载 PPT 与教案列表
  useEffect(() => {
    if (!id) return;
    (async () => {
      setLoading(true);
      try {
        const p = await pptApi.getById(id);
        setPpt(p);
        const ls = await lessonPlanApi.getMyAll();
        setLessons(Array.isArray(ls) ? ls : []);
        if (p.lessonPlanId) setSelectedLesson(p.lessonPlanId);
        setCurrent(1);
      } catch {
        message.error('加载失败');
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  // 记录变化时加载对应版本预览
  useEffect(() => {
    if (ppt) loadPreviews(rev);
  }, [ppt, rev, loadPreviews]);

  // 卸载时释放 objectURL
  useEffect(
    () => () => {
      Object.values(urlsRef.current).forEach((u) => URL.revokeObjectURL(u));
      stopPollingRef.current?.();
    },
    [],
  );

  /** 任务结束时收报告并跳到产物记录；产物是独立记录，模板本身保持不变 */
  const finishTask = useCallback(
    (t: RewriteTask) => {
      stopPollingRef.current?.();
      stopPollingRef.current = null;
      setTask(null);
      if (t.status === 'error') {
        message.error(t.error || '改写失败');
        return;
      }
      if (!t.report) return;
      setReport(t.report);
      message.success(`改写完成，已生成新课件《${t.report.title}》`);
      router.replace(`/ppt/rewrite?id=${t.report.id}`);
    },
    [router],
  );

  const watchTask = useCallback(
    (taskId: string) => {
      if (stopPollingRef.current) return;
      stopPollingRef.current = startRewritePolling((list) => {
        const t = list.find((x) => x.id === taskId);
        if (!t) return false;
        setTask(t);
        if (isPendingTask(t)) return true;
        finishTask(t);
        return false;
      });
    },
    [finishTask],
  );

  // 进页面接上正在跑的改写：从列表页发起后点进来看，进度不丢
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    pptApi
      .listRewriteTasks()
      .then((list) => {
        const t = list.find((x) => x.pptId === id && isPendingTask(x));
        if (cancelled || !t) return;
        setTask(t);
        watchTask(t.id);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [id, watchTask]);

  const handleRewrite = async () => {
    if (!selectedLesson) {
      message.warning('请先选择教案');
      return;
    }
    try {
      const t = await pptApi.startRewrite(id, selectedLesson);
      setTask(t);
      watchTask(t.id);
    } catch (e: any) {
      message.error(e?.message || '发起改写失败');
    }
  };

  const handleDownload = async () => {
    try {
      await downloadPptFile(id, ppt?.title || '课件');
    } catch {
      message.error('下载失败');
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '80px 0' }}>
        <Spin description="加载模板中…" />
      </div>
    );
  }

  if (!ppt) {
    return <Empty description="未找到该 PPT" style={{ padding: '80px 0' }} />;
  }

  return (
    <div style={{ background: '#fff', borderRadius: 8 }}>
      {/* 顶部栏 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '10px 16px',
          borderBottom: '1px solid #f0f0f0',
        }}
      >
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => router.back()} />
        <Title level={5} style={{ margin: 0, flex: 1 }} ellipsis>
          {ppt.title}
        </Title>
        {hasWorking ? <Tag color="green">改写产物</Tag> : <Tag color="blue">模板</Tag>}
        <Tag>{pages} 页</Tag>
        <Button type="primary" ghost icon={<DownloadOutlined />} onClick={handleDownload}>
          下载 PPT
        </Button>
      </div>

      {/* 三栏主体 */}
      <div style={{ display: 'flex', flexWrap: 'wrap', minHeight: 'calc(100vh - 220px)' }}>
        {/* 左：缩略图列 */}
        <div
          style={{
            width: 176,
            maxHeight: 'calc(100vh - 220px)',
            overflowY: 'auto',
            borderRight: '1px solid #f0f0f0',
            padding: 8,
          }}
        >
          {Array.from({ length: pages }, (_, i) => i + 1).map((p) => (
            <div
              key={p}
              onClick={() => setCurrent(p)}
              style={{
                marginBottom: 8,
                cursor: 'pointer',
                border: current === p ? '2px solid #1677ff' : '2px solid transparent',
                borderRadius: 6,
                overflow: 'hidden',
                position: 'relative',
              }}
            >
              {urls[p] ? (
                <img src={urls[p]} alt={`第${p}页`} style={{ width: '100%', display: 'block' }} />
              ) : previewDone ? (
                <div style={{ aspectRatio: '16/9', background: '#fff' }} />
              ) : (
                <div style={{ aspectRatio: '16/9', background: '#fafafa' }} />
              )}
              <span
                style={{
                  position: 'absolute',
                  left: 4,
                  top: 4,
                  background: 'rgba(0,0,0,.55)',
                  color: '#fff',
                  fontSize: 11,
                  borderRadius: 4,
                  padding: '0 5px',
                }}
              >
                {p}
              </span>
            </div>
          ))}
        </div>

        {/* 中：大图预览 */}
        <div
          style={{
            flex: 1,
            minWidth: 320,
            background: '#f5f5f5',
            padding: 20,
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'center',
          }}
        >
          {urls[current] ? (
            <img
              src={urls[current]}
              alt={`第${current}页`}
              style={{
                width: '100%',
                maxWidth: 920,
                borderRadius: 6,
                boxShadow: '0 2px 14px rgba(0,0,0,.14)',
              }}
            />
          ) : previewDone ? (
            <div style={{ width: '100%', maxWidth: 920 }}>
              <TextSlide shapes={(ppt.structure?.slides?.[current - 1]?.shapes ?? []) as ShapeBox[]} />
            </div>
          ) : (
            <Spin />
          )}
        </div>

        {/* 右：AI 改写操作 */}
        <div
          style={{
            width: 300,
            borderLeft: '1px solid #f0f0f0',
            padding: 16,
            maxHeight: 'calc(100vh - 220px)',
            overflowY: 'auto',
          }}
        >
          <Card size="small" title="AI 按教案改写" style={{ marginBottom: 16 }}>
            <Select
              showSearch
              optionFilterProp="label"
              style={{ width: '100%', marginBottom: 12 }}
              placeholder="选择要套用的教案"
              value={selectedLesson}
              onChange={setSelectedLesson}
              options={lessons.map((l) => ({ value: l.id, label: l.title }))}
            />
            <Button
              type="primary"
              block
              loading={running}
              icon={<ThunderboltOutlined />}
              onClick={handleRewrite}
            >
              {running ? '后台改写中' : '开始 AI 改写'}
            </Button>
            {task && (
              <div style={{ marginTop: 10 }}>
                <Progress
                  percent={task.total ? Math.round((task.done / task.total) * 100) : 0}
                  size="small"
                  showInfo={task.total > 0}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {task.stage}
                  {task.total ? `（${task.done}/${task.total}）` : ''}
                </Text>
              </div>
            )}
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
              AI 仅替换文字内容，模板的配色、版式、装饰、动画保持不变。整册改写要几分钟，可以去别的页面，回来看进度。
            </Text>
          </Card>

          <Card size="small" title="信息" style={{ marginTop: 16 }}>
            <div style={{ fontSize: 13, lineHeight: 2 }}>
              <div>总页数：{pages} 页</div>
              <div>当前查看：{hasWorking ? '改写结果' : '原始模板'}</div>
            </div>
          </Card>

          {report && (
            <Card size="small" title="本次改写报告" style={{ marginTop: 16 }}>
              <div style={{ fontSize: 13, lineHeight: 2 }}>
                <div>
                  产物：《{report.title}》 {report.pages} 页
                  {report.templatePages !== report.pages ? `（模板 ${report.templatePages} 页）` : ''}
                </div>
                <div>替换文本：{report.applied} 处</div>
                {report.deletedSlides.length > 0 && (
                  <div>删除模板页：第 {report.deletedSlides.map((s) => s + 1).join('、')} 页</div>
                )}
                {report.watermarkSlots > 0 && <div>清除来源水印：{report.watermarkSlots} 处</div>}
                {report.echoedFixed > 0 && <div>照抄模板已补写：{report.echoedFixed} 处</div>}
                {report.clearedSlots > 0 && <div>补写后仍照抄已清空：{report.clearedSlots} 处</div>}
                {report.notEditableTexts > 0 && (
                  <div>改不到的文本：{report.notEditableTexts} 处（图表等嵌入对象里的文字）</div>
                )}
                {report.pagePlanFailedBatches > 0 && (
                  <Text type="warning" style={{ fontSize: 12 }}>
                    有 {report.pagePlanFailedBatches} 批页面没能判断该不该删，可以再改写一次。
                  </Text>
                )}
                {report.unrewritten.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <Text type="warning" style={{ fontSize: 12 }}>
                      {report.unrewritten.length} 个文本框仍是模板原文，可在 PowerPoint 里手动补充：
                    </Text>
                    <ul style={{ paddingLeft: 18, margin: '4px 0 0', maxHeight: 160, overflowY: 'auto' }}>
                      {report.unrewritten.map((u) => (
                        <li key={u.target} style={{ fontSize: 12, color: '#8c8c8c' }}>
                          第 {Number(u.target.split('-')[0]) + 1} 页：{u.text || '（空）'}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {report.blanked.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {report.blanked.length} 个文本框被清空（多为水印与无效原文）：
                    </Text>
                    <ul style={{ paddingLeft: 18, margin: '4px 0 0', maxHeight: 160, overflowY: 'auto' }}>
                      {report.blanked.map((b) => (
                        <li key={b.target} style={{ fontSize: 12, color: '#8c8c8c' }}>
                          第 {Number(b.target.split('-')[0]) + 1} 页：{b.text || '（空）'}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PptRewritePage() {
  return (
    <Suspense
      fallback={
        <div style={{ textAlign: 'center', padding: '80px 0' }}>
          <Spin />
        </div>
      }
    >
      <RewriteInner />
    </Suspense>
  );
}
