-- obs-whisper Supabase 스키마
-- Supabase 대시보드 > SQL Editor 에 붙여 넣고 실행하세요.

-- 방송 연결(세션): 앱이 스트림에 붙을 때마다 한 행
create table if not exists public.broadcast_sessions (
  id          text primary key,              -- 예: 20261029-140312
  committee   text not null,                 -- 예: 경제노동위원회
  stream_url  text,                          -- 도의회 원본 HLS 주소
  relay_path  text,                          -- 앱 중계 HLS 경로 (원본 재생이 막힐 때 대안)
  status      text,
  started_at  timestamptz not null default now(),
  ended_at    timestamptz,                   -- null 이면 방송 중
  updated_at  timestamptz not null default now()
);

-- 인식된 문장: 한 문장에 한 행
create table if not exists public.transcripts (
  id             bigint generated always as identity primary key,
  session_id     text references public.broadcast_sessions(id) on delete set null,
  committee      text not null,
  seq            integer,                    -- 앱 내 순번
  start_s        double precision,           -- 세션 시작 기준 발언 위치(초)
  pts            double precision,           -- 원본 스트림 PTS(초): 영상 싱크 기준
  broadcast_time timestamptz not null,       -- 발언 시각
  recognized_at  timestamptz,
  text           text not null,
  html           text,                       -- 키워드 강조가 들어간 HTML (이스케이프 완료)
  created_at     timestamptz not null default now()
);

create index if not exists transcripts_committee_time_idx
  on public.transcripts (committee, broadcast_time desc);
create index if not exists broadcast_sessions_committee_started_idx
  on public.broadcast_sessions (committee, started_at desc);

-- updated_at 자동 갱신
create or replace function public.touch_updated_at() returns trigger
language plpgsql as $$ begin new.updated_at = now(); return new; end $$;
drop trigger if exists broadcast_sessions_touch on public.broadcast_sessions;
create trigger broadcast_sessions_touch before update on public.broadcast_sessions
  for each row execute function public.touch_updated_at();

-- 보안: 누구나 읽기만 가능, 쓰기는 서비스 키(앱)만
alter table public.broadcast_sessions enable row level security;
alter table public.transcripts enable row level security;

drop policy if exists "public read sessions" on public.broadcast_sessions;
create policy "public read sessions" on public.broadcast_sessions
  for select to anon, authenticated using (true);

drop policy if exists "public read transcripts" on public.transcripts;
create policy "public read transcripts" on public.transcripts
  for select to anon, authenticated using (true);

-- ================================================================
-- AI 분석 결과: 5분 윈도우마다 AI가 생성한 요약 JSON 전체를 저장
-- ================================================================
create table if not exists public.ai_summaries (
  id             bigint generated always as identity primary key,
  session_id     text references public.broadcast_sessions(id) on delete set null,
  committee      text not null,
  window_num     integer not null,               -- 몇 번째 5분 윈도우인지
  current_topic  text,                           -- 현재 논의 주제
  summary_json   jsonb not null,                 -- AI 응답 전체 (recent, topics 등)
  full_summary   text,                           -- 전체 회의 종합 요약
  created_at     timestamptz not null default now()
);

create index if not exists ai_summaries_session_idx
  on public.ai_summaries (session_id, window_num desc);

-- ================================================================
-- 요청사항: AI가 감지한 위원의 자료 요청/질의를 개별 행으로 저장
-- ================================================================
create table if not exists public.ai_requests (
  id             bigint generated always as identity primary key,
  session_id     text references public.broadcast_sessions(id) on delete set null,
  committee      text not null,
  raised_by      text,                           -- 요청 위원명
  description    text not null,                  -- 요청 요약
  original_text  text,                           -- 위원 발언 원문
  type           text,                           -- 자료종류 (통계/현황/계획서 등)
  target         text,                           -- 대상기관 (경기신용보증재단/경제실 등)
  period         text,                           -- 자료 기간
  deadline       text,                           -- 제출기한
  status         text not null default 'new',    -- new / ongoing / completed
  requested_at   timestamptz,                    -- 요청 시각
  sentence_ids   integer[],                      -- 근거 문장 ID 배열
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index if not exists ai_requests_session_idx
  on public.ai_requests (session_id, created_at desc);
create index if not exists ai_requests_target_idx
  on public.ai_requests (committee, target);

-- updated_at 자동 갱신
drop trigger if exists ai_requests_touch on public.ai_requests;
create trigger ai_requests_touch before update on public.ai_requests
  for each row execute function public.touch_updated_at();

-- ================================================================
-- 지적/답변: AI가 감지한 지적사항과 답변 연결
-- ================================================================
create table if not exists public.ai_issues (
  id             bigint generated always as identity primary key,
  session_id     text references public.broadcast_sessions(id) on delete set null,
  committee      text not null,
  issue          text not null,                  -- 지적사항 요약
  raised_by      text,                           -- 제기 위원명
  issue_ids      integer[],                      -- 지적 근거 문장 ID
  response       text,                           -- 답변 내용
  responded_by   text,                           -- 답변자
  response_ids   integer[],                      -- 답변 근거 문장 ID
  status         text not null default 'unanswered', -- unanswered/answered/partial
  commitment     text,                           -- 약속/조치사항
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index if not exists ai_issues_session_idx
  on public.ai_issues (session_id, created_at desc);

drop trigger if exists ai_issues_touch on public.ai_issues;
create trigger ai_issues_touch before update on public.ai_issues
  for each row execute function public.touch_updated_at();

-- ================================================================
-- RLS: 조회는 누구나, 쓰기는 서비스 키만
-- ================================================================
alter table public.ai_summaries enable row level security;
alter table public.ai_requests enable row level security;
alter table public.ai_issues enable row level security;

drop policy if exists "public read ai_summaries" on public.ai_summaries;
create policy "public read ai_summaries" on public.ai_summaries
  for select to anon, authenticated using (true);

drop policy if exists "public read ai_requests" on public.ai_requests;
create policy "public read ai_requests" on public.ai_requests
  for select to anon, authenticated using (true);

drop policy if exists "public read ai_issues" on public.ai_issues;
create policy "public read ai_issues" on public.ai_issues
  for select to anon, authenticated using (true);

-- ================================================================
-- Realtime: 모든 테이블을 실시간 구독 가능하게
-- ================================================================
do $$
begin
  begin alter publication supabase_realtime add table public.broadcast_sessions;
  exception when duplicate_object then null; end;
  begin alter publication supabase_realtime add table public.transcripts;
  exception when duplicate_object then null; end;
  begin alter publication supabase_realtime add table public.ai_summaries;
  exception when duplicate_object then null; end;
  begin alter publication supabase_realtime add table public.ai_requests;
  exception when duplicate_object then null; end;
  begin alter publication supabase_realtime add table public.ai_issues;
  exception when duplicate_object then null; end;
end $$;
