#!/usr/bin/env python3
"""
本地端口暴露工具 — 无需公网IP, 免费将内网服务暴露到公网
=========================================================
原理: 通过 SSH 反向隧道连接到免费公共服务 (serveo.net)

用法:
  python expose.py                     # 交互式输入端口
  python expose.py -P 8080             # 暴露网页
  python expose.py -P 3306 -t tcp      # 暴露数据库
  python expose.py -P 8080,3306        # 多个端口
"""

import subprocess
import sys
import argparse
import signal
import threading
import re
import time
import os
import shutil


SERVEO_HOST = "serveo.net"
SERVEO_HTTP_PORT = 80   # HTTP: 获得域名
SERVEO_TCP_PORT = 0     # TCP:  自动分配端口
SSH_TIMEOUT = 30

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-o", "ConnectTimeout=10",
    "-T",  # 不分配伪终端
]


def check_ssh():
    """检查SSH是否可用"""
    if shutil.which("ssh") is None:
        print("❌ 未找到 SSH 客户端, 请安装 OpenSSH")
        print("   Windows: 设置 → 应用 → 可选功能 → 添加 OpenSSH 客户端")
        sys.exit(1)
    return True


def find_ssh_key():
    """查找已有的SSH密钥"""
    home = os.path.expanduser("~")
    for name in ["id_ed25519", "id_rsa", "id_ecdsa"]:
        path = os.path.join(home, ".ssh", name)
        if os.path.exists(path):
            return path
    return None


def parse_public_url(line: str) -> str | None:
    """从 serveo.net 输出中提取公网地址"""
    # HTTP: "Forwarding HTTP traffic from https://xxxx.serveo.net"
    m = re.search(r"from\s+(https?://[\w.-]+)", line, re.IGNORECASE)
    if m:
        return m.group(1)
    # TCP: "Forwarding TCP connections from serveo.net:12345"
    m = re.search(r"from\s+([\w.-]+:\d+)", line, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def run_tunnel(local_port: int, tunnel_type: str, subdomain: str = None) -> str | None:
    """
    启动单个SSH反向隧道, 返回公网地址.
    HTTP: 获得 https://xxx.serveo.net 域名
    TCP:  获得 serveo.net:xxxxx 地址
    """
    if tunnel_type == "http":
        remote_port = SERVEO_HTTP_PORT
    else:
        remote_port = SERVEO_TCP_PORT

    # 构建远程规格
    if subdomain and tunnel_type == "http":
        remote_spec = f"{subdomain}:{remote_port}:localhost:{local_port}"
    else:
        remote_spec = f"{remote_port}:localhost:{local_port}"

    cmd = ["ssh"] + SSH_OPTS + ["-R", remote_spec, SERVEO_HOST]
    print(f"  → ssh -R {remote_port}:localhost:{local_port} {SERVEO_HOST}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    except FileNotFoundError:
        print("  ❌ 找不到 ssh 命令")
        return None

    result = {"url": None, "done": False}

    # serveo 的提示信息输出在 stderr
    def read_stderr():
        for line in iter(proc.stderr.readline, ""):
            line = line.strip()
            if line:
                if "Warning:" not in line and "Authenticated" not in line:
                    print(f"     {line}")
                url = parse_public_url(line)
                if url:
                    result["url"] = url
                    result["done"] = True
                    print(f"\n  ✅ 公网地址: {url}\n")

    t = threading.Thread(target=read_stderr, daemon=True)
    t.start()

    # 等待获取地址
    waited = 0
    while not result["done"] and waited < SSH_TIMEOUT:
        if proc.poll() is not None:
            print(f"  ❌ SSH 连接失败 (退出码: {proc.returncode})")
            print(f"     请确认 serveo.net 是否可达, 或稍后重试")
            return None
        time.sleep(0.5)
        waited += 0.5

    if result["url"]:
        return result["url"]
    else:
        print(f"  ⚠️  SSH已连接但未能解析公网地址")
        print(f"     请查看上方输出, 地址可能已显示在其中")
        return None


def handle_sigint(signum, frame):
    print("\n\n正在关闭所有隧道...\n")
    sys.exit(0)


# ---------------------------------------------------------------------------
# 交互模式
# ---------------------------------------------------------------------------
def interactive_mode():
    """交互式输入端口"""
    print()
    print("  ╔═══════════════════════════════════╗")
    print("  ║   本地端口暴露工具 (无需公网IP)  ║")
    print("  ║   基于 serveo.net 免费隧道服务   ║")
    print("  ╚═══════════════════════════════════╝")
    print()

    key = find_ssh_key()
    if key:
        print(f"  SSH密钥: {key}")
    else:
        print("  ⚠️  未检测到SSH密钥")
        print("  请先运行: ssh-keygen -t ed25519")
        print("  (serveo.net 仅支持密钥登录)")

    print()
    print("  输入要暴露的端口配置:")
    print("    单个端口      → 8080         (默认HTTP)")
    print("    指定类型      → 3306:tcp     (数据库)")
    print("    多个端口      → 8080,3306:tcp,3000:http")
    print()

    raw = input("  > ").strip()
    if not raw:
        return None

    configs = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        try:
            port = int(parts[0].strip())
        except ValueError:
            print(f"  忽略无效端口: {parts[0]}")
            continue
        ptype = parts[1].strip().lower() if len(parts) > 1 else "http"
        if ptype not in ("http", "tcp"):
            ptype = "http"
        configs.append((port, ptype))

    return configs if configs else None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    signal.signal(signal.SIGINT, handle_sigint)
    check_ssh()

    parser = argparse.ArgumentParser(
        description="本地端口暴露工具 — 无需公网IP, 免费将内网服务暴露到公网",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python expose.py                        # 交互式
  python expose.py -P 8080                # 暴露网页 (HTTP)
  python expose.py -P 3306 -t tcp         # 暴露数据库 (TCP)
  python expose.py -P 8080,3000           # 多个端口
  python expose.py -P 8080 -s myapp       # 自定义子域名
        """
    )
    parser.add_argument("-P", "--ports", default=None,
                        help="要暴露的端口, 逗号分隔")
    parser.add_argument("-t", "--type", default="http", choices=["http", "tcp"],
                        help="http=网页获得域名, tcp=数据库/TCP (默认: http)")
    parser.add_argument("-s", "--subdomain", default=None,
                        help="自定义子域名, 如 myapp → myapp.serveo.net (仅HTTP)")

    args = parser.parse_args()

    if args.ports:
        ports = [int(p.strip()) for p in args.ports.split(",") if p.strip()]
        if not ports:
            print("错误: 无效端口")
            sys.exit(1)
        configs = [(p, args.type) for p in ports]
    else:
        configs = interactive_mode()
        if not configs:
            return

    # 子域名仅对单端口HTTP有效
    subdomain = args.subdomain if (args.subdomain and len(configs) == 1 and configs[0][1] == "http") else None

    print()
    print("=" * 50)
    for port, ptype in configs:
        print(f"  localhost:{port} → 公网 ({ptype.upper()})")
    print("=" * 50)
    print()

    # 依次启动隧道 (serveo 多隧道需要独立SSH连接)
    results: dict[int, str] = {}
    for port, ptype in configs:
        print(f"── 暴露 localhost:{port} ──")
        url = run_tunnel(port, ptype, subdomain)
        if url:
            results[port] = url
        time.sleep(0.5)

    print()
    print("=" * 50)
    if results:
        print("  公网访问地址:")
        for port, url in results.items():
            print(f"    localhost:{port}  →  {url}")
    else:
        print("  未能建立任何隧道")
    print()
    print("  按 Ctrl+C 停止")
    print("=" * 50)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已关闭")


if __name__ == "__main__":
    main()
