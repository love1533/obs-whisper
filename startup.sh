#!/bin/bash
# 경기신보 의회 모니터링 앱 시작 스크립트
# 사용: ./startup.sh            (백그라운드 실행, 기본 포트 8766)
#       WEB_PORT=9000 ./startup.sh
#       ./startup.sh -f         (포그라운드 실행, 터미널 UI 표시)
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$ROOT/logs"
PID_FILE="$RUN_DIR/obs-whisper.pid"
LOG_FILE="$LOG_DIR/obs-whisper.log"
PYTHON="$ROOT/venv/bin/python"

mkdir -p "$RUN_DIR" "$LOG_DIR"

FOREGROUND=0
[ "${1:-}" = "-f" ] && FOREGROUND=1

# 0. venv 확인
if [ ! -x "$PYTHON" ]; then
  echo "❌ venv가 없습니다: $PYTHON"
  echo "   python3 -m venv venv && venv/bin/pip install -r requirements.txt"
  exit 1
fi

# 1. 환경 변수 로드 (env 우선, 없으면 .env)
#    config.py는 .env만 읽지만, 먼저 export된 값이 우선하므로 env 파일 값이 적용된다.
if [ -f "$ROOT/env" ]; then
  set -a; source "$ROOT/env"; set +a
  echo "✅ 설정 로드: env"
elif [ -f "$ROOT/.env" ]; then
  set -a; source "$ROOT/.env"; set +a
  echo "✅ 설정 로드: .env"
else
  echo "❌ env 또는 .env 파일이 없습니다."
  exit 1
fi

# 2. 포트 결정 (8765는 다른 프로젝트가 쓰는 경우가 있어 기본 8766)
export WEB_PORT="${WEB_PORT:-8766}"
export WEB_HOST="${WEB_HOST:-127.0.0.1}"

# 3. 이미 실행 중인지 확인
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE")"
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "⚠️  이미 실행 중입니다 (PID $OLD_PID). 먼저 ./stop.sh 로 종료하세요."
    exit 1
  fi
  rm -f "$PID_FILE"
fi

# 4. 포트 점유 확인
if PORT_PID="$(lsof -nP -t -iTCP:"$WEB_PORT" -sTCP:LISTEN 2>/dev/null | head -1)" && [ -n "$PORT_PID" ]; then
  echo "❌ 포트 $WEB_PORT 이(가) 이미 사용 중입니다 (PID $PORT_PID):"
  ps -p "$PORT_PID" -o pid=,command= || true
  echo "   다른 포트로 실행: WEB_PORT=8767 ./startup.sh"
  exit 1
fi

# 5. 실행
if [ "$FOREGROUND" = "1" ]; then
  echo "▶ 포그라운드 실행 (종료: Ctrl+C) — http://localhost:$WEB_PORT"
  exec "$PYTHON" main.py
fi

echo "▶ 백그라운드 실행 중..."
nohup "$PYTHON" main.py >> "$LOG_FILE" 2>&1 &
APP_PID=$!
echo "$APP_PID" > "$PID_FILE"

# 6. 기동 확인 (최대 30초 동안 포트가 열리는지 대기)
for i in $(seq 1 30); do
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "❌ 앱이 시작 직후 종료되었습니다. 로그 마지막 줄:"
    tail -20 "$LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
  fi
  if lsof -nP -t -iTCP:"$WEB_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "✅ 시작 완료 (PID $APP_PID)"
    echo "   웹 뷰어: http://localhost:$WEB_PORT"
    echo "   로그:    $LOG_FILE  (tail -f 로 확인)"
    echo "   종료:    ./stop.sh"
    exit 0
  fi
  sleep 1
done

echo "⚠️  30초 안에 포트 $WEB_PORT 이(가) 열리지 않았습니다. 프로세스는 살아 있습니다 (PID $APP_PID)."
echo "   로그 확인: tail -f $LOG_FILE"
exit 1
