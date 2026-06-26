# ==============================================================================
# 🎙️ WhisperX Voiceprint System - API Endpoints 自动化测试脚本
# ==============================================================================

import os
import sys
import pytest
from fastapi.testclient import TestClient

# 引入项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from voiceprint_system.server import app, load_server_config

# 创建 TestClient 实例
client = TestClient(app)

def test_config_generation():
    """验证启动时是否成功自动初始化并生成 jwt_secret_key 及管理员密码"""
    cfg = load_server_config()
    assert "admin_username" in cfg
    assert "admin_password_hash" in cfg
    assert "jwt_secret_key" in cfg
    assert cfg["admin_username"] == "admin"

def test_login_flow():
    """测试 JWT 认证登录及授权访问控制流"""
    # 1. 错误的登录凭证测试
    resp = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "wrong_password"}
    )
    assert resp.status_code == 401
    
    # 2. 正确的登录测试
    resp = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200
    token_data = resp.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "bearer"
    
    # 3. 拦截未携带 Token 的非授权请求
    resp_meetings = client.get("/api/meetings")
    assert resp_meetings.status_code == 401
    
    # 4. 携带 Token 的授权请求测试
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    resp_verify = client.get("/api/auth/verify", headers=headers)
    assert resp_verify.status_code == 200
    assert resp_verify.json()["username"] == "admin"

def test_meetings_and_speakers_query():
    """测试会议列表和声纹库的查询接口"""
    # 获取 Token
    resp = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    
    # 查询会议列表
    resp_meetings = client.get("/api/meetings", headers=headers)
    assert resp_meetings.status_code == 200
    assert isinstance(resp_meetings.json(), list)
    
    # 查询已命名发言人列表
    resp_named = client.get("/api/speakers/named", headers=headers)
    assert resp_named.status_code == 200
    assert isinstance(resp_named.json(), list)
    
    # 查询待标定全局声纹池列表
    resp_unknown = client.get("/api/speakers/unknown", headers=headers)
    assert resp_unknown.status_code == 200
    assert isinstance(resp_unknown.json(), list)

def test_system_config_endpoints():
    """测试配置读写接口"""
    # 获取 Token
    resp = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    
    # 读取配置
    resp_cfg = client.get("/api/config", headers=headers)
    assert resp_cfg.status_code == 200
    config_data = resp_cfg.json()
    assert "provider" in config_data
    assert "admin_password_hash" not in config_data  # 安全隐蔽检查
    
    # 更新配置测试
    update_payload = {
        "provider": "ollama",
        "ollama_host": "http://localhost:11434",
        "ollama_model": "gemma4:12b-32k",
        "deepseek_api_key": "••••••••••••••••"
    }
    resp_update = client.post("/api/config", json=update_payload, headers=headers)
    assert resp_update.status_code == 200
