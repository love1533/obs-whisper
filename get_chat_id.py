"""텔레그램 채팅 ID 확인 스크립트"""
import asyncio
from telegram import Bot
import os
from dotenv import load_dotenv

load_dotenv()

async def get_chat_id():
    """봇에게 메시지를 보낸 후 채팅 ID를 확인"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN이 .env 파일에 설정되지 않았습니다.")
        return
    
    bot = Bot(token=bot_token)
    
    try:
        # 봇 정보 확인
        bot_info = await bot.get_me()
        print(f"✅ 봇 연결 성공: @{bot_info.username}")
        print(f"   봇 이름: {bot_info.first_name}")
        print()
        print("📝 채팅 ID를 확인하려면:")
        print("   1. 텔레그램에서 봇(@Gcgf2025_bot)에게 아무 메시지나 보내세요")
        print("   2. 아래 명령어로 업데이트를 확인하세요:")
        print("      python3 get_chat_id.py")
        print()
        
        # 최근 업데이트 확인
        updates = await bot.get_updates(limit=10)
        
        if updates:
            print("📨 최근 메시지:")
            for update in updates:
                if update.message:
                    chat = update.message.chat
                    print(f"   채팅 ID: {chat.id}")
                    print(f"   사용자: {chat.first_name} {chat.last_name or ''}")
                    print(f"   사용자명: @{chat.username or 'N/A'}")
                    print(f"   메시지: {update.message.text or '(이미지/파일 등)'}")
                    print()
                    
                    # 첫 번째 채팅 ID를 .env에 자동 저장
                    chat_id = chat.id
                    print(f"💾 채팅 ID {chat_id}를 .env 파일에 저장하시겠습니까? (y/n): ", end="")
                    # 자동으로 저장
                    save_to_env(chat_id)
                    return chat_id
        else:
            print("⚠️  아직 봇에게 메시지를 보내지 않았습니다.")
            print("   봇에게 메시지를 보낸 후 다시 실행하세요.")
            
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        if "Unauthorized" in str(e):
            print("   봇 토큰이 유효하지 않습니다. .env 파일을 확인하세요.")

def save_to_env(chat_id):
    """채팅 ID를 .env 파일에 저장"""
    env_file = ".env"
    
    # .env 파일 읽기
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            lines = f.readlines()
        
        # TELEGRAM_CHAT_ID 업데이트
        updated = False
        with open(env_file, 'w') as f:
            for line in lines:
                if line.startswith("TELEGRAM_CHAT_ID="):
                    f.write(f"TELEGRAM_CHAT_ID={chat_id}\n")
                    updated = True
                else:
                    f.write(line)
            
            if not updated:
                f.write(f"TELEGRAM_CHAT_ID={chat_id}\n")
        
        print(f"✅ 채팅 ID {chat_id}가 .env 파일에 저장되었습니다!")
    else:
        print("⚠️  .env 파일을 찾을 수 없습니다.")

def main():
    """메인 함수"""
    asyncio.run(get_chat_id())

if __name__ == "__main__":
    main()


