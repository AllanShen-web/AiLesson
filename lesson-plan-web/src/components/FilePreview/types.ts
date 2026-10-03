/**
 * 文件预览的数据来源。
 *
 * 两类来源字段不同（上传文件给 mimetype，AI 文件给 format），
 * 所以这里做成联合类型，由组件内部归一化后再分派渲染。
 */
export type PreviewSource =
  | {
      kind: 'url';
      /** 需要带 token 请求的文件地址，组件会 fetch 成 blob 再渲染 */
      url: string;
      fileName: string;
      /** 文件格式（docx/pdf/pptx/md/txt...），优先用它判断渲染方式 */
      format?: string;
      /** 没有 format 时退化为 mimetype 判断 */
      mimetype?: string;
    }
  | {
      kind: 'blob';
      blob: Blob;
      fileName: string;
      format?: string;
      mimetype?: string;
    };

/** 归一化后的格式分类，组件内部使用 */
export type PreviewCategory = 'docx' | 'pdf' | 'pptx' | 'markdown' | 'text' | 'image' | 'unsupported';

const TEXT_FORMATS = new Set(['txt', 'text', 'plain', 'log', 'json', 'csv', 'tsv']);
const MARKDOWN_FORMATS = new Set(['md', 'markdown']);
const IMAGE_FORMATS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp']);
const DOC_FORMATS = new Set(['docx', 'doc']);
const PDF_FORMATS = new Set(['pdf']);
const PPT_FORMATS = new Set(['pptx', 'ppt']);

/** 从文件名取扩展名（小写、不含点） */
export function extOf(fileName: string): string {
  const m = /\.([a-z0-9]+)$/i.exec(fileName || '');
  return m ? m[1].toLowerCase() : '';
}

/**
 * 判断文件能否预览。
 * 三级退化：format → 文件名后缀 → mimetype。
 * 因为上传文件和 AI 文件分别提供 format 与 mimetype，必须都认。
 */
export function detectCategory(source: Pick<PreviewSource, 'fileName' | 'format' | 'mimetype'>): PreviewCategory {
  const byFormat = String(source.format || '').toLowerCase();
  const byExt = extOf(source.fileName);
  const byMime = String(source.mimetype || '').toLowerCase();

  const candidates = [byFormat, byExt].filter(Boolean);
  for (const c of candidates) {
    if (DOC_FORMATS.has(c)) return 'docx';
    if (PDF_FORMATS.has(c)) return 'pdf';
    if (PPT_FORMATS.has(c)) return 'pptx';
    if (MARKDOWN_FORMATS.has(c)) return 'markdown';
    if (TEXT_FORMATS.has(c)) return 'text';
    if (IMAGE_FORMATS.has(c)) return 'image';
  }

  // format / 后缀都没认出来时才看 mimetype
  if (byMime.startsWith('image/')) return 'image';
  if (byMime === 'application/pdf') return 'pdf';
  if (byMime.includes('wordprocessingml') || byMime === 'application/msword') return 'docx';
  if (byMime.includes('presentationml') || byMime === 'application/vnd.ms-powerpoint') return 'pptx';
  if (byMime.startsWith('text/markdown')) return 'markdown';
  if (byMime.startsWith('text/') || byMime === 'application/json' || byMime === 'application/xml') return 'text';

  return 'unsupported';
}
