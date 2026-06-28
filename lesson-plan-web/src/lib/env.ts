/**
 * 后端接口基址：由 .env.development 提供，指向本机跑着的 lesson-plan-server。
 * 缺省值取同一个地址，不配环境变量也能直接 npm run dev。
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:6586/api';
/** 基址去掉 /api 的站点根：后端返回的图片路径是 `/api/images/1` 这种相对形式，拼上它才是可请求的完整地址 */
export const API_HOST = API_BASE.replace(/\/api$/, '');
