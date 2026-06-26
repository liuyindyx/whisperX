import os
import json
import urllib.request
import urllib.error

# 从环境变量中获取 API Key 作为默认备用
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

# 配置文件持久化路径 (位于 voiceprint_system/database/config.json，以支持容器挂载持久化)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "config.json")

def load_config() -> dict:
    """加载持久化配置文件，如果不存在则使用默认配置并融合环境变量"""
    default_config = {
        "provider": "ollama",  # 默认使用本地 Ollama
        "deepseek_api_key": DEEPSEEK_API_KEY,
        "google_api_key": GOOGLE_API_KEY,
        "ollama_host": "http://localhost:11434",
        "ollama_model": "gemma4:12b-32k"  # 默认对应 Gemma 4 12B 32K 模型
    }
    
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
                # 融合默认值，防止旧版本配置文件缺少新字段
                for k, v in default_config.items():
                    if k not in config:
                        config[k] = v
                return config
        except Exception as e:
            print(f"[Warning] 加载配置文件失败: {e}")
            
    return default_config

def call_ollama(prompt: str, host: str, model: str) -> str:
    """使用标准库 urllib 调用本地 Ollama API (一次性返回)"""
    url = f"{host}/api/chat"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是一个严谨高效的会议总结专家，擅长从转写文本中提炼出重点突出、逻辑严密的会议纪要。"
            },
            {"role": "user", "content": prompt}
        ],
        "options": {
            "num_ctx": 32768  # 启用 32K 长上下文
        },
        "stream": False
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    
    try:
        # 增加超时时间至 120 秒，因为本地跑 12B 大模型生成可能较慢
        with urllib.request.urlopen(req, timeout=120) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            return resp_data["message"]["content"]
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Ollama API HTTP 错误 ({e.code}): {error_msg}")
    except Exception as e:
        raise RuntimeError(f"Ollama API 连接失败 (请检查 Ollama 是否在后台运行且模型已 pull): {e}")

def call_ollama_stream(prompt: str, host: str, model: str):
    """使用标准库 urllib 流式调用本地 Ollama API"""
    url = f"{host}/api/chat"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是一个严谨高效的会议总结专家，擅长从转写文本中提炼出重点突出、逻辑严密的会议纪要。"
            },
            {"role": "user", "content": prompt}
        ],
        "options": {
            "num_ctx": 32768  # 启用 32K 长上下文
        },
        "stream": True
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            for line in response:
                if line:
                    chunk = json.loads(line.decode("utf-8"))
                    chunk_content = chunk.get("message", {}).get("content", "")
                    if chunk_content:
                        yield chunk_content
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Ollama API HTTP 错误 ({e.code}): {error_msg}")
    except Exception as e:
        raise RuntimeError(f"Ollama API 连接失败 (请检查 Ollama 是否在后台运行且模型已 pull): {e}")

def call_deepseek_stream(prompt: str, api_key: str):
    """使用标准库 urllib 流式调用 DeepSeek API"""
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
        "temperature": 0.2,
        "stream": True
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(data).encode("utf-8"), 
        headers=headers, 
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            for line in response:
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data: "):
                    data_content = line_str[6:]
                    if data_content == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_content)
                        chunk_content = chunk["choices"][0]["delta"].get("content", "")
                        if chunk_content:
                            yield chunk_content
                    except Exception:
                        continue
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"DeepSeek API HTTP 错误 ({e.code}): {error_msg}")
    except Exception as e:
        raise RuntimeError(f"DeepSeek API 连接失败: {e}")

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

def generate_summary_document_stream(meeting_dir: str):
    """
    流式生成总结，并实时 yield 产生的文本块。生成结束后自动保存到 summary.md。
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
    
    # 加载当前保存的配置
    config = load_config()
    provider = config.get("provider", "ollama")
    
    summary_content = ""
    error_logs = []
    
    # 1. 尝试使用选定的 provider 进行流式调用
    if provider == "ollama":
        host = config.get("ollama_host", "http://localhost:11434")
        model = config.get("ollama_model", "gemma4:12b-32k")
        print(f"[LLM Stream] 正在使用本地 Ollama 流式生成总结 (模型: {model}, 接口: {host})...")
        try:
            for chunk in call_ollama_stream(prompt, host, model):
                summary_content += chunk
                yield chunk
        except Exception as e:
            error_logs.append(f"Ollama 流式生成失败: {e}")
            print(f"[LLM Stream] [Warning] Ollama 流式生成失败: {e}")
            
    elif provider == "deepseek":
        api_key = config.get("deepseek_api_key", "")
        if api_key:
            print("[LLM Stream] 正在使用 DeepSeek 流式生成总结...")
            try:
                for chunk in call_deepseek_stream(prompt, api_key):
                    summary_content += chunk
                    yield chunk
            except Exception as e:
                error_logs.append(f"DeepSeek 流式生成失败: {e}")
                print(f"[LLM Stream] [Warning] DeepSeek 流式生成失败: {e}")
        else:
            error_logs.append("未配置 DeepSeek API Key")
            
    elif provider == "gemini":
        api_key = config.get("google_api_key", "")
        if api_key:
            print("[LLM] 正在使用 Google Gemini (非流式一次性返回) 生成总结...")
            try:
                content = call_gemini(prompt, api_key)
                summary_content = content
                yield content
            except Exception as e:
                error_logs.append(f"Gemini 生成失败: {e}")
                print(f"[LLM] [Warning] Gemini 生成失败: {e}")
        else:
            error_logs.append("未配置 Google API Key")
            
    # 2. 如果选定服务失败，且没有生成任何内容，尝试 Fallback 自动调用（直接 yield 结果）
    if not summary_content:
        print("[LLM Stream] 选定服务失败，尝试使用备用凭证进行 Fallback 调用...")
        
        # 1. 尝试 Ollama
        if provider != "ollama":
            host = config.get("ollama_host", "http://localhost:11434")
            model = config.get("ollama_model", "gemma4:12b-32k")
            try:
                for chunk in call_ollama_stream(prompt, host, model):
                    summary_content += chunk
                    yield chunk
            except Exception as e:
                error_logs.append(f"Fallback Ollama 失败: {e}")
                
        # 2. 尝试 DeepSeek
        if not summary_content and provider != "deepseek" and config.get("deepseek_api_key"):
            try:
                for chunk in call_deepseek_stream(prompt, config.get("deepseek_api_key")):
                    summary_content += chunk
                    yield chunk
            except Exception as e:
                error_logs.append(f"Fallback DeepSeek 失败: {e}")
                
        # 3. 尝试 Gemini
        if not summary_content and provider != "gemini" and config.get("google_api_key"):
            try:
                content = call_gemini(prompt, config.get("google_api_key"))
                summary_content = content
                yield content
            except Exception as e:
                error_logs.append(f"Fallback Gemini 失败: {e}")
                
    if not summary_content:
        detail_errors = "\n".join(error_logs)
        raise RuntimeError(
            f"所有大模型总结服务均不可用。详细故障日志：\n{detail_errors}"
        )
        
    # 保存结果至 summary.md
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_content)
        
    # 同时更新 meeting_meta.json
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

def generate_summary_document(meeting_dir: str) -> str:
    """ 一次性读取并生成总结，保证向下兼容 """
    full_content = ""
    for chunk in generate_summary_document_stream(meeting_dir):
        full_content += chunk
    return full_content
