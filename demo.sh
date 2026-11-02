#!/bin/bash
# 로컬 전용 데모: 서버·Supabase·OpenAI 없이 web/ 뷰어를 띄우고 AI요약·요청자료가 실시간처럼 흘러가는 시나리오를 재생한다.
#   bash demo.sh          # 보통 속도 (발언 7초 간격 정도)
#   bash demo.sh fast     # 빠르게
#   bash demo.sh slow     # 느리게
# 운영 배포와 무관하다. demo.js는 localhost 에서 ?demo 가 붙었을 때만 동작한다.
set -e
cd "$(dirname "$0")/web"
PORT="${PORT:-8790}"
MODE="${1:-}"
URL="http://localhost:${PORT}/?demo${MODE:+=$MODE}"
if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
  echo "정적 서버 시작: http://localhost:${PORT} (PID $!)"
  sleep 1
fi
echo "데모 주소: $URL"
command -v open >/dev/null && open "$URL"
