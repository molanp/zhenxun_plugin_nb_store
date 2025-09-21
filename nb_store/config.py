from pathlib import Path

PLUGIN_FLODER = Path() / "nonebot_plugins"
PLUGIN_FLODER.mkdir(parents=True, exist_ok=True)

PLUGIN_INDEX = "https://registry.nonebot.dev/plugins.json"
"""nb插件列表"""

LOG_COMMAND = "nb商店"
