"""Draw Things 官方 HTTP API；只连接本机，不自动回退云端。"""
import base64
import io
import json
import urllib.error
import urllib.request

from PIL import Image


class DrawThingsError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DrawThingsClient:
    def __init__(self, port=7861, settings=None, transport=None):
        if isinstance(port, bool) or not str(port).isdigit() or not 1024 <= int(port) <= 65535:
            raise DrawThingsError("本机端口必须是 1024–65535 的整数")
        self.port = int(port)
        self.settings = settings or {}
        self.transport = transport or self.request

    def request(self, path, body=None):
        url = f"http://127.0.0.1:{self.port}" + path
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"})
        # 不使用系统代理，不允许本机端点重定向到外网。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(req, timeout=5 if body is None else 600) as response:
                raw = response.read(48 * 1024 * 1024 + 1)
                if len(raw) > 48 * 1024 * 1024:
                    raise DrawThingsError("本地图片响应过大")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise DrawThingsError("Draw Things HTTP 接口拒绝连接；请检查本机 API 设置") from None
            if body is None:
                raise DrawThingsError("这个端口不是可用的 Draw Things HTTP 接口；请检查端口与协议") from None
            raise DrawThingsError(f"Draw Things 拒绝生成（HTTP {exc.code}）；请检查模型已下载以及采样参数") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DrawThingsError(f"无法连接或等待 Draw Things 超时。请打开应用，启用 localhost HTTP API，端口设为 {self.port}；不要重启其他服务。已提交的本地生成可能仍继续，不会自动重发") from None
        except (ValueError, UnicodeError):
            raise DrawThingsError("端口返回的不是 Draw Things JSON 数据，请检查是否连接了其他应用") from None

    def inspect(self):
        config = self.transport("/sdapi/v1/options")
        if not isinstance(config, dict) or not {"model", "sampler", "steps", "guidance_scale", "batch_count"} <= config.keys():
            raise DrawThingsError("这个端口未返回 Draw Things 配置；不会向未知服务提交文案")
        model = config["model"]
        if not isinstance(model, str) or not model.endswith(".ckpt"):
            raise DrawThingsError("请先在 Draw Things 下载并选择一个图片模型")
        # 固定单张图片，其他核心采样设置沿用应用当前配置并记录快照。
        settings = {key: config[key] for key in ("steps", "sampler", "guidance_scale")}
        if not isinstance(settings["steps"], int) or not 1 <= settings["steps"] <= 150:
            raise DrawThingsError("Draw Things 步数配置无效")
        if not isinstance(settings["sampler"], str) or not isinstance(settings["guidance_scale"], (int, float)):
            raise DrawThingsError("Draw Things 采样配置无效")
        return {"model": model, "settings": settings, "port": self.port}

    def generate(self, prompt, model):
        self.inspect()  # 确认端口归属；不修改任务创建时保存的模型与参数。
        body = {"prompt": prompt, "negative_prompt": "", "model": model, "width": 768, "height": 512,
                "seed": -1, "batch_count": 1, "batch_size": 1, "hires_fix": False,
                **self.settings}
        result = self.transport("/sdapi/v1/txt2img", body)
        try:
            images = result.get("images", [])
            if len(images) != 1 or not isinstance(images[0], str) or len(images[0]) > 44 * 1024 * 1024:
                raise ValueError()
            raw = base64.b64decode(images[0], validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 16_000_000 or min(image.size) < 64:
                    raise ValueError()
                image.load()
                dimensions = list(image.size)
                output = io.BytesIO()
                image.convert("RGB").save(output, format="PNG")
            return output.getvalue(), {"model": model, "dimensions": dimensions, "local": True,
                                       "settings": self.settings}
        except (ValueError, TypeError, AttributeError, OSError, Image.DecompressionBombError):
            raise DrawThingsError("Draw Things 未返回可用的单张图片，请检查应用中的生成结果") from None
