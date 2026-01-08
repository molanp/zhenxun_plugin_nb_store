import math
from pathlib import Path

from aiocache import cached
import aiofiles
import ujson

from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.image_utils import BuildImage, ImageTemplate, RowStyle
from zhenxun.utils.manager.virtual_env_package_manager import VirtualEnvPackageManager

from .config import LOG_COMMAND, PLUGIN_FLODER, PLUGIN_INDEX
from .models import StorePluginInfo
from .utils import (
    Plugin,
    copy2,
    get_whl_download_url,
    init_ver_data,
    path_mkdir,
    path_rm,
)


def sort_plugins_by(
    plugin_list: list[StorePluginInfo], order_by: str
) -> list[StorePluginInfo]:
    """按时间倒序排列"""
    return sorted(
        plugin_list,
        key=lambda x: getattr(x, order_by),
        reverse=True,
    )


async def inject_botpy():
    file_path = Path() / "bot.py"
    async with aiofiles.open(file_path, encoding="utf-8") as f:
        lines = await f.readlines()

    # 检查是否已存在目标代码
    target_line = 'nonebot.load_plugins("nonebot_plugins")'
    if any(target_line in line for line in lines):
        logger.debug("已存在目标代码(加载注入)，跳过", LOG_COMMAND)
        return

    # 找到最后一个 load_plugins 调用的位置
    last_index = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("nonebot.load_plugins("):
            last_index = i

    if last_index == -1:
        logger.warning(
            "未找到 `nonebot.load_plugins` 调用，无法注入插件加载", LOG_COMMAND
        )
        return

    # 保持相同缩进
    indent = " " * (len(lines[last_index]) - len(lines[last_index].lstrip()))
    new_line = f'{indent}nonebot.load_plugins("nonebot_plugins")\n'

    # 插入新行
    lines.insert(last_index + 1, new_line)

    async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
        await f.writelines(lines)
    logger.info("Nonebot插件加载注入成功", LOG_COMMAND)


async def common_install_plugin(plugin_info: StorePluginInfo):
    """通用插件安装流程"""
    await inject_botpy()
    down_url = await get_whl_download_url(plugin_info.project_link)
    if not down_url:
        raise FileNotFoundError(f"插件 {plugin_info.name} 未找到安装包...")
    target_path = PLUGIN_FLODER / plugin_info.module_name
    whl_data = await AsyncHttpx.get(down_url)
    await path_rm(target_path)
    path_mkdir(target_path)
    await copy2(whl_data.content, target_path)
    await Plugin(plugin_info).set_local_ver(plugin_info.version)
    await install_requirement(target_path / "requirements.txt")


def row_style(column: str, text: str) -> RowStyle:
    """文本风格

    参数:
        column: 表头
        text: 文本内容

    返回:
        RowStyle: RowStyle
    """
    style = RowStyle()
    if column == "-" and text == "已安装":
        style.font_color = "#67C23A"
    if column == "商店测试":
        style.font_color = "#67C23A" if text == "True" else "#F56C6C"
    return style


async def install_requirement(path: Path):
    return await VirtualEnvPackageManager.install_requirement(path)


class StoreManager:
    # module -> local_version
    suc_plugin: dict[str, str] | None = None

    @classmethod
    async def init_suc_plugin(cls) -> dict[str, str]:
        """Load installed NB store plugins and their local versions once."""
        if cls.suc_plugin is not None:
            return cls.suc_plugin

        loaded_modules: list[str] = await PluginInfo.filter(
            load_status=True,
            module_path__startswith="nonebot_plugins."
        ).values_list("module", flat=True)  # type: ignore
        nb_plugins = await cls.get_data()
        nb_plugin_map = {p.module_name: p for p in nb_plugins}

        suc_plugin: dict[str, str] = {}
        await init_ver_data()
        for module in loaded_modules:
            plugin_info = nb_plugin_map.get(module)
            if not plugin_info:
                continue
            local_ver =  Plugin(plugin_info).get_local_ver()
            suc_plugin[module] = local_ver or "Unknown"

        return suc_plugin

    @classmethod
    async def get_plugins_by_page(
        cls,
        page: int = 1,
        page_size: int = 50,
        order_by: str = "time",
        only_show_update: bool = False,
        query: str = "",
    ) -> BuildImage | str:
        plugins: list[StorePluginInfo] = await cls.get_data()
        if query:
            plugins = [
                plugin_info
                for plugin_info in plugins
                if query.lower() in plugin_info.name.lower()
                or query.lower() in plugin_info.author.lower()
                or query.lower() in plugin_info.desc.lower()
            ]
        if cls.suc_plugin is None:
            cls.suc_plugin = await cls.init_suc_plugin()

        plugins = sort_plugins_by(plugins, order_by)
        if only_show_update:
            plugins = [
                plugin
                for plugin in plugins
                if plugin.module_name in cls.suc_plugin
                and cls.suc_plugin[plugin.module_name] != plugin.version
            ]
        total = math.ceil(len(plugins) / page_size)
        if not 0 < page <= total:
            return "没有更多数据了..."
        start = (page - 1) * page_size
        end = start + page_size
        return await cls.render_plugins_list(
            plugins[start:end], f"当前页码 {page}/{total}, 在命令后附加页码进行翻页"
        )

    @classmethod
    async def get_nb_plugins(cls) -> list[StorePluginInfo]:
        """获取github插件列表信息

        返回:
            list[StorePluginInfo]: 插件列表数据
        """
        response = await AsyncHttpx.get(PLUGIN_INDEX, check_status_code=200)
        if response.status_code == 200:
            logger.info("获取nb插件列表成功", LOG_COMMAND)
            data = []
            data.extend(
                StorePluginInfo(**detail)
                for detail in ujson.loads(response.text)
                if detail.get("type") != "library"
            )
            return data
        else:
            logger.warning(f"获取nb插件列表失败: {response.status_code}", LOG_COMMAND)
        return []

    @classmethod
    @cached(60)
    async def get_data(cls) -> list[StorePluginInfo]:
        """获取插件信息数据

        返回:
            list[StorePluginInfo]: 插件信息数据
        """
        return await cls.get_nb_plugins()

    @classmethod
    def version_check(cls, plugin_info: StorePluginInfo):
        """版本检查

        参数:
            plugin_info: StorePluginInfo
            suc_plugin: 模块名: 版本号

        返回:
            str: 版本号
        """
        assert isinstance(cls.suc_plugin, dict)
        module = plugin_info.module_name
        local_ver = cls.suc_plugin.get(module)
        if module in cls.suc_plugin and plugin_info.version != local_ver:
            return f"{local_ver} (有更新->{plugin_info.version})"
        return plugin_info.version

    @classmethod
    async def render_plugins_list(
        cls,
        plugin_list: list[StorePluginInfo],
        tip: str = "通过添加/移除/更新插件 包名/名称 来管理插件",
    ) -> BuildImage:
        column_name = [
            "-",
            "商店测试",
            "包名",
            "名称",
            "简介",
            "作者",
            "版本",
            "上次更新时间",
        ]
        if cls.suc_plugin is None:
            cls.suc_plugin = await cls.init_suc_plugin()

        data_list = [
            [
                "已安装" if plugin_info.module_name in cls.suc_plugin else "",
                plugin_info.valid,
                plugin_info.project_link,
                plugin_info.name,
                plugin_info.desc,
                plugin_info.author,
                cls.version_check(plugin_info),
                plugin_info.time,
            ]
            for plugin_info in plugin_list
        ]
        return await ImageTemplate.table_page(
            "nb商店插件列表",
            tip,
            column_name,
            data_list,
            text_style=row_style,
        )

    @classmethod
    async def get_plugins_info(cls) -> BuildImage:
        """插件列表

        返回:
            BuildImage | str: 返回消息
        """
        return await cls.render_plugins_list(await cls.get_data())

    @classmethod
    async def add_plugin(cls, plugin_id: str) -> str:
        """添加插件

        参数:
            plugin_id: 插件id或模块名

        返回:
            str: 返回消息
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        try:
            plugin_key = await cls._get_module_by_pypi_id_name(plugin_id)
        except ValueError as e:
            return str(e)
        if cls.suc_plugin is None:
            cls.suc_plugin = await cls.init_suc_plugin()

        plugin_info = next(
            (p for p in plugin_list if p.module_name == plugin_key), None
        )
        if not plugin_info:
            return f"插件 {plugin_id} 不存在"
        if plugin_info.module_name in cls.suc_plugin:
            return f"插件 {plugin_info.name} 已安装，无需重复安装"
        logger.info(f"正在安装插件 {plugin_info.name}...", LOG_COMMAND)
        await common_install_plugin(plugin_info)
        return f"插件 {plugin_info.name} 安装成功! 重启后生效"

    @classmethod
    async def remove_plugin(cls, plugin_id: str) -> str:
        """移除插件

        参数:
            plugin_id: 插件id或模块名

        返回:
            str: 返回消息
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        try:
            plugin_key = await cls._get_module_by_pypi_id_name(plugin_id)
        except ValueError as e:
            return str(e)
        plugin_info = next(
            (p for p in plugin_list if p.module_name == plugin_key), None
        )
        if not plugin_info:
            return f"插件 {plugin_key} 不存在"
        path = PLUGIN_FLODER / plugin_info.module_name
        if not path.exists():
            return f"插件 {plugin_info.name} 不存在..."
        logger.debug(f"尝试移除插件 {plugin_info.name} 文件: {path}", LOG_COMMAND)
        await path_rm(path)
        await Plugin(plugin_info).remove_local_ver()
        return f"插件 {plugin_info.name} 移除成功! 重启后生效"

    @classmethod
    async def update_plugin(cls, plugin_id: str) -> str:
        """更新插件

        参数:
            plugin_id: 插件id

        返回:
            str: 返回消息
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        try:
            plugin_key = await cls._get_module_by_pypi_id_name(plugin_id)
        except ValueError as e:
            return str(e)
        plugin_info = next(
            (p for p in plugin_list if p.module_name == plugin_key), None
        )
        if not plugin_info:
            return f"插件 {plugin_key} 不存在"
        logger.info(f"尝试更新插件 {plugin_info.name}", LOG_COMMAND)
        if cls.suc_plugin is None:
            cls.suc_plugin = await cls.init_suc_plugin()

        if plugin_info.module_name not in cls.suc_plugin:
            return f"插件 {plugin_info.name} 未安装，无法更新"
        logger.debug(f"当前NB商店插件列表: {cls.suc_plugin}", LOG_COMMAND)
        if cls.suc_plugin[plugin_info.module_name] == plugin_info.version:
            return f"插件 {plugin_info.name} 已是最新版本"
        await common_install_plugin(plugin_info)
        return f"插件 {plugin_info.name} 更新成功! 重启后生效"

    @classmethod
    async def update_all_plugin(cls) -> str:
        """更新插件

        参数:
            plugin_id: 插件id

        返回:
            str: 返回消息
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        plugin_name_list = [p.name for p in plugin_list]
        update_failed_list = []
        update_success_list = []
        result = "--已更新{}个插件 {}个失败 {}个成功--"
        if cls.suc_plugin is None:
            cls.suc_plugin = await cls.init_suc_plugin()

        logger.debug(f"尝试更新全部插件 {plugin_name_list}", LOG_COMMAND)
        for plugin_info in plugin_list:
            try:
                if plugin_info.module_name not in cls.suc_plugin:
                    logger.debug(
                        f"插件 {plugin_info.name}({plugin_info.module_name}) 未安装"
                        "，跳过",
                        LOG_COMMAND,
                    )
                    continue
                if cls.suc_plugin[plugin_info.module_name] == plugin_info.version:
                    logger.debug(
                        f"插件 {plugin_info.name}({plugin_info.module_name}) "
                        "已是最新版本，跳过",
                        LOG_COMMAND,
                    )
                    continue
                logger.info(
                    f"正在更新插件 {plugin_info.name}({plugin_info.module_name})",
                    LOG_COMMAND,
                )
                await common_install_plugin(plugin_info)
                update_success_list.append(plugin_info.name)
            except Exception as e:
                logger.error(
                    f"更新插件 {plugin_info.name}({plugin_info.module_name}) 失败",
                    LOG_COMMAND,
                    e=e,
                )
                update_failed_list.append(plugin_info.name)
        if not update_success_list and not update_failed_list:
            return "全部插件已是最新版本"
        if update_success_list:
            result += "\n* 以下插件更新成功:\n\t- {}".format(
                "\n\t- ".join(update_success_list)
            )
        if update_failed_list:
            result += "\n* 以下插件更新失败:\n\t- {}".format(
                "\n\t- ".join(update_failed_list)
            )
        return (
            result.format(
                len(update_success_list) + len(update_failed_list),
                len(update_failed_list),
                len(update_success_list),
            )
            + "\n重启后生效"
        )

    @classmethod
    async def _get_module_by_pypi_id_name(cls, plugin_id: str) -> str:
        """获取插件module

        参数:
            plugin_id: pypi包名或插件名称

        异常:
            ValueError: 插件不存在

        返回:
            str: 插件模块名
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        """检查包名或名称匹配"""
        for p in plugin_list:
            if plugin_id in [p.project_link, p.name]:
                return p.module_name
        raise ValueError("插件 包名 / 名称 不存在...")
