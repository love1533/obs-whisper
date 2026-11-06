"""설정 파일"""
import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================================================
# 텔레그램 설정
# ==========================================================================
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "0") == "1"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ==========================================================================
# 방송 스트림 설정 (경기도의회 - 경제노동위원회 전용 하드코딩)
#   live.ggc.go.kr 플레이어 로직: streamUrl = "https://"+ip+"/"+ch+"/playlist.m3u8"
#   경제노동위원회: data-ch="ch6", data-ip="stream02.cdn.gov-ntruss.com/live"
# ==========================================================================
COMMITTEE_NAME = os.getenv("COMMITTEE_NAME", "경제노동위원회")
# 위원 명단 (이름:직책, 쉼표 구분). 발언자 태깅과 요청자료 위원 분류의 기준.
# 명단에 없는 이름은 요청자료에서 "확인필요"로 분류된다.
_MEMBERS_DEFAULT = "장태환,최민,장윤정:부위원장,박준모,송규근,송연섭,신명순,오현정,유수연,이기대,최보라,최상규,최혜경,한영수,정용한"
COMMITTEE_MEMBERS = [
    (part.split(":", 1)[0].strip(), (part.split(":", 1)[1].strip() if ":" in part else "위원"))
    for part in os.getenv("COMMITTEE_MEMBERS", _MEMBERS_DEFAULT).split(",") if part.strip()
]
STREAM_CH = "ch6"
STREAM_HOST = "stream02.cdn.gov-ntruss.com/live"
STREAM_URL = os.getenv("STREAM_URL", f"https://{STREAM_HOST}/{STREAM_CH}/playlist.m3u8")
STREAM_REFERER = "https://live.ggc.go.kr/"
# 방송이 꺼져있거나 스트림이 끊겼을 때 재접속 대기 시간(초)
STREAM_RETRY_SECONDS = int(os.getenv("STREAM_RETRY_SECONDS", "10"))
# HLS 시작 세그먼트 위치: -1=라이브 엣지(최저지연), -2=한 세그먼트 여유(더 안정)
HLS_LIVE_START_INDEX = int(os.getenv("HLS_LIVE_START_INDEX", "-1"))

# 오디오 설정 (스트림은 16kHz 모노로 고정 추출)
SAMPLE_RATE = 16000
AUDIO_RATE = SAMPLE_RATE  # 하위 호환

# ==========================================================================
# 음성 인식(STT) 설정
#   backend=mlx  : MLX Whisper (Apple Silicon GPU/Metal 가속, 기본·권장)
#   backend=faster-whisper : CTranslate2 (CPU 전용, 폴백용)
# ==========================================================================
#   backend=clova : CLOVA Speech Live (네이버 클라우드 gRPC 스트리밍 STT)
STT_BACKEND = os.getenv("STT_BACKEND", "clova")
STT_MLX_REPO = os.getenv("STT_MLX_REPO", "mlx-community/whisper-large-v3-mlx")
STT_MODEL = os.getenv("STT_MODEL", "large-v3")           # faster-whisper용
STT_DEVICE = os.getenv("STT_DEVICE", "auto")             # faster-whisper용: auto/cpu/cuda
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")  # faster-whisper용
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ko")
STT_BEAM_SIZE = int(os.getenv("STT_BEAM_SIZE", "5"))     # faster-whisper용

# CLOVA Speech Live (gRPC)
CLOVA_SPEECH_URL = os.getenv("CLOVA_SPEECH_URL", "clovaspeech-gw.ncloud.com:50051")
CLOVA_SPEECH_SECRET = os.getenv("CLOVA_SPEECH_SECRET", "")
# Clova Live는 발언이 끊기지 않으면(무음 없음) 문장을 확정하지 않아 긴 보고가 수 분간
# 전송되지 않는다. 그래서 시간 기준으로도 끊는다.
#   CLOVA_SOFT_CUT_S : 이 시간이 지나고 문장 끝("~습니다." 등)이 보이면 거기서 끊어 전송
#   CLOVA_MAX_SENTENCE_S : 이 시간이 지나면 문장 끝이 없어도 무조건 끊어 전송 (최대 길이)
CLOVA_SOFT_CUT_S = float(os.getenv("CLOVA_SOFT_CUT_S", "40"))
CLOVA_MAX_SENTENCE_S = float(os.getenv("CLOVA_MAX_SENTENCE_S", "60"))

# ==========================================================================
# VAD(음성 구간 감지) 설정 - 문장이 끊기는 지점을 정적으로 감지
# ==========================================================================
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))        # 0~1, 높을수록 엄격
VAD_MIN_SPEECH_MS = int(os.getenv("VAD_MIN_SPEECH_MS", "250"))  # 이보다 짧은 소리는 발화로 안 침
VAD_SILENCE_MS = int(os.getenv("VAD_SILENCE_MS", "500"))        # 이만큼 조용하면 발화 종료로 판단
VAD_MAX_UTTERANCE_S = float(os.getenv("VAD_MAX_UTTERANCE_S", "6"))  # 한 발화 최대 길이(지연·정확도 균형)

# ==========================================================================
# 문장 버퍼 설정 - 문장 단위로 자연스럽게 끊어 송출
# ==========================================================================
# 종결 부호 없이 이 시간만큼 새 발화가 없으면 버퍼에 남은 조각을 그대로 송출(초)
SENTENCE_FLUSH_TIMEOUT = float(os.getenv("SENTENCE_FLUSH_TIMEOUT", "3"))

# ==========================================================================
# 웹 뷰어 - 방송 영상 + 싱크 맞춘 발언 패널 (http://localhost:WEB_PORT)
# ==========================================================================
WEB_ENABLED = os.getenv("WEB_ENABLED", "1") == "1"
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")    # 0.0.0.0이면 같은 네트워크의 다른 기기도 접속
WEB_PORT = int(os.getenv("WEB_PORT", "8765"))
# 영상을 라이브보다 이만큼 늦게 재생해 문장이 영상보다 먼저 도착하게 한다(초).
# 발화 길이(최대 VAD_MAX_UTTERANCE_S) + 정적 대기 + 인식 + 문장 버퍼 시간보다 커야 한다.
VIEWER_DELAY_S = float(os.getenv("VIEWER_DELAY_S", "15"))
# 영상 재생 방식
#   auto   : 시청자가 도의회 원본을 직접 재생, 막히면(CORS 등) 앱 중계로 자동 전환 (기본)
#   direct : 원본 직접 재생만 (앱은 영상 중계를 하지 않음)
#   relay  : 항상 앱이 중계한 영상으로 재생
VIDEO_MODE = os.getenv("VIDEO_MODE", "auto")
# 시청자가 직접 재생할 원본 주소 (기본: STT가 받는 주소와 동일)
VIEWER_STREAM_URL = os.getenv("VIEWER_STREAM_URL", STREAM_URL)
# 뷰어 페이지를 다른 곳(정적 호스팅)에 올렸을 때 앱 중계 HLS에 접근할 주소 (예: https://내도메인)
RELAY_BASE_URL = os.getenv("RELAY_BASE_URL", "")
# 중계 영상 보관 길이(초) - 뷰어에서 되감기 가능한 범위
HLS_WINDOW_S = int(os.getenv("HLS_WINDOW_S", "3600"))
HLS_DIR = os.getenv("HLS_DIR", "web_live")
# 인식된 문장 저장 폴더 (날짜별 .jsonl + .txt)
TRANSCRIPT_DIR = os.getenv("TRANSCRIPT_DIR", "transcripts")

# ==========================================================================
# Supabase (선택) - 문장을 transcripts 테이블에 저장하고 Realtime으로 시청자에게 전달
#   테이블 생성: supabase/schema.sql
# ==========================================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")   # 앱 전용 쓰기 키 (절대 공개 금지)
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")         # 뷰어용 읽기 키 (공개 가능)
SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)

# ==========================================================================
# AI 요약 (선택) - 5분 단위 발언 분석 + 요청자료 추출 + 지적/답변 추적
# ==========================================================================
SUMMARY_ENABLED = os.getenv("SUMMARY_ENABLED", "0") == "1"
SUMMARY_PROVIDER = os.getenv("SUMMARY_PROVIDER", "anthropic")       # "anthropic" 또는 "openai"
SUMMARY_API_KEY = os.getenv("SUMMARY_API_KEY", "")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "claude-sonnet-4-20250514")
SUMMARY_INTERVAL_S = int(os.getenv("SUMMARY_INTERVAL_S", "300"))    # 5분 단위 분석
SUMMARY_FULL_INTERVAL_S = int(os.getenv("SUMMARY_FULL_INTERVAL_S", "900"))  # 15분 전체 요약

# ==========================================================================
# 검증
# ==========================================================================
if TELEGRAM_ENABLED and not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
# TELEGRAM_CHAT_ID는 브로드캐스트 모드에서는 선택사항 (telegram_users.json에서 사용자 목록 로드)
