"""
Bot Worker — 每个企微 Bot 对应一个 worker，
管理 WebSocket 连接 → 接收消息 → 调用绑定的 Dify → 回复
"""
import asyncio
import json as _json_mod
import logging
import re
import httpx

from app.wecom_bot import WeComClient
from app import database as db

logger = logging.getLogger(__name__)


def _strip_thinking(text: str) -> str:
    """去掉 Dify 返回的 <think>...</think> 块"""
    t = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    idx = t.find('<think>')
    if idx >= 0:
        t = t[:idx]
    return t.strip()


async def _get_default_inputs(api_key: str, base_url: str, cache: dict) -> dict:
    """自动获取 Dify 应用的必填输入参数"""
    cid = f"{base_url}:{api_key[:8]}"
    if cid in cache:
        return dict(cache[cid])
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            resp = await cli.get(
                f"{base_url.rstrip('/')}/parameters",
                headers={"Authorization": f"Bearer {api_key}"}
            )
            if resp.status_code == 200:
                params = resp.json()
                defaults = {}
                for field in params.get("user_input_form", []):
                    for _, cfg in field.items():
                        var = cfg.get("variable")
                        if var and cfg.get("required"):
                            defaults[var] = cfg.get("default") or (cfg.get("options", [None])[0] or "")
                cache[cid] = defaults
                logger.info("Dify inputs detected for %s: %s", cid, defaults)
                return dict(defaults)
    except Exception as e:
        logger.warning("Cannot fetch Dify params for %s: %s", cid, e)
    cache[cid] = {}
    return {}


async def _chat_blocking(query: str, user: str, api_key: str, base_url: str,
                         conversation_id: str | None = None, inputs_cache: dict | None = None) -> dict:
    """阻塞模式调用 Dify"""
    cache = inputs_cache if inputs_cache is not None else {}
    inputs = await _get_default_inputs(api_key, base_url, cache)
    payload = {
        "inputs": inputs,
        "query": query,
        "response_mode": "blocking",
        "conversation_id": conversation_id or "",
        "user": user,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as cli:
        resp = await cli.post(
            f"{base_url.rstrip('/')}/chat-messages",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        if resp.status_code != 200:
            body_str = (await resp.aread()).decode()[:300]
            logger.error("Dify blocking returned %d: %s", resp.status_code, body_str)
            return {"error": f"Dify returned {resp.status_code}", "answer": ""}
        return resp.json()


class BotWorker:
    """管理单个 Bot 的生命周期"""

    def __init__(self, bot_pk: int, bot_id: str, secret: str):
        self.bot_pk = bot_pk
        self.bot_id = bot_id
        self.secret = secret
        self.client = WeComClient(bot_pk, bot_id, secret)
        self._task: asyncio.Task | None = None
        self._inputs_cache: dict = {}

    @property
    def connected(self) -> bool:
        return self.client.connected

    def start(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    async def _run(self):
        """Bot 主循环：连接 → 接收消息 → 重连"""
        db.update_wecom_bot(self.bot_pk, status="connecting")
        while True:
            try:
                ok = await self.client.connect()
                if not ok:
                    db.update_wecom_bot(self.bot_pk, status="auth_failed")
                    logger.warning("[Bot#%d] 认证失败，5s 后重连...", self.bot_pk)
                    await asyncio.sleep(5)
                    continue

                db.update_wecom_bot(self.bot_pk, status="connected")
                logger.info("[Bot#%d] WebSocket 已连接", self.bot_pk)
                hb = asyncio.create_task(self.client.heartbeat(30))

                try:
                    while True:
                        try:
                            frame = await self.client.recv()
                        except Exception as e:
                            logger.warning("[Bot#%d] recv loop break: %s", self.bot_pk, e)
                            break

                        if frame is None:
                            continue

                        cmd = frame.get("cmd", "")
                        if cmd == "aibot_msg_callback":
                            body = frame.get("body", {})
                            req_id = frame.get("headers", {}).get("req_id", "")
                            await self._handle_message(req_id, body)
                        elif cmd == "aibot_event_callback":
                            body = frame.get("body", {})
                            event = body.get("event", {})
                            event_type = event.get("event_type", "")
                            if event_type == "enter_chat":
                                logger.info("[Bot#%d] 用户进入会话: %s", self.bot_pk,
                                            body.get("from", {}).get("userid"))
                            elif event_type == "aibot_rating":
                                rating = event.get("rating", "")
                                user_id = body.get("from", {}).get("userid", "")
                                logger.info("[Bot#%d] RATING: user=%s rating=%s",
                                            self.bot_pk, user_id, rating)
                                asyncio.create_task(self._forward_rating(user_id, rating))
                finally:
                    hb.cancel()
            except asyncio.CancelledError:
                db.update_wecom_bot(self.bot_pk, status="stopped")
                break
            except Exception as e:
                logger.error("[Bot#%d] Worker error: %s", self.bot_pk, e)
            finally:
                await self.client.disconnect()
                db.update_wecom_bot(self.bot_pk, status="disconnected")

            bot_info = db.get_wecom_bot(self.bot_pk)
            if bot_info and bot_info.get("enabled") == 0:
                db.update_wecom_bot(self.bot_pk, status="stopped")
                logger.info("[Bot#%d] Bot 已禁用，停止运行", self.bot_pk)
                break

            await asyncio.sleep(5)

    async def _handle_message(self, req_id: str, body: dict):
        """处理企微消息"""
        from_info = body.get("from", {})
        user_id = from_info.get("userid", "")
        msg_type = body.get("msgtype", "text")
        chat_type = body.get("chattype", "single")
        chat_id = body.get("chatid", "")

        if not user_id:
            return

        conv_key = f"{chat_id}•••{user_id}" if chat_type == "group" and chat_id else user_id
        user_name = conv_key

        if msg_type in ("image", "file", "voice"):
            asyncio.create_task(self.client.send_text(req_id, "暂不支持图片/文件/语音"))
            return

        if msg_type != "text":
            return

        content = (body.get("text", {}).get("content") or "").strip()
        if not content:
            return

        content = re.sub(r'@\S+\s*(AI|Bot|助手|小助手|机器人)?\s*', '', content).strip()

        if content in ("/new", "/新对话", "新对话"):
            db.delete_bot_conv_id(self.bot_pk, conv_key)
            asyncio.create_task(self.client.send_text(req_id, "已开启新对话"))
            return

        response_url = body.get("response_url", "")
        quote = body.get("quote", {})
        quote_text = ""
        if quote:
            qt = quote.get("text", {}).get("content", "") or ""
            if qt:
                quote_text = qt
                content = f"【引用的消息】\n{qt}\n\n【我的问题】\n{content}"

        logger.info("[Bot#%d][%s] %s | quote=%s", self.bot_pk, conv_key, content[:200], quote_text[:100])
        asyncio.create_task(self._process_and_reply(req_id, conv_key, user_name, content, response_url))

    async def _process_and_reply(self, req_id: str, conv_key: str, user_name: str,
                                  content: str, response_url: str):
        """调用绑定的 Dify 获取回答并回复"""
        dify_cfg = db.get_bot_dify_config(self.bot_pk)
        if not dify_cfg:
            await self.client.send_text(req_id, "(未绑定 Dify 应用，请联系管理员)")
            return

        api_key = dify_cfg["api_key"]
        base_url = dify_cfg["base_url"]
        dify_app_id = dify_cfg["id"]
        dify_conv_id = db.get_bot_conv_id(self.bot_pk, conv_key)

        try:
            result = await _chat_blocking(
                content, conv_key, api_key, base_url,
                conversation_id=dify_conv_id,
                inputs_cache=self._inputs_cache,
            )
            full_answer = result.get("answer", "") or ""
            new_conv_id = result.get("conversation_id")
            total_tokens = result.get("metadata", {}).get("usage", {}).get("total_tokens", 0)
            message_id = result.get("message_id")
        except Exception as e:
            logger.error("[Bot#%d][%s] dify error: %s", self.bot_pk, conv_key, e)
            full_answer = "(Service unavailable)"
            new_conv_id = None
            total_tokens = 0
            message_id = None

        clean = _strip_thinking(full_answer) or full_answer or "(Empty reply)"

        if new_conv_id:
            db.save_bot_conv_id(self.bot_pk, conv_key, new_conv_id)
        if message_id:
            db.save_bot_feedback_id(self.bot_pk, conv_key, message_id)

        db.save_bot_message(self.bot_pk, conv_key, user_name, content, clean, total_tokens, dify_app_id)

        if response_url:
            try:
                async with httpx.AsyncClient(timeout=10) as cli:
                    await cli.post(response_url, json={
                        "msgtype": "markdown",
                        "markdown": {"content": clean}
                    })
            except Exception as e:
                logger.error("[Bot#%d][%s] http reply error: %s", self.bot_pk, conv_key, e)
        else:
            await self.client.send_text(req_id, clean)

        logger.info("[Bot#%d][%s] done, tokens=%s msg_id=%s",
                    self.bot_pk, conv_key, total_tokens, message_id)

    async def _forward_rating(self, user_id: str, rating: str):
        """将企微点赞/点踩转发到 Dify feedback API"""
        message_id = db.get_bot_feedback_id(self.bot_pk, user_id)
        if not message_id:
            logger.warning("[Bot#%d] RATING skipped: no message_id for %s", self.bot_pk, user_id)
            return

        dify_cfg = db.get_bot_dify_config(self.bot_pk)
        if not dify_cfg:
            return

        rating_val = "like" if rating in ("1", "like") else "dislike"
        try:
            async with httpx.AsyncClient(timeout=10) as cli:
                url = f"{dify_cfg['base_url'].rstrip('/')}/messages/{message_id}/feedbacks"
                resp = await cli.post(url,
                                      headers={"Authorization": f"Bearer {dify_cfg['api_key']}"},
                                      json={"rating": rating_val, "user": user_id})
                logger.info("[Bot#%d] RATING forwarded: %s %s -> %s (%d)",
                            self.bot_pk, user_id, rating_val, message_id, resp.status_code)
        except Exception as e:
            logger.error("[Bot#%d] RATING forward error: %s", self.bot_pk, e)
