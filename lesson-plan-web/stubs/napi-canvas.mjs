// @napi-rs/canvas 是 Node 原生模块，仅供 Node 环境兜底。
// 浏览器环境由 emf-converter 优先使用 OffscreenCanvas / document.createElement('canvas')，不会走到该模块。
// 打包时用 resolveAlias 指向此空 stub，避免 Turbopack/Webpack 尝试解析原生模块而报错。
export {};
