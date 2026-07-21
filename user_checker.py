"""주기적으로 사용자 확인 및 등록 모듈"""
import asyncio
from telegram import Bot
import os
from dotenv import load_dotenv
from user_manager import UserManager
from telegram_sender import TelegramSender
import config
import time

load_dotenv()


class UserChecker:
    """주기적으로 봇 사용자를 확인하고 등록하는 클래스"""
    
    def __init__(self, check_interval_minutes: int = 1, ui_callback=None):
        """
        Args:
            check_interval_minutes: 사용자 확인 간격 (분)
            ui_callback: UI 업데이트 콜백 함수 (user_count를 인자로 받음)
        """
        self.check_interval = check_interval_minutes * 60  # 초로 변환
        self.user_manager = UserManager()
        self.bot_token = config.TELEGRAM_BOT_TOKEN
        self.bot: Bot = None
        self.telegram_sender = TelegramSender(
            bot_token=config.TELEGRAM_BOT_TOKEN,
            chat_ids=self.user_manager.get_all_users()
        )
        self.ui_callback = ui_callback
        self.is_running = False
        # 처리한 업데이트 커서 (같은 업데이트를 반복 처리·알림하지 않도록)
        self._offset = None
    
    async def _initialize_bot(self):
        """봇 초기화"""
        if not self.bot:
            self.bot = Bot(token=self.bot_token)
    
    async def check_new_users(self) -> dict:
        """
        새로운 사용자 확인 및 등록
        
        Returns:
            {'new_users': 새 사용자 수, 'total_users': 전체 사용자 수, 'new_user_list': 새 사용자 정보 리스트}
        """
        await self._initialize_bot()

        # offset 이후의 새 업데이트만 확인하고, 처리한 만큼 커서를 전진시켜
        # 같은 업데이트가 매번 다시 반환되지 않게 한다(반복 알림 방지).
        updates = await self.bot.get_updates(offset=self._offset, limit=100, timeout=0)

        new_users = []
        existing_count = 0

        if updates:
            for update in updates:
                # 이 업데이트는 처리 완료 → 다음 조회에서 제외되도록 커서 전진
                self._offset = update.update_id + 1

                if update.message:
                    chat = update.message.chat
                    chat_id = chat.id
                    user_name = f"{chat.first_name} {chat.last_name or ''}".strip()
                    username = f"@{chat.username}" if chat.username else "N/A"

                    is_new = self.user_manager.add_user(chat_id)

                    if is_new:
                        new_users.append({
                            'chat_id': chat_id,
                            'name': user_name,
                            'username': username
                        })
                    else:
                        existing_count += 1
        
        total_users = self.user_manager.get_user_count()
        
        return {
            'new_users': len(new_users),
            'total_users': total_users,
            'new_user_list': new_users,
            'existing_users': existing_count
        }
    
    async def broadcast_new_users_notification(self, result: dict):
        """새로 등록된 사용자 정보를 전체 사용자에게 브로드캐스트"""
        user_ids = self.user_manager.get_all_users()
        
        if not user_ids:
            return
        
        if result['new_users'] > 0:
            notification = "🆕 새 사용자 등록 알림\n\n"
            notification += f"새로 등록된 사용자: {result['new_users']}명\n"
            notification += f"전체 등록 사용자: {result['total_users']}명\n\n"
            
            for user in result['new_user_list']:
                notification += f"• {user['name']} ({user['username']})\n"
            
            # 모든 사용자에게 브로드캐스트
            await self.telegram_sender.broadcast_message(notification, user_ids)
    
    async def run_periodic_check(self):
        """주기적으로 사용자 확인"""
        self.is_running = True
        
        print(f"🔄 사용자 확인 시작 (간격: {self.check_interval // 60}분)")
        
        while self.is_running:
            try:
                print(f"\n[{time.strftime('%H:%M:%S')}] 사용자 확인 중...")
                result = await self.check_new_users()
                
                print(f"  📊 전체 사용자: {result['total_users']}명")
                print(f"  🆕 새 사용자: {result['new_users']}명")
                print(f"  📌 기존 사용자: {result['existing_users']}명")
                
                # UI 업데이트 콜백 호출
                if self.ui_callback:
                    try:
                        self.ui_callback(result['total_users'])
                    except:
                        pass
                
                if result['new_users'] > 0:
                    print(f"\n  새로 등록된 사용자:")
                    for user in result['new_user_list']:
                        print(f"    ✅ {user['name']} ({user['username']}) - ID: {user['chat_id']}")
                    
                    # 전체 사용자에게 새 사용자 알림 브로드캐스트
                    await self.broadcast_new_users_notification(result)
                
            except Exception as e:
                print(f"  ❌ 사용자 확인 오류: {e}")
                import traceback
                traceback.print_exc()
            
            # 다음 확인까지 대기
            await asyncio.sleep(self.check_interval)
    
    def start(self):
        """사용자 확인 시작 (비동기)"""
        asyncio.create_task(self.run_periodic_check())
    
    def stop(self):
        """사용자 확인 중지"""
        self.is_running = False


async def check_users_once():
    """한 번만 사용자 확인 (스크립트용)"""
    checker = UserChecker()
    result = await checker.check_new_users()
    
    print("=" * 70)
    print("사용자 확인 결과")
    print("=" * 70)
    print(f"전체 등록 사용자: {result['total_users']}명")
    print(f"새로 등록된 사용자: {result['new_users']}명")
    print(f"기존 사용자: {result['existing_users']}명")
    
    if result['new_users'] > 0:
        print("\n새로 등록된 사용자 목록:")
        for user in result['new_user_list']:
            print(f"  ✅ {user['name']} ({user['username']}) - ID: {user['chat_id']}")
    
    print("=" * 70)
    
    return result


def main():
    """메인 함수 (스크립트 실행용)"""
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        # 한 번만 확인
        asyncio.run(check_users_once())
    else:
        # 주기적으로 확인
        checker = UserChecker(check_interval_minutes=1)
        print("사용자 확인을 시작합니다. 1분마다 자동으로 확인합니다.")
        print("종료하려면 Ctrl+C를 누르세요.")
        try:
            asyncio.run(checker.run_periodic_check())
        except KeyboardInterrupt:
            print("\n사용자 확인을 중지합니다.")
            checker.stop()


if __name__ == "__main__":
    main()

