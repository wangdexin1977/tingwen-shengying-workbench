"""本机浏览器回归：模拟上传控件、合成麦克风；不访问用户账号。"""
import base64
import json
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import toutiao_publish as publish


def check_upload(playwright, directory):
    browser = playwright.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page()
    fixture = '''<h1>本机上传测试</h1><input type=file accept="image/*" id=cover>
    <input type=file accept="video/*" id=video hidden><input placeholder="填写视频标题">
    <p id=status>请上传视频</p><button onclick="window.published=true">发布</button>
    <script>window.published=false;video.onchange=()=>{status.textContent='视频上传中 40% 标题';};</script>'''
    page.route(publish.URL, lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=fixture))
    page.goto(publish.URL)
    source = directory / '中文成片测试.mp4'
    source.write_bytes(b'test-file')
    data = {'path': str(source), 'size': source.stat().st_size}
    assert publish.load_video(page, data)
    selected = page.locator('#video').evaluate('(el)=>({name:el.files[0].name,size:el.files[0].size})')
    assert selected == {'name': source.name, 'size': source.stat().st_size}
    assert page.locator('#cover').evaluate('(el)=>el.files.length') == 0
    assert publish.upload_result(page) is None
    publish.fill_title(page, '稿件标题')
    expect(page.get_by_placeholder('填写视频标题')).to_have_value('稿件标题')
    page.get_by_placeholder('填写视频标题').fill('用户修改过的标题')
    publish.fill_title(page, '不可覆盖')
    expect(page.get_by_placeholder('填写视频标题')).to_have_value('用户修改过的标题')
    page.locator('#status').evaluate('(el)=>el.textContent="上传成功 标题 标签"')
    assert publish.upload_result(page) == 'ready'
    assert not page.evaluate('window.published')
    browser.close()
    print('PASS: 真实文件控件、正确 MP4、封面排除、进度/完成区别、保留标题、未点击发布')


def check_recording(playwright, directory):
    browser = playwright.chromium.launch(channel='chrome', headless=True,
        args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'])
    context = browser.new_context(permissions=['microphone'])
    page = context.new_page()
    captured = []
    identifier = 'a' * 16
    voices = [{'id': identifier, 'name': '浏览器录音测试', 'is_favorite': False}]
    stored_preferences = {'controls': {'audioProvider': 'omnivoice', 'omniVoice': identifier,
                                      'speechSpeed': 1.25, 'speechSilence': 450, 'omniExpression': 'happy'}}
    def fixture_api(route):
        nonlocal stored_preferences
        endpoint = route.request.url.split('/api/', 1)[1]
        if endpoint == 'omnivoice/voices':
            body = route.request.post_data_json
            captured.append(body)
            result = {'created': True, 'voice_id': identifier, 'voices': voices}
        elif endpoint == 'omnivoice':
            result = {'available': True, 'voices': voices, 'message': '测试音色库就绪'}
        elif endpoint == 'preferences':
            if route.request.method == 'POST':
                stored_preferences = route.request.post_data_json
            result = {'preferences': stored_preferences}
        elif endpoint == 'queue':
            result = {'order': [], 'projects': [], 'running': False, 'message': '本机测试'}
        elif endpoint == 'jobs':
            result = {'jobs': []}
        else:
            route.continue_()
            return
        route.fulfill(content_type='application/json', body=json.dumps(result))
    page.route('**/api/**', fixture_api)
    page.goto('http://127.0.0.1:8092/')
    expect(page.locator('#settingsSaveStatus')).to_contain_text('配置')
    page.get_by_label('语音来源', exact=True).select_option('omnivoice')
    expect(page.locator('#currentCloneVoice')).to_contain_text('浏览器录音测试')
    expect(page.locator('#localBackend')).to_be_hidden()
    expect(page.locator('#speechSpeed')).to_be_hidden()
    expect(page.locator('#voiceParametersSummary')).to_contain_text('1.25')
    page.locator('#cloneVoicePicker > summary').click()
    expect(page.locator('#localBackend')).to_be_visible()
    page.locator('#cloneVoicePicker > summary').click()
    page.locator('#voiceParameters > summary').click()
    expect(page.locator('#speechSpeed')).to_be_visible()
    page.get_by_label('分段停顿（毫秒）', exact=True).fill('650')
    page.locator('#voiceParameters > summary').click()
    expect(page.locator('#speechSpeed')).to_be_hidden()
    expect(page.locator('#speechSilence')).to_have_value('650')
    expect(page.locator('#settingsSaveStatus')).to_have_text('配置已自动保存')
    page.reload()
    expect(page.locator('#settingsSaveStatus')).to_have_text('配置已自动保存')
    expect(page.locator('#speechSilence')).to_have_value('650')
    expect(page.locator('#speechSpeed')).to_be_hidden()
    expect(page.locator('#voiceParametersSummary')).to_contain_text('650')
    print('PASS: 音色/模型和语音参数默认折叠、按需展开、折叠后参数生效及重新打开恢复')
    page.locator('#addOmniVoicePanel summary').click()
    page.get_by_role('button', name='🎙 开始录音', exact=True).click()
    expect(page.locator('#voiceRecordingStatus')).to_contain_text('正在录音')
    page.wait_for_timeout(1500)  # 确实采集合成麦克风数据，不是模拟 MediaRecorder。
    page.get_by_role('button', name='停止录音', exact=True).click()
    expect(page.locator('#voiceRecordingStatus')).to_contain_text('已载入')
    expect(page.locator('#voiceRecordingPreview')).to_be_visible()
    page.get_by_label('音色名称', exact=True).fill('我的浏览器测试音色')
    page.get_by_role('button', name='命名并保存音色', exact=True).click()
    expect(page.locator('#voiceRecordingStatus')).to_contain_text('保存完成')
    assert captured[0]['name'] == '我的浏览器测试音色'
    recording = base64.b64decode(captured[0]['audio_data'])
    assert len(recording) > 1000
    (directory / captured[0]['filename']).write_bytes(recording)
    expect(page.locator('#omniVoice')).to_have_value(identifier)
    assert page.evaluate('voiceStream===null')
    page.get_by_role('button', name='重新选择 / 清除录音', exact=True).click()
    expect(page.locator('#voiceRecordingPreview')).to_be_hidden()
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name='上传已有录音', exact=True).click()
    chooser.value.set_files(str(directory / captured[0]['filename']))
    expect(page.locator('#voiceRecordingStatus')).to_contain_text('已载入')
    page.get_by_label('音色名称', exact=True).fill('上传的录音音色')
    page.get_by_role('button', name='命名并保存音色', exact=True).click()
    expect(page.locator('#voiceRecordingStatus')).to_contain_text('保存完成')
    assert len(captured) == 2 and captured[1]['name'] == '上传的录音音色'
    print('PASS: 浏览器录音、停止与释放麦克风、试听、命名保存、自动选中、清除')
    print('PASS: 上传已有录音按钮、文件载入试听及命名保存')
    context.close()
    denied = browser.new_context(permissions=[])
    denied_page = denied.new_page()
    denied_page.route('**/api/**', fixture_api)
    denied_page.add_init_script('navigator.mediaDevices.getUserMedia=()=>Promise.reject(new DOMException("拒绝","NotAllowedError"));')
    denied_page.goto('http://127.0.0.1:8092/')
    expect(denied_page.locator('#settingsSaveStatus')).to_contain_text('配置')
    denied_page.get_by_label('语音来源', exact=True).select_option('omnivoice')
    denied_page.locator('#addOmniVoicePanel summary').click()
    denied_page.get_by_role('button', name='🎙 开始录音', exact=True).click()
    expect(denied_page.locator('#voiceRecordingStatus')).to_contain_text('未获得麦克风权限')
    expect(denied_page.get_by_role('button', name='上传已有录音')).to_be_enabled()
    browser.close()
    print('PASS: 麦克风权限拒绝时可改用文件上传')


with tempfile.TemporaryDirectory(prefix='shengying-browser-test-') as temporary:
    with sync_playwright() as playwright:
        check_upload(playwright, Path(temporary))
        check_recording(playwright, Path(temporary))
