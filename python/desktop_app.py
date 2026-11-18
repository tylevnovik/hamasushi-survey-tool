#!/usr/bin/env python3
"""Local desktop & LAN web server with drag-and-drop receipt photo QR decoding."""

from __future__ import annotations

import argparse
import base64
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
from urllib.parse import urlparse
import webbrowser

from survey_common import DEFAULT_COMMENT
from survey_http import solve_survey_http

try:
    from qr_utils import decode_qr_from_bytes, pick_survey_payload

    HAS_OPENCV_QR = True
except ImportError:
    HAS_OPENCV_QR = False

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"


def _extract_image_bytes(content_type: str, body: bytes) -> bytes:
    """Support either JSON base64 payloads or raw binary image uploads."""
    lowered = content_type.lower()
    if "application/json" in lowered:
        data = json.loads(body.decode("utf-8"))
        b64_str = str(data.get("image_base64") or "").strip()
        if "," in b64_str and b64_str.startswith("data:"):
            b64_str = b64_str.split(",", 1)[1]
        if not b64_str:
            raise ValueError("缺少图片数据。")
        return base64.b64decode(b64_str)
    if body:
        return body
    raise ValueError("上传图片内容为空。")


class DesktopAppHandler(SimpleHTTPRequestHandler):
    """HTTP handler serving static frontend assets and local solve/QR APIs."""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }

    def log_message(self, format: str, *args: object) -> None:
        # Suppress noisy static asset logs while keeping errors readable.
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)

    def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        req_path = urlparse(self.path).path
        if req_path in {"/api/health", "/api/solve"}:
            self._send_json(200, {"ok": True, "mode": "desktop", "opencv_qr": HAS_OPENCV_QR})
            return
        if req_path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        req_path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        if length > 25 * 1024 * 1024:
            self._send_json(413, {"success": False, "error": "图片文件过大（上限 25MB）。"})
            return
        body = self.rfile.read(length) if length > 0 else b""

        if req_path == "/api/solve":
            self._handle_solve_url(body)
        elif req_path == "/api/solve-image":
            self._handle_solve_image(body)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def _handle_solve_url(self, body: bytes) -> None:
        try:
            data = json.loads((body or b"{}").decode("utf-8"))
            target = str(data.get("url") or "").strip()
            comment = str(data.get("comment") or DEFAULT_COMMENT)
            if not target:
                self._send_json(400, {"success": False, "error": "请提供问卷链接或 RCN 小票编号。"})
                return
            result = solve_survey_http(target, comment=comment)
            self._send_json(200, result.to_dict())
        except ValueError as exc:
            self._send_json(400, {"success": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"success": False, "error": f"执行失败: {exc}"})

    def _handle_solve_image(self, body: bytes) -> None:
        if not HAS_OPENCV_QR:
            self._send_json(
                501,
                {"success": False, "error": "当前环境未安装 OpenCV，请使用 `uv sync --extra desktop` 安装。"},
            )
            return
        try:
            content_type = self.headers.get("Content-Type", "")
            image_bytes = _extract_image_bytes(content_type, body)
            candidates = decode_qr_from_bytes(image_bytes)
            decoded = pick_survey_payload(candidates)
            if not decoded:
                self._send_json(
                    422,
                    {"success": False, "error": "未能从照片中识别出二维码，请尝试对准二维码裁切或拍清晰后再拖入。"},
                )
                return

            result = solve_survey_http(decoded)
            payload = result.to_dict()
            payload["decoded_qr"] = decoded
            self._send_json(200, payload)
        except Exception as exc:
            self._send_json(500, {"success": False, "error": f"图片解析失败: {exc}"})


def _find_app_browser() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        shutil.which("msedge"),
        shutil.which("chrome"),
        shutil.which("google-chrome"),
    ]
    for path in candidates:
        if path and Path(path).exists():
            return str(path)
    return None


def _launch_desktop_window(url: str) -> None:
    browser_exe = _find_app_browser()
    if browser_exe:
        try:
            subprocess.Popen(
                [browser_exe, f"--app={url}", "--window-size=560,840"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except OSError:
            pass
    webbrowser.open(url)


def _get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def main() -> int:
    """Start the local drag-and-drop web app server and open the desktop window."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="滨寿司小票问卷工具 - 本地拖拽桌面版")
    parser.add_argument("--port", type=int, default=0, help="监听端口（默认自动分配空闲端口）")
    parser.add_argument("--lan", action="store_true", help="同时允许同一局域网手机访问")
    parser.add_argument("--no-browser", action="store_true", help="只启动服务，不自动弹窗")
    args = parser.parse_args()

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    handler_cls = partial(DesktopAppHandler, directory=str(PUBLIC_DIR))
    server = ThreadingHTTPServer((host, args.port), handler_cls)
    actual_port = server.server_address[1]
    local_url = f"http://127.0.0.1:{actual_port}/"

    print("=" * 56)
    print("滨寿司小票问卷助手（桌面版）已启动！")
    print(f"本地窗口地址: {local_url}")
    if args.lan:
        print(f"手机同 Wi-Fi 地址: http://{_get_lan_ip()}:{actual_port}/")
    print(f"离线 OpenCV 照片二维码增强识别: {'已启用' if HAS_OPENCV_QR else '未启用'}")
    print("使用方式: 直接把手机拍的小票照片拖进窗口，或 Ctrl+V 粘贴图片/链接！")
    print("提示: 关闭此黑框窗口即可退出后台服务。")
    print("=" * 56)

    if not args.no_browser:
        threading.Timer(0.35, _launch_desktop_window, args=(local_url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已关闭。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
