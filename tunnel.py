#!/usr/bin/env python3
"""
内网穿透工具 — 将内网服务映射到公网服务器
============================================
用法:
  服务端(部署在公网服务器):  python tunnel.py server
  客户端(部署在内网机器):    python tunnel.py client

架构:
  [外部用户] --> [公网服务器:映射端口] --> [控制/数据隧道] --> [内网客户端:本地端口]
"""

import socket
import struct
import json
import threading
import sys
import time
import logging
import argparse

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tunnel")

# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------
DEFAULT_CONTROL_PORT = 7000
DEFAULT_DATA_PORT = 7001
DEFAULT_PASSWORD = ""  # 必须由用户设置, 无默认值
BUFFER_SIZE = 65536


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def recv_exact(sock, n):
    """精确接收 n 字节"""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def send_frame(sock, data: bytes):
    """发送带长度前缀的数据帧"""
    header = struct.pack(">I", len(data))
    sock.sendall(header + data)


def recv_frame(sock):
    """接收带长度前缀的数据帧"""
    header = recv_exact(sock, 4)
    if header is None:
        return None
    length = struct.unpack(">I", header)[0]
    if length > 10 * 1024 * 1024:  # 10MB上限
        return None
    return recv_exact(sock, length)


def send_json(sock, obj: dict):
    """发送JSON控制消息"""
    send_frame(sock, json.dumps(obj).encode("utf-8"))


def recv_json(sock):
    """接收JSON控制消息"""
    data = recv_frame(sock)
    if data is None:
        return None
    return json.loads(data.decode("utf-8"))


def bridge(a: socket.socket, b: socket.socket, name: str = ""):
    """双向桥接两个socket, 任一方向关闭即结束"""
    def _pipe(src, dst, direction):
        try:
            while True:
                data = src.recv(BUFFER_SIZE)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            try:
                src.shutdown(socket.SHUT_RD)
            except Exception:
                pass
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    t1 = threading.Thread(target=_pipe, args=(a, b, "->"), daemon=True)
    t2 = threading.Thread(target=_pipe, args=(b, a, "<-"), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()


# ---------------------------------------------------------------------------
# 服务端
# ---------------------------------------------------------------------------
class TunnelServer:
    def __init__(self, control_port, data_port, password):
        self.control_port = control_port
        self.data_port = data_port
        self.password = password
        self.control_sock = None       # 到客户端的控制连接
        self.send_lock = threading.Lock()
        self.pending = {}              # conn_id -> (external_sock, timestamp)
        self.lock = threading.Lock()
        self.listeners = {}            # port -> socket
        self.running = True
        self.stats = {"connections": 0, "bytes_in": 0, "bytes_out": 0}

    def start(self):
        log.info("=" * 50)
        log.info("  内网穿透服务端已启动")
        log.info(f"  控制端口: {self.control_port}")
        log.info(f"  数据端口: {self.data_port}")
        log.info("=" * 50)

        threading.Thread(target=self._data_listener, daemon=True).start()
        self._control_listener()

    def _control_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.control_port))
        sock.listen(5)
        log.info(f"[Server] 等待客户端连接...")

        try:
            conn, addr = sock.accept()
            log.info(f"[Server] 客户端已连接: {addr[0]}:{addr[1]}")
            self._handle_client(conn)
        except KeyboardInterrupt:
            pass
        finally:
            sock.close()
            self.running = False

    def _handle_client(self, conn):
        self.control_sock = conn

        # 1. 接收注册信息
        msg = recv_json(conn)
        if not msg:
            log.error("[Server] 注册消息接收失败")
            conn.close()
            return
        if msg.get("password") != self.password:
            log.warning("[Server] 客户端认证失败")
            send_json(conn, {"status": "error", "msg": "密码错误"})
            conn.close()
            return

        ports = msg.get("ports", [])
        if not ports:
            send_json(conn, {"status": "error", "msg": "未指定端口"})
            conn.close()
            return

        log.info(f"[Server] 端口映射注册: {ports}")

        # 2. 回复确认
        send_json(conn, {"status": "ok", "data_port": self.data_port})
        log.info(f"[Server] 注册确认已发送")

        # 3. 启动公网监听
        for port in ports:
            threading.Thread(target=self._public_listener, args=(port,), daemon=True).start()

        # 4. 开始接收控制消息(通知客户端有新连接)
        self._control_recv_loop()

    def _control_recv_loop(self):
        """接收客户端发来的控制消息(如conn_ready, conn_close)"""
        try:
            while self.running:
                msg = recv_json(self.control_sock)
                if msg is None:
                    break
                msg_type = msg.get("type", "")
                if msg_type == "pong":
                    pass  # 心跳回复
                elif msg_type == "conn_close":
                    conn_id = msg.get("conn_id", "")
                    log.info(f"[Server] 隧道关闭: {conn_id[:20]}...")
        except Exception as e:
            log.error(f"[Server] 控制通道错误: {e}")
        finally:
            log.info("[Server] 客户端已断开")
            self.running = False

    def _public_listener(self, port):
        """在公网端口上监听, 接受外部连接"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            sock.listen(50)
            sock.settimeout(1.0)
            self.listeners[port] = sock
            log.info(f"[Server] 公网端口已开放: 0.0.0.0:{port}")

            while self.running:
                try:
                    ext_conn, addr = sock.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break

                if not self.control_sock:
                    ext_conn.close()
                    continue

                conn_id = f"{int(time.time()*1000000)}-{port}-{addr[0]}"
                with self.lock:
                    self.pending[conn_id] = (ext_conn, time.time())

                log.info(f"[Server] 新外部连接: {addr[0]}:{addr[1]} -> 端口{port} [{conn_id[:20]}...]")
                self.stats["connections"] += 1

                # 通知客户端建立数据隧道
                try:
                    with self.send_lock:
                        send_json(self.control_sock, {
                            "type": "new_conn",
                            "conn_id": conn_id,
                            "port": port,
                        })
                except Exception:
                    ext_conn.close()
                    with self.lock:
                        self.pending.pop(conn_id, None)
                    break
        except Exception as e:
            log.error(f"[Server] 端口{port}监听失败: {e}")
        finally:
            sock.close()

    def _data_listener(self):
        """监听客户端发来的数据隧道连接"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.data_port))
        sock.listen(50)
        sock.settimeout(1.0)
        log.info(f"[Server] 数据通道监听: 0.0.0.0:{self.data_port}")

        while self.running:
            try:
                data_conn, addr = sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break

            # 读取conn_id
            try:
                conn_id_data = recv_frame(data_conn)
                if not conn_id_data:
                    data_conn.close()
                    continue
                conn_id = conn_id_data.decode("utf-8")
            except Exception:
                data_conn.close()
                continue

            with self.lock:
                entry = self.pending.pop(conn_id, None)

            if entry is None:
                log.warning(f"[Server] 未找到对应的待处理连接: {conn_id[:30]}...")
                data_conn.close()
                continue

            ext_conn, _ = entry
            log.info(f"[Server] 隧道已建立: [{conn_id[:20]}...]")
            threading.Thread(
                target=self._tunnel_bridge,
                args=(ext_conn, data_conn, conn_id),
                daemon=True,
            ).start()

        sock.close()

    def _tunnel_bridge(self, ext_conn, data_conn, conn_id):
        """桥接外部连接和数据隧道"""
        try:
            bridge(ext_conn, data_conn)
        except Exception as e:
            log.debug(f"[Server] 隧道桥接结束: {e}")
        finally:
            try:
                ext_conn.close()
            except Exception:
                pass
            try:
                data_conn.close()
            except Exception:
                pass
            log.info(f"[Server] 隧道关闭: [{conn_id[:20]}...]")


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class TunnelClient:
    def __init__(self, server_host, control_port, password, ports):
        self.server_host = server_host
        self.control_port = control_port
        self.password = password
        self.ports = ports          # 要映射的本地端口列表
        self.control_sock = None
        self.data_port = None       # 由服务端告知
        self.running = True
        self.active_tunnels = {}
        self.stats = {"connections": 0, "bytes_in": 0, "bytes_out": 0}

    def start(self):
        log.info("=" * 50)
        log.info("  内网穿透客户端已启动")
        log.info(f"  服务端: {self.server_host}:{self.control_port}")
        log.info(f"  映射端口: {self.ports}")
        log.info("=" * 50)

        # 1. 连接控制通道
        self.control_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.control_sock.settimeout(30)
        try:
            self.control_sock.connect((self.server_host, self.control_port))
        except Exception as e:
            log.error(f"[Client] 无法连接服务端: {e}")
            return

        # 2. 注册
        send_json(self.control_sock, {
            "action": "register",
            "password": self.password,
            "ports": self.ports,
        })
        resp = recv_json(self.control_sock)
        if not resp or resp.get("status") != "ok":
            log.error(f"[Client] 注册失败: {resp.get('msg', '未知错误') if resp else '无响应'}")
            self.control_sock.close()
            return

        self.data_port = resp.get("data_port", DEFAULT_DATA_PORT)
        log.info(f"[Client] 注册成功! 数据端口: {self.data_port}")
        self.control_sock.settimeout(None)

        # 3. 开始处理控制消息
        self._control_loop()

    def _control_loop(self):
        """接收服务端控制消息"""
        try:
            while self.running:
                msg = recv_json(self.control_sock)
                if msg is None:
                    log.warning("[Client] 与服务端的控制连接已断开")
                    break

                msg_type = msg.get("type", "")
                if msg_type == "new_conn":
                    conn_id = msg["conn_id"]
                    port = msg["port"]
                    log.info(f"[Client] 收到隧道请求: 端口{port} [{conn_id[:20]}...]")
                    self.stats["connections"] += 1
                    threading.Thread(
                        target=self._create_data_tunnel,
                        args=(conn_id, port),
                        daemon=True,
                    ).start()
                else:
                    log.debug(f"[Client] 未知消息类型: {msg_type}")
        except Exception as e:
            log.error(f"[Client] 控制通道错误: {e}")
        finally:
            self.running = False
            log.info("[Client] 客户端已停止")

    def _create_data_tunnel(self, conn_id, port):
        """创建数据隧道: 连接服务端数据端口 + 连接本地服务, 然后桥接"""
        # 连接服务端数据端口
        data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        data_sock.settimeout(10)
        try:
            data_sock.connect((self.server_host, self.data_port))
        except Exception as e:
            log.error(f"[Client] 无法连接数据端口: {e}")
            return

        # 发送conn_id标识这个隧道
        try:
            send_frame(data_sock, conn_id.encode("utf-8"))
        except Exception as e:
            log.error(f"[Client] 发送conn_id失败: {e}")
            data_sock.close()
            return

        # 连接本地服务
        local_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        local_sock.settimeout(5)
        try:
            local_sock.connect(("127.0.0.1", port))
        except Exception as e:
            log.error(f"[Client] 无法连接本地服务 localhost:{port}: {e}")
            data_sock.close()
            return

        log.info(f"[Client] 隧道已建立: [{conn_id[:20]}...] -> localhost:{port}")
        self.active_tunnels[conn_id] = (data_sock, local_sock)

        # 双向桥接
        try:
            bridge(data_sock, local_sock)
        except Exception as e:
            log.debug(f"[Client] 隧道桥接结束: {e}")
        finally:
            try:
                data_sock.close()
            except Exception:
                pass
            try:
                local_sock.close()
            except Exception:
                pass
            self.active_tunnels.pop(conn_id, None)
            log.info(f"[Client] 隧道关闭: [{conn_id[:20]}...]")


# ---------------------------------------------------------------------------
# 交互式输入
# ---------------------------------------------------------------------------
def input_ports():
    """交互式输入要映射的端口列表"""
    print("\n请输入要映射的端口号 (多个端口用逗号分隔):")
    print("  例: 8080")
    print("  例: 8080,3000,5000")
    print()
    while True:
        raw = input("端口列表 > ").strip()
        if not raw:
            print("  请输入至少一个端口号")
            continue
        try:
            ports = [int(p.strip()) for p in raw.split(",") if p.strip()]
            if not ports:
                print("  请输入至少一个端口号")
                continue
            invalid = [p for p in ports if p < 1 or p > 65535]
            if invalid:
                print(f"  无效端口: {invalid}, 端口范围 1-65535")
                continue
            return ports
        except ValueError:
            print("  格式错误, 请用逗号分隔的数字, 如: 8080,3000")


def input_password():
    """输入连接密码"""
    while True:
        raw = input("连接密码 > ").strip()
        if raw:
            return raw
        print("  密码不能为空, 请重新输入")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="内网穿透工具 — 将内网服务映射到公网",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  服务端: python tunnel.py server -cp 7000 -dp 7001 -p mypass
  客户端: python tunnel.py client -s 1.2.3.4 -cp 7000 -p mypass -P 8080,3000
        """
    )
    parser.add_argument("mode", choices=["server", "client"], help="运行模式")
    parser.add_argument("-s", "--server", default="127.0.0.1", help="服务端IP (客户端模式使用)")
    parser.add_argument("-cp", "--control-port", type=int, default=DEFAULT_CONTROL_PORT, help=f"控制端口 (默认: {DEFAULT_CONTROL_PORT})")
    parser.add_argument("-dp", "--data-port", type=int, default=DEFAULT_DATA_PORT, help=f"数据端口 (默认: {DEFAULT_DATA_PORT})")
    parser.add_argument("-p", "--password", default=None, help="连接密码")
    parser.add_argument("-P", "--ports", default=None, help="映射端口, 逗号分隔 (客户端模式使用)")

    args = parser.parse_args()

    print()
    print("  ╔══════════════════════════════╗")
    print("  ║     内网穿透工具 v1.0       ║")
    print("  ╚══════════════════════════════╝")
    print()

    if args.mode == "server":
        password = args.password if args.password else input_password()
        server = TunnelServer(args.control_port, args.data_port, password)
        try:
            server.start()
        except KeyboardInterrupt:
            log.info("服务端已关闭")

    elif args.mode == "client":
        password = args.password if args.password else input_password()
        if args.ports:
            ports = [int(p.strip()) for p in args.ports.split(",") if p.strip()]
        else:
            ports = input_ports()

        if not ports:
            log.error("未指定映射端口")
            sys.exit(1)

        print(f"\n配置确认:")
        print(f"  服务端地址: {args.server}:{args.control_port}")
        print(f"  映射端口: {ports}")
        print(f"  连接密码: {'*' * len(password)}")
        print()

        client = TunnelClient(args.server, args.control_port, password, ports)
        try:
            client.start()
        except KeyboardInterrupt:
            log.info("客户端已关闭")


if __name__ == "__main__":
    main()
