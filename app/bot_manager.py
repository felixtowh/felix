"""
Bot Manager — 管理所有 Bot Worker 的生命周期
"""
import logging

from app import database as db
from app.bot_worker import BotWorker

logger = logging.getLogger("bot_manager")


class BotManager:
    """管理所有 Bot Worker 的生命周期"""

    def __init__(self):
        self.workers: dict[int, BotWorker] = {}

    def start_all(self):
        """启动所有已启用的 Bot"""
        bots = db.list_wecom_bots(enabled_only=True)
        for bot in bots:
            self._start_one(bot)
        logger.info("BotManager: 已启动 %d 个 Bot", len(self.workers))

    def stop_all(self):
        for pk in list(self.workers.keys()):
            self.stop_bot(pk)

    def _start_one(self, bot: dict):
        pk = bot["id"]
        if pk in self.workers:
            self.stop_bot(pk)
        worker = BotWorker(pk, bot["bot_id"], bot["secret"])
        self.workers[pk] = worker
        worker.start()
        logger.info("BotManager: Bot#%d (%s) 已启动", pk, bot.get("name", ""))

    def start_bot(self, bot_pk: int):
        bot = db.get_wecom_bot(bot_pk)
        if not bot:
            logger.warning("BotManager: Bot#%d 不存在", bot_pk)
            return
        if not bot.get("enabled"):
            logger.warning("BotManager: Bot#%d 已禁用，不启动", bot_pk)
            return
        self._start_one(bot)

    def stop_bot(self, bot_pk: int):
        worker = self.workers.pop(bot_pk, None)
        if worker:
            worker.stop()
            logger.info("BotManager: Bot#%d 已停止", bot_pk)

    def restart_bot(self, bot_pk: int):
        self.stop_bot(bot_pk)
        self.start_bot(bot_pk)

    def get_status(self) -> list[dict]:
        bots = db.list_wecom_bots()
        result = []
        for bot in bots:
            pk = bot["id"]
            worker = self.workers.get(pk)
            result.append({
                "id": pk,
                "name": bot.get("name", ""),
                "bot_id": bot.get("bot_id", ""),
                "enabled": bool(bot.get("enabled")),
                "status": bot.get("status", "stopped"),
                "connected": worker.connected if worker else False,
            })
        return result

    def is_running(self, bot_pk: int) -> bool:
        worker = self.workers.get(bot_pk)
        return worker is not None and worker.connected


# 全局单例
bot_manager = BotManager()
