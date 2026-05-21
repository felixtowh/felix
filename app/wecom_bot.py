"""
企业微信智能机器人客户端 — WebSocket 连接管理
每个 Bot 实例化一个 WeComClient，管理自己的 WebSocket 连接
"""
import json
import asyncio
import logging
import uuid

import websockets

WECOM_WS_URL = "wss://openws.work.weixin.qq.com"

logger = logging.getLogger(__name__)


class WeComClient:
    """单个企微 Bot 的 WebSocket 客户端"""

    def __init__(self, bot_pk: int, bot_id: str, secret: str):
        self.bot_pk = bot_pk
        self.bot_id = bot_id
        self.secret = secret
        self.ws: websockets.WebSocketClientProtocol | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self.ws is not None

    async def connect(self) -> bool:
        """建立 WebSocket 连接并认证"""
        try:
            self.ws = await websockets.connect(WECOM_WS_URL, ping_interval=None, max_size=2 ** 20)
            req_id = self._gen_req_id("auth_")
            await self.ws.send(json.dumps({
                "cmd": "aibot_subscribe",
                "headers": {"req_id": req_id},
                "body": {"bot_id": self.bot_id, "secret": self.secret},
            }))
            raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
            frame = json.loads(raw)
            if frame.get("errcode") == 0:
                self._connected = True
                logger.info("[Bot#%d] WebSocket 已认证", self.bot_pk)
                return True
            else:
                logger.error("[Bot#%d] 认证失败: errcode=%s errmsg=%s",
                             self.bot_pk, frame.get("errcode"), frame.get("errmsg"))
                await self.ws.close()
                self.ws = None
                return False
        except Exception as e:
            logger.error("[Bot#%d] 连接失败: %s", self.bot_pk, e)
            self._connected = False
            self.ws = None
            return False

    async def disconnect(self):
        self._connected = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

    async def recv(self) -> dict | None:
        """接收一条消息帧，自动处理心跳"""
        if not self.ws:
            return None
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=65)
            frame = json.loads(raw)
            cmd = frame.get("cmd", "")
            if cmd in ("aibot_msg_callback", "aibot_event_callback"):
                logger.debug("[Bot#%d] WS callback: cmd=%s", self.bot_pk, cmd)
            if cmd == "pong":
                return None
            if cmd in ("aibot_msg_callback", "aibot_event_callback"):
                return frame
            return None
        except asyncio.TimeoutError:
            logger.warning("[Bot#%d] WS recv timeout (65s), reconnecting...", self.bot_pk)
            self._connected = False
            if self.ws:
                try:
                    await self.ws.close()
                except Exception:
                    pass
            self.ws = None
            raise ConnectionError("recv timeout")
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("[Bot#%d] WS closed: code=%s reason=%s", self.bot_pk, e.code, e.reason)
            self._connected = False
            self.ws = None
            raise
        except Exception as e:
            logger.error("[Bot#%d] recv error: %s", self.bot_pk, e)
            self._connected = False
            self.ws = None
            raise

    async def heartbeat(self, interval: int = 30):
        """心跳保活协程"""
        while self.ws:
            await asyncio.sleep(interval)
            try:
                await self.ws.send(json.dumps({"cmd": "ping"}))
            except Exception:
                break

    async def send_text(self, req_id: str, content: str) -> bool:
        """通过 WebSocket 通道回复文本消息"""
        if not self.ws:
            return False
        try:
            await self.ws.send(json.dumps({
                "cmd": "aibot_respond_msg",
                "headers": {"req_id": req_id},
                "body": {"msgtype": "text", "text": {"content": content}},
            }))
            return True
        except Exception as e:
            logger.error("[Bot#%d] send_text error: %s", self.bot_pk, e)
            return False

    @staticmethod
    def _gen_req_id(prefix: str = "") -> str:
        return f"{prefix}{uuid.uuid4().hex[:16]}"
