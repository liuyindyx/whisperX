import os
import json
import urllib.request
import urllib.error

# 从环境变量中获取 API Key 和 API Base
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

# 提示词模版
SUMMARY_PROMPT_TEMPLATE = """您是一位专业的会议纪要整理专家。请根据以下会议转译内容，生成一份结构清晰、重点突出的会议总结（Summary）。

要求总结必须包含以下三个板块，请严格按照该结构使用 GitHub 风格的 Markdown 格式输出：

# 📊 会议纪要与深度总结

## 1. 👥 发言角色与观点总结
请分析会议中各个主要发言人的核心观点、发言主要态度以及他们提出的关键事项（包括他们分别在会议中承担的角色与贡献）。

## 2. 📅 会议时间线讨论章节
请按照会议进行的时间顺序（结合时间戳），划分出不同的讨论阶段/章节，并写明每个时间段内讨论的核心议题与关键结论。

## 3. 🎯 分主题多维总结与待办事项
请脱离时间线，对整场会议中涉及的核心议题、技术点或业务融合点进行分门别类的主题总结，并梳理出清晰的待办事项列表（To-Do List），明确负责人。

---

以下是会议的转译文本（格式为“发言人 [时间]：发言内容”）：
{transcript_text}
"""

def call_deepseek(prompt: str, api_key: str) -> str:
    """使用标准库 urllib 调用 DeepSeek Chat API"""
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    data = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system", 
                "content": "你是一个严谨高效的会议总结专家，擅长从转写文本中提炼出重点突出、逻辑严密的会议纪要。"
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(data).encode("utf-8"), 
        headers=headers, 
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            return resp_data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"DeepSeek API HTTP 错误 ({e.code}): {error_msg}")
    except Exception as e:
        raise RuntimeError(f"DeepSeek API 连接失败: {e}")

def call_gemini(prompt: str, api_key: str) -> str:
    """使用标准库 urllib 调用 Google Gemini API"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(data).encode("utf-8"), 
        headers=headers, 
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            return resp_data["candidates"][0]["content"]["parts"][0]["text"]
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini API HTTP 错误 ({e.code}): {error_msg}")
    except Exception as e:
        raise RuntimeError(f"Gemini API 连接失败: {e}")

def format_timestamp(seconds: float) -> str:
    """将秒数格式化为 MM:SS"""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"

def generate_summary_document(meeting_dir: str) -> str:
    """
    根据会议专属目录下的 transcript.json，读取文本并调用大模型生成总结，
    最终保存至同级目录下的 summary.md 中。
    """
    json_path = os.path.join(meeting_dir, "transcript.json")
    summary_path = os.path.join(meeting_dir, "summary.md")
    
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"未找到转译 JSON 文件: {json_path}")
        
    with open(json_path, "r", encoding="utf-8") as f:
        transcript_data = json.load(f)
        
    # 提取并格式化转译文本
    formatted_segments = []
    for seg in transcript_data.get("segments", []):
        start = seg.get("start", 0.0)
        speaker = seg.get("speaker_name", "未知发言人")
        text = seg.get("text", "").strip()
        formatted_segments.append(f"{speaker} [{format_timestamp(start)}]: {text}")
        
    transcript_text = "\n".join(formatted_segments)
    
    # 构建 Prompt
    prompt = SUMMARY_PROMPT_TEMPLATE.format(transcript_text=transcript_text)
    
    # 尝试调用大模型
    summary_content = ""
    error_logs = []
    
    # 1. 优先尝试使用 DeepSeek (如果配置了 API Key)
    if DEEPSEEK_API_KEY:
        print("[LLM] 正在使用 DeepSeek 生成会议总结...")
        try:
            summary_content = call_deepseek(prompt, DEEPSEEK_API_KEY)
        except Exception as e:
            error_logs.append(f"DeepSeek 失败: {e}")
            print(f"[LLM] [Warning] DeepSeek 失败: {e}")
            
    # 2. 如果 DeepSeek 失败或未配置，尝试使用 Gemini
    if not summary_content and GOOGLE_API_KEY:
        print("[LLM] 正在使用 Google Gemini 生成会议总结...")
        try:
            summary_content = call_gemini(prompt, GOOGLE_API_KEY)
        except Exception as e:
            error_logs.append(f"Gemini 失败: {e}")
            print(f"[LLM] [Warning] Gemini 失败: {e}")
            
    # 3. 如果两个都失败了或未配置 API Key
    if not summary_content:
        keys_available = []
        if DEEPSEEK_API_KEY: keys_available.append("DEEPSEEK")
        if GOOGLE_API_KEY: keys_available.append("GOOGLE")
        
        detail_errors = "\n".join(error_logs)
        raise RuntimeError(
            f"大模型总结生成失败。检测到可用密钥: {keys_available}。详细错误：\n{detail_errors}"
        )
        
    # 保存 summary.md
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_content)
        
    # 同时更新 meeting_meta.json 中的 status (如果有)
    meta_path = os.path.join(meeting_dir, "meeting_meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["has_summary"] = True
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Warning] 写入元数据失败: {e}")
            
    return summary_content
