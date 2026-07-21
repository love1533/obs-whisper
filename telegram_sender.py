"""텔레그램 메시지 전송 모듈"""
import asyncio
from telegram import Bot
from typing import Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TelegramSender:
    """텔레그램 메시지 전송 클래스"""
    
    def __init__(self, bot_token: str, chat_id: str = None, chat_ids: list = None):
        """
        Args:
            bot_token: 텔레그램 봇 토큰
            chat_id: 단일 채팅 ID (하위 호환성)
            chat_ids: 채팅 ID 목록 (브로드캐스트용)
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.chat_ids = chat_ids if chat_ids else ([chat_id] if chat_id else [])
        self.bot: Optional[Bot] = None
        self._initialize_bot()
    
    def _initialize_bot(self):
        """봇 초기화"""
        try:
            self.bot = Bot(token=self.bot_token)
            logger.info("텔레그램 봇이 초기화되었습니다.")
        except Exception as e:
            logger.error(f"텔레그램 봇 초기화 오류: {e}")
            raise
    
    async def send_message(self, text: str, chat_id: int = None) -> bool:
        """
        텔레그램으로 메시지 전송
        
        Args:
            text: 전송할 텍스트
            chat_id: 특정 채팅 ID (None이면 chat_ids 목록 사용)
            
        Returns:
            전송 성공 여부
        """
        if not text or not text.strip():
            return False
        
        target_ids = [chat_id] if chat_id else self.chat_ids
        
        if not target_ids:
            logger.warning("전송할 채팅 ID가 없습니다.")
            return False
        
        success_count = 0
        for cid in target_ids:
            try:
                await self.bot.send_message(
                    chat_id=cid,
                    text=text,
                    parse_mode="HTML"
                )
                success_count += 1
            except Exception as e:
                logger.error(f"메시지 전송 오류 (chat_id={cid}): {e}")
        
        if success_count > 0:
            logger.info(f"메시지 전송 성공: {success_count}/{len(target_ids)}명 ({text[:50]}...)")
            return True
        return False
    
    @staticmethod
    def _is_dead_user(err: str) -> bool:
        """영구적으로 도달 불가한 사용자인지(차단/탈퇴/삭제) 판단"""
        e = err.lower()
        return any(k in e for k in (
            "blocked", "deactivated", "chat not found",
            "user is deleted", "bot can't initiate", "kicked",
        ))

    async def broadcast_message(self, text: str, chat_ids: list) -> dict:
        """
        여러 사용자에게 동시(병렬) 브로드캐스트

        Args:
            text: 전송할 텍스트
            chat_ids: 채팅 ID 목록

        Returns:
            {'success', 'failed', 'total', 'dead': [영구 도달불가 chat_id...]}
        """
        if not text or not text.strip() or not chat_ids:
            return {'success': 0, 'failed': 0, 'total': 0, 'dead': []}

        async def send_one(cid):
            try:
                await self.bot.send_message(chat_id=cid, text=text, parse_mode="HTML")
                return (cid, True, None)
            except Exception as e:
                return (cid, False, str(e))

        # 모든 사용자에게 동시 전송 (순차 → 병렬로 지연 대폭 감소)
        results = await asyncio.gather(*[send_one(c) for c in chat_ids])

        success_count = 0
        failed_count = 0
        dead = []
        for cid, ok, err in results:
            if ok:
                success_count += 1
            else:
                failed_count += 1
                if self._is_dead_user(err):
                    dead.append(cid)
                else:
                    logger.error(f"브로드캐스트 오류 (chat_id={cid}): {err}")

        logger.info(f"브로드캐스트 완료: {success_count}명 성공, {failed_count}명 실패 (전체 {len(chat_ids)}명)")
        return {
            'success': success_count,
            'failed': failed_count,
            'total': len(chat_ids),
            'dead': dead,
        }
    
    def send_message_sync(self, text: str, chat_id: int = None) -> bool:
        """
        동기 방식으로 메시지 전송
        
        Args:
            text: 전송할 텍스트
            chat_id: 특정 채팅 ID (None이면 chat_ids 목록 사용)
            
        Returns:
            전송 성공 여부
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.send_message(text, chat_id))
    
    def broadcast_message_sync(self, text: str, chat_ids: list) -> dict:
        """
        동기 방식으로 브로드캐스트
        
        Args:
            text: 전송할 텍스트
            chat_ids: 채팅 ID 목록
            
        Returns:
            {'success': 성공 수, 'failed': 실패 수, 'total': 전체 수}
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.broadcast_message(text, chat_ids))

