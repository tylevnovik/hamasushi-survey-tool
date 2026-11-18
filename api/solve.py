"""Vercel Python Serverless Function for Hamasushi survey solving."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from survey_common import DEFAULT_COMMENT  # noqa: E402
from survey_http import solve_survey_http  # noqa: E402


class handler(BaseHTTPRequestHandler):
    """Vercel serverless HTTP entrypoint at /api/solve."""

    def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(204, {})

    def do_GET(self) -> None:  # noqa: N802
        self._send_json(200, {"ok": True, "mode": "vercel", "opencv_qr": False})

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8"))
            target = str(data.get("url") or "").strip()
            comment = str(data.get("comment") or DEFAULT_COMMENT)
            if not target:
                self._send_json(400, {"success": False, "error": "请提供问卷链接或 RCN 小票编号。"})
                return

            result = solve_survey_http(target, comment=comment)
            self._send_json(200, result.to_dict())
        except ValueError as exc:
            self._send_json(400, {"success": False, "error": str(exc)})
        except Exception as exc:  # pragma: no cover - runtime safety
            self._send_json(500, {"success": False, "error": f"服务器处理失败: {exc}"})
