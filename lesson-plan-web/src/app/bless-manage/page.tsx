'use client';

import { useEffect, useState, useRef } from 'react';
import { Tabs, Table, Button, Popconfirm, Input, Switch, Space, Typography, message, Spin, ColorPicker, Card, Row, Col, Tag, QRCode, Modal } from 'antd';
import { DeleteOutlined, SaveOutlined, PlusOutlined, EyeInvisibleOutlined, EyeOutlined, CopyOutlined } from '@ant-design/icons';
import { useAuth } from '@/contexts/AuthContext';
import { type BlessEntry, type BlessSettings, type BlessSettingRecord } from '@/lib/api';
import { blessApi, blessSettingApi } from '@/lib/blessApi';
import { API_HOST } from '@/lib/env';
import { useMobile } from '@/hooks/useMobile';

const { Title, Text } = Typography;

const DEFAULT_SETTINGS: BlessSettings = {
  title: '🎓 毕业快乐！',
  titleColor: '#e17055',
  subtitle: '— 同学 —',
  subtitleColor: '#6c5ce7',
  blessings: [
    { text: '热烈庆祝同学大学毕业！', color: '#e17055', bold: true },
    { text: '前程似锦，未来可期！✨', color: '#2d3436', bold: false },
  ],
  footerText: '愿星光照亮你的征途，荣耀与你同在 🌟',
  enableFloating: true,
  enableGradient: true,
  bgImageUrl: null,
  bgImageUrls: [],
  bgInterval: 5,
  replyMessage: '',
  replyBgColor: '#fff5eb',
  replyTextColor: '#8b3a1a',
};

function getPublicBlessUrl(username: string) {
  if (typeof window === 'undefined') return '';
  return `${window.location.origin}/bless?to=${encodeURIComponent(username)}`;
}

export default function BlessManagePage() {
  const { user } = useAuth();
  const isMobile = useMobile();
  useEffect(() => { document.title = '个人留言板管理'; }, []);

  // --- 留言管理 ---
  const [entries, setEntries] = useState<BlessEntry[]>([]);
  const [entriesLoading, setEntriesLoading] = useState(true);

  const loadEntries = async () => {
    setEntriesLoading(true);
    try {
      const data = await blessApi.getMyEntries();
      setEntries(data);
    } catch {
      message.error('加载留言失败');
    } finally {
      setEntriesLoading(false);
    }
  };

  useEffect(() => { loadEntries(); }, []);

  const handleDelete = async (id: number) => {
    try {
      await blessApi.remove(id);
      message.success('删除成功');
      setEntries(prev => prev.filter(e => e.id !== id));
    } catch {
      message.error('删除失败');
    }
  };

  const handleToggleHidden = async (id: number) => {
    try {
      const updated = await blessApi.toggleHidden(id);
      setEntries(prev => prev.map(e => e.id === id ? { ...e, hidden: updated.hidden } : e));
      message.success(updated.hidden ? '已隐藏' : '已显示');
    } catch {
      message.error('操作失败');
    }
  };

  const entryColumns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '署名', dataIndex: 'nickname', width: 100 },
    {
      title: '内容',
      dataIndex: 'content',
      ellipsis: true,
      render: (text: string, record: BlessEntry) => (
        <span style={{ opacity: record.hidden ? 0.4 : 1 }}>{text}</span>
      ),
    },
    {
      title: '图片',
      dataIndex: 'imageUrl',
      width: 80,
      render: (url: string | null) =>
        url ? <img src={`${API_HOST}${url}`} alt="" style={{ width: 48, height: 48, objectFit: 'cover', borderRadius: 6 }} /> : '-',
    },
    {
      title: '状态',
      width: 80,
      render: (_: unknown, record: BlessEntry) =>
        record.hidden ? <Tag color="default">已隐藏</Tag> : <Tag color="success">显示中</Tag>,
    },
    {
      title: '时间',
      dataIndex: 'createdAt',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      width: 140,
      render: (_: unknown, record: BlessEntry) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={record.hidden ? <EyeOutlined /> : <EyeInvisibleOutlined />}
            onClick={() => handleToggleHidden(record.id)}
          >
            {record.hidden ? '显示' : '隐藏'}
          </Button>
          <Popconfirm title="确定删除这条留言？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // --- 样式设置 ---
  const [settings, setSettings] = useState<BlessSettings>(DEFAULT_SETTINGS);
  const [settingRecord, setSettingRecord] = useState<BlessSettingRecord | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const bgFileRef = useRef<HTMLInputElement>(null);

  const loadSettings = async () => {
    setSettingsLoading(true);
    try {
      const data = await blessSettingApi.getMy();
      setSettingRecord(data);
      const s = data.settings;
      if ((!s.bgImageUrls || s.bgImageUrls.length === 0) && s.bgImageUrl) {
        s.bgImageUrls = [s.bgImageUrl];
      }
      if (!s.bgInterval) s.bgInterval = 5;
      setSettings(s);
      setEnabled(data.enabled);
    } catch {
      message.error('加载设置失败');
    } finally {
      setSettingsLoading(false);
    }
  };

  useEffect(() => { loadSettings(); }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await blessSettingApi.update(settings);
      message.success('保存成功');
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleToggleEnabled = async () => {
    try {
      const res = await blessSettingApi.toggleEnabled();
      setEnabled(res.enabled);
      message.success(res.enabled ? '留言板已开启' : '留言板已关闭');
    } catch {
      message.error('操作失败');
    }
  };

  const handleCopyUrl = async () => {
    const url = getPublicBlessUrl(user?.username || '');
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(url);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = url;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }
      message.success('链接已复制');
    } catch {
      message.error('复制失败，请手动复制');
    }
  };

  const updateBlessing = (index: number, field: string, value: unknown) => {
    const newBlessings = [...settings.blessings];
    newBlessings[index] = { ...newBlessings[index], [field]: value };
    setSettings({ ...settings, blessings: newBlessings });
  };

  const addBlessing = () => {
    setSettings({
      ...settings,
      blessings: [...settings.blessings, { text: '新祝福语', color: '#2d3436', bold: false }],
    });
  };

  const removeBlessing = (index: number) => {
    setSettings({
      ...settings,
      blessings: settings.blessings.filter((_, i) => i !== index),
    });
  };

  const handleBgUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if ((settings.bgImageUrls || []).length >= 10) {
      message.warning('最多上传 10 张背景图');
      if (bgFileRef.current) bgFileRef.current.value = '';
      return;
    }
    try {
      const res = await blessApi.uploadImage(file);
      const urls = [...(settings.bgImageUrls || []), res.url];
      setSettings({ ...settings, bgImageUrls: urls, bgImageUrl: urls[0] || null });
      message.success('背景图上传成功');
    } catch {
      message.error('上传失败');
    }
    if (bgFileRef.current) bgFileRef.current.value = '';
  };

  const blessUrl = user ? getPublicBlessUrl(user.username) : '';

  const [qrCardDataUrl, setQrCardDataUrl] = useState('');

  // Preload QR card image
  useEffect(() => {
    if (!blessUrl || !user) return;
    const timer = setTimeout(() => {
      const qrCanvas = document.querySelector('.bless-qr canvas') as HTMLCanvasElement;
      if (!qrCanvas) return;
      const CARD_W = 400, CARD_H = 520, PADDING = 40;
      const canvas = document.createElement('canvas');
      canvas.width = CARD_W;
      canvas.height = CARD_H;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.roundRect(0, 0, CARD_W, CARD_H, 16);
      ctx.fill();
      const drawContent = (avatarImg?: HTMLImageElement) => {
        const avatarSize = 56, avatarX = PADDING, avatarY = PADDING;
        ctx.save();
        ctx.beginPath();
        ctx.arc(avatarX + avatarSize / 2, avatarY + avatarSize / 2, avatarSize / 2, 0, Math.PI * 2);
        ctx.clip();
        if (avatarImg && avatarImg.complete) {
          ctx.drawImage(avatarImg, avatarX, avatarY, avatarSize, avatarSize);
        } else {
          ctx.fillStyle = '#1677ff';
          ctx.fillRect(avatarX, avatarY, avatarSize, avatarSize);
          ctx.fillStyle = '#fff';
          ctx.font = 'bold 24px sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText((user.nickname || user.username || '?')[0], avatarX + avatarSize / 2, avatarY + avatarSize / 2);
        }
        ctx.restore();
        ctx.fillStyle = '#333333';
        ctx.font = 'bold 20px sans-serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(user.nickname || user.username || '', avatarX + avatarSize + 14, avatarY + avatarSize / 2 - 6);
        ctx.fillStyle = '#999999';
        ctx.font = '13px sans-serif';
        ctx.fillText('我的电子留言板', avatarX + avatarSize + 14, avatarY + avatarSize / 2 + 14);
        ctx.strokeStyle = '#eeeeee';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(PADDING, avatarY + avatarSize + 24);
        ctx.lineTo(CARD_W - PADDING, avatarY + avatarSize + 24);
        ctx.stroke();
        const qrSize = 220, qrX = (CARD_W - qrSize) / 2, qrY = avatarY + avatarSize + 44;
        ctx.drawImage(qrCanvas, qrX, qrY, qrSize, qrSize);
        ctx.fillStyle = '#999999';
        ctx.font = '13px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('扫一扫上面的二维码图案，访问我的电子留言板。', CARD_W / 2, qrY + qrSize + 36);
        setQrCardDataUrl(canvas.toDataURL('image/png'));
      };
      const avatarUrl = user.avatar ? `${API_HOST}${user.avatar}` : null;
      if (avatarUrl) {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => drawContent(img);
        img.onerror = () => drawContent();
        img.src = avatarUrl;
      } else {
        drawContent();
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [blessUrl, user]);

  const handleOpenUrl = () => {
    window.open(blessUrl, '_blank');
  };

  const handleDownloadQR = () => {
    if (!qrCardDataUrl) {
      message.info('二维码图片加载中，请稍后再试');
      return;
    }
    if (isMobile) {
      Modal.info({
        title: '长按图片保存',
        content: <img src={qrCardDataUrl} alt="二维码" style={{ width: '100%', borderRadius: 8 }} />,
        width: 340,
        maskClosable: true,
      });
    } else {
      const link = document.createElement('a');
      link.download = `bless-${user?.username || 'qrcode'}.png`;
      link.href = qrCardDataUrl;
      link.click();
    }
  };

  const settingsContent = (
    <div style={{ maxWidth: 700 }}>
      <Row gutter={[24, 0]}>
        <Col span={12}>
          <Card title="标题" size="small" style={{ marginBottom: 16 }}>
            <Input
              value={settings.title}
              onChange={e => setSettings({ ...settings, title: e.target.value })}
              style={{ marginBottom: 8 }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>颜色</Text>
              <ColorPicker
                value={settings.titleColor}
                onChange={(_, hex) => setSettings({ ...settings, titleColor: hex })}
              />
            </div>
          </Card>
        </Col>
        <Col span={12}>
          <Card title="副标题" size="small" style={{ marginBottom: 16 }}>
            <Input
              value={settings.subtitle}
              onChange={e => setSettings({ ...settings, subtitle: e.target.value })}
              style={{ marginBottom: 8 }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>颜色</Text>
              <ColorPicker
                value={settings.subtitleColor}
                onChange={(_, hex) => setSettings({ ...settings, subtitleColor: hex })}
              />
            </div>
          </Card>
        </Col>
      </Row>

      <Card title="祝福语" size="small" style={{ marginBottom: 16 }}>
        {settings.blessings.map((b, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <Input
              value={b.text}
              onChange={e => updateBlessing(i, 'text', e.target.value)}
              style={{ flex: 1 }}
            />
            <ColorPicker
              value={b.color}
              onChange={(_, hex) => updateBlessing(i, 'color', hex)}
            />
            <Switch
              size="small"
              checked={b.bold}
              onChange={v => updateBlessing(i, 'bold', v)}
              checkedChildren="粗"
              unCheckedChildren="细"
            />
            <Button type="text" danger size="small" icon={<DeleteOutlined />} onClick={() => removeBlessing(i)} />
          </div>
        ))}
        <Button type="dashed" size="small" icon={<PlusOutlined />} onClick={addBlessing}>
          添加祝福语
        </Button>
      </Card>

      <Card title="底部文字" size="small" style={{ marginBottom: 16 }}>
        <Input
          value={settings.footerText}
          onChange={e => setSettings({ ...settings, footerText: e.target.value })}
        />
      </Card>

      <Card title="回敬祝福语" size="small" style={{ marginBottom: 16 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>用户留言成功后自动弹出的回敬祝福语（留空则不弹出）</Text>
        <Input
          value={settings.replyMessage}
          onChange={e => setSettings({ ...settings, replyMessage: e.target.value })}
          placeholder="如：感谢您的祝福！🎉"
          style={{ marginTop: 8 }}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>背景色</Text>
            <ColorPicker
              value={settings.replyBgColor}
              onChange={(_, hex) => setSettings({ ...settings, replyBgColor: hex })}
              size="small"
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>文字色</Text>
            <ColorPicker
              value={settings.replyTextColor}
              onChange={(_, hex) => setSettings({ ...settings, replyTextColor: hex })}
              size="small"
            />
          </div>
        </div>
      </Card>

      <Card title="页面效果" size="small" style={{ marginBottom: 16 }}>
        <Space size="large">
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>浮动表情</Text>
            <br />
            <Switch
              checked={settings.enableFloating}
              onChange={v => setSettings({ ...settings, enableFloating: v })}
            />
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>渐变背景</Text>
            <br />
            <Switch
              checked={settings.enableGradient}
              onChange={v => setSettings({ ...settings, enableGradient: v })}
            />
          </div>
        </Space>
      </Card>

      <Card title="背景图片" size="small" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <input ref={bgFileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleBgUpload} />
          <Button onClick={() => bgFileRef.current?.click()}>上传背景图</Button>
          <Space>
            <Text type="secondary" style={{ fontSize: 12 }}>切换间隔(秒)</Text>
            <Input
              type="number"
              value={settings.bgInterval || 5}
              min={1}
              style={{ width: 70 }}
              onChange={e => setSettings({ ...settings, bgInterval: Number(e.target.value) || 5 })}
            />
          </Space>
        </div>
        {(settings.bgImageUrls || []).length > 0 && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
            {(settings.bgImageUrls || []).map((url, i) => (
              <div key={i} style={{ position: 'relative' }}>
                <img
                  src={`${API_HOST}${url}`}
                  alt={`bg${i}`}
                  style={{ width: 80, height: 50, objectFit: 'cover', borderRadius: 6 }}
                />
                <Button
                  type="link"
                  danger
                  size="small"
                  style={{ position: 'absolute', top: -4, right: -4, padding: 0, fontSize: 12 }}
                  onClick={() => {
                    const newUrls = (settings.bgImageUrls || []).filter((_, idx) => idx !== i);
                    setSettings({ ...settings, bgImageUrls: newUrls, bgImageUrl: newUrls[0] || null });
                  }}
                >×</Button>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Button
        type="primary"
        icon={<SaveOutlined />}
        loading={saving}
        onClick={handleSave}
        style={{ background: 'linear-gradient(135deg, #fd79a8, #6c5ce7)', border: 'none' }}
      >
        保存设置
      </Button>
    </div>
  );

  if (settingsLoading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

  return (
    <div>
      <Title level={4} style={{ marginBottom: 16 }}>个人留言板</Title>

      {/* 留言板地址、开关、QR码 - 在 tabs 上方 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap' }}>
          <Space>
            <Text>留言板状态</Text>
            <Switch checked={enabled} onChange={handleToggleEnabled} checkedChildren="开启" unCheckedChildren="关闭" />
          </Space>
          {enabled && blessUrl && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Text>访问地址</Text>
                <Text style={{ color: '#1677ff', fontSize: 13, cursor: 'pointer' }} onClick={handleOpenUrl}>{blessUrl}</Text>
                <Button size="small" icon={<CopyOutlined />} onClick={handleCopyUrl}>复制</Button>
              </div>
              <div className="bless-qr" onClick={handleDownloadQR} style={{ cursor: 'pointer' }} title="点击保存二维码">
                <QRCode value={blessUrl} size={64} />
                {user?.avatar && (
                  <img
                    className="bless-qr-avatar"
                    src={`${API_HOST}${user.avatar}`}
                    crossOrigin="anonymous"
                    style={{ display: 'none' }}
                    alt=""
                  />
                )}
              </div>
            </>
          )}
        </div>
      </Card>

      <Tabs
        items={[
          {
            key: 'entries',
            label: '留言管理',
            children: (
              <div style={{ overflowX: 'auto' }}>
                <Table
                  rowKey="id"
                  columns={entryColumns}
                  dataSource={entries}
                  loading={entriesLoading}
                  pagination={{ pageSize: 20 }}
                  size={isMobile ? 'small' : 'middle'}
                />
              </div>
            ),
          },
          {
            key: 'settings',
            label: '样式设置',
            children: settingsContent,
          },
        ]}
      />
    </div>
  );
}
