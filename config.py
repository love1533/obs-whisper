"""설정 파일"""
import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================================================
# 텔레그램 설정
# ==========================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ==========================================================================
# 방송 스트림 설정 (경기도의회 - 경제노동위원회 전용 하드코딩)
#   live.ggc.go.kr 플레이어 로직: streamUrl = "https://"+ip+"/"+ch+"/playlist.m3u8"
#   경제노동위원회: data-ch="ch6", data-ip="stream02.cdn.gov-ntruss.com/live"
# ==========================================================================
COMMITTEE_NAME = "경제노동위원회"
STREAM_CH = "ch6"
STREAM_HOST = "stream02.cdn.gov-ntruss.com/live"
STREAM_URL = f"https://{STREAM_HOST}/{STREAM_CH}/playlist.m3u8"
STREAM_REFERER = "https://live.ggc.go.kr/"
# 방송이 꺼져있거나 스트림이 끊겼을 때 재접속 대기 시간(초)
STREAM_RETRY_SECONDS = int(os.getenv("STREAM_RETRY_SECONDS", "10"))

# 오디오 설정 (스트림은 16kHz 모노로 고정 추출)
SAMPLE_RATE = 16000
AUDIO_RATE = SAMPLE_RATE  # 하위 호환

# ==========================================================================
# 음성 인식(STT) 설정
#   backend=mlx  : MLX Whisper (Apple Silicon GPU/Metal 가속, 기본·권장)
#   backend=faster-whisper : CTranslate2 (CPU 전용, 폴백용)
# ==========================================================================
STT_BACKEND = os.getenv("STT_BACKEND", "mlx")
STT_MLX_REPO = os.getenv("STT_MLX_REPO", "mlx-community/whisper-large-v3-mlx")
STT_MODEL = os.getenv("STT_MODEL", "large-v3")           # faster-whisper용
STT_DEVICE = os.getenv("STT_DEVICE", "auto")             # faster-whisper용: auto/cpu/cuda
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")  # faster-whisper용
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ko")
STT_BEAM_SIZE = int(os.getenv("STT_BEAM_SIZE", "5"))     # faster-whisper용

# ==========================================================================
# VAD(음성 구간 감지) 설정 - 문장이 끊기는 지점을 정적으로 감지
# ==========================================================================
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))        # 0~1, 높을수록 엄격
VAD_MIN_SPEECH_MS = int(os.getenv("VAD_MIN_SPEECH_MS", "250"))  # 이보다 짧은 소리는 발화로 안 침
VAD_SILENCE_MS = int(os.getenv("VAD_SILENCE_MS", "700"))        # 이만큼 조용하면 발화 종료로 판단
VAD_MAX_UTTERANCE_S = float(os.getenv("VAD_MAX_UTTERANCE_S", "8"))  # 한 발화 최대 길이(지연·정확도 균형)

# ==========================================================================
# 문장 버퍼 설정 - 문장 단위로 자연스럽게 끊어 송출
# ==========================================================================
# 종결 부호 없이 이 시간만큼 새 발화가 없으면 버퍼에 남은 조각을 그대로 송출(초)
SENTENCE_FLUSH_TIMEOUT = float(os.getenv("SENTENCE_FLUSH_TIMEOUT", "6"))

# ==========================================================================
# 검증
# ==========================================================================
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
# TELEGRAM_CHAT_ID는 브로드캐스트 모드에서는 선택사항 (telegram_users.json에서 사용자 목록 로드)
