import os
import sys
import time
import json
import shutil
import glob
import subprocess
import numpy as np
import streamlit as st

# ==============================================================================
# 路径与配置初始化
# ==============================================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

MEETINGS_DIR = os.path.join(PROJECT_ROOT, "meetings")
DB_DIR = os.path.join(PROJECT_ROOT, "voiceprint_system", "database")
UNKNOWN_DIR = os.path.join(DB_DIR, "_unknown")

os.makedirs(MEETINGS_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(UNKNOWN_DIR, exist_ok=True)

# 设置 Streamlit 页面属性
st.set_page_config(
    page_title="🎙️ 声纹会议转译管理系统",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==============================================================================
# CSS 注入 - 打造精致暗黑科技风 UI
# ==============================================================================
st.markdown("""
<style>
    /* 全局暗黑背景与主体色 */
    .stApp {
        background-color: #0e1117;
        color: #e6edf3;
    }
    
    /* 侧边栏样式定制 */
    section[data-testid="stSidebar"] {
        background-color: #161b22 !important;
        border-right: 1px solid #30363d;
    }
    
    /* 标题与副标题定制 */
    h1, h2, h3 {
        color: #00d4aa !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    
    /* 卡片与容器阴影圆角效果 */
    .css-1r6g72d, .stCard, div[data-testid="stExpander"] {
        background-color: #1f242c !important;
        border: 1px solid #30363d !important;
        border-radius: 8px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15) !important;
        padding: 15px !important;
        margin-bottom: 15px !important;
    }
    
    /* 模拟声纹卡片样式 */
    .speaker-card {
        background-color: #1f242c;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 16px;
        margin: 10px 0;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        transition: transform 0.2s ease, border-color 0.2s ease;
    }
    .speaker-card:hover {
        transform: translateY(-2px);
        border-color: #00d4aa;
    }
    
    /* 提亮代码块与运行日志 */
    code, pre {
        background-color: #161b22 !important;
        color: #39ebc7 !important;
        border: 1px solid #30363d;
        border-radius: 6px;
    }
    
    /* 自定义按钮高亮 */
    div.stButton > button:first-child {
        background-color: #00d4aa !important;
        color: #0e1117 !important;
        border: none !important;
        border-radius: 6px !important;
        padding: 8px 16px !important;
        font-weight: 600 !important;
        transition: background-color 0.3s ease, transform 0.1s ease !important;
    }
    div.stButton > button:first-child:hover {
        background-color: #00f0ff !important;
        transform: scale(1.02);
    }
    div.stButton > button:first-child:active {
        transform: scale(0.98);
    }
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# 后端辅助逻辑 (轻量级，避免加载深度学习模型)
# ==============================================================================
def is_pid_running(pid):
    """检测 PID 进程是否仍在运行 (Unix/Mac/Linux 兼容)"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def format_timestamp(seconds: float) -> str:
    """将秒数转换为时分秒.毫秒"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    else:
        return f"{m:02d}:{s:02d}.{ms:03d}"

def regenerate_transcript_md(meeting_dir, meeting_name):
    """根据最新的 transcript.json 重新生成 transcript.md 文件，确保人名实时一致"""
    json_path = os.path.join(meeting_dir, "transcript.json")
    md_path = os.path.join(meeting_dir, "transcript.md")
    if not os.path.exists(json_path):
        return False
        
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        creation_time = "未知时间"
        meta_path = os.path.join(meeting_dir, "meeting_meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf)
                creation_time = meta.get("creation_time", creation_time)
            except:
                pass
                
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# 会议语音转译文档\n\n")
            f.write(f"* **会议名称**: {meeting_name}\n")
            f.write(f"* **生成时间**: {creation_time}\n\n")
            f.write(f"---\n\n")
            
            for seg in data.get("segments", []):
                time_str = f"[{format_timestamp(seg.get('start', 0.0))} - {format_timestamp(seg.get('end', 0.0))}]"
                speaker = seg.get("speaker_name", "未知发言人")
                text = seg.get("text", "").strip()
                f.write(f"**{speaker}** {time_str}  \n{text}\n\n")
        return True
    except Exception as e:
        st.error(f"重新生成 Markdown 文档失败: {e}")
        return False

def promote_unknown_logic(unknown_id, real_name):
    """晋升声纹并将结果反向更新到所有相关的会议记录中"""
    uid_dir = os.path.join(UNKNOWN_DIR, unknown_id)
    if not os.path.exists(uid_dir):
        return False, f"未找到未知声纹 ID: {unknown_id}"
        
    person_dir = os.path.join(DB_DIR, real_name)
    os.makedirs(person_dir, exist_ok=True)
    
    timestamp = int(time.time() * 1000)
    
    # 移动声纹文件特征 .npy
    src_npy = os.path.join(uid_dir, "feature.npy")
    if os.path.exists(src_npy):
        dst_npy = os.path.join(person_dir, f"promoted_{unknown_id}_{timestamp}.npy")
        shutil.copy2(src_npy, dst_npy)
        
    # 移动配套的音频切片文件 .wav
    src_wav = os.path.join(uid_dir, "feature.wav")
    if os.path.exists(src_wav):
        dst_wav = os.path.join(person_dir, f"promoted_{unknown_id}_{timestamp}.wav")
        shutil.copy2(src_wav, dst_wav)
        
    # 清理未知声纹缓存目录
    shutil.rmtree(uid_dir, ignore_errors=True)
    
    # 反向更新会议目录下的所有引用
    updated_meetings = []
    if os.path.exists(MEETINGS_DIR):
        for meeting_name in os.listdir(MEETINGS_DIR):
            meeting_dir = os.path.join(MEETINGS_DIR, meeting_name)
            if not os.path.isdir(meeting_dir) or meeting_name.startswith('.'):
                continue
                
            meta_path = os.path.join(meeting_dir, "meeting_meta.json")
            if not os.path.exists(meta_path):
                continue
                
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except:
                continue
                
            speaker_refs = meta.get("speaker_references", {})
            need_update = False
            target_speakers = []
            
            for spk_label, ref_info in speaker_refs.items():
                if ref_info.get("voiceprint_id") == unknown_id:
                    target_speakers.append(spk_label)
                    ref_info["voiceprint_type"] = "labeled"
                    ref_info["voiceprint_id"] = real_name
                    need_update = True
                    
            if not need_update:
                continue
                
            # 保存更新后的 meeting_meta.json
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
                
            # 更新 transcript.json 中的说话人姓名
            json_path = os.path.join(meeting_dir, "transcript.json")
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        t_data = json.load(f)
                        
                    for seg in t_data.get("segments", []):
                        if seg.get("speaker") in target_speakers:
                            seg["speaker_name"] = real_name
                        if "words" in seg:
                            for w in seg["words"]:
                                if w.get("speaker") in target_speakers:
                                    w["speaker_name"] = real_name
                                    
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(t_data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"Update transcript.json error: {e}")
                    
            # 重新生成 transcript.md
            regenerate_transcript_md(meeting_dir, meeting_name)
            
            # 更新 summary.md 中的姓名
            summary_path = os.path.join(meeting_dir, "summary.md")
            if os.path.exists(summary_path):
                try:
                    with open(summary_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    content = content.replace("未知发言人", real_name)
                    content = content.replace(unknown_id, real_name)
                    with open(summary_path, "w", encoding="utf-8") as f:
                        f.write(content)
                except Exception as e:
                    print(f"Update summary.md error: {e}")
                    
            updated_meetings.append(meeting_name)
            
    return True, f"成功将未知发言人声纹标定为【{real_name}】！已反向更新 {len(updated_meetings)} 个会议的历史记录。"

def update_meeting_speaker_name(meeting_name, speaker_label, new_name):
    """单场会议层面的声纹标记更名与重新匹配功能"""
    meeting_dir = os.path.join(MEETINGS_DIR, meeting_name)
    meta_path = os.path.join(meeting_dir, "meeting_meta.json")
    if not os.path.exists(meta_path):
        return False, "未找到会议元数据文件"
        
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
            
        speaker_refs = meta.get("speaker_references", {})
        if speaker_label not in speaker_refs:
            return False, f"未找到发言人标签: {speaker_label}"
            
        # 更新该发言人引用所绑定的声纹角色
        speaker_refs[speaker_label]["voiceprint_type"] = "labeled"
        speaker_refs[speaker_label]["voiceprint_id"] = new_name
        
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
            
        # 更新 transcript.json 中此标签下的所有 speaker_name
        json_path = os.path.join(meeting_dir, "transcript.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                t_data = json.load(f)
                
            for seg in t_data.get("segments", []):
                if seg.get("speaker") == speaker_label:
                    seg["speaker_name"] = new_name
                if "words" in seg:
                    for w in seg["words"]:
                        if w.get("speaker") == speaker_label:
                            w["speaker_name"] = new_name
                            
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(t_data, f, ensure_ascii=False, indent=2)
                
        # 重新生成相应的 transcript.md 对话文本
        regenerate_transcript_md(meeting_dir, meeting_name)
        
        # 提示用户重新生成 AI 总结，或尝试在现有 summary.md 中简单替换 (如果存在)
        summary_path = os.path.join(meeting_dir, "summary.md")
        if os.path.exists(summary_path):
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # 尝试将转写里匹配的名称做一次全局替换
                content = content.replace("未知发言人", new_name)
                with open(summary_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                print(f"Update summary.md error: {e}")
                
        return True, f"成功将 【{speaker_label}】 的声纹角色重新匹配为【{new_name}】！"
    except Exception as e:
        return False, f"重新匹配更名失败: {e}"

def get_speaker_templates(speaker_name):
    """获取指定已命名发言人下的所有声纹特征及配套音频文件列表"""
    p_dir = os.path.join(DB_DIR, speaker_name)
    templates = []
    if os.path.exists(p_dir):
        for f in os.listdir(p_dir):
            if f.endswith('.npy'):
                base = os.path.splitext(f)[0]
                wav_path = os.path.join(p_dir, base + ".wav")
                templates.append({
                    "npy_name": f,
                    "npy_path": os.path.join(p_dir, f),
                    "wav_path": wav_path if os.path.exists(wav_path) else None,
                    "base_name": base
                })
    return templates

def move_speaker_template(speaker_name, template_name, target_speaker):
    """将全局库中已经匹配的某项声纹模板移动（重新匹配）到另外的人员名下"""
    src_npy = os.path.join(DB_DIR, speaker_name, template_name)
    dst_npy = os.path.join(DB_DIR, target_speaker, template_name)
    
    base = os.path.splitext(template_name)[0]
    src_wav = os.path.join(DB_DIR, speaker_name, base + ".wav")
    dst_wav = os.path.join(DB_DIR, target_speaker, base + ".wav")
    
    if os.path.exists(src_npy):
        os.makedirs(os.path.dirname(dst_npy), exist_ok=True)
        shutil.move(src_npy, dst_npy)
    if os.path.exists(src_wav):
        shutil.move(src_wav, dst_wav)
        
    # 清理因移出而变空的旧发言人目录
    src_dir = os.path.join(DB_DIR, speaker_name)
    if os.path.exists(src_dir) and not os.listdir(src_dir):
        shutil.rmtree(src_dir, ignore_errors=True)

def delete_speaker_template(speaker_name, template_name):
    """从全局数据库中彻底删除某条声纹模版"""
    npy_path = os.path.join(DB_DIR, speaker_name, template_name)
    base = os.path.splitext(template_name)[0]
    wav_path = os.path.join(DB_DIR, speaker_name, base + ".wav")
    
    if os.path.exists(npy_path):
        os.remove(npy_path)
    if os.path.exists(wav_path):
        os.remove(wav_path)
        
    # 清理因删除而变空的旧发言人目录
    src_dir = os.path.join(DB_DIR, speaker_name)
    if os.path.exists(src_dir) and not os.listdir(src_dir):
        shutil.rmtree(src_dir, ignore_errors=True)

def get_audio_files():
    """扫描项目根目录下的可用会议录音文件"""
    files = []
    for ext in ["*.mp3", "*.wav", "*.m4a", "*.flac"]:
        files.extend(glob.glob(os.path.join(PROJECT_ROOT, ext)))
    return sorted([os.path.basename(f) for f in files])

def get_meeting_history():
    """获取所有已转译的会议历史记录"""
    history = []
    if os.path.exists(MEETINGS_DIR):
        for m_name in os.listdir(MEETINGS_DIR):
            m_dir = os.path.join(MEETINGS_DIR, m_name)
            if os.path.isdir(m_dir) and not m_name.startswith('.'):
                meta_path = os.path.join(m_dir, "meeting_meta.json")
                creation_time = "未知"
                engine_used = "未知"
                status = "已完成"
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                        creation_time = meta.get("creation_time", "未知")
                        engine_used = meta.get("engine_used", "未知")
                        status = meta.get("status", "已完成")
                    except:
                        pass
                
                has_transcript = os.path.exists(os.path.join(m_dir, "transcript.md"))
                has_summary = os.path.exists(os.path.join(m_dir, "summary.md"))
                
                history.append({
                    "name": m_name,
                    "dir": m_dir,
                    "creation_time": creation_time,
                    "engine": engine_used,
                    "status": status,
                    "has_transcript": has_transcript,
                    "has_summary": has_summary
                })
    return sorted(history, key=lambda x: x["creation_time"], reverse=True)

def get_named_speakers():
    """获取声纹库中已命名的发言人列表"""
    speakers = []
    if os.path.exists(DB_DIR):
        for name in os.listdir(DB_DIR):
            p_dir = os.path.join(DB_DIR, name)
            if os.path.isdir(p_dir) and not name.startswith('.') and name != "_unknown":
                npy_count = len([f for f in os.listdir(p_dir) if f.endswith('.npy')])
                speakers.append({"name": name, "npy_count": npy_count})
    return sorted(speakers, key=lambda x: x["name"])

def get_unknown_speakers():
    """获取等待标定的全局未知发言人声纹池"""
    unknowns = []
    if os.path.exists(UNKNOWN_DIR):
        for uid in os.listdir(UNKNOWN_DIR):
            uid_dir = os.path.join(UNKNOWN_DIR, uid)
            if os.path.isdir(uid_dir) and not uid.startswith('.'):
                wav_path = os.path.join(uid_dir, "feature.wav")
                npy_path = os.path.join(uid_dir, "feature.npy")
                if os.path.exists(npy_path):
                    referred_meetings = []
                    sample_texts = {}
                    
                    if os.path.exists(MEETINGS_DIR):
                        for m_name in os.listdir(MEETINGS_DIR):
                            m_dir = os.path.join(MEETINGS_DIR, m_name)
                            if not os.path.isdir(m_dir) or m_name.startswith('.'):
                                continue
                            meta_path = os.path.join(m_dir, "meeting_meta.json")
                            if os.path.exists(meta_path):
                                try:
                                    with open(meta_path, "r", encoding="utf-8") as f:
                                        meta = json.load(f)
                                    speaker_refs = meta.get("speaker_references", {})
                                    
                                    matched_spk_labels = []
                                    for spk_label, ref_info in speaker_refs.items():
                                        if ref_info.get("voiceprint_id") == uid:
                                            matched_spk_labels.append(spk_label)
                                            
                                    if matched_spk_labels:
                                        referred_meetings.append(m_name)
                                        json_path = os.path.join(m_dir, "transcript.json")
                                        if os.path.exists(json_path):
                                            with open(json_path, "r", encoding="utf-8") as jf:
                                                t_data = json.load(jf)
                                            samples = []
                                            for seg in t_data.get("segments", []):
                                                if seg.get("speaker") in matched_spk_labels:
                                                    samples.append(seg.get("text", "").strip())
                                                    if len(samples) >= 3:
                                                        break
                                            if samples:
                                                sample_texts[m_name] = samples
                                except:
                                    pass
                    
                    unknowns.append({
                        "id": uid,
                        "wav_path": wav_path if os.path.exists(wav_path) else None,
                        "meetings": referred_meetings,
                        "samples": sample_texts
                    })
    return sorted(unknowns, key=lambda x: x["id"])

# ==============================================================================
# Streamlit 界面导航
# ==============================================================================
st.sidebar.markdown("<h2 style='text-align: center;'>🎙️ 会议声纹转译系统</h2>", unsafe_allow_html=True)
st.sidebar.markdown("---")

panel = st.sidebar.radio(
    "功能控制中心",
    ["🚀 任务启动与跟踪", "👥 声纹标定中心"],
    index=0
)

# 提示栏
st.sidebar.markdown("---")
st.sidebar.markdown("### 系统状态")
named_speakers_count = len(get_named_speakers())
pending_unknowns_count = len(get_unknown_speakers())
st.sidebar.metric("已录入人员", f"{named_speakers_count} 人")
st.sidebar.metric("待标定声纹", f"{pending_unknowns_count} 个")

# ==============================================================================
# Panel A: 任务启动与跟踪
# ==============================================================================
if panel == "🚀 任务启动与跟踪":
    st.markdown("<h1>🚀 会议转译与任务启动跟踪</h1>", unsafe_allow_html=True)
    
    # 检测后台转写任务状态
    is_running = False
    if "running_pid" in st.session_state:
        pid = st.session_state["running_pid"]
        if is_pid_running(pid):
            is_running = True
        else:
            del st.session_state["running_pid"]
            
    # 上半部分：任务配置与启动
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown("### 📥 选择会议音频")
        
        # 支持上传音频文件
        uploaded_file = st.file_uploader("上传会议音频 (.mp3, .wav, .m4a)", type=["mp3", "wav", "m4a", "flac"])
        if uploaded_file is not None:
            save_path = os.path.join(PROJECT_ROOT, uploaded_file.name)
            with open(save_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            st.success(f"文件上传成功: {uploaded_file.name}")
            
        # 选择已有文件
        local_audios = get_audio_files()
        if not local_audios:
            st.warning("⚠️ 根目录下没有找到音频文件，请在上方上传文件。")
            selected_audio = None
        else:
            selected_audio = st.selectbox("从项目根目录选择录音:", local_audios)
            
    with col2:
        st.markdown("### ⚙️ 参数与引擎配置")
        
        with st.expander("高级转译参数设置", expanded=True):
            engine = st.selectbox("选择转译引擎 (ASR Engine)", ["whispercpp", "whisperx"], index=0)
            threshold = st.slider("声纹匹配余弦相似度阈值 (Threshold)", 0.50, 0.95, 0.68, 0.01)
            
            use_num_speakers = st.checkbox("指定会议发言人人数（已知人数时可勾选）")
            num_speakers = None
            if use_num_speakers:
                num_speakers = st.number_input("发言人总数", min_value=1, max_value=20, value=2, step=1)
                
        # 启动转写按钮
        if is_running:
            st.button("🚀 转译任务正在后台进行...", disabled=True)
        else:
            if selected_audio:
                launch_btn = st.button("🚀 开始转译会议")
                if launch_btn:
                    audio_full_path = os.path.join(PROJECT_ROOT, selected_audio)
                    meeting_name = os.path.splitext(selected_audio)[0]
                    meeting_output_dir = os.path.join(MEETINGS_DIR, meeting_name)
                    os.makedirs(meeting_output_dir, exist_ok=True)
                    log_file_path = os.path.join(meeting_output_dir, "run_transcription.log")
                    
                    # 组合 CLI 命令
                    cmd = ["uv", "run", "python", "voiceprint_system/main.py", 
                           "--audio", audio_full_path, 
                           "--engine", engine, 
                           "--threshold", str(threshold)]
                    if num_speakers:
                        cmd.extend(["--num-speakers", str(num_speakers)])
                        
                    # 以非交互模式运行（默认会自动跳过手动输入，将未知发言人保存至数据库暂存池）
                    # 写入启动日志
                    with open(log_file_path, "w", encoding="utf-8") as lf:
                        lf.write(f"[Command] {' '.join(cmd)}\n")
                        lf.write("[Status] 任务已启动...\n\n")
                        
                    # 启动后台子进程
                    try:
                        p = subprocess.Popen(
                            cmd,
                            cwd=PROJECT_ROOT,
                            stdout=open(log_file_path, "a", encoding="utf-8", errors="ignore"),
                            stderr=subprocess.STDOUT,
                            text=True
                        )
                        st.session_state["running_pid"] = p.pid
                        st.session_state["running_meeting"] = meeting_name
                        st.session_state["log_path"] = log_file_path
                        st.success(f"🎉 任务已成功启动，正在后台转译：{meeting_name}！")
                        st.rerun()
                    except Exception as e:
                        st.error(f"启动失败: {e}")
            else:
                st.button("🚀 开始转译会议", disabled=True)
                
    st.markdown("---")
    
    # 实时日志查看器
    if is_running:
        st.markdown(f"### ⏳ 实时运行日志跟踪 (正在处理: **{st.session_state.get('running_meeting')}**)")
        
        # 终止任务按钮
        if st.button("⏹️ 终止当前任务"):
            try:
                os.kill(pid, 9)
                st.warning("任务已被终止。")
                if "running_pid" in st.session_state:
                    del st.session_state["running_pid"]
                st.rerun()
            except Exception as e:
                st.error(f"终止进程失败: {e}")
                
        # 实时渲染日志
        log_path = st.session_state.get("log_path")
        if log_path and os.path.exists(log_path):
            log_placeholder = st.empty()
            
            # 定时器刷新逻辑
            for _ in range(15):
                if not is_pid_running(pid):
                    st.success("🎉 任务转译已完成！")
                    if "running_pid" in st.session_state:
                        del st.session_state["running_pid"]
                    st.rerun()
                    break
                with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                    lines = lf.readlines()
                    # 展示最后 40 行日志
                    log_placeholder.code("".join(lines[-40:]))
                time.sleep(1)
            st.button("🔄 刷新最新日志")
            
    # 下半部分：历史会议列表与查看
    st.markdown("### 📅 历史转译会议")
    
    # 判断是否为 inline 浏览模式
    if "view_meeting" in st.session_state:
        v_meeting = st.session_state["view_meeting"]
        v_mode = st.session_state["view_mode"]
        m_dir = os.path.join(MEETINGS_DIR, v_meeting)
        
        st.markdown(f"#### 📄 会议【{v_meeting}】的 {'转译文档' if v_mode == 'transcript' else '总结内容'}")
        
        if st.button("⬅️ 返回历史列表", key="back_to_list"):
            del st.session_state["view_meeting"]
            del st.session_state["view_mode"]
            st.rerun()
            
        # ==========================================
        # 针对已完成声纹匹配的进行“声纹再匹配/更名”功能
        # ==========================================
        if v_mode == "transcript":
            meta_path = os.path.join(m_dir, "meeting_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    speaker_refs = meta.get("speaker_references", {})
                    
                    with st.expander("🗣️ 发言人声纹再匹配与更名 (会议局部更正)", expanded=False):
                        st.markdown("如果您发现当前转译文档中某位发言人的声纹名字匹配有误，可在下方针对此会议进行重新映射：")
                        for spk_label, ref_info in speaker_refs.items():
                            curr_name = ref_info.get("voiceprint_id", "未知发言人")
                            curr_type = ref_info.get("voiceprint_type", "unlabeled")
                            
                            cols_rematch = st.columns([2, 3, 3, 2])
                            with cols_rematch[0]:
                                st.markdown(f"**{spk_label}**")
                                st.caption(f"当前匹配: `{curr_name}` ({'已标定' if curr_type == 'labeled' else '未标定'})")
                            with cols_rematch[1]:
                                existing_speakers = [""] + [s['name'] for s in get_named_speakers()]
                                sel_rematch = st.selectbox(
                                    "选择已有发言人", 
                                    existing_speakers, 
                                    key=f"rematch_sel_{v_meeting}_{spk_label}"
                                )
                            with cols_rematch[2]:
                                txt_rematch = st.text_input(
                                    "或输入全新姓名", 
                                    key=f"rematch_txt_{v_meeting}_{spk_label}"
                                )
                            with cols_rematch[3]:
                                st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)
                                if st.button("💾 确认重新匹配", key=f"rematch_btn_{v_meeting}_{spk_label}"):
                                    target_name = txt_rematch.strip() if txt_rematch.strip() else sel_rematch
                                    if not target_name:
                                        st.error("请选择或输入名称")
                                    else:
                                        success, msg = update_meeting_speaker_name(v_meeting, spk_label, target_name)
                                        if success:
                                            st.success(msg)
                                            time.sleep(1)
                                            st.rerun()
                                        else:
                                            st.error(msg)
                except Exception as e:
                    st.error(f"加载声纹再匹配功能失败: {e}")
                    
        file_to_read = "transcript.md" if v_mode == "transcript" else "summary.md"
        target_file = os.path.join(m_dir, file_to_read)
        if os.path.exists(target_file):
            with open(target_file, "r", encoding="utf-8") as f:
                st.markdown(f.read())
        else:
            st.error(f"无法读取该文件: {file_to_read}")
            
    else:
        history = get_meeting_history()
        if not history:
            st.info("💡 暂无历史会议记录。请在上方上传或选择文件并点击“开始转译”。")
        else:
            for item in history:
                with st.container():
                    st.markdown(f"""
                    <div class="speaker-card">
                        <h4 style="margin: 0; color: #00d4aa;">📅 {item['name']}</h4>
                        <p style="margin: 5px 0 10px 0; font-size: 0.9em; color: #8b949e;">
                            创建时间: {item['creation_time']} &nbsp;|&nbsp; 引擎: {item['engine']}
                        </p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    btn_cols = st.columns([1.5, 1.5, 2.0, 5])
                    
                    with btn_cols[0]:
                        if item['has_transcript']:
                            if st.button("🔍 查看转译文本", key=f"btn_tr_{item['name']}"):
                                st.session_state["view_meeting"] = item['name']
                                st.session_state["view_mode"] = "transcript"
                                st.rerun()
                        else:
                            st.button("🔍 暂无文本", key=f"btn_tr_no_{item['name']}", disabled=True)
                            
                    with btn_cols[1]:
                        if item['has_summary']:
                            if st.button("📝 查看纪要总结", key=f"btn_sm_{item['name']}"):
                                st.session_state["view_meeting"] = item['name']
                                st.session_state["view_mode"] = "summary"
                                st.rerun()
                        else:
                            st.button("📝 暂无总结", key=f"btn_sm_no_{item['name']}", disabled=True)
                            
                    # ==========================================
                    # AI 总结后端触发按钮
                    # ==========================================
                    with btn_cols[2]:
                        sum_btn_label = "🔄 重新生成总结" if item['has_summary'] else "🤖 生成 AI 总结"
                        if st.button(sum_btn_label, key=f"btn_ai_sum_{item['name']}"):
                            with st.spinner("🤖 正在调用 AI 大模型生成深度会议总结，请稍候..."):
                                try:
                                    from voiceprint_system.src.summary import generate_summary_document
                                    generate_summary_document(item['dir'])
                                    st.success("🎉 会议总结已成功生成并写入 summary.md！")
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"生成总结失败: {e}")
                    st.write("")

# ==============================================================================
# Panel B: 声纹标定中心
# ==============================================================================
elif panel == "👥 声纹标定中心":
    st.markdown("<h1>👥 声纹库与全局发言人标定中心</h1>", unsafe_allow_html=True)
    
    col_left, col_right = st.columns([2, 3])
    
    # 左侧：已命名的声纹特征库预览及声纹移动/删除管理（完成声纹匹配后的再匹配管理）
    with col_left:
        st.markdown("### 🗄️ 声纹库已命名发言人 (声纹管理与再匹配)")
        speakers = get_named_speakers()
        if not speakers:
            st.info("💡 库里目前还没有被标记姓名的发言人声纹。")
        else:
            for spk in speakers:
                with st.expander(f"👤 {spk['name']} (共有 {spk['npy_count']} 个声纹样本)", expanded=False):
                    templates = get_speaker_templates(spk['name'])
                    if not templates:
                        st.info("该目录中暂无声纹特征数据文件。")
                    else:
                        for t in templates:
                            st.markdown(f"📄 **样本特征**: `{t['npy_name']}`")
                            if t['wav_path']:
                                st.audio(t['wav_path'])
                            
                            cols_t = st.columns([2, 1, 1])
                            with cols_t[0]:
                                other_spks = [""] + [s['name'] for s in speakers if s['name'] != spk['name']]
                                move_target = st.selectbox(
                                    "重新匹配至他人:", 
                                    other_spks, 
                                    key=f"move_sel_{spk['name']}_{t['npy_name']}"
                                )
                            with cols_t[1]:
                                st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                                if st.button("🔄 移动", key=f"move_btn_{spk['name']}_{t['npy_name']}"):
                                    if move_target:
                                        move_speaker_template(spk['name'], t['npy_name'], move_target)
                                        st.success(f"声纹样本已移至【{move_target}】名下！")
                                        time.sleep(1)
                                        st.rerun()
                                    else:
                                        st.error("请选择目标人员")
                            with cols_t[2]:
                                st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                                if st.button("🗑️ 删除", key=f"del_btn_{spk['name']}_{t['npy_name']}"):
                                    delete_speaker_template(spk['name'], t['npy_name'])
                                    st.success("声纹样本已从库中删除！")
                                    time.sleep(1)
                                    st.rerun()
                
    # 右侧：等待标注的全局未知声纹卡片列表与丢弃非人声/混杂声音数据功能
    with col_right:
        st.markdown("### ⏳ 待标定全局声纹池")
        unknowns = get_unknown_speakers()
        if not unknowns:
            st.success("🎉 太棒了！当前待标定的声纹池是空的，所有声音都已经有了主人！")
        else:
            for unk in unknowns:
                with st.container():
                    st.markdown(f"""
                    <div style="background-color: #1f242c; border: 1px solid #30363d; border-radius: 8px; padding: 15px; margin-bottom: 15px;">
                        <h4 style="margin-top: 0; color: #ff7b72;">🔊 未知发言人: {unk['id']}</h4>
                        <p style="font-size: 0.9em; margin-bottom: 5px;">出现的会议: <b>{', '.join(unk['meetings']) or '未知会议'}</b></p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # 播放按钮
                    if unk['wav_path']:
                        st.audio(unk['wav_path'])
                    else:
                        st.warning("暂无配套的人声音频剪辑样本。")
                        
                    # 渲染出现的样例文字（帮助用户识别）
                    if unk['samples']:
                        st.markdown("##### 🔊 说话样例文本参考:")
                        for m_title, phrases in unk['samples'].items():
                            for p in phrases:
                                st.markdown(f"- *\"{p}\"* (来自会议: {m_title})")
                                
                    # 标定逻辑表单
                    st.markdown("##### 🏷️ 确认人名标定")
                    
                    # 文本输入：可以是全新姓名
                    name_input = st.text_input(f"方法一：输入新成员姓名", key=f"txt_name_{unk['id']}")
                    
                    # 下拉列表：关联已有人员姓名
                    existing_names = [""] + [s['name'] for s in speakers]
                    name_select = st.selectbox(f"方法二：选择已有成员姓名进行特征追加合并", existing_names, key=f"sel_name_{unk['id']}")
                    
                    # 执行标定按钮与丢弃混杂音按钮
                    st.write("")
                    btn_cols_op = st.columns([2, 2, 4])
                    
                    with btn_cols_op[0]:
                        promote_click = st.button("✅ 确认标定", key=f"btn_promo_{unk['id']}")
                        if promote_click:
                            final_name = name_input.strip() if name_input.strip() else name_select
                            if not final_name:
                                st.error("请输入或选择姓名！")
                            else:
                                success, msg = promote_unknown_logic(unk['id'], final_name)
                                if success:
                                    st.success(msg)
                                    time.sleep(1.5)
                                    st.rerun()
                                else:
                                    st.error(msg)
                                    
                    # ==========================================
                    # 丢弃混杂声纹样本功能按钮
                    # ==========================================
                    with btn_cols_op[1]:
                        discard_click = st.button("🗑️ 丢弃此声纹", key=f"btn_discard_{unk['id']}")
                        if discard_click:
                            uid_dir = os.path.join(UNKNOWN_DIR, unk['id'])
                            shutil.rmtree(uid_dir, ignore_errors=True)
                            st.success(f"已成功丢弃无效声纹样本 {unk['id']}！")
                            time.sleep(1)
                            st.rerun()
                    st.markdown("---")
