import os
import sys
import time
import json
import shutil
import glob
import subprocess
import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, status, Form, UploadFile, File, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext

# Set up project root and import modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from voiceprint_system.src.summary import load_config, CONFIG_PATH, generate_summary_document_stream
from voiceprint_system.src.voiceprint import VoiceprintManager

MEETINGS_DIR = os.path.join(PROJECT_ROOT, "meetings")
DB_DIR = os.path.join(PROJECT_ROOT, "voiceprint_system", "database")
UNKNOWN_DIR = os.path.join(DB_DIR, "_unknown")

os.makedirs(MEETINGS_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(UNKNOWN_DIR, exist_ok=True)

# ----------------------------------------------------------------------
# Security & JWT Setup
# ----------------------------------------------------------------------
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

# Load configuration with authentication defaults
def load_server_config():
    cfg = load_config()
    dirty = False
    if "admin_username" not in cfg:
        cfg["admin_username"] = "admin"
        dirty = True
    if "admin_password_hash" not in cfg:
        # Default password is 'admin123'
        cfg["admin_password_hash"] = pwd_context.hash("admin123")
        dirty = True
    if "jwt_secret_key" not in cfg:
        import uuid
        cfg["jwt_secret_key"] = uuid.uuid4().hex
        dirty = True
    if dirty:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg

# Load initial config to read JWT keys
app_config = load_server_config()
SECRET_KEY = app_config["jwt_secret_key"]
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 24 * 60  # 1 day

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

class ConfigUpdateRequest(BaseModel):
    provider: str
    ollama_host: Optional[str] = None
    ollama_model: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    google_api_key: Optional[str] = None

class TranscribeRequest(BaseModel):
    audio: str
    engine: str = "whisperx"
    threshold: float = 0.68
    num_speakers: Optional[int] = None

class RenameSpeakerRequest(BaseModel):
    new_name: str

class MoveTemplateRequest(BaseModel):
    target_speaker: str

class RemapSpeakerRequest(BaseModel):
    speaker_label: str
    new_name: str

# ----------------------------------------------------------------------
# Helper Functions
# ----------------------------------------------------------------------
def is_pid_running(pid: int) -> bool:
    """Check if process with PID is running"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    cfg = load_server_config()
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, cfg["jwt_secret_key"], algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
        
    if token_data.username != cfg["admin_username"]:
        raise credentials_exception
    return token_data.username

def create_access_token(data: dict, expires_delta: Optional[datetime.timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=15)
    to_encode.update({"exp": expire})
    cfg = load_server_config()
    encoded_jwt = jwt.encode(to_encode, cfg["jwt_secret_key"], algorithm=ALGORITHM)
    return encoded_jwt

def format_timestamp(seconds: float) -> str:
    """Format seconds to HH:MM:SS.mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    else:
        return f"{m:02d}:{s:02d}.{ms:03d}"

def regenerate_transcript_md(meeting_dir, meeting_name):
    """Regenerate transcript.md from transcript.json"""
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
        print(f"Failed to regenerate Markdown document: {e}")
        return False

# In-memory dictionary to track running processes
# key: meeting_name, value: subprocess.Popen object
running_processes: Dict[str, subprocess.Popen] = {}

# ----------------------------------------------------------------------
# FastAPI Setup
# ----------------------------------------------------------------------
app = FastAPI(
    title="🎙️ WhisperX Voiceprint API Server",
    description="Backend API for meeting transcription and speaker voiceprint management",
    version="1.0.0"
)

# ----------------------------------------------------------------------
# Authentication Endpoints
# ----------------------------------------------------------------------
@app.post("/api/auth/login", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    cfg = load_server_config()
    if form_data.username != cfg["admin_username"] or not pwd_context.verify(form_data.password, cfg["admin_password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": form_data.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/auth/verify")
async def verify_token(current_user: str = Depends(get_current_user)):
    return {"status": "authenticated", "username": current_user}

@app.post("/api/auth/change-password")
async def change_password(req: ChangePasswordRequest, current_user: str = Depends(get_current_user)):
    cfg = load_server_config()
    if not pwd_context.verify(req.old_password, cfg["admin_password_hash"]):
        raise HTTPException(status_code=400, detail="Incorrect old password")
    
    cfg["admin_password_hash"] = pwd_context.hash(req.new_password)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return {"message": "Password changed successfully"}

# ----------------------------------------------------------------------
# Audio & Meeting Endpoints
# ----------------------------------------------------------------------
@app.get("/api/audios")
async def get_audio_files(current_user: str = Depends(get_current_user)):
    """Scan root folder for audio files"""
    files = []
    for ext in ["*.mp3", "*.wav", "*.m4a", "*.flac"]:
        files.extend(glob.glob(os.path.join(PROJECT_ROOT, ext)))
    return sorted([os.path.basename(f) for f in files])

@app.post("/api/meetings/upload")
async def upload_audio_file(file: UploadFile = File(...), current_user: str = Depends(get_current_user)):
    """Upload a new audio file to project root"""
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".mp3", ".wav", ".m4a", ".flac"]:
        raise HTTPException(status_code=400, detail="Unsupported audio format")
    
    dest_path = os.path.join(PROJECT_ROOT, filename)
    try:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {"filename": filename, "message": "Uploaded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.get("/api/meetings")
async def get_meetings(current_user: str = Depends(get_current_user)):
    """Get history of meetings"""
    history = []
    if os.path.exists(MEETINGS_DIR):
        for m_name in os.listdir(MEETINGS_DIR):
            m_dir = os.path.join(MEETINGS_DIR, m_name)
            if os.path.isdir(m_dir) and not m_name.startswith('.'):
                meta_path = os.path.join(m_dir, "meeting_meta.json")
                creation_time = "未知"
                engine_used = "未知"
                status_str = "已完成"
                
                # Check meta
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                        creation_time = meta.get("creation_time", "未知")
                        engine_used = meta.get("engine_used", "未知")
                        status_str = meta.get("status", "已完成")
                    except:
                        pass
                
                # Dynamic check running status
                if status_str in ["processing", "transcribing"]:
                    # Check in-memory first
                    proc = running_processes.get(m_name)
                    if proc and proc.poll() is None:
                        status_str = "processing"
                    else:
                        # Fallback check by reading meta's PID
                        meta_pid = None
                        if os.path.exists(meta_path):
                            try:
                                with open(meta_path, "r", encoding="utf-8") as f:
                                    meta = json.load(f)
                                meta_pid = meta.get("pid")
                            except:
                                pass
                        if meta_pid and is_pid_running(meta_pid):
                            status_str = "processing"
                        else:
                            # If not running but status says processing, it must have failed/crashed
                            # Let's verify if transcript exists
                            if os.path.exists(os.path.join(m_dir, "transcript.json")):
                                status_str = "completed"
                            else:
                                status_str = "failed"
                
                has_transcript = os.path.exists(os.path.join(m_dir, "transcript.md"))
                has_summary = os.path.exists(os.path.join(m_dir, "summary.md"))
                
                history.append({
                    "name": m_name,
                    "creation_time": creation_time,
                    "engine": engine_used,
                    "status": status_str,
                    "has_transcript": has_transcript,
                    "has_summary": has_summary
                })
    return sorted(history, key=lambda x: x["creation_time"], reverse=True)

@app.get("/api/meetings/{name}")
async def get_meeting_details(name: str, current_user: str = Depends(get_current_user)):
    """Fetch transcript details, Markdown, and summary"""
    m_dir = os.path.join(MEETINGS_DIR, name)
    if not os.path.exists(m_dir) or not os.path.isdir(m_dir):
        raise HTTPException(status_code=404, detail="Meeting not found")
        
    meta_path = os.path.join(m_dir, "meeting_meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except:
            pass
            
    transcript_json = {}
    json_path = os.path.join(m_dir, "transcript.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                transcript_json = json.load(f)
        except:
            pass
            
    transcript_md = ""
    md_path = os.path.join(m_dir, "transcript.md")
    if os.path.exists(md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                transcript_md = f.read()
        except:
            pass
            
    summary_md = ""
    sum_path = os.path.join(m_dir, "summary.md")
    if os.path.exists(sum_path):
        try:
            with open(sum_path, "r", encoding="utf-8") as f:
                summary_md = f.read()
        except:
            pass

    return {
        "name": name,
        "meta": meta,
        "transcript_json": transcript_json,
        "transcript_md": transcript_md,
        "summary_md": summary_md
    }

@app.delete("/api/meetings/{name}")
async def delete_meeting(name: str, current_user: str = Depends(get_current_user)):
    """Delete a meeting directory"""
    m_dir = os.path.join(MEETINGS_DIR, name)
    if not os.path.exists(m_dir):
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    # Kill process if running
    proc = running_processes.get(name)
    if proc:
        try:
            proc.kill()
        except:
            pass
        running_processes.pop(name, None)
        
    try:
        shutil.rmtree(m_dir)
        return {"message": "Meeting deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete meeting: {str(e)}")

@app.post("/api/meetings/transcribe")
async def start_transcription(req: TranscribeRequest, current_user: str = Depends(get_current_user)):
    """Launch transcription subprocess"""
    audio_filename = req.audio
    audio_full_path = os.path.join(PROJECT_ROOT, audio_filename)
    if not os.path.exists(audio_full_path):
        raise HTTPException(status_code=404, detail=f"Audio file not found: {audio_filename}")
        
    meeting_name = os.path.splitext(audio_filename)[0]
    meeting_output_dir = os.path.join(MEETINGS_DIR, meeting_name)
    os.makedirs(meeting_output_dir, exist_ok=True)
    
    # Check if already running
    if meeting_name in running_processes:
        proc = running_processes[meeting_name]
        if proc.poll() is None:
            return {"message": "Transcription already running", "meeting": meeting_name, "pid": proc.pid}
            
    log_file_path = os.path.join(meeting_output_dir, "run_transcription.log")
    
    # Check torch for default device
    import torch
    default_device = "cpu"
    if torch.cuda.is_available():
        default_device = "cuda"
    elif torch.backends.mps.is_available():
        default_device = "mps"
        
    # Assemble CLI command
    cmd = [
        "uv", "run", "python", "voiceprint_system/main.py",
        "--audio", audio_full_path,
        "--engine", req.engine,
        "--threshold", str(req.threshold),
        "--device", default_device
    ]
    if req.num_speakers:
        cmd.extend(["--num-speakers", str(req.num_speakers)])
        
    # Write start logs
    with open(log_file_path, "w", encoding="utf-8") as lf:
        lf.write(f"[Command] {' '.join(cmd)}\n")
        lf.write("[Status] 任务已启动...\n\n")
        
    try:
        p = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdout=open(log_file_path, "a", encoding="utf-8", errors="ignore"),
            stderr=subprocess.STDOUT,
            text=True
        )
        running_processes[meeting_name] = p
        
        # Save temporary meta
        meta = {
            "meeting_name": meeting_name,
            "status": "processing",
            "pid": p.pid,
            "engine_used": req.engine,
            "creation_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "audio_path": audio_full_path,
            "speaker_references": {}
        }
        meta_path = os.path.join(meeting_output_dir, "meeting_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
            
        return {"message": "Transcription started", "meeting": meeting_name, "pid": p.pid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start: {str(e)}")

@app.post("/api/meetings/{name}/stop")
async def stop_transcription(name: str, current_user: str = Depends(get_current_user)):
    """Stop currently running transcription"""
    # 1. Kill process tracked in memory
    proc = running_processes.get(name)
    if proc:
        try:
            proc.terminate()
            time.sleep(0.5)
            proc.kill()
        except:
            pass
        running_processes.pop(name, None)
        
    # 2. Kill process based on saved PID
    m_dir = os.path.join(MEETINGS_DIR, name)
    meta_path = os.path.join(m_dir, "meeting_meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            pid = meta.get("pid")
            if pid and is_pid_running(pid):
                os.kill(pid, 9)
            meta["status"] = "failed"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except:
            pass
            
    return {"message": "Task stopped successfully"}

@app.get("/api/meetings/{name}/log")
async def get_meeting_log(name: str, current_user: str = Depends(get_current_user)):
    """Get latest transcription logs"""
    log_path = os.path.join(MEETINGS_DIR, name, "run_transcription.log")
    if not os.path.exists(log_path):
        return {"log": "No log file found yet."}
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return {"log": content}
    except Exception as e:
        return {"log": f"Failed to read logs: {str(e)}"}

def bg_generate_summary(m_dir: str, meeting_name: str):
    """Background task to generate summary using configured LLM"""
    summary_path = os.path.join(m_dir, "summary.md")
    try:
        full_text = ""
        # Write intermediate status
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("# 正在生成 AI 纪要中，请稍候...")
            
        for chunk in generate_summary_document_stream(m_dir):
            full_text += chunk
            
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(full_text)
            
        # Update meeting meta
        meta_path = os.path.join(m_dir, "meeting_meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
            meta["status"] = "completed"
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(meta, mf, ensure_ascii=False, indent=2)
    except Exception as e:
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(f"# AI 纪要生成失败\n\n错误信息: {str(e)}")

@app.post("/api/meetings/{name}/summarize")
async def generate_summary(name: str, background_tasks: BackgroundTasks, current_user: str = Depends(get_current_user)):
    """Trigger LLM summary generation in background"""
    m_dir = os.path.join(MEETINGS_DIR, name)
    if not os.path.exists(m_dir):
        raise HTTPException(status_code=404, detail="Meeting folder not found")
        
    background_tasks.add_task(bg_generate_summary, m_dir, name)
    return {"message": "AI Summary generation started in background"}

@app.post("/api/meetings/{name}/remap-speaker")
async def remap_speaker(name: str, req: RemapSpeakerRequest, current_user: str = Depends(get_current_user)):
    """Remap a speaker inside a specific meeting"""
    from voiceprint_system.app import update_meeting_speaker_name
    success, msg = update_meeting_speaker_name(name, req.speaker_label, req.new_name)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}

# ----------------------------------------------------------------------
# Speaker Management Endpoints
# ----------------------------------------------------------------------
@app.get("/api/speakers/named")
async def list_named_speakers(current_user: str = Depends(get_current_user)):
    """List all registered speakers in the database"""
    speakers = []
    if os.path.exists(DB_DIR):
        for name in os.listdir(DB_DIR):
            p_dir = os.path.join(DB_DIR, name)
            if os.path.isdir(p_dir) and not name.startswith('.') and name != "_unknown":
                npy_files = [f for f in os.listdir(p_dir) if f.endswith('.npy')]
                templates = []
                for f in npy_files:
                    base = os.path.splitext(f)[0]
                    wav_name = base + ".wav"
                    has_wav = os.path.exists(os.path.join(p_dir, wav_name))
                    templates.append({
                        "npy": f,
                        "wav": wav_name if has_wav else None
                    })
                speakers.append({
                    "name": name,
                    "template_count": len(npy_files),
                    "templates": templates
                })
    return sorted(speakers, key=lambda x: x["name"])

@app.get("/api/speakers/unknown")
async def list_unknown_speakers(current_user: str = Depends(get_current_user)):
    """List the pool of global unknown speakers"""
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
                    
                    # Scan meetings for references
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
                        "has_wav": os.path.exists(wav_path),
                        "meetings": referred_meetings,
                        "samples": sample_texts
                    })
    return sorted(unknowns, key=lambda x: x["id"])

@app.post("/api/speakers/unknown/{unknown_id}/promote")
async def promote_unknown_speaker(unknown_id: str, req: RenameSpeakerRequest, current_user: str = Depends(get_current_user)):
    """Promote an unknown speaker voiceprint to named speaker"""
    from voiceprint_system.app import promote_unknown_logic
    success, msg = promote_unknown_logic(unknown_id, req.new_name)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}

@app.delete("/api/speakers/unknown/{unknown_id}")
async def discard_unknown_speaker(unknown_id: str, current_user: str = Depends(get_current_user)):
    """Discard an unknown speaker voiceprint"""
    uid_dir = os.path.join(UNKNOWN_DIR, unknown_id)
    if not os.path.exists(uid_dir):
        raise HTTPException(status_code=404, detail="Unknown speaker ID not found")
    try:
        shutil.rmtree(uid_dir)
        return {"message": f"Successfully discarded unknown speaker {unknown_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {str(e)}")

@app.post("/api/speakers/named/{speaker_name}/rename")
async def rename_speaker_globally(speaker_name: str, req: RenameSpeakerRequest, current_user: str = Depends(get_current_user)):
    """Rename a speaker folder globally and retroactively update references"""
    src_dir = os.path.join(DB_DIR, speaker_name)
    if not os.path.exists(src_dir):
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    dst_name = req.new_name.strip()
    if not dst_name or dst_name == "_unknown":
        raise HTTPException(status_code=400, detail="Invalid target name")
        
    dst_dir = os.path.join(DB_DIR, dst_name)
    if os.path.exists(dst_dir):
        # Merge folders if destination exists
        try:
            for item in os.listdir(src_dir):
                shutil.move(os.path.join(src_dir, item), os.path.join(dst_dir, item))
            shutil.rmtree(src_dir)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Merge failed: {str(e)}")
    else:
        try:
            os.rename(src_dir, dst_dir)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Rename failed: {str(e)}")
            
    # Retroactively update all meeting transcript references from speaker_name -> dst_name
    # Iterate through all meetings
    updated_count = 0
    if os.path.exists(MEETINGS_DIR):
        for meeting_name in os.listdir(MEETINGS_DIR):
            m_dir = os.path.join(MEETINGS_DIR, meeting_name)
            if not os.path.isdir(m_dir) or meeting_name.startswith('.'):
                continue
            meta_path = os.path.join(m_dir, "meeting_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    
                    speaker_refs = meta.get("speaker_references", {})
                    need_update = False
                    
                    for spk_label, ref_info in speaker_refs.items():
                        if ref_info.get("voiceprint_id") == speaker_name:
                            ref_info["voiceprint_id"] = dst_name
                            need_update = True
                            
                    if need_update:
                        with open(meta_path, "w", encoding="utf-8") as f:
                            json.dump(meta, f, ensure_ascii=False, indent=2)
                            
                        # Update transcript json
                        json_path = os.path.join(m_dir, "transcript.json")
                        if os.path.exists(json_path):
                            with open(json_path, "r", encoding="utf-8") as f:
                                t_data = json.load(f)
                            for seg in t_data.get("segments", []):
                                if seg.get("speaker_name") == speaker_name:
                                    seg["speaker_name"] = dst_name
                                if "words" in seg:
                                    for w in seg["words"]:
                                        if w.get("speaker_name") == speaker_name:
                                            w["speaker_name"] = dst_name
                            with open(json_path, "w", encoding="utf-8") as f:
                                json.dump(t_data, f, ensure_ascii=False, indent=2)
                                
                        # Regenerate md
                        regenerate_transcript_md(m_dir, meeting_name)
                        
                        # Update summary.md
                        sum_path = os.path.join(m_dir, "summary.md")
                        if os.path.exists(sum_path):
                            with open(sum_path, "r", encoding="utf-8") as f:
                                content = f.read()
                            content = content.replace(speaker_name, dst_name)
                            with open(sum_path, "w", encoding="utf-8") as f:
                                f.write(content)
                        updated_count += 1
                except:
                    pass
                    
    return {"message": f"Successfully renamed speaker to {dst_name} and updated {updated_count} historical meetings"}

@app.delete("/api/speakers/named/{speaker_name}/template/{template_name}")
async def delete_speaker_template_file(speaker_name: str, template_name: str, current_user: str = Depends(get_current_user)):
    """Delete a template file (.npy and its .wav) from a named speaker"""
    from voiceprint_system.app import delete_speaker_template
    # Checks if it exists
    p_dir = os.path.join(DB_DIR, speaker_name)
    npy_path = os.path.join(p_dir, template_name)
    if not os.path.exists(npy_path):
        raise HTTPException(status_code=404, detail="Template not found")
        
    try:
        delete_speaker_template(speaker_name, template_name)
        return {"message": "Template deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {str(e)}")

@app.post("/api/speakers/named/{speaker_name}/template/{template_name}/move")
async def move_speaker_template_file(speaker_name: str, template_name: str, req: MoveTemplateRequest, current_user: str = Depends(get_current_user)):
    """Move a template file from one speaker to another"""
    from voiceprint_system.app import move_speaker_template
    p_dir = os.path.join(DB_DIR, speaker_name)
    npy_path = os.path.join(p_dir, template_name)
    if not os.path.exists(npy_path):
        raise HTTPException(status_code=404, detail="Template not found")
        
    target_name = req.target_speaker.strip()
    if not target_name or target_name == "_unknown":
        raise HTTPException(status_code=400, detail="Invalid target speaker name")
        
    try:
        move_speaker_template(speaker_name, template_name, target_name)
        return {"message": f"Template moved successfully to {target_name}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to move: {str(e)}")

@app.get("/api/speakers/audio/unknown/{unknown_id}")
async def serve_unknown_audio(unknown_id: str, current_user: str = Depends(get_current_user)):
    """Serve the WAV snippet file for an unknown speaker"""
    wav_path = os.path.join(UNKNOWN_DIR, unknown_id, "feature.wav")
    if not os.path.exists(wav_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(wav_path, media_type="audio/wav")

@app.get("/api/speakers/audio/named/{speaker_name}/{filename}")
async def serve_named_audio(speaker_name: str, filename: str, current_user: str = Depends(get_current_user)):
    """Serve the WAV snippet file for a named speaker"""
    # Safe check to prevent path traversal
    if ".." in speaker_name or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid file path")
        
    wav_path = os.path.join(DB_DIR, speaker_name, filename)
    if not os.path.exists(wav_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(wav_path, media_type="audio/wav")

# ----------------------------------------------------------------------
# System Configuration Endpoints
# ----------------------------------------------------------------------
@app.get("/api/config")
async def get_system_config(current_user: str = Depends(get_current_user)):
    """Get active LLM summary configuration"""
    cfg = load_server_config()
    # Mask API keys in response
    resp = cfg.copy()
    if resp.get("deepseek_api_key"):
        resp["deepseek_api_key"] = "••••••••••••••••"
    if resp.get("google_api_key"):
        resp["google_api_key"] = "••••••••••••••••"
    # Remove password hash
    resp.pop("admin_password_hash", None)
    resp.pop("jwt_secret_key", None)
    return resp

@app.post("/api/config")
async def update_system_config(req: ConfigUpdateRequest, current_user: str = Depends(get_current_user)):
    """Update system LLM configuration"""
    cfg = load_config()
    
    cfg["provider"] = req.provider
    if req.ollama_host is not None:
        cfg["ollama_host"] = req.ollama_host
    if req.ollama_model is not None:
        cfg["ollama_model"] = req.ollama_model
        
    # Only update API keys if they are not masked value
    if req.deepseek_api_key is not None and req.deepseek_api_key != "••••••••••••••••":
        cfg["deepseek_api_key"] = req.deepseek_api_key
    if req.google_api_key is not None and req.google_api_key != "••••••••••••••••":
        cfg["google_api_key"] = req.google_api_key
        
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return {"message": "Config updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {str(e)}")

# ----------------------------------------------------------------------
# Mount Static Files & SPA Route
# ----------------------------------------------------------------------
# Ensure static directory exists
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# Mount static folder
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def read_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_file):
        # Create a fallback index.html if it does not exist yet (will be created in next steps)
        with open(index_file, "w", encoding="utf-8") as f:
            f.write("<h1>Loading WhisperX Voiceprint System...</h1>")
    return FileResponse(index_file)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("voiceprint_system.server:app", host="0.0.0.0", port=8000, reload=True)
