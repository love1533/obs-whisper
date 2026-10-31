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

-- 실시간: 두 테이블의 변경을 Realtime(Postgres Changes)으로 내보냄
do $$
begin
  begin alter publication supabase_realtime add table public.broadcast_sessions;
  exception when duplicate_object then null; end;
  begin alter publication supabase_realtime add table public.transcripts;
  exception when duplicate_object then null; end;
end $$;
