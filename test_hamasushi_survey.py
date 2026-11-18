"""Unit and integration tests for Hamasushi survey tools."""

from __future__ import annotations

from functools import partial
from http.server import ThreadingHTTPServer
import io
import socket
import subprocess
import threading
import time

import cv2
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
import requests

import desktop_app
import hamasushi_survey as hs
import qr_utils
import survey_http


def test_parse_receipt_code() -> None:
    info = hs.parse_receipt_code("RCN0512142801118888654321")
    assert info.receipt_code == "RCN0512142801118888654321"
    assert info.month == 5
    assert info.day == 12
    assert info.hour == 14
    assert info.minute == 28
    assert info.shop_code == "8888"

    late_min = hs.parse_receipt_code("RCN0308195501119999123456")
    assert late_min.month == 3
    assert late_min.day == 8
    assert late_min.hour == 19
    assert late_min.minute == 55
    assert late_min.shop_code == "9999"


def test_normalize_target_url() -> None:
    assert (
        hs.normalize_target_url("RCN0512142801118888654321")
        == "https://cn-hamasushi.csfeedback.net/sv/zh/RCN0512142801118888654321"
    )
    full = "https://cn-hamasushi.csfeedback.net/sv/zh/RCN0512142801118888654321"
    assert hs.normalize_target_url(full) == full
    wrapped = f"扫码结果：{full} 请打开"
    assert hs.normalize_target_url(wrapped) == full


def test_score_option_text_priorities() -> None:
    assert hs.score_option_text("10　非常推荐") > hs.score_option_text("0　完全不推荐")
    assert hs.score_option_text("满意") > hs.score_option_text("还可以")
    assert hs.score_option_text("满意") > hs.score_option_text("不太满意")
    assert hs.score_option_text("满意") > hs.score_option_text("不满意")
    assert hs.score_option_text("正好") > hs.score_option_text("太大")
    assert hs.score_option_text("正好") > hs.score_option_text("太硬")
    assert hs.score_option_text("是") > hs.score_option_text("否")
    assert hs.score_option_text("无线网络很棒") >= 0


def test_extract_validation_code_from_completion_html() -> None:
    html = """
    <html><body>
      <div id="main">
        <div class="progress_bar">100%</div>
        <p>感谢您的配合</p>
        <p>验证码</p>
        <p><strong>S1234</strong></p>
      </div>
      <footer>Powered by csfeedback.net ref:311035</footer>
    </body></html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(html)
        status, code = hs.detect_non_form_status(page)
        browser.close()
    assert status == "completed"
    assert code == "S1234"


def test_detect_already_used_html() -> None:
    html = """
    <html><body>
      <div id="main">
        <p>该收银小票编号已回答过本次问卷，感谢您的参与。</p>
      </div>
    </body></html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(html)
        status, code = hs.detect_non_form_status(page)
        browser.close()
    assert status == "already_used_or_invalid"
    assert code is None


def test_http_question_payload_preserves_repeated_q_and_picks_best() -> None:
    html = """
    <form name="form1" method="post" action="">
      <input type="hidden" name="page" value="3" />
      <input type="hidden" name="q[]" value="27263" />
      <input type="hidden" name="q[]" value="27264" />
      <input id="r1" name="q_27263" value="1" type="radio"><label for="r1">太大</label>
      <input id="r2" name="q_27263" value="3" type="radio"><label for="r2">正好</label>
      <input id="r3" name="q_27264" value="1" type="radio"><label for="r3">太硬</label>
      <input id="r4" name="q_27264" value="3" type="radio"><label for="r4">正好</label>
      <textarea name="q_27279"></textarea>
    </form>
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", attrs={"name": "form1"})
    assert form is not None
    payload = survey_http._build_question_payload(form, comment="好评", force_fill_text=False)
    assert ("page", "3") in payload
    assert payload.count(("q[]", "27263")) == 1
    assert payload.count(("q[]", "27264")) == 1
    assert ("q_27263", "3") in payload
    assert ("q_27264", "3") in payload
    assert ("q_27279", "") in payload


def test_qr_decode_from_synthetic_receipt_photo() -> None:
    target = "https://cn-hamasushi.csfeedback.net/sv/zh/RCN0512142801118888654321"
    enc = cv2.QRCodeEncoder.create()
    qr_raw = enc.encode(target)
    qr_large = cv2.resize(qr_raw, (260, 260), interpolation=cv2.INTER_NEAREST)

    # Simulate a phone photo: receipt strip placed on a darker table with QR near bottom.
    canvas = np.full((1800, 1200), 170, dtype=np.uint8)
    canvas[120:1700, 260:940] = 246
    canvas[1320:1580, 470:730] = qr_large

    buf = io.BytesIO()
    Image.fromarray(canvas).save(buf, format="JPEG", quality=90)
    decoded = qr_utils.decode_qr_from_bytes(buf.getvalue())
    assert qr_utils.pick_survey_payload(decoded) == target


def test_desktop_app_http_endpoints() -> None:
    handler_cls = partial(desktop_app.DesktopAppHandler, directory=str(desktop_app.PUBLIC_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        r_index = requests.get(f"{base}/", timeout=5)
        assert r_index.status_code == 200
        assert "滨寿司问卷验证码助手" in r_index.text

        r_jsqr = requests.get(f"{base}/vendor/jsQR.js", timeout=5)
        assert r_jsqr.status_code == 200
        assert len(r_jsqr.content) > 50_000

        r_health = requests.get(f"{base}/api/solve", timeout=5)
        assert r_health.status_code == 200
        assert r_health.json()["opencv_qr"] is True

        r_empty = requests.post(f"{base}/api/solve", json={"url": ""}, timeout=5)
        assert r_empty.status_code == 400

        blank_buf = io.BytesIO()
        Image.new("RGB", (120, 120), color=(255, 255, 255)).save(blank_buf, format="PNG")
        r_no_qr = requests.post(
            f"{base}/api/solve-image",
            data=blank_buf.getvalue(),
            headers={"Content-Type": "image/png"},
            timeout=5,
        )
        assert r_no_qr.status_code == 422
    finally:
        server.shutdown()
        server.server_close()


def test_browser_ui_photo_qr_flow() -> None:
    """Verify public/index.html loads cleanly, decodes a dropped receipt photo, and renders the code."""
    target = "https://cn-hamasushi.csfeedback.net/sv/zh/RCN0512142801118888654321"
    enc = cv2.QRCodeEncoder.create()
    qr_raw = enc.encode(target)
    qr_large = cv2.resize(qr_raw, (260, 260), interpolation=cv2.INTER_NEAREST)
    canvas = np.full((1400, 1000), 190, dtype=np.uint8)
    canvas[950:1210, 370:630] = qr_large
    buf = io.BytesIO()
    Image.fromarray(canvas).save(buf, format="PNG")

    handler_cls = partial(desktop_app.DesktopAppHandler, directory=str(desktop_app.PUBLIC_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    console_errors: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.on("pageerror", lambda err: console_errors.append(str(err)))
            page.route(
                "**/api/solve",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success":true,"status":"completed","code":"S1234","pages_visited":20,"elapsed_ms":1200,"receipt_code":"RCN0512142801118888654321"}',
                )
                if route.request.method == "POST"
                else route.continue_(),
            )
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator("#file-input").set_input_files(
                {"name": "receipt.png", "mimeType": "image/png", "buffer": buf.getvalue()}
            )
            page.locator("#result-card.visible").wait_for(timeout=8000)
            assert page.locator("#result-code").inner_text().strip() == "S1234"
            assert page.locator("#url-input").input_value().strip() == target
            browser.close()
        assert not console_errors, f"Unexpected JS errors: {console_errors}"
    finally:
        server.shutdown()
        server.server_close()


def test_powershell_zero_install_server() -> None:
    """Verify Windows PowerShell 5.1 win_app.ps1 serves the UI and decodes receipt photos in Edge."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    proc = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "win_app.ps1",
            "-NoBrowser",
            "-Port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(30):
            try:
                r = requests.get(f"{base}/api/solve", timeout=1)
                if r.status_code == 200:
                    assert r.json()["mode"] == "windows-powershell"
                    break
            except requests.RequestException:
                time.sleep(0.2)
        else:
            raise AssertionError("PowerShell win_app.ps1 server failed to start in time")

        enc = cv2.QRCodeEncoder.create()
        qr_raw = enc.encode("https://cn-hamasushi.csfeedback.net/sv/zh/RCN0512142801118888654321")
        qr_large = cv2.resize(qr_raw, (260, 260), interpolation=cv2.INTER_NEAREST)
        canvas = np.full((1400, 1000), 190, dtype=np.uint8)
        canvas[950:1210, 370:630] = qr_large
        buf = io.BytesIO()
        Image.fromarray(canvas).save(buf, format="PNG")

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.route(
                "**/api/solve",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success":true,"status":"completed","code":"S7777","pages_visited":20,"elapsed_ms":1500,"receipt_code":"RCN0512142801118888654321"}',
                )
                if route.request.method == "POST"
                else route.continue_(),
            )
            page.goto(f"{base}/", wait_until="networkidle")
            assert "Windows 免安装模式" in page.locator("#mode-foot").inner_text()
            page.locator("#file-input").set_input_files(
                {"name": "receipt.png", "mimeType": "image/png", "buffer": buf.getvalue()}
            )
            page.locator("#result-card.visible").wait_for(timeout=8000)
            assert page.locator("#result-code").inner_text().strip() == "S7777"
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_rust_binary_server() -> None:
    """Verify compiled Rust hamasushi-tool.exe serves the embedded UI and decodes receipt photos."""
    from pathlib import Path

    exe_path = Path("rust/target/release/hamasushi-tool.exe")
    assert exe_path.is_file(), f"Missing release binary: {exe_path}"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    proc = subprocess.Popen(
        [str(exe_path), "--no-browser", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(30):
            try:
                r = requests.get(f"{base}/api/solve", timeout=1)
                if r.status_code == 200:
                    assert r.json()["mode"] == "rust-native"
                    break
            except requests.RequestException:
                time.sleep(0.15)
        else:
            raise AssertionError("Rust hamasushi-tool.exe server failed to start in time")

        blank_buf = io.BytesIO()
        Image.new("RGB", (120, 120), color=(255, 255, 255)).save(blank_buf, format="PNG")
        r_no_qr = requests.post(
            f"{base}/api/solve-image",
            data=blank_buf.getvalue(),
            headers={"Content-Type": "image/png"},
            timeout=5,
        )
        assert r_no_qr.status_code == 422

        enc = cv2.QRCodeEncoder.create()
        qr_raw = enc.encode("https://cn-hamasushi.csfeedback.net/sv/zh/RCN0512142801118888654321")
        qr_large = cv2.resize(qr_raw, (260, 260), interpolation=cv2.INTER_NEAREST)
        canvas = np.full((1400, 1000), 190, dtype=np.uint8)
        canvas[950:1210, 370:630] = qr_large
        buf = io.BytesIO()
        Image.fromarray(canvas).save(buf, format="PNG")

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.route(
                "**/api/solve",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success":true,"status":"completed","code":"S8888","pages_visited":20,"elapsed_ms":1100,"receipt_code":"RCN0512142801118888654321"}',
                )
                if route.request.method == "POST"
                else route.continue_(),
            )
            page.goto(f"{base}/", wait_until="networkidle")
            assert "Rust 单文件零依赖模式" in page.locator("#mode-foot").inner_text()
            page.locator("#file-input").set_input_files(
                {"name": "receipt.png", "mimeType": "image/png", "buffer": buf.getvalue()}
            )
            page.locator("#result-card.visible").wait_for(timeout=8000)
            assert page.locator("#result-code").inner_text().strip() == "S8888"
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    test_parse_receipt_code()
    test_normalize_target_url()
    test_score_option_text_priorities()
    test_extract_validation_code_from_completion_html()
    test_detect_already_used_html()
    test_http_question_payload_preserves_repeated_q_and_picks_best()
    test_qr_decode_from_synthetic_receipt_photo()
    test_desktop_app_http_endpoints()
    test_browser_ui_photo_qr_flow()
    test_powershell_zero_install_server()
    test_rust_binary_server()
    print("All unit tests passed!")
