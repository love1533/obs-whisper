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
# 쉼 없이 이어지는 긴 발언은 40초 뒤 문장 끝(~습니다. 등)에서, 늦어도 60초에는 끊어 전송
# (끊긴 뒷부분은 같은 발언자 배지를 물려받아 '발언 계속'으로 표시)
CLOVA_SOFT_CUT_S=40
CLOVA_MAX_SENTENCE_S=60
# 위원 명단 (이름:직책). 발언자 배지와 요청자료 위원 분류의 기준, 명단 밖 이름은 "확인필요"
COMMITTEE_MEMBERS=장태환,최민,장윤정:부위원장,박준모,송규근,송연섭,신명순,오현정,유수연,이기대,최보라,최상규,최혜경,한영수,정용한
# 감사 대상 기관과 집중 기관 (11/9: 경기신보 + 일자리재단 공동 감사)
#   AUDIT_FOCUS 를 비우면 요청자료 필터와 요약 집중이 꺼진다 (모든 기관 대상)
AUDIT_AGENCIES=경기신용보증재단,경기도일자리재단
AUDIT_FOCUS=경기신용보증재단
AUDIT_FOCUS_ALIASES=경기신용보증재단,신용보증재단,보증재단,경기신보,신보
# 기관 답변자 (이름:직함). "시석중입니다"처럼 이름만 말해도 발언자 배지가 붙는다
AUDIT_WITNESSES=시석중:경기신용보증재단 이사장,윤덕룡:경기도일자리재단 대표

# Supabase
SUPABASE_URL=https://YOUR-PROJECT.supabase.co
SUPABASE_SERVICE_KEY=서비스키 (절대 공개 금지)
SUPABASE_ANON_KEY=공개용 anon 키

# AI 요약
SUMMARY_ENABLED=1
SUMMARY_PROVIDER=openai
SUMMARY_API_KEY=sk-...
SUMMARY_MODEL=gpt-4o-mini
# 구간 보고서 단위(분): 구간이 끝나면 그 구간 발언 전체를 요약해 transcripts/날짜_보고서.md 에 쌓는다
SUMMARY_WINDOW_MIN=30

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

### 발언자 표시와 위원 분류

- 실시간 탭: "장윤정 위원입니다", "경제실장 OOO입니다"처럼 이름이 명시된 문장에만 발언자 배지가 붙는다(위원은 사진/머리글자 + 이름, 기관 관계자는 `(이름)`). 추측해서 붙이지 않으며, 한 문장에 여러 사람을 소개하면 붙이지 않는다. 판정은 `speaker_tagger.py`.
- 요청자료·지적 탭: AI가 적은 위원명이 `COMMITTEE_MEMBERS` 명단에 없으면 "확인필요"로 분류되어 맨 위 "(확인필요)" 행에 모인다. 요청 시각은 AI 값이 아니라 근거 문장의 실제 발언 시각이다.
- 기관 답변자(`AUDIT_WITNESSES`, 예: 시석중 이사장·윤덕룡 대표)는 "시석중입니다"처럼 직함 없이 말해도 배지가 붙고, 직함은 설정값으로 통일된다.

### 감사 대상 기관 집중 (요청자료 필터·AI 요약·30분 보고서)

- `AUDIT_FOCUS`(기본 경기신용보증재단)가 설정되면 요청자료는 **발언 원문(근거 문장)에 경기신보·신보 등 `AUDIT_FOCUS_ALIASES`가 실제로 들어 있는 건만** 남긴다. AI가 문맥으로 추정한 요청은 로그에 `🔕 요청 제외`로 찍히고 버려진다. 일자리재단 등 다른 기관만 대상인 요청은 들어오지 않는다.
- AI 요약 프롬프트에 감사 대상 기관·답변자·집중 기관이 들어가며, 요약은 집중 기관 관련 내용을 중심으로 쓰고 다른 기관 내용은 한두 줄로만 다룬다.
- `SUMMARY_WINDOW_MIN`(기본 30분) 구간이 끝나면 그 구간의 발언 전체를 한 번 더 LLM에 보내 보고서용 요약(제목·2~4문단·핵심 쟁점·기관 답변·수치)을 만든다. 결과는 Supabase `ai_summaries.summary_json.block`에 저장되고(뷰어 AI요약 탭의 구간 카드), 로컬에는 `transcripts/YYYY-MM-DD_보고서.md`가 구간이 닫힐 때마다 다시 써진다. 정회 등으로 발언이 없어도 5분 틱에서 지난 구간은 닫힌다.
- 뷰어 AI요약 탭의 **보고서 다운로드** 버튼은 같은 내용(구간 요약 + 요청자료 + 지적사항 + 전체 요약)을 마크다운으로 내려받는다.
- 실시간 탭은 아침부터의 모든 발언을 보여 주고(Supabase는 1000행씩 나눠 전부 읽음, 로컬 WebSocket은 전체 전송), 회의록 다운로드도 전체 발언을 발언자 표시와 함께 담는다.

#### 요청자료는 확실한 건만
- 위원이 문서를 내 달라고 분명히 말한 요청만 남긴다: LLM이 매긴 `confidence` 0.7 미만, 원문 10자 미만,
  제출 요구 표현(제출·주세요·보내·요청…)이나 자료 종류(자료·현황·내역·회의록…)가 없는 건은 버린다
  (로그 `🔕 요청 제외(...)`).
- 같은 자료를 다시 말한 건은 설명 유사도(2-gram 0.6 이상)나 원문 일치로 한 건에 합친다.
- 발언 위원이 "확인필요"면 위원장 지명("OOO 의원님 질의하시기 바랍니다", 로그 `🎤 질의 위원`)을 따라
  그 시점에 질의 중이던 위원으로 채운다. STT 오기(송기근→송규근, 최상기→최상규 등)는 `speaker_tagger._MEMBER_ALIASES`로 흡수한다.
- 뷰어 요청자료 탭은 최근 요청 순으로 위원을 정렬하고, 확인필요 건은 맨 아래 둔다.

### 위원별 질의 성향 분석 (회의 후 일괄, 뷰어 "위원 성향" 탭)

회의록을 위원별 질의 구간으로 나누고(규칙 우선), 구간마다 LLM으로 "실제로 무엇을 어떻게 물었는지"를 정리한 뒤
위원별로 집계한다. 예상 질의·준비 체크리스트는 만들지 않는다 (원문 근거만).

```bash
# 1) 답변자 명단 준비: config/answerers/<회의일자>.json (피감기관·답변자·호칭 규칙). 위원 명단은 config/roster.json (고정)
# 2) 회의가 끝난 뒤 실행 (transcripts/<날짜>.jsonl 사용, LLM 설정은 env 의 SUMMARY_*)
python analyze_meeting.py --date 2026-11-09
python analyze_meeting.py --date 2026-11-09 --segments-only   # 구간 분할만 보고 검증 (LLM 호출 없음)
python analyze_meeting.py --date 2026-11-09 --expect "박준모,한영수,..."   # 본질의 기대 순서와 대조
python analyze_meeting.py --profiles-only                      # 저장된 분석으로 프로필·뷰어 데이터만 재생성
python analyze_meeting.py --date 2026-11-09 --supabase         # Supabase 에도 저장 (아래 마이그레이션 먼저)
```

- 결과: `transcripts/analysis/<날짜>.segments.json` (구간), `<날짜>.analyses.json` (구간 분석), `profiles.json` (위원 프로필),
  `web/data/profiles.json` (뷰어 데이터 → git 커밋·푸시하면 Vercel 뷰어 "위원 성향" 탭에 표시)
- Supabase 저장을 쓰려면 `supabase/migrations/20261107_member_profiles.sql` 을 SQL Editor 에서 한 번 실행한다
  (meetings / members / answerers / segments / analyses / member_profiles). 뷰어는 테이블이 있으면 그 값을, 없으면 정적 JSON 을 쓴다.
- 구간 분할 규칙 (`lib/segment.py`): 위원장 지명("OOO 의원님 질의하시기 바랍니다", "이어서 우리 OOO 의원님" 등)으로 시작,
  구간 첫 3줄의 자기소개("OO 출신 OOO입니다")로 확정, "수고하셨습니다"·정회로 종료. 위원장이 마무리 뒤 덧붙이는 긴 발언은
  `위원장 보충` 구간, 짧은 진행 발언은 분석에서 제외. 명단에 없는 이름은 UNKNOWN 으로 남기고 뷰어에 "명단 추가 후보"로 보여 준다.
- 분석 프롬프트: `prompts/member_segment.md` (구간), `prompts/member_profile.md` (집계). 출력은 pydantic 으로 검증하고 실패 시 1회 재시도.
- 피감기관이 둘 이상이면 질문마다 `target_agency` 를 붙인다 (호칭 → 답변자 소속 → 주제 키워드 순, 애매하면 "공통"/"불명").
- 11/6 샘플 검증: 위원 15명 전원 구간 보유, UNKNOWN 0건, 본질의 순서가 기대값과 일치.

### 5. 운영 대시보드 (로컬 전용)

앱이 처리하는 내역을 실시간으로 보는 운영자용 화면입니다 (2초마다 갱신, Vercel에는 배포되지 않음).

- 주소: http://127.0.0.1:8766/ops (`WEB_PORT`에 맞춰 변경)
- 스트림 수신: 연결 상태, 받은 오디오 길이, 연결 시도/성공 횟수, 마지막 ffmpeg 오류
- 음성 인식: 인식 문장 수(1분/5분 속도), 중간 인식 결과, Clova 재연결 횟수, 최근 문장 30개
- AI 분석: 요청 감지·5분 분석·전체 요약·시간대 요약 각각의 실행 횟수, 마지막 실행, 소요 시간, 다음 예정, 결과/오류, LLM 호출 수와 토큰
- Supabase: 대기 큐, 종류별 전송 성공/실패, 마지막 오류
- 이벤트 로그: `logs/obs-whisper.log`에서 HTTP 잡음을 뺀 최근 300줄 (오류만 보기 필터)
- JSON API: `/ops/api/state`, `/ops/api/events?after=N`

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
