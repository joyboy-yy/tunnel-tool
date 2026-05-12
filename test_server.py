#!/usr/bin/env python3
"""简单的 HTTP 测试服务 — 用于验证内网穿透"""
import http.server
import socketserver
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><title>隧道测试</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; max-width: 600px; margin: 80px auto; text-align: center; }}
  h1 {{ color: #2563eb; }}
  .card {{ background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 12px; padding: 24px; margin: 16px 0; }}
  .green {{ color: #16a34a; font-weight: bold; }}
</style></head>
<body>
  <h1>内网穿透测试成功!</h1>
  <div class="card">
    <p>如果你能看到这个页面，说明隧道已成功建立</p>
    <p class="green">Tunnel is working!</p>
    <p>端口: {PORT}</p>
  </div>
</body>
</html>""".encode())

Handler.extensions_map.update({".": "text/plain"})

with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
    print(f"测试服务已启动: http://localhost:{PORT}")
    print("按 Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
