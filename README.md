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

## 웹 뷰어 - 방송 영상 + 싱크 맞춘 발언 패널

앱을 실행하면 텔레그램 송출과 함께 웹 뷰어가 뜹니다: **http://127.0.0.1:8765**

- 왼쪽: **도의회 원본 영상을 시청자 브라우저가 직접 재생** (우리 쪽은 영상을 보내지 않음)
- 영상 하단: 지금 발언 중인 문장을 뉴스 자막처럼 표시 (켜기/끄기 가능)
- 오른쪽: 발언 내용이 시각별 문단으로 정리되어 쌓임 · 키워드 강조 ·
  문장을 클릭하면 그 발언 시점으로 영상 이동

### 싱크 원리 (원본 스트림 PTS 기준)

```
도의회 CDN (HLS) ───────────────────────────────→ 시청자 브라우저 (hls.js로 원본 직접 재생)
      │                                                  ↑ 현재 재생 PTS = currentTime + initPTS
      └→ [Mac] ffmpeg -copyts → 16kHz PCM → VAD → Whisper → 문장 + 원본 PTS
                                                    └→ WebSocket / Supabase Realtime ─┘
```

ffmpeg를 `-copyts`로 돌려 첫 음성 샘플의 **원본 스트림 PTS**를 읽고, 문장마다
`PTS = 첫 PTS + 발언 위치`를 붙입니다. 브라우저의 hls.js도 같은 원본 PTS를 알 수 있어서,
영상이 그 PTS를 재생하는 순간 문장을 띄웁니다. 영상은 인식 시간만큼(`VIEWER_DELAY_S`,
기본 15초) 라이브보다 늦게 재생되어 문장이 항상 먼저 도착합니다.

### 영상 재생 방식 (`VIDEO_MODE`)

| 값 | 동작 |
|---|---|
| `auto` (기본) | 원본 직접 재생. 원본이 막히면(CORS·Referer 차단 등) 앱 중계 영상으로 자동 전환 |
| `direct` | 원본 직접 재생만. 앱은 영상 중계를 하지 않음 |
| `relay` | 항상 앱이 중계한 영상(`-c copy`, 재인코딩 없음)으로 재생 |

중계 영상도 원본과 같은 PTS를 유지하므로(`-copyts -muxdelay 0`) 어느 쪽으로 재생해도 싱크가 맞습니다.

### 설정 (`.env`)

```env
WEB_ENABLED=1
WEB_HOST=127.0.0.1     # 0.0.0.0 이면 같은 네트워크의 다른 기기에서도 접속
WEB_PORT=8765
VIDEO_MODE=auto        # auto | direct | relay
VIEWER_DELAY_S=15      # 자막이 영상보다 늦게 뜨면 늘리기
HLS_WINDOW_S=3600      # 중계 영상 되감기 가능한 범위(초)
TRANSCRIPT_DIR=transcripts
```

## Supabase - 문장 저장 + 실시간 전달 (선택)

설정하면 인식된 문장이 Supabase `transcripts` 테이블에 저장되고, Realtime으로
시청자에게 전달됩니다. 뷰어 페이지를 아무 정적 호스팅에나 올려 외부에 공개할 수 있고,
Mac은 STT와 텍스트 전송만 하면 됩니다 (영상은 도의회 CDN에서 직접).

1. Supabase 프로젝트를 만들고 **SQL Editor**에서 [`supabase/schema.sql`](supabase/schema.sql) 실행
   - `broadcast_sessions`(방송 연결 상태), `transcripts`(문장) 테이블 생성
   - 누구나 읽기만 가능(RLS), 쓰기는 서비스 키만 / Realtime 등록
2. 앱 `.env`에 추가 (Settings → API):
   ```env
   SUPABASE_URL=https://YOUR-PROJECT.supabase.co
   SUPABASE_SERVICE_KEY=...   # service_role 또는 secret 키 - 절대 공개 금지
   ```
3. 뷰어 공개: `web/` 폴더(`index.html`, `app.js`, `style.css`)를 정적 호스팅에 올리고,
   `web/config.example.js`를 `config.js`로 복사해 `supabaseUrl`, `supabaseAnonKey`(anon/publishable 키) 입력.
   config.js에 Supabase가 설정되어 있으면 뷰어는 WebSocket 대신 Supabase Realtime으로 문장을 받습니다.

> 원본이 막혀 중계 영상이 필요하면 `relayBase`에 Mac의 외부 주소를 넣어야 합니다
> (리버스 프록시/HTTPS 필요). 원본 직접 재생이 되면 Mac을 외부에 노출할 필요가 없습니다.

요금 참고: Free 플랜(동시 접속 200, 월 메시지 200만)으로 시작할 수 있고, 1주일 비활동 시
프로젝트가 일시정지되므로 상시 운영은 Pro($25/월~) 권장.

## 텍스트 저장

인식된 문장은 `transcripts/` 폴더에 날짜별로 저장됩니다 (Supabase를 쓰더라도 로컬 백업으로 항상 저장).

- `transcripts/2026-10-29.jsonl` — 한 줄에 한 문장 (발언 시각, 원본 PTS, 세션 내 위치, 원문, 강조 HTML).
  뷰어를 새로고침하거나 앱을 재시작해도 오늘 기록을 여기서 다시 불러옵니다.
- `transcripts/2026-10-29.txt` — `[14:03:21] 문장` 형식의 읽기용 회의록

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
- **aiohttp**: 웹 뷰어 서버 (WebSocket + 중계 HLS), 브라우저는 **hls.js**
- **httpx**: Supabase REST 저장 (python-telegram-bot 의존성으로 함께 설치됨), 브라우저는 **supabase-js**

자세한 개발 가이드는 [DEVELOPMENT.md](DEVELOPMENT.md)를 참고하세요.
