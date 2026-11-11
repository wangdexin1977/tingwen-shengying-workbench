# OmniVoice 停顿与运行库升级

核对日期：2026-10-30。

## 核对结果

- 官方 OmniVoice 最新正式发布仍为 [0.2.1](https://github.com/k2-fsa/OmniVoice/releases/tag/0.2.1)。本产品默认运行 Apple MLX 移植，不能把官方 Python 包版本与 MLX 运行库版本混为一谈。
- MLX 运行库最新正式发布为 [mlx-audio 0.6.3](https://github.com/Blaizzy/mlx-audio/releases/tag/v0.6.3)。原本机环境为 0.4.6；新建 `tts/.omnivoice-venv/` 安装 0.6.3、MLX / MLX Metal 0.32.4、Transformers 5.14.0，其他只读依赖沿用已有 TTS 环境。
- 当前两份 bfloat16 模型权重的实际 SHA256 均与线上最新文件一致。仓库 `mlx-community/OmniVoice-bfloat16` 修订为 `6119f7072580cc30c3cbdf496269216b03ad9742`，无须重复下载模型。
- 原 `tts/.venv/` 保留，Fish、IndexTTS、CosyVoice、数字人继续使用原环境。只有工作台默认 OmniVoice 合成任务在独立环境验证后切换；音色导入和文档处理沿用原环境。

## 停顿原因与修复

1. MLX 0.4.6 和 0.6.3 的 Omni 生成代码均使用固定英文示例估算时长，没有把实际参考音频语速用于时长估计。现适配器参照官方 `_estimate_target_tokens`，使用对齐的参考原文和编码帧数估计目标时长，语速参数作用于这一估计；显式总时长仍优先。
2. 原来优先填满字符上限，可能把完整句子拆在逗号处。现优先组合完整句子；只有单句确实超过安全上限时才继续在逗号等位置拆分。
3. 原来每段裁去首尾静音后统一再补 250ms，模型已有停顿与人工停顿叠加。现将段间值作为最小停顿，只补不足部分；外层长文批次也采用同样规则，其他模型的批次逻辑不变。
4. 参照官方输出清理，保留普通停顿，压缩异常长的近静音，并在原有首尾静音中保留 100ms。没有通过删掉文案、减少推理步数或降低权重精度来缩短音频。

参考：[官方生成参数](https://github.com/k2-fsa/OmniVoice/blob/0.2.1/docs/generation-parameters.md)、[官方模型实现](https://github.com/k2-fsa/OmniVoice/blob/0.2.1/omnivoice/models/omnivoice.py)。

## 验证与启用

隔离环境通过模型加载、实际生成及专项测试后，写入 `tts/.omnivoice-venv/validated.json`，由 `omnivoice_local.runtime_python()` 选择。没有环境或有效验证记录时继续使用原环境；原环境也可使用适配器中的停顿修复。环境与验证音频均被 Git 忽略。

```sh
./.venv/bin/python -B scripts/run_tests.py
./tts/.omnivoice-venv/bin/python -B -m unittest test_omnivoice_pauses test_omnivoice_text -v
```

同一音色、同一份 243 字文稿与相同参数（32 步、guidance 2、seed 42、speed 1）对比：旧结果 54.78 秒，升级并修复后约 45.73 秒；超过 600ms 的静音由 8 处降至 0 处，最长由 1.11 秒降至约 0.53 秒。统计采用 10ms RMS、-45dB 阈值，仅描述该样本，不等于每篇文稿或每个音色都具有同样效果。ASR 对比未发现整句内容缺失，存在同音字识别差异和疑似口语填词，仍需试听判断听感。

原稿、音色参考文件和原有任务成品保留。修复应用于新生成或重试的音频，不会改写历史 MP3。

完整 1907 字原稿使用相同音色与参数完成 19 段生成，音质检查通过。原成品约 425.41 秒，新版约 354.26 秒；超过 600ms 的静音 65→19，最长 1.85→0.95 秒。正常停顿仍保留，没有以“静音数量为零”作为目标。

165 项主工作台隔离回归和 12 项语音专项通过。实际工作台 API 已验证：新建 OmniVoice 任务调用独立环境 Python，完成 WAV→MP3、媒体验证与耗时保存；新环境的启用状态显示在音色库提示中。代码复查未发现重要问题。
