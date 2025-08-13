from datetime import datetime

from nonebot.compat import model_dump
from pydantic import BaseModel


class TagDetail(BaseModel):
    label: str
    """标签名称"""
    color: str
    """十六进制标签颜色"""


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
    tags: list[TagDetail] = []
    """标签"""
    author: str
    """作者"""
    version: str
    """版本"""
    is_official: bool
    """是否为官方插件"""
    time: datetime
    """更新时间"""
    valid: bool
    """是否通过商店测试"""

    def to_dict(self, **kwargs):
        return model_dump(self, **kwargs)
