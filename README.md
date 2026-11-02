# 경기신보 의회 모니터링 시스템

경기도의회 행정사무감사에서 **경기신용보증재단(경기신보)** 관련 위원 질의·요청사항을 실시간으로 모니터링하고, AI가 자동 분석·요약하는 시스템입니다.

## 주요 기능

- **실시간 자막** — 의회 방송 음성을 STT(Clova Speech)로 텍스트 변환, 실시간 표시
- **AI 요약** — 시간대별(1시간 단위) 핵심 쟁점, Q&A, 지표 카드, 레이더 차트
- **경기신보 요청사항** — 위원별 자료 요청을 AI가 자동 감지·분류, 원문 보존, 엑셀 다운로드
- **날짜별 조회** — 과거 감사 기록을 날짜 선택으로 조회
- **모바일 지원** — 반응형 웹, Vercel 정적 배포

## 아키텍처

```
서버 (Mac) ─ .env에 AI KEY 보유
  │
  ├─ STT (Clova Speech) → transcripts 테이블
  ├─ 1분마다: 요청 감지 (GPT-4o-mini, 저렴) → ai_requests 테이블
  ├─ 5분마다: AI 요약 (GPT-4o-mini) → ai_summaries 테이블
  └─ 1시간마다: 시간대별 요약 저장
        │
        ▼ Supabase Realtime
        │
직원 브라우저/모바일 (AI KEY 불필요, 조회만)
  ├─ 실시간 탭: 발언 자막
  ├─ AI 요약 탭: 시간대별 타임라인 + 대시보드
  └─ 경기신보 요청사항 탭: 위원별 요청 + 원문
```

## 설치

### 1. 의존성

```bash
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 환경 변수 (`.env`)

```env
# 위원회
COMMITTEE_NAME=경제노동위원회

# 스트림 (11/9 당일 경기도의회 라이브 URL로 교체)
STREAM_URL=https://stream02.cdn.gov-ntruss.com/live/ch6/playlist.m3u8

# STT (Clova Speech)
CLOVA_SPEECH_URL=clovaspeech-gw.ncloud.com:50051
CLOVA_SPEECH_SECRET=your_secret

# Supabase
SUPABASE_URL=https://YOUR-PROJECT.supabase.co
SUPABASE_SERVICE_KEY=서비스키 (절대 공개 금지)
SUPABASE_ANON_KEY=공개용 anon 키

# AI 요약
SUMMARY_ENABLED=1
SUMMARY_PROVIDER=openai
SUMMARY_API_KEY=sk-...
SUMMARY_MODEL=gpt-4o-mini

# 웹 뷰어
WEB_ENABLED=1
WEB_PORT=8765
```

### 3. Supabase 테이블 생성

SQL Editor에서 [`supabase/schema.sql`](supabase/schema.sql) 실행:
- `broadcast_sessions` — 방송 세션
- `transcripts` — 인식된 문장
- `ai_summaries` — AI 요약 (시간대별)
- `ai_requests` — 위원 요청사항
- `ai_issues` — 지적/답변

### 4. 실행

```bash
source .venv/bin/activate
python main.py
```

웹 뷰어: http://127.0.0.1:8765

## Vercel 배포 (웹 뷰어 공개)

직원들이 외부에서 접속할 수 있도록 웹 뷰어만 Vercel에 배포합니다.

```bash
# config.js 생성 (.env에서 Supabase 키 주입)
bash web/gen_config.sh

# 배포
cd web
vercel login
vercel --prod
```

배포 URL: `https://gcgfvideo.vercel.app`

> Vercel에는 HTML/CSS/JS만 올라감. AI KEY나 서비스 키는 서버(Mac)에만 존재.
> 브라우저가 Supabase에 직접 WebSocket 연결하므로 Vercel 무료 플랜으로 충분.

## AI 분석 비용

| 주기 | 모델 | 역할 | 하루 비용 (6시간 감사) |
|------|------|------|---------------------|
| 1분 | GPT-4o-mini | 요청사항 감지 | ~200원 |
| 5분 | GPT-4o-mini | 전체 요약 | ~2,000원 |
| **합계** | | | **~3,000원/일** |

경기신보 행정사무감사는 연 1~2일이므로 연간 약 6,000원.

## 로컬 데모 (운영 배포용 아님)

서버·Supabase·OpenAI 없이 뷰어만 띄워 AI요약·요청자료·지적답변이 실시간처럼 흘러가는 모습을 본다.

```bash
bash demo.sh          # 보통 속도   → http://localhost:8790/?demo
bash demo.sh fast     # 빠르게      → ?demo=fast
bash demo.sh slow     # 느리게      → ?demo=slow
```

- 시나리오는 [`web/demo.js`](web/demo.js) 한 파일에 있다(2025년 실제 회의록 기반 재구성, 30문장). 접속하면 이미 지난 오전 발언이 한 번에 들어오고, 이후 발언이 몇 초 간격으로 중간 인식(타이핑)→확정 순으로 들어온다. 자료 요청 발언 뒤에는 요청자료 탭에 추가되며 토스트 알림이 뜨고, AI요약은 시간대별 블록·대시보드·질의응답·전체 요약이 단계적으로 갱신된다.
- `demo.js`는 localhost(사설 IP 포함)에서 `?demo`가 붙었을 때만 app.js가 불러온다. 운영(Vercel) 주소에서는 동작하지 않으며 `.vercelignore`로 CLI 배포에서도 제외된다.

## 시뮬레이션

과거 회의록 기반 시뮬레이션 데이터:
- [`supabase/simulation_1101.sql`](supabase/simulation_1101.sql) — 2025년 실제 회의록 기반
- [`supabase/sample_data.sql`](supabase/sample_data.sql) — 기본 샘플

## 프로젝트 구조

```
├── main.py                 # 앱 진입점
├── config.py               # 환경 변수 로딩
├── audio_pipeline.py       # HLS → PCM 오디오 추출
├── vad_segmenter.py        # Silero VAD 발화 분절
├── summarizer.py           # AI 요약 엔진 (프롬프트 + LLM 호출)
├── summary_scheduler.py    # 1분/5분/1시간 주기 스케줄러
├── caption_server.py       # 웹 서버 (WebSocket + 정적 파일)
├── supabase_sink.py        # Supabase 저장 모듈
├── supabase/
│   └── schema.sql          # DB 스키마
├── web/
│   ├── index.html          # 메인 페이지
│   ├── app.js              # 실시간 자막 + Supabase 연동
│   ├── summary.js          # AI 요약 + 요청사항 렌더링
│   ├── style.css           # 라이트 테마 스타일
│   ├── config.js           # Supabase 키 (gen_config.sh로 생성)
│   ├── gen_config.sh       # .env → config.js 생성
│   ├── favicon.svg         # 파비콘
│   └── vercel.json         # Vercel 배포 설정
└── .env                    # 환경 변수 (Git 미포함)
```
