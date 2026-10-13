"""AI Horde 官方 v2 异步接口。密钥只保留在进程内，不写入任务或日志。"""
import base64
import io
import json
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image

API = "https://aihorde.net/api/v2"
ANONYMOUS = "0000000000"


class HordeError(RuntimeError):
    pass


class SubmissionUnknown(HordeError):
    pass


class HordeClient:
    def __init__(self, key="", transport=None):
        self.key = key or ANONYMOUS
        self.transport = transport

    def request(self, method, path, data=None):
        if self.transport:
            return self.transport(method, path, data)
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(API + path, data=body, method=method, headers={
            "Content-Type": "application/json", "apikey": self.key,
            "Client-Agent": "TingwenVideoWorkbench:1.0:local",
            "User-Agent": "TingwenVideoWorkbench/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read(20 * 1024 * 1024 + 1)
                if len(raw) > 20 * 1024 * 1024:
                    raise HordeError("接口返回内容过大")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            code = exc.code
            # 不回显响应体，避免密钥或用户提示词混入日志。
            messages = {401: "API Key 无效", 403: "请求被拒绝，请检查账号权限或请求限制",
                        404: "云端任务已过期或不存在", 429: "请求过于频繁或匿名额度受限，请稍后重试"}
            raise HordeError(messages.get(code, f"AI Horde 返回 HTTP {code}")) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if method == "POST":
                raise SubmissionUnknown("提交超时，无法确定云端是否收到；不会自动重复提交。请检查网络后手动重试。") from None
            raise HordeError("无法连接 AI Horde，请检查网络后重试") from None

    def submit(self, prompt, model):
        payload = {
            "prompt": prompt, "models": [model],
            "params": {"width": 640, "height": 384, "steps": 20, "n": 1,
                       "sampler_name": "k_euler", "cfg_scale": 7.5},
            "nsfw": False, "censor_nsfw": True, "trusted_workers": True,
            "validated_backends": True, "slow_workers": True,
            "r2": False, "shared": False, "allow_downgrade": True,
        }
        result = self.request("POST", "/generate/async", payload)
        try:
            return str(uuid.UUID(result["id"]))
        except (KeyError, ValueError, TypeError):
            raise SubmissionUnknown("没有取得有效云端任务 ID；为避免重复请求，不自动重试") from None

    def poll(self, remote_id, control, progress, interval=10, max_seconds=3600):
        remote_id = str(uuid.UUID(remote_id))
        start = time.monotonic()
        failures = 0
        while time.monotonic() - start < max_seconds:
            control.check_cancel()
            try:
                state = self.request("GET", f"/generate/check/{remote_id}")
                failures = 0
            except HordeError as exc:
                failures += 1
                progress(f"查询暂时失败 ({failures}/6)：{exc}")
                if failures >= 6:
                    raise
                control.sleep(min(30, interval * failures))
                continue
            if state.get("faulted"):
                raise HordeError("云端生成失败；可以重新生成图片，音频将保留")
            if state.get("done"):
                result = self.request("GET", f"/generate/status/{remote_id}")
                images = result.get("generations", [])
                if not images:
                    raise HordeError("任务结束但没有返回图片")
                image = images[0]
                if image.get("censored"):
                    raise HordeError("图片被安全过滤，请修改要求后重新生成")
                try:
                    raw = base64.b64decode(image["img"], validate=True)
                    with Image.open(io.BytesIO(raw)) as picture:
                        if picture.width * picture.height > 16000000:
                            raise ValueError("图片尺寸过大")
                        picture.load()
                        png = io.BytesIO()
                        picture.convert("RGB").save(png, format="PNG")
                        dimensions = [picture.width, picture.height]
                    return png.getvalue(), {"model": image.get("model", ""),
                        "seed": image.get("seed", ""), "dimensions": dimensions,
                        "shared": result.get("shared", self.key == ANONYMOUS)}
                except (KeyError, ValueError, OSError):
                    raise HordeError("返回的图片数据无效，无法用于合成") from None
            if not state.get("is_possible", True):
                progress("目前没有兼容的在线算力，正在等待；可取消后换模型")
            else:
                progress(f"排队位置 {state.get('queue_position', '?')} · "
                         f"预计约 {state.get('wait_time', '?')} 秒（仅参考） · "
                         f"{'正在绘制' if state.get('processing') else '等待算力'}")
            # 暂停不阻止云端绘制；继续查询并保存返回素材，不启动后续合成。
            control.sleep(interval)
        raise HordeError("已等待一小时；云端任务 ID 已保留，可继续查询而不重新提交")

    def cancel(self, remote_id):
        return self.request("DELETE", f"/generate/status/{str(uuid.UUID(remote_id))}")
