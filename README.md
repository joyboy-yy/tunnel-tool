# tunnel-tool

[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)]()

内网穿透工具集 — 无需公网 IP / 无需注册，将本地服务映射到公网。  
Intranet tunneling tools — expose local services to the internet without a public IP or registration.

## 工具概览 / Tools Overview

| 工具 | 原理 | 适用场景 |
|------|------|----------|
| `expose.py` | SSH 反向隧道 → [serveo.net](https://serveo.net) | **零依赖**，快速临时暴露，无需自有服务器 |
| `tunnel.py` | 自建 TCP 隧道 (Server/Client) | **自有公网服务器**，稳定、可控、支持多端口 |

```
expose.py:   [外部用户] → serveo.net → SSH隧道 → 你的电脑 localhost

tunnel.py:   [外部用户] → 你的公网服务器 → TCP隧道 → 你的内网电脑 localhost
```

## 环境要求 / Requirements

- Python 3.8+
- `expose.py` 额外需要: OpenSSH 客户端 + SSH 密钥
- `tunnel.py` 额外需要: 一台有公网 IP 的服务器

## expose.py — 免费方案 (基于 serveo.net)

无需公网服务器，通过 SSH 反向隧道将本地端口暴露到公网。

### 快速开始

```bash
# 暴露网页 (自动获得 https 域名)
python expose.py -P 8080

# 暴露数据库 (TCP)
python expose.py -P 3306 -t tcp

# 同时暴露多个端口
python expose.py -P 8080,3306

# 交互式模式
python expose.py
```

### 选项

```
-P, --ports       要暴露的端口, 逗号分隔
-t, --type        http (默认, 获得域名) / tcp (获得公网地址)
-s, --subdomain   自定义子域名, 如 myapp → myapp.serveo.net
```

### 使用场景

```bash
# 临时展示本地网页给外部人员
python expose.py -P 8080
# → https://xxxx.serveo.net

# 远程连接本地数据库
python expose.py -P 3306 -t tcp
# → serveo.net:12345
# 对方: mysql -h serveo.net -P 12345 -u root -p
```

### 注意事项

- serveo.net 是免费公共服务，高峰期可能较慢
- 自定义子域名先到先得
- 仅用于开发调试，不要用于生产环境

---

## tunnel.py — 自建方案 (需公网服务器)

如果你有一台公网 VPS/服务器，`tunnel.py` 提供更稳定、可控的内网穿透。

### 架构

```
[外部用户] → [公网服务器:映射端口] → [控制+数据隧道] → [内网客户端:本地服务]
```

- **控制通道**: 服务端与客户端之间维持一条长连接，传递隧道建立信号
- **数据通道**: 每个外部连接对应一条独立的 TCP 隧道
- **协议**: 自定义二进制分帧协议 (4字节长度前缀 + JSON/RAW数据)

### 快速开始

#### 1. 在公网服务器上启动服务端

```bash
python tunnel.py server -cp 7000 -dp 7001 -p 你的密码
```

#### 2. 在内网机器上启动客户端

```bash
python tunnel.py client -s 服务器IP -cp 7000 -p 你的密码 -P 8080,3306
```

### 选项

```
mode              运行模式: server / client
-cp, --control-port  控制端口 (默认: 7000)
-dp, --data-port     数据端口 (默认: 7001)
-p, --password       连接密码 (必填)
-s, --server         服务端IP (客户端模式)
-P, --ports          映射端口, 逗号分隔 (客户端模式)
```

### 防火墙配置

确保公网服务器开放以下端口:

| 端口 | 用途 | 协议 |
|------|------|------|
| 7000 | 控制通道 | TCP |
| 7001 | 数据通道 | TCP |
| 8080, 3306... | 映射的业务端口 | TCP |

---

## 对比 / Comparison

| | expose.py | tunnel.py |
|------|-----------|-----------|
| 需要公网服务器 | 否 | 是 |
| 需要注册/付费 | 否 | 否 |
| 稳定性 | 依赖第三方 | 可控 |
| 延迟 | 经 serveo 中转 | 直连 |
| 适用 | 临时开发调试 | 长期使用 |

---

## 项目结构

```
tunnel-tool/
├── expose.py      # serveo.net SSH 反向隧道
├── tunnel.py      # 自建 TCP 隧道 (Server/Client)
├── README.md
├── LICENSE        # MIT
└── .gitignore
```

## 常见问题 / FAQ

**Q: expose.py 连接失败?**  
确认 serveo.net 可达，且已生成 SSH 密钥: `ssh-keygen -t ed25519`

**Q: tunnel.py 客户端连不上服务端?**  
检查防火墙是否放行了控制端口和数据端口。

**Q: 是否支持 HTTPS?**  
`expose.py` HTTP 模式自动获得 serveo.net 的 HTTPS 证书。`tunnel.py` 是纯 TCP 转发，可在公网服务器前加 Nginx 反向代理。

## License

[MIT](LICENSE)
