from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Match,
    Option,
    Subcommand,
    on_alconna,
)
from nonebot_plugin_session import EventSession

from zhenxun.configs.utils import PluginExtraData
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

from .data_source import StoreManager

__plugin_meta__ = PluginMetadata(
    name="Nonebot插件商店",
    description="Nonebot插件商店",
    usage="""
    nb商店 ?页码 ?每页项数 <?-o> xx : 查看当前的nonebot 插件商店.使用参数 -o 指定排序字段
    添加nb插件 name/pypi_name     : 添加nonebot 市场插件
    移除nb插件 name/pypi_name     : 移除nonebot 市场插件
    搜索nb插件 <任意关键字>  ?页码 ?每页项数 <?-o> xx     : 搜索nonebot 市场插件.使用参数 -o 指定排序字段
    更新nb插件 name/pypi_name     : 更新nonebot 市场插件
    查看可更新nb插件 ?页码 ?每页项数 <?-o> xx : 查看可更新nonebot 市场插件.使用参数 -o 指定排序字段
    更新全部nb插件          : 更新全部nonebot 市场插件
    """.strip(),
    extra=PluginExtraData(
        author="molanp",
        version="1.1",
        plugin_type=PluginType.SUPERUSER,
    ).to_dict(),
)

_matcher = on_alconna(
    Alconna(
        "nb商店",
        Args["page?", int, 1]["page_size?", int, 20],
        Option(
            "-o|--order",
            Args["order_by", str, "time"],
            help_text="排序标准，默认为更新时间",
        ),
        Subcommand("add", Args["plugin_id", str]),
        Subcommand("remove", Args["plugin_id", str]),
        Subcommand("search", Args["plugin_name_or_author", str]),
        Subcommand("update", Args["plugin_id", str]),
        Subcommand("can_update"),
        Subcommand("update_all"),
    ),
    permission=SUPERUSER,
    priority=1,
    block=True,
)

_matcher.shortcut(
    r"(添加|安装)nb插件",
    command="nb商店",
    arguments=["add", "{%0}"],
    prefix=True,
)

_matcher.shortcut(
    r"(移除|卸载)nb插件",
    command="nb商店",
    arguments=["remove", "{%0}"],
    prefix=True,
)

_matcher.shortcut(
    r"搜索nb插件",
    command="nb商店",
    arguments=["search", "{%0}"],
    prefix=True,
)

_matcher.shortcut(
    r"更新nb插件",
    command="nb商店",
    arguments=["update", "{%0}"],
    prefix=True,
)

_matcher.shortcut(
    r"查看可更新nb插件",
    command="nb商店",
    arguments=["can_update"],
    prefix=True,
)

_matcher.shortcut(
    r"更新全部nb插件",
    command="nb商店",
    arguments=["update_all"],
    prefix=True,
)


@_matcher.assign("$main")
async def _(
    session: EventSession,
    page: Match[int],
    page_size: Match[int],
    order_by: Match[str],
):
    _order_by = order_by.result if order_by.available else "time"
    try:
        result = await StoreManager.get_plugins_by_page(
            page.result, page_size.result, _order_by
        )
        logger.info(
            f"查看插件列表 orber_by: {_order_by}",
            "nb商店",
            session=session,
        )
        await MessageUtils.build_message(result).send()
    except Exception as e:
        logger.error("查看插件列表失败", "nb商店", session=session, e=e)
        await MessageUtils.build_message("获取插件列表失败...").send()


@_matcher.assign("add")
async def _(session: EventSession, plugin_id: str):
    try:
        await MessageUtils.build_message(f"正在添加插件: {plugin_id}").send()
        result = await StoreManager.add_plugin(plugin_id)
    except Exception as e:
        logger.error(f"添加插件 {plugin_id}失败", "nb商店", session=session, e=e)
        await MessageUtils.build_message(f"添加插件 {plugin_id} 失败 e: {e}").finish()
    logger.info(f"添加插件 {plugin_id}", "nb商店", session=session)
    await MessageUtils.build_message(result).send()


@_matcher.assign("remove")
async def _(session: EventSession, plugin_id: str):
    try:
        result = await StoreManager.remove_plugin(plugin_id)
    except Exception as e:
        logger.error(f"移除插件 {plugin_id}失败", "nb商店", session=session, e=e)
        await MessageUtils.build_message(f"移除插件 {plugin_id} 失败 e: {e}").finish()
    logger.info(f"移除插件 {plugin_id}", "nb商店", session=session)
    await MessageUtils.build_message(result).send()


@_matcher.assign("search")
async def _(
    session: EventSession,
    plugin_name_or_author: str,
    page: Match[int],
    page_size: Match[int],
    order_by: Match[str],
):
    _order_by = order_by.result if order_by.available else "time"
    try:
        result = await StoreManager.get_plugins_by_page(
            page.result, page_size.result, _order_by, query=plugin_name_or_author
        )
    except Exception as e:
        logger.error(
            f"搜索插件 name: {plugin_name_or_author}失败",
            "nb商店",
            session=session,
            e=e,
        )
        await MessageUtils.build_message(
            f"搜索插件 name: {plugin_name_or_author} 失败 e: {e}"
        ).finish()
    logger.info(f"搜索插件 name: {plugin_name_or_author}", "nb商店", session=session)
    await MessageUtils.build_message(result).send()


@_matcher.assign("update")
async def _(session: EventSession, plugin_id: str):
    try:
        await MessageUtils.build_message(f"正在更新插件 {plugin_id}").send()
        result = await StoreManager.update_plugin(plugin_id)
    except Exception as e:
        logger.error(f"更新插件 {plugin_id}失败", "nb商店", session=session, e=e)
        await MessageUtils.build_message(f"更新插件 {plugin_id} 失败 e: {e}").finish()
    logger.info(f"更新插件 {plugin_id}", "nb商店", session=session)
    await MessageUtils.build_message(result).send()


@_matcher.assign("can_update")
async def _(
    session: EventSession,
    page: Match[int],
    page_size: Match[int],
    order_by: Match[str],
):
    _order_by = order_by.result if order_by.available else "time"
    try:
        result = await StoreManager.get_plugins_by_page(
            page.result, page_size.result, _order_by, True
        )
    except Exception as e:
        logger.error("查看可更新插件失败", "nb商店", session=session, e=e)
        await MessageUtils.build_message(f"查看可更新插件失败 e: {e}").finish()
    logger.info("查看可更新插件", "nb商店", session=session)
    await MessageUtils.build_message(result).send()


@_matcher.assign("update_all")
async def _(session: EventSession):
    try:
        await MessageUtils.build_message("正在更新全部插件").send()
        result = await StoreManager.update_all_plugin()
    except Exception as e:
        logger.error("更新全部插件失败", "nb商店", session=session, e=e)
        await MessageUtils.build_message(f"更新全部插件失败 e: {e}").finish()
    logger.info("更新全部插件", "nb商店", session=session)
    await MessageUtils.build_message(result).send()
