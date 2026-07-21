# OBS Whisper - 경기도의회 경제노동위원회 실시간 자막 송출

경기도의회 라이브 방송(**경제노동위원회**) 스트림을 직접 받아 음성을 자동으로
텍스트로 변환하고, **문장 단위로 자연스럽게 끊어** 텔레그램으로 송출하는 앱입니다.

> 이전에는 스피커 출력을 BlackHole로 캡처했지만, 이제 `live.ggc.go.kr`의
> HLS 스트림(`ch6`)을 ffmpeg로 **직접** 받아오므로 브라우저·가상 오디오 장치가
> 필요 없습니다.

## 기능

- 📡 경제노동위원회 라이브 스트림 직접 수신 (ffmpeg, HLS)
- 🔁 방송이 꺼지거나 끊기면 자동 재접속 (방송 시작 시 자동 수신)
- 🗣️ **faster-whisper `large-v3`** 로컬 음성 인식 (한국어, 고정밀)
- ✂️ **Silero VAD**로 말이 끊기는 지점을 감지해 발화 단위로 분절
- 📝 완성된 **문장 단위**로만 텔레그램 전송 (문장이 중간에 잘리지 않음)
- 📱 텔레그램 봇 브로드캐스트 — **방송 중 새로 등록한 사용자도 이어서 수신**
- 🔤 키워드 자동 강조 (볼드체)
- 🎨 Rich 기반 실시간 터미널 UI

## 설치

1. Python 3.9+ 와 **ffmpeg** 필요:
```bash
brew install ffmpeg
```

2. 의존성 설치:
```bash
pip install -r requirements.txt
```
> 최초 실행 시 `large-v3` 모델(약 1.3GB, int8)이 자동 다운로드됩니다.

3. 환경 변수 (`.env`):
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=0

# 음성 인식 (faster-whisper)
STT_MODEL=large-v3          # tiny/base/small/medium/large-v3/large-v3-turbo
STT_DEVICE=auto             # auto/cpu/cuda
STT_COMPUTE_TYPE=int8       # int8(빠름)/float16/float32(정확)
STT_BEAM_SIZE=5

# VAD (문장 분절)
VAD_SILENCE_MS=700          # 이만큼 조용하면 발화 종료로 판단
VAD_MAX_UTTERANCE_S=15      # 한 발화 최대 길이(안전장치)

# 문장 버퍼
SENTENCE_FLUSH_TIMEOUT=6    # 종결부호 없이 이 시간 지나면 남은 조각 송출
```

## 실행 및 중단

### 실행

```bash
# (최초 1회) 가상환경 활성화
source venv/bin/activate

# 앱 실행 (포그라운드)
python main.py
```

경제노동위원회 방송이 켜지면 자동으로 수신·전사·송출을 시작하고, 방송이
꺼지면 대기하다가 재개되면 다시 이어받습니다.

### 중단

- **포그라운드로 실행 중일 때**: 터미널에서 **`Ctrl + C`** → 종료 안내 메시지를
  전송하고 안전하게 정리 후 종료합니다.

### 백그라운드 실행 / 중단

```bash
# 백그라운드로 실행 (로그는 app.log 에 기록)
nohup python main.py > app.log 2>&1 &
echo $!               # 출력된 PID를 기억해 두세요

# 실시간 로그 보기
tail -f app.log

# 중단 (위에서 기억한 PID 사용)
kill <PID>

# PID를 잊었다면 프로세스로 찾아서 종료
pkill -f "python main.py"
```

> `kill`(기본 SIGTERM)/`Ctrl+C`(SIGINT)는 모두 정상 종료 처리되어 종료 안내를
> 전송합니다. 강제 종료가 필요할 때만 `kill -9 <PID>` 를 사용하세요(정리 없이 즉시 종료).

## 대상 채널 (하드코딩)

`config.py`에 경제노동위원회가 고정되어 있습니다:
```
STREAM_URL = https://stream02.cdn.gov-ntruss.com/live/ch6/playlist.m3u8
```
(경기도의회 플레이어 로직 `"https://"+ip+"/"+ch+"/playlist.m3u8"` 에서
경제노동위원회의 `ch6`, `stream02` 값을 사용)

## 유틸리티 스크립트

```bash
python3 update_users.py    # 봇에 메시지 보낸 사용자 자동 등록
python3 get_chat_id.py     # 채팅 ID 확인
```

## 아키텍처

```
경제노동위 HLS(ch6)
  → StreamRecorder (ffmpeg → 16kHz 모노 PCM, 자동 재접속)
  → VADSegmenter   (Silero VAD, 정적 기준 발화 분절)   ┐ 백그라운드 스레드
  → [발화 큐]                                           ┘
  → Transcriber    (faster-whisper large-v3, 한국어)
  → SentenceBuffer (완성 문장만 추출, 미완성 꼬리는 다음 발화와 연결)
  → TextFormatter  (키워드 볼드)
  → TelegramSender (매 문장 최신 사용자 목록으로 브로드캐스트)
```

## 주요 라이브러리

- **faster-whisper** (CTranslate2): 로컬 고속 음성 인식
- **silero-vad**: 발화 구간 감지
- **ffmpeg**: HLS 스트림 오디오 추출
- **python-telegram-bot**, **rich**, **numpy**, **python-dotenv**

자세한 개발 가이드는 [DEVELOPMENT.md](DEVELOPMENT.md)를 참고하세요.
