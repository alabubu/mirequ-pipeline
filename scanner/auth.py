#!/usr/bin/env python3
"""
微信公众平台自动登录模块
- 使用 Selenium + Chrome 打开后台
- 等待扫码登录
- 自动提取 token 和 cookie
- 持久化保存，过期自动重新登录

用法:
  from auth import WxAuth
  auth = WxAuth()
  cookie, token = auth.ensure_credentials()  # 自动登录/复用
"""

import json
import os
import re
import time
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")


class WxAuth:
    """微信公众平台自动登录与凭证管理"""

    MP_URL = "https://mp.weixin.qq.com/"

    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.browser = None
        self._config = self._load_config()

    # ========== 配置读写 ==========
    def _load_config(self) -> dict:
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_config(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

    # ========== 凭证有效性检测 ==========
    def test_credentials(self) -> bool:
        """快速测试当前 cookie/token 是否有效（不触发限流 API）"""
        import requests

        cookie = self._config.get("cookie", "")
        token = self._config.get("token", "")

        if not cookie or not token:
            return False

        try:
            # 用轻量请求验证登录态：访问后台首页，检查是否被重定向到登录页
            r = requests.get(
                "https://mp.weixin.qq.com/",
                headers={
                    "Cookie": cookie,
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                },
                timeout=10,
                allow_redirects=True,
            )
            return "token=" in r.url and "login" not in r.url.lower()
        except Exception:
            return False

    # ========== Selenium 登录 ==========
    def _setup_browser(self):
        """配置 Chrome 浏览器"""
        options = Options()
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1280,800")
        # 不设 headless，用户需要看到二维码扫码

        service = Service(ChromeDriverManager().install())
        self.browser = webdriver.Chrome(service=service, options=options)

    def _wait_for_login(self, timeout: int = 180) -> bool:
        """等待扫码登录成功"""
        try:
            self.browser.get(self.MP_URL)

            # 等待二维码 iframe
            try:
                qr_frame = WebDriverWait(self.browser, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "weui-desktop-qrcheck__iframe"))
                )
                self.browser.switch_to.frame(qr_frame)
                WebDriverWait(self.browser, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "qrcode"))
                )
                self.browser.switch_to.default_content()
                print("📱 请用微信扫描二维码登录...")
            except Exception:
                print("可能已登录或页面结构变化，继续检测...")

            # 轮询检测登录成功
            start = time.time()
            while time.time() - start < timeout:
                try:
                    indicators = [
                        "weui-desktop-panel__title",
                        "menu_item",
                        "menuBar",
                    ]
                    page_source = self.browser.page_source
                    url = self.browser.current_url

                    if any(cls in page_source for cls in indicators) or "/cgi-bin/home" in url:
                        print("✅ 登录成功！")
                        time.sleep(3)
                        return True
                except Exception:
                    pass
                time.sleep(2)

            print("⏰ 登录超时")
            return False

        except Exception as e:
            print(f"⚠️ 登录异常: {e}")
            return False

    def _extract_token(self) -> str:
        """多策略提取 token"""
        strategies = [
            # 策略1: localStorage
            lambda: self.browser.execute_script("""
                for (var i = 0; i < localStorage.length; i++) {
                    var k = localStorage.key(i);
                    if (k.includes('token') || k.includes('Token'))
                        return localStorage.getItem(k);
                }
                return null;
            """),
            # 策略2: URL 参数
            lambda: re.search(
                r'[?&]token=(\d+)', self.browser.current_url
            ).group(1) if re.search(r'[?&]token=(\d+)', self.browser.current_url) else None,
            # 策略3: 页面源码正则
            lambda: (
                m.group(1) if (m := re.search(r'token[=:]\s*["\']?(\d{6,12})["\']?', self.browser.page_source))
                else None
            ),
            # 策略4: AJAX 请求
            lambda: self.browser.execute_script("""
                try {
                    var xhr = new XMLHttpRequest();
                    xhr.open('GET', '/cgi-bin/bizattr?action=get_attr&lang=zh_CN', false);
                    xhr.send();
                    var r = JSON.parse(xhr.responseText);
                    return (r.base_resp && r.base_resp.token) ? r.base_resp.token : null;
                } catch(e) { return null; }
            """),
        ]

        for i, strat in enumerate(strategies):
            try:
                token = strat()
                if token and len(str(token)) >= 6:
                    print(f"  ✅ 策略{i+1} 获取到 token: {token}")
                    return str(token)
            except Exception:
                continue

        print("  ❌ 所有策略均未获取到 token")
        return ""

    def _extract_cookie(self) -> str:
        """提取所有 cookie 拼接为字符串"""
        cookies = self.browser.get_cookies()
        return "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    def login(self) -> bool:
        """执行完整登录流程"""
        try:
            self._setup_browser()
            if not self._wait_for_login():
                return False

            token = self._extract_token()
            cookie = self._extract_cookie()

            if not token or not cookie:
                print("❌ 无法获取完整凭证")
                return False

            self._config["cookie"] = cookie
            self._config["token"] = token
            self._config["last_login"] = datetime.now().isoformat()
            self._save_config()

            print(f"💾 凭证已保存")
            print(f"   token: {token}")
            print(f"   cookie: {cookie[:60]}...")
            return True

        finally:
            if self.browser:
                self.browser.quit()
                self.browser = None

    # ========== 公开接口 ==========
    def ensure_credentials(self) -> tuple[str, str]:
        """
        确保有有效凭证，必要时自动登录
        返回 (cookie, token)
        """
        # 先测试现有凭证
        if self.test_credentials():
            print("✅ 现有凭证有效")
            return self._config["cookie"], self._config["token"]

        # 凭证无效，重新登录
        print("🔄 凭证已过期，需要重新登录...")
        if self.login():
            return self._config["cookie"], self._config["token"]

        raise RuntimeError("登录失败，无法获取凭证")

    def get_cookie(self) -> str:
        return self._config.get("cookie", "")

    def get_token(self) -> str:
        return self._config.get("token", "")


# ========== 命令行入口 ==========
if __name__ == "__main__":
    import sys

    auth = WxAuth()

    if "--test" in sys.argv:
        valid = auth.test_credentials()
        print(f"凭证有效: {valid}")
    elif "--login" in sys.argv or "--force" in sys.argv:
        auth.login()
    else:
        try:
            cookie, token = auth.ensure_credentials()
            print(f"\n✅ 凭证就绪")
            print(f"token: {token}")
        except RuntimeError as e:
            print(f"❌ {e}")
            sys.exit(1)
