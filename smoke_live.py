"""收费烟测：必须显式 --allow-paid 且设置 ARK_API_KEY，仅提交一张测试图。"""
import json
import os
import sys
import time

import app as server


def main():
    if sys.argv[1:] != ["--allow-paid"] or not os.environ.get("ARK_API_KEY"):
        raise SystemExit("未执行：测试会调用一次付费豆包接口。需设置 ARK_API_KEY 并显式传入 --allow-paid；无自动重试。")
    client = server.app.test_client()
    headers = {"X-Local-Token": server.TOKEN}
    response = client.post("/api/jobs", headers=headers, json={
        "title": "山水之间", "text": "这是一段听文视频工作台的公开测试音频。远山与河流映着夕阳，画面与声音在这里相遇。",
        "prompt": "a peaceful mountain landscape at sunset, a river in the foreground, distant mountains, warm cinematic lighting, landscape photography, no text, no people",
        "voice": "zh-CN-YunxiNeural", "model": server.MODELS[0], "payment_consent": True,
    })
    if response.status_code != 201:
        raise RuntimeError(response.get_json())
    identifier = response.get_json()["id"]
    print("TEST_JOB", identifier, flush=True)
    deadline = time.monotonic() + 600
    previous = None
    while time.monotonic() < deadline:
        job = server.read_job(identifier)
        state = {k: job[k] for k in ("audio_state", "audio_message", "image_state", "image_message", "video_state", "video_message")}
        if state != previous:
            print(json.dumps(state, ensure_ascii=False), flush=True)
            previous = state
        if job["audio_state"] == "ready" and job["image_state"] == "ready" and job["video_state"] == "idle" and not server.ACTIVE:
            # 只确认本文件创建的测试素材，验证确认 API，不涉及用户任务。
            assert not (server.folder(identifier) / "video.mp4").exists()
            result = client.post(f"/api/jobs/{identifier}/confirm", headers=headers, json={"image_version": job["image_version"]})
            assert result.status_code == 200, result.get_json()
        if job["video_state"] == "done":
            print("LIVE_E2E_PASSED", str(server.folder(identifier) / "video.mp4"), flush=True)
            return
        if any(job[f"{part}_state"] == "failed" for part in ("audio", "image", "video")):
            client.post(f"/api/jobs/{identifier}/cancel", headers=headers, json={})
            time.sleep(1)
            raise RuntimeError("外部服务或合成失败；任务记录已保存")
        time.sleep(2)
    # 不重发；已提交的云端收费请求可能继续，保留任务记录。
    server.CONTROLS[identifier].cancelled.set()
    print("LIVE_E2E_PENDING: waiting time limit reached; no resubmission", flush=True)


if __name__ == "__main__":
    main()
