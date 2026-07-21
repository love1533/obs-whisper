"""텔레그램 봇 사용자 목록 최신화 스크립트"""
import asyncio
from telegram import Bot
import os
from dotenv import load_dotenv
from user_manager import UserManager
import config

load_dotenv()


async def update_all_users():
    """봇에 메시지를 보낸 모든 사용자 확인 및 등록"""
    bot_token = config.TELEGRAM_BOT_TOKEN
    
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN이 설정되지 않았습니다.")
        return
    
    try:
        bot = Bot(token=bot_token)
        user_manager = UserManager()
        
        print("=" * 70)
        print("텔레그램 봇 사용자 목록 최신화")
        print("=" * 70)
        
        # 기존 사용자 수
        old_count = user_manager.get_user_count()
        print(f"\n📊 기존 등록 사용자: {old_count}명")
        
        # 최대 100개의 최근 업데이트 확인
        print("\n🔄 최근 메시지를 확인하여 사용자를 등록 중...")
        updates = await bot.get_updates(limit=100)
        
        new_users = []
        existing_users = []
        
        if updates:
            print(f"   발견된 업데이트: {len(updates)}개")
            
            for update in updates:
                if update.message:
                    chat = update.message.chat
                    chat_id = chat.id
                    user_name = f"{chat.first_name} {chat.last_name or ''}".strip()
                    username = f"@{chat.username}" if chat.username else "N/A"
                    
                    is_new = user_manager.add_user(chat_id)
                    
                    if is_new:
                        new_users.append({
                            'chat_id': chat_id,
                            'name': user_name,
                            'username': username
                        })
                        print(f"   ✅ 새 사용자 등록: {user_name} ({username}) - ID: {chat_id}")
                    else:
                        existing_users.append({
                            'chat_id': chat_id,
                            'name': user_name,
                            'username': username
                        })
        else:
            print("   ⚠️  최근 메시지가 없습니다.")
        
        # 결과 출력
        print("\n" + "=" * 70)
        print("최신화 결과")
        print("=" * 70)
        
        new_count = user_manager.get_user_count()
        print(f"📊 기존 사용자: {old_count}명")
        print(f"🆕 새로 등록된 사용자: {len(new_users)}명")
        print(f"📌 전체 등록 사용자: {new_count}명")
        
        if new_users:
            print("\n새로 등록된 사용자 목록:")
            for user in new_users:
                print(f"  • {user['name']} ({user['username']}) - ID: {user['chat_id']}")
        
        if existing_users:
            print(f"\n기존 사용자: {len(existing_users)}명 (이미 등록됨)")
        
        print("\n" + "=" * 70)
        print(f"✅ 사용자 목록이 최신화되었습니다! (총 {new_count}명)")
        print("=" * 70)
        
        # 등록된 모든 사용자 목록 출력
        all_users = user_manager.get_all_users()
        print(f"\n📋 현재 등록된 모든 사용자 ID:")
        for i, user_id in enumerate(all_users, 1):
            print(f"   {i}. {user_id}")
        
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()


def main():
    """메인 함수"""
    try:
        asyncio.run(update_all_users())
    except KeyboardInterrupt:
        print("\n\n사용자에 의해 중단되었습니다.")
    except Exception as e:
        print(f"\n❌ 실행 오류: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()


