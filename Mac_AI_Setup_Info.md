# Mac AI 语音转译基础环境配置记录

本文件记录了当前 Mac (Apple Silicon M4) 上已安装的 AI 语音转译基础条件，供后续项目配置（如 WhisperX）参考使用。

---

## 1. 硬件环境与代理
* **硬件处理器**：Apple Silicon M4 芯片（支持 Unified Memory 统一内存，GPU 具备强大的 Metal 硬件加速能力）。
* **网络代理**：本地运行有 Clash Verge 代理，本地端口为 `7897`。
  * 终端中访问 GitHub / Hugging Face 时，可使用以下环境变量配置：
    ```bash
    export http_proxy=http://127.0.0.1:7897
    export https_proxy=http://127.0.0.1:7897
    ```

---

## 2. 已安装的基础条件与命令行工具

以下工具均已通过 Homebrew 安装，并成功配置至系统环境变量中：

### ① FFmpeg (音频处理利器)
* **安装路径**：`/opt/homebrew/bin/ffmpeg`
* **用途**：用于对各种格式的音频文件（如 MP3 等）进行重采样、分道或转换，将其转为语音识别专用的 WAV 格式。
* **常用转换命令（16kHz、单声道、16-bit PCM）**：
  ```bash
  ffmpeg -y -i "input.mp3" -ar 16000 -ac 1 -c:a pcm_s16le "output.wav"
  ```

### ② Whisper.cpp (C++ 原生优化版本)
* **安装路径**：`/opt/homebrew/bin/whisper-cli`
* **技术特性**：支持 macOS GPU (Metal) 原生推理加速，大幅提高转译效率并保持低 CPU 负荷。
* **已下载的大模型**：
  * **模型路径**：`/Users/liuyindyx/Models/whisper/ggml-large-v3-turbo.bin` (大小约 1.5 GB，GGML 格式)
* **防幻听/防复读优化运行命令**：
  ```bash
  whisper-cli -m ~/Models/whisper/ggml-large-v3-turbo.bin -f "input.wav" -l zh -otxt -fa --max-context 32 -of "output_prefix"
  ```
  *(注：在 `whisper.cpp` 中运行较长录音时，建议显式声明 `--max-context 32`，可有效防止陷入循环性复读幻听。)*

### ③ GitHub CLI (gh)
* **安装路径**：`/opt/homebrew/bin/gh`
* **用途**：用于在终端直接拉取和管理 GitHub 项目。

---

## 3. 本地音频标准规范

无论是使用 `whisper.cpp` 还是后续的 `WhisperX`，请确保传入的音频符合如下语音识别标准规范：
* **采样率 (Sample Rate)**：16000 Hz (16kHz)
* **声道数 (Channels)**：1 (单声道/Mono)
* **编码格式 (Codec)**：16-bit 线性 PCM (`pcm_s16le`)
* **容器格式**：`.wav`
