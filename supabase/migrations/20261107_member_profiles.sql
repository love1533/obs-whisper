-- 위원별 질의 성향 분석 (analyze_meeting.py 결과 저장)
-- Supabase 대시보드 > SQL Editor 에 붙여 넣고 실행하세요. 여러 번 실행해도 안전합니다.

-- 회의 (id = 회의 날짜 YYYY-MM-DD)
create table if not exists public.meetings (
  id          text primary key,
  date        date not null,
  committee   text not null,
  agencies    text[] not null default '{}',   -- 피감기관
  title       text,
  updated_at  timestamptz not null default now()
);

-- 위원 (상임위 고정 명단: config/roster.json)
create table if not exists public.members (
  id          bigint generated always as identity primary key,
  name        text not null unique,
  role        text,
  district    text,
  party       text,
  aliases     text[] not null default '{}',
  updated_at  timestamptz not null default now()
);

-- 답변자 (회의별: config/answerers/<날짜>.json)
create table if not exists public.answerers (
  id          bigint generated always as identity primary key,
  meeting_id  text references public.meetings(id) on delete cascade,
  name        text not null,
  title       text,
  agency      text,
  aliases     text[] not null default '{}',
  unique (meeting_id, name)
);

-- 질의 구간 (규칙 분할 결과)
create table if not exists public.segments (
  id            bigint generated always as identity primary key,
  meeting_id    text not null references public.meetings(id) on delete cascade,
  member_id     bigint references public.members(id) on delete set null,   -- null 이면 UNKNOWN/위원장
  speaker_raw   text not null,            -- 명단 이름 | 위원장 | 확인필요
  phase         text,                     -- 자료요구 | 본질의 | 추가질의 | 위원장 보충
  type          text,                     -- 질의 | 자료요구
  start_ts      text not null,            -- HH:MM:SS
  end_ts        text,
  start_id      bigint,                   -- transcripts.id (뷰어에서 원문으로 이동)
  sentence_ids  bigint[] not null default '{}',
  raw_text      text,
  candidates    text[] not null default '{}',   -- UNKNOWN일 때 명단 추가 후보
  created_at    timestamptz not null default now(),
  unique (meeting_id, start_ts, speaker_raw)
);
create index if not exists segments_meeting_idx on public.segments (meeting_id, start_ts);

-- 구간 LLM 분석 (json = prompts/member_segment.md 출력)
create table if not exists public.analyses (
  id          bigint generated always as identity primary key,
  segment_id  bigint not null references public.segments(id) on delete cascade,
  json        jsonb not null,
  model       text,
  created_at  timestamptz not null default now(),
  unique (segment_id, model)
);

-- 위원 프로필 (analyses 집계로 재생성)
create table if not exists public.member_profiles (
  member_id     bigint primary key references public.members(id) on delete cascade,
  updated_at    timestamptz not null default now(),
  profile_json  jsonb not null
);

-- 뷰어(anon)는 읽기만
alter table public.meetings        enable row level security;
alter table public.members         enable row level security;
alter table public.answerers       enable row level security;
alter table public.segments        enable row level security;
alter table public.analyses        enable row level security;
alter table public.member_profiles enable row level security;

drop policy if exists "public read meetings" on public.meetings;
create policy "public read meetings" on public.meetings for select to anon, authenticated using (true);
drop policy if exists "public read members" on public.members;
create policy "public read members" on public.members for select to anon, authenticated using (true);
drop policy if exists "public read answerers" on public.answerers;
create policy "public read answerers" on public.answerers for select to anon, authenticated using (true);
drop policy if exists "public read segments" on public.segments;
create policy "public read segments" on public.segments for select to anon, authenticated using (true);
drop policy if exists "public read analyses" on public.analyses;
create policy "public read analyses" on public.analyses for select to anon, authenticated using (true);
drop policy if exists "public read member_profiles" on public.member_profiles;
create policy "public read member_profiles" on public.member_profiles for select to anon, authenticated using (true);
