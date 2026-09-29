#!/usr/bin/env python3
"""
transcribe.py — 影片/音訊逐字稿工具（支援 URL 或本機檔案）

============================================================
安裝步驟
============================================================

1) 系統需要安裝 ffmpeg（yt-dlp 下載、faster-whisper 讀取音訊都需要）：
   - Ubuntu/WSL: sudo apt update && sudo apt install -y ffmpeg
   - macOS:      brew install ffmpeg
   - Windows:    choco install ffmpeg   (或到 https://ffmpeg.org 下載並加入 PATH)

2) Python 套件（建議 Python 3.9+，建議在 venv 裡安裝）：
   pip install yt-dlp faster-whisper

   - 有 NVIDIA GPU 且裝好 CUDA/cuDNN 的話，faster-whisper 會自動用 GPU 加速
     （沒有的話會自動 fallback 用 CPU，只是比較慢，準確率不受影響）。

============================================================
使用方式
============================================================

# 本機影片/音訊檔
python transcribe.py /path/to/video.mp4

# YouTube 或其他 yt-dlp 支援的網址
python transcribe.py "https://www.youtube.com/watch?v=xxxxxxxx"

# 指定輸出檔名前綴（預設用來源檔名）
python transcribe.py video.mp4 -o my_output

# 指定模型大小（預設 large-v3，準確率最高但最慢；想快一點可用 medium/small）
python transcribe.py video.mp4 --model medium

# 已知語言可以指定（跳過自動偵測，較快也較準）：
python transcribe.py video.mp4 --language zh
python transcribe.py video.mp4 --language en

輸出：
  <輸出前綴>.txt   純文字逐字稿
  <輸出前綴>.srt   帶時間戳記的字幕檔（可用來對照影片畫面）

============================================================
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def is_url(s: str) -> bool:
    return re.match(r"^https?://", s, re.IGNORECASE) is not None


def download_audio(url: str, workdir: str) -> str:
    """用 yt-dlp 把網址的最佳音軌下載成 wav，回傳本機檔案路徑。"""
    try:
        import yt_dlp
    except ImportError:
        sys.exit("缺少套件 yt-dlp，請先執行: pip install yt-dlp")

    out_template = os.path.join(workdir, "audio.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "0",
            }
        ],
        "quiet": False,
        "noprogress": False,
    }

    print(f"[1/2] 下載音訊: {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    audio_path = os.path.join(workdir, "audio.wav")
    if not os.path.exists(audio_path):
        sys.exit("下載後找不到音訊檔，請確認網址是否可存取、yt-dlp 是否為最新版本。")
    return audio_path


def format_timestamp(seconds: float) -> str:
    """轉成 SRT 用的 00:00:00,000 格式。"""
    millis = int(round(seconds * 1000))
    hh, millis = divmod(millis, 3_600_000)
    mm, millis = divmod(millis, 60_000)
    ss, millis = divmod(millis, 1_000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{millis:03d}"


def transcribe(audio_path: str, model_size: str, language: str | None):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("缺少套件 faster-whisper，請先執行: pip install faster-whisper")

    print(f"[2/2] 載入模型 ({model_size}) 並開始辨識，第一次執行會先下載模型檔，請耐心等候…")

    # 有 GPU 就用 GPU（float16 較快），沒有就用 CPU（int8 較省資源、準確率不受影響）
    try:
        model = WhisperModel(model_size, device="cuda", compute_type="float16")
        print("      使用 GPU (CUDA) 加速")
    except Exception:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        print("      使用 CPU（沒有偵測到可用的 GPU）")

    segments, info = model.transcribe(
        audio_path,
        language=language,          # None = 自動偵測語言
        vad_filter=True,            # 過濾靜音/雜音，減少幻聲字，提升準確率
        vad_parameters=dict(min_silence_duration_ms=500),
        beam_size=5,                # 提高辨識準確率（比預設更精細的搜尋）
        best_of=5,
    )

    print(f"      偵測語言: {info.language} (信心度 {info.language_probability:.2f})")

    results = []
    for seg in segments:
        print(f"      [{format_timestamp(seg.start)} -> {format_timestamp(seg.end)}] {seg.text.strip()}")
        results.append(seg)
    return results


def write_outputs(segments, out_prefix: str):
    txt_path = f"{out_prefix}.txt"
    srt_path = f"{out_prefix}.srt"

    with open(txt_path, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(seg.text.strip() + "\n")

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg.start)} --> {format_timestamp(seg.end)}\n")
            f.write(seg.text.strip() + "\n\n")

    print(f"\n完成！輸出：\n  {txt_path}\n  {srt_path}")


def main():
    parser = argparse.ArgumentParser(description="影片/音訊逐字稿工具（URL 或本機檔案皆可）")
    parser.add_argument("source", help="本機影片/音訊檔路徑，或 http(s) 網址（YouTube 等）")
    parser.add_argument("-o", "--output", help="輸出檔名前綴（預設依來源檔名自動產生）")
    parser.add_argument(
        "--model",
        default="large-v3",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        help="Whisper 模型大小，預設 large-v3（準確率最高但最慢）",
    )
    parser.add_argument("--language", default=None, help="指定語言代碼（如 zh, en），不指定則自動偵測")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmpdir:
        if is_url(args.source):
            audio_path = download_audio(args.source, tmpdir)
            default_prefix = "transcript"
        else:
            src = Path(args.source)
            if not src.exists():
                sys.exit(f"找不到檔案: {src}")
            audio_path = str(src)
            default_prefix = src.stem

        out_prefix = args.output or default_prefix
        segments = transcribe(audio_path, args.model, args.language)
        write_outputs(segments, out_prefix)


if __name__ == "__main__":
    main()
