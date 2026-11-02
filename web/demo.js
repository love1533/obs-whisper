// 로컬 전용 데모 모드 - 실제 행정사무감사처럼 발언·AI요약·요청자료·지적답변이 "실시간으로" 흘러가게 재생한다.
//
//   사용: web/ 를 로컬에서 띄운 뒤  http://localhost:8790/?demo   (빠르게: ?demo=fast, 느리게: ?demo=slow)
//   조건: app.js가 localhost/사설 IP 에서 ?demo 가 붙었을 때만 이 파일을 불러온다. 운영(Vercel)에서는 절대 실행되지 않는다.
//   데이터: Supabase·OpenAI·서버 없이 이 파일 안의 시나리오만 쓴다(2025년 실제 회의록 기반 재구성, supabase/simulation_1101.sql 참고).
//
//   흐름: 페이지를 열면 "이미 지난 오전 발언"이 한 번에 들어오고, 이후 발언이 몇 초 간격으로 한 문장씩 들어온다.
//         발언 앞에 중간 인식(partial)이 타이핑되듯 보이고, 자료 요청이 나오면 잠시 뒤 요청자료 탭에 추가되며 토스트 알림이 뜬다.
//         AI요약은 발언이 쌓일 때마다 갱신되고(시간대별 블록·대시보드·질의응답), 지적사항에 답변이 오면 상태가 바뀐다.
(() => {
  const local = /^(localhost|127\.0\.0\.1|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$/.test(location.hostname);
  const mode = new URLSearchParams(location.search).get("demo");
  if (!local || mode === null) return;
  const bridge = window._appBridge;
  const handlers = bridge && bridge.handlers;
  if (!handlers) { console.error("demo.js: app.js 브릿지가 없습니다"); return; }

  const $ = (id) => document.getElementById(id);
  const SPEED = { fast: 0.3, slow: 2.5 }[mode] || 1;   // 발언 간격 배율
  const BASE_GAP_S = 7;                                   // 기본 발언 간격(초)
  const PRELOADED = 14;                                   // 이 개수까지는 "이미 지난 발언"으로 한 번에 표시
  const SESSION_ID = "demo-" + new Date().toISOString().slice(0, 10).replace(/-/g, "") + "-101500";

  // ------------------------------------------------------------ 시나리오
  // 각 항목: 발언 텍스트, (선택) 강조 html, 그리고 이 발언 직후 일어나는 이벤트(요청 감지 / 지적 / 답변 / 요약 갱신)
  // 시간은 "지금"을 기준으로 뒤로 계산한다. offsetMin: 첫 발언(개회)으로부터 몇 분 뒤인지.
  const S = [];
  const say = (offsetMin, text, html, ev) => S.push({ offsetMin, text, html: html || text, ev: ev || {} });

  say(0,  "위원장 장태환입니다. 2026년도 경제노동위원회 행정사무감사를 시작하겠습니다. 오늘은 경기신용보증재단, 경제실, 경기경제자유구역청에 대한 종합감사를 실시합니다.",
          "위원장 <b>장태환</b>입니다. 2026년도 <b>경제노동위원회</b> <b>행정사무감사</b>를 시작하겠습니다. 오늘은 <b>경기신용보증재단</b>, 경제실, 경기경제자유구역청에 대한 종합감사를 실시합니다.");
  say(1,  "먼저 경기신용보증재단 이사장께서 기관 현황 보고를 해 주시고, 이어서 위원님들 질의 순서로 진행하겠습니다. 질의하실 위원님은 거수로 신청해 주시기 바랍니다.");
  say(3,  "경기신용보증재단 이사장입니다. 2026년 10월 말 기준 보증잔액은 13조 2천억 원이고, 올해 신규 보증은 4조 1천억 원을 공급했습니다. 대위변제는 5,870억 원으로 전년 대비 8% 늘었습니다.",
          "<b>경기신용보증재단</b> 이사장입니다. 2026년 10월 말 기준 <b>보증잔액</b>은 <b>13조 2천억 원</b>이고, 올해 신규 보증은 <b>4조 1천억 원</b>을 공급했습니다. <b>대위변제</b>는 <b>5,870억 원</b>으로 전년 대비 8% 늘었습니다.");
  say(6,  "성남시 출신 정용한 위원입니다. 이사장님, 위수탁사업에서 회의록 작성이 제대로 안 되고 있습니다. 제가 요청한 회의록이 제출이 안 됐어요.",
          "성남시 출신 <b>정용한</b> 위원입니다. 이사장님, 위수탁사업에서 <b>회의록</b> 작성이 제대로 안 되고 있습니다. 제가 요청한 회의록이 제출이 안 됐어요.");
  say(7,  "47개의 위탁사업 566억 5000만 원 규모인데 회의자료가 57개밖에 없다고요. 이게 기본적인 행정 기록 아닙니까? 위탁사업별 관리 현황하고 수수료율 자료 제출해 주세요.",
          "47개의 <b>위탁사업</b> <b>566억</b> 5000만 원 규모인데 회의자료가 57개밖에 없다고요. 이게 기본적인 행정 기록 아닙니까? 위탁사업별 관리 현황하고 <b>수수료율</b> 자료 제출해 주세요.",
          { request: { raised_by: "정용한", description: "47개 위탁사업(566억) 회의록·관리 현황, 수수료율 자료", type: "관리현황/회의록", target: "경기신용보증재단", period: "2024~2026년" },
            issue: { raised_by: "정용한", issue: "위수탁사업 566억 규모 중 회의자료 57개만 존재, 회의록 미제출", amounts: ["566억 원"] } });
  say(9,  "경기신용보증재단 이사장입니다. 지적하신 부분 송구합니다. 위탁사업 운영위원회 회의록은 금주 내 일괄 정리해서 제출하겠습니다.",
          "<b>경기신용보증재단</b> 이사장입니다. 지적하신 부분 송구합니다. 위탁사업 운영위원회 회의록은 <b>금주 내</b> 일괄 정리해서 제출하겠습니다.",
          { answer: { issueIndex: 0, response: "위탁사업 운영위원회 회의록을 금주 내 일괄 정리해 제출하겠다.", responded_by: "재단 이사장", status: "partial", commitment: "회의록 금주 내 제출" } });
  say(12, "광명시 출신 최민 위원입니다. 경기신보 인원 현황을 보면 정규직 377명, 계약직 115명, 임시직 112명, 시간제근로자 56명입니다. 총 608명인데 정규직 대비 계약직과 임시직이 약 75%를 차지하고 있어요.",
          "광명시 출신 <b>최민</b> 위원입니다. 경기신보 인원 현황을 보면 정규직 <b>377명</b>, 계약직 <b>115명</b>, 임시직 <b>112명</b>, 시간제근로자 <b>56명</b>입니다. 총 <b>608명</b>인데 정규직 대비 계약직과 임시직이 약 <b>75%</b>를 차지하고 있어요.");
  say(13, "지난 3년 동안에, 지금 4년째 얘기를 하고 있어요. 그런데 하나도 시정이 안 돼요. 시간제근로자 56명이 민감한 고객 정보를 다루는 업무를 수행하고 있는데 이게 맞습니까? 고용 형태별 상세 현황하고 시간제근로자 업무 배치 현황 제출해 주십시오.",
          "지난 3년 동안에, 지금 <b>4년째</b> 얘기를 하고 있어요. 그런데 하나도 시정이 안 돼요. 시간제근로자 56명이 민감한 <b>고객 정보</b>를 다루는 업무를 수행하고 있는데 이게 맞습니까? 고용 형태별 상세 현황하고 시간제근로자 업무 배치 현황 제출해 주십시오.",
          { request: { raised_by: "최민", description: "정규직·계약직·임시직·시간제근로자 상세 현황, 시간제근로자 업무 배치 현황", type: "인력현황", target: "경기신용보증재단" },
            issue: { raised_by: "최민", issue: "비정규직 비율 75% (4년째 미시정), 시간제근로자 56명 고객 민감정보 취급", amounts: ["비정규직 75%", "시간제근로자 56명"] } });
  say(15, "보증배수를 보면 서울이 9.1배인데 경기도는 6배밖에 안 됩니다. 기본재산 대비 보증배수가 낮다는 것은 그만큼 소상공인에게 보증이 덜 가고 있다는 뜻 아닙니까?",
          "<b>보증배수</b>를 보면 서울이 <b>9.1배</b>인데 경기도는 <b>6배</b>밖에 안 됩니다. 기본재산 대비 보증배수가 낮다는 것은 그만큼 소상공인에게 보증이 덜 가고 있다는 뜻 아닙니까?",
          { issue: { raised_by: "최민", issue: "보증배수 서울 9.1배 vs 경기 6배, 소상공인 보증 접근성 부족", amounts: ["서울 9.1배", "경기 6배"] } });
  say(17, "경기신용보증재단 이사장입니다. 중앙회에서 재보증한도를 제공하고 있는데 작년부터 재보증 예산이 부족한 상황입니다. 인력 문제는 도와 협의하여 개선해 나가겠습니다.",
          "<b>경기신용보증재단</b> 이사장입니다. 중앙회에서 <b>재보증한도</b>를 제공하고 있는데 작년부터 재보증 예산이 부족한 상황입니다. 인력 문제는 도와 협의하여 개선해 나가겠습니다.",
          { answer: { issueIndex: 1, response: "인력 문제는 도와 협의하여 개선해 나가겠다.", responded_by: "재단 이사장", status: "partial", commitment: "도와 협의하여 인력 구조 개선" },
            answer2: { issueIndex: 2, response: "중앙회 재보증한도를 쓰고 있으나 작년부터 재보증 예산이 부족하다.", responded_by: "재단 이사장", status: "partial" },
            summary: 1 });
  say(21, "광주시 출신 최보라 위원입니다. 경제실에 질의하겠습니다. 소상공인연합회 전문가 세 분야 상담 용역 관련해서 상담 일지, 상담 결과, 후속 조치 자료를 제출해 주십시오. 도민의 혈세가 단발성으로 담당자의 기억에 의존해 공중분해되고 있습니다.",
          "광주시 출신 <b>최보라</b> 위원입니다. 경제실에 질의하겠습니다. <b>소상공인연합회</b> 전문가 세 분야 상담 용역 관련해서 상담 일지, 상담 결과, 후속 조치 자료를 제출해 주십시오. 도민의 혈세가 단발성으로 담당자의 기억에 의존해 공중분해되고 있습니다.",
          { request: { raised_by: "최보라", description: "소상공인연합회 전문가 상담 용역 상담일지·결과·후속조치 자료", type: "용역결과/현황", target: "경제실" } });
  say(24, "용인시 출신 한영수 위원입니다. 2년 연속 부채 중점기관으로 지정됐는데, 주요 정책별로 보증 사업 부실이 얼마나 되는지 상세 명세를 좀 주세요.",
          "용인시 출신 <b>한영수</b> 위원입니다. 2년 연속 <b>부채 중점기관</b>으로 지정됐는데, 주요 정책별로 보증 사업 <b>부실</b>이 얼마나 되는지 상세 명세를 좀 주세요.");
  say(25, "불승인율이 2023년 9.9%에서 2025년 22%로 2배 이상 증가했는데 올해는 어떤지, 보증신청 승인 불승인 건수하고 불승인율도 같이 정리해서 제출해 주시기 바랍니다.",
          "<b>불승인율</b>이 2023년 <b>9.9%</b>에서 2025년 <b>22%</b>로 2배 이상 증가했는데 올해는 어떤지, 보증신청 승인 불승인 건수하고 불승인율도 같이 정리해서 제출해 주시기 바랍니다.",
          { request: { raised_by: "한영수", description: "주요 정책 보증 사업별 부실 현황 상세 명세, 불승인율 추이(2023~2026년)", type: "통계/현황", target: "경기신용보증재단", period: "2023~2026년", deadline: "감사 종료 전" },
            issue: { raised_by: "한영수", issue: "2년 연속 부채 중점기관 지정, 불승인율 9.9%→22% 급증", amounts: ["불승인율 22%"] } });
  say(27, "부실채권이 30% 넘는다고 하는데 코로나19 극복 특례보증하고 신용업 보증에서 특히 높은 부실률이라고 알고 있어요. 이 부분 사업별로 상세하게 정리해서 자료 제출해 주십시오.",
          "<b>부실채권</b>이 <b>30%</b> 넘는다고 하는데 <b>코로나19 극복 특례보증</b>하고 신용업 보증에서 특히 높은 <b>부실률</b>이라고 알고 있어요. 이 부분 사업별로 상세하게 정리해서 자료 제출해 주십시오.",
          { request: { raised_by: "한영수", description: "코로나19 특례보증·신용업 보증 부실률 현황, 부실채권 30% 관련 상세 자료", type: "통계", target: "경기신용보증재단", period: "2020~2026년" } });
  // ---- 여기까지(PRELOADED=14) 는 접속 시 한 번에 들어오고, 아래부터 실시간으로 한 문장씩 들어온다
  say(29, "경기신용보증재단 이사장입니다. 불승인율은 올해 10월 말 기준 18.4%로 작년보다는 낮아졌습니다. 심사 기준을 완화한 것이 아니라 사전 상담을 강화해서 신청 단계에서 걸러지는 건이 늘었습니다.",
          "<b>경기신용보증재단</b> 이사장입니다. <b>불승인율</b>은 올해 10월 말 기준 <b>18.4%</b>로 작년보다는 낮아졌습니다. 심사 기준을 완화한 것이 아니라 <b>사전 상담</b>을 강화해서 신청 단계에서 걸러지는 건이 늘었습니다.",
          { answer: { issueIndex: 3, response: "불승인율은 2026년 10월 말 18.4%로 낮아졌다. 사전 상담 강화로 신청 단계에서 걸러지는 건이 늘었다.", responded_by: "재단 이사장", status: "partial" } });
  say(30, "한영수 위원입니다. 사전 상담에서 걸러졌다는 건 결국 보증을 못 받았다는 얘기 아닙니까? 상담 단계 반송 건수까지 포함해서 다시 주세요.",
          "<b>한영수</b> 위원입니다. 사전 상담에서 걸러졌다는 건 결국 보증을 못 받았다는 얘기 아닙니까? 상담 단계 <b>반송 건수</b>까지 포함해서 다시 주세요.",
          { request: { raised_by: "한영수", description: "사전 상담 단계 반송 건수 포함 보증신청 처리 현황(2026년)", type: "통계", target: "경기신용보증재단", period: "2026년" } });
  say(32, "수원시 출신 최상규 위원입니다. 경기청년 기회사다리 금융 관련해서 대출금이 얼마나 되는지, 상환자 수, 손실보전액 이런 것들 현황 자료를 제출해 주시기 바랍니다.",
          "수원시 출신 <b>최상규</b> 위원입니다. <b>경기청년 기회사다리 금융</b> 관련해서 <b>대출금</b>이 얼마나 되는지, 상환자 수, <b>손실보전액</b> 이런 것들 현황 자료를 제출해 주시기 바랍니다.",
          { request: { raised_by: "최상규", description: "경기청년 기회사다리 금융 대출금·상환자 수·손실보전액 현황", type: "현황", target: "경기신용보증재단" }, summary: 2 });
  say(34, "경기신용보증재단 이사장입니다. 기회사다리 금융은 올해 9월 말까지 누적 1만 2천 명에게 1,240억 원을 지원했고, 손실보전은 38억 원 집행됐습니다. 상세 자료는 바로 제출하겠습니다.",
          "<b>경기신용보증재단</b> 이사장입니다. 기회사다리 금융은 올해 9월 말까지 누적 <b>1만 2천 명</b>에게 <b>1,240억 원</b>을 지원했고, <b>손실보전</b>은 <b>38억 원</b> 집행됐습니다. 상세 자료는 바로 제출하겠습니다.",
          { requestStatus: { description: "경기청년 기회사다리 금융 대출금·상환자 수·손실보전액 현황", status: "ongoing" } });
  say(37, "고양시 출신 송규근 위원입니다. 경기신보 외부감사 관련해서 질의합니다. 직원분들이 블라인드에 올리는 글은 익명성이 있고, 이것이 사실에 근거한 내용입니다. 신보 내부 문제가 심각한 것 아닙니까? 반송 요청율이 48%라고요.",
          "고양시 출신 <b>송규근</b> 위원입니다. 경기신보 <b>외부감사</b> 관련해서 질의합니다. 직원분들이 <b>블라인드</b>에 올리는 글은 익명성이 있고, 이것이 사실에 근거한 내용입니다. 신보 내부 문제가 심각한 것 아닙니까? <b>반송 요청율</b>이 <b>48%</b>라고요.",
          { issue: { raised_by: "송규근", issue: "반송 요청율 48%, 블라인드 직원 제보 기반 내부 운영 문제", amounts: ["반송율 48%"] } });
  say(38, "차세대 전산시스템 55억 사업이 좌초된 건, 업체 선정 때부터 잘못돼서 이런 일이 벌어졌습니다. 2회 유찰 후 가격이 싸서 1개 업체와만 합의했는데 금융 관련 전산 작업 경험이 부족한 업체였어요. 업체 선정 과정 자료하고 내부감사 결과 제출해 주십시오.",
          "<b>차세대 전산시스템</b> <b>55억</b> 사업이 좌초된 건, 업체 선정 때부터 잘못돼서 이런 일이 벌어졌습니다. 2회 유찰 후 가격이 싸서 1개 업체와만 합의했는데 금융 관련 전산 작업 경험이 부족한 업체였어요. <b>업체 선정 과정</b> 자료하고 <b>내부감사</b> 결과 제출해 주십시오.",
          { request: { raised_by: "송규근", description: "차세대 시스템 사업 진행 경과, 업체 선정 과정 자료, 내부감사 결과", type: "경과보고/감사결과", target: "경기신용보증재단", deadline: "11월 중" },
            issue: { raised_by: "송규근", issue: "차세대 전산시스템 55억 사업 좌초, 업체 선정 과정 문제", amounts: ["55억 원"] } });
  say(40, "경기신용보증재단 이사장입니다. 수행사의 PM이 교체되고 내부 다툼이 발생하여 계약기간을 초과했습니다. 법률자문에 따라 연장 불승인 후 조달청에 해지를 요청했습니다. 서울보증보험을 통해 약 7억 원을 회수했고 미회수 41.8억 원은 심사 진행 중입니다.",
          "<b>경기신용보증재단</b> 이사장입니다. 수행사의 PM이 교체되고 내부 다툼이 발생하여 계약기간을 초과했습니다. 법률자문에 따라 연장 불승인 후 조달청에 해지를 요청했습니다. 서울보증보험을 통해 약 <b>7억 원</b>을 회수했고 미회수 <b>41.8억 원</b>은 심사 진행 중입니다.",
          { answer: { issueIndex: 5, response: "수행사 PM 교체·내부 다툼으로 계약 초과. 법률자문에 따라 해지. 보증보험 7억 회수, 41.8억 심사 중.", responded_by: "재단 이사장", status: "partial", commitment: "서울보증보험 통해 41.8억 원 회수 심사 진행 중" } });
  say(41, "송규근 위원입니다. 외부감사 진행 경과를 의회에 수시로 보고하실 겁니까?", "<b>송규근</b> 위원입니다. <b>외부감사</b> 진행 경과를 의회에 수시로 보고하실 겁니까?",
          { issue: { raised_by: "송규근", issue: "외부감사 진행 경과 의회 보고 여부" } });
  say(42, "경기신용보증재단 이사장입니다. 네, 진행 경과를 의회에 수시로 보고하겠습니다.", "<b>경기신용보증재단</b> 이사장입니다. 네, 진행 경과를 의회에 수시로 보고하겠습니다.",
          { answer: { issueIndex: 6, response: "진행 경과를 의회에 수시로 보고하겠다.", responded_by: "재단 이사장", status: "answered", commitment: "외부감사 진행 경과 의회 수시 보고" }, summary: 3 });
  say(45, "안산시 출신 장윤정 위원입니다. 이번 신보 시스템 개선 사업은 경기신보와 중앙신보 시스템 연계를 위한 것이라고 하셨는데, 현재 연계 현황이 어떻게 되는지, 새로운 시스템 사업은 언제 추진할 계획인지 자료 제출 바랍니다.",
          "안산시 출신 <b>장윤정</b> 위원입니다. 이번 <b>신보 시스템</b> 개선 사업은 경기신보와 <b>중앙신보 시스템 연계</b>를 위한 것이라고 하셨는데, 현재 연계 현황이 어떻게 되는지, 새로운 시스템 사업은 언제 추진할 계획인지 자료 제출 바랍니다.",
          { request: { raised_by: "장윤정", description: "중앙신보 시스템 연계 현황, 새로운 시스템 사업 추진 계획", type: "계획/현황", target: "경기신용보증재단" } });
  say(47, "경기신용보증재단 이사장입니다. 재단중앙이음시스템은 올해 12월 1단계 연계를 마치고, 차세대 시스템 재추진은 내년 상반기 ISP를 거쳐 하반기에 발주할 계획입니다.",
          "<b>경기신용보증재단</b> 이사장입니다. <b>재단중앙이음시스템</b>은 올해 12월 1단계 연계를 마치고, 차세대 시스템 재추진은 내년 상반기 <b>ISP</b>를 거쳐 하반기에 발주할 계획입니다.");
  say(50, "고양시 출신 이기대 위원입니다. 경기도에서 신보에 출연한 출연금이 어떻게 집행이 되고 있는지, 앞으로 출연 계획은 어떤지 자료 주시고요. 재단 경영평가 결과도 같이 제출해 주십시오.",
          "고양시 출신 <b>이기대</b> 위원입니다. 경기도에서 신보에 <b>출연금</b>이 어떻게 집행이 되고 있는지, 앞으로 출연 계획은 어떤지 자료 주시고요. 재단 <b>경영평가</b> 결과도 같이 제출해 주십시오.",
          { request: { raised_by: "이기대", description: "신보 출연금 집행 현황 및 향후 출연 계획, 재단 경영평가 결과", type: "현황/계획", target: "경기신용보증재단", period: "2024~2027년" } });
  say(52, "한영수 위원입니다. 올해 불승인 사유를 유형별로 분류한 상세 자료 주시고, 취약 소상공인 특례보증, 영세 소상공인 특별보증 이런 것들 부실률이 각각 어떻게 되는지 정리해 주세요.",
          "<b>한영수</b> 위원입니다. 올해 <b>불승인 사유</b>를 유형별로 분류한 상세 자료 주시고, <b>취약 소상공인 특례보증</b>, <b>영세 소상공인 특별보증</b> 이런 것들 <b>부실률</b>이 각각 어떻게 되는지 정리해 주세요.",
          { request: { raised_by: "한영수", description: "2026년 불승인 사유별 상세 자료, 취약 소상공인 특례보증 부실률", type: "통계", target: "경기신용보증재단", period: "2026년" }, summary: 4 });
  say(55, "비례대표 송연섭 위원입니다. 공공에서 신청주의만 따르지 말고 온 도민이 골고루 혜택받을 수 있도록 해주십시오. 경기신보가 진정으로 소상공인을 위한 기관이 되어야 합니다.",
          "비례대표 <b>송연섭</b> 위원입니다. 공공에서 신청주의만 따르지 말고 온 도민이 골고루 혜택받을 수 있도록 해주십시오. <b>경기신보</b>가 진정으로 소상공인을 위한 기관이 되어야 합니다.");
  say(57, "경기신용보증재단 이사장입니다. 찾아가는 보증 상담을 31개 시군으로 확대하고, 모바일앱 이지원으로 비대면 신청 비중을 높여 보증 사각지대를 줄이겠습니다.",
          "<b>경기신용보증재단</b> 이사장입니다. <b>찾아가는 보증 상담</b>을 31개 시군으로 확대하고, 모바일앱 <b>이지원</b>으로 비대면 신청 비중을 높여 보증 사각지대를 줄이겠습니다.");
  say(60, "위원장 장태환입니다. 오전 질의는 여기까지 하고 정회하겠습니다. 요청하신 자료는 재단에서 정리해서 오후 속개 전까지 위원님들께 배부해 주시기 바랍니다. 오후 2시에 속개하겠습니다.",
          "위원장 <b>장태환</b>입니다. 오전 질의는 여기까지 하고 <b>정회</b>하겠습니다. 요청하신 자료는 재단에서 정리해서 오후 속개 전까지 위원님들께 배부해 주시기 바랍니다. 오후 2시에 속개하겠습니다.",
          { recess: true, summary: 5 });

  // ------------------------------------------------------------ AI 요약 스냅샷 (summary 이벤트 번호별)
  // 실제 시스템처럼 시간대별(hourly) 블록 + 누적 주제 + 전체 요약을 단계적으로 채운다.
  const HOUR_A = { hour: "10:00 ~ 11:00", hour_label: "오전" };
  const HOUR_B = { hour: "11:00 ~ 12:00", hour_label: "오전" };
  const SUMMARIES = {
    1: {
      hourly: [{
        ...HOUR_A, current_topic: "경기신보 위탁사업 관리·인력 구조·보증배수",
        recent: {
          key_statements: [
            { text: "보증잔액 13조 2천억, 신규 보증 4조 1천억, 대위변제 5,870억(전년 대비 8%↑)", sentence_ids: [3], type: "statement" },
            { text: "47개 위탁사업 566억 규모인데 회의자료 57개뿐, 회의록·수수료율 자료 요구", sentence_ids: [4, 5], type: "question" },
            { text: "회의록은 금주 내 일괄 제출하겠다", sentence_ids: [6], type: "answer" },
            { text: "비정규직 75%, 4년째 시정 안 됨. 시간제근로자 56명이 고객 민감정보 취급", sentence_ids: [7, 8], type: "question" },
            { text: "보증배수 서울 9.1배 vs 경기 6배, 소상공인 보증 접근성 문제", sentence_ids: [9], type: "question" },
            { text: "재보증 예산 부족, 인력 문제는 도와 협의하여 개선", sentence_ids: [10], type: "answer" },
          ],
          key_issues: ["위수탁사업 566억 관리 부실 (회의록 미비)", "비정규직 75% (4년째 미시정, 고객정보 취급 우려)", "보증배수 서울 대비 현저히 낮음 (6배 vs 9.1배)"],
          policies: ["위수탁사업", "재보증한도", "기본재산 보증배수"],
          numbers: ["대위변제 5,870억", "위탁사업 566억", "비정규직 75%", "정규직 377명", "보증배수 6배", "서울 9.1배"],
          qa_pairs: [
            { question: "위탁사업 566억 규모인데 회의자료가 57개뿐, 기본 행정기록 아닌가?", q_ids: [4, 5], answer: "송구하다. 운영위원회 회의록을 금주 내 일괄 정리해 제출하겠다.", a_ids: [6] },
            { question: "비정규직 75%를 4년째 지적하는데 시정이 안 됨. 시간제근로자가 고객 민감정보를 취급하는 것이 맞나?", q_ids: [7, 8], answer: "인력 문제는 도와 협의하여 개선해 나가겠다.", a_ids: [10] },
            { question: "보증배수가 서울 9.1배 대비 6배에 불과, 소상공인에게 보증이 덜 가는 것 아닌가?", q_ids: [9], answer: "중앙회 재보증한도를 쓰고 있으나 작년부터 재보증 예산이 부족하다.", a_ids: [10], follow_up: "재보증 예산 확보 방안 미제시" },
          ],
        },
      }],
      topics: [
        { title: "위탁사업 회의록·관리 부실", summary: "정용한 위원이 47개 위탁사업(566억 5천만 원)의 회의자료가 57개뿐이라고 지적. 이사장은 금주 내 회의록 일괄 제출을 약속.", active: true },
        { title: "인력 구조(비정규직 75%)와 보증배수", summary: "최민 위원이 4년째 시정되지 않는 비정규직 75% 구조와 시간제근로자의 고객정보 취급, 서울 대비 낮은 보증배수(6배 vs 9.1배)를 지적. 이사장은 재보증 예산 부족을 언급하며 도와 협의해 개선하겠다고 답변.", active: true },
      ],
      past: [], full_summary: "",
    },
  };
  SUMMARIES[2] = {
    hourly: [SUMMARIES[1].hourly[0], {
      ...HOUR_B, current_topic: "경기신보 재정 건전성 (부채 중점기관·불승인율·부실률)",
      recent: {
        key_statements: [
          { text: "소상공인연합회 상담 용역 상담일지·결과·후속조치 자료 요구 (경제실)", sentence_ids: [11], type: "question" },
          { text: "2년 연속 부채 중점기관 지정, 불승인율 9.9%→22% 급증, 사업별 부실 명세 요구", sentence_ids: [12, 13], type: "question" },
          { text: "부실채권 30% 초과, 코로나 특례보증·신용업 보증 부실률 상세 요구", sentence_ids: [14], type: "question" },
          { text: "불승인율 올해 10월 말 18.4%로 하락, 사전 상담 강화 효과", sentence_ids: [15], type: "answer" },
          { text: "사전 상담 반송 건수까지 포함해 다시 제출 요구", sentence_ids: [16], type: "question" },
          { text: "기회사다리 금융 대출금·상환자·손실보전액 현황 요구", sentence_ids: [17], type: "question" },
        ],
        key_issues: ["2년 연속 부채 중점기관 지정", "불승인율 22% → 18.4% (사전 상담 반송 포함 시 불명확)", "부실채권 30% 초과, 코로나 특례보증 부실"],
        policies: ["코로나19 극복 특례보증", "경기청년 기회사다리 금융", "사전 상담제"],
        numbers: ["불승인율 22%", "불승인율 18.4%", "부실채권 30%"],
        qa_pairs: [
          { question: "불승인율이 2023년 9.9%에서 2025년 22%로 급증했는데 올해는 어떤가?", q_ids: [12, 13], answer: "올해 10월 말 18.4%로 낮아졌다. 심사 완화가 아니라 사전 상담 강화로 신청 단계에서 걸러진 건이 늘었다.", a_ids: [15], follow_up: "사전 상담 반송 건수를 포함하면 실질 거절률은 더 높을 수 있음" },
          { question: "코로나 특례보증·신용업 보증 부실률을 사업별로 상세히 제출하라", q_ids: [14], answer: null, a_ids: [] },
          { question: "기회사다리 금융 대출금·상환자 수·손실보전액 현황은?", q_ids: [17], answer: null, a_ids: [] },
        ],
      },
    }],
    topics: [
      ...SUMMARIES[1].topics.map((t) => ({ ...t, active: false })),
      { title: "재정 건전성: 부채 중점기관·불승인율·부실률", summary: "한영수 위원이 2년 연속 부채 중점기관 지정과 불승인율 급증(9.9%→22%), 부실채권 30% 초과를 지적하고 사업별 부실 명세를 요구. 이사장은 올해 불승인율이 18.4%로 낮아졌다고 답했으나, 사전 상담 단계 반송을 포함한 재제출을 요구받음.", active: true },
    ],
    past: [SUMMARIES[1].topics[0]], full_summary: "",
  };
  SUMMARIES[3] = {
    hourly: [SUMMARIES[1].hourly[0], {
      ...HOUR_B, current_topic: "경기신보 차세대 전산시스템 좌초·외부감사",
      recent: {
        key_statements: [
          ...SUMMARIES[2].hourly[1].recent.key_statements,
          { text: "기회사다리 금융 누적 1만 2천 명·1,240억 지원, 손실보전 38억 집행", sentence_ids: [18], type: "answer" },
          { text: "반송 요청율 48%, 블라인드 직원 제보로 본 내부 운영 문제", sentence_ids: [19], type: "question" },
          { text: "차세대 시스템 55억 좌초, 업체 선정 과정 자료·내부감사 결과 요구", sentence_ids: [20], type: "question" },
          { text: "법률자문 따라 해지, 보증보험 7억 회수, 41.8억 심사 중", sentence_ids: [21], type: "answer" },
          { text: "외부감사 경과 의회 수시 보고 약속", sentence_ids: [23], type: "answer" },
        ],
        key_issues: [...SUMMARIES[2].hourly[1].recent.key_issues, "차세대 전산시스템 55억 사업 좌초·손실 회수 미흡", "반송 요청율 48% (내부 운영 문제)"],
        policies: [...SUMMARIES[2].hourly[1].recent.policies, "차세대 전산시스템", "외부감사"],
        numbers: [...SUMMARIES[2].hourly[1].recent.numbers, "반송율 48%", "55억 원 좌초", "7억 원 회수", "41.8억 원 미회수", "손실보전 38억 원"],
        qa_pairs: [
          ...SUMMARIES[2].hourly[1].recent.qa_pairs.slice(0, 2),
          { question: "기회사다리 금융 대출금·상환자 수·손실보전액 현황은?", q_ids: [17], answer: "9월 말 누적 1만 2천 명에게 1,240억 원 지원, 손실보전 38억 원 집행. 상세 자료 제출 예정.", a_ids: [18] },
          { question: "차세대 전산시스템 55억 사업이 좌초됐는데, 업체 선정부터 잘못된 것 아닌가?", q_ids: [20], answer: "수행사 PM 교체·내부 다툼으로 계약 초과. 법률자문에 따라 해지, 보증보험 7억 회수, 41.8억 심사 중", a_ids: [21], follow_up: "41.8억 원 회수 가능성과 책임 소재" },
          { question: "외부감사 진행 경과를 의회에 수시로 보고할 것인가?", q_ids: [22], answer: "네, 수시로 보고하겠다.", a_ids: [23] },
        ],
      },
    }],
    topics: [
      ...SUMMARIES[2].topics.map((t) => ({ ...t, active: false })),
      { title: "차세대 전산시스템 좌초와 외부감사", summary: "송규근 위원이 55억 원 차세대 시스템 사업 좌초 원인으로 업체 선정 과정을 지적하고 반송 요청율 48%를 들어 내부 운영 문제를 제기. 이사장은 법률자문에 따른 해지와 보증보험 7억 원 회수, 41.8억 원 심사 중임을 설명하고 외부감사 경과를 의회에 수시 보고하기로 약속.", active: true },
    ],
    past: SUMMARIES[2].topics.slice(0, 2), full_summary: "",
  };
  SUMMARIES[4] = {
    ...SUMMARIES[3],
    hourly: [SUMMARIES[1].hourly[0], {
      ...SUMMARIES[3].hourly[1], current_topic: "경기신보 시스템 연계·출연금·특례보증 부실률",
      recent: {
        ...SUMMARIES[3].hourly[1].recent,
        key_statements: [
          ...SUMMARIES[3].hourly[1].recent.key_statements,
          { text: "중앙신보 시스템 연계 현황·새 시스템 추진 계획 자료 요구", sentence_ids: [24], type: "question" },
          { text: "재단중앙이음시스템 12월 1단계 연계, 차세대 재추진은 내년 상반기 ISP 후 하반기 발주", sentence_ids: [25], type: "answer" },
          { text: "출연금 집행 현황·향후 출연 계획·경영평가 결과 요구", sentence_ids: [26], type: "question" },
          { text: "불승인 사유별 상세, 취약·영세 소상공인 특례보증 부실률 요구", sentence_ids: [27], type: "question" },
        ],
        policies: [...SUMMARIES[3].hourly[1].recent.policies, "재단중앙이음시스템", "취약 소상공인 특례보증"],
        qa_pairs: [
          ...SUMMARIES[3].hourly[1].recent.qa_pairs,
          { question: "중앙신보 시스템 연계 현황과 새 시스템 사업 추진 시점은?", q_ids: [24], answer: "이음시스템은 12월 1단계 연계 완료, 차세대 재추진은 내년 상반기 ISP 후 하반기 발주 계획", a_ids: [25] },
        ],
      },
    }],
    full_summary: "2026년 경제노동위원회 행정사무감사 오전 회의에서 경기신용보증재단에 대한 집중 질의가 이루어졌다. 정용한 위원은 47개 위탁사업(566억 5천만 원)의 회의자료가 57개뿐이라며 관리 부실을 지적했고, 이사장은 금주 내 회의록 제출을 약속했다. 최민 위원은 4년째 시정되지 않는 비정규직 75% 구조와 시간제근로자의 고객정보 취급, 서울(9.1배) 대비 낮은 보증배수(6배)를 문제 삼았다.\n\n한영수 위원은 2년 연속 부채 중점기관 지정과 불승인율 급증(9.9%→22%), 부실채권 30% 초과를 지적했다. 이사장은 올해 불승인율이 18.4%로 낮아졌다고 답했으나 사전 상담 단계 반송 건수를 포함한 재제출을 요구받았다. 송규근 위원은 차세대 전산시스템 55억 원 사업 좌초의 원인으로 업체 선정 과정을 지적했고, 이사장은 보증보험으로 7억 원을 회수했으며 41.8억 원은 심사 중이라고 답변했다.\n\n장윤정·이기대 위원은 각각 중앙신보 시스템 연계 현황과 출연금 집행·경영평가 자료를 요구했으며, 이사장은 재단중앙이음시스템 1단계 연계를 12월에 마치고 차세대 시스템은 내년 상반기 ISP를 거쳐 재추진하겠다고 밝혔다.",
  };
  SUMMARIES[5] = {
    ...SUMMARIES[4],
    hourly: [SUMMARIES[1].hourly[0], {
      ...SUMMARIES[4].hourly[1], current_topic: "오전 질의 종료·정회 (오후 2시 속개)",
      recent: {
        ...SUMMARIES[4].hourly[1].recent,
        key_statements: [
          ...SUMMARIES[4].hourly[1].recent.key_statements,
          { text: "신청주의 탈피, 도민이 골고루 혜택받는 보증 요구", sentence_ids: [28], type: "question" },
          { text: "찾아가는 보증 상담 31개 시군 확대, 모바일앱 이지원으로 비대면 신청 확대", sentence_ids: [29], type: "answer" },
          { text: "오전 질의 종료·정회, 요청자료는 오후 속개 전 배부", sentence_ids: [30], type: "statement" },
        ],
        policies: [...SUMMARIES[4].hourly[1].recent.policies, "찾아가는 보증 상담", "모바일앱 이지원"],
      },
    }],
    topics: SUMMARIES[4].topics.map((t) => ({ ...t, active: false })),
    past: SUMMARIES[4].topics,
  };

  // ------------------------------------------------------------ 상태
  const now0 = Date.now() / 1000;
  // 개회 시각: "지금"에서 PRELOADED번째 발언의 offset만큼 거슬러 올라간다 → 이미 지난 발언은 과거 시각, 이후 발언은 지금부터 흐른다
  const t0 = now0 - S[PRELOADED - 1].offsetMin * 60 - 20;
  const requests = [];
  const issues = [];
  let summaryNo = 0;
  let nextUpdateAt = now0 + 300;
  const recs = S.map((s, i) => ({
    id: i + 1, session: SESSION_ID, start: null, pts: null,
    broadcast_time: Math.round((t0 + s.offsetMin * 60) * 100) / 100,
    recognized_at: Math.round((t0 + s.offsetMin * 60) * 100) / 100,
    text: s.text, html: s.html,
  }));

  function summaryPayload() {
    const snap = SUMMARIES[summaryNo] || { hourly: [], topics: [], past: [], full_summary: "" };
    const updated = Date.now() / 1000;
    const hourly = snap.hourly.map((h, i) => ({
      ...h, updated_at: updated, next_update_at: nextUpdateAt,
      sentence_count: i === snap.hourly.length - 1 ? recs.length : 10,
    }));
    const last = hourly[hourly.length - 1];
    return {
      hourly,
      current_topic: last ? last.current_topic : "",
      recent: last ? last.recent : null,
      topics: snap.topics, past: snap.past, full_summary: snap.full_summary,
      updated_at: summaryNo ? updated : 0, next_update_at: nextUpdateAt,
      requests: requests.slice(), issues: issues.slice(),
    };
  }
  const pushSummary = () => handlers.summary(summaryPayload());

  // 이벤트 처리: 요청 감지(실제처럼 발언 몇 초 뒤 토스트와 함께), 지적 추가, 답변 반영, 요약 갱신
  function applyEvents(i, live) {
    const ev = S[i].ev;
    const rec = recs[i];
    const id = rec.id;
    const delay = (ms) => (live ? ms * SPEED : 0);
    if (ev.issue) {
      issues.push({ issue: ev.issue.issue, raised_by: ev.issue.raised_by, issue_ids: [id], amounts: ev.issue.amounts || [], response: null, responded_by: null, response_ids: [], status: "unanswered", commitment: null });
    }
    for (const a of [ev.answer, ev.answer2]) {
      if (!a) continue;
      const it = issues[a.issueIndex];
      if (it) Object.assign(it, { response: a.response, responded_by: a.responded_by, response_ids: [id], status: a.status, commitment: a.commitment || it.commitment });
    }
    if (ev.requestStatus) {
      const r = requests.find((x) => x.description === ev.requestStatus.description);
      if (r) r.status = ev.requestStatus.status;
    }
    if (ev.request) {
      const req = { ...ev.request, original_text: S[i].text, requested_at: rec.broadcast_time, sentence_ids: [id], status: "new" };
      const add = () => {
        requests.push(req);
        if (live) {
          // 실제 운영과 같은 경로: 요청 1건이 Realtime INSERT로 들어오면 토스트·알림이 울린다
          window._summaryBridge.addRequest(req);
          pushSummary();
        }
      };
      if (live) setTimeout(add, delay(2500)); else add();
    }
    if (ev.summary && live) {
      setTimeout(() => { summaryNo = ev.summary; nextUpdateAt = Date.now() / 1000 + 300; pushSummary(); }, delay(4000));
    } else if (ev.summary) {
      summaryNo = ev.summary;
    }
    if (ev.recess && live) {
      setTimeout(() => { handlers.status("정회 중 · 14:00 속개 예정"); }, delay(3000));
    }
  }

  // ------------------------------------------------------------ 재생
  function typePartial(text, done) {
    // 중간 인식 결과가 타이핑되듯 보이다가 확정 문장으로 바뀐다
    const chunks = Math.min(6, Math.max(2, Math.round(text.length / 25)));
    let k = 0;
    const step = () => {
      k += 1;
      handlers.partial(text.slice(0, Math.round((text.length * k) / (chunks + 1))) + " …");
      if (k < chunks) setTimeout(step, (350 + Math.random() * 300) * SPEED);
      else setTimeout(done, 500 * SPEED);
    };
    step();
  }

  function playFrom(i) {
    if (i >= S.length) return;
    const rec = recs[i];
    const gap = (BASE_GAP_S + (S[i].offsetMin - S[i - 1].offsetMin) * 1.2) * 1000 * SPEED;
    setTimeout(() => {
      typePartial(S[i].text, () => {
        const live = Math.round(Date.now() / 100) / 10;
        rec.broadcast_time = live; rec.recognized_at = live;
        handlers.sentence(rec);
        applyEvents(i, true);
        playFrom(i + 1);
      });
    }, gap);
  }

  // 1) 이미 지난 발언을 한 번에 넣는다 (서버의 hello와 같은 경로)
  for (let i = 0; i < PRELOADED; i++) applyEvents(i, false);
  handlers.hello({
    status: "행정사무감사 진행 중 (데모)",
    session: { id: SESSION_ID, relay: null },
    sentences: recs.slice(0, PRELOADED),
  });
  pushSummary();
  // 2) 그 뒤 발언은 실시간으로 흘려보낸다
  playFrom(PRELOADED);

  // 화면 표시: 제목 옆 데모 표시
  const sub = $("title");
  const tag = document.createElement("span");
  tag.textContent = "로컬 데모";
  tag.style.cssText = "margin-left:8px;padding:2px 8px;border-radius:999px;background:#f59e0b;color:#1f1300;font-size:11px;font-weight:700;vertical-align:middle";
  (sub || document.body).appendChild(tag);
  console.info("[demo] 로컬 데모 모드:", S.length + "문장", "속도 ×" + (1 / SPEED).toFixed(1));
})();
