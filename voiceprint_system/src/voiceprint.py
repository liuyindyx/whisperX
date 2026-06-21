import os
import json
import time
import shutil
import numpy as np
import torch
import torchaudio
import warnings

# 忽略 SpeechBrain / Hugging Face 的某些冗长警告
warnings.filterwarnings("ignore", category=UserWarning)

class VoiceprintManager:
    def __init__(self, db_dir="database", threshold=0.68, device="mps"):
        """
        初始化声纹管理器
        db_dir: 声纹数据库根目录
        threshold: 余弦相似度判别阈值 (0.0 到 1.0)
        device: 硬件加速设备，如 'mps', 'cuda', 或 'cpu'
        """
        self.db_dir = os.path.abspath(db_dir)
        self.threshold = threshold
        self._unknown_dir = os.path.join(self.db_dir, "_unknown")
        
        # 【重要修复】强制声纹提取器运行在 CPU 上
        # 原因：SpeechBrain (v1.1.0) 库中的 interfaces.py 不兼容 Mac 独有的 "mps" 设备名，
        # 会因未定义 device_type 而触发 AttributeError 崩溃。
        # 鉴于 ECAPA-TDNN 声纹提取模型非常轻量（处理几秒的切片仅需数十毫秒），
        # 在 Apple Silicon CPU (含 NEON/Accelerate 优化) 上运行速度极快，
        # 因此强制使用 "cpu" 运行以保证高稳定性与零性能损失。
        # 主流水线（ASR、对齐与聚类）仍会正常使用 Mac GPU (MPS) 加速。
        self.device = "cpu"

        os.makedirs(self.db_dir, exist_ok=True)
        os.makedirs(self._unknown_dir, exist_ok=True)
        
        print(f"[Info] 正在加载 SpeechBrain 声纹模型 (ECAPA-TDNN)...")
        # 懒加载以提高初次启动速度
        from speechbrain.pretrained import EncoderClassifier
        self.classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            run_opts={"device": self.device}
        )
        print(f"[Info] 声纹模型加载完成，运行设备: {self.device}")

    def extract_embedding(self, wav_path: str, start_s: float, end_s: float) -> np.ndarray:
        """
        根据开始/结束时间（秒），截取音频并提取其 192 维的声纹 Embedding 向量
        """
        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"找不到音频文件: {wav_path}")
            
        duration = end_s - start_s
        if duration < 0.3:
            # 音频切片过短无法有效提取声纹，返回全零向量或警告
            return np.zeros(192, dtype=np.float32)

        try:
            info = torchaudio.info(wav_path)
            sr = info.sample_rate
            frame_offset = int(start_s * sr)
            num_frames = int(duration * sr)
            
            # 安全检查防止越界
            if frame_offset >= info.num_frames:
                return np.zeros(192, dtype=np.float32)
            if frame_offset + num_frames > info.num_frames:
                num_frames = info.num_frames - frame_offset

            waveform, sample_rate = torchaudio.load(wav_path, frame_offset=frame_offset, num_frames=num_frames)
            
            # SpeechBrain 模型要求输入必须是 16kHz
            if sample_rate != 16000:
                resampler = torchaudio.transforms.Resample(orig_freq=sample_rate, new_freq=16000)
                waveform = resampler(waveform)
            
            # 转换为单声道
            if waveform.shape[0] > 1:
                waveform = torch.mean(waveform, dim=0, keepdim=True)
                
            waveform = waveform.to(self.device)
            
            with torch.no_grad():
                # embedding Shape: [batch, time, features] -> [1, 1, 192]
                embedding = self.classifier.encode_batch(waveform)
                emb_np = embedding.squeeze().cpu().numpy()
                
            # 归一化向量
            norm = np.linalg.norm(emb_np)
            if norm > 0:
                emb_np = emb_np / norm
                
            return emb_np
            
        except Exception as e:
            print(f"[Warning] 提取声纹特征失败 ({start_s}s - {end_s}s): {e}")
            return np.zeros(192, dtype=np.float32)

    def extract_combined_embedding(self, wav_path: str, clips: list) -> np.ndarray:
        """
        根据多个时间段的音频切片，拼接后提取一个统一的、高鲁棒性的声纹 Embedding 向量。
        clips: 包含 (start_s, end_s) 元组的列表
        """
        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"找不到音频文件: {wav_path}")
            
        if not clips:
            return np.zeros(192, dtype=np.float32)

        waveforms = []
        try:
            info = torchaudio.info(wav_path)
            sr = info.sample_rate
            
            for start_s, end_s in clips:
                duration = end_s - start_s
                if duration < 0.3:
                    continue
                    
                frame_offset = int(start_s * sr)
                num_frames = int(duration * sr)
                
                if frame_offset >= info.num_frames:
                    continue
                if frame_offset + num_frames > info.num_frames:
                    num_frames = info.num_frames - frame_offset

                waveform, sample_rate = torchaudio.load(wav_path, frame_offset=frame_offset, num_frames=num_frames)
                
                # 重采样
                if sample_rate != 16000:
                    resampler = torchaudio.transforms.Resample(orig_freq=sample_rate, new_freq=16000)
                    waveform = resampler(waveform)
                
                # 单声道
                if waveform.shape[0] > 1:
                    waveform = torch.mean(waveform, dim=0, keepdim=True)
                    
                waveforms.append(waveform)
                
            if not waveforms:
                return np.zeros(192, dtype=np.float32)
                
            # 在时间维度 (dim=1) 上拼接所有音频切片
            combined_waveform = torch.cat(waveforms, dim=1)
            combined_waveform = combined_waveform.to(self.device)
            
            with torch.no_grad():
                embedding = self.classifier.encode_batch(combined_waveform)
                emb_np = embedding.squeeze().cpu().numpy()
                
            norm = np.linalg.norm(emb_np)
            if norm > 0:
                emb_np = emb_np / norm
                
            return emb_np, combined_waveform.cpu().numpy()
            
        except Exception as e:
            print(f"[Warning] 提取联合声纹特征失败: {e}")
            return np.zeros(192, dtype=np.float32), None

    def _cosine_similarity(self, emb_a: np.ndarray, emb_b: np.ndarray) -> float:
        """计算两个向量之间的余弦相似度"""
        dot_prod = np.dot(emb_a, emb_b)
        norm_a = np.linalg.norm(emb_a)
        norm_b = np.linalg.norm(emb_b)
        if norm_a > 0 and norm_b > 0:
            return dot_prod / (norm_a * norm_b)
        return 0.0

    def identify_speaker(self, query_emb: np.ndarray) -> dict:
        """
        升级版声纹识别：同时搜索已命名库和全局 _unknown/ 池。
        
        返回值 (dict):
          - type: 'labeled' | 'unlabeled' | 'new_unknown'
          - name: 已命名人员姓名（仅 type='labeled' 时有效）
          - unknown_id: 全局未知声纹 ID（仅 type='unlabeled' 时有效）
          - display_name: 用于文档中的显示名称
          - similarity: 最佳匹配的相似度
        """
        if np.all(query_emb == 0):
            return {"type": "new_unknown", "name": None, "unknown_id": None,
                    "display_name": "未知发言人", "similarity": 0.0}

        best_match = {"type": "new_unknown", "name": None, "unknown_id": None,
                      "display_name": "未知发言人", "similarity": -1.0}

        if not os.path.exists(self.db_dir):
            return best_match

        # 1. 搜索已命名声纹库
        for name in os.listdir(self.db_dir):
            person_dir = os.path.join(self.db_dir, name)
            if not os.path.isdir(person_dir) or name.startswith('.') or name == "_unknown":
                continue
            
            for file_name in os.listdir(person_dir):
                if file_name.endswith('.npy'):
                    try:
                        stored_emb = np.load(os.path.join(person_dir, file_name))
                        sim = self._cosine_similarity(query_emb, stored_emb)
                        if sim > best_match["similarity"]:
                            best_match["similarity"] = sim
                            best_match["type"] = "labeled"
                            best_match["name"] = name
                            best_match["unknown_id"] = None
                            best_match["display_name"] = name
                    except Exception as e:
                        print(f"[Warning] 读取声纹数据 {file_name} 失败: {e}")

        # 2. 搜索全局 _unknown/ 池
        if os.path.exists(self._unknown_dir):
            for uid in os.listdir(self._unknown_dir):
                uid_dir = os.path.join(self._unknown_dir, uid)
                if not os.path.isdir(uid_dir) or uid.startswith('.'):
                    continue
                npy_path = os.path.join(uid_dir, "feature.npy")
                if os.path.exists(npy_path):
                    try:
                        stored_emb = np.load(npy_path)
                        sim = self._cosine_similarity(query_emb, stored_emb)
                        if sim > best_match["similarity"]:
                            best_match["similarity"] = sim
                            best_match["type"] = "unlabeled"
                            best_match["name"] = None
                            best_match["unknown_id"] = uid
                            best_match["display_name"] = "未知发言人"
                    except Exception as e:
                        print(f"[Warning] 读取未知声纹数据 {uid} 失败: {e}")

        # 3. 判断阈值
        if best_match["similarity"] >= self.threshold:
            return best_match
        else:
            return {"type": "new_unknown", "name": None, "unknown_id": None,
                    "display_name": "未知发言人", "similarity": best_match["similarity"]}

    def register_unknown_voiceprint(self, emb: np.ndarray, waveform: np.ndarray = None) -> str:
        """
        将未识别的声纹特征注册到全局 _unknown/ 池中，返回生成的全局唯一 ID。
        """
        if np.all(emb == 0):
            print(f"[Warning] 尝试注册全零声纹向量到 _unknown 池，已拦截。")
            return None

        timestamp = int(time.time() * 1000)
        unknown_id = f"unknown_{timestamp}"
        uid_dir = os.path.join(self._unknown_dir, unknown_id)
        os.makedirs(uid_dir, exist_ok=True)

        # 归一化后再保存
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm

        npy_path = os.path.join(uid_dir, "feature.npy")
        np.save(npy_path, emb)
        print(f"[Database] 已将未知声纹保存至全局暂存池: {unknown_id}")

        # 保存配套的 WAV 音频样本供用户后续监听
        if waveform is not None:
            try:
                wav_path = os.path.join(uid_dir, "feature.wav")
                waveform_tensor = torch.from_numpy(waveform)
                torchaudio.save(wav_path, waveform_tensor, 16000)
                print(f"[Database] 已保存配套人声样本音频: {unknown_id}/feature.wav")
            except Exception as e_wav:
                print(f"[Warning] 保存未知声纹配套音频失败: {e_wav}")

        return unknown_id

    def register_voiceprint(self, name: str, emb: np.ndarray, tag="manual", waveform: np.ndarray = None):
        """
        将发言人的特征向量注册/追加到本地数据库中，如果提供 waveform 也会保存其人声录音样本。
        """
        if np.all(emb == 0):
            print(f"[Warning] 尝试注册全零声纹向量（发言人：{name}），已拦截。")
            return
            
        person_dir = os.path.join(self.db_dir, name)
        os.makedirs(person_dir, exist_ok=True)
        
        # 使用随机数或计数器生成不重复的文件名
        timestamp = int(time.time() * 1000)
        file_name = f"{tag}_{timestamp}.npy"
        file_path = os.path.join(person_dir, file_name)
        
        # 归一化后再保存
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm
            
        np.save(file_path, emb)
        print(f"[Database] 已为【{name}】追加声纹模板: {file_name}")
        
        # 保存配套的 WAV 音频样本供用户监听校验
        if waveform is not None:
            try:
                wav_path = os.path.splitext(file_path)[0] + ".wav"
                waveform_tensor = torch.from_numpy(waveform)
                torchaudio.save(wav_path, waveform_tensor, 16000)
                print(f"[Database] 已为【{name}】保存配套人声样本音频: {os.path.basename(wav_path)}")
            except Exception as e_wav:
                print(f"[Warning] 保存声纹配套音频失败: {e_wav}")

    def promote_unknown(self, unknown_id: str, real_name: str, meetings_dir: str = None):
        """
        将 _unknown/ 池中的声纹"晋升"为已命名人员：
        1. 将声纹特征从 _unknown/unknown_id/ 移动到 database/real_name/ 下
        2. 如果提供了 meetings_dir，扫描所有会议的 meeting_meta.json 并反向更新引用了该 unknown_id 的文档
        """
        uid_dir = os.path.join(self._unknown_dir, unknown_id)
        if not os.path.exists(uid_dir):
            print(f"[Error] 未找到全局未知声纹 ID: {unknown_id}")
            return False

        person_dir = os.path.join(self.db_dir, real_name)
        os.makedirs(person_dir, exist_ok=True)

        # 移动声纹特征
        timestamp = int(time.time() * 1000)
        src_npy = os.path.join(uid_dir, "feature.npy")
        if os.path.exists(src_npy):
            dst_npy = os.path.join(person_dir, f"promoted_{unknown_id}_{timestamp}.npy")
            shutil.copy2(src_npy, dst_npy)

        src_wav = os.path.join(uid_dir, "feature.wav")
        if os.path.exists(src_wav):
            dst_wav = os.path.join(person_dir, f"promoted_{unknown_id}_{timestamp}.wav")
            shutil.copy2(src_wav, dst_wav)

        # 删除 _unknown 中的原始文件夹
        shutil.rmtree(uid_dir, ignore_errors=True)
        print(f"[Database] 已将 [{unknown_id}] 晋升为已命名人员【{real_name}】")

        # 反向更新引用了该 unknown_id 的所有历史会议文档
        if meetings_dir and os.path.exists(meetings_dir):
            self._retroactive_update(unknown_id, real_name, meetings_dir)

        return True

    def _retroactive_update(self, unknown_id: str, real_name: str, meetings_dir: str):
        """
        扫描 meetings/ 下所有会议的 meeting_meta.json，
        找到引用了 unknown_id 的会议，并反向替换其 transcript/summary 文件中的人名。
        """
        updated_count = 0
        for meeting_name in os.listdir(meetings_dir):
            meeting_dir = os.path.join(meetings_dir, meeting_name)
            if not os.path.isdir(meeting_dir) or meeting_name.startswith('.'):
                continue

            meta_path = os.path.join(meeting_dir, "meeting_meta.json")
            if not os.path.exists(meta_path):
                continue

            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                continue

            speaker_refs = meta.get("speaker_references", {})
            need_update = False

            # 找到引用了该 unknown_id 的 SPEAKER
            target_speakers = []
            for spk_label, ref_info in speaker_refs.items():
                if ref_info.get("voiceprint_id") == unknown_id:
                    target_speakers.append(spk_label)
                    ref_info["voiceprint_type"] = "labeled"
                    ref_info["voiceprint_id"] = real_name
                    need_update = True

            if not need_update:
                continue

            # 更新 meeting_meta.json
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            # 更新 transcript.json
            json_path = os.path.join(meeting_dir, "transcript.json")
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        transcript_data = json.load(f)
                    
                    for seg in transcript_data.get("segments", []):
                        if seg.get("speaker") in target_speakers and seg.get("speaker_name") == "未知发言人":
                            seg["speaker_name"] = real_name
                        if "words" in seg:
                            for w in seg["words"]:
                                if w.get("speaker") in target_speakers and w.get("speaker_name") == "未知发言人":
                                    w["speaker_name"] = real_name

                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(transcript_data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"[Warning] 反向更新 transcript.json 失败 ({meeting_name}): {e}")

            # 更新 transcript.md
            md_path = os.path.join(meeting_dir, "transcript.md")
            if os.path.exists(md_path):
                try:
                    with open(md_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    content = content.replace("**未知发言人**", f"**{real_name}**")
                    with open(md_path, "w", encoding="utf-8") as f:
                        f.write(content)
                except Exception as e:
                    print(f"[Warning] 反向更新 transcript.md 失败 ({meeting_name}): {e}")

            # 更新 summary.md
            summary_path = os.path.join(meeting_dir, "summary.md")
            if os.path.exists(summary_path):
                try:
                    with open(summary_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    content = content.replace("未知发言人", real_name)
                    with open(summary_path, "w", encoding="utf-8") as f:
                        f.write(content)
                except Exception as e:
                    print(f"[Warning] 反向更新 summary.md 失败 ({meeting_name}): {e}")

            updated_count += 1
            print(f"[Retroactive] 已反向更新会议【{meeting_name}】中的发言人名称")

        print(f"[Retroactive] 反向更新完成，共更新 {updated_count} 场会议的文档")
