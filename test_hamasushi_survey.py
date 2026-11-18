"""Unit tests for Hamasushi survey parser, scoring heuristics, and code extraction."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

import hamasushi_survey as hs


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


if __name__ == "__main__":
    test_parse_receipt_code()
    test_normalize_target_url()
    test_score_option_text_priorities()
    test_extract_validation_code_from_completion_html()
    test_detect_already_used_html()
    print("All unit tests passed!")
