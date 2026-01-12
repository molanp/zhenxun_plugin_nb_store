import asyncio
import contextlib
import csv
import html.parser
import io
from pathlib import Path
import shutil
import subprocess
import sys
# import re
from urllib.parse import urljoin
import zipfile

import aiofiles
from nonebot.utils import run_sync
from packaging.requirements import Requirement
from packaging.version import parse as parse_version
import ujson

from zhenxun.configs.path_config import DATA_PATH as BASE_PATH
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from .config import LOG_COMMAND
from .models import StorePluginInfo

DATA_PATH = BASE_PATH / "nb_store"
DATA_PATH.mkdir(parents=True, exist_ok=True)

PLUGIN_VER_DATA: dict[str, str] = {}
PLUGIN_VER_LOCK = asyncio.Lock()

# CONFLICTING_DEPS_PATTERN = re.compile(r"nonebot[._-]plugin[._-]orm", re.IGNORECASE)


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
def zip_namelist(zf: zipfile.ZipFile):
    """获取zip文件中的文件列表"""
    return zf.namelist()


@run_sync
def zip_read(zf: zipfile.ZipFile, filename: str):
    """读取zip文件中的文件(相对路径)"""
    return zf.read(filename)


def path_mkdir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


@run_sync
def path_rm(path: Path) -> None:
    """
    删除目录或文件。如果路径不存在则静默返回
    """
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except FileNotFoundError:
        # 忽略路径不存在的情况
        return


async def get_record_files(zf: zipfile.ZipFile):
    """从RECORD文件中获取包文件列表"""
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
    records: list[str] = []
    for line in record_data.decode("utf-8").splitlines():
        reader = csv.reader([line])
        """CSV结构: path, hash, size"""
        if row := next(reader):
            records.append(row[0])
    return records


async def get_dependencies_from_metadata(zf: zipfile.ZipFile) -> list[str]:
    """从METADATA文件中获取依赖列表（并在依赖层面检查冲突）"""
    namelist = await zip_namelist(zf)
    metadata_file = next(
        (f for f in namelist if f.endswith("METADATA") and ".dist-info/" in f),
        None,
    )
    if not metadata_file:
        return []
    data = await zip_read(zf, metadata_file)
    decoded_data = data.decode("utf-8", errors="ignore")
    dependencies: list[str] = []
    prefix = "Requires-Dist:"
    prefix_len = len(prefix)

    for line in decoded_data.splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue

        dep_str = line[prefix_len:].strip()
        if not dep_str:
            continue
        # if conflict_match := CONFLICTING_DEPS_PATTERN.search(dep_str):
        #     raise RuntimeError(f"该插件的依赖文件中发现与真寻冲突的依赖({conflict_match.group(0)}), 已阻止本次安装")
        try:
            req = Requirement(dep_str)
            formatted = format_req_for_pip(req)
        except Exception:
            formatted = dep_str

        dependencies.append(formatted)

    return dependencies


async def extract_code_from_whl(zf: zipfile.ZipFile, dest_dir: Path):
    """从WHL文件中提取代码文件"""
    records = await get_record_files(zf)
    code_files = [
        f
        for f in records
        if not (".dist-info/" in f or ".data/" in f or f.endswith("/"))
    ]
    for file in code_files:
        data = await zip_read(zf, file)
        dest_path = dest_dir / file
        path_mkdir(dest_path.parent)
        async with aiofiles.open(dest_path, "wb") as f:
            await f.write(data)


async def get_pip_index_url() -> str:
    """获取pip的索引地址"""
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
    """从索引地址中获取最新的whl文件的下载地址"""
    if not index_url.endswith("/"):
        index_url += "/"
    url = urljoin(index_url, package.replace("_", "-").lower())
    if not url.endswith("/"):
        url += "/"
    html = await AsyncHttpx.get(url, timeout=10, headers={"User-Agent": "pip/25.0.0"})
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
        logger.warning(
            "为避免清华pip的403错误，已自动切换为阿里云镜像。请及时更换镜像源配置",
            LOG_COMMAND,
        )
        index_url = "https://mirrors.aliyun.com/pypi/simple"
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


async def copy2(whl_bytes: bytes, target_path: Path) -> None:
    """
    将 wheel/zip 内容解压到 target_path，并在 target_path 中写入 requirements.txt
      - 解压文件内容到 target_path
      - 如果包内有 requirements 文件将其写入 target_path/requirements.txt

    """
    path_mkdir(target_path)
    zf = await open_zip(whl_bytes)
    try:
        await extract_code_from_whl(zf, target_path)
        deps = await get_dependencies_from_metadata(zf)
    finally:
        await run_sync(zf.close)()
    if not (target_path / "__init__.py").exists():
        logger.warning(
            f"{target_path} 不是一个有效的插件目录，正在修复...", LOG_COMMAND
        )
        await move_contents_up_one_level(target_path)
    if deps:
        async with aiofiles.open(
            target_path / "requirements.txt",
            "w",
            encoding="utf-8",
        ) as f:
            for dep in deps:
                await f.write(dep + "\n")


async def init_ver_data():
    global PLUGIN_VER_DATA
    async with PLUGIN_VER_LOCK:
        # Double-check under lock to avoid redundant I/O under concurrency
        if not PLUGIN_VER_DATA and (DATA_PATH / "plugin_ver.json").exists():
            async with aiofiles.open(DATA_PATH / "plugin_ver.json") as f:
                PLUGIN_VER_DATA = ujson.loads(await f.read())
        return PLUGIN_VER_DATA


class Plugin:
    """插件信息操作类"""

    def __init__(self, plugin_info: StorePluginInfo):
        self.pkg_name = plugin_info.project_link
        self.ver = plugin_info.version
        """插件最新版本号"""

    def get_local_ver(self) -> str | None:
        """获取插件的本地版本号"""
        return PLUGIN_VER_DATA.get(self.pkg_name)

    async def set_local_ver(self, ver: str):
        """设置插件的本地号版本"""
        global PLUGIN_VER_DATA
        async with PLUGIN_VER_LOCK:
            PLUGIN_VER_DATA[self.pkg_name] = ver
            await self.write()

    async def remove_local_ver(self):
        """移除插件的本地版本号"""
        global PLUGIN_VER_DATA
        async with PLUGIN_VER_LOCK:
            PLUGIN_VER_DATA.pop(self.pkg_name, None)
            await self.write()

    async def write(self):
        async with aiofiles.open(
            DATA_PATH / "plugin_ver.json", "w", encoding="utf-8"
        ) as f:
            await f.write(ujson.dumps(PLUGIN_VER_DATA, ensure_ascii=False, indent=2))
