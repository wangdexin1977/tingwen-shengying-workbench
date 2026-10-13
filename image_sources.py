"""图片来源的薄适配层；新增来源只需注册客户端和配置入口。"""
from drawthings import DrawThingsClient
from seedream import SeedreamClient

SOURCES = {
    "drawthings": {"name": "Draw Things · 本地免费", "paid": False},
    "seedream": {"name": "豆包 Seedream · 云端收费", "paid": True},
}


def client_for(job, key=""):
    if job["provider"] == "drawthings":
        return DrawThingsClient(job.get("drawthings_port", 7861), job.get("image_settings", {}))
    if job["provider"] == "seedream":
        return SeedreamClient(key)
    raise ValueError("不支持的图片来源；不会自动切换到其他平台")
