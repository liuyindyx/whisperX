import os
import json
import subprocess
import torch
import numpy as np

class BaseASRAdapter:
    def transcribe_and_diarize(
        self,
        audio_path: str,
        num_speakers=None,
        min_speakers=None,
        max_speakers=None,
        token=None,
        diarize_model=None
    ) -> dict:
        """
        统一接口：输入音频路径，返回带说话人标签与时间戳的转写字典。
        """
        raise NotImplementedError

class WhisperXAdapter(BaseASRAdapter):
    """
    方案 A：原生 Python WhisperX 适配器 (CTranslate2 CPU ASR + PyTorch MPS 辅助)
    """
    def __init__(self, device="mps", whisper_model="large-v3", compute_type="int8"):
        """
        device: 'mps' 用于 PyTorch 对齐和角色分割模型在 Mac GPU 加速
        whisper_model: whisper 模型名称
        compute_type: CTranslate2 运算精度，如 'int8', 'float32', 'float16'
        """
        self.device = device
        # CTranslate2 不支持 MPS，因此 ASR 强制运行在 CPU 上 (NEON/Accelerate 优化速度极快)
        self.asr_device = "cpu" if device == "mps" else device
        self.whisper_model = whisper_model
        self.compute_type = compute_type
        
        # 延迟导入以防止启动时加载缓慢
        import whisperx
        self.whisperx = whisperx

    def transcribe_and_diarize(
        self,
        audio_path: str,
        num_speakers=None,
        min_speakers=None,
        max_speakers=None,
        token=None,
        diarize_model=None
    ) -> dict:
        token = token or os.environ.get("HF_TOKEN")
        print(f"[ASR] 正在加载 Whisper 模型 {self.whisper_model} (精度: {self.compute_type})...")
        model = self.whisperx.load_model(
            self.whisper_model, 
            device=self.asr_device, 
            compute_type=self.compute_type
        )
        
        print("[ASR] 正在载入音频...")
        audio = self.whisperx.load_audio(audio_path)
        
        print("[ASR] 正在执行语音转写...")
        # batch_size=4 适合 Mac mini 内存
        raw_result = model.transcribe(audio, batch_size=4)
        
        # 检查是否成功识别语言，若无默认使用中文
        language = raw_result.get("language", "zh")
        print(f"[ASR] 识别语言: {language}")
        
        # 释放 ASR 模型内存以防 GPU/CPU 内存不足
        del model
        import gc
        gc.collect()
        if self.device == "mps":
            torch.mps.empty_cache()

        print(f"[Align] 正在加载对齐模型 (语言: {language}, 设备: {self.device})...")
        model_a, metadata = self.whisperx.load_align_model(
            language_code=language, 
            device=self.device
        )
        
        print("[Align] 正在执行字词时间轴对齐...")
        aligned_result = self.whisperx.align(
            raw_result["segments"], 
            model_a, 
            metadata, 
            audio, 
            self.device, 
            return_char_alignments=False
        )
        
        # 释放对齐模型内存
        del model_a
        gc.collect()
        if self.device == "mps":
            torch.mps.empty_cache()

        print(f"[Diarize] 正在加载说话人分割与聚类模型 PyAnnote (设备: {self.device})...")
        # 不需要 hugging face token，从 whisperx.diarize 导入 DiarizationPipeline
        from whisperx.diarize import DiarizationPipeline
        diarize_model_inst = DiarizationPipeline(
            model_name=diarize_model,
            token=token, 
            device=self.device
        )
        
        print("[Diarize] 正在执行说话人切片聚类...")
        diarize_segments = diarize_model_inst(
            audio,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers
        )
        
        print("[Pipeline] 正在将说话人与对齐后的文本字词融合...")
        final_result = self.whisperx.assign_word_speakers(diarize_segments, aligned_result)
        
        return final_result

class WhisperCppAdapter(BaseASRAdapter):
    """
    方案 B：外部 whisper.cpp (Metal 加速) + Python PyTorch MPS 对齐与分割
    """
    def __init__(
        self,
        device="mps",
        whisper_cli_path="/opt/homebrew/bin/whisper-cli",
        model_path="/Users/liuyindyx/Models/whisper/ggml-large-v3-turbo.bin"
    ):
        self.device = device
        self.cli_path = whisper_cli_path
        self.model_path = model_path
        
        import whisperx
        self.whisperx = whisperx

    def transcribe_and_diarize(
        self,
        audio_path: str,
        num_speakers=None,
        min_speakers=None,
        max_speakers=None,
        token=None,
        diarize_model=None
    ) -> dict:
        token = token or os.environ.get("HF_TOKEN")
        if not os.path.exists(self.cli_path):
            raise FileNotFoundError(f"找不到 whisper-cli 可执行文件: {self.cli_path}")
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"找不到 GGML 模型文件: {self.model_path}")
            
        print(f"[ASR cpp] 正在使用 whisper.cpp 命令行转译 (Metal 加速)...")
        output_prefix = audio_path + "_cpp_temp"
        
        # 组合命令行参数，输出为 JSON (-oj)
        cmd = [
            self.cli_path,
            "-m", self.model_path,
            "-f", audio_path,
            "-oj",             # 输出 JSON 格式时间戳
            "-of", output_prefix, # 输出文件前缀
            "-l", "zh",        # 设定中文
            "-fa",             # 防幻听复读
            "--max-context", "32"
        ]
        
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"whisper.cpp 运行失败: {e}")
            
        json_path = f"{output_prefix}.json"
        if not os.path.exists(json_path):
            raise FileNotFoundError(f"未找到 whisper.cpp 生成的 JSON 转译结果: {json_path}")
            
        with open(json_path, "r", encoding="utf-8") as f:
            cpp_output = json.load(f)
            
        # 清除临时生成的 JSON 文件
        if os.path.exists(json_path):
            os.remove(json_path)

        # 解析 whisper.cpp json 转换成 WhisperX 兼容格式
        # whisper.cpp json 格式示例:
        # { "transcription": [ { "offsets": { "from": 100, "to": 1200 }, "text": "xxx" } ] }
        # 时间单位是毫秒 (ms)，需要转换为秒 (s)
        raw_segments = []
        for segment in cpp_output.get("transcription", []):
            start = segment["offsets"]["from"] / 1000.0
            end = segment["offsets"]["to"] / 1000.0
            text = segment["text"].strip()
            raw_segments.append({
                "start": start,
                "end": end,
                "text": text
            })
            
        print("[ASR cpp] 成功解析 whisper.cpp 的段落文本。")
        
        # 载入音频准备对齐与分割
        audio = self.whisperx.load_audio(audio_path)
        
        # 强制对齐 (MPS 硬件加速)
        print(f"[Align] 正在加载对齐模型 (语言: zh, 设备: {self.device})...")
        model_a, metadata = self.whisperx.load_align_model(language_code="zh", device=self.device)
        print("[Align] 正在执行字词时间轴对齐...")
        aligned_result = self.whisperx.align(
            raw_segments, 
            model_a, 
            metadata, 
            audio, 
            self.device, 
            return_char_alignments=False
        )
        
        del model_a
        import gc; gc.collect()
        if self.device == "mps":
            torch.mps.empty_cache()

        # 说话人分割 (MPS 硬件加速)
        print(f"[Diarize] 正在加载说话人分割与聚类模型 PyAnnote (设备: {self.device})...")
        from whisperx.diarize import DiarizationPipeline
        diarize_model_inst = DiarizationPipeline(model_name=diarize_model, token=token, device=self.device)
        print("[Diarize] 正在执行说话人切片聚类...")
        diarize_segments = diarize_model_inst(
            audio,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers
        )
        
        print("[Pipeline] 正在将说话人与对齐后的文本字词融合...")
        final_result = self.whisperx.assign_word_speakers(diarize_segments, aligned_result)
        
        return final_result
