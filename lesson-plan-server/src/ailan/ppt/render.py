"""课件预览渲染：把 pptx 渲染成逐页 PNG，写进可重建的预览缓存。

两条渲染路线，按平台各取所长：
- Windows 且有 PowerPoint：走 COM 的 `Presentation.Export`，本机保真度最高，不用额外装东西；
- 其它情况：LibreOffice headless 转 PDF，再用 pdftoppm 出图（容器需装 libreoffice-impress、poppler-utils）。

缓存只为预览服务，缺了随时能重渲，不参与数据正确性；所以渲染失败只影响这一页图，
不把整条改写链路带崩。

并发口径有两条：同一 (课件, 版本) 的重复请求共用一次渲染；不同任务串行执行——
PowerPoint 与 soffice 都是单实例导出，两个渲染同时读写同一缓存目录会 ENOTEMPTY/EBUSY。
"""

from __future__ import annotations

import asyncio
import base64
import re
import shutil
import sys
import tempfile
from pathlib import Path

from ailan.core.config import settings
from ailan.ppt.schemas import Revision

RENDER_TIMEOUT_SECONDS = 150
_PREVIEW_SIZE_PX = (1280, 720)
_TRAILING_NUMBER = re.compile(r"(\d+)(?=\D*$)")
_IS_WINDOWS = sys.platform == "win32"


def _cache_root() -> Path:
    return Path(settings.ppt_preview_dir or Path.cwd() / ".ppt-preview")


def _sort_by_trailing_number(paths: list[Path]) -> list[Path]:
    """按文件名末尾数字排序：pdftoppm 与 PowerPoint 的编号位数不一致，字典序会乱。"""

    def number(path: Path) -> int:
        match = _TRAILING_NUMBER.search(path.name)
        return int(match.group(1)) if match else 0

    return sorted(paths, key=number)


async def _run(command: str, args: list[str]) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        command,
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=RENDER_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        raise RuntimeError(f"渲染命令超时：{command}") from None
    return process.returncode or 0, (stderr or b"").decode("utf-8", "replace").strip()


async def _render_via_powerpoint(in_file: Path, out_dir: Path) -> list[Path]:
    """PowerPoint COM 渲染。

    脚本用 `-EncodedCommand` 传：路径里有中文与空格，走命令行参数会被引号和编码双重坑掉。
    """

    def quote(value: Path) -> str:
        return str(value).replace("'", "''")

    script = "\n".join(
        [
            "$ErrorActionPreference='Stop'",
            f"$pptx='{quote(in_file)}'",
            f"$out='{quote(out_dir)}'",
            "$pp = New-Object -ComObject PowerPoint.Application",
            "$pres = $pp.Presentations.Open($pptx,-1,0,0)",
            f"$pres.Export($out,'PNG',{_PREVIEW_SIZE_PX[0]},{_PREVIEW_SIZE_PX[1]})",
            "$pres.Close()",
            "$pp.Quit()",
        ]
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    code, stderr = await _run("powershell.exe", ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])
    if code != 0:
        raise RuntimeError(stderr or "PowerPoint 渲染失败")
    return _sort_by_trailing_number([path for path in out_dir.glob("*.png")])


async def _render_via_libreoffice(in_file: Path, work_dir: Path) -> list[Path]:
    soffice = "soffice.exe" if _IS_WINDOWS else "soffice"
    code, stderr = await _run(
        soffice, ["--headless", "--convert-to", "pdf", "--outdir", str(work_dir), str(in_file)]
    )
    if code != 0:
        raise RuntimeError(stderr or "LibreOffice 转 PDF 失败")
    pdf = work_dir / (in_file.stem + ".pdf")
    code, stderr = await _run("pdftoppm", ["-png", "-r", "128", str(pdf), str(work_dir / "page")])
    if code != 0:
        raise RuntimeError(stderr or "pdftoppm 出图失败")
    return _sort_by_trailing_number([path for path in work_dir.glob("page-*.png")])


class PptRenderer:
    """渲染任务的编排者：去重、串行排队、缓存落盘。"""

    def __init__(self) -> None:
        self.cache_root = _cache_root()
        self._inflight: dict[str, asyncio.Task[int]] = {}
        self._queue = asyncio.Lock()

    async def render(self, presentation_id: int, revision: Revision, data: bytes) -> int:
        """渲染并写缓存 `<root>/<id>/<revision>/p<n>.png`，返回页数。"""
        key = f"{presentation_id}/{revision}"
        running = self._inflight.get(key)
        if running is not None:
            return await running
        task = asyncio.create_task(self._render_queued(presentation_id, revision, data))
        self._inflight[key] = task
        task.add_done_callback(lambda done, key=key: self._forget(key, done))
        return await task

    def cached_path(self, presentation_id: int, revision: Revision, page: int) -> Path | None:
        """某页的缓存文件路径；没缓存返回 None，由调用方决定是补渲还是报缺。"""
        candidate = self.cache_root / str(presentation_id) / revision / f"p{page}.png"
        return candidate if candidate.is_file() else None

    def _forget(self, key: str, task: asyncio.Task[int]) -> None:
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)

    async def _render_queued(self, presentation_id: int, revision: Revision, data: bytes) -> int:
        async with self._queue:
            return await self._render_once(presentation_id, revision, data)

    async def _render_once(self, presentation_id: int, revision: Revision, data: bytes) -> int:
        work_dir = Path(tempfile.mkdtemp(prefix="ppt-render-"))
        in_file = work_dir / "input.pptx"
        out_dir = work_dir / "png"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            in_file.write_bytes(data)
            paths = await self._dispatch(in_file, work_dir, out_dir)
            cache_dir = self.cache_root / str(presentation_id) / revision
            # 先清后写：页数变少时旧图必须消失，否则第 9 页还留着上一版的残图
            shutil.rmtree(cache_dir, ignore_errors=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            for index, path in enumerate(paths, start=1):
                shutil.copyfile(path, cache_dir / f"p{index}.png")
            return len(paths)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def _dispatch(self, in_file: Path, work_dir: Path, out_dir: Path) -> list[Path]:
        if not _IS_WINDOWS:
            return await _render_via_libreoffice(in_file, work_dir)
        try:
            return await _render_via_powerpoint(in_file, out_dir)
        except (RuntimeError, OSError):
            return await _render_via_libreoffice(in_file, work_dir)


__all__ = ["RENDER_TIMEOUT_SECONDS", "PptRenderer"]
