# 会议转译与声纹管理系统 (Voiceprint System)

本系统是一个完全独立、可插拔的会议转译与精准发言人识别及声纹管理外挂模块。系统针对 **Mac (Apple Silicon)** 进行本地运行优化，整合了 WhisperX 高精度转译对齐能力与 SpeechBrain 声纹识别技术，并配套提供了现代化的暗黑科技风格 Web 管理界面。

---

## 🌟 核心特性

1. **零侵入解耦设计**：作为外挂模块与 WhisperX 核心代码物理隔离，保证底层依赖的独立与洁净。
2. **多原型声纹匹配 (Multi-Prototype Matching)**：支持每个发言人名下存入多段不同设备、不同环境的声纹特征（`.npy`），采用最近邻相似度比对。
3. **拼合人声特征提取 (Combined Embedding)**：自动拼合同一说话人在整场会议中质量最高的数段音频切片进行声纹提取，提升声纹表征的鲁棒性。
4. **全局未知声纹池与跨会议识别**：未识别的声音自动归入全局 `_unknown/` 声纹池，并保存配套人声片段音频（`.wav`）；相同的未知发言人在不同会议中可被识别为同一个 `unknown_id`。
5. **AI 纪要与深度总结**：后端集成 API 自动调用，可优先使用 DeepSeek (或备用 Google Gemini-1.5-Flash) 自动根据会议转译文本，提炼出“发言角色与观点”、“时间线讨论章节”、“分主题多维总结与 To-Do List”。
6. **双层声纹再匹配与库治理**：
   * **会议内局部再匹配**：在网页浏览时可随时对某场会议内匹配错误的发言人进行重命名或重映射，并自动反向更新该会议文档。
   * **全局库样本治理**：声纹库支持逐条试听人声片段、删除或将特征样本“移动/重新划分”给其他人。
7. **一键丢弃无效声音**：在标定队列中对混杂声音或环境噪音提供“丢弃”功能，防止无效声纹常驻队列。

---

## 📁 目录结构

```text
voiceprint_system/
├── database/               # 全局声纹特征数据库
│   ├── 刘银/               # 已命名发言人声纹目录 (含 .npy 特征与 .wav 样本)
│   └── _unknown/           # 全局未知声纹暂存池 (含 ID 目录、feature.npy 与 feature.wav)
├── src/
│   ├── audio.py            # 音频预处理：响度归一化、带通滤波与重采样
│   ├── voiceprint.py       # 声纹提取、库比对、晋升与反向更新历史文档逻辑
│   ├── adapters.py         # ASR 适配器：兼容 WhisperX 与 whisper.cpp 引擎
│   ├── pipeline.py         # 转译流水线控制：输出会议目录、生成元数据
│   └── summary.py          # AI 会议纪要总结：轻量化 urllib 调用大模型 API
├── app.py                  # 现代化暗黑科技风格 Web 管理界面 (Streamlit)
├── main.py                 # 主运行入口与命令行交互式标注逻辑
├── requirements.txt        # 外挂依赖文件
└── .gitignore              # Git 忽略配置
```

在系统运行转译后，输出结果将自动归档至项目根目录同级的 `meetings/` 专属目录下：
```text
meetings/[会议名称]/
├── transcript.json         # 包含字级时间轴和声纹标签映射的完整转译数据
├── transcript.md           # 美化排版后的发言人分段对话文档
├── summary.md              # AI 大模型自动生成的会议纪要总结文档
├── meeting_meta.json       # 记录发言人标签 (SPEAKER_XX) 到声纹库的引用关系元数据
└── run_transcription.log   # 异步转译过程中的实时运行日志
```

---

## 🛠 环境依赖与运行

### 1. 安装核心依赖
在项目根目录下运行以载入所有依赖：
```bash
# 同步 WhisperX 环境
uv pip install -e .

# 安装声纹外挂所需的附加包
uv pip install -r voiceprint_system/requirements.txt
```

### 2. 配置大模型 API 密钥 (用于 AI 总结)
在系统环境变量中配置以下任一密钥：
```bash
export DEEPSEEK_API_KEY="您的 DeepSeek API Key"
# 或
export GOOGLE_API_KEY="您的 Gemini API Key"
```

### 3. 启动 Web 交互后台 (推荐)
系统配备了全功能的 Web 页面，运行以下命令即可启动浏览器管理后台：
```bash
uv run streamlit run voiceprint_system/app.py
```
* **功能板块 A：任务启动与跟踪**：支持拖拽上传音频、配置高级参数并异步启动后台转写（实时跟踪日志），还可在网页内直接查阅历史转写文本与 AI 纪要。
* **功能板块 B：声纹标定中心**：在线播放未知人声、结合发言上下文线索确认标定（自动逆向更新历史文档），同时支持已命名发言人声纹样本的试听、删除与移动（再匹配）。

### 4. 命令行 CLI 运行
若需在终端运行：
```bash
# 默认使用 Large V3 运行转译并使用声纹库进行识别 (非交互式，未知声纹自动放入 _unknown 池)
uv run python voiceprint_system/main.py --audio "your_meeting.mp3"
```
命令行参数：
* `--audio`：输入录音文件路径
* `--engine`：ASR 引擎选择 (`whisperx` / `whispercpp`)
* `--threshold`：声纹余弦相似度匹配阈值 (默认 `0.68`)
* `--num-speakers`：指定发言人人数（可选）
