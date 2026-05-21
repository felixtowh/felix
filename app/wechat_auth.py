import os
import httpx
from typing import Optional, Dict

WECHAT_CORP_ID = os.getenv("WECHAT_CORP_ID", "")
WECHAT_AGENT_ID = os.getenv("WECHAT_AGENT_ID", "")
WECHAT_SECRET = os.getenv("WECHAT_SECRET", "")

# 缓存 access_token
_access_token_cache = {
    "token": None,
    "expires_at": 0
}

async def get_access_token() -> Optional[str]:
    """获取企微 access_token"""
    import time
    
    # 检查缓存
    if _access_token_cache["token"] and time.time() < _access_token_cache["expires_at"]:
        return _access_token_cache["token"]
    
    if not WECHAT_CORP_ID or not WECHAT_SECRET:
        return None
    
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    params = {
        "corpid": WECHAT_CORP_ID,
        "corpsecret": WECHAT_SECRET
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        data = response.json()
        
        if data.get("errcode") == 0:
            token = data["access_token"]
            expires_in = data.get("expires_in", 7200)
            _access_token_cache["token"] = token
            _access_token_cache["expires_at"] = time.time() + expires_in - 300  # 提前5分钟过期
            return token
        else:
            print(f"获取 access_token 失败: {data}")
            return None

async def get_department_map() -> Dict[int, str]:
    """获取企微部门ID与名称映射"""
    access_token = await get_access_token()
    if not access_token:
        return {}
    
    url = "https://qyapi.weixin.qq.com/cgi-bin/department/list"
    params = {"access_token": access_token}
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        data = response.json()
        
        if data.get("errcode") == 0:
            return {dept["id"]: dept["name"] for dept in data.get("department", [])}
        else:
            print(f"获取部门列表失败: {data}")
            return {}


def resolve_departments(dept_ids: list, dept_map: Dict[int, str]) -> list:
    """将部门ID数组转为名称数组"""
    if not dept_ids:
        return []
    names = []
    for did in dept_ids:
        name = dept_map.get(did)
        if name:
            names.append(name)
    return names


async def get_user_info(code: str) -> Optional[Dict]:
    """
    通过 OAuth code 获取用户信息
    
    Args:
        code: OAuth 授权后返回的 code
        
    Returns:
        用户信息字典，包含 userid、name 等
    """
    access_token = await get_access_token()
    if not access_token:
        return None
    
    # 第一步：获取 user_ticket
    url = f"https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo"
    params = {
        "access_token": access_token,
        "code": code
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        data = response.json()
        
        if data.get("errcode") != 0:
            print(f"获取用户信息失败: {data}")
            return None
        
        userid = data.get("userid")
        user_ticket = data.get("user_ticket")
        
        if not userid:
            return None
        
        # 获取部门名称映射
        dept_map = await get_department_map()
        
        # 第二步：获取详细用户信息
        if user_ticket:
            detail_url = f"https://qyapi.weixin.qq.com/cgi-bin/auth/getuserdetail"
            detail_params = {"access_token": access_token}
            detail_payload = {"user_ticket": user_ticket}
            
            detail_response = await client.post(
                detail_url, 
                params=detail_params, 
                json=detail_payload
            )
            detail_data = detail_response.json()
            
            if detail_data.get("errcode") == 0:
                return {
                    "userid": userid,
                    "name": detail_data.get("name", ""),
                    "department": resolve_departments(detail_data.get("department", []), dept_map),
                    "position": detail_data.get("position", ""),
                    "mobile": detail_data.get("mobile", ""),
                    "email": detail_data.get("email", ""),
                    "avatar": detail_data.get("avatar", ""),
                    "gender": detail_data.get("gender", 0),
                    "english_name": detail_data.get("english_name", ""),
                    "alias": detail_data.get("alias", ""),
                }
        
        # 如果没有 user_ticket 或获取详情失败，尝试用通讯录接口兜底获取姓名
        fallback_user = await get_user_info_by_id(userid)
        if fallback_user:
            return fallback_user
        
        # 通讯录也失败了，只返回 userid
        return {
            "userid": userid,
            "name": userid,
            "department": [],
            "position": "",
            "mobile": "",
            "email": "",
            "avatar": "",
            "gender": 0,
            "english_name": "",
            "alias": "",
        }

async def get_user_info_by_id(userid: str) -> Optional[Dict]:
    """
    通过 userid 获取用户信息（需要通讯录权限）
    
    Args:
        userid: 企微用户ID
        
    Returns:
        用户信息字典
    """
    access_token = await get_access_token()
    if not access_token:
        return None
    
    url = f"https://qyapi.weixin.qq.com/cgi-bin/user/get"
    params = {
        "access_token": access_token,
        "userid": userid
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        data = response.json()
        
        if data.get("errcode") == 0:
            dept_map = await get_department_map()
            return {
                "userid": data.get("userid", ""),
                "name": data.get("name", ""),
                "department": resolve_departments(data.get("department", []), dept_map),
                "position": data.get("position", ""),
                "mobile": data.get("mobile", ""),
                "email": data.get("email", ""),
                "avatar": data.get("avatar", ""),
                "gender": data.get("gender", 0),
                "english_name": data.get("english_name", ""),
                "alias": data.get("alias", ""),
            }
        else:
            print(f"获取用户详情失败: {data}")
            return None

def build_oauth_url(redirect_uri: str, state: str = "") -> str:
    """
    构建企微 OAuth 授权 URL（应用内静默授权）
    
    Args:
        redirect_uri: 授权后跳转的地址
        state: 状态参数（可选）
        
    Returns:
        OAuth 授权 URL
    """
    from urllib.parse import quote
    
    if not WECHAT_CORP_ID or not WECHAT_AGENT_ID:
        return ""
    
    encoded_redirect = quote(redirect_uri, safe='')
    
    url = (
        f"https://open.weixin.qq.com/connect/oauth2/authorize"
        f"?appid={WECHAT_CORP_ID}"
        f"&redirect_uri={encoded_redirect}"
        f"&response_type=code"
        f"&scope=snsapi_privateinfo"
        f"&agentid={WECHAT_AGENT_ID}"
    )
    
    if state:
        url += f"&state={state}"
    
    url += "#wechat_redirect"
    
    return url


def build_qrconnect_url(redirect_uri: str, state: str = "login") -> str:
    """
    构建企微 Web 扫码登录 URL（浏览器内扫码）
    
    Args:
        redirect_uri: 授权后跳转的地址
        state: 状态参数（默认 login）
        
    Returns:
        企微 Web 扫码登录 URL
    """
    from urllib.parse import quote
    
    if not WECHAT_CORP_ID or not WECHAT_AGENT_ID:
        return ""
    
    encoded_redirect = quote(redirect_uri, safe='')
    
    url = (
        f"https://open.work.weixin.qq.com/wwopen/sso/qrConnect"
        f"?appid={WECHAT_CORP_ID}"
        f"&agentid={WECHAT_AGENT_ID}"
        f"&redirect_uri={encoded_redirect}"
        f"&state={state}"
    )
    
    return url



# =============== ISV 占位符（Deploy 项目不使用 ISV 功能）===============

WECHAT_SUITE_ID = ""
WECHAT_SUITE_SECRET = ""
WECHAT_TOKEN = ""
WECHAT_ENCODING_AES_KEY = ""

async def get_suite_access_token() -> Optional[str]:
    return None

def get_suite_ticket() -> Optional[str]:
    return None

def save_suite_ticket(ticket: str):
    pass

def get_auth_corp(corp_id: str = None) -> Optional[Dict]:
    return None

def save_auth_corp(corp_info: Dict):
    pass

async def exchange_auth_code(suite_access_token: str, auth_code: str) -> Optional[Dict]:
    return None

def build_isv_oauth_url(corp_id: str, agent_id: str, redirect_uri: str, state: str = "", scope: str = "snsapi_base") -> str:
    return ""

async def get_user_info_3rd(code: str, need_detail: bool = False) -> Optional[Dict]:
    return None

async def handle_callback(xml_body: str, msg_signature: str, timestamp: str, nonce: str) -> str:
    return "success"

def calculate_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    import hashlib
    tmp_list = [token, timestamp, nonce, encrypt]
    tmp_list.sort()
    tmp_str = "".join(tmp_list)
    return hashlib.sha1(tmp_str.encode()).hexdigest()

def decrypt_message(encrypt: str, encoding_aes_key: str) -> str:
    return encrypt
