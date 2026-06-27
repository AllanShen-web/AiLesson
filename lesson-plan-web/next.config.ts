import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    resolveAlias: {
      '@napi-rs/canvas': './stubs/napi-canvas.mjs',
    },
  },
};

export default nextConfig;
