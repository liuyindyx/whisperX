import os
import argparse
import json
from src.voiceprint import VoiceprintManager
from src.adapters import WhisperXAdapter, WhisperCppAdapter
from src.pipeline import MeetingTranscribePipeline

# 会议输出根目录（与 WhisperX 项目根目录同级的 meetings/ 文件夹）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEETINGS_DIR = os.path.join(PROJECT_ROOT, "meetings")

def format_timestamp(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    else:
        return f"{m:02d}:{s:02d}.{ms:03d}"

def save_output_files(result_dict: dict, meeting_output_dir: str, meeting_name: str):
    """
    将转译结果保存为 Markdown 和 JSON 文件到会议专属目录
    """
    os.makedirs(meeting_output_dir, exist_ok=True)
    
    # 1. 保存为 JSON
    json_path = os.path.join(meeting_output_dir, "transcript.json")
    # 自定义序列化，处理 numpy array (如果有)
    def default_serializer(obj):
        if hasattr(obj, "tolist"):
            return obj.tolist()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result_dict["transcription"], f, ensure_ascii=False, indent=2, default=default_serializer)
    
    # 2. 保存为 Markdown
    md_path = os.path.join(meeting_output_dir, "transcript.md")
    from datetime import datetime
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 会议语音转译文档\n\n")
        f.write(f"* **会议名称**: {meeting_name}\n")
        f.write(f"* **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"---\n\n")
        
        for seg in result_dict["transcription"].get("segments", []):
            time_str = f"[{format_timestamp(seg.get('start', 0.0))} - {format_timestamp(seg.get('end', 0.0))}]"
            speaker = seg.get("speaker_name", "未知发言人")
            text = seg.get("text", "").strip()
            f.write(f"**{speaker}** {time_str}  \n{text}\n\n")
            
    print(f"[Export] 成功导出转写文本至 Markdown: {md_path}")
    print(f"[Export] 成功导出原始结构至 JSON: {json_path}")
    return md_path, json_path

def main():
    parser = argparse.ArgumentParser(description="Mac mini 本地会议转写与声纹数据库检索程序")
    parser.add_argument("--audio", type=str, required=True, help="输入的会议录音文件 (.mp3, .wav 等)")
    parser.add_argument("--engine", type=str, default="whisperx", choices=["whisperx", "whispercpp"], help="ASR 转写引擎 (whisperx 或 whispercpp)")
    parser.add_argument("--num-speakers", type=int, default=None, help="发言人总数（若已知，可提供以提升准确性）")
    parser.add_argument("--min-speakers", type=int, default=None, help="最少发言人数")
    parser.add_argument("--max-speakers", type=int, default=None, help="最多发言人数")
    parser.add_argument("--threshold", type=float, default=0.68, help="声纹余弦相似度匹配阈值")
    parser.add_argument("--device", type=str, default="mps", help="PyTorch 运行设备 (mps, cpu)")
    parser.add_argument("--hf-token", type=str, default=None, help="Hugging Face Access Token (用于下载受限的 PyAnnote 说话人分割模型)")
    parser.add_argument("--mirror", action="store_true", help="是否使用 hf-mirror.com 镜像站加速 Hugging Face 模型的下载")
    parser.add_argument("--diarize-model", type=str, default="pyannote/speaker-diarization-3.1", help="PyAnnote 说话人分割模型的 Hugging Face ID (例如 pyannote/speaker-diarization-3.1 或 pyannote/speaker-diarization-community-1)")
    parser.add_argument("--meeting-name", type=str, default=None, help="会议名称（若不提供，自动使用音频文件名）")
    
    args = parser.parse_args()
    
    if args.mirror:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        print("[Info] 已启用 Hugging Face 国内镜像站 (https://hf-mirror.com) 进行模型下载加速。")
    
    if not os.path.exists(args.audio):
        print(f"[Error] 找不到音频文件: {args.audio}")
        return

    # 确定会议名称和输出目录
    meeting_name = args.meeting_name or os.path.splitext(os.path.basename(args.audio))[0]
    meeting_output_dir = os.path.join(MEETINGS_DIR, meeting_name)
    os.makedirs(meeting_output_dir, exist_ok=True)
    
    # 1. 初始化声纹库管理器（定位在 main.py 同级目录下的 database）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_dir = os.path.join(script_dir, "database")
    voice_manager = VoiceprintManager(db_dir=db_dir, threshold=args.threshold, device=args.device)
    
    # 2. 选择 ASR 适配器 (完全解耦设计)
    if args.engine == "whisperx":
        # 默认在 Mac CPU 上运行 faster-whisper 量化版以降低系统负载，在 MPS 上跑对齐和角色分割
        asr_adapter = WhisperXAdapter(device=args.device, whisper_model="large-v3", compute_type="int8")
    else:
        # 方案 B，调用本地已经配置好的 whisper-cli 命令行做 ASR
        asr_adapter = WhisperCppAdapter(
            device=args.device,
            whisper_cli_path="/opt/homebrew/bin/whisper-cli",
            model_path="/Users/liuyindyx/Models/whisper/ggml-large-v3-turbo.bin"
        )
        
    # 3. 构造流水线并运行
    pipeline = MeetingTranscribePipeline(asr_adapter=asr_adapter, voice_manager=voice_manager)
    
    print("\n" + "="*50)
    print(f" 开始处理会议: {args.audio}")
    print(f" 输出目录: {meeting_output_dir}")
    print("="*50 + "\n")
    
    result = pipeline.process_meeting(
        args.audio,
        meeting_output_dir=meeting_output_dir,
        num_speakers=args.num_speakers,
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
        hf_token=args.hf_token,
        diarize_model=args.diarize_model
    )
    
    # 4. 打印初步识别转写结果
    print("\n" + "="*20 + " 转写与识别结果 " + "="*20)
    for seg in result["transcription"].get("segments", []):
        time_str = f"[{format_timestamp(seg.get('start', 0.0))} - {format_timestamp(seg.get('end', 0.0))}]"
        speaker = seg.get("speaker_name", "未知发言人")
        text = seg.get("text", "").strip()
        print(f"{time_str} 【{speaker}】: {text}")
    print("="*56 + "\n")
    
    # 保存初始结果到会议专属目录
    md_path, json_path = save_output_files(result, meeting_output_dir, meeting_name)
    
    # 5. 交互式持续标注 (Continuous Labeling) 反馈流程
    # 检查是否有未识别出的 SPEAKER (未知发言人)
    unknown_speakers = []
    for spk, info in result["detected_speakers"].items():
        if info["real_name"] == "未知发言人":
            unknown_speakers.append(spk)
            
    if unknown_speakers:
        print("\n" + "*"*15 + " 发现未知发言人，启动声纹标注程序 " + "*"*15)
        print("未知发言人的声纹已自动保存至全局暂存池 (database/_unknown/)。")
        print("您可以：")
        print("  1. 在此处直接输入姓名进行即时标注")
        print("  2. 按回车跳过，后续通过 Web UI (streamlit run voiceprint_system/app.py) 进行异步标定\n")
        
        has_new_register = False
        
        for spk in unknown_speakers:
            info = result["detected_speakers"][spk]
            # 找到该 speaker 的几句样例文本供用户参考识别
            samples = []
            for seg in result["transcription"].get("segments", []):
                if seg.get("speaker") == spk:
                    samples.append(seg.get("text", "").strip())
                    if len(samples) >= 3:
                        break
            
            print(f"===> 未知发言人标签: 【{spk}】")
            print("该发言人在会议中说了以下内容:")
            for s in samples:
                print(f"   - \"{s}\"")
                
            try:
                name = input(f"请输入该发言人 【{spk}】 的真实姓名 (若不标注直接按回车跳过): ").strip()
            except EOFError:
                print("\n[Info] 检测到非交互式环境或输入结束，跳过此轮手动标注。")
                name = ""
            
            if name:
                # 获取该 speaker 在 meeting_meta.json 中对应的 unknown_id
                ref_info = result.get("speaker_references", {}).get(spk, {})
                unknown_id = ref_info.get("voiceprint_id")
                
                if unknown_id and unknown_id.startswith("unknown_"):
                    # 将 _unknown/ 中的声纹晋升为已命名人员，并反向更新历史会议
                    voice_manager.promote_unknown(unknown_id, name, MEETINGS_DIR)
                else:
                    # 直接注册（fallback 逻辑）
                    voice_manager.register_voiceprint(
                        name=name,
                        emb=info["embedding"],
                        tag=f"feedback_{meeting_name}",
                        waveform=info.get("waveform")
                    )
                
                has_new_register = True
                
                # 实时更新本次内存中的人名
                for seg in result["transcription"].get("segments", []):
                    if seg.get("speaker") == spk:
                        seg["speaker_name"] = name
                    if "words" in seg:
                        for w in seg["words"]:
                            if w.get("speaker") == spk:
                                w["speaker_name"] = name
                                
                print(f"已将 【{spk}】 标记为 【{name}】\n")
            else:
                print(f"已跳过对 【{spk}】 的标注（声纹已保存至暂存池，可后续通过 Web UI 标定）\n")
                
        if has_new_register:
            # 重新保存更新人名后的文件
            print("[Update] 正在重新生成人名更新后的转译文件...")
            save_output_files(result, meeting_output_dir, meeting_name)
            
    print("\n" + "="*50)
    print(" 任务全部完成！")
    print(f" 输出目录: {meeting_output_dir}")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()
