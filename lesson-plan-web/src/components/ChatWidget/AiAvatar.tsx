'use client';

import React, { useState } from 'react';
import { getAvatarUrl } from '@/lib/aiChatApi';

interface AiAvatarProps {
  size?: number;
  fontSize?: number;
  style?: React.CSSProperties;
}

export default function AiAvatar({ size = 32, fontSize = 14, style }: AiAvatarProps) {
  const [imgError, setImgError] = useState(false);
  const [imgLoaded, setImgLoaded] = useState(false);

  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: imgLoaded ? 'transparent' : 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        overflow: 'hidden',
        ...style,
      }}
    >
      {!imgError && (
        <img
          src={getAvatarUrl()}
          alt="AI"
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            display: imgLoaded ? 'block' : 'none',
          }}
          onError={() => setImgError(true)}
          onLoad={() => setImgLoaded(true)}
        />
      )}
      {(imgError || !imgLoaded) && (
        <span style={{ color: '#fff', fontSize, fontWeight: 600 }}>AI</span>
      )}
    </div>
  );
}
