#!/usr/bin/env python3
"""
30 秒精确捕获 appmsg_token
运行: python3 capture.py
然后立刻开微信 PC → 打开一篇公众号文章 → 自动完成
"""

import json, os, signal, subprocess, sys, time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(__file__).parent
CAPTURE_OUTPUT = SCRIPT_DIR / "_captured.json"
READING_CONFIG = SCRIPT_DIR / "reading_config.json"

MITMDUMP = Path(sys.executable).parent / "mitmdump"

# ====== 代理开关 ======
def get_active_service():
    """获取当前活跃的网络服务"""
    r = subprocess.run(["networksetup", "-listnetworkserviceorder"], capture_output=True, text=True)
    names = []
    for line in r.stdout.split("\n"):
        if line.strip().startswith("(") and ")" in line:
            name = line.split(") ")[1].strip()
            names.append(name)
    # 优先以太网，其次 Wi-Fi
    for n in names:
        if "Ethernet" in n or "USB" in n or "LAN" in n:
            return n
    for n in names:
        if "Wi-Fi" in n:
            return n
    return names[0] if names else "Wi-Fi"

def proxy(state: str, svc: str):
    for proto in ["webproxy", "securewebproxy"]:
        subprocess.run(["networksetup", f"-set{proto}state", svc, state], capture_output=True)
    if state == "on":
        subprocess.run(["networksetup", "-setwebproxy", svc, "127.0.0.1", "8080"], capture_output=True)
        subprocess.run(["networksetup", "-setsecurewebproxy", svc, "127.0.0.1", "8080"], capture_output=True)

# ====== 捕获 addon ======
ADDON_CODE = f"""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from mitmproxy import ctx, http

OUTPUT = Path("{CAPTURE_OUTPUT}")

class Capture:
    def request(self, flow: http.HTTPFlow):
        if "getappmsgext" not in flow.request.pretty_url:
            return
        qs = parse_qs(urlparse(flow.request.pretty_url).query)
        token = qs.get("appmsg_token", [""])[0]
        cookie = flow.request.headers.get("Cookie", "") or flow.request.headers.get("cookie", "")
        if token and cookie:
            OUTPUT.write_text(json.dumps({{"appmsg_token": token, "cookie": cookie}}, ensure_ascii=False))
            ctx.log.info("CAPTURED")

addons = [Capture()]
"""

def main():
    if CAPTURE_OUTPUT.exists():
        CAPTURE_OUTPUT.unlink()

    svc = get_active_service()
    print(f"📶 {svc}")

    # 写 addon
    addon_file = SCRIPT_DIR / "_addon.py"
    addon_file.write_text(ADDON_CODE)

    # 启 mitmdump
    proc = subprocess.Popen(
        [str(MITMDUMP), "-s", str(addon_file), "--set", "block_global=false", "-q"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(3)

    # 开代理
    proxy("on", svc)
    print("🔌 代理开启")
    print()
    print("👉 现在打开微信 PC → 打开一篇公众号文章")
    print("⏳ 等待... (60秒)")
    print()

    # 等捕获
    for i in range(60):
        time.sleep(1)
        if i % 10 == 9:
            print(f"  {i+1}s")
        if CAPTURE_OUTPUT.exists():
            break

    # 关
    proxy("off", svc)
    proc.terminate()
    proc.wait(timeout=3)
    addon_file.unlink()
    print()

    if CAPTURE_OUTPUT.exists():
        data = json.loads(CAPTURE_OUTPUT.read_text())
        CAPTURE_OUTPUT.unlink()
        print(f"✅ 捕获成功!")
        print(f"   token: {data['appmsg_token'][:40]}...")

        config = {"appmsg_token": data["appmsg_token"], "cookie": data["cookie"], "interval": 8, "max_per_run": 30}
        with open(READING_CONFIG, "w") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"💾 已保存")
    else:
        print("⏰ 未捕获，请确认微信已打开文章并完全加载")

    print("🔌 代理已关闭")


if __name__ == "__main__":
    main()
