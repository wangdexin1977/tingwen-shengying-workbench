"""火山方舟官方图片接口；单次提交，不自动重试收费请求。"""
import base64
import io
import json
import re
import urllib.error
import urllib.request

from PIL import Image

ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
MODELS = ["doubao-seedream-5-0-pro-260628", "doubao-seedream-5-0-flash-260915"]


class SeedreamError(RuntimeError):
    pass


class SubmissionUnknown(SeedreamError):
    pass


class SeedreamClient:
    def __init__(self, key, transport=None):
        self.key = key
        self.transport = transport or self.request

    def request(self, body):
        request = urllib.request.Request(
            ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read(48 * 1024 * 1024 + 1)
                if len(raw) > 48 * 1024 * 1024:
                    raise SubmissionUnknown("豆包响应过大；请求可能已计费，不会自动重发")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # 429 不一定是临时限流；保留官方错误码，不能靠 HTTP 状态猜原因。
            code, detail = "", ""
            try:
                error = json.loads(exc.read(64 * 1024)).get("error", {})
                code = re.sub(r"[^a-zA-Z0-9_.-]", "", str(error.get("code", "")))[:120]
                detail = str(error.get("message", "")).replace(self.key, "[密钥已隐藏]")
                detail = re.sub(r"(?i)bearer\s+\S+", "Bearer [密钥已隐藏]", detail)
                detail = re.sub(r"https?://\S+", "[服务文档链接]", detail)[:700]
            except (ValueError, AttributeError, OSError):
                pass
            messages = {400: "参数或图片要求被拒绝", 401: "API Key 无效，请检查或更新",
                        403: "无模型访问权限，请在方舟开通所选模型", 404: "模型不存在或未开通",
                        429: "额度、余额或并发受限，请检查方舟控制台"}
            if exc.code >= 500:
                raise SubmissionUnknown("豆包服务异常；请求结果未知，可能已计费，不会自动重发") from None
            description = messages.get(exc.code, "请求失败")
            if "InsufficientBalance" in code or "Arrearage" in code:
                description = "账户余额不足或欠费；需要在火山方舟处理账户余额"
            elif code == "SetLimitExceeded":
                description = "所选模型达到用量上限／安心体验限额，方舟已暂停该模型。请在控制台“开通管理”检查并调整限额后重试（不会自动关闭费用保护）"
            elif "Quota" in code or "LimitExceeded" in code:
                description = "模型调用额度或限额不足，请检查该模型的额度"
            elif "NotOpen" in code or "NotActivated" in code:
                description = "该模型尚未开通，请在火山方舟开通模型"
            suffix = f"；{detail}" if detail else ""
            raise SeedreamError(f"豆包接口：{description}（HTTP {exc.code}" + (f" · {code}" if code else "") + ")" + suffix) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise SubmissionUnknown("豆包连接超时或响应异常；请求可能已完成并计费。请查看方舟用量后决定是否重发") from None

    def generate(self, prompt, model):
        if not self.key or model not in MODELS:
            raise SeedreamError("请提供有效的 API Key 和图片模型")
        body = {"model": model, "prompt": prompt, "size": "2K", "response_format": "b64_json",
                "output_format": "png", "watermark": True}
        # 直接返回图片数据，不依赖短期 URL，也不下载不受信任的地址。
        result = self.transport(body)
        try:
            items = result.get("data", [])
            if len(items) != 1 or items[0].get("error") or result.get("error"):
                raise ValueError()
            encoded = items[0].get("b64_json", "")
            if not isinstance(encoded, str) or len(encoded) > 44 * 1024 * 1024:
                raise ValueError()
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 16_000_000 or image.width < 64 or image.height < 64:
                    raise ValueError()
                image.load()
                dimensions = [image.width, image.height]
                output = io.BytesIO()
                image.convert("RGB").save(output, format="PNG")
            return output.getvalue(), {"model": model, "dimensions": dimensions,
                                       "size": items[0].get("size", "2K"), "watermark": True}
        except (ValueError, TypeError, KeyError, AttributeError, OSError, Image.DecompressionBombError):
            raise SubmissionUnknown("豆包未返回可用的单张图片；请求可能已计费，请检查用量，不会自动重发") from None
