import os
import subprocess
import shutil

def normalize_and_denoise(input_path: str, output_path: str) -> str:
    """
    使用 FFmpeg 对音频进行预处理：
    1. loudnorm: EBU R128 标准响度归一化，平衡远近场音量（解决麦克风远近音量不一问题）。
    2. highpass/lowpass: 限制带通为 80Hz - 8000Hz，滤除人声带外的低频机器噪音与高频尖锐噪音。
    3. 重采样为 16000Hz, 单声道, pcm_s16le WAV 规范格式。
    """
    # 优先查找 PATH 中的 ffmpeg，如果找不到则使用 Mac Homebrew 的默认路径
    ffmpeg_cmd = shutil.which("ffmpeg")
    if not ffmpeg_cmd:
        if os.path.exists("/opt/homebrew/bin/ffmpeg"):
            ffmpeg_cmd = "/opt/homebrew/bin/ffmpeg"
        else:
            ffmpeg_cmd = "ffmpeg"

    cmd = [
        ffmpeg_cmd, "-y", "-i", input_path,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,highpass=f=80,lowpass=f=8000",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        output_path
    ]
    
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"[Warning] FFmpeg 预处理失败: {e}。将尝试进行直接重采样格式化。")
        # 降级方案：仅进行基础重采样
        fallback_cmd = [
            ffmpeg_cmd, "-y", "-i", input_path,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            output_path
        ]
        try:
            subprocess.run(fallback_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return output_path
        except Exception as e_fallback:
            print(f"[Error] 格式化重采样也失败: {e_fallback}")
            # 如果彻底失败，拷贝原音频返回
            shutil.copyfile(input_path, output_path)
            return output_path
