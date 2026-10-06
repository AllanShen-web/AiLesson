'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
  Card,
  Form,
  Input,
  InputNumber,
  Switch,
  Button,
  message,
  Spin,
  Typography,
  Alert,
  Divider,
} from 'antd';
import { SaveOutlined, ApiOutlined, CameraOutlined } from '@ant-design/icons';
import { getAiConfig, updateAiConfig, testAiConnection, getAvatarUrl, uploadAvatar } from '@/lib/aiChatApi';
import { useAuth } from '@/contexts/AuthContext';
import NoAccess from '@/components/NoAccess';

const { Title } = Typography;

export default function AiConfigPage() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [avatarKey, setAvatarKey] = useState(0);
  const [uploadingAvatar, setUploadingAvatar] = useState(false);
  const [noApiKey, setNoApiKey] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const { hasPermission, loading: authLoading } = useAuth();
  const canView = hasPermission('ai_config:view');
  const canUpdate = hasPermission('ai_config:update');
  const canUploadAvatar = hasPermission('ai_config:upload_avatar');

  useEffect(() => {
    if (canView) loadConfig();
  }, [canView]);

  const loadConfig = async () => {
    try {
      const data = await getAiConfig();
      if (data) {
        form.setFieldsValue(data);
        // 后端把未配置的密钥掩码成空串，空串就是「还没有 Key」
        setNoApiKey(!data.apiKey);
      } else {
        setNoApiKey(true);
      }
    } catch {
      message.error('加载配置失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      await updateAiConfig(values);
      await loadConfig();
      message.success('配置已保存');
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    try {
      setTesting(true);
      const result = await testAiConnection();
      if (result.success) {
        message.success(result.message);
      } else {
        message.error(result.message);
      }
    } catch {
      message.error('测试连接失败');
    } finally {
      setTesting(false);
    }
  };

  const handleAvatarUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      setUploadingAvatar(true);
      await uploadAvatar(file);
      setAvatarKey((k) => k + 1);
      message.success('头像已更新');
    } catch {
      message.error('上传头像失败');
    } finally {
      setUploadingAvatar(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  if (authLoading) return null;
  if (!canView) return <NoAccess />;

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <Title level={4} style={{ marginBottom: 24 }}>
        AI 模型配置
      </Title>

      {noApiKey && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="尚未配置 API Key"
          description="AI 助手与流式对话暂不可用。请在下方填写模型 API Key 并保存，然后点击「测试连接」确认配置生效。"
        />
      )}

      <Card>
        {/* 头像上传区域 */}
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleAvatarUpload} />
          <div
            onClick={() => (canUploadAvatar ? fileRef.current?.click() : message.warning('没有上传头像的权限'))}
            style={{
              width: 80,
              height: 80,
              borderRadius: '50%',
              background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              position: 'relative',
              overflow: 'hidden',
              border: '3px solid #f0f0f0',
            }}
          >
            <img
              key={avatarKey}
              src={`${getAvatarUrl()}?t=${avatarKey}`}
              alt="AI"
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: avatarKey ? 'block' : 'none' }}
              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
              onLoad={(e) => { (e.target as HTMLImageElement).style.display = 'block'; }}
            />
            {avatarKey === 0 && (
              <span style={{ color: '#fff', fontSize: 28, fontWeight: 600, pointerEvents: 'none' }}>AI</span>
            )}
            <div
              style={{
                position: 'absolute',
                bottom: 0,
                left: 0,
                right: 0,
                background: 'rgba(0,0,0,0.5)',
                padding: '2px 0',
                display: 'flex',
                justifyContent: 'center',
              }}
            >
              <CameraOutlined style={{ color: '#fff', fontSize: 14 }} />
            </div>
          </div>
          <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
            {uploadingAvatar ? '上传中...' : '点击更换头像'}
          </div>
        </div>

        <Form
          form={form}
          layout="vertical"
          initialValues={{
            provider: 'aliyun',
            modelName: 'qwen3.7-plus',
            embeddingModelName: 'text-embedding-v3',
            embeddingBaseUrl: '',
            embeddingApiKey: '',
            baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
            apiKey: '',
            maxTokens: 4096,
            temperature: 0.7,
            isActive: true,
            systemPrompt: '你是Ailan智慧教案平台的智慧助手布布',
            enableThinking: false,
          }}
        >
          <Form.Item
            label="供应商"
            name="provider"
            rules={[{ required: true, message: '请输入供应商标识' }]}
          >
            <Input placeholder="如: aliyun, openai, deepseek" />
          </Form.Item>

          <Form.Item
            label="模型名称"
            name="modelName"
            rules={[{ required: true, message: '请输入模型名称' }]}
          >
            <Input placeholder="如: deepseek-v3, gpt-4o" />
          </Form.Item>

          <Form.Item
            label="API 地址"
            name="baseUrl"
            rules={[{ required: true, message: '请输入 API 地址' }]}
          >
            <Input placeholder="如: https://api.example.com/v1" />
          </Form.Item>

          <Form.Item
            label="API Key"
            name="apiKey"
            extra="已存的密钥只显示尾 6 位，保持这串 **** 不变即沿用原值"
            rules={[{ required: true, message: '请输入 API Key' }]}
          >
            <Input.Password placeholder="sk-..." />
          </Form.Item>

          <Divider titlePlacement="left" plain style={{ margin: '8px 0 16px' }}>
            向量模型（RAG 向量化用）
          </Divider>

          <Form.Item
            label="嵌入模型名称"
            name="embeddingModelName"
            extra="需支持 OpenAI 兼容 /embeddings 端点且输出 1024 维，如阿里 text-embedding-v3"
            rules={[{ required: true, message: '请输入嵌入模型名称' }]}
          >
            <Input placeholder="如: text-embedding-v3" />
          </Form.Item>

          <Form.Item
            label="向量 API 地址"
            name="embeddingBaseUrl"
            extra="留空则复用上方聊天的 API 地址；聊天供应商没有嵌入端点时（如阶跃）必填，填支持向量模型的兼容地址"
          >
            <Input placeholder="如: https://dashscope.aliyuncs.com/compatible-mode/v1" />
          </Form.Item>

          <Form.Item
            label="向量 API Key"
            name="embeddingApiKey"
            extra="留空则复用上方聊天的 API Key"
          >
            <Input.Password placeholder="sk-..." />
          </Form.Item>

          <Form.Item label="最大输出 Token" name="maxTokens">
            <InputNumber min={1} max={32768} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item label="温度 (Temperature)" name="temperature">
            <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item label="启用" name="isActive" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Form.Item label="深度思考" name="enableThinking" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Form.Item
            label="系统提示词"
            name="systemPrompt"
            extra="定义AI助手的角色和行为规范，修改后立即生效"
          >
            <Input.TextArea
              rows={6}
              placeholder="例如：你是Ailan智慧教案平台的智慧助手布布，你擅长帮助教师编写教案、设计教学活动..."
              style={{ resize: 'vertical' }}
            />
          </Form.Item>

          <div style={{ display: 'flex', gap: 12 }}>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={handleSave}
              loading={saving}
              disabled={!canUpdate}
            >
              保存配置
            </Button>
            <Button
              icon={<ApiOutlined />}
              onClick={handleTest}
              loading={testing}
            >
              测试连接
            </Button>
          </div>
        </Form>
      </Card>
    </div>
  );
}
