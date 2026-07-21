# 개발 가이드 - OBS Whisper 프로젝트

이 문서는 OBS Whisper 프로젝트의 개발 방법과 사용된 라이브러리에 대한 가이드입니다.

## 📚 사용된 주요 라이브러리

### 1. **OpenAI Whisper** - 음성 인식
```python
import whisper
```
- **용도**: 실시간 음성을 텍스트로 변환
- **설치**: `pip install openai-whisper`
- **사용 예시**:
```python
model = whisper.load_model("base")
result = model.transcribe(audio_data, language="ko")
text = result["text"]
```
- **모델 크기**: tiny, base, small, medium, large (크기가 클수록 정확도 높음, 속도 느림)

### 2. **python-telegram-bot** - 텔레그램 봇
```python
from telegram import Bot
```
- **용도**: 텔레그램 봇 API를 통한 메시지 전송
- **설치**: `pip install python-telegram-bot>=20.7`
- **사용 예시**:
```python
bot = Bot(token="YOUR_BOT_TOKEN")
await bot.send_message(chat_id=chat_id, text="메시지", parse_mode="HTML")
```

### 3. **PyAudio** - 오디오 입력
```python
import pyaudio
```
- **용도**: 마이크/오디오 장치에서 실시간 오디오 입력
- **설치**: 
  - macOS: `brew install portaudio && pip install pyaudio`
  - Linux: `sudo apt-get install portaudio19-dev && pip install pyaudio`
- **사용 예시**:
```python
p = pyaudio.PyAudio()
stream = p.open(
    format=pyaudio.paInt16,
    channels=1,
    rate=16000,
    input=True,
    input_device_index=device_index
)
```

### 4. **Rich** - 터미널 UI
```python
from rich.console import Console
from rich.panel import Panel
from rich.live import Live
```
- **용도**: 아름다운 터미널 UI 및 실시간 상태 표시
- **설치**: `pip install rich>=13.7.0`
- **사용 예시**:
```python
from rich.live import Live
from rich.panel import Panel

with Live(render_function(), refresh_per_second=4) as live:
    live.update(new_content)
```

### 5. **NumPy** - 수치 연산
```python
import numpy as np
```
- **용도**: 오디오 데이터 처리 및 변환
- **설치**: `pip install numpy>=1.24.0`
- **사용 예시**:
```python
audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
audio_float = audio_array.astype(np.float32) / 32768.0
```

### 6. **python-dotenv** - 환경 변수 관리
```python
from dotenv import load_dotenv
```
- **용도**: `.env` 파일에서 환경 변수 로드
- **설치**: `pip install python-dotenv>=1.0.0`
- **사용 예시**:
```python
load_dotenv()
token = os.getenv("TELEGRAM_BOT_TOKEN")
```

## 🏗️ 프로젝트 구조

```
obs-whisper/
├── main.py                 # 메인 애플리케이션
├── audio_recorder.py       # 오디오 녹음 모듈
├── transcriber.py          # Whisper 음성 인식 모듈
├── telegram_sender.py      # 텔레그램 메시지 전송 모듈
├── user_manager.py         # 사용자 목록 관리 모듈
├── user_checker.py         # 주기적 사용자 확인 모듈
├── text_formatter.py       # 키워드 강조 포맷팅 모듈
├── ui.py                   # Rich 기반 터미널 UI
├── config.py               # 설정 관리
├── get_chat_id.py          # 채팅 ID 확인 유틸리티
├── update_users.py         # 사용자 목록 최신화 유틸리티
├── list_audio_devices.py   # 오디오 장치 목록 확인
├── requirements.txt        # 의존성 목록
├── .env                    # 환경 변수 (비공개)
└── telegram_users.json     # 등록된 사용자 목록
```

## 🔧 개발 방법

### 1. 환경 설정

```bash
# 가상 환경 생성 및 활성화
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# 또는
venv\Scripts\activate  # Windows

# 의존성 설치
pip install -r requirements.txt
```

### 2. 환경 변수 설정

`.env` 파일 생성:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=0
TRANSCRIPTION_INTERVAL=10
INPUT_DEVICE_INDEX=5
WHISPER_MODEL=base
```

### 3. 주요 모듈 설명

#### **AudioRecorder** (`audio_recorder.py`)
- 실시간 오디오 입력을 큐에 저장
- 스레드 기반 비동기 녹음
- 지정된 시간만큼 오디오 데이터 수집

```python
recorder = AudioRecorder(
    chunk_size=1024,
    sample_rate=16000,
    channels=1,
    input_device_index=5
)
recorder.start()
audio_data = recorder.get_audio_data(duration_seconds=10)
```

#### **Transcriber** (`transcriber.py`)
- Whisper 모델을 사용한 음성-텍스트 변환
- 한국어 전사 지원

```python
transcriber = Transcriber(model_size="base")
text = transcriber.transcribe(audio_data, sample_rate=16000)
```

#### **TelegramSender** (`telegram_sender.py`)
- 단일/다중 사용자에게 메시지 전송
- HTML 포맷 지원 (볼드체 등)
- 동기/비동기 방식 모두 지원

```python
sender = TelegramSender(bot_token=token, chat_ids=[id1, id2])
result = sender.broadcast_message_sync("메시지", [id1, id2])
```

#### **UserManager** (`user_manager.py`)
- JSON 파일 기반 사용자 목록 관리
- 사용자 추가/제거/조회

```python
manager = UserManager()
manager.add_user(chat_id)
users = manager.get_all_users()
```

#### **UserChecker** (`user_checker.py`)
- 주기적으로 텔레그램 봇 업데이트 확인
- 새 사용자 자동 등록
- 새 사용자 알림 브로드캐스트

```python
checker = UserChecker(check_interval_minutes=1)
await checker.run_periodic_check()
```

### 4. 실행 방법

```bash
# 메인 앱 실행
python3 main.py

# 사용자 목록 최신화
python3 update_users.py

# 오디오 장치 확인
python3 list_audio_devices.py

# 채팅 ID 확인
python3 get_chat_id.py
```

## 🎯 주요 기능 구현 방법

### 1. 실시간 오디오 수집 및 전사

```python
# 오디오 수집 (10초)
audio_data = recorder.get_audio_data(duration_seconds=10)

# 텍스트 변환
text = transcriber.transcribe(audio_data, sample_rate=16000)

# 전송
if text:
    sender.broadcast_message_sync(text, user_ids)
```

### 2. 키워드 강조

```python
formatter = TextFormatter()
formatted = formatter.format_text("경기신용보증재단의 예산")
# 결과: "<b>경기신용보증재단</b>의 <b>예산</b>"
```

### 3. 주기적 작업 (스레드)

```python
import threading
import asyncio

def run_checker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(checker.run_periodic_check())

thread = threading.Thread(target=run_checker, daemon=True)
thread.start()
```

### 4. Rich UI 업데이트

```python
from rich.live import Live

with Live(ui.render(), refresh_per_second=4) as live:
    while running:
        ui.update_status("새 상태")
        live.update(ui.render())
```

## 🔍 문제 해결

### 오디오 입력 문제
- macOS: 시스템 설정 > 보안 및 개인 정보 보호 > 마이크 권한 확인
- BlackHole 설치 및 OBS 오디오 라우팅 확인
- `list_audio_devices.py`로 장치 번호 확인

### 텔레그램 전송 실패
- 봇 토큰 확인
- 사용자가 봇에게 메시지를 보냈는지 확인
- `update_users.py`로 사용자 등록 확인

### Whisper 모델 로딩 느림
- 작은 모델 사용 (tiny, base)
- GPU 사용 시 `fp16=True` 설정

## 📝 참고 자료

- [OpenAI Whisper 문서](https://github.com/openai/whisper)
- [python-telegram-bot 문서](https://python-telegram-bot.org/)
- [Rich 문서](https://rich.readthedocs.io/)
- [PyAudio 문서](https://people.csail.mit.edu/hubert/pyaudio/docs/)

## 🚀 확장 가능한 기능

1. **데이터베이스 연동**: SQLite/PostgreSQL로 사용자 관리
2. **웹 대시보드**: Flask/FastAPI로 웹 인터페이스 추가
3. **음성 필터링**: VAD (Voice Activity Detection) 추가
4. **다국어 지원**: 언어 자동 감지 및 전사
5. **실시간 스트리밍**: WebSocket으로 실시간 텍스트 전송


