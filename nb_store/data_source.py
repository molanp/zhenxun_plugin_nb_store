import math
from pathlib import Path

from aiocache import cached
import aiofiles
from nonebot.utils import run_sync
import ujson as json

from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.image_utils import BuildImage, ImageTemplate, RowStyle
from zhenxun.utils.manager.virtual_env_package_manager import VirtualEnvPackageManager

from .config import LOG_COMMAND, PLUGIN_FLODER, PLUGIN_INDEX
from .models import StorePluginInfo
from .utils import copy2_return_deps, get_whl_download_url, path_mkdir, path_rm


def sort_plugins_by(
    plugin_list: list[StorePluginInfo], order_by: str
) -> list[StorePluginInfo]:
    """按时间倒序排列"""
    return sorted(
        plugin_list,
        key=lambda x: getattr(x, order_by),
        reverse=True,
    )


async def common_install_plugin(plugin_info: StorePluginInfo, rm_exists: bool = False):
    """通用插件安装流程"""
    down_url = await get_whl_download_url(plugin_info.project_link)
    if not down_url:
        raise FileNotFoundError(f"插件 {plugin_info.name} 未找到安装包...")
    if rm_exists:
        await path_rm(PLUGIN_FLODER / plugin_info.module_name)
    await path_mkdir(PLUGIN_FLODER / plugin_info.module_name)
    whl_data = await AsyncHttpx.get(down_url)
    deps = await copy2_return_deps(whl_data.content, PLUGIN_FLODER)
    async with aiofiles.open(
        PLUGIN_FLODER / plugin_info.module_name / "requirements.txt",
        "w",
        encoding="utf-8",
    ) as f:
        for dep in deps:
            await f.write(dep + "\n")
    await install_requirement(
        PLUGIN_FLODER / plugin_info.module_name / "requirements.txt"
    )


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


@run_sync
def install_requirement(path: Path):
    return VirtualEnvPackageManager.install_requirement(path)


class StoreManager:
    suc_plugin: dict[str, str] | None = None

    @classmethod
    async def get_plugins_by_page(
        cls,
        page: int = 1,
        page_size: int = 50,
        order_by: str = "time",
        only_show_update=False,
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
        if not cls.suc_plugin:
            db_plugin_list = await cls.get_loaded_plugins("module", "version")
            cls.suc_plugin = {p[0]: (p[1] or "?") for p in db_plugin_list}
        plugins = sort_plugins_by(plugins, order_by)
        if only_show_update:
            plugins = [
                plugin
                for plugin in plugins
                if plugin.module_name in cls.suc_plugin
                and not cls.check_version_is_new(plugin, cls.suc_plugin)
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
                for detail in json.loads(response.text)
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
    def version_check(cls, plugin_info: StorePluginInfo, suc_plugin: dict[str, str]):
        """版本检查

        参数:
            plugin_info: StorePluginInfo
            suc_plugin: 模块名: 版本号

        返回:
            str: 版本号
        """
        module = plugin_info.module_name
        if suc_plugin.get(module) and not cls.check_version_is_new(
            plugin_info, suc_plugin
        ):
            return f"{suc_plugin[module]} (有更新->{plugin_info.version})"
        return plugin_info.version

    @classmethod
    def check_version_is_new(
        cls, plugin_info: StorePluginInfo, suc_plugin: dict[str, str]
    ):
        """检查版本是否是最新

        参数:
            plugin_info: StorePluginInfo
            suc_plugin: 模块名: 版本号

        返回:
            bool: 是否是最新
        """
        module = plugin_info.module_name
        return suc_plugin.get(module) and plugin_info.version == suc_plugin[module]

    @classmethod
    async def get_loaded_plugins(cls, *args) -> list[tuple[str, str]]:
        """获取已加载的插件

        返回:
            list[str]: 已加载的插件
        """
        return await PluginInfo.filter(load_status=True).values_list(*args)

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
        if not cls.suc_plugin:
            db_plugin_list = await cls.get_loaded_plugins("module", "version")
            cls.suc_plugin = {p[0]: (p[1] or "?") for p in db_plugin_list}
        data_list = [
            [
                "已安装" if plugin_info.module_name in cls.suc_plugin else "",
                plugin_info.valid,
                plugin_info.project_link,
                plugin_info.name,
                plugin_info.desc,
                plugin_info.author,
                cls.version_check(plugin_info, cls.suc_plugin),
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
        if not cls.suc_plugin:
            db_plugin_list = await cls.get_loaded_plugins("module", "version")
            cls.suc_plugin = {p[0]: (p[1] or "?") for p in db_plugin_list}
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
        db_plugin_list = await cls.get_loaded_plugins("module", "version")
        suc_plugin = {p[0]: (p[1] or "Unknown") for p in db_plugin_list}
        if plugin_info.module_name not in [p[0] for p in db_plugin_list]:
            return f"插件 {plugin_info.name} 未安装，无法更新"
        logger.debug(f"当前插件列表: {suc_plugin}", LOG_COMMAND)
        if cls.check_version_is_new(plugin_info, suc_plugin):
            return f"插件 {plugin_info.name} 已是最新版本"
        await common_install_plugin(plugin_info, True)
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
        db_plugin_list = await cls.get_loaded_plugins("module", "version")
        suc_plugin = {p[0]: (p[1] or "Unknown") for p in db_plugin_list}
        logger.debug(f"尝试更新全部插件 {plugin_name_list}", LOG_COMMAND)
        for plugin_info in plugin_list:
            try:
                if plugin_info.module_name not in suc_plugin:
                    logger.debug(
                        f"插件 {plugin_info.name}({plugin_info.module_name}) 未安装"
                        "，跳过",
                        LOG_COMMAND,
                    )
                    continue
                if cls.check_version_is_new(plugin_info, suc_plugin):
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
