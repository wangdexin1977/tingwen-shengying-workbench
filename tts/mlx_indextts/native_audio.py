"""Native long-audio playback with the existing Gradio file response contract."""

from pathlib import Path

import gradio as gr
from gradio.data_classes import FileData


class NativeAudio(gr.HTML):
    """Serve audio as a file without creating a Web Audio waveform buffer."""

    data_model = FileData

    def __init__(
        self, value=None, *, label=None, autoplay=False, visible=True,
        elem_classes=None, render=True,
    ):
        self.autoplay = autoplay
        super().__init__(
            value=value, label=label, show_label=True, container=True,
            visible=visible, elem_classes=elem_classes, autoplay=autoplay, render=render,
            html_template='''
                <audio controls preload="metadata" style="width:100%" hidden></audio>
                <a download hidden>下载音频</a>
                <span class="native-audio-placeholder">生成后可播放或下载</span>
            ''',
            js_on_load='''
                const update = () => {
                    const audio = element.querySelector("audio");
                    const link = element.querySelector("a");
                    const placeholder = element.querySelector("span");
                    const file = props.value;
                    const url = file?.url;
                    audio.hidden = !url;
                    link.hidden = !url;
                    placeholder.hidden = !!url;
                    if (!url) {
                        audio.pause();
                        audio.removeAttribute("src");
                        audio.load();
                        link.removeAttribute("href");
                    } else if (audio.getAttribute("src") !== url) {
                        audio.src = url;
                        link.href = url;
                        link.download = file.orig_name || "audio";
                        audio.load();
                        if (props.autoplay) audio.play().catch(() => {});
                    }
                };
                watch(["value", "autoplay"], update);
                update();
            ''',
        )

    def postprocess(self, value):
        if not value:
            return None
        if isinstance(value, FileData):
            return value
        if isinstance(value, dict):
            return FileData.model_validate(value)
        path = Path(value)
        return FileData(path=str(path), orig_name=path.name)

    def api_info(self):
        return gr.components.Component.api_info(self)
