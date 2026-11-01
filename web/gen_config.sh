#!/bin/bash
# .env에서 Supabase 키를 읽어 web/config.js를 생성
# 사용: cd obs-whisper && bash web/gen_config.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "❌ .env 파일이 없습니다: $ENV_FILE"
  exit 1
fi

# .env 로드
set -a
source "$ENV_FILE"
set +a

cat > "${SCRIPT_DIR}/config.js" <<EOF
window.VIEWER_CONFIG = {
  title: "경기신용보증재단 행정사무감사 모니터링",
  committee: "${COMMITTEE_NAME:-경제노동위원회}",
  streamUrl: "${VIEWER_STREAM_URL:-}",
  videoMode: "${VIDEO_MODE:-auto}",
  viewerDelay: ${VIEWER_DELAY_S:-15},
  supabaseUrl: "${SUPABASE_URL}",
  supabaseAnonKey: "${SUPABASE_ANON_KEY}",
};
EOF

echo "✅ web/config.js 생성 완료 (Supabase: ${SUPABASE_URL})"
