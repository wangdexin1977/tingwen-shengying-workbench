"""将用户选定的成片交给头条网页上传控件；永不点击最终发布。"""
import fcntl
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'settings/toutiao-publish'
URL = 'https://mp.toutiao.com/profile_v4/xigua/upload-video'
LOCK = threading.RLock()
BUSY = {'starting', 'waiting_login', 'waiting_input', 'uploading'}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    pending.replace(path)


def update(path, state, message):
    data = json.loads(path.read_text())
    data.update(state=state, message=message, updated=time.time())
    write(path, data)
    return data


def status(identifier):
    if str(uuid.UUID(identifier)) != identifier:
        raise ValueError('发布任务不存在')
    path = DATA / 'requests' / (identifier + '.json')
    data = json.loads(path.read_text())
    if data['state'] in BUSY:
        try:
            os.kill(int((DATA / 'worker.pid').read_text()), 0)
        except (OSError, ValueError):
            data = update(path, 'interrupted', '发布窗口已关闭或连接中断，请重新点击“发布到今日头条”。视频文件保留。')
    return data


def start(path, title):
    python = ROOT / '.publish-venv/bin/python'
    if not python.is_file():
        raise RuntimeError('自动上传组件尚未安装，请运行“安装头条发布组件.command”。')
    if not Path('/Applications/Google Chrome.app').exists():
        raise RuntimeError('自动上传需要 Google Chrome，请先安装 Chrome。')
    with LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        DATA.chmod(0o700)
        for request in (DATA / 'requests').glob('*.json'):
            previous = status(request.stem)
            if previous['state'] in BUSY:
                if previous['path'] == str(path):
                    return previous  # 重复点击同一视频不重复上传。
                raise RuntimeError('另一个视频正在等待登录或上传，请完成或关闭该发布标签页后再分享。')
        identifier = str(uuid.uuid4())
        data = dict(id=identifier, path=str(path), title=title, filename=path.name,
                    size=path.stat().st_size, state='starting',
                    message='正在打开头条专用发布窗口，随后自动载入此 MP4。',
                    created=time.time(), updated=time.time(), url=URL)
        # 先保证 worker 存在，再发布请求，避免并发检查把新请求误判成中断。
        try:
            os.kill(int((DATA / 'worker.pid').read_text()), 0)
        except (OSError, ValueError):
            with (DATA / 'worker.log').open('ab') as log:
                process = subprocess.Popen([str(python), '-B', str(Path(__file__).resolve()), '--worker'],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True)
            (DATA / 'worker.pid').write_text(str(process.pid))
        write(DATA / 'requests' / (identifier + '.json'), data)
        return data


def official(url):
    parsed = urlsplit(url)
    return parsed.scheme == 'https' and parsed.hostname == 'mp.toutiao.com'


def video_input(page):
    """只选择视频文件控件，排除封面、图片或不明文件控件。"""
    if not official(page.url):
        return None
    for frame in page.frames:
        if not official(frame.url):
            continue
        controls = frame.locator('input[type="file"]')
        for index in range(controls.count()):
            control = controls.nth(index)
            accept = (control.get_attribute('accept') or '').lower()
            if 'video' in accept or '.mp4' in accept:
                return control
            if not accept:
                label = control.evaluate('(el) => el.parentElement.innerText')
                if '视频' in label and '封面' not in label and '图片' not in label:
                    return control
    return None


def load_video(page, request):
    control = video_input(page)
    if control is None:
        return False
    path = Path(request['path'])
    if path.is_symlink() or not path.is_file() or path.stat().st_size != request['size']:
        raise ValueError('成片文件已变化，请回工作台重新选择该视频。')
    try:
        control.set_input_files(str(path), timeout=15000)
    except Exception as exc:
        # 页面可能已经开始传输，不盲目重试以免重复上传。
        raise RuntimeError('视频交接未确认成功，请检查发布窗口。为避免重复上传，本次已停止；原视频保留。') from exc
    # set_input_files 成功表示已交给页面，不表示平台上传/转码已完成。
    return True


def upload_result(page):
    """仅在网页明确显示上传成功时标记就绪，不把选中文件当成成功。"""
    if not official(page.url):
        return None
    text = page.locator('body').inner_text(timeout=2000)
    if any(marker in text for marker in ['上传失败', '视频上传失败', '文件格式不支持', '视频大小超过', '视频时长超过']):
        return 'failed'
    if any(marker in text for marker in ['上传成功', '上传完成']) and any(marker in text for marker in ['标题', '视频简介']):
        return 'ready'
    return None


def fill_title(page, title):
    # 不覆盖用户已输入内容；标签、声明和发布按钮由用户在平台操作。
    for control in page.locator('input[placeholder*="标题"], textarea[placeholder*="标题"]').all():
        if control.is_visible() and not control.input_value():
            control.fill(title[:30], timeout=2000)
            return


def worker():
    from playwright.sync_api import sync_playwright, Error
    DATA.mkdir(parents=True, exist_ok=True)
    lock = (DATA / 'browser.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    (DATA / 'worker.pid').write_text(str(os.getpid()))
    pages = {}
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(DATA / 'chrome-profile'), channel='chrome', headless=False,
                no_viewport=True, timeout=60000)
            while context.pages:
                for path in sorted((DATA / 'requests').glob('*.json')):
                    request = json.loads(path.read_text())
                    if request['state'] not in BUSY:
                        continue
                    identifier = request['id']
                    try:
                        if identifier not in pages:
                            if request['state'] != 'starting':
                                update(path, 'interrupted', '发布连接已重启，请重新选择视频；不会自动重复上传。')
                                continue
                            page = context.new_page()
                            pages[identifier] = page
                            page.bring_to_front()
                            try:
                                page.goto(URL, wait_until='domcontentloaded', timeout=45000)
                            except Error:
                                pass  # 页面仍可能继续加载；下面逐步检测。
                            update(path, 'waiting_input', '正在等待头条视频上传页面；如需登录，请在发布窗口完成登录。')
                        page = pages[identifier]
                        if page.is_closed():
                            update(path, 'cancelled', '发布标签页已关闭，原视频保留，可重新分享。')
                            continue
                        if time.time() - request['created'] > 12 * 3600:
                            update(path, 'interrupted', '已等待 12 小时，请检查头条页面后重新分享。')
                            continue
                        if request['state'] == 'uploading':
                            result = upload_result(page)
                            if result == 'failed':
                                update(path, 'failed', '头条页面提示上传失败，请在发布窗口查看原因或重试。原视频保留。')
                            elif result == 'ready':
                                update(path, 'ready', '头条页面已显示上传完成。请核对标题、填写标签等信息，再自行点击发布。')
                                try:
                                    fill_title(page, request['title'])
                                except Error:
                                    pass
                            continue
                        if load_video(page, request):
                            update(path, 'uploading', 'MP4 已自动选中并交给头条上传。请在发布窗口查看上传进度，填写标题与标签；完成后自行发布。')
                            try:
                                fill_title(page, request['title'])
                            except Error:
                                pass
                        elif '/login' in urlsplit(page.url).path or urlsplit(page.url).hostname == 'sso.toutiao.com':
                            # 登录可能在新标签页完成，成功后原发布标签页仍停在登录页。
                            if any(other != page and official(other.url) and '/profile_v4/' in urlsplit(other.url).path for other in context.pages):
                                page.goto(URL, wait_until='domcontentloaded', timeout=45000)
                                continue
                            if request['state'] != 'waiting_login':
                                update(path, 'waiting_login', '请在 Chrome 发布窗口登录今日头条；登录后会自动载入这个 MP4，无需寻找文件。')
                        elif official(page.url) and '/profile_v4/' in urlsplit(page.url).path and '/xigua/upload-video' not in page.url:
                            page.goto(URL, wait_until='domcontentloaded', timeout=45000)
                        elif time.time() - request['created'] > 180 and request['state'] != 'waiting_login':
                            update(path, 'failed', '没有识别到头条的视频上传控件。请检查账号发布权限或页面是否变化；可复制视频路径手动选择。')
                    except (ValueError, RuntimeError) as exc:
                        update(path, 'failed', str(exc))
                    except Error:
                        if pages.get(identifier) and pages[identifier].is_closed():
                            update(path, 'cancelled', '发布标签页已关闭，原视频保留。')
                        # 页面跳转或重绘时下一轮继续，不上传第二次。
                try:
                    context.pages[0].wait_for_timeout(1000)
                except (Error, IndexError):
                    if not context.pages:
                        break
    except Exception:
        for path in (DATA / 'requests').glob('*.json'):
            if json.loads(path.read_text())['state'] in BUSY:
                update(path, 'failed', 'Chrome 发布窗口启动或连接失败。请关闭旧的专用发布窗口后重试；检查 Chrome 和发布组件是否完整。')
    finally:
        if (DATA / 'worker.pid').exists() and (DATA / 'worker.pid').read_text() == str(os.getpid()):
            (DATA / 'worker.pid').unlink()
        lock.close()


if __name__ == '__main__':
    worker()
