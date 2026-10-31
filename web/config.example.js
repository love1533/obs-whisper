// 뷰어 페이지를 정적 호스팅(Vercel, Netlify, GitHub Pages, Supabase Storage 등)에
// 올릴 때 사용하는 설정. 이 파일을 config.js 로 복사해서 값을 채우세요.
// (앱이 직접 띄우는 http://127.0.0.1:8765 에서는 앱이 config.js를 자동으로 만듭니다)
window.VIEWER_CONFIG = {
  title: "경기도의회 경제노동위원회",
  committee: "경제노동위원회",          // config.py의 COMMITTEE_NAME과 같아야 함
  // 시청자가 직접 재생할 도의회 원본 HLS
  streamUrl: "https://stream02.cdn.gov-ntruss.com/live/ch6/playlist.m3u8",
  videoMode: "auto",                    // auto | direct | relay
  relayBase: "",                        // 원본이 막힐 때 쓸 앱 중계 주소 (예: https://내-맥-도메인). 비우면 중계 전환 안 함
  viewerDelay: 15,                      // config.py의 VIEWER_DELAY_S와 같게
  // Supabase 프로젝트 > Settings > API 의 Project URL 과 anon(publishable) 키
  supabaseUrl: "https://YOUR-PROJECT.supabase.co",
  supabaseAnonKey: "YOUR-ANON-KEY",
};
