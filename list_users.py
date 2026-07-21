"""텔레그램 봇 가입자 정보 조회 스크립트"""
import asyncio
from telegram import Bot
from user_manager import UserManager
import os
from dotenv import load_dotenv
import config

load_dotenv()

async def list_all_users():
    """등록된 모든 사용자 정보 조회"""
    print("======================================================================")
    print("텔레그램 봇 가입자 정보 조회")
    print("======================================================================")
    print()
    
    bot_token = config.TELEGRAM_BOT_TOKEN
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN이 .env 파일에 설정되지 않았습니다.")
        return
    
    user_manager = UserManager()
    user_ids = user_manager.get_all_users()
    
    if not user_ids:
        print("⚠️  등록된 사용자가 없습니다.")
        return
    
    print(f"📊 총 등록 사용자: {len(user_ids)}명")
    print()
    print("🔄 사용자 정보 조회 중...")
    print()
    
    bot = Bot(token=bot_token)
    
    # 봇 정보 확인
    try:
        bot_info = await bot.get_me()
        print(f"🤖 봇: @{bot_info.username} ({bot_info.first_name})")
        print()
    except Exception as e:
        print(f"⚠️  봇 정보 조회 실패: {e}")
        print()
    
    users_info = []
    failed_count = 0
    
    for i, chat_id in enumerate(user_ids, 1):
        try:
            # 사용자 정보 조회
            chat = await bot.get_chat(chat_id)
            
            user_info = {
                'chat_id': chat_id,
                'first_name': chat.first_name or 'N/A',
                'last_name': chat.last_name or '',
                'username': chat.username or 'N/A',
                'type': chat.type
            }
            users_info.append(user_info)
            
            # 실시간 출력
            name = f"{chat.first_name or ''} {chat.last_name or ''}".strip() or '이름 없음'
            username = f"@{chat.username}" if chat.username else "N/A"
            print(f"  {i:2d}. {name:30s} ({username:20s}) - ID: {chat_id}")
            
        except Exception as e:
            failed_count += 1
            error_msg = str(e)
            if "chat not found" in error_msg.lower():
                print(f"  {i:2d}. ❌ 사용자를 찾을 수 없음 (ID: {chat_id}) - 봇을 차단했거나 삭제된 계정일 수 있습니다")
            else:
                print(f"  {i:2d}. ❌ 조회 실패 (ID: {chat_id}): {error_msg}")
    
    print()
    print("======================================================================")
    print("조회 결과 요약")
    print("======================================================================")
    print(f"✅ 성공: {len(users_info)}명")
    if failed_count > 0:
        print(f"❌ 실패: {failed_count}명")
    print()
    
    # 상세 정보 출력
    if users_info:
        print("📋 상세 정보:")
        print()
        for i, user in enumerate(users_info, 1):
            name = f"{user['first_name']} {user['last_name']}".strip() or '이름 없음'
            username = user['username']
            print(f"{i}. {name}")
            print(f"   사용자명: {username}")
            print(f"   채팅 ID: {user['chat_id']}")
            print(f"   타입: {user['type']}")
            print()

def main():
    asyncio.run(list_all_users())

if __name__ == "__main__":
    main()

