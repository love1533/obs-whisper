"""텍스트 포맷팅 모듈 - 키워드 강조"""
import re
from typing import List


class TextFormatter:
    """텍스트 포맷팅 클래스"""
    
    def __init__(self, keywords: List[str] = None):
        """
        Args:
            keywords: 강조할 키워드 목록
        """
        # 기본 키워드 목록
        self.keywords = keywords or [
            "보증",
            "부실",
            "차세대",
            "경기신용보증재단",
            "경기신보",
            "신보",
            "신용보증재단",
            "신용재단",
            "경기도",
            "예산",
            "대위변제",
            "새출발기금",
            "감사"
        ]
        # 키워드를 길이 순으로 정렬 (긴 키워드부터 매칭)
        self.keywords.sort(key=len, reverse=True)
    
    def format_text(self, text: str) -> str:
        """
        텍스트에서 키워드를 찾아 볼드체로 강조
        
        Args:
            text: 원본 텍스트
            
        Returns:
            포맷팅된 텍스트 (HTML 형식)
        """
        if not text:
            return text
        
        # 모든 키워드를 하나의 패턴으로 한 번에 치환 (긴 키워드 우선).
        # 키워드별로 차례로 치환하면 '경기신용보증재단' 안의 '보증'이 다시 감싸져
        # <b>가 중첩되므로 한 번만 훑는다.
        pattern = re.compile("|".join(re.escape(k) for k in self.keywords), re.IGNORECASE)
        formatted_text = pattern.sub(lambda m: f"<b>{m.group(0)}</b>", text)

        return formatted_text
    
    def is_keyword_dump(self, text: str) -> bool:
        """텍스트가 사실상 '키워드 나열'(프롬프트 echo/환각)인지 판단.

        키워드를 3개 이상 포함하고, 키워드·구두점·공백을 제거했을 때 남는 실질
        내용이 거의 없으면 echo로 보고 True를 반환한다. 일반 발화가 키워드를
        한두 개 언급하는 경우는 걸러지지 않는다.
        """
        if not text:
            return False
        kw_hits = sum(1 for kw in self.keywords if kw in text)
        if kw_hits < 3:
            return False
        stripped = text
        for kw in self.keywords:
            stripped = stripped.replace(kw, "")
        stripped = re.sub(r"[\s,./·、'\"()]+", "", stripped)
        return len(stripped) <= 4

    def add_keyword(self, keyword: str):
        """새 키워드 추가"""
        if keyword not in self.keywords:
            self.keywords.append(keyword)
            self.keywords.sort(key=len, reverse=True)
    
    def remove_keyword(self, keyword: str):
        """키워드 제거"""
        if keyword in self.keywords:
            self.keywords.remove(keyword)

