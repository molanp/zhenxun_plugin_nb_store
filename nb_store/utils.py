import asyncio
import contextlib
import csv
import html.parser
import io
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urljoin
import zipfile

from nonebot.utils import run_sync
from nonebot_plugin_alconna import UniMessage
from packaging.requirements import Requirement
from packaging.version import parse as parse_version

from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from .config import LOG_COMMAND


class SimpleIndexParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._current_href = None
        self._current_tag = None

    def handle_starttag(self, tag, attrs):
        self._current_tag = tag
        if tag == "a":
            self._current_href = dict(attrs).get("href")

    def handle_data(self, data):
        if data.lower().endswith(".whl") and (
            self._current_tag == "a" and self._current_href
        ):
            self.links.append(self._current_href)

    def handle_endtag(self, tag):
        if tag == "a":
            self._current_href = None  # 重置 href


def format_req_for_pip(req: Requirement) -> str:
    parts = [req.name]
    if req.extras:
        extras = ",".join(sorted(req.extras))
        parts.append(f"[{extras}]")
    if req.specifier:
        parts.append(str(req.specifier))
    if req.marker:
        marker_str = str(req.marker).replace("'", '"')
        parts.append(f"; {marker_str}")
    return "".join(parts)


@run_sync
def open_zip(whl_bytes):
    return zipfile.ZipFile(io.BytesIO(whl_bytes))


@run_sync
def zip_namelist(zf):
    return zf.namelist()


@run_sync
def zip_read(zf, filename):
    return zf.read(filename)


@run_sync
def path_mkdir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


@run_sync
def path_rm(path: Path):
    shutil.rmtree(path)


async def get_record_files(zf):
    namelist = await zip_namelist(zf)
    record_file = next(
        (
            name
            for name in namelist
            if name.endswith("RECORD") and ".dist-info/" in name
        ),
        None,
    )
    if not record_file:
        raise FileNotFoundError("找不到RECORD文件")
    record_data = await zip_read(zf, record_file)
    records = []
    for line in record_data.decode("utf-8").splitlines():
        reader = csv.reader([line])
        if row := next(reader):
            records.append(row[0])
    return records


async def get_dependencies_from_metadata(zf) -> list[str]:
    namelist = await zip_namelist(zf)
    metadata_file = next(
        (f for f in namelist if f.endswith("METADATA") and ".dist-info/" in f),
        None,
    )
    if not metadata_file:
        return []
    data = await zip_read(zf, metadata_file)
    dependencies = []
    for line in data.decode("utf-8", errors="ignore").splitlines():
        if line.startswith("Requires-Dist:"):
            dep_str = line[len("Requires-Dist:") :].strip()
            try:
                req = Requirement(dep_str)
                dependencies.append(format_req_for_pip(req))
            except Exception:
                dependencies.append(dep_str)
    return dependencies


async def extract_code_from_record(zf, dest_dir: Path):
    records = await get_record_files(zf)
    code_files = [
        f
        for f in records
        if not (".dist-info/" in f or ".data/" in f or f.endswith("/"))
    ]
    for file in code_files:
        data = await zip_read(zf, file)
        dest_path = dest_dir / file
        await path_mkdir(dest_path.parent)
        f = await run_sync(open)(dest_path, "wb")
        try:
            await run_sync(f.write)(data)
        finally:
            await run_sync(f.close)()
    return code_files


async def get_pip_index_url() -> str:
    with contextlib.suppress(Exception):
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "pip", "config", "get", "global.index-url"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if url := result.stdout.strip():
            if not url.endswith("/"):
                url += "/"
            return url
    with contextlib.suppress(Exception):
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "pip", "config", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if "index-url" in line:
                return line.split("=", 1)[-1].strip()
    return "https://pypi.org/simple/"


async def get_latest_whl_url_from_simple(package: str, index_url: str) -> str | None:
    url = urljoin(index_url, package.replace("_", "-").lower())
    if not url.endswith("/"):
        url += "/"
    html = await AsyncHttpx.get(url, timeout=10, headers={"User-Agent": "pip"})
    parser = SimpleIndexParser()
    parser.feed(html.text)
    whl_links = parser.links
    if not whl_links:
        return None
    whl_links.sort(key=lambda link: parse_version(link.split("-")[1]), reverse=True)
    return urljoin(url, whl_links[0]) if whl_links else None


async def get_whl_download_url(package: str) -> str | None:
    """获取whl文件的下载地址

    参数:
        :package str: 包名

    返回:
        :str: 下载地址
    """

    index_url = await get_pip_index_url()
    if "pypi.tuna.tsinghua.edu.cn" in index_url:
        await UniMessage("你正在使用清华大学镜像源，可能会导致 403 问题").send()
    return await get_latest_whl_url_from_simple(package, index_url)


@run_sync
def move_contents_up_one_level(target_dir: Path) -> None:
    """
    将目标目录中的所有文件和子目录移动到其父目录

    参数:
        :target_dir Path: 要处理的目标目录
    """
    if not target_dir.is_dir():
        raise ValueError(f"{target_dir} 不是有效的目录")

    parent_dir = target_dir.parent

    # 移动所有文件和子目录
    for item in target_dir.iterdir():
        dest_path = parent_dir / item.name

        # 处理目标路径已存在的情况
        if dest_path.exists():
            if dest_path.is_dir() and item.is_dir():
                # 合并目录（将内容复制到已有目录）
                for sub_item in item.iterdir():
                    shutil.move(str(sub_item), str(dest_path))
                shutil.rmtree(item)
            else:
                # 覆盖已有文件
                if dest_path.is_file():
                    dest_path.unlink()
                shutil.move(str(item), str(dest_path))
        else:
            # 直接移动
            shutil.move(str(item), str(parent_dir))

    # 可选：删除现在为空的原始目录
    if not any(target_dir.iterdir()):
        target_dir.rmdir()


async def copy2_return_deps(whl_bytes: bytes, dest_path: Path) -> list[str]:
    """
    提取whl文件中的代码文件夹复制到指定目录，并返回依赖列表

    参数:
        :whl_bytes: whl文件的字节流
        :dest_dir: 目标目录

    返回:
        :list: 依赖列表
    """
    await path_mkdir(dest_path)
    zf = await open_zip(whl_bytes)
    try:
        await extract_code_from_record(zf, dest_path)
        deps = await get_dependencies_from_metadata(zf)
    finally:
        await run_sync(zf.close)()
    if not (dest_path / "__init__.py").exists():
        logger.warning(f"{dest_path} 不是一个有效的插件目录，正在修复...", LOG_COMMAND)
        await move_contents_up_one_level(dest_path)
    return deps
