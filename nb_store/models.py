from nonebot.compat import model_dump
from pydantic import BaseModel


class StorePluginInfo(BaseModel):
    """插件信息"""

    name: str
    """插件名"""
    module_name: str
    """模块名"""
    project_link: str
    """pypi包名"""
    desc: str
    """简介"""
    tags: list[dict[str, str]] = []
    """标签"""
    author: str
    """作者"""
    version: str
    """版本"""

    def to_dict(self, **kwargs):
        return model_dump(self, **kwargs)
