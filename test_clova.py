"""Clova Speech Live 연동 테스트

사용법:
  python test_clova.py                    # 라이브 스트림에서 30초 캡처 후 테스트
  python test_clova.py /path/to/file.mp4  # 로컬 파일로 테스트
"""
import sys
import subprocess
import numpy as np
from dotenv import load_dotenv
load_dotenv()

import config
from transcriber import Transcriber

DURATION = 30
source = sys.argv[1] if len(sys.argv) > 1 else config.STREAM_URL

print(f"1) 오디오 추출 중... ({DURATION}초)", file=sys.stderr)
print(f"   소스: {source}", file=sys.stderr)
cmd = [
    "ffmpeg", "-y",
    "-headers", f"Referer: {config.STREAM_REFERER}\r\n",
    "-i", source,
    "-t", str(DURATION),
    "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
    "-f", "wav", "pipe:1",
]
proc = subprocess.run(cmd, capture_output=True, timeout=120)
if proc.returncode != 0:
    print(f"ffmpeg 오류: {proc.stderr.decode()[-500:]}", file=sys.stderr)
    sys.exit(1)

# WAV 헤더(44바이트) 건너뛰고 PCM → float32
pcm = np.frombuffer(proc.stdout[44:], dtype=np.int16).astype(np.float32) / 32768.0
print(f"   오디오: {len(pcm)/16000:.1f}초, {len(pcm)} 샘플", file=sys.stderr)

print(f"2) Clova Speech Live 인식 중...", file=sys.stderr)
transcriber = Transcriber(
    backend="clova",
    clova_url=config.CLOVA_SPEECH_URL,
    clova_secret=config.CLOVA_SPEECH_SECRET,
    language="ko",
)

# 6초씩 잘라서 테스트 (실제 앱의 VAD 분절과 유사)
chunk_s = 6
sample_per_chunk = 16000 * chunk_s
for i in range(0, len(pcm), sample_per_chunk):
    chunk = pcm[i:i + sample_per_chunk]
    if len(chunk) < 8000:
        break
    start = i / 16000
    text = transcriber.transcribe(chunk)
    if text:
        print(f"   [{start:5.1f}s] {text}")
    else:
        print(f"   [{start:5.1f}s] (인식 없음)")

print("\n✅ 테스트 완료!", file=sys.stderr)
