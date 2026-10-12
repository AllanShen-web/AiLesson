'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams } from 'next/navigation';
import { Input, Button, Spin } from 'antd';
import { SendOutlined, DeleteOutlined } from '@ant-design/icons';
import { blessApi, blessSettingApi, type BlessEntry, type BlessSettings, type BlessSettingRecord } from '@/lib/api';
import { API_HOST } from '@/lib/env';

const { TextArea } = Input;

const EMOJIS = ['🎉','🎊','🌟','✨','💫','🎀','🌈','🦋','🌸','🎓','🎆','🎇','💐','🥳'];
const FLOAT_COUNT = 50;
const TRAIL_LEN = 10;
const FW_POOL_SIZE = 160;

const DEFAULT_SETTINGS: BlessSettings = {
  title: '🎓 毕业快乐！',
  titleColor: '#e17055',
  subtitle: '',
  subtitleColor: '#6c5ce7',
  blessings: [],
  footerText: '',
  enableFloating: true,
  enableGradient: true,
  bgImageUrl: null,
  bgImageUrls: [],
  bgInterval: 5,
  replyMessage: '',
  replyBgColor: '#fff5eb',
  replyTextColor: '#8b3a1a',
};

function generateTempId() {
  return Math.random().toString(36).substring(2, 8) + Date.now().toString(36);
}

export default function BlessPageClient() {
  const searchParams = useSearchParams();
  const username = searchParams.get('to') || '';

  const [entries, setEntries] = useState<BlessEntry[]>([]);
  const [settings, setSettings] = useState<BlessSettings>(DEFAULT_SETTINGS);
  const [nickname, setNickname] = useState('');
  const [content, setContent] = useState('');
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(true);
  const [notFound, setNotFound] = useState(true);
  const [bgIndex, setBgIndex] = useState(0);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [myIds, setMyIds] = useState<Set<number>>(new Set());
  const [replyDanmaku, setReplyDanmaku] = useState('');
  const [toastMsg, setToastMsg] = useState('');
  const [danmakuList, setDanmakuList] = useState<{ id: number; text: string; top: number; duration: number }[]>([]);
  const [confirmDelete, setConfirmDelete] = useState<{ id: number; nick: string; content: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const h1Ref = useRef<HTMLHeadingElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const fwPoolRef = useRef<HTMLDivElement[]>([]);
  const trailRef = useRef<{ x: number; y: number }[]>([]);
  const mouseRef = useRef({ x: -100, y: -100 });

  const loadEntries = useCallback(async () => {
    try {
      setEntries(await blessApi.getWall(username));
    } catch {
      // silent
    } finally {
      setFetching(false);
    }
  }, [username]);

  const loadSetting = useCallback(async () => {
    try {
      const data = await blessSettingApi.getPublic(username);
      if (!data || !data.enabled) {
        setNotFound(true);
        return;
      }
      setSettings(data.settings);
      setNotFound(false);
      setSettingsLoaded(true);
      document.title = `${data.nickname || username}的留言板`;
    } catch {
      setNotFound(true);
    }
  }, [username]);

  useEffect(() => {
    loadSetting();
    loadEntries();
  }, [loadSetting, loadEntries]);

  // Restore myIds from sessionStorage
  useEffect(() => {
    if (entries.length === 0) return;
    const ids = new Set<number>();
    entries.forEach(e => {
      if (sessionStorage.getItem(`bl_temp_${e.id}`)) ids.add(e.id);
    });
    if (ids.size > 0) setMyIds(prev => new Set([...prev, ...ids]));
  }, [entries]);

  // Entry danmaku - only on first load
  const [danmakuTriggered, setDanmakuTriggered] = useState(false);
  useEffect(() => {
    if (danmakuTriggered || entries.length === 0 || !settingsLoaded) return;
    setDanmakuTriggered(true);
    const list = entries.slice(0, 15).map((e, i) => ({
      id: e.id,
      text: `${e.nickname}：${e.content}`,
      top: 10 + (i % 8) * 11,
      duration: 10 + Math.random() * 5,
    }));
    setDanmakuList(list);
    const timer = setTimeout(() => setDanmakuList([]), 18000);
    return () => clearTimeout(timer);
  }, [entries, settingsLoaded]);

  // Background image carousel
  useEffect(() => {
    const urls = settings.bgImageUrls || [];
    if (urls.length <= 1) return;
    const interval = (settings.bgInterval || 5) * 1000;
    const timer = setInterval(() => {
      setBgIndex(prev => (prev + 1) % urls.length);
    }, interval);
    return () => clearInterval(timer);
  }, [settings.bgImageUrls, settings.bgInterval]);

  // Floating emoji waterfall
  useEffect(() => {
    if (!settingsLoaded || !settings.enableFloating) return;
    const parent = document.body;
    const els: HTMLDivElement[] = [];
    for (let i = 0; i < FLOAT_COUNT; i++) {
      const el = document.createElement('div');
      el.className = 'floating';
      el.textContent = EMOJIS[i % EMOJIS.length];
      el.style.left = (Math.random() * 100) + '%';
      el.style.fontSize = (Math.random() * 18 + 18) + 'px';
      const dur = Math.random() * 5 + 7;
      el.style.animation = `floatUp ${dur}s linear infinite`;
      el.style.animationDelay = -(Math.random() * dur) + 's';
      parent.appendChild(el);
      els.push(el);
    }
    return () => els.forEach(el => el.remove());
  }, [settingsLoaded, settings.enableFloating]);

  // Fireworks pool
  useEffect(() => {
    const parent = document.body;
    const pool: HTMLDivElement[] = [];
    for (let i = 0; i < FW_POOL_SIZE; i++) {
      const el = document.createElement('div');
      el.style.cssText = 'position:fixed;border-radius:50%;pointer-events:none;z-index:999;will-change:transform,opacity;';
      parent.appendChild(el);
      pool.push(el);
    }
    fwPoolRef.current = pool;
    return () => pool.forEach(el => el.remove());
  }, []);

  const launchFirework = useCallback((bx: number, by: number) => {
    const pool = fwPoolRef.current;
    for (let i = 0; i < FW_POOL_SIZE; i++) {
      const el = pool[i];
      const angle = (Math.PI * 2 * i) / FW_POOL_SIZE + (Math.random() - 0.5) * 0.5;
      const speed = Math.random() * 5 + 3;
      const vx = Math.cos(angle) * speed;
      const vy = Math.sin(angle) * speed;
      const size = Math.random() * 3 + 3;
      const hue = Math.random() * 360;
      const tx = vx * 40;
      const ty = vy * 40 + 20;
      el.style.left = bx + 'px';
      el.style.top = by + 'px';
      el.style.width = size + 'px';
      el.style.height = size + 'px';
      el.style.background = `hsl(${hue},85%,60%)`;
      el.style.boxShadow = `0 0 8px hsl(${hue},85%,60%)`;
      el.style.opacity = '1';
      el.style.transition = 'none';
      el.style.transform = 'translate(0,0) scale(1.2)';
      void el.offsetWidth;
      el.style.transition = 'transform 1.1s cubic-bezier(0.22,0.61,0.36,1), opacity 1.1s ease-out';
      el.style.transform = `translate(${tx}px,${ty}px) scale(0)`;
      el.style.opacity = '0';
    }
  }, []);

  const launchDouble = useCallback(() => {
    const h1 = h1Ref.current;
    if (!h1) return;
    const r = h1.getBoundingClientRect();
    const cy = r.top + r.height / 2;
    launchFirework(r.left - 40, cy);
    setTimeout(() => launchFirework(r.right + 40, cy), 200);
  }, [launchFirework]);

  useEffect(() => {
    const timer = setTimeout(launchDouble, 500);
    return () => clearTimeout(timer);
  }, [launchDouble]);

  // Mouse trail - small stars
  useEffect(() => {
    if ('ontouchstart' in window) return;
    const parent = document.body;
    const dots: HTMLDivElement[] = [];
    const trail = trailRef.current;
    for (let i = 0; i < TRAIL_LEN; i++) {
      trail.push({ x: -100, y: -100 });
      const dot = document.createElement('div');
      const sz = (1 - i / TRAIL_LEN) * 12 + 13;
      dot.textContent = '✦';
      dot.style.cssText = `position:fixed;pointer-events:none;z-index:998;font-size:${sz}px;color:hsl(45,90%,65%);text-shadow:0 0 ${sz / 2}px hsl(45,90%,75%);will-change:transform;line-height:1;`;
      parent.appendChild(dot);
      dots.push(dot);
    }
    const onMouseMove = (e: MouseEvent) => {
      mouseRef.current = { x: e.clientX, y: e.clientY };
    };
    document.addEventListener('mousemove', onMouseMove);
    let raf: number;
    const update = () => {
      trail[0].x = mouseRef.current.x;
      trail[0].y = mouseRef.current.y;
      for (let i = 1; i < TRAIL_LEN; i++) {
        trail[i].x += (trail[i - 1].x - trail[i].x) * 0.15;
        trail[i].y += (trail[i - 1].y - trail[i].y) * 0.15;
      }
      for (let i = 0; i < TRAIL_LEN; i++) {
        dots[i].style.left = trail[i].x + 'px';
        dots[i].style.top = trail[i].y + 'px';
        dots[i].style.opacity = String((1 - i / TRAIL_LEN) * 0.85);
      }
      raf = requestAnimationFrame(update);
    };
    raf = requestAnimationFrame(update);
    return () => {
      document.removeEventListener('mousemove', onMouseMove);
      cancelAnimationFrame(raf);
      dots.forEach(el => el.remove());
      trail.length = 0;
    };
  }, []);

  const handleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      setToastMsg('图片不能超过 5MB');
      setTimeout(() => setToastMsg(''), 3000);
      return;
    }
    setImageFile(file);
    const reader = new FileReader();
    reader.onload = () => setImagePreview(reader.result as string);
    reader.readAsDataURL(file);
  };

  const handleSubmit = async () => {
    const trimmedNick = nickname.trim();
    const trimmedContent = content.trim();
    if (!trimmedNick) {
      setToastMsg('请输入署名');
      setTimeout(() => setToastMsg(''), 3000);
      return;
    }
    if (!trimmedContent) {
      setToastMsg('请输入留言内容');
      setTimeout(() => setToastMsg(''), 3000);
      return;
    }
    setLoading(true);
    try {
      let imageUrl: string | undefined;
      if (imageFile) {
        const res = await blessApi.uploadImage(imageFile);
        imageUrl = res.url;
      }
      const tempId = generateTempId();
      const entry = await blessApi.create(trimmedContent, imageUrl, trimmedNick, tempId, username);
      sessionStorage.setItem(`bl_temp_${entry.id}`, tempId);
      setEntries(prev => [entry, ...prev]);
      setMyIds(prev => new Set(prev).add(entry.id));
      setContent('');
      setImageFile(null);
      setImagePreview(null);
      if (fileRef.current) fileRef.current.value = '';
      if (settings.replyMessage) {
        setReplyDanmaku(settings.replyMessage);
        setTimeout(() => setReplyDanmaku(''), 4000);
      } else {
        setToastMsg('留言成功！');
        setTimeout(() => setToastMsg(''), 3000);
      }
    } catch (err: unknown) {
      setToastMsg(err instanceof Error ? err.message : '留言失败');
      setTimeout(() => setToastMsg(''), 3000);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: number) => {
    const entry = entries.find(e => e.id === id);
    setConfirmDelete({ id, nick: entry?.nickname || '', content: entry?.content || '' });
  };

  const doDelete = async () => {
    if (!confirmDelete) return;
    const id = confirmDelete.id;
    setConfirmDelete(null);
    try {
      const tempId = sessionStorage.getItem(`bl_temp_${id}`);
      if (tempId) {
        await blessApi.removeByTempId(id, tempId);
        sessionStorage.removeItem(`bl_temp_${id}`);
      } else {
        await blessApi.remove(id);
      }
      setEntries(prev => prev.filter(e => e.id !== id));
      setMyIds(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      setToastMsg('欢迎再留言哦。');
      setTimeout(() => setToastMsg(''), 3000);
    } catch (err: unknown) {
      setToastMsg(err instanceof Error ? err.message : '删除失败');
      setTimeout(() => setToastMsg(''), 3000);
    }
  };

  if (notFound) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f0f2f5' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 64, lineHeight: 1 }}>😢</div>
          <div style={{ fontSize: 18, color: '#666', marginTop: 8 }}>留言板不存在或已关闭</div>
        </div>
      </div>
    );
  }

  return (
    <>
      <style>{`
        @keyframes gradientBG {
          0% { background-position: 0% 50%; }
          25% { background-position: 50% 0%; }
          50% { background-position: 100% 50%; }
          75% { background-position: 50% 100%; }
          100% { background-position: 0% 50%; }
        }
        @keyframes floatUp {
          0% { transform: translateY(0) rotate(0deg); opacity: 0.85; }
          100% { transform: translateY(-110vh) rotate(360deg); opacity: 0.85; }
        }
        @keyframes bounce {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-10px); }
        }
        .floating {
          position: fixed;
          pointer-events: none;
          z-index: 0;
          bottom: -40px;
          will-change: transform;
        }
        .mb-container {
          min-height: 100vh;
          display: flex;
          justify-content: center;
          font-family: "Microsoft YaHei", "华文行楷", sans-serif;
          cursor: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='32' height='32'%3E%3Ctext y='24' font-size='24'%3E⭐%3C/text%3E%3C/svg%3E") 16 16, auto;
          background-size: 400% 400%;
          overflow-x: hidden;
          padding: 40px 20px 60px;
        }
        .mb-content {
          position: relative;
          z-index: 2;
          width: 90%;
          max-width: 650px;
          text-align: center;
        }
        .mb-title {
          font-size: 3.2em;
          color: #e17055;
          text-shadow: 2px 2px 0 #fff, 4px 4px 0 rgba(255,255,255,0.3);
          margin-bottom: 10px;
          animation: bounce 2s ease infinite;
        }
        .mb-subtitle {
          font-size: 1.3em;
          color: #6c5ce7;
          font-weight: bold;
          margin-bottom: 30px;
        }
        .mb-blessing-box {
          background: rgba(255, 255, 255, 0.65);
          backdrop-filter: blur(8px);
          border-radius: 16px;
          padding: 20px 30px;
          position: relative;
        }
        .mb-blessing-box::before, .mb-blessing-box::after {
          content: '🎀 ✨ 🌸 ✨ 🎀';
          position: absolute;
          left: 0; right: 0;
          text-align: center;
          font-size: 14px;
          letter-spacing: 8px;
        }
        .mb-blessing-box::before { top: -20px; }
        .mb-blessing-box::after { bottom: -20px; }
        .mb-blessing {
          font-size: 1.35em;
          line-height: 2.2;
          color: #2d3436;
          margin: 0;
        }
        .mb-blessing strong { color: #e17055; font-weight: 800; }
        .mb-divider {
          height: 3px;
          background: linear-gradient(90deg, #fd79a8, #6c5ce7, #00b894, #fdcb6e, #e17055);
          border-radius: 2px;
          margin: 36px auto;
          width: 60%;
        }
        .mb-footer {
          font-size: 1em;
          color: #636e72;
          margin-top: 16px;
          font-style: italic;
        }
        .mb-tag {
          display: inline-block;
          background: linear-gradient(135deg, #fd79a8, #6c5ce7);
          color: #fff;
          padding: 6px 18px;
          border-radius: 20px;
          font-size: 0.85em;
          margin-top: 14px;
          letter-spacing: 1px;
        }
        .mb-section-title {
          font-size: 1.5em;
          color: #6c5ce7;
          margin: 40px 0 20px;
          font-weight: bold;
        }
        .mb-form {
          background: rgba(255,255,255,0.65);
          backdrop-filter: blur(8px);
          border-radius: 16px;
          padding: 24px;
          text-align: left;
        }
        .mb-input-wrap {
          position: relative;
          margin-bottom: 12px;
        }
        .mb-input-wrap textarea {
          padding-left: 52px !important;
          padding-right: 12px !important;
        }
        .mb-img-btn {
          position: absolute;
          top: 10px;
          left: 10px;
          z-index: 1;
          cursor: pointer;
          width: 34px;
          height: 34px;
          border-radius: 10px;
          border: 2px dashed #ccc;
          display: flex;
          align-items: center;
          justify-content: center;
          color: #bbb;
          font-size: 22px;
          line-height: 1;
          transition: all 0.2s;
        }
        .mb-img-btn:hover {
          border-color: #6c5ce7;
          color: #6c5ce7;
        }
        .mb-img-thumb {
          width: 30px;
          height: 30px;
          border-radius: 8px;
          object-fit: cover;
          cursor: pointer;
        }
        .mb-entries {
          margin-top: 24px;
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .mb-entry {
          background: rgba(255,255,255,0.75);
          backdrop-filter: blur(8px);
          border-radius: 14px;
          padding: 16px 20px;
          text-align: left;
          border-left: 4px solid;
          transition: transform 0.2s;
          position: relative;
        }
        .mb-entry:hover { transform: translateX(4px); }
        .mb-entry-nick {
          font-weight: 700;
          font-size: 0.95em;
          margin-bottom: 4px;
        }
        .mb-entry-content {
          font-size: 1.05em;
          color: #2d3436;
          line-height: 1.6;
          word-break: break-word;
        }
        .mb-entry-image {
          margin-top: 8px;
          max-width: 100%;
          max-height: 240px;
          border-radius: 10px;
          object-fit: cover;
        }
        .mb-entry-time {
          font-size: 0.78em;
          color: #999;
          margin-top: 6px;
        }
        .mb-entry-del {
          position: absolute;
          top: 12px;
          right: 12px;
          cursor: pointer;
          color: #ccc;
          transition: color 0.2s;
          font-size: 16px;
        }
        .mb-entry-del:hover { color: #ff4d4f; }
        @media (max-width: 480px) {
          .mb-title { font-size: 2.2em; }
          .mb-blessing { font-size: 1.1em; }
        }
      `}</style>

      <div
        className="mb-container"
        style={{
          background: settings.enableGradient
            ? 'linear-gradient(135deg, #ffecd2, #fcb69f, #ffeaa7, #dfe6e9, #a29bfe, #fd79a8)'
            : '#ffecd2',
          backgroundSize: settings.enableGradient ? '400% 400%' : undefined,
          animation: settings.enableGradient ? 'gradientBG 12s ease infinite' : undefined,
          ...(settings.bgImageUrls && settings.bgImageUrls.length > 0 ? {
            backgroundImage: `url(${API_HOST}${settings.bgImageUrls[bgIndex % settings.bgImageUrls.length]})`,
            backgroundSize: '100vw auto',
            backgroundPosition: 'center top',
            backgroundRepeat: 'no-repeat',
          } : settings.bgImageUrl ? {
            backgroundImage: `url(${API_HOST}${settings.bgImageUrl})`,
            backgroundSize: '100vw auto',
            backgroundPosition: 'center top',
            backgroundRepeat: 'no-repeat',
          } : {}),
        }}
      >
        <div className="mb-content" ref={containerRef}>
          <h1 className="mb-title" ref={h1Ref} onMouseEnter={launchDouble} style={{ color: settings.titleColor }}>
            {settings.title}
          </h1>
          <div className="mb-subtitle" style={{ color: settings.subtitleColor }}>{settings.subtitle}</div>

          <div className="mb-blessing-box">
            {settings.blessings.map((b, i) => (
              <div key={i} className="mb-blessing" style={{ color: b.color }}>
                {b.bold ? <strong>{b.text}</strong> : b.text}
              </div>
            ))}
          </div>

          <div className="mb-divider" />
          <div className="mb-footer">{settings.footerText}</div>
          <div className="mb-tag">🎉 Congratulations 🎉</div>

          <div className="mb-section-title">💌 留言板</div>

          <div className="mb-form">
            <div className="mb-input-wrap">
              <TextArea
                placeholder="离开页面前，您可以删除自己的留言。"
                autoSize={{ minRows: 2, maxRows: 5 }}
                value={content}
                onChange={e => setContent(e.target.value)}
                style={{ borderRadius: 10 }}
              />
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                style={{ display: 'none' }}
                onChange={handleImageChange}
              />
              <span className="mb-img-btn" onClick={() => fileRef.current?.click()}>
                {imagePreview ? (
                  <img
                    src={imagePreview}
                    alt="preview"
                    className="mb-img-thumb"
                    title="点击移除"
                    onClick={(e) => {
                      e.stopPropagation();
                      setImageFile(null);
                      setImagePreview(null);
                      if (fileRef.current) fileRef.current.value = '';
                    }}
                  />
                ) : (
                  '+'
                )}
              </span>
            </div>

            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <Input
                placeholder="署名（20字以内）"
                maxLength={20}
                value={nickname}
                onChange={e => setNickname(e.target.value)}
                style={{ borderRadius: 10, flex: 1 }}
              />
              <Button
                type="primary"
                icon={<SendOutlined />}
                loading={loading}
                onClick={handleSubmit}
                style={{
                  background: 'linear-gradient(135deg, #fd79a8, #6c5ce7)',
                  border: 'none',
                  borderRadius: 10,
                  height: 40,
                  minWidth: 110,
                }}
              >
                发送祝福
              </Button>
            </div>
          </div>

          {fetching ? (
            <div style={{ marginTop: 30 }}><Spin /></div>
          ) : (
            <div className="mb-entries">
              {entries.length === 0 && (
                <div style={{ color: '#999', fontSize: 14, marginTop: 10 }}>还没有留言，来做第一个送上祝福的人吧~</div>
              )}
              {entries.map(entry => (
                <div
                  key={entry.id}
                  className="mb-entry"
                  style={{ borderLeftColor: entry.color }}
                >
                  {myIds.has(entry.id) && (
                    <span
                      className="mb-entry-del"
                      onClick={() => handleDelete(entry.id)}
                      title="删除"
                    >
                      <DeleteOutlined />
                    </span>
                  )}
                  <div className="mb-entry-content">{entry.content}</div>
                  {entry.imageUrl && (
                    <img
                      src={`${API_HOST}${entry.imageUrl}`}
                      alt="留言图片"
                      className="mb-entry-image"
                    />
                  )}
                  <div className="mb-entry-nick" style={{ color: entry.color }}>
                    — {entry.nickname}
                  </div>
                  <div className="mb-entry-time">
                    {new Date(entry.createdAt).toLocaleString('zh-CN')}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {confirmDelete && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.3)', backdropFilter: 'blur(4px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 10000,
        }} onClick={() => setConfirmDelete(null)}>
          <div style={{
            background: '#fff', borderRadius: 16, padding: '24px 28px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.15)',
            maxWidth: 320, width: '85%', textAlign: 'center',
          }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>🗑️</div>
            <div style={{ fontSize: 16, fontWeight: 500, color: '#333', marginBottom: 4 }}>
              {confirmDelete.nick}：{confirmDelete.content}
            </div>
            <div style={{ fontSize: 14, color: '#888', marginBottom: 20 }}>
              确定要删除这条留言吗？
            </div>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
              <button
                onClick={() => setConfirmDelete(null)}
                style={{
                  flex: 1, padding: '10px 0', borderRadius: 10, border: '1px solid #ddd',
                  background: '#fff', color: '#666', fontSize: 14, cursor: 'pointer',
                }}
              >
                取消
              </button>
              <button
                onClick={doDelete}
                style={{
                  flex: 1, padding: '10px 0', borderRadius: 10, border: 'none',
                  background: 'linear-gradient(135deg, #ff6b6b, #ee5a24)',
                  color: '#fff', fontSize: 14, cursor: 'pointer', fontWeight: 500,
                }}
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}

      {replyDanmaku && (
        <div style={{
          position: 'fixed', left: '50%', top: '50%',
          transform: 'translate(-50%, -50%)',
          zIndex: 9999, pointerEvents: 'none',
        }}>
          <div style={{
            background: settings.replyBgColor || '#fff5eb',
            color: settings.replyTextColor || '#8b3a1a',
            fontSize: 16, fontWeight: 500,
            padding: '12px 28px', borderRadius: 22,
            boxShadow: `0 4px 20px ${settings.replyBgColor || '#fff5eb'}66`,
            border: `1px solid ${settings.replyBgColor || '#fff5eb'}88`,
            animation: 'replyBubble 5s ease-out forwards',
            whiteSpace: 'nowrap',
          }}>
            {replyDanmaku}
          </div>
          <style>{`@keyframes replyBubble { 0% { opacity: 0; transform: translateY(20px) scale(0.9); } 12% { opacity: 1; transform: translateY(0) scale(1); } 65% { opacity: 1; } 100% { opacity: 0; transform: translateY(-40vh); } }`}</style>
        </div>
      )}

      {toastMsg && (
        <div style={{
          position: 'fixed', left: '50%', top: 50,
          transform: 'translateX(-50%)',
          zIndex: 9999, pointerEvents: 'none',
        }}>
          <div style={{
            background: 'rgba(0,0,0,0.55)',
            color: '#fff', fontSize: 13,
            padding: '6px 16px', borderRadius: 14,
            animation: 'toastFade 3s ease-out forwards',
            whiteSpace: 'nowrap',
          }}>
            {toastMsg}
          </div>
          <style>{`@keyframes toastFade { 0% { opacity: 0; transform: translateY(-8px); } 10% { opacity: 1; transform: translateY(0); } 75% { opacity: 1; } 100% { opacity: 0; } }`}</style>
        </div>
      )}

      {danmakuList.map(d => (
        <div key={`${d.id}-${d.top}`} style={{
          position: 'fixed', top: `${d.top}%`, left: 0,
          zIndex: 9998, pointerEvents: 'none',
          animation: `danmakuScroll ${d.duration}s linear forwards`,
          whiteSpace: 'nowrap',
        }}>
          <span style={{
            background: 'rgba(255,255,255,0.55)',
            backdropFilter: 'blur(4px)',
            color: 'rgba(108,58,42,0.7)',
            fontSize: 13,
            padding: '4px 12px', borderRadius: 12,
            border: '1px solid rgba(252,182,159,0.2)',
          }}>
            {d.text}
          </span>
        </div>
      ))}
      <style>{`@keyframes danmakuScroll { 0% { transform: translateX(100vw); } 100% { transform: translateX(-100%); } }`}</style>
    </>
  );
}
