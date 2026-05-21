import httpx
import json
from typing import AsyncGenerator, Optional

DIFY_API_TIMEOUT = 60

# Dify 错误码翻译
DIFY_ERROR_MAP = {
    "app_unavailable": "应用未发布，请前往 Dify 后台点击「发布」按钮",
    "provider_not_initialize": "模型未配置，请检查应用的模型设置",
    "provider_quota_exceeded": "模型配额已用完，请联系管理员",
    "model_currently_not_support": "当前模型不支持该功能",
    "completion_request_error": "模型调用失败，请检查模型配置",
    "workflow_request_error": "工作流执行失败",
    "invalid_param": "参数错误：工作流未发布或缺少必填参数",
    "tool_invoke_error": "工具调用失败",
    "file_too_large": "上传文件过大",
    "unsupported_file_type": "不支持的文件格式",
}

def translate_dify_error(body: dict) -> str:
    """将 Dify API 返回的错误翻译为中文"""
    code = body.get("code", "")
    msg = body.get("message", "")
    # 优先用 code 查找翻译
    if code in DIFY_ERROR_MAP:
        return DIFY_ERROR_MAP[code]
    # 特殊消息匹配
    if "Workflow not published" in msg:
        return "工作流未发布，请前往 Dify 后台点击「发布」按钮"
    if "App unavailable" in msg:
        return "应用未发布，请前往 Dify 后台点击「发布」按钮"
    # 默认返回原文
    return msg or f"请求失败（错误码: {code}）"

class DifyClient:
    """Dify API 客户端"""
    
    def __init__(self, api_key: str, base_url: str = "https://dify.towh.cn", verify_ssl: bool = True):
        self.api_key = api_key
        # 处理 base_url 可能已包含 /v1 后缀的情况，避免双重 /v1
        base = base_url.rstrip('/')
        if base.endswith('/v1'):
            self.base_url = base[:-3]  # 去掉 /v1，由各方法统一追加
        else:
            self.base_url = base
        self.verify_ssl = verify_ssl
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    async def _get_default_inputs(self) -> dict:
        """
        获取应用的默认输入参数
        从 /parameters 接口获取用户输入表单，为必填字段填充默认值
        """
        try:
            params = await self.get_parameters()
            user_input_form = params.get("user_input_form", [])
            defaults = {}
            for field in user_input_form:
                # 支持多种字段类型的格式
                for field_type, config in field.items():
                    variable = config.get("variable")
                    required = config.get("required", False)
                    options = config.get("options", [])
                    default_value = config.get("default", "")
                    
                    if variable and required:
                        if default_value:
                            defaults[variable] = default_value
                        elif options:
                            defaults[variable] = options[0]
                        else:
                            defaults[variable] = ""
            return defaults
        except Exception:
            return {}
    
    async def chat_message(
        self, 
        query: str, 
        user: str,
        conversation_id: Optional[str] = None,
        inputs: dict = None,
        response_mode: str = "blocking"
    ) -> dict:
        """
        发送对话消息
        
        Args:
            query: 用户输入内容
            user: 用户标识（企微 UserID）
            conversation_id: 会话ID（首次为空字符串）
            inputs: 应用输入变量
            response_mode: 响应模式 (blocking/streaming)
        """
        url = f"{self.base_url}/v1/chat-messages"
        
        # 如果没有提供 inputs，自动获取默认值
        if inputs is None:
            inputs = await self._get_default_inputs()
        else:
            # 合并默认值：自定义 inputs 优先，缺失字段用默认值填充
            defaults = await self._get_default_inputs()
            merged = defaults.copy()
            merged.update(inputs)
            inputs = merged
        
        payload = {
            "inputs": inputs,
            "query": query,
            "response_mode": response_mode,
            "user": user,
            "files": []
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                response = await client.post(
                    url,
                    headers=self.headers,
                    json=payload
                )
                
                # 处理常见错误
                if response.status_code == 400:
                    try:
                        body = response.json()
                        raise Exception(translate_dify_error(body))
                    except json.JSONDecodeError:
                        raise Exception(f"请求参数错误: {response.text[:200]}")
                elif response.status_code == 401:
                    raise Exception("API Key 认证失败，请检查应用配置")
                elif response.status_code == 404:
                    raise Exception("Dify 应用不存在，请检查 base_url 和 API Key")
                elif response.status_code == 429:
                    raise Exception("请求过于频繁，请稍后重试")
                elif response.status_code >= 500:
                    raise Exception(f"Dify 服务器错误: {response.status_code}")
                
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            raise Exception(f"网络请求失败: {str(e)}")
    
    async def chat_message_stream(
        self,
        query: str,
        user: str,
        conversation_id: Optional[str] = None,
        inputs: dict = None
    ) -> AsyncGenerator[dict, None]:
        """
        流式发送对话消息（Server-Sent Events）
        
        Yields:
            流式响应的事件字典，包含 event, answer, conversation_id 等
        """
        url = f"{self.base_url}/v1/chat-messages"
        
        # 如果没有提供 inputs，自动获取默认值
        if inputs is None:
            inputs = await self._get_default_inputs()
        else:
            # 合并默认值：自定义 inputs 优先，缺失字段用默认值填充
            defaults = await self._get_default_inputs()
            merged = defaults.copy()
            merged.update(inputs)
            inputs = merged
        
        payload = {
            "inputs": inputs,
            "query": query,
            "response_mode": "streaming",
            "user": user,
            "files": []
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=self.headers,
                    json=payload
                ) as response:
                    # 处理常见错误
                    if response.status_code == 400:
                        try:
                            body = await response.aread()
                            error_body = json.loads(body)
                            raise Exception(translate_dify_error(error_body))
                        except (json.JSONDecodeError, Exception) as decode_err:
                            if isinstance(decode_err, json.JSONDecodeError):
                                raise Exception(f"请求参数错误: {body[:200]}")
                            raise
                    elif response.status_code == 401:
                        raise Exception("API Key 认证失败，请检查应用配置")
                    elif response.status_code == 404:
                        raise Exception("Dify 应用不存在，请检查 base_url 和 API Key")
                    elif response.status_code == 429:
                        raise Exception("请求过于频繁，请稍后重试")
                    elif response.status_code >= 500:
                        raise Exception(f"Dify 服务器错误: {response.status_code}")
                    
                    response.raise_for_status()
                    
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]  # 去掉 "data: " 前缀
                            if data == "[DONE]":
                                break
                            try:
                                event = json.loads(data)
                                yield event
                            except json.JSONDecodeError:
                                continue
        except httpx.RequestError as e:
            raise Exception(f"网络请求失败: {str(e)}")
    
    async def get_conversations(self, user: str, last_id: Optional[str] = None, limit: int = 20) -> dict:
        """获取用户的会话列表"""
        url = f"{self.base_url}/v1/conversations"
        params = {"user": user, "limit": limit}
        if last_id:
            params["last_id"] = last_id
            
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                response = await client.get(
                    url,
                    headers=self.headers,
                    params=params
                )
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            raise Exception(f"获取会话列表失败: {str(e)}")
    
    async def get_messages(self, conversation_id: str, user: str, last_id: Optional[str] = None, limit: int = 20) -> dict:
        """获取会话的消息历史"""
        url = f"{self.base_url}/v1/messages"
        params = {"conversation_id": conversation_id, "user": user, "limit": limit}
        if last_id:
            params["last_id"] = last_id
            
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                response = await client.get(
                    url,
                    headers=self.headers,
                    params=params
                )
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            raise Exception(f"获取消息历史失败: {str(e)}")
    
    async def delete_conversation(self, conversation_id: str, user: str) -> dict:
        """删除会话"""
        url = f"{self.base_url}/v1/conversations/{conversation_id}"
        payload = {"user": user}
        
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                response = await client.delete(
                    url,
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            raise Exception(f"删除会话失败: {str(e)}")
    
    async def rename_conversation(self, conversation_id: str, user: str, name: str = None, auto_generate: bool = False) -> dict:
        """重命名会话"""
        url = f"{self.base_url}/v1/conversations/{conversation_id}/name"
        payload = {
            "user": user,
            "auto_generate": auto_generate
        }
        if name and not auto_generate:
            payload["name"] = name
        
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                response = await client.post(
                    url,
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            raise Exception(f"重命名会话失败: {str(e)}")
    
    async def message_feedback(self, message_id: str, user: str, rating: str, content: str = None) -> dict:
        """消息反馈（点赞/点踩）"""
        url = f"{self.base_url}/v1/messages/{message_id}/feedbacks"
        payload = {
            "user": user,
            "rating": rating
        }
        if content:
            payload["content"] = content
        
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                response = await client.post(
                    url,
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            raise Exception(f"提交反馈失败: {str(e)}")
    
    async def upload_file(self, file_content: bytes, filename: str, user: str, mime_type: str = None) -> dict:
        """上传文件"""
        url = f"{self.base_url}/v1/files/upload"
        
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                files = {
                    "file": (filename, file_content, mime_type or "application/octet-stream")
                }
                data = {"user": user}
                response = await client.post(
                    url,
                    headers={"Authorization": self.headers["Authorization"]},
                    files=files,
                    data=data
                )
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            raise Exception(f"上传文件失败: {str(e)}")
    
    async def get_parameters(self) -> dict:
        """获取应用参数（用于检查应用连接状态）"""
        url = f"{self.base_url}/v1/parameters"
        
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                response = await client.get(
                    url,
                    headers=self.headers
                )
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            raise Exception(f"获取应用参数失败: {str(e)}")
    
    async def text_to_speech(self, message_id: str = "", text: str = "", user: str = "", streaming: bool = False) -> bytes:
        """文字转语音"""
        url = f"{self.base_url}/v1/text-to-audio"
        payload = {
            "user": user,
            "streaming": streaming
        }
        if message_id:
            payload["message_id"] = message_id
        if text:
            payload["text"] = text
        
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                response = await client.post(
                    url,
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                return response.content
        except httpx.RequestError as e:
            raise Exception(f"文字转语音失败: {str(e)}")
    
    async def speech_to_text(self, audio_content: bytes, filename: str, user: str, mime_type: str = "audio/wav") -> dict:
        """语音转文字"""
        url = f"{self.base_url}/v1/audio-to-text"
        
        try:
            async with httpx.AsyncClient(timeout=DIFY_API_TIMEOUT, verify=self.verify_ssl) as client:
                files = {
                    "file": (filename, audio_content, mime_type)
                }
                data = {"user": user}
                response = await client.post(
                    url,
                    headers={"Authorization": self.headers["Authorization"]},
                    files=files,
                    data=data
                )
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            raise Exception(f"语音转文字失败: {str(e)}")


# ==================== 持久化会话管理 ====================

import os
import sqlite3
from datetime import datetime
from contextlib import contextmanager

# 使用与主数据库相同的路径
CONVERSATION_DB_PATH = os.getenv("DATABASE_URL", "sqlite:///app/data/portal.db").replace("sqlite://", "")

@contextmanager
def get_conv_db():
    """获取会话数据库连接"""
    conn = sqlite3.connect(CONVERSATION_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_conversation_db():
    """初始化会话持久化表"""
    with get_conv_db() as db:
        # 本地会话缓存表（用于快速查询和持久化）
        db.execute("""
            CREATE TABLE IF NOT EXISTS conversations_local (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                app_id INTEGER NOT NULL,
                conversation_id TEXT NOT NULL,
                name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, app_id, conversation_id)
            )
        """)
        
        # 本地消息缓存表（用于消息持久化）
        db.execute("""
            CREATE TABLE IF NOT EXISTS messages_local (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                app_id INTEGER NOT NULL,
                conversation_id TEXT NOT NULL,
                message_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations_local(conversation_id)
            )
        """)
        
        db.commit()

def save_conversation_local(user_id: str, app_id: int, conversation_id: str, name: str = None):
    """持久化保存会话信息到本地数据库"""
    if not conversation_id:
        return
    
    with get_conv_db() as db:
        db.execute("""
            INSERT INTO conversations_local (user_id, app_id, conversation_id, name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, app_id, conversation_id) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP,
                name = COALESCE(?, name)
        """, (user_id, app_id, conversation_id, name, name))
        db.commit()

def save_message_local(user_id: str, app_id: int, conversation_id: str, role: str, content: str, message_id: str = None):
    """持久化保存消息到本地数据库"""
    if not conversation_id:
        return
    
    with get_conv_db() as db:
        db.execute("""
            INSERT INTO messages_local (user_id, app_id, conversation_id, message_id, role, content)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, app_id, conversation_id, message_id, role, content))
        db.commit()

def get_conversations_local(user_id: str, app_id: int) -> list:
    """从本地数据库获取会话列表"""
    with get_conv_db() as db:
        return db.execute("""
            SELECT * FROM conversations_local 
            WHERE user_id = ? AND app_id = ?
            ORDER BY updated_at DESC
        """, (user_id, app_id)).fetchall()

def get_messages_local(user_id: str, app_id: int, conversation_id: str) -> list:
    """从本地数据库获取消息历史"""
    with get_conv_db() as db:
        return db.execute("""
            SELECT * FROM messages_local 
            WHERE user_id = ? AND app_id = ? AND conversation_id = ?
            ORDER BY created_at ASC
        """, (user_id, app_id, conversation_id)).fetchall()

def delete_conversation_local(user_id: str, app_id: int, conversation_id: str):
    """从本地数据库删除会话"""
    with get_conv_db() as db:
        db.execute("DELETE FROM messages_local WHERE conversation_id = ?", (conversation_id,))
        db.execute("""
            DELETE FROM conversations_local 
            WHERE user_id = ? AND app_id = ? AND conversation_id = ?
        """, (user_id, app_id, conversation_id))
        db.commit()


# ==================== 兼容旧的内存缓存（带持久化后备） ====================

conversation_cache = {}

def get_conversation_id(user_id: str, app_id: int) -> Optional[str]:
    """获取用户的当前会话ID（优先内存，后备数据库）"""
    key = f"{user_id}:{app_id}"
    
    # 先查内存
    conv_id = conversation_cache.get(key)
    if conv_id:
        return conv_id
    
    # 后备查数据库
    try:
        conversations = get_conversations_local(user_id, app_id)
        if conversations:
            # 返回最新的会话
            latest = conversations[0]
            conversation_cache[key] = latest["conversation_id"]
            return latest["conversation_id"]
    except:
        pass
    
    return None

def set_conversation_id(user_id: str, app_id: int, conversation_id: str):
    """设置用户的会话ID（内存+持久化）"""
    if not conversation_id:
        return
    
    key = f"{user_id}:{app_id}"
    conversation_cache[key] = conversation_id
    
    # 同时持久化到数据库
    try:
        save_conversation_local(user_id, app_id, conversation_id)
    except:
        pass

def clear_conversation(user_id: str, app_id: int):
    """清除用户的会话"""
    key = f"{user_id}:{app_id}"
    if key in conversation_cache:
        del conversation_cache[key]
