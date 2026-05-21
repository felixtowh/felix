import os
import json
import sys
import shutil
import hashlib
import secrets
import urllib.parse
import logging
import traceback
import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exception_handlers import http_exception_handler
import httpx

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.database import (
    init_db, get_all_apps, get_active_apps, get_app, get_home_app, create_app, update_app, delete_app,
    get_db,
    get_or_create_user, get_user_by_userid, get_user_by_name, get_all_users, set_user_admin, delete_user,
    update_user, reset_user_password, batch_create_users, verify_password,
    get_user_allowed_apps, user_has_app_permission, grant_app_permission, revoke_app_permission,
    get_app_permissions, record_usage, get_usage_stats, get_daily_stats, get_app_detail_stats,
    get_conversation_mapping, set_conversation_mapping, get_user_conversations, get_user_active_conversation,
    update_conversation_title, update_conversation_tokens,
    set_app_visibility, get_app_visibility, batch_grant_app_permission, batch_revoke_app_permission,
    add_message_feedback, get_message_feedback, delete_message_feedback,
    add_favorite, remove_favorite, get_user_favorites, is_favorite,
    get_user_memories, create_user_memory, update_user_memory, delete_user_memory,
    get_setting, set_setting
)
from app.dify_client import DifyClient
from app.wechat_auth import (
    build_oauth_url, build_qrconnect_url, get_user_info, WECHAT_CORP_ID, WECHAT_AGENT_ID,
    WECHAT_SUITE_ID, WECHAT_TOKEN, WECHAT_ENCODING_AES_KEY,
    get_suite_access_token, get_auth_corp, save_auth_corp,
    exchange_auth_code, build_isv_oauth_url, get_user_info_3rd, handle_callback,
    get_suite_ticket, calculate_signature, decrypt_message
)
from app.bot_manager import bot_manager
from app import database as db_bot  # Bot 管理用别名避免和已有 database 导入冲突

# Portal 公网地址，用于构建企微 OAuth 回调等外部链接
PORTAL_URL = os.getenv("PORTAL_URL", "")

# 外部 API Key，供 Dify 代码执行器/HTTP 请求节点调用 Portal 时验证身份
# 优先从数据库读取，其次回退到环境变量
ENV_EXTERNAL_API_KEY = os.getenv("EXTERNAL_API_KEY", "")

def get_external_api_key() -> str:
    """获取外部 API Key（优先数据库配置，其次环境变量）"""
    db_key = get_setting("external_api_key", "")
    return db_key if db_key else ENV_EXTERNAL_API_KEY

def is_memory_api_enabled() -> bool:
    """习惯接口是否全局开启"""
    return get_setting("memory_api_enabled", "") == "1"

app = FastAPI(title="Dify Portal")

def get_public_url(request: Request) -> str:
    """获取对外访问的基础 URL，优先使用 PORTAL_URL 环境变量"""
    if PORTAL_URL:
        return PORTAL_URL.rstrip("/")
    # 回退到 request.base_url（注意：反向代理场景下可能不准确）
    return str(request.base_url).rstrip("/")

# 辅助函数：创建 DifyClient，根据应用配置处理 SSL 验证
def create_dify_client(api_key: str, base_url: str, skip_ssl_verify: bool = False) -> DifyClient:
    """创建 DifyClient
    
    Args:
        api_key: API 密钥
        base_url: 基础 URL
        skip_ssl_verify: 是否跳过 SSL 证书验证（仅对 HTTPS 有效）
    
    Returns:
        DifyClient 实例
    """
    # HTTP 不需要 SSL 验证，HTTPS 根据配置决定
    if base_url.startswith('https'):
        verify_ssl = not skip_ssl_verify
    else:
        verify_ssl = True  # HTTP 不需要验证，但设为 True 也没影响
    return DifyClient(api_key=api_key, base_url=base_url, verify_ssl=verify_ssl)

# 文件上传配置
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/app/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(f"{UPLOAD_DIR}/logos", exist_ok=True)

# Dify 应用参数缓存（TTL 5 分钟）
_app_params_cache = {}
_app_params_cache_ttl = 300  # 秒

def _get_cached_app_params(app_id: int, api_key: str, enable_instant_reply: bool = False):
    """获取缓存的应用参数，过期返回 None。缓存 key 包含 enable_instant_reply，开关变化时自动失效。"""
    key = f"{app_id}:{api_key}:{int(enable_instant_reply)}"
    cached = _app_params_cache.get(key)
    if cached and (time.time() - cached["ts"]) < _app_params_cache_ttl:
        return cached["data"]
    return None

def _set_cached_app_params(app_id: int, api_key: str, data: dict, enable_instant_reply: bool = False):
    """缓存应用参数"""
    _app_params_cache[f"{app_id}:{api_key}:{int(enable_instant_reply)}"] = {"ts": time.time(), "data": data}

# 文件上传白名单和大小限制
# Logo 上传需要图片格式，对话文件上传需要文档格式
ALLOWED_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "svg", "txt", "md", "pdf", "docx", "pptx"}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB

async def save_upload_file(upload_file: UploadFile, subdir: str = "") -> Optional[str]:
    """保存上传的文件并返回 URL，非法文件返回 None"""
    if not upload_file.filename:
        return None
    
    # 校验扩展名
    ext = os.path.splitext(upload_file.filename)[1].lstrip(".").lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        logger.warning(f"拒绝上传非法扩展名文件: {upload_file.filename}")
        return None
    
    # 校验大小
    file_content = await upload_file.read()
    if len(file_content) > MAX_UPLOAD_SIZE:
        logger.warning(f"拒绝上传 oversized 文件: {upload_file.filename}, size={len(file_content)}")
        return None
    
    # 生成唯一文件名（北京时间）
    timestamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{upload_file.filename}"
    
    # 构建保存路径
    if subdir:
        save_dir = os.path.join(UPLOAD_DIR, subdir)
    else:
        save_dir = UPLOAD_DIR
    os.makedirs(save_dir, exist_ok=True)
    
    file_path = os.path.join(save_dir, filename)
    
    # 保存文件
    with open(file_path, "wb") as buffer:
        buffer.write(file_content)
    
    # 返回访问 URL
    if subdir:
        return f"/uploads/{subdir}/{filename}"
    return f"/uploads/{filename}"

# 自定义 403 错误处理
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 403:
        # 外部 API 返回 JSON 而不是 HTML 页面
        if request.url.path.startswith('/api/external/'):
            return JSONResponse({"success": False, "error": exc.detail}, status_code=403)
        return templates.TemplateResponse("403.html", {"request": request}, status_code=403)
    # 其他错误使用默认处理
    return await http_exception_handler(request, exc)

# 挂载静态文件
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception as e:
    logger.warning(f"挂载静态文件目录失败: {e}")

# 挂载上传文件目录
try:
    app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
except Exception as e:
    logger.warning(f"挂载上传文件目录失败: {e}")

# 企业微信可信域名验证文件
@app.get("/WW_verify_{token}.txt")
async def wechat_verify_file(token: str):
    file_path = Path("static") / f"WW_verify_{token}.txt"
    if file_path.exists():
        return PlainTextResponse(content=file_path.read_text(encoding='utf-8'))
    raise HTTPException(status_code=404, detail="Verification file not found")

# 模板
templates = Jinja2Templates(directory="templates", auto_reload=True)

# 初始化数据库
@app.on_event("startup")
async def startup():
    init_db()
    # 启动所有已配置的企微 Bot
    bot_manager.start_all()


@app.on_event("shutdown")
async def shutdown():
    bot_manager.stop_all()
    logger.info("All bot workers stopped")

# ==================== 工具函数 ====================

def get_current_user(request: Request):
    """从 cookie 获取当前用户，并实时从数据库刷新关键字段（is_admin、name）
    解决：管理员授权/取消授权后，刷新页面即可生效，无需重新登录
    """
    user_json = request.cookies.get("user")
    if not user_json:
        return None
    try:
        user = json.loads(user_json)
    except json.JSONDecodeError:
        return None
    
    # 实时从数据库刷新关键字段，确保权限变更立即生效
    userid = user.get("userid")
    if userid:
        db_user = get_user_by_userid(userid)
        if db_user:
            user["is_admin"] = bool(db_user.get("is_admin", False))
            # name 也可能被管理员修改，一并刷新
            if db_user.get("name"):
                user["name"] = db_user["name"]
            # id 保持数据库中的值
            user["id"] = db_user.get("id", user.get("id"))
        else:
            # 用户已被管理员删除，cookie 仍然有效，强制失效
            return None
    
    return user

def is_admin_user(request: Request):
    """检查当前用户是否为管理员（每次从数据库验证最新状态）"""
    user = get_current_user(request)
    if not user:
        return False
    # 从数据库重新验证管理员权限，防止权限取消后仍通过 cookie 访问
    full_user = get_user_by_userid(user.get("userid"))
    return full_user and bool(full_user.get("is_admin", False))


async def handle_oauth_login(request: Request, app_id: int = None) -> Optional[RedirectResponse]:
    """处理企微 OAuth 登录逻辑，提取自 index() 和 chat_page()"""
    code = request.query_params.get("code")
    corp_id = request.query_params.get("corpid")
    
    redirect_path = f"/chat/{app_id}" if app_id else "/"
    
    # 优先尝试 ISV 模式（第三方应用）
    if code and WECHAT_SUITE_ID:
        try:
            wechat_user = await get_user_info_3rd(code, need_detail=False)
            if wechat_user:
                userid = wechat_user["userid"]
                name = wechat_user.get("name") or userid
                user, _ = get_or_create_user(userid=userid, name=name)
                response = RedirectResponse(redirect_path, status_code=302)
                response.set_cookie(
                    key="user",
                    value=json.dumps(user),
                    max_age=86400 * 7,
                    httponly=True,
                    samesite="lax"
                )
                return response
        except Exception as e:
            logger.warning(f"ISV 企微登录失败: {e}")
    
    # ISV 模式：未登录 + 无 code + 有 corp_id → 主动发起 OAuth
    if not code and corp_id and WECHAT_SUITE_ID:
        try:
            corp_info = get_auth_corp(corp_id)
            if corp_info and corp_info.get("agent_id"):
                redirect_uri = f"{PORTAL_URL or str(request.base_url).rstrip('/')}{redirect_path}"
                oauth_url = build_isv_oauth_url(
                    corp_id=corp_id,
                    agent_id=corp_info["agent_id"],
                    redirect_uri=redirect_uri,
                    state="",
                    scope="snsapi_base"
                )
                if oauth_url:
                    logger.info(f"重定向到 ISV OAuth: {oauth_url[:100]}...")
                    return RedirectResponse(oauth_url, status_code=302)
            else:
                logger.warning(f"企业 {corp_id} 未授权或缺少 agent_id, corp_info={corp_info}")
        except Exception as e:
            logger.warning(f"构建 ISV OAuth URL 失败: {e}")
    
    # 兜底：自建应用模式（兼容）
    if code and WECHAT_CORP_ID and WECHAT_AGENT_ID:
        try:
            wechat_user = await get_user_info(code)
            if wechat_user:
                user, _ = get_or_create_user(
                    userid=wechat_user["userid"],
                    name=wechat_user["name"]
                )
                response = RedirectResponse(redirect_path, status_code=302)
                response.set_cookie(
                    key="user",
                    value=json.dumps(user),
                    max_age=86400 * 7,
                    httponly=True,
                    samesite="lax"
                )
                return response
        except Exception as e:
            logger.warning(f"自建应用企微登录失败: {e}")
    
    return None


def build_dify_user(user: dict, app: dict) -> str:
    """传给 Dify 的 user 标识，统一使用 Portal 用户账号(userid)"""
    return user.get("userid") or user.get("name") or str(user.get("id")) or "anonymous"


# ==================== 页面路由 ====================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页 - 默认进入首页应用的对话页，如果没有配置则展示应用列表
    支持双入口：
    1. 企微应用内打开：自动触发 OAuth 授权登录
    2. 浏览器直接访问：未登录跳转到登录页
    """
    user = get_current_user(request)
    
    # 未登录，检测企微授权（支持 ISV 第三方应用模式）
    if not user:
        oauth_response = await handle_oauth_login(request)
        if oauth_response:
            return oauth_response
        return RedirectResponse("/login")
    
    # 已登录，检查是否有配置首页应用
    home_app = get_home_app()
    if home_app and user_has_app_permission(user.get("id"), home_app["id"]):
        # 直接渲染对话页，不跳转（单页体验）
        return templates.TemplateResponse("chat.html", {
            "request": request,
            "user": user,
            "app": home_app
        })
    
    # 没有首页应用
    if not home_app:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "user": user,
            "apps": [],
            "no_home_app": True,
        })

    # 有首页但无权限
    user_id = user.get("id")
    apps = get_user_allowed_apps(user_id)

    first_login = request.cookies.get("first_login")
    response = templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "apps": apps,
        "first_login": first_login == "1"
    })
    if first_login:
        response.delete_cookie("first_login")
    return response

@app.get("/chat/{app_id}", response_class=HTMLResponse)
async def chat_page(request: Request, app_id: int):
    """对话页面（同样支持企微自动登录）"""
    user = get_current_user(request)
    
    # 未登录时，检测企微授权（支持 ISV 第三方应用模式）
    if not user:
        oauth_response = await handle_oauth_login(request, app_id=app_id)
        if oauth_response:
            return oauth_response
        return RedirectResponse("/")
    
    app = get_app(app_id)
    if not app or not app["is_active"]:
        # 应用不存在或已停用，自动返回首页
        return RedirectResponse("/", status_code=302)
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        # 无权限，自动返回首页
        return RedirectResponse("/", status_code=302)
    
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "user": user,
        "app": app
    })

# ==================== 登录路由 ====================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    """登录页面"""
    # 已登录用户自动跳转到首页
    if get_current_user(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.get("/login/wecom")
async def login_wecom():
    """企微扫码登录跳转（支持 ISV 第三方应用模式）"""
    base_url = PORTAL_URL.rstrip("/") if PORTAL_URL else ""
    if not base_url:
        return RedirectResponse("/login?error=未配置 PORTAL_URL 环境变量", status_code=302)
    
    # 优先使用 ISV 模式
    if WECHAT_SUITE_ID:
        auth_corp = get_auth_corp()
        if not auth_corp:
            return RedirectResponse("/login?error=应用尚未被企业授权安装，请联系管理员", status_code=302)
        callback_url = f"{base_url}/auth/callback"
        # 登录按钮场景：使用 snsapi_privateinfo 尝试获取用户详细信息
        oauth_url = build_isv_oauth_url(
            corp_id=auth_corp["corp_id"],
            agent_id=auth_corp["agent_id"],
            redirect_uri=callback_url,
            state="login",
            scope="snsapi_privateinfo"
        )
        if oauth_url:
            return RedirectResponse(oauth_url, status_code=302)
        return RedirectResponse("/login?error=ISV OAuth URL 构建失败", status_code=302)
    
    # 兜底：自建应用扫码登录
    if not WECHAT_CORP_ID or not WECHAT_AGENT_ID:
        return RedirectResponse("/login?error=企微登录未配置", status_code=302)
    callback_url = f"{base_url}/auth/callback"
    qr_url = build_qrconnect_url(redirect_uri=callback_url, state="login")
    return RedirectResponse(qr_url, status_code=302)

@app.post("/login")
async def login(request: Request, userid: str = Form(...), password: str = Form(...)):
    """登录处理"""
    # 验证用户
    user = get_user_by_userid(userid)
    
    if not user:
        return RedirectResponse(f"/login?error={urllib.parse.quote('用户不存在')}", status_code=302)
    
    # 验证密码（优先使用数据库存储的密码，如果没有则使用默认规则）
    stored_password = user.get("password")
    if stored_password:
        if not verify_password(password, stored_password):
            return RedirectResponse(f"/login?error={urllib.parse.quote('密码错误')}", status_code=302)
    else:
        # 兼容旧用户，默认密码规则：userid + "001"
        expected_password = userid + "001"
        if password != expected_password:
            return RedirectResponse(f"/login?error={urllib.parse.quote('密码错误')}", status_code=302)

    # 设置 cookie
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        key="user",
        value=json.dumps(user),
        max_age=86400 * 7,  # 7天
        httponly=True,
        samesite="lax"
    )

    return response

@app.post("/api/change-password")
async def change_password(request: Request):
    """修改密码"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    data = await request.json()
    current_password = data.get("current_password", "")
    new_password = data.get("new_password", "")

    if not current_password or not new_password:
        raise HTTPException(status_code=400, detail="密码不能为空")

    # 获取用户完整信息（包含密码）
    full_user = get_user_by_userid(user["userid"])

    # 验证当前密码
    stored_password = full_user.get("password")
    if stored_password:
        if not verify_password(current_password, stored_password):
            raise HTTPException(status_code=400, detail="当前密码错误")
    else:
        expected_password = user["userid"] + "001"
        if current_password != expected_password:
            raise HTTPException(status_code=400, detail="当前密码错误")

    # 更新密码（使用哈希存储）
    success = reset_user_password(user["id"], new_password)

    if not success:
        raise HTTPException(status_code=500, detail="密码更新失败")

    return {"success": True, "message": "密码修改成功"}

# ==================== ISV 第三方应用回调路由 ====================

@app.post("/wecom/callback")
async def wecom_callback(
    request: Request,
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = ""
):
    """
    企微 ISV 数据回调 / 指令回调
    接收 SuiteTicket、授权变更等推送
    """
    body = await request.body()
    xml_body = body.decode("utf-8")
    result = await handle_callback(xml_body, msg_signature, timestamp, nonce)
    return PlainTextResponse(content=result)


@app.get("/wecom/callback")
async def wecom_callback_get(
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = ""
):
    """
    企微 ISV 回调 URL 验证（GET 请求）
    企微在配置回调 URL 时会先发送 GET 请求验证
    """
    if not WECHAT_TOKEN or not WECHAT_ENCODING_AES_KEY or not echostr:
        return PlainTextResponse(content="")
    try:
        expected_sig = calculate_signature(WECHAT_TOKEN, timestamp, nonce, echostr)
        if expected_sig == msg_signature:
            decrypted = decrypt_message(echostr, WECHAT_ENCODING_AES_KEY)
            return PlainTextResponse(content=decrypted)
    except Exception as e:
        print(f"回调验证失败: {e}")
    return PlainTextResponse(content="")


@app.get("/wecom/auth")
async def wecom_auth(request: Request, auth_code: Optional[str] = None):
    """
    企业授权安装回调
    管理员扫码授权后，企微跳转到这里
    """
    if not auth_code:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "message": "授权失败：未获取到 auth_code"
        })
    
    suite_access_token = await get_suite_access_token()
    if not suite_access_token:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "message": "授权失败：SuiteAccessToken 未获取，请等待几分钟后重试"
        })
    
    corp_info = await exchange_auth_code(suite_access_token, auth_code)
    if not corp_info:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "message": "授权失败：无法换取企业授权信息"
        })
    
    return templates.TemplateResponse("error.html", {
        "request": request,
        "message": f"授权成功！企业：{corp_info.get('corp_name', '')}，请从企微工作台进入应用。"
    })


# ==================== 企微授权路由（保留，可选） ====================

@app.get("/auth/callback")
async def auth_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None):
    """企微 OAuth 回调（支持 ISV 第三方应用模式）"""
    if not code:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "message": "授权失败：未获取到 code"
        })
    
    # 优先尝试 ISV 模式
    wechat_user = None
    if WECHAT_SUITE_ID:
        try:
            # 登录按钮场景：尝试获取用户详细信息（需要 scope=snsapi_privateinfo）
            wechat_user = await get_user_info_3rd(code, need_detail=True)
        except Exception as e:
            print(f"ISV 获取用户信息失败: {e}")
    
    # 兜底：自建应用模式
    if not wechat_user and WECHAT_CORP_ID:
        try:
            wechat_user = await get_user_info(code)
        except Exception as e:
            print(f"自建应用获取用户信息失败: {e}")
    
    if not wechat_user:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "message": "授权失败：无法获取用户信息"
        })
    
    # 获取或创建用户
    userid = wechat_user["userid"]
    name = wechat_user.get("name") or userid
    user, is_new = get_or_create_user(
        userid=userid,
        name=name
    )
    
    # 设置 cookie
    response = RedirectResponse("/")
    response.set_cookie(
        key="user",
        value=json.dumps(user),
        max_age=86400 * 7,
        httponly=True,
        samesite="lax"
    )
    
    # 首次登录提示
    if is_new:
        response.set_cookie(
            key="first_login",
            value="1",
            max_age=60,
            httponly=True,
            samesite="lax"
        )
    
    return response

@app.get("/logout")
async def logout():
    """退出登录"""
    response = RedirectResponse("/")
    response.delete_cookie("user")
    return response

# ==================== API 路由 ====================

@app.get("/api/chat/{app_id}/parameters")
async def get_chat_parameters(request: Request, app_id: int):
    """获取 Dify 应用的能力配置参数（用于前端动态调整 UI）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限访问此应用")
    
    try:
        # 先检查缓存（key 包含 enable_instant_reply，开关变化时自动失效）
        cached = _get_cached_app_params(app_id, app["api_key"], bool(app.get("enable_instant_reply")))
        if cached:
            return cached

        client = DifyClient(app["api_key"], app["base_url"])
        params = await client.get_parameters()
        
        # 文件上传配置
        file_upload = params.get("file_upload", {}) or {}
        file_config = file_upload.get("fileUploadConfig", {}) or {}
        sys_params = params.get("system_parameters", {}) or {}
        
        # 取应用级与系统级的较严格值
        file_size_limit = min(
            file_config.get("file_size_limit", sys_params.get("file_size_limit", 15)),
            sys_params.get("file_size_limit", 15)
        ) if file_config.get("file_size_limit") else sys_params.get("file_size_limit", 15)
        
        image_size_limit = min(
            file_config.get("image_file_size_limit", sys_params.get("image_file_size_limit", 10)),
            sys_params.get("image_file_size_limit", 10)
        ) if file_config.get("image_file_size_limit") else sys_params.get("image_file_size_limit", 10)
        
        # 将 user_input_form 平展为统一格式
        raw_forms = params.get("user_input_form", [])
        user_input_form = []
        for field in raw_forms:
            for field_type, config in field.items():
                user_input_form.append({
                    "type": field_type,
                    "variable": config.get("variable", ""),
                    "label": config.get("label", ""),
                    "required": config.get("required", False),
                    "options": config.get("options", []),
                    "default": config.get("default", ""),
                    "placeholder": config.get("placeholder", "")
                })
        
        result = {
            "success": True,
            "features": {
                "file_upload": {
                    "enabled": file_upload.get("enabled", False),
                    "max_files": file_upload.get("number_limits", 1),
                    "max_size_mb": file_size_limit,
                    "max_image_size_mb": image_size_limit,
                    "allowed_extensions": file_upload.get("allowed_file_extensions", []),
                    "allowed_types": file_upload.get("allowed_file_types", [])
                },
                "speech_to_text": params.get("speech_to_text", {}).get("enabled", False),
                "text_to_speech": params.get("text_to_speech", {}).get("enabled", False)
            },
            "ui": {
                "opening_statement": params.get("opening_statement", ""),
                "suggested_questions": params.get("suggested_questions", []),
                "suggested_questions_after_answer": params.get("suggested_questions_after_answer", {}).get("enabled", False)
            },
            "inputs": {
                "user_input_form": user_input_form
            },
            "portal": {
                "instant_reply": {
                    "enabled": bool(app.get("enable_instant_reply")) and get_setting("instant_reply_enabled") == "1",
                    "mode": get_setting("instant_reply_mode") or "single",
                    "interval": int(get_setting("instant_reply_interval") or 5),
                    "max_tokens": int(get_setting("instant_reply_max_tokens") or 40)
                }
            }
        }
        _set_cached_app_params(app_id, app["api_key"], result, bool(app.get("enable_instant_reply")))
        return result
    except Exception as e:
        print(f"获取应用参数失败: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取应用配置失败: {str(e)}")

@app.post("/api/chat/{app_id}")
async def chat_api(request: Request, app_id: int):
    """对话 API"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    # 获取请求数据
    data = await request.json()
    query = data.get("message", "")
    request_conversation_id = data.get("conversation_id")
    custom_inputs = data.get("inputs", {})
    
    if not query:
        raise HTTPException(status_code=400, detail="消息不能为空")
    
    dify_user = build_dify_user(user, app)
    
    # 注入 Portal 回调参数（供 Dify 代码执行器/HTTP 请求节点使用）
    api_key = get_external_api_key()
    if api_key and PORTAL_URL and is_memory_api_enabled() and app.get("enable_memory"):
        custom_inputs = custom_inputs or {}
        custom_inputs["portal_user_id"] = user.get("userid") or user.get("name") or str(user.get("id"))
        custom_inputs["portal_key"] = api_key
        custom_inputs["portal_api_key"] = api_key  # 兼容旧变量名
        custom_inputs["portal_base_url"] = PORTAL_URL.rstrip("/")
    
    # 获取会话ID（优先使用请求中传入的，否则创建新对话）
    if request_conversation_id:
        conversation_id = request_conversation_id
        logger.info(f"Using conversation_id from request: {conversation_id}")
    else:
        # 用户点击了"新对话"，不传递旧会话ID，让 Dify 创建新对话
        conversation_id = None
        logger.info("Creating new conversation (no conversation_id provided)")
    
    # 调用 Dify API
    client = create_dify_client(api_key=app["api_key"], base_url=app["base_url"] if app["base_url"] else "http://your-dify-server.com", skip_ssl_verify=app["skip_ssl_verify"] if app["skip_ssl_verify"] else False)
    
    try:
        response = await client.chat_message(
            query=query,
            user=dify_user,
            conversation_id=conversation_id,
            inputs=custom_inputs if custom_inputs else None,
            response_mode="blocking"
        )
        
        # 保存会话ID到数据库（使用 Dify 返回的 conversation_id 作为 local_conv_id）
        if response.get("conversation_id"):
            local_conv_id = response["conversation_id"]  # 使用 Dify 的 ID 作为本地 ID，确保持久化
            set_conversation_mapping(
                user_id=user.get("id"),
                app_id=app_id,
                local_conv_id=local_conv_id,
                dify_conv_id=response["conversation_id"],
                title=None
            )
            logger.info(f"Saved conversation mapping - local: {local_conv_id}, dify: {response['conversation_id']}")
            
            # 新会话调用 Dify 自动生成标题
            existing = get_conversation_mapping(user.get("id"), app_id, local_conv_id)
            if not existing or not existing["title"]:
                try:
                    rename_result = await client.rename_conversation(local_conv_id, dify_user, auto_generate=True)
                    if rename_result.get("name"):
                        update_conversation_title(user.get("id"), app_id, local_conv_id, rename_result["name"])
                        logger.info(f"Dify auto-generated title: {rename_result['name']}")
                except Exception as e:
                    logger.warning(f"Failed to auto-generate conversation title: {e}")
        
        # 记录使用统计
        record_usage(user.get("id"), app_id)
        
        return {
            "success": True,
            "answer": response.get("answer", ""),
            "conversation_id": response.get("conversation_id", "")
        }
    except Exception as e:
        logger.error(f"Chat API error - app_id: {app_id}, user: {dify_user}, error: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"对话失败: {str(e)}")

@app.post("/api/chat/{app_id}/stream")
async def chat_stream(request: Request, app_id: int):
    """流式对话 API"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    # 获取请求数据
    data = await request.json()
    query = data.get("message", "")
    request_conversation_id = data.get("conversation_id")
    custom_inputs = data.get("inputs", {})
    
    if not query:
        raise HTTPException(status_code=400, detail="消息不能为空")
    
    dify_user = build_dify_user(user, app)
    
    # 注入 Portal 回调参数（供 Dify 代码执行器/HTTP 请求节点使用）
    api_key = get_external_api_key()
    if api_key and PORTAL_URL and is_memory_api_enabled() and app.get("enable_memory"):
        custom_inputs = custom_inputs or {}
        custom_inputs["portal_user_id"] = user.get("userid") or user.get("name") or str(user.get("id"))
        custom_inputs["portal_api_key"] = api_key
        custom_inputs["portal_base_url"] = PORTAL_URL.rstrip("/")
    
    # 获取会话ID（优先使用请求中传入的，否则创建新对话）
    if request_conversation_id:
        conversation_id = request_conversation_id
    else:
        # 用户点击了"新对话"，不传递旧会话ID，让 Dify 创建新对话
        conversation_id = None
    
    # 调用 Dify API（流式）
    client = create_dify_client(api_key=app["api_key"], base_url=app["base_url"] if app["base_url"] else "http://your-dify-server.com", skip_ssl_verify=app["skip_ssl_verify"] if app["skip_ssl_verify"] else False)
    
    async def generate():
        logger.info(f"Starting stream for app_id={app_id}, user={dify_user}")
        event_count = 0
        try:
            logger.info("Calling chat_message_stream...")
            async for event in client.chat_message_stream(
                query=query,
                user=dify_user,
                conversation_id=conversation_id,
                inputs=custom_inputs if custom_inputs else None
            ):
                event_count += 1
                # 定期检测客户端是否已断开（如用户刷新页面）
                if event_count % 10 == 0:
                    if await request.is_disconnected():
                        logger.info(f"Client disconnected, stopping stream for app_id={app_id}, conv={conversation_id}")
                        break
                
                logger.info(f"Got event: {event.get('event')}, msg_id={event.get('message_id')}, answer_len={len(event.get('answer', '') if event.get('answer') else '')}")
                event_json = json.dumps(event) + "\n"
                yield event_json
                
                if event.get("event") == "message_end" and event.get("conversation_id"):
                    local_conv_id = event["conversation_id"]
                    set_conversation_mapping(
                        user_id=user.get("id"),
                        app_id=app_id,
                        local_conv_id=local_conv_id,
                        dify_conv_id=event["conversation_id"],
                        title=None
                    )
                    record_usage(user.get("id"), app_id)

                    # 提取并保存 token 使用量
                    try:
                        metadata = event.get("metadata", {})
                        usage = metadata.get("usage", {}) if isinstance(metadata, dict) else {}
                        total_tokens = usage.get("total_tokens", 0)
                        if total_tokens and total_tokens > 0:
                            update_conversation_tokens(user.get("id"), app_id, local_conv_id, total_tokens)
                            logger.info(f"Recorded tokens: app_id={app_id}, conv={local_conv_id}, tokens={total_tokens}")
                    except Exception as e:
                        logger.warning(f"Failed to record tokens: {e}")

                    # 新会话调用 Dify 自动生成标题
                    existing = get_conversation_mapping(user.get("id"), app_id, local_conv_id)
                    if not existing or not existing["title"]:
                        try:
                            rename_result = await client.rename_conversation(local_conv_id, dify_user, auto_generate=True)
                            if rename_result.get("name"):
                                update_conversation_title(user.get("id"), app_id, local_conv_id, rename_result["name"])
                                logger.info(f"Dify auto-generated title: {rename_result['name']}")
                        except Exception as e:
                            logger.warning(f"Failed to auto-generate conversation title: {e}")
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error: {e.response.status_code} - {str(e)}")
            error_msg = str(e)
            if e.response.status_code == 401:
                error_msg = "API Key 无效，请管理员检查应用配置"
            elif e.response.status_code == 404:
                error_msg = "Dify API 未找到，请检查基础 URL"
            elif e.response.status_code >= 500:
                error_msg = "Dify 服务器错误，请稍后重试"
            yield json.dumps({"event": "error", "message": error_msg}) + "\n"
        except Exception as e:
            logger.error(f"Stream exception type: {type(e)}")
            logger.error(f"Stream exception args: {e.args}")
            logger.error(f"Stream exception repr: {repr(e)}")
            logger.error(f"Stream traceback:\n{traceback.format_exc()}")
            error_msg = str(e) if str(e) else f"服务器错误: {type(e).__name__}"
            yield json.dumps({"event": "error", "message": error_msg}) + "\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        }
    )

# ==================== 管理后台路由 ====================

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """管理后台首页"""
    if not get_current_user(request):
        return RedirectResponse("/login")
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")

    return templates.TemplateResponse("admin/index.html", {
        "request": request,
        "user": get_current_user(request)
    })

@app.get("/admin/apps", response_class=HTMLResponse)
async def admin_apps(request: Request):
    """应用管理页面"""
    if not get_current_user(request):
        return RedirectResponse("/login")
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")

    apps = get_all_apps()
    return templates.TemplateResponse("admin/apps.html", {
        "request": request,
        "user": get_current_user(request),
        "apps": apps,
        "global_instant_reply_enabled": get_setting("instant_reply_enabled") == "1"
    })

@app.get("/admin/apps/list", response_class=HTMLResponse)
async def admin_apps_list(request: Request):
    """应用列表总览页面"""
    if not get_current_user(request):
        return RedirectResponse("/login")
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")

    apps = get_all_apps()
    return templates.TemplateResponse("admin/apps_list.html", {
        "request": request,
        "user": get_current_user(request),
        "apps": apps
    })

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    """用户管理页面"""
    if not get_current_user(request):
        return RedirectResponse("/login")
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")

    users = get_all_users()
    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "user": get_current_user(request),
        "users": users
    })

@app.get("/admin/stats", response_class=HTMLResponse)
async def admin_stats(request: Request):
    """统计页面"""
    if not get_current_user(request):
        return RedirectResponse("/login")
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")

    stats = get_usage_stats()
    return templates.TemplateResponse("admin/stats.html", {
        "request": request,
        "user": get_current_user(request),
        "stats": stats
    })

@app.get("/admin/bots", response_class=HTMLResponse)
async def admin_bots(request: Request):
    """Bot 管理页面（原生集成）"""
    if not get_current_user(request):
        return RedirectResponse("/login")
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    bots = db_bot.list_wecom_bots()
    # 附加连接状态和绑定信息
    for bot in bots:
        pk = bot["id"]
        worker = bot_manager.workers.get(pk)
        bot["connected"] = worker.connected if worker else False
        bot["dify_app"] = db_bot.get_bot_dify_config(pk)
    dify_apps = db_bot.list_bot_dify_apps()
    return templates.TemplateResponse("admin/bots.html", {
        "request": request,
        "user": get_current_user(request),
        "bots": bots,
        "dify_apps": dify_apps
    })


# ==================== Bot 管理 API ====================

@app.get("/admin/api/bots")
async def admin_api_list_bots(request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    bots = db_bot.list_wecom_bots()
    for bot in bots:
        pk = bot["id"]
        worker = bot_manager.workers.get(pk)
        bot["connected"] = worker.connected if worker else False
        bot["dify_app"] = db_bot.get_bot_dify_config(pk)
    return bots


@app.post("/admin/api/bots")
async def admin_api_create_bot(request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid JSON"}, status_code=400)

    name = (data.get("name") or "").strip()
    wecom_bot_id = (data.get("bot_id") or "").strip()
    secret = (data.get("secret") or "").strip()

    if not wecom_bot_id or not secret:
        return {"success": False, "message": "bot_id 和 secret 为必填项"}

    existing = db_bot.get_wecom_bot_by_bot_id(wecom_bot_id)
    if existing:
        return {"success": False, "message": f"Bot ID {wecom_bot_id} 已存在"}

    pk = db_bot.create_wecom_bot(name, wecom_bot_id, secret, enabled=1)

    dify_app_id = data.get("dify_app_id")
    if dify_app_id:
        db_bot.set_bot_dify_mapping(pk, int(dify_app_id))

    bot_manager.start_bot(pk)
    return {"success": True, "message": f"Bot #{pk} 已创建并启动", "id": pk}


@app.put("/admin/api/bots/{bot_pk}")
async def admin_api_update_bot(bot_pk: int, request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    bot = db_bot.get_wecom_bot(bot_pk)
    if not bot:
        return JSONResponse({"success": False, "message": "Bot 不存在"}, status_code=404)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid JSON"}, status_code=400)

    updates = {}
    for k in ("name", "bot_id", "secret", "enabled"):
        if k in data and data[k] is not None:
            updates[k] = data[k]

    if updates:
        db_bot.update_wecom_bot(bot_pk, **updates)

    if "dify_app_id" in data:
        if data["dify_app_id"]:
            db_bot.set_bot_dify_mapping(bot_pk, int(data["dify_app_id"]))
        else:
            db_bot.delete_bot_mapping(bot_pk)

    bot_manager.restart_bot(bot_pk)
    return {"success": True, "message": f"Bot #{bot_pk} 已更新并重启"}


@app.delete("/admin/api/bots/{bot_pk}")
async def admin_api_delete_bot(bot_pk: int, request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    bot = db_bot.get_wecom_bot(bot_pk)
    if not bot:
        return JSONResponse({"success": False, "message": "Bot 不存在"}, status_code=404)

    bot_manager.stop_bot(bot_pk)
    db_bot.delete_wecom_bot(bot_pk)
    return {"success": True, "message": f"Bot #{bot_pk} 已删除"}


@app.post("/admin/api/bots/{bot_pk}/start")
async def admin_api_start_bot(bot_pk: int, request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    bot_manager.start_bot(bot_pk)
    return {"success": True, "message": f"Bot #{bot_pk} 启动中"}


@app.post("/admin/api/bots/{bot_pk}/stop")
async def admin_api_stop_bot(bot_pk: int, request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    bot_manager.stop_bot(bot_pk)
    db_bot.update_wecom_bot(bot_pk, status="stopped")
    return {"success": True, "message": f"Bot #{bot_pk} 已停止"}



@app.get("/admin/api/bot-stats")
async def admin_api_bot_stats(request: Request, bot_id: int = 0):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    return db_bot.bot_stats(bot_id if bot_id else None)


@app.get("/admin/api/bot-users")
async def admin_api_bot_users(request: Request, bot_id: int = 0):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    return db_bot.bot_list_users(bot_id if bot_id else None)


# ==================== Dify App 管理 API ====================

@app.get("/admin/api/dify-apps")
async def admin_api_list_dify_apps(request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    apps = db_bot.list_bot_dify_apps()
    for app in apps:
        mappings = db_bot.get_bot_dify_mappings_for_app(app["id"])
        app["bot_count"] = len(mappings)
    return apps


@app.post("/admin/api/dify-apps")
async def admin_api_create_dify_app(request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid JSON"}, status_code=400)

    name = (data.get("name") or "").strip()
    api_key = (data.get("api_key") or "").strip()
    base_url = (data.get("base_url") or "https://api.dify.ai/v1").strip()

    if not api_key:
        return {"success": False, "message": "api_key 为必填项"}

    pk = db_bot.create_bot_dify_app(name, api_key, base_url, enabled=1)
    return {"success": True, "message": f"Dify 应用 #{pk} 已创建", "id": pk}


@app.put("/admin/api/dify-apps/{app_pk}")
async def admin_api_update_dify_app(app_pk: int, request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    app_info = db_bot.get_bot_dify_app(app_pk)
    if not app_info:
        return JSONResponse({"success": False, "message": "Dify 应用不存在"}, status_code=404)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid JSON"}, status_code=400)

    updates = {}
    for k in ("name", "api_key", "base_url", "enabled"):
        if k in data and data[k] is not None:
            updates[k] = data[k]

    if updates:
        db_bot.update_bot_dify_app(app_pk, **updates)

    # 重启所有绑定该应用的 Bot
    mappings = db_bot.get_bot_dify_mappings_for_app(app_pk)
    for m in mappings:
        bot_manager.restart_bot(m["bot_id"])

    return {"success": True, "message": f"Dify 应用 #{app_pk} 已更新，关联 Bot 已重启"}


@app.delete("/admin/api/dify-apps/{app_pk}")
async def admin_api_delete_dify_app(app_pk: int, request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    app_info = db_bot.get_bot_dify_app(app_pk)
    if not app_info:
        return JSONResponse({"success": False, "message": "Dify 应用不存在"}, status_code=404)

    # 先停掉关联的 Bot
    mappings = db_bot.get_bot_dify_mappings_for_app(app_pk)
    for m in mappings:
        bot_manager.stop_bot(m["bot_id"])

    db_bot.delete_bot_dify_app(app_pk)

    for m in mappings:
        bot_manager.start_bot(m["bot_id"])

    return {"success": True, "message": f"Dify 应用 #{app_pk} 已删除"}


@app.post("/admin/api/dify-apps/{app_pk}/toggle")
async def admin_api_toggle_dify_app(app_pk: int, request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    app_info = db_bot.get_bot_dify_app(app_pk)
    if not app_info:
        return JSONResponse({"success": False, "message": "Dify 应用不存在"}, status_code=404)
    new_enabled = 0 if app_info.get("enabled") else 1
    db_bot.update_bot_dify_app(app_pk, enabled=new_enabled)
    # 重启所有绑定该应用的 Bot
    mappings = db_bot.get_bot_dify_mappings_for_app(app_pk)
    for m in mappings:
        bot_manager.restart_bot(m["bot_id"])
    status_text = "已启用" if new_enabled else "已停用"
    return {"success": True, "message": f"Dify 应用 #{app_pk} {status_text}", "enabled": bool(new_enabled)}


@app.get("/admin/api/test-dify")
async def admin_api_test_dify(request: Request, api_key: str = "", base_url: str = ""):
    if not is_admin_user(request):
        raise HTTPException(status_code=403)
    """测试 Dify 连通性"""
    import ssl
    import urllib.request
    if not api_key:
        return {"ok": False, "error": "未提供 api_key"}
    base = (base_url or "https://api.dify.ai/v1").rstrip('/')
    for path in ["/parameters", "/v1/parameters"]:
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(
                f"{base}{path}",
                headers={"Authorization": f"Bearer {api_key}"}
            )
            resp = urllib.request.urlopen(req, timeout=10, context=ctx)
            if resp.status == 200:
                body = json.loads(resp.read())
                return {"ok": True, "message": f"Connected ({path})",
                        "app_name": body.get("app_name", "")}
            return {"ok": False, "error": f"HTTP {resp.status}"}
        except Exception as e:
            last_err = str(e)[:100]
    return {"ok": False, "error": last_err}


@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_page(request: Request):
    """系统配置页面"""
    if not get_current_user(request):
        return RedirectResponse("/login")
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")

    return templates.TemplateResponse("admin/settings.html", {
        "request": request,
        "user": get_current_user(request)
    })

@app.get("/admin/apps/{app_id}/detail", response_class=HTMLResponse)
async def admin_app_detail(request: Request, app_id: int):
    """应用详情页面"""
    if not get_current_user(request):
        return RedirectResponse("/login")
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")

    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")

    stats = get_app_detail_stats(app_id)
    return templates.TemplateResponse("admin/app_detail.html", {
        "request": request,
        "user": get_current_user(request),
        "app": app,
        "stats": stats
    })

# ==================== 管理后台 API ====================

@app.post("/admin/api/apps")
async def admin_create_app(
    request: Request,
    name: str = Form(...),
    api_key: str = Form(...),
    base_url: str = Form("http://your-dify-server.com"),
    welcome_message: str = Form(None),
    logo_url: str = Form(None),
    user_field: str = Form("name"),
    skip_ssl_verify: bool = Form(False),
    is_home_app: bool = Form(False),
    enable_memory: bool = Form(False),
    enable_thinking: bool = Form(False),
    logo_file: UploadFile = File(None)
):
    """创建应用"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    # 处理上传的 logo 文件
    final_logo_url = logo_url
    if logo_file and logo_file.filename:
        saved_url = await save_upload_file(logo_file, "logos")
        if saved_url is None:
            raise HTTPException(status_code=400, detail="上传的 logo 文件不合法（仅支持 jpg/png/gif/svg，且不超过 5MB）")
        final_logo_url = saved_url
    elif not final_logo_url:
        final_logo_url = "/static/images/debo-logo.png"  # 默认得宝 LOGO
    
    # 如果是 HTTPS URL，根据用户选择决定是否跳过 SSL 验证
    if base_url.startswith('https'):
        pass  # 使用用户传入的 skip_ssl_verify
    else:
        skip_ssl_verify = False  # HTTP 不需要跳过
    
    app_id = create_app(
        name=name,
        api_key=api_key,
        base_url=base_url,
        welcome_message=welcome_message,
        logo_url=final_logo_url,
        user_field=user_field,
        skip_ssl_verify=skip_ssl_verify,
        is_home_app=is_home_app,
        enable_memory=enable_memory,
        enable_thinking=enable_thinking
    )
    
    return {"success": True, "id": app_id}

@app.post("/admin/api/apps/{app_id}/update")
async def admin_update_app(
    request: Request,
    app_id: int,
    name: str = Form(None),
    api_key: str = Form(None),
    base_url: str = Form(None),
    welcome_message: str = Form(None),
    is_active: bool = Form(None),
    is_home_app: bool = Form(None),
    enable_memory: bool = Form(None),
    enable_thinking: bool = Form(None),
    enable_instant_reply: bool = Form(None),
    logo_url: str = Form(None),
    user_field: str = Form(None),
    skip_ssl_verify: bool = Form(None),
    logo_file: UploadFile = File(None)
):
    """更新应用"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    updates = {}
    if name is not None:
        updates["name"] = name
    if api_key is not None:
        updates["api_key"] = api_key
    if base_url is not None and base_url.strip():
        updates["base_url"] = base_url
    if welcome_message is not None:
        updates["welcome_message"] = welcome_message
    if is_active is not None:
        updates["is_active"] = is_active
    if is_home_app is not None:
        updates["is_home_app"] = is_home_app
        # 设为首页时，先清除其他应用的首页状态
        if is_home_app:
            with get_db() as db:
                db.execute("UPDATE apps SET is_home_app = 0")
    if enable_memory is not None:
        updates["enable_memory"] = enable_memory
    if enable_thinking is not None:
        updates["enable_thinking"] = enable_thinking
    if enable_instant_reply is not None:
        updates["enable_instant_reply"] = enable_instant_reply
    if user_field is not None:
        updates["user_field"] = user_field
    if skip_ssl_verify is not None:
        updates["skip_ssl_verify"] = skip_ssl_verify
    
    # 处理上传的 logo 文件
    if logo_file and logo_file.filename:
        saved_url = await save_upload_file(logo_file, "logos")
        if saved_url is None:
            raise HTTPException(status_code=400, detail="上传的 logo 文件不合法（仅支持 jpg/png/gif/svg，且不超过 5MB）")
        updates["logo_url"] = saved_url
    elif logo_url is not None:
        updates["logo_url"] = logo_url
    
    success = update_app(app_id, **updates)
    
    return {"success": success}

@app.post("/admin/api/apps/{app_id}/delete")
async def admin_delete_app(request: Request, app_id: int):
    """删除应用"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    delete_app(app_id)
    
    return {"success": True}

@app.get("/admin/api/apps/{app_id}/status")
async def admin_get_app_status(request: Request, app_id: int):
    """获取应用状态"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 尝试连接 Dify API - 使用 /parameters 端点检查应用状态
    try:
        client = DifyClient(
            api_key=app["api_key"],
            base_url=app["base_url"] or "http://your-dify-server.com",
            verify_ssl=not app.get("skip_ssl_verify", False)
        )
        
        loop = asyncio.get_event_loop()
        start_time = loop.time()
        
        # 使用 /parameters 端点获取应用信息（Dify 官方推荐的方式）
        result = await client.get_parameters()
        
        response_time = int((loop.time() - start_time) * 1000)
        
        return {
            "success": True,
            "connected": True,
            "response_time": response_time,
            "api_version": "v1",
            "last_used": app.get("updated_at"),
            "errors": []
        }
    except httpx.HTTPStatusError as e:
        error_msg = str(e)
        if e.response.status_code == 401:
            error_msg = "API Key 无效，请更新应用配置中的 API Key"
        elif e.response.status_code == 404:
            error_msg = "Dify API 未找到，请检查基础 URL 是否正确"
        return {
            "success": True,
            "connected": False,
            "response_time": None,
            "api_version": None,
            "last_used": None,
            "errors": [error_msg]
        }
    except Exception as e:
        return {
            "success": True,
            "connected": False,
            "response_time": None,
            "api_version": None,
            "last_used": None,
            "errors": [f"连接失败: {str(e)[:100]}"]
        }

@app.get("/admin/api/apps/{app_id}/info")
async def admin_get_app_info(request: Request, app_id: int):
    """获取应用完整信息（管理员用，含 API Key）"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    return {"success": True, "app": dict(app)}


@app.get("/admin/api/apps/{app_id}/test")
async def admin_test_app_connection(request: Request, app_id: int):
    """测试应用连接"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    try:
        client = DifyClient(
            api_key=app["api_key"],
            base_url=app["base_url"] or "http://your-dify-server.com",
            verify_ssl=not app.get("skip_ssl_verify", False)
        )
        
        # 使用 /parameters 端点检查应用连接
        result = await client.get_parameters()
        
        return {"success": True, "connected": True, "app_name": result.get("name", app["name"])}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/admin/api/users/search")
async def admin_search_users(request: Request, keyword: str = ""):
    """搜索用户（keyword 为空时返回全部用户）"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    users = get_all_users()
    matched_users = []
    
    keyword_lower = keyword.lower().strip() if keyword else ""
    for user in users:
        user_dict = dict(user)
        # keyword 为空时返回所有用户；否则按姓名、账号、部门筛选
        if not keyword_lower:
            matched_users.append({
                "id": user_dict["id"],
                "userid": user_dict["userid"],
                "name": user_dict["name"],
                "department": user_dict.get("department", "")
            })
        else:
            if (keyword_lower in user_dict.get("name", "").lower() or
                keyword_lower in user_dict.get("userid", "").lower() or
                keyword_lower in (user_dict.get("department") or "").lower()):
                matched_users.append({
                    "id": user_dict["id"],
                    "userid": user_dict["userid"],
                    "name": user_dict["name"],
                    "department": user_dict.get("department", "")
                })
    
    return {"success": True, "users": matched_users}

@app.post("/admin/api/users/{user_id}/admin")
async def admin_set_user_admin(request: Request, user_id: int, is_admin: bool = Form(...)):
    """设置用户管理员权限"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    current_user = get_current_user(request)
    
    # 1. 防止自我取消管理员权限
    if current_user and current_user.get("id") == user_id and not is_admin:
        raise HTTPException(status_code=400, detail="不能取消自己的管理员权限")
    
    # 2. 如果是取消管理员，检查是否还有其他管理员
    if not is_admin:
        users = get_all_users()
        admin_count = sum(1 for u in users if (dict(u) if hasattr(u, 'keys') else u).get("is_admin"))
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="系统必须至少保留一个管理员")
    
    set_user_admin(user_id, is_admin)
    
    return {"success": True}

@app.post("/admin/api/users/{user_id}/delete")
async def admin_delete_user(request: Request, user_id: int):
    """删除用户"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    current_user = get_current_user(request)
    
    # 1. 不能删除自己
    if current_user and current_user.get("id") == user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    
    # 2. 检查被删除的用户是否是管理员，如果是则检查是否还有其他管理员
    user_to_delete = None
    users = get_all_users()
    for u in users:
        user_dict = dict(u) if hasattr(u, 'keys') else u
        if user_dict.get("id") == user_id:
            user_to_delete = user_dict
            break
    
    if user_to_delete and user_to_delete.get("is_admin"):
        admin_count = sum(1 for u in users if (dict(u) if hasattr(u, 'keys') else u).get("is_admin"))
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="系统必须至少保留一个管理员，无法删除唯一的管理员")
    
    delete_user(user_id)
    
    return {"success": True}


@app.post("/admin/api/users/create")
async def admin_create_user(request: Request):
    """创建用户"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")

    data = await request.json()
    userid = data.get('userid', '').strip()
    name = data.get('name', '').strip()
    password = data.get('password', '').strip()
    if not userid or not name:
        raise HTTPException(status_code=400, detail="账号和昵称不能为空")

    # 检查用户是否已存在
    existing = get_user_by_userid(userid)
    if existing:
        raise HTTPException(status_code=400, detail="该账号已存在")

    # 创建用户
    new_user, _ = get_or_create_user(
        userid=userid,
        name=name,
        password=password or None
    )

    return {"success": True, "id": new_user["id"] if new_user else None}


@app.post("/admin/api/users/{user_id}/update")
async def admin_update_user(
    request: Request,
    user_id: int,
    name: str = Form(...),
    userid: str = Form(...),
    department: str = Form(None),
    position: str = Form(None)
):
    """更新用户信息"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    success = update_user(
        user_id=user_id,
        name=name,
        userid=userid,
        department=department,
        position=position
    )
    
    return {"success": success}


@app.post("/admin/api/users/{user_id}/reset-password")
async def admin_reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...)
):
    """重置用户密码"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    # 获取用户信息
    users = get_all_users()
    user = None
    for u in users:
        if dict(u).get('id') == user_id:
            user = dict(u)
            break
    
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 真正更新数据库密码
    success = reset_user_password(user_id, new_password)
    if not success:
        raise HTTPException(status_code=500, detail="密码重置失败")
    
    return {
        "success": True,
        "message": f"用户 {user.get('name')} 的密码已重置",
        "login_url": "/login"
    }


@app.post("/admin/api/users/batch-import")
async def admin_batch_import_users(request: Request):
    """批量导入用户"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    data = await request.json()
    users_data = data.get('users', [])
    
    if not users_data:
        raise HTTPException(status_code=400, detail="没有要导入的用户数据")
    
    result = batch_create_users(users_data)
    
    return {
        "success": True,
        "created": result['created'],
        "skipped": result['skipped'],
        "total": len(users_data)
    }

@app.get("/admin/api/stats")
async def admin_api_stats(request: Request):
    """获取统计数据 API"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    stats = get_usage_stats()
    return {
        "success": True,
        "total": {
            "total_users": stats["total"]["total_users"] or 0,
            "active_apps": stats["total"]["active_apps"] or 0,
            "total_conversations": stats["total"]["total_conversations"] or 0,
            "total_tokens": stats["total"]["total_tokens"] or 0,
            "today_active_users": stats["total"]["today_active_users"] or 0,
            "today_conversations": stats["total"]["today_conversations"] or 0,
            "today_tokens": stats["total"]["today_tokens"] or 0,
            "today_active_apps": stats["total"]["today_active_apps"] or 0
        },
        "app_stats": [dict(row) for row in stats["app_stats"]],
        "user_stats": [dict(row) for row in stats["user_stats"]]
    }

@app.get("/admin/api/stats/daily")
async def admin_api_stats_daily(request: Request, days: int = 30):
    """获取每日统计数据（近 N 天）"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    daily = get_daily_stats(days=days)
    return {
        "success": True,
        "dates": [row["date"] for row in daily],
        "active_users": [row["active_users"] for row in daily],
        "message_counts": [row["message_count"] for row in daily]
    }

@app.get("/admin/api/apps/{app_id}/stats")
async def admin_api_app_stats(request: Request, app_id: int, days: int = 30):
    """获取某个应用的详细统计数据"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    stats = get_app_detail_stats(app_id, days=days)
    return {
        "success": True,
        "app": {
            "id": stats["app"]["id"],
            "name": stats["app"]["name"],
            "welcome_message": stats["app"]["welcome_message"],
            "base_url": stats["app"]["base_url"],
            "is_active": stats["app"]["is_active"],
            "is_home_app": stats["app"]["is_home_app"]
        },
        "total": stats["total"],
        "users": stats["users"],
        "daily": stats["daily"]
    }

# ==================== 用户 API ====================

@app.get("/api/user/me")
async def get_current_user_api(request: Request):
    """获取当前登录用户的最新信息（用于权限刷新）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    full_user = get_user_by_userid(user["userid"])
    if not full_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {
        "success": True,
        "user": {
            "id": full_user["id"],
            "userid": full_user["userid"],
            "name": full_user["name"],
            "is_admin": bool(full_user["is_admin"])
        }
    }

@app.get("/api/user/apps")
async def get_user_apps_api(request: Request):
    """获取当前用户有权限的应用列表"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    apps = get_user_allowed_apps(user.get("id"))
    # 注入收藏状态
    favorite_ids = get_user_favorites(user.get("id"))
    result = []
    for app in apps:
        app_dict = dict(app)
        app_dict["is_favorite"] = app_dict["id"] in favorite_ids
        result.append(app_dict)
    return {
        "success": True,
        "apps": result
    }

@app.get("/api/user/favorites")
async def get_user_favorites_api(request: Request):
    """获取用户收藏的应用列表"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    favorite_ids = get_user_favorites(user.get("id"))
    apps = get_user_allowed_apps(user.get("id"))
    favorite_apps = [a for a in apps if a["id"] in favorite_ids]
    return {
        "success": True,
        "apps": favorite_apps
    }

@app.post("/api/user/favorites/{app_id}")
async def add_favorite_api(request: Request, app_id: int):
    """收藏应用"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限使用此应用")
    
    add_favorite(user.get("id"), app_id)
    return {"success": True}

@app.delete("/api/user/favorites/{app_id}")
async def remove_favorite_api(request: Request, app_id: int):
    """取消收藏应用"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    remove_favorite(user.get("id"), app_id)
    return {"success": True}

@app.get("/api/apps/{app_id}")
async def get_app_info_api(request: Request, app_id: int):
    """获取应用基本信息（不含敏感字段）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    return {
        "success": True,
        "app": {
            "id": app["id"],
            "name": app["name"],
            "welcome_message": app["welcome_message"],
            "logo_url": app["logo_url"],
            "user_field": app["user_field"],
            "base_url": app["base_url"] if app["base_url"] else "http://your-dify-server.com",
            "enable_memory": bool(app.get("enable_memory", 0)),
            "enable_thinking": bool(app.get("enable_thinking", 0))
        }
    }

@app.get("/api/chat/{app_id}/conversations")
async def get_conversations_api(request: Request, app_id: int):
    """获取指定应用的历史会话列表（优先从本地数据库获取，确保新对话立即显示）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    try:
        # 优先从本地数据库获取会话列表（确保新对话刷新后立即显示）
        local_convs = get_user_conversations(user.get("id"), app_id)
        conversations = []
        local_ids = set()
        
        for row in local_convs:
            conv_id = row["local_conv_id"]
            local_ids.add(conv_id)
            created_ts = 0
            if row["created_at"]:
                try:
                    dt = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
                    # 转换为北京时间戳
                    dt = dt.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
                    created_ts = int(dt.timestamp())
                except Exception:
                    created_ts = int(datetime.now().timestamp())
            conversations.append({
                "id": conv_id,
                "name": row["title"] or "未命名对话",
                "created_at": created_ts
            })
        
        # 同步 Dify 会话标题
        try:
            dify_user = build_dify_user(user, app)
            client = create_dify_client(api_key=app["api_key"], base_url=app["base_url"] if app["base_url"] else "http://your-dify-server.com", skip_ssl_verify=app["skip_ssl_verify"] if app["skip_ssl_verify"] else False)
            dify_result = await client.get_conversations(dify_user, limit=100)
            dify_names = {c["id"]: c["name"] for c in dify_result.get("data", []) if c.get("name")}
            for conv in conversations:
                dify_name = dify_names.get(conv["id"])
                if dify_name and dify_name != conv["name"]:
                    update_conversation_title(user.get("id"), app_id, conv["id"], dify_name)
                    conv["name"] = dify_name
        except Exception as e:
            logger.warning(f"Sync Dify conversation titles failed: {e}")
        
        # 按创建时间排序
        conversations.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        
        return {
            "success": True,
            "conversations": conversations
        }
    except Exception as e:
        logger.error(f"Get conversations error - app_id: {app_id}, error: {str(e)}")
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e), "conversations": []}

@app.put("/api/chat/{app_id}/conversations/{conversation_id}")
async def rename_conversation_api(request: Request, app_id: int, conversation_id: str):
    """重命名会话标题"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    data = await request.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="标题不能为空")
    
    try:
        # 更新本地数据库
        update_conversation_title(user.get("id"), app_id, conversation_id, name)
        
        # 同步到 Dify
        dify_user = build_dify_user(user, app)
        client = create_dify_client(api_key=app["api_key"], base_url=app["base_url"] if app["base_url"] else "http://your-dify-server.com", skip_ssl_verify=app["skip_ssl_verify"] if app["skip_ssl_verify"] else False)
        await client.rename_conversation(conversation_id, dify_user, name=name)
        
        return {"success": True, "name": name}
    except Exception as e:
        logger.error(f"Rename conversation error - app_id: {app_id}, conv_id: {conversation_id}, error: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"重命名失败: {str(e)}")

@app.get("/api/chat/{app_id}/messages")
async def get_messages_api(request: Request, app_id: int, conversation_id: str):
    """获取指定会话的消息历史"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")
    
    try:
        dify_user = build_dify_user(user, app)
        
        client = create_dify_client(api_key=app["api_key"], base_url=app["base_url"] if app["base_url"] else "http://your-dify-server.com", skip_ssl_verify=app["skip_ssl_verify"] if app["skip_ssl_verify"] else False)
        
        logger.info(f"Fetching messages - app_id: {app_id}, conversation_id: {conversation_id}, user: {dify_user}")
        
        # 分页获取所有历史消息（Dify 默认 limit=20）
        all_messages = []
        last_id = None
        while True:
            result = await client.get_messages(conversation_id=conversation_id, user=dify_user, last_id=last_id, limit=100)
            batch = result.get("data", [])
            if not batch:
                break
            all_messages.extend(batch)
            if not result.get("has_more"):
                break
            last_id = batch[-1].get("id")
        
        # 按 created_at 正序排序，确保消息时间线从上到下由旧到新
        all_messages.sort(key=lambda m: float(m.get("created_at") or 0))
        
        return {
            "success": True,
            "messages": all_messages
        }
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning(f"Conversation not found on Dify - app_id: {app_id}, conversation_id: {conversation_id}")
            return {"success": True, "messages": [], "error": "该对话在 Dify 端已被删除或过期"}
        logger.error(f"Get messages error - app_id: {app_id}, conversation_id: {conversation_id}, error: {str(e)}")
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e), "messages": []}
    except Exception as e:
        logger.error(f"Get messages error - app_id: {app_id}, conversation_id: {conversation_id}, error: {str(e)}")
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e), "messages": []}

@app.post("/api/chat/{app_id}/instant-status")
async def instant_status_api(request: Request, app_id: int):
    """轮询获取瞬时状态提示"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    data = await request.json()
    query = data.get("query", "")
    elapsed = data.get("elapsed", 0)
    last_reply = data.get("last_reply", "")
    
    # 无条件记录请求到达，方便排查
    global_ir_enabled = get_setting("instant_reply_enabled") == "1"
    app_ir_enabled = bool(app.get("enable_instant_reply"))
    logger.info(f"Instant status REQUEST app_id={app_id}, global_ir={global_ir_enabled}, app_ir={app_ir_enabled}, elapsed={elapsed}")
    
    # 检查应用是否启用了瞬时回复
    if not app.get("enable_instant_reply"):
        logger.info(f"Instant status app_id={app_id}: enable_instant_reply=0, returning empty")
        return {"success": True, "answer": ""}
    
    try:
        reply = await _call_instant_llm(query, elapsed, last_reply)
        api_key = get_setting("instant_reply_api_key")
        use_llm = get_setting("instant_reply_llm_enabled") == "1"
        strategy = "llm" if (use_llm and api_key) else "fixed"
        logger.info(f"Instant status app_id={app_id}: elapsed={elapsed}, strategy={strategy}, reply_len={len(reply)}")
        return {"success": True, "answer": reply, "strategy": strategy}
    except Exception as e:
        logger.warning(f"Instant status error: {e}")
        return {"success": True, "answer": ""}

def _get_default_model(base_url: str) -> str:
    """根据 Base URL 推断默认模型——优先推荐各平台速度最快的付费版本，避开免费拥堵节点"""
    url = base_url.lower()
    if "deepseek" in url:
        return "deepseek-v4-flash"       # V4 轻量极速版，13B 激活参数
    if "moonshot" in url:
        return "kimi-latest"             # Kimi 稳定版，kimi-k2-turbo-preview 部分账号已 404（K2 系列 5.25 下线）
    if "siliconflow" in url:
        return "deepseek-ai/DeepSeek-V3"
    if "dashscope" in url:
        return "qwen-turbo"
    if "bigmodel" in url:
        return "glm-4.7-flashx"          # 智谱付费高速版，避开免费 Flash 拥堵
    if "minimax" in url:
        return "abab6.5s-chat"           # Minimax 轻量快速版
    return "gpt-4o-mini"

async def _call_instant_llm(query: str, elapsed: int, last_reply: str) -> str:
    """固定策略兜底 + LLM 竞争（5秒超时）。
    先立即可得固定策略文案，同时后台调用 LLM；LLM 在 5 秒内返回则替换，否则固定策略兜底。
    用户始终有文案看，无感知享受自然回复。"""
    if not get_setting("instant_reply_enabled"):
        logger.info("_call_instant_llm: global instant_reply_enabled=0, returning empty")
        return ""
    
    # 1. 先计算固定策略文案（兜底，立即可得，零延迟）
    # 0 秒立即显示第一条，让用户立刻看到反馈；后续按时间递进
    if elapsed < 10:
        fixed_reply = "正在为您处理中，请稍候..."       # 0-9 秒：立即显示
    elif elapsed < 15:
        fixed_reply = "还在努力处理中，请耐心等待..."   # 第 2 次轮询（10 秒）
    elif elapsed < 20:
        fixed_reply = "处理时间较长，感谢您的耐心等候..." # 第 3 次轮询（15 秒）
    elif elapsed < 30:
        fixed_reply = "仍在处理中，马上就好..."         # 第 4/5 次轮询（20/25 秒）
    else:
        fixed_reply = "非常抱歉让您久等了，仍在处理中..." # 第 6 次轮询（30 秒+）
    
    api_key = get_setting("instant_reply_api_key")
    use_llm = get_setting("instant_reply_llm_enabled") == "1"
    
    # 未开启 LLM 或无 API Key，直接返回固定策略
    if not use_llm or not api_key:
        logger.info(f"_call_instant_llm: fixed only, elapsed={elapsed}, reply={fixed_reply}")
        return fixed_reply
    
    # 2. 同时尝试 LLM（5 秒超时），固定策略兜底
    async def _llm_task() -> str:
        base_url = get_setting("instant_reply_base_url") or "https://api.openai.com/v1"
        model = get_setting("instant_reply_model") or _get_default_model(base_url)
        
        default_prompt = (
            "你是一个 AI 助手的状态播报员。用户正在等待一个复杂工作流的执行结果。\n\n"
            "当前信息：\n"
            "- 用户原始请求：{query}\n"
            "- 已经等待了：{elapsed} 秒\n"
            "- 你上一次播报的内容：{last_reply}\n\n"
            "要求：\n"
            "- 生成一句新的状态提示，让用户知道系统仍在积极处理中\n"
            "- 必须结合上一次的播报内容递进，不要重复之前说过的话\n"
            "- 语气亲切自然，15-25 字\n"
            "- 如果等待时间很长（>30秒），适当表达歉意\n"
            "- 聚焦在'处理进度'上，不要重复用户的问题\n\n"
            "只输出纯文本，不要任何前缀、JSON 格式、编号。"
        )
        system_prompt = get_setting("instant_reply_prompt") or default_prompt
        prompt = system_prompt.replace("{query}", query).replace("{elapsed}", str(elapsed)).replace("{last_reply}", last_reply)
        
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": "请生成新的状态提示。"}
                    ],
                    "temperature": 0.5,
                    "max_tokens": int(get_setting("instant_reply_max_tokens") or 40)
                }
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
    
    try:
        llm_reply = await asyncio.wait_for(_llm_task(), timeout=5.0)
        if llm_reply:
            logger.info(f"_call_instant_llm: LLM win, elapsed={elapsed}, reply={llm_reply}")
            return llm_reply
    except asyncio.TimeoutError:
        logger.info(f"_call_instant_llm: LLM timeout (5s), fallback fixed, elapsed={elapsed}, reply={fixed_reply}")
    except Exception as e:
        logger.warning(f"_call_instant_llm: LLM error, fallback fixed, elapsed={elapsed}, err={e}")
    
    return fixed_reply

# ==================== 健康检查 ====================

@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}

# ==================== 文件上传 API ====================

@app.post("/api/chat/{app_id}/upload")
async def upload_file_api(request: Request, app_id: int, file: UploadFile = File(...)):
    """上传文件到 Dify（按应用路由）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    # 读取文件内容
    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    
    # 获取文件名和 MIME 类型
    filename = file.filename
    mime_type = file.content_type
    
    dify_user = build_dify_user(user, app)
    
    # 从数据库获取应用 API Key
    api_key = app["api_key"]
    base_url = app["base_url"] if app["base_url"] else "http://your-dify-server.com"
    skip_ssl_verify = app["skip_ssl_verify"] if app["skip_ssl_verify"] else False
    
    # 调用 Dify API 上传文件
    client = create_dify_client(api_key=api_key, base_url=base_url, skip_ssl_verify=skip_ssl_verify)
    
    try:
        result = await client.upload_file(
            file_content=file_content,
            filename=filename,
            user=dify_user,
            mime_type=mime_type
        )
        return {"success": True, "file": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)}")

# ==================== 消息反馈 API ====================

@app.post("/api/chat/{app_id}/feedback")
async def message_feedback_api(request: Request, app_id: int):
    """消息反馈（点赞/点踩）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    data = await request.json()
    message_id = data.get("message_id")
    rating = data.get("rating")  # 'like' 或 'dislike'
    content = data.get("content")  # 可选的反馈内容
    
    if not message_id or not rating:
        raise HTTPException(status_code=400, detail="缺少必要参数")
    
    if rating not in ["like", "dislike"]:
        raise HTTPException(status_code=400, detail="rating 必须是 'like' 或 'dislike'")
    
    dify_user = build_dify_user(user, app)
    
    # 调用 Dify API
    client = create_dify_client(api_key=app["api_key"], base_url=app["base_url"] if app["base_url"] else "http://your-dify-server.com", skip_ssl_verify=app["skip_ssl_verify"] if app["skip_ssl_verify"] else False)
    
    try:
        # 调用 Dify API
        await client.message_feedback(
            message_id=message_id,
            user=dify_user,
            rating=rating,
            content=content
        )
        
        # 保存到本地数据库
        add_message_feedback(
            message_id=message_id,
            user_id=user.get("id"),
            app_id=app_id,
            feedback_type=rating,
            content=content
        )
        
        return {"success": True, "message": "反馈已提交"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"提交反馈失败: {str(e)}")

@app.delete("/api/chat/{app_id}/feedback/{message_id}")
async def delete_message_feedback_api(request: Request, app_id: int, message_id: str):
    """删除消息反馈"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    # 删除本地反馈
    delete_message_feedback(message_id=message_id, user_id=user.get("id"))
    
    return {"success": True, "message": "反馈已删除"}

# ==================== 语音功能 API ====================

@app.post("/api/chat/{app_id}/text-to-speech")
async def text_to_speech_api(request: Request, app_id: int):
    """文字转语音"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    data = await request.json()
    message_id = data.get("message_id")
    text = data.get("text")
    
    if not text:
        raise HTTPException(status_code=400, detail="缺少文本内容")
    
    dify_user = build_dify_user(user, app)
    
    client = create_dify_client(api_key=app["api_key"], base_url=app["base_url"] if app["base_url"] else "http://your-dify-server.com", skip_ssl_verify=app["skip_ssl_verify"] if app["skip_ssl_verify"] else False)
    
    try:
        audio_data = await client.text_to_speech(
            message_id=message_id or "",
            text=text,
            user=dify_user
        )
        
        return Response(
            content=audio_data,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "attachment; filename=speech.mp3"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文字转语音失败: {str(e)}")

@app.post("/api/chat/{app_id}/speech-to-text")
async def speech_to_text_api(request: Request, app_id: int, file: UploadFile = File(...)):
    """语音转文字"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 检查权限
    if not user_has_app_permission(user.get("id"), app_id):
        raise HTTPException(status_code=403, detail="无权限")
    
    # 读取音频文件
    audio_content = await file.read()
    if not audio_content:
        raise HTTPException(status_code=400, detail="音频内容为空")
    
    dify_user = build_dify_user(user, app)
    
    client = create_dify_client(api_key=app["api_key"], base_url=app["base_url"] if app["base_url"] else "http://your-dify-server.com", skip_ssl_verify=app["skip_ssl_verify"] if app["skip_ssl_verify"] else False)
    
    try:
        result = await client.speech_to_text(
            audio_content=audio_content,
            filename=file.filename,
            user=dify_user,
            mime_type=file.content_type or "audio/wav"
        )
        
        return {"success": True, "text": result.get("text", "")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音转文字失败: {str(e)}")

# ==================== 应用权限管理 API ====================

@app.get("/admin/api/apps/{app_id}/permissions")
async def admin_get_app_permissions(request: Request, app_id: int):
    """获取应用的权限用户列表"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    # 获取可见性设置
    visibility = get_app_visibility(app_id)
    
    # 获取有权限的用户
    permissions = get_app_permissions(app_id)
    
    return {
        "success": True,
        "visibility": visibility,
        "users": [dict(row) for row in permissions]
    }

@app.post("/admin/api/apps/{app_id}/visibility")
async def admin_set_app_visibility_api(request: Request, app_id: int):
    """设置应用可见性"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    data = await request.json()
    visibility_type = data.get("visibility_type", "all")
    
    if visibility_type not in ["all", "specific"]:
        raise HTTPException(status_code=400, detail="visibility_type 必须是 'all' 或 'specific'")
    
    set_app_visibility(app_id, visibility_type)
    
    return {"success": True, "message": "可见性设置已更新"}

@app.post("/admin/api/apps/{app_id}/permissions/batch-grant")
async def admin_batch_grant_permissions(request: Request, app_id: int):
    """批量授予用户应用权限"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    data = await request.json()
    user_ids = data.get("user_ids", [])
    
    if not user_ids:
        raise HTTPException(status_code=400, detail="缺少用户ID列表")
    
    created_count = batch_grant_app_permission(app_id, user_ids)
    
    return {
        "success": True,
        "message": f"已成功授予 {created_count} 个用户权限",
        "granted_count": created_count
    }

@app.post("/admin/api/apps/{app_id}/permissions/batch-revoke")
async def admin_batch_revoke_permissions(request: Request, app_id: int):
    """批量撤销用户应用权限"""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    
    app = get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    
    data = await request.json()
    user_ids = data.get("user_ids", [])
    
    if not user_ids:
        raise HTTPException(status_code=400, detail="缺少用户ID列表")
    
    batch_revoke_app_permission(app_id, user_ids)
    
    return {
        "success": True,
        "message": f"已成功撤销 {len(user_ids)} 个用户的权限"
    }

# ==================== 用户记忆/习惯 API ====================

@app.get("/api/user/memories")
async def api_get_memories(request: Request):
    """获取当前用户的记忆列表"""
    if not is_memory_api_enabled():
        raise HTTPException(status_code=403, detail="习惯接口未开启")
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    memories = get_user_memories(user.get("id"))
    return {"memories": memories}

@app.post("/api/user/memories")
async def api_create_memory(request: Request):
    """新增记忆"""
    if not is_memory_api_enabled():
        raise HTTPException(status_code=403, detail="习惯接口未开启")
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    data = await request.json()
    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="内容不能为空")
    memory_id = create_user_memory(user.get("id"), content, source="manual")
    return {"success": True, "id": memory_id}

@app.put("/api/user/memories/{memory_id}")
async def api_update_memory(request: Request, memory_id: int):
    """修改记忆"""
    if not is_memory_api_enabled():
        raise HTTPException(status_code=403, detail="习惯接口未开启")
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    data = await request.json()
    content = data.get("content")
    is_active = data.get("is_active")
    success = update_user_memory(memory_id, content=content, is_active=is_active)
    return {"success": success}

@app.delete("/api/user/memories/{memory_id}")
async def api_delete_memory(request: Request, memory_id: int):
    """删除记忆"""
    if not is_memory_api_enabled():
        raise HTTPException(status_code=403, detail="习惯接口未开启")
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    success = delete_user_memory(memory_id)
    return {"success": success}

# ==================== 外部 API（供 Dify 代码执行器/HTTP 请求节点调用）====================

def _check_external_api_key(request: Request):
    """校验外部 API Key，支持 X-Portal-Key（新）和 X-External-API-Key（兼容旧），大小写敏感"""
    api_key = request.headers.get("X-Portal-Key") or request.headers.get("X-External-API-Key")
    valid_key = get_external_api_key()
    if not valid_key or not api_key or api_key != valid_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")

def _check_memory_api_enabled():
    """检查习惯接口是否全局开启"""
    if not is_memory_api_enabled():
        raise HTTPException(status_code=403, detail="习惯接口未开启")

@app.get("/api/external/memories")
async def external_get_memories(request: Request):
    """
    供 Dify 应用调用，用于拉取用户习惯。
    请求头必须携带 X-External-API-Key。
    返回格式：连续字符串，习惯之间用分号隔开。
    """
    _check_memory_api_enabled()
    _check_external_api_key(request)
    
    user_id_raw = request.headers.get("X-Portal-User-Id") or request.query_params.get("user_id")
    if not user_id_raw:
        raise HTTPException(status_code=400, detail="user_id is required (Header: X-Portal-User-Id or Query: user_id)")
    
    # 支持数字 id 或字符串 userid
    try:
        user_id_int = int(user_id_raw)
    except ValueError:
        user = get_user_by_userid(user_id_raw)
        if not user:
            raise HTTPException(status_code=400, detail="user_id must be a valid numeric user ID or registered userid")
        user_id_int = user["id"]
    
    try:
        memories = get_user_memories(user_id_int)
        memory_text = ";".join([m["content"] for m in memories]) if memories else ""
        # 支持纯文本返回，方便 Dify HTTP 请求节点直接引用
        if request.query_params.get("format") == "text":
            return PlainTextResponse(content=memory_text)
        return {
            "success": True,
            "memory_text": memory_text
        }
    except Exception as e:
        logger.error(f"Error getting memories for user_id={user_id_raw}: {e}")
        raise HTTPException(status_code=500, detail=f"获取习惯失败: {str(e)}")

@app.post("/api/external/memory")
async def external_update_memory(request: Request):
    """
    供 Dify 应用调用，用于更新用户习惯。
    请求头必须携带 X-External-API-Key。
    """
    _check_memory_api_enabled()
    _check_external_api_key(request)
    
    data = await request.json()
    user_id_raw = request.headers.get("X-Portal-User-Id") or data.get("user_id")
    action = data.get("action", "add")
    
    if not user_id_raw:
        raise HTTPException(status_code=400, detail="user_id is required (Header: X-Portal-User-Id or Body: user_id)")
    
    # 支持数字 id 或字符串 userid
    try:
        user_id_int = int(user_id_raw)
    except ValueError:
        user = get_user_by_userid(user_id_raw)
        if not user:
            raise HTTPException(status_code=400, detail="user_id must be a valid numeric user ID or registered userid")
        user_id_int = user["id"]
    
    updated = 0
    
    if action == "add":
        content = data.get("content", "").strip()
        if content and len(content) <= 500:
            create_user_memory(user_id_int, content, source="auto")
            updated += 1
    
    elif action == "update":
        memory_id = data.get("memory_id")
        content = data.get("content", "").strip()
        if memory_id and content:
            update_user_memory(memory_id, content=content)
            updated += 1
    
    elif action == "delete":
        memory_id = data.get("memory_id")
        if memory_id:
            delete_user_memory(memory_id)
            updated += 1
    
    return {"success": True, "updated": updated}

# ==================== 管理员系统配置 ====================

@app.get("/api/admin/settings")
async def admin_get_settings(request: Request):
    """管理员获取系统配置"""
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Forbidden")
    
    return {
        "external_api_key": get_external_api_key(),
        "portal_url": PORTAL_URL,
        "memory_api_enabled": is_memory_api_enabled(),
        "instant_reply_enabled": get_setting("instant_reply_enabled") == "1",
        "instant_reply_llm_enabled": get_setting("instant_reply_llm_enabled") == "1",
        "instant_reply_mode": get_setting("instant_reply_mode") or "single",
        "instant_reply_interval": get_setting("instant_reply_interval") or "5",
        "instant_reply_max_tokens": get_setting("instant_reply_max_tokens") or "40",
        "instant_reply_api_key": get_setting("instant_reply_api_key") or "",
        "instant_reply_base_url": get_setting("instant_reply_base_url") or "",
        "instant_reply_model": get_setting("instant_reply_model") or "",
        "instant_reply_prompt": get_setting("instant_reply_prompt") or ""
    }

@app.post("/api/admin/settings")
async def admin_update_settings(request: Request):
    """管理员更新系统配置"""
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Forbidden")
    
    data = await request.json()
    key = data.get("key")
    value = data.get("value", "")
    
    logger.info(f"admin_update_settings: key={key}, value={value!r}, type={type(value).__name__}")
    
    if key == "external_api_key":
        set_setting("external_api_key", value)
        return {"success": True, "message": "外部 API Key 已更新"}
    
    if key == "memory_api_enabled":
        set_setting("memory_api_enabled", "1" if value else "")
        logger.info(f"memory_api_enabled set to: {'1' if value else ''} (value was {value!r})")
        return {"success": True, "message": "习惯接口开关已更新"}
    
    # 智能瞬时回复配置
    instant_reply_keys = [
        "instant_reply_enabled", "instant_reply_llm_enabled", "instant_reply_mode", "instant_reply_interval",
        "instant_reply_max_tokens", "instant_reply_api_key", "instant_reply_base_url",
        "instant_reply_model", "instant_reply_prompt"
    ]
    if key in instant_reply_keys:
        set_setting(key, str(value))
        return {"success": True, "message": "瞬时回复配置已更新"}
    
    return {"success": False, "message": "不支持的配置项"}

