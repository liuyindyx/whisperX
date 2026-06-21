import os
import json
from .audio import normalize_and_denoise
from .voiceprint import VoiceprintManager
from .adapters import BaseASRAdapter

class MeetingTranscribePipeline:
    def __init__(self, asr_adapter: BaseASRAdapter, voice_manager: VoiceprintManager):
        self.asr_adapter = asr_adapter
        self.voice_manager = voice_manager

    def process_meeting(
        self,
        raw_audio_path: str,
        meeting_output_dir: str,
        num_speakers=None,
        min_speakers=None,
        max_speakers=None,
        hf_token=None,
        diarize_model=None
    ) -> dict:
        """
        会议录音处理主流水线：
        1. 音频预处理（响度归一化 + 降噪带通滤除）
        2. ASR 转写、时间轴对齐、说话人分割（聚类）
        3. 对每个聚类角色，提取合并多段高质量人声特征向量
        4. 声纹库检索与匹配（含全局 _unknown/ 池的跨会议识别）
        5. 生成 meeting_meta.json 记录声纹引用关系
        
        meeting_output_dir: 该场会议的专属输出目录 (e.g., meetings/会议名/)
        """
        os.makedirs(meeting_output_dir, exist_ok=True)
        temp_audio = os.path.join(meeting_output_dir, "temp_normalized_input.wav")
        
        # 1. 音频预处理
        print(f"[Pipeline] 正在对原始音频 {raw_audio_path} 进行降噪与音量归一化预处理...")
        processed_audio = normalize_and_denoise(raw_audio_path, temp_audio)
        
        # 2. 语音识别与角色分割
        print("[Pipeline] 正在调用 ASR 适配器进行语音识别与角色时间切片分割...")
        asr_result = self.asr_adapter.transcribe_and_diarize(
            processed_audio,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            token=hf_token,
            diarize_model=diarize_model
        )
        
        # 3. 收集各个 SPEAKER 的所有音频切片信息，用于拼接提取高鲁棒性声纹
        # 结构： { 'SPEAKER_00': [ (start, end), (start, end), ... ], ... }
        speaker_segments = {}
        for seg in asr_result.get("segments", []):
            spk = seg.get("speaker")
            if not spk:
                continue
            
            start = seg.get("start", 0.0)
            end = seg.get("end", 0.0)
            duration = end - start
            
            # 过滤掉短于 1.0s 的无意义拟声词（如"对"、"嗯"），选取更有特征的连续话语
            if duration >= 1.0:
                if spk not in speaker_segments:
                    speaker_segments[spk] = []
                speaker_segments[spk].append((start, end, duration))

        # 4. 提取声纹 Embedding 并检索声纹数据库（含 _unknown/ 跨会议匹配）
        print("[Pipeline] 正在为识别出的发言人进行声纹特征提取与匹配...")
        speaker_mapping = {}          # 原始标签 -> 显示名称
        speaker_references = {}       # 用于生成 meeting_meta.json 的引用记录
        detected_speakers_info = {}   # 用于保存临时 Embedding，方便后续标注反馈

        for spk, clips_info in speaker_segments.items():
            # 按照时长降序排序，取前 5 段最长的高质量话语进行拼接，既能均摊噪声，又不会占用过大内存
            clips_info_sorted = sorted(clips_info, key=lambda x: x[2], reverse=True)
            top_clips = [(start, end) for start, end, _ in clips_info_sorted[:5]]
            
            print(f"[Pipeline] SPEAKER 【{spk}】：提取并拼合了 {len(top_clips)} 段高质量音频做声纹校验...")
            
            # 提取联合声纹向量与配套拼接音频样本
            emb, waveform_np = self.voice_manager.extract_combined_embedding(processed_audio, top_clips)
            
            # 升级版声纹检索：同时搜索已命名库和全局 _unknown/ 池
            match_result = self.voice_manager.identify_speaker(emb)
            
            if match_result["type"] == "labeled":
                # 已命名人员匹配成功
                display_name = match_result["name"]
                speaker_references[spk] = {
                    "voiceprint_type": "labeled",
                    "voiceprint_id": match_result["name"]
                }
            elif match_result["type"] == "unlabeled":
                # 匹配到了全局 _unknown/ 池中的某个已存在的未知声纹
                display_name = "未知发言人"
                speaker_references[spk] = {
                    "voiceprint_type": "unlabeled",
                    "voiceprint_id": match_result["unknown_id"]
                }
            else:
                # 全新未知声纹：注册到全局 _unknown/ 池
                unknown_id = self.voice_manager.register_unknown_voiceprint(emb, waveform_np)
                display_name = "未知发言人"
                speaker_references[spk] = {
                    "voiceprint_type": "unlabeled",
                    "voiceprint_id": unknown_id if unknown_id else "unknown_error"
                }

            speaker_mapping[spk] = display_name
            detected_speakers_info[spk] = {
                "real_name": display_name,
                "embedding": emb,
                "waveform": waveform_np,
                "clips": top_clips,
                "match_result": match_result
            }
            
            print(f"[Pipeline] SPEAKER 【{spk}】 匹配结果 ===> 【{display_name}】 (type={match_result['type']}, sim={match_result['similarity']:.4f})")

        # 5. 更新转写段落与字词中的说话人标签为真实姓名
        print("[Pipeline] 正在替换段落时间轴上的角色人名标签...")
        for seg in asr_result.get("segments", []):
            spk = seg.get("speaker")
            if spk in speaker_mapping:
                seg["speaker_name"] = speaker_mapping[spk]
            else:
                seg["speaker_name"] = "未知发言人"
                
            # 同时也为字级时间轴标注人名
            if "words" in seg:
                for w in seg["words"]:
                    w_spk = w.get("speaker")
                    if w_spk in speaker_mapping:
                        w["speaker_name"] = speaker_mapping[w_spk]
                    else:
                        w["speaker_name"] = "未知发言人"

        # 6. 生成 meeting_meta.json
        meta = {
            "meeting_name": os.path.basename(meeting_output_dir),
            "status": "processing_completed",
            "engine_used": type(self.asr_adapter).__name__,
            "creation_time": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "audio_path": os.path.abspath(raw_audio_path),
            "speaker_references": speaker_references
        }
        meta_path = os.path.join(meeting_output_dir, "meeting_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"[Pipeline] 已生成会议元数据: {meta_path}")

        # 7. 清理临时生成的预处理 WAV
        if os.path.exists(temp_audio):
            try:
                os.remove(temp_audio)
            except Exception as e_clean:
                print(f"[Warning] 清除临时音频文件失败: {e_clean}")
                
        print("[Pipeline] 本次录音转译与匹配任务执行完毕。")
        return {
            "transcription": asr_result,
            "detected_speakers": detected_speakers_info,
            "speaker_references": speaker_references
        }
