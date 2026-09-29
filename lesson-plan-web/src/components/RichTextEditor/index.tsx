'use client';

import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import type { CSSProperties, ElementType, ReactNode } from 'react';
import { createEditor, Descendant, Editor, Transforms, Element, Range, Node as SlateNode, Path } from 'slate';
import { withHistory } from 'slate-history';
import { Slate, Editable, withReact, useSlate, RenderLeafProps, RenderElementProps } from 'slate-react';
import isHotkey from 'is-hotkey';
import { previewBoxHeight } from '@/lib/api';

type BlockType =
  | 'paragraph'
  | 'heading-one' | 'heading-two' | 'heading-three'
  | 'heading-four' | 'heading-five' | 'heading-six'
  | 'bulleted-list' | 'numbered-list' | 'list-item'
  | 'block-quote';

type CustomElement = { type: BlockType; children: CustomText[] };
type CustomText = { text: string; bold?: boolean; italic?: boolean; underline?: boolean; strike?: boolean; code?: boolean };

declare module 'slate' {
  interface CustomTypes {
    Element: CustomElement;
    Text: CustomText;
  }
}

/** 工具栏可切换的块级格式（不含 list-item，它是列表的子节点） */
const BLOCK_FORMATS: BlockType[] = [
  'heading-one', 'heading-two', 'heading-three',
  'heading-four', 'heading-five', 'heading-six',
  'bulleted-list', 'numbered-list', 'block-quote',
];

const LIST_TYPES: BlockType[] = ['bulleted-list', 'numbered-list'];

/** 列表的直接子节点只能是 list-item：列表项被改成标题/段落后要让它脱离 ul/ol */
function withLists<T extends Editor>(editor: T): T {
  const { normalizeNode } = editor;
  editor.normalizeNode = (entry) => {
    const [node, path] = entry;
    if (Element.isElement(node) && LIST_TYPES.includes(node.type)) {
      for (const [child, childPath] of SlateNode.children(editor, path)) {
        if (Element.isElement(child) && child.type !== 'list-item') {
          Transforms.liftNodes(editor, { at: childPath });
          return;
        }
      }
    }
    normalizeNode(entry);
  };
  return editor;
}

const blockPathOf = (point: { path: Path }): Path => point.path.slice(0, -1);

function hasNode(editor: Editor, path: Path): boolean {
  const parent = SlateNode.get(editor, path.slice(0, -1)) as Element | Editor;
  return path[path.length - 1] >= 0 && (parent.children as Descendant[]).length > path[path.length - 1];
}

/**
 * 选区端点正好压在某个块的开头 / 结尾时，那个块里其实一个字都没选中（hanging selection）。
 * 把端点收回真正有字符的块，否则按块套格式会多染一块。
 */
function tightenRange(editor: Editor, range: Range): Range {
  let [start, end] = Range.edges(range);
  const startBlock = blockPathOf(start);
  const endBlock = blockPathOf(end);
  if (Path.equals(startBlock, endBlock)) return { anchor: start, focus: end };

  if (Editor.isStart(editor, end, endBlock) && endBlock[endBlock.length - 1] > 0) {
    const prev = Path.previous(endBlock);
    if (hasNode(editor, prev)) end = Editor.end(editor, prev);
  }
  if (Editor.isEnd(editor, start, startBlock)) {
    const next = Path.next(startBlock);
    if (hasNode(editor, next)) start = Editor.start(editor, next);
  }
  return { anchor: start, focus: end };
}

function ToolbarButton({ icon, format, active, onClick }: {
  icon: string;
  format: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      // mousedown 阻止默认行为，避免按钮抢走编辑区焦点导致选区丢失
      onMouseDown={(e) => { e.preventDefault(); onClick(); }}
      title={format}
      style={{
        padding: '4px 8px',
        margin: '0 2px',
        border: active ? '1px solid #1677ff' : '1px solid transparent',
        background: active ? '#e6f4ff' : 'transparent',
        borderRadius: 4,
        cursor: 'pointer',
        fontSize: 14,
        fontWeight: format === 'bold' ? 'bold' : 'normal',
        fontStyle: format === 'italic' ? 'italic' : 'normal',
        textDecoration: format === 'underline' ? 'underline' : 'none',
      }}
    >
      {icon}
    </button>
  );
}

function Toolbar({ editor }: { editor: Editor }) {
  const isMarkActive = useCallback((format: string) => {
    const marks = Editor.marks(editor);
    return marks ? (marks as any)[format] === true : false;
  }, [editor]);

  const toggleMark = useCallback((format: string) => {
    const isActive = isMarkActive(format);
    if (isActive) {
      Editor.removeMark(editor, format as any);
    } else {
      Editor.addMark(editor, format as any, true);
    }
  }, [editor, isMarkActive]);

  /**
   * 选区覆盖的「最内层块」类型，用于工具栏按钮高亮。
   *
   * mode:'lowest' 不能少：不加的话从根开始遍历，根节点本身也满足 isBlock，
   * 根节点类型一进集合，标题按钮就会常亮。
   */
  const activeBlocks = useCallback((): Set<BlockType> => {
    const active = new Set<BlockType>();
    const { selection } = editor;
    if (!selection) return active;

    for (const [node, path] of Editor.nodes(editor, {
      at: selection,
      match: (n) => Element.isElement(n) && Editor.isBlock(editor, n),
      mode: 'lowest',
    })) {
      if (path.length === 0) continue;
      active.add((node as CustomElement).type);
      // 光标落在列表项内时，列表按钮也要高亮
      const [parent] = Editor.parent(editor, path);
      if (Element.isElement(parent) && LIST_TYPES.includes((parent as CustomElement).type)) {
        active.add((parent as CustomElement).type);
      }
    }
    return active;
  }, [editor]);

  const blocks = activeBlocks();
  const isBlockActive = (format: string) => blocks.has(format as BlockType);

  /**
   * 切换块级格式（标题 / 列表 / 引用），只作用于选中内容。
   *
   * 块级格式在 HTML 里是整块容器（不存在「半个标题」），所以做法是：
   * 先收紧选区（去掉端点压在块边界的空块），再按选区两端切开，让选中部分
   * 独占完整的块，最后只对这些块改类型。
   *
   * 三个坑：
   * 1) 不能把 Range 直接交给 Transforms.splitNodes——它收到 Range 会先 deleteRange
   *    把内容删掉，所以按起止两个「点」分别切，并且先切尾再切头（切头会让后面的块索引后移）。
   * 2) 折叠光标不切块：光标停在段落中间时，用户要的是整段变标题（同 Word）。
   * 3) isActive 必须用切分后的块重算。工具栏高亮用的 blocks 是渲染时（切分前）算的，
   *    拿它判断会把「相邻块本来就是标题」误判成当前格式已激活。
   */
  const toggleBlock = useCallback((format: BlockType) => {
    const { selection } = editor;
    if (!selection) return;

    let sel = tightenRange(editor, selection);
    if (!Range.isCollapsed(sel)) {
      const [start, end] = Range.edges(sel);
      const needHead = !Editor.isStart(editor, start, blockPathOf(start));
      const needTail = !Editor.isEnd(editor, end, blockPathOf(end));
      if (needHead || needTail) {
        Transforms.select(editor, sel);
        Editor.withoutNormalizing(editor, () => {
          if (needTail) Transforms.splitNodes(editor, { at: end });
          if (needHead) Transforms.splitNodes(editor, { at: start });
        });
        // 切出来的后半块起点正好是选区终点，要再收紧一次把它排除
        if (editor.selection) sel = tightenRange(editor, editor.selection);
      }
    }
    // 必须走 Transforms.select 而不是直接赋值：赋值不产生 operation，
    // DOM 选区还停在切分前的路径上，下一次输入就会错位
    Transforms.select(editor, sel);

    // mode: 'lowest' 取最内层块——列表项要单独命中 li，不能连 ul 一起改；
    // path 为空的是根节点（它侥幸满足 isBlock），必须排除，否则又是全文变标题
    const selectedBlocks = (range: Range) =>
      Array.from(
        Editor.nodes(editor, {
          at: range,
          match: (n) => Element.isElement(n) && Editor.isBlock(editor, n),
          mode: 'lowest',
        }),
      )
        .map(([, path]) => path)
        .filter((path) => path.length > 0);

    const paths = selectedBlocks(sel);
    if (paths.length === 0) return;

    const isList = LIST_TYPES.includes(format);
    const typeAt = (path: Path) => (SlateNode.get(editor, path) as CustomElement).type;
    const isActive = isList
      ? paths.every(
          (path) =>
            typeAt(path) === 'list-item' &&
            (Editor.parent(editor, path)[0] as CustomElement).type === format,
        )
      : paths.some((path) => typeAt(path) === format);

    Editor.withoutNormalizing(editor, () => {
      if (!isList) {
        for (const path of paths) {
          Transforms.setNodes(editor, { type: isActive ? 'paragraph' : format }, { at: path });
        }
        return;
      }

      if (isActive) {
        Transforms.unwrapNodes(editor, {
          at: sel,
          match: (n) => Element.isElement(n) && LIST_TYPES.includes(n.type),
          mode: 'lowest',
          split: true,
        });
        // unwrap 改动了路径，只能用 Slate 自己维护后的 editor.selection 重新取块
        for (const path of selectedBlocks(editor.selection ?? sel)) {
          Transforms.setNodes(editor, { type: 'paragraph' }, { at: path });
        }
        return;
      }

      for (const path of paths) {
        Transforms.setNodes(editor, { type: 'list-item' }, { at: path });
      }
      // 一把包住整段选区，逐块 wrap 会包出多个 ul
      Transforms.wrapNodes(
        editor,
        { type: format, children: [] } as CustomElement,
        {
          at: Editor.range(editor, paths[0], paths[paths.length - 1]),
          match: (n) => Element.isElement(n) && n.type === 'list-item',
          mode: 'lowest',
        },
      );
    });
  }, [editor]);

  return (
    <div style={{
      display: 'flex',
      gap: 4,
      padding: '8px',
      borderBottom: '1px solid #d9d9d9',
      background: '#fafafa',
      flexWrap: 'wrap',
      alignItems: 'center',
    }}>
      <ToolbarButton icon="H1" format="heading-one" active={isBlockActive('heading-one')} onClick={() => toggleBlock('heading-one')} />
      <ToolbarButton icon="H2" format="heading-two" active={isBlockActive('heading-two')} onClick={() => toggleBlock('heading-two')} />
      <ToolbarButton icon="H3" format="heading-three" active={isBlockActive('heading-three')} onClick={() => toggleBlock('heading-three')} />
      <ToolbarButton icon="H4" format="heading-four" active={isBlockActive('heading-four')} onClick={() => toggleBlock('heading-four')} />
      <ToolbarButton icon="H5" format="heading-five" active={isBlockActive('heading-five')} onClick={() => toggleBlock('heading-five')} />
      <ToolbarButton icon="H6" format="heading-six" active={isBlockActive('heading-six')} onClick={() => toggleBlock('heading-six')} />
      <span style={{ width: 1, height: 20, background: '#d9d9d9', margin: '0 4px' }} />
      <ToolbarButton icon="B" format="bold" active={isMarkActive('bold')} onClick={() => toggleMark('bold')} />
      <ToolbarButton icon="I" format="italic" active={isMarkActive('italic')} onClick={() => toggleMark('italic')} />
      <ToolbarButton icon="U" format="underline" active={isMarkActive('underline')} onClick={() => toggleMark('underline')} />
      <ToolbarButton icon="S" format="strike" active={isMarkActive('strike')} onClick={() => toggleMark('strike')} />
      <span style={{ width: 1, height: 20, background: '#d9d9d9', margin: '0 4px' }} />
      <ToolbarButton icon="• List" format="bulleted-list" active={isBlockActive('bulleted-list')} onClick={() => toggleBlock('bulleted-list')} />
      <ToolbarButton icon="1. List" format="numbered-list" active={isBlockActive('numbered-list')} onClick={() => toggleBlock('numbered-list')} />
      <ToolbarButton icon="❝" format="block-quote" active={isBlockActive('block-quote')} onClick={() => toggleBlock('block-quote')} />
    </div>
  );
}

const renderLeaf = ({ attributes, children, leaf }: RenderLeafProps) => {
  let element = children;
  if ((leaf as any).bold) element = <strong>{element}</strong>;
  if ((leaf as any).italic) element = <em>{element}</em>;
  if ((leaf as any).underline) element = <u>{element}</u>;
  if ((leaf as any).strike) element = <s>{element}</s>;
  if ((leaf as any).code) element = <code style={{ background: '#f5f5f5', padding: '2px 4px', borderRadius: 3 }}>{element}</code>;
  return <span {...attributes}>{element}</span>;
};

/** 块级元素的标签与样式：编辑态和只读预览共用一份，保证两边观感完全一致 */
const BLOCK_RENDER: Record<BlockType, { tag: string; style: CSSProperties }> = {
  paragraph: { tag: 'p', style: { margin: '8px 0', lineHeight: 1.6 } },
  'heading-one': { tag: 'h1', style: { fontSize: 28, fontWeight: 'bold', margin: '16px 0 8px' } },
  'heading-two': { tag: 'h2', style: { fontSize: 24, fontWeight: 'bold', margin: '14px 0 8px' } },
  'heading-three': { tag: 'h3', style: { fontSize: 20, fontWeight: 'bold', margin: '12px 0 8px' } },
  'heading-four': { tag: 'h4', style: { fontSize: 18, fontWeight: 'bold', margin: '10px 0 8px' } },
  'heading-five': { tag: 'h5', style: { fontSize: 16, fontWeight: 'bold', margin: '8px 0 6px' } },
  'heading-six': { tag: 'h6', style: { fontSize: 14, fontWeight: 'bold', margin: '8px 0 6px' } },
  'bulleted-list': { tag: 'ul', style: { paddingLeft: 24, margin: '8px 0' } },
  'numbered-list': { tag: 'ol', style: { paddingLeft: 24, margin: '8px 0' } },
  'list-item': { tag: 'li', style: { margin: '4px 0' } },
  'block-quote': {
    tag: 'blockquote',
    style: { borderLeft: '4px solid #1677ff', paddingLeft: 16, margin: '8px 0', color: '#666', fontStyle: 'italic' },
  },
};

const renderElement = ({ attributes, children, element }: RenderElementProps) => {
  const conf = BLOCK_RENDER[(element as CustomElement).type] || BLOCK_RENDER.paragraph;
  const Tag = conf.tag as ElementType;
  return <Tag {...attributes} style={conf.style}>{children}</Tag>;
};

/** 字数口径同 Word「字符数(不计空格)」：去掉空白后按码点计，中文一个字、英文单词的每个字母都算一个 */
function charCount(text: string): number {
  return [...text.replace(/\s+/g, '')].length;
}

/**
 * 字数条：无选区显示全文，有选区显示选中字数。
 *
 * 必须用 useSlate 订阅（所以渲染在 <Slate> 内部）：光换编辑器内容不会让外层组件重渲染，
 * 而选区移动只产生 set_selection 操作，序列化出来的 HTML 一字不变，父级 setDraft 同值直接 bail out。
 */
function CharCountBar() {
  const editor = useSlate();

  const selection = editor.selection;
  let selected = 0;
  if (selection && !Range.isCollapsed(selection)) {
    // 换字段时父组件直接替换 editor.children，旧 selection 可能指向已不存在的路径，
    // 拿它取字会抛 "Cannot find a descendant at path"；越界 offset 会被自动收钳，不必管
    if (Editor.hasPath(editor, selection.anchor.path) && Editor.hasPath(editor, selection.focus.path)) {
      selected = charCount(Editor.string(editor, selection));
    }
  }

  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'flex-end',
        padding: '4px 12px',
        borderTop: '1px solid #d9d9d9',
        background: '#fafafa',
        fontSize: 12,
        color: selected > 0 ? '#1677ff' : '#8c8c8c',
        fontVariantNumeric: 'tabular-nums',
        userSelect: 'none',
      }}
    >
      {selected > 0 ? `选中 ${selected} 字` : `全文 ${charCount(Editor.string(editor, []))} 字`}
    </div>
  );
}

interface RichTextEditorProps {
  value?: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  readOnly?: boolean;
  height?: number;
}

// ── HTML ↔ Slate ──

const BLOCK_TAG_MAP: Record<string, BlockType> = {
  H1: 'heading-one', H2: 'heading-two', H3: 'heading-three',
  H4: 'heading-four', H5: 'heading-five', H6: 'heading-six',
  UL: 'bulleted-list', OL: 'numbered-list', BLOCKQUOTE: 'block-quote',
};

/**
 * HTML → Slate 节点树。
 * 必须走 DOM 逐节点解析：用 textContent 一把梭时 <li> 之间不会插换行，
 * 列表项会被粘成一段，标题/加粗/列表结构全部丢失。
 */
function htmlToSlate(html: string): Descendant[] {
  const empty: Descendant[] = [{ type: 'paragraph', children: [{ text: '' }] }];
  if (!html) return empty;

  const container = document.createElement('div');
  container.innerHTML = html;
  if (!(container.textContent || '').trim()) return empty;

  const nodes = parseChildren(container);
  return nodes.length > 0 ? nodes : empty;
}

function parseChildren(parent: Node): Descendant[] {
  const out: Descendant[] = [];

  for (const node of Array.from(parent.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent || '';
      // 标签之间的换行/缩进是格式化空白，不产生段落
      if (!text.trim()) continue;
      // 纯文本（AI 生成的教案正文就是 \n 分隔、不带任何标签）必须一行一段。
      // 整段塞进一个 text 节点会让全篇变成同一个顶层块，块级格式（标题/列表/引用）
      // 是按块应用的，于是点 H1 就把全文都变成标题；行内格式（加粗等）作用于
      // 选区内的 text 节点，所以看不出问题。
      for (const line of splitLines(text)) {
        out.push({ type: 'paragraph', children: [{ text: line }] } as Descendant);
      }
      continue;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) continue;

    const el = node as HTMLElement;
    const tag = el.tagName;
    const blockType = BLOCK_TAG_MAP[tag];

    if (tag === 'LI') {
      out.push({ type: 'list-item', children: parseInline(el) } as unknown as Descendant);
      continue;
    }

    if (tag === 'UL' || tag === 'OL') {
      const items = parseChildren(el).filter((n) => (n as any).type === 'list-item');
      if (items.length > 0) out.push({ type: blockType!, children: items } as unknown as Descendant);
      continue;
    }

    if (blockType) {
      out.push({ type: blockType, children: parseInline(el) } as unknown as Descendant);
      continue;
    }

    if (tag === 'BR') {
      out.push({ type: 'paragraph', children: [{ text: '' }] } as Descendant);
      continue;
    }

    if (tag === 'P' || tag === 'DIV') {
      // 按 <br> 切段，否则 "第一行<br>第二行" 会被并成一段
      for (const seg of splitByBr(el)) {
        const children = parseInline(seg);
        out.push({ type: 'paragraph', children } as Descendant);
      }
      continue;
    }

    // 行内标签直接出现在块级容器下，或未知块级标签：递归保留内部结构
    const nested = parseChildren(el);
    if (nested.length > 0) {
      out.push(...nested);
    } else {
      const children = parseInline(el);
      if (children.length > 0) out.push({ type: 'paragraph', children } as Descendant);
    }
  }

  return out;
}

/** 按换行拆段；只掐掉首尾的空行，中间空行保留为空段落（那是真实的段落间隔） */
function splitLines(text: string): string[] {
  const lines = text.split(/\r?\n/);
  while (lines.length > 1 && !lines[0].trim()) lines.shift();
  while (lines.length > 1 && !lines[lines.length - 1].trim()) lines.pop();
  return lines;
}

function splitByBr(el: HTMLElement): HTMLElement[] {
  const segments: HTMLElement[] = [];
  let current = document.createElement('div');
  for (const node of Array.from(el.childNodes)) {
    if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR') {
      segments.push(current);
      current = document.createElement('div');
      continue;
    }
    current.appendChild(node.cloneNode(true));
  }
  segments.push(current);
  return segments;
}

function parseInline(el: Node): CustomText[] {
  const out: CustomText[] = [];

  const walk = (node: Node, marks: Partial<CustomText>) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent || '';
      if (text) out.push({ text, ...marks } as CustomText);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;

    const child = node as HTMLElement;
    const tag = child.tagName;
    if (tag === 'BR') return;

    const nextMarks: Partial<CustomText> = { ...marks };
    if (tag === 'B' || tag === 'STRONG') nextMarks.bold = true;
    else if (tag === 'I' || tag === 'EM') nextMarks.italic = true;
    else if (tag === 'U') nextMarks.underline = true;
    else if (tag === 'S' || tag === 'STRIKE' || tag === 'DEL') nextMarks.strike = true;
    else if (tag === 'CODE') nextMarks.code = true;

    for (const c of Array.from(child.childNodes)) walk(c, nextMarks);
  };

  for (const node of Array.from(el.childNodes)) walk(node, {});

  return out.length > 0 ? out : [{ text: '' }];
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderNodeContent(node: CustomElement): string {
  return node.children.map((child) => {
    const t = child as CustomText;
    // 先转义再套标签，标签本身不能转义
    let text = escapeHtml(t.text);
    if (t.bold) text = `<strong>${text}</strong>`;
    if (t.italic) text = `<em>${text}</em>`;
    if (t.underline) text = `<u>${text}</u>`;
    if (t.strike) text = `<s>${text}</s>`;
    if (t.code) text = `<code>${text}</code>`;
    return text;
  }).join('');
}

function renderListItems(node: CustomElement): string {
  return node.children.map((child) => {
    const item = child as unknown as CustomElement;
    return `<li>${renderNodeContent(item)}</li>`;
  }).join('');
}

function slateToHtml(value: Descendant[]): string {
  if (!value || value.length === 0) return '';

  // 递归判断是否有实际文字：列表节点的 children 是 list-item，文字嵌在更深一层
  const hasText = (nodes: any[]): boolean =>
    nodes.some((n) => {
      if (typeof n.text === 'string' && n.text.length > 0) return true;
      return Array.isArray(n.children) && hasText(n.children);
    });
  if (!hasText(value as any[])) return '';

  let html = '';
  for (const node of value) {
    const el = node as CustomElement;
    switch (el.type) {
      case 'heading-one': html += `<h1>${renderNodeContent(el)}</h1>\n`; break;
      case 'heading-two': html += `<h2>${renderNodeContent(el)}</h2>\n`; break;
      case 'heading-three': html += `<h3>${renderNodeContent(el)}</h3>\n`; break;
      case 'heading-four': html += `<h4>${renderNodeContent(el)}</h4>\n`; break;
      case 'heading-five': html += `<h5>${renderNodeContent(el)}</h5>\n`; break;
      case 'heading-six': html += `<h6>${renderNodeContent(el)}</h6>\n`; break;
      case 'bulleted-list': html += `<ul>\n${renderListItems(el)}\n</ul>\n`; break;
      case 'numbered-list': html += `<ol>\n${renderListItems(el)}\n</ol>\n`; break;
      case 'block-quote': html += `<blockquote>${renderNodeContent(el)}</blockquote>\n`; break;
      default: html += `<p>${renderNodeContent(el)}</p>\n`;
    }
  }
  return html.trim();
}

// ── 只读预览（渲染编辑器导出的 HTML，样式与编辑态同源） ──

const isTextNode = (node: Descendant): node is CustomText =>
  typeof (node as CustomText).text === 'string';

function renderPreviewNodes(nodes: Descendant[]): ReactNode {
  return nodes.map((node, i) => {
    if (isTextNode(node)) {
      let content: ReactNode = node.text;
      if (node.bold) content = <strong>{content}</strong>;
      if (node.italic) content = <em>{content}</em>;
      if (node.underline) content = <u>{content}</u>;
      if (node.strike) content = <s>{content}</s>;
      if (node.code) content = <code style={{ background: '#f5f5f5', padding: '2px 4px', borderRadius: 3 }}>{content}</code>;
      return <span key={i}>{content}</span>;
    }

    const el = node as CustomElement;
    const conf = BLOCK_RENDER[el.type] || BLOCK_RENDER.paragraph;
    const Tag = conf.tag as ElementType;
    return <Tag key={i} style={conf.style}>{renderPreviewNodes(el.children as unknown as Descendant[])}</Tag>;
  });
}

/**
 * 表单里展示富文本字段：把编辑器存的 HTML 渲成和编辑态一样的排版，而不是拍平成纯文本。
 *
 * 只读模式用节点树渲染而不是 dangerouslySetInnerHTML：htmlToSlate 只认白名单标签，
 * 未知标签和 style、on 开头的属性天然被丢掉，等于顺带过了一道清洗。
 * 解析依赖 document，所以放到 effect 里做（项目是 output:'export'，预渲染阶段没有 DOM）。
 *
 * editable 模式（原地改字）：同一只框换成 contentEditable，进框即可增删文字、复制粘贴，
 * 失焦时把 DOM 内容过一遍「解析→序列化」清洗后再回写表单；排版（标题/加粗等）仍走富文本编辑器。
 */
export function RichTextPreview({ value, bare, rows, editable, onChange }: {
  value?: string;
  bare?: boolean;
  rows?: number;
  editable?: boolean;
  onChange?: (val: string) => void;
}) {
  // null = 还没解析（首帧渲染空框，避免先闪一下「暂无内容」）
  const [nodes, setNodes] = useState<Descendant[] | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  // 输入法正在选字（拼音还挂在框里没上屏）时改 DOM 会把这次输入打断
  const composingRef = useRef(false);

  // 清洗管线：解析器只认白名单标签，序列化器再转义文本，
  // 所以写 innerHTML 前先过一遍——innerHTML 虽不执行 script，但 img onerror 之类属性会生效
  const sanitize = (raw: string) => slateToHtml(htmlToSlate(raw));

  useEffect(() => {
    if (editable) return;
    setNodes(value ? htmlToSlate(value) : []);
  }, [value, editable]);

  // 表单值是唯一的真源：值一变就立刻刷进框里，光标在不在框内都不例外。
  // 之前「聚焦就不写」会让 AI 代填看不见，而失焦时又拿框里的旧 DOM 反写表单，把内容清掉。
  const syncDom = () => {
    const el = boxRef.current;
    if (!el) return;
    const next = sanitize(value || '');
    if (el.innerHTML !== next) el.innerHTML = next;
  };

  useEffect(() => {
    if (!editable || composingRef.current) return;
    syncDom();
  }, [value, editable]);

  const boxStyle: CSSProperties = {
    ...(bare ? {} : { border: '1px solid #d9d9d9', borderRadius: 6, background: '#fafafa', padding: '8px 12px' }),
    height: previewBoxHeight(rows, !!bare),
    overflowY: 'auto',
  };

  if (editable) {
    return (
      <div
        ref={boxRef}
        className="rt-inline"
        contentEditable
        suppressContentEditableWarning
        data-placeholder="点击可直接输入文字，排版格式请用编辑按钮"
        style={{ ...boxStyle, background: '#fff' }}
        onCompositionStart={() => {
          composingRef.current = true;
        }}
        onCompositionEnd={() => {
          composingRef.current = false;
          // 选字期间到达的外部值（AI 代填、Modal 保存）此刻补写
          syncDom();
        }}
        onBlur={(e) => {
          const next = sanitize(e.currentTarget.innerHTML);
          if (next !== sanitize(value || '')) onChange?.(next);
        }}
        onPaste={(e) => {
          // 只收纯文本：外部富文本带着网页垃圾样式，进来后清洗也救不回排版
          e.preventDefault();
          document.execCommand('insertText', false, e.clipboardData.getData('text/plain'));
        }}
      />
    );
  }

  const isEmpty = !nodes || slateToHtml(nodes) === '';

  // 高度固定、超出内部滚动：内容由模板的行数设置决定，不随内容增减把表单撑开
  // bare：只出正文，外框交给调用方（教案详情弹窗整块滚动，不能再套一层边框）
  return (
    <div style={boxStyle}>
      {nodes && (isEmpty
        ? <span style={{ color: '#bfbfbf' }}>暂无内容</span>
        : renderPreviewNodes(nodes))}
    </div>
  );
}

export default function RichTextEditor({
  value = '',
  onChange,
  placeholder = '请输入内容...',
  readOnly = false,
  height = 200,
}: RichTextEditorProps) {
  const [editor] = useState(() => withLists(withReact(withHistory(createEditor()))));
  const initialValue = useMemo(() => htmlToSlate(value), [value]);

  // 外部 value 变化时同步进编辑器（如切换字段、Modal 复用同一实例）。
  // 只有内容真的不同才替换，避免把用户正在输入的状态冲掉。
  useEffect(() => {
    const incoming = htmlToSlate(value);
    if (slateToHtml(editor.children) === slateToHtml(incoming)) return;
    editor.children = incoming;
    editor.onChange();
  }, [editor, value]);

  const handleChange = useCallback((newValue: Descendant[]) => {
    onChange?.(slateToHtml(newValue));
  }, [onChange]);

  const onKeyDown = useCallback((event: React.KeyboardEvent) => {
    if (isHotkey('mod+b', event)) {
      event.preventDefault();
      Editor.addMark(editor, 'bold', true);
    } else if (isHotkey('mod+i', event)) {
      event.preventDefault();
      Editor.addMark(editor, 'italic', true);
    } else if (isHotkey('mod+u', event)) {
      event.preventDefault();
      Editor.addMark(editor, 'underline', true);
    }
  }, [editor]);

  return (
    <div style={{ border: '1px solid #d9d9d9', borderRadius: 6, overflow: 'hidden' }}>
      {/* 只读模式不显示工具栏，也就没有第二个表头 */}
      {!readOnly && <Toolbar editor={editor} />}
      <Slate editor={editor} initialValue={initialValue} onChange={handleChange}>
        <div style={{ minHeight: height, maxHeight: 600, overflowY: 'auto', padding: '12px 16px' }}>
          <Editable
            renderElement={renderElement}
            renderLeaf={renderLeaf}
            placeholder={placeholder}
            readOnly={readOnly}
            onKeyDown={onKeyDown}
            style={{ outline: 'none', minHeight: height - 20 }}
          />
        </div>
        <CharCountBar />
      </Slate>
    </div>
  );
}

export { slateToHtml, htmlToSlate };
