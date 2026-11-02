#!/bin/bash
# 경기신보 의회 모니터링 앱 종료 스크립트
# 사용: ./stop.sh
set -uo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
PID_FILE="$ROOT/.run/obs-whisper.pid"

# 종료 대상 PID 수집: PID 파일 → 없으면 이 폴더의 main.py 프로세스 검색
PIDS=""
if [ -f "$PID_FILE" ]; then
  PIDS="$(cat "$PID_FILE")"
fi
# macOS에서는 venv 파이썬이 실제 경로로 표시되므로, main.py 프로세스 중 작업 폴더가 이 프로젝트인 것만 고른다
FOUND=""
for P in $(pgrep -f "[Pp]ython.* main\.py$" || true); do
  CWD="$(lsof -a -p "$P" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  [ "$CWD" = "$ROOT" ] && FOUND="$FOUND $P"
done
PIDS="$(printf '%s\n%s\n' "$PIDS" "$FOUND" | tr -s ' ' '\n' | grep -E '^[0-9]+$' | sort -u)"

if [ -z "$PIDS" ]; then
  echo "ℹ️  실행 중인 앱이 없습니다."
  rm -f "$PID_FILE"
  exit 0
fi

for PID in $PIDS; do
  if ! kill -0 "$PID" 2>/dev/null; then
    continue
  fi
  # 자식 프로세스(streamlink, ffmpeg)도 함께 정리하기 위해 미리 기록
  CHILDREN="$(pgrep -P "$PID" || true)"

  echo "▶ 종료 요청 (PID $PID)..."
  kill -TERM "$PID" 2>/dev/null || true

  # 최대 10초 정상 종료 대기
  for i in $(seq 1 10); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
  done

  if kill -0 "$PID" 2>/dev/null; then
    echo "⚠️  정상 종료되지 않아 강제 종료합니다 (PID $PID)"
    kill -KILL "$PID" 2>/dev/null || true
  fi

  for C in $CHILDREN; do
    kill -0 "$C" 2>/dev/null && kill -KILL "$C" 2>/dev/null || true
  done
  echo "✅ 종료됨 (PID $PID)"
done

rm -f "$PID_FILE"
