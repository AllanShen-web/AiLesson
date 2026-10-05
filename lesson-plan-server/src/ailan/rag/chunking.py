"""文档切块。

纯函数、无外部依赖，方便用真值表单独验证。策略是段落 → 句子两级拆分后贪心装填：
- 段落边界（空行）优先，避免一个块里混进跨段的内容；
- 句子不被从中间截断（单句本身超过块大小才硬切）；
- 块与块之间用整句回种，让检索命中的分块保留一点上文。

切块尺寸按字符数而不是 token：中文一字符约一个 token，英文偏大，
但嵌入模型这一侧只看输入长度上限，800 字符对两者都安全。
"""

from __future__ import annotations

import re

DEFAULT_SIZE = 800
DEFAULT_OVERLAP = 100

# 在中英文句末标点和换行之后断开；不做 trim，保留原始空白才不会把英文词间距挤掉
_SENTENCE_BREAK = re.compile(r"(?<=[。！？；.!?;])|(?<=\n)")
_PARAGRAPH_BREAK = re.compile(r"\n{2,}")


def chunk_text(text: str, *, size: int = DEFAULT_SIZE, overlap: int = DEFAULT_OVERLAP) -> list[str]:
    paragraphs = _PARAGRAPH_BREAK.split(text)
    sentences = [
        sentence for paragraph in paragraphs if paragraph.strip() for sentence in _split_sentences(paragraph)
    ]
    units = [piece for sentence in sentences for piece in _hard_cut(sentence, size)]
    if not units:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        chunks.append("".join(current).strip())
        # 用本块末尾的整句回种下一块，累计不超过 overlap
        seed: list[str] = []
        seed_len = 0
        for sentence in reversed(current):
            if seed_len + len(sentence) > overlap:
                break
            seed.insert(0, sentence)
            seed_len += len(sentence)
        current, current_len = seed, seed_len

    for unit in units:
        if current_len + len(unit) > size and current:
            flush()
        # 回种的上文加新句仍然放不下时丢弃上文：宁可丢上下文，也不让块超出尺寸
        if current_len + len(unit) > size:
            current, current_len = [], 0
        current.append(unit)
        current_len += len(unit)

    flush()
    return chunks


def _split_sentences(paragraph: str) -> list[str]:
    return [sentence for sentence in _SENTENCE_BREAK.split(paragraph) if sentence.strip()]


def _hard_cut(sentence: str, size: int) -> list[str]:
    if len(sentence) <= size:
        return [sentence]
    return [sentence[i : i + size] for i in range(0, len(sentence), size)]
