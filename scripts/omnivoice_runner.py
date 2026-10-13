"""通过已安装的 MLX IndexTTS API 使用 OmniVoice；不安装依赖、不下载模型。"""
import contextlib
import json
import os
import signal
import sys
from pathlib import Path


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main(request):
    root = Path(os.environ['INDEXTTS_PROJECT_ROOT'])
    sys.path.insert(0, str(root)); os.chdir(root)
    with contextlib.redirect_stdout(sys.stderr):
        from mlx_indextts import webui
    if request.get('operation') == 'import':
        with contextlib.redirect_stdout(sys.stderr):
            entry, created = webui._store_voice_in_library(Path(request['file']), display_name=request['name'])
            if created and request.get('reference_text'):
                webui._update_voice_metadata(entry['id'], omnivoice_ref_text=request['reference_text'])
        emit({'ok': True, 'voice_id': entry['id'], 'created': created})
        return
    if request.get('operation') == 'document':
        with contextlib.redirect_stdout(sys.stderr):
            document = webui.import_document(request['file'], cache_dir=webui.DOCUMENT_CACHE_DIR)
        emit({'ok': True, 'document': document.to_dict(), 'text': document.text})
        return
    def cancel(*_): webui._generation_cancelled.set()
    signal.signal(signal.SIGTERM, cancel); signal.signal(signal.SIGINT, cancel)
    backend = request.get('backend', 'OmniVoice')
    values = {**webui.DEFAULT_SETTINGS, **webui.DEFAULT_OMNIVOICE_SETTINGS, **webui.DEFAULT_FISH_S2_SETTINGS, **webui.DEFAULT_COSYVOICE3_SETTINGS}
    profile = request.get('profile_snapshot')
    if not isinstance(profile, dict):
        profile = webui.read_user_config().get('model_profiles', {}).get(backend, {})
    for key in values:
        if key in profile: values[key] = profile[key]
    values.pop('model_backend', None); values.pop('emotion', None)
    labels = {'follow_reference': '跟随参考音频', 'calm': '平静', 'happy': '高兴', 'sad': '悲伤', 'energetic': '激昂', 'serious': '严肃', 'whisper': '耳语'}
    values.update(omnivoice_mode='clone', omnivoice_language='chinese', omnivoice_ref_text='', omnivoice_instruct='')
    values.update(request.get('parameters') or {})
    expression = labels[request['expression']]
    if backend == 'IndexTTS 2.0':
        expression = {'平静': '自然/平静', '激昂': '愤怒', '严肃': '自然/平静', '耳语': '跟随参考音频'}.get(expression, expression)
    if backend == 'IndexTTS 2.5': expression = '跟随参考音频'
    with contextlib.redirect_stdout(sys.stderr):
        audio, status, _ = webui.synthesize(text=Path(request['text_file']).read_text(encoding='utf-8'),
            voice_library_id=request['voice_id'], model_backend=backend, emotion_label=expression,
            output_format='wav', output_directory=request['output_directory'], persist_output_settings=False,
            progress=lambda value, desc='': emit_progress(value, desc), **values)
    if not audio or not Path(audio).is_file() or not Path(audio).stat().st_size:
        raise RuntimeError(status or 'OmniVoice 未返回有效音频')
    emit({'ok': True, 'audio_path': audio, 'status': status})


def emit_progress(value, desc):
    # The inference code redirects stdout; emit protocol messages to the original stream.
    current, total = value if isinstance(value, (tuple, list)) and len(value) == 2 else (None, None)
    print(json.dumps({'event': 'progress', 'current': current, 'total': total, 'message': desc}, ensure_ascii=False), file=sys.__stdout__, flush=True)


if __name__ == '__main__':
    try: main(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    except Exception as exc:
        emit({'ok': False, 'error': str(exc)[:1000]}); sys.exit(1)
