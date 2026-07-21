"""사용자 목록 관리 모듈"""
import json
import os
from typing import List, Set


class UserManager:
    """텔레그램 사용자 목록 관리 클래스"""
    
    def __init__(self, users_file: str = "telegram_users.json"):
        """
        Args:
            users_file: 사용자 목록을 저장할 파일 경로
        """
        self.users_file = users_file
        self._users: Set[int] = set()
        self._load_users()
    
    def _load_users(self):
        """파일에서 사용자 목록 로드.

        읽기/파싱 실패 시(동시 쓰기 중 등) 빈 목록으로 덮어쓰지 않고 기존 목록을
        유지한다. 순간적으로 빈 목록이 되면 기존 사용자가 '신규'로 재감지되어
        알림이 반복되는 문제를 막는다.
        """
        if os.path.exists(self.users_file):
            try:
                with open(self.users_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._users = set(data.get('users', []))
            except Exception as e:
                # 기존 self._users 유지 (빈 목록으로 리셋하지 않음)
                print(f"⚠️  사용자 목록 로드 오류(기존 목록 유지): {e}")
        # 파일이 없으면 기존 목록 유지 (최초 생성 시엔 빈 set)
    
    def reload(self):
        """파일에서 사용자 목록을 다시 읽어 최신 상태로 동기화

        UserChecker(별도 인스턴스)나 update_users.py가 방송 중에 새 사용자를
        JSON에 추가할 수 있으므로, 파일을 단일 소스로 삼아 매번 최신화한다.
        """
        self._load_users()

    def add_user(self, chat_id: int) -> bool:
        """
        사용자 추가

        Args:
            chat_id: 텔레그램 채팅 ID

        Returns:
            새로 추가된 사용자인지 여부 (True: 새 사용자, False: 기존 사용자)
        """
        # 다른 인스턴스가 추가한 사용자를 덮어쓰지 않도록 먼저 최신화
        self._load_users()
        is_new = chat_id not in self._users
        self._users.add(chat_id)
        if is_new:
            self._save_users()
        return is_new
    
    def remove_user(self, chat_id: int) -> bool:
        """
        사용자 제거

        Args:
            chat_id: 텔레그램 채팅 ID

        Returns:
            제거 성공 여부
        """
        self._load_users()
        if chat_id in self._users:
            self._users.remove(chat_id)
            self._save_users()
            return True
        return False
    
    def _save_users(self):
        """사용자 목록을 원자적으로 저장 (임시파일 → os.replace).

        os.replace는 원자적이므로, 동시에 읽는 쪽(다른 스레드/프로세스)이 절대
        반쯤 쓰인 파일을 보지 않는다(항상 이전 또는 새 완성본만 관찰).
        """
        tmp = f"{self.users_file}.tmp"
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'users': list(self._users)}, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.users_file)
        except Exception as e:
            print(f"⚠️  사용자 목록 저장 오류: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    
    def get_all_users(self) -> List[int]:
        """모든 사용자 ID 목록 반환 (파일에서 최신 상태로 읽음)"""
        self._load_users()
        return list(self._users)

    def get_user_count(self) -> int:
        """등록된 사용자 수 반환 (파일에서 최신 상태로 읽음)"""
        self._load_users()
        return len(self._users)

