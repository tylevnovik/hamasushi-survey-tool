#!/usr/bin/env python3
"""Automated Hamasushi China (csfeedback.net) customer survey filler."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Iterable
from urllib.parse import urlparse

from playwright.sync_api import (
    Browser,
    Dialog,
    Error as PlaywrightError,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

BASE_SURVEY_URL = "https://cn-hamasushi.csfeedback.net/sv/zh"
DEFAULT_COMMENT = "菜品新鲜美味，服务态度很好，整体体验非常满意。"

RECEIPT_PATTERN = re.compile(
    r"^(RCN)(\d{2})(\d{2})(\d{2})(\d{2})\d{4}(\d{4})\d+$",
    re.IGNORECASE,
)

CAPTCHA_INDICATORS = (
    "cf-turnstile",
    "g-recaptcha",
    "h-captcha",
    "recaptcha",
    "turnstile",
    "hcaptcha",
    "verify you are human",
    "请完成安全验证",
    "人机验证",
    "滑动拼图",
    "点选验证码",
)

TERMINAL_USED_INDICATORS = (
    "已回答",
    "已参与",
    "已使用",
    "已提交",
    "超过期限",
    "已过期",
    "无效",
    "无法回答",
    "not found",
    "expired",
)

EXACT_OPTION_SCORES: dict[str, int] = {
    "是": 35,
    "on": 35,
    "否": -60,
    "无": -50,
    "没有": -90,
    "其他": -20,
}

# Priority score table for choosing positive/expected survey options across pages.
# Higher scores win; negative scores avoid complaints/undesirable options.
OPTION_SCORE_RULES: tuple[tuple[str, int], ...] = (
    ("10\u3000非常推荐", 120),
    ("非常推荐", 115),
    ("非常满意", 110),
    ("十分满意", 110),
    ("满意", 100),
    ("肯定会", 95),
    ("愿意", 90),
    ("正好", 90),
    ("合适", 85),
    ("中国", 75),
    ("堂食", 70),
    ("几乎每天", 65),
    ("大约每周", 60),
    ("技术人员", 55),
    ("20～29岁", 55),
    ("20-29", 55),
    ("男性", 50),
    ("一个人", 50),
    ("6,000", 45),
    ("金枪鱼", 45),
    ("还可以", 40),
    ("应该会", 40),
    # Penalize negative / outlier / refusal answers so neutral/positive choices win.
    ("不太满意", -100),
    ("不满意", -110),
    ("完全不推荐", -120),
    ("不愿意", -90),
    ("不确定", -40),
    ("不会", -90),
    ("不好说", -40),
    ("太大", -60),
    ("稍大", -30),
    ("稍小", -30),
    ("太小", -60),
    ("太硬", -60),
    ("稍硬", -30),
    ("稍软", -30),
    ("太软", -60),
    ("不想回复", -80),
    ("未接受服务", -80),
    ("以上都不是", -100),
)

CODE_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:验证码|调查编号|确认编号|兑换码|编号|Code)[：:\s]*([A-Za-z0-9-]{4,24})", re.IGNORECASE),
    re.compile(r"(?:NO\.?|Number)[：:\s]*([A-Za-z0-9-]{4,24})", re.IGNORECASE),
)


@dataclass
class ReceiptInfo:
    """Parsed receipt metadata extracted from URL or RCN receipt code."""

    receipt_code: str = ""
    shop_code: str = ""
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None


@dataclass
class SurveyConfig:
    """Runtime configuration for running the survey automation."""

    url: str
    headless: bool = False
    step_delay: float = 0.25
    max_pages: int = 35
    comment: str = DEFAULT_COMMENT
    pause_before_submit: bool = False
    output_dir: Path = Path("output")
    browser_channel: str | None = None
    shop_code: str | None = None
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None
    verbose: bool = True


@dataclass
class SurveyResult:
    """Outcome summary returned after completing or stopping a survey run."""

    success: bool
    status: str
    code: str | None
    progress: str
    pages_visited: int
    screenshot_path: str | None
    final_url: str
    page_text_excerpt: str


def normalize_target_url(raw_input: str) -> str:
    """Accept either a full csfeedback URL or a bare receipt code."""
    cleaned = raw_input.strip()
    if not cleaned:
        raise ValueError("问卷链接或小票编号不能为空。")
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return f"{BASE_SURVEY_URL}/{cleaned.lstrip('/')}"


def extract_receipt_from_url(url: str) -> str:
    """Extract trailing receipt code segment from a survey URL if present."""
    parsed = urlparse(url)
    segments = [seg for seg in parsed.path.split("/") if seg]
    if segments and segments[-1].upper().startswith("RCN"):
        return segments[-1]
    return ""


def parse_receipt_code(code: str) -> ReceiptInfo:
    """Parse date, visit time, and shop code from a Hamasushi RCN receipt code."""
    info = ReceiptInfo(receipt_code=code.strip())
    match = RECEIPT_PATTERN.match(info.receipt_code)
    if not match:
        return info

    month = int(match.group(2))
    day = int(match.group(3))
    hour = int(match.group(4))
    minute = int(match.group(5))
    shop_code = match.group(6)

    if 1 <= month <= 12:
        info.month = month
    if 1 <= day <= 31:
        info.day = day
    if 0 <= hour <= 23:
        info.hour = hour
    if 0 <= minute <= 59:
        info.minute = minute
    info.shop_code = shop_code
    return info


def merge_receipt_overrides(info: ReceiptInfo, config: SurveyConfig) -> ReceiptInfo:
    """Apply explicit CLI overrides on top of parsed receipt defaults."""
    return ReceiptInfo(
        receipt_code=info.receipt_code,
        shop_code=config.shop_code or info.shop_code,
        month=config.month if config.month is not None else info.month,
        day=config.day if config.day is not None else info.day,
        hour=config.hour if config.hour is not None else info.hour,
        minute=config.minute if config.minute is not None else info.minute,
    )


def score_option_text(text: str) -> int:
    """Return a heuristic preference score for an option label."""
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return -10

    lowered = normalized.lower()
    if lowered in EXACT_OPTION_SCORES:
        return EXACT_OPTION_SCORES[lowered]

    score = 0
    for keyword, weight in OPTION_SCORE_RULES:
        if keyword.lower() in lowered:
            # Negative rules override positive substring matches (e.g. "不满意" contains "满意").
            if weight < 0:
                return weight
            score = max(score, weight)

    if normalized.startswith("10"):
        score = max(score, 120)
    return score


def launch_browser(playwright: Playwright, config: SurveyConfig) -> Browser:
    """Launch system Edge/Chrome when available, falling back to Playwright Chromium."""
    channels: list[str | None] = (
        [config.browser_channel] if config.browser_channel else ["msedge", "chrome", None]
    )
    errors: list[str] = []
    for channel in channels:
        try:
            kwargs: dict[str, object] = {"headless": config.headless}
            if channel:
                kwargs["channel"] = channel
            return playwright.chromium.launch(**kwargs)  # type: ignore[arg-type]
        except PlaywrightError as exc:
            label = channel or "chromium"
            errors.append(f"{label}: {exc}")

    joined = "\n".join(errors)
    raise RuntimeError(
        "无法启动浏览器。请确认本机已安装 Edge/Chrome 或执行 `uv run playwright install chromium`。\n"
        f"详细错误:\n{joined}"
    )


def get_progress_text(page: Page) -> str:
    """Read current progress bar text (e.g. '55%') if present."""
    bar = page.locator(".progress_bar").first
    if bar.count() == 0:
        return ""
    return bar.inner_text().strip()


def parse_progress_percent(progress_text: str) -> int | None:
    """Convert '95%' into integer 95, or None if unavailable."""
    match = re.search(r"(\d+)\s*%", progress_text)
    return int(match.group(1)) if match else None


def detect_captcha(page: Page) -> bool:
    """Check whether any CAPTCHA or human-verification gate is visible/present."""
    body_text = page.locator("body").inner_text().lower() if page.locator("body").count() else ""
    html_snippet = page.content().lower()
    for indicator in CAPTCHA_INDICATORS:
        if indicator in body_text or indicator in html_snippet:
            return True
    return False


def find_advance_button(page: Page) -> Locator | None:
    """Locate the primary Next/Start/Submit button inside the survey form."""
    candidates = [
        "form[name='form1'] .btn_wrap a.btn",
        "form[name='form1'] a.btn.type02",
        "form[name='form1'] a.btn.type03",
        "form[name='form1'] button[type='submit']",
        "form[name='form1'] input[type='submit']",
        ".btn_wrap a.btn.type02",
    ]
    for selector in candidates:
        locator = page.locator(selector).first
        if locator.count() > 0 and locator.is_visible():
            return locator
    return None


def has_question_inputs(page: Page) -> bool:
    """Determine whether the page contains interactive survey inputs."""
    selector = (
        "form[name='form1'] input[type='radio'], "
        "form[name='form1'] input[type='checkbox'], "
        "form[name='form1'] select, "
        "form[name='form1'] textarea, "
        "form[name='form1'] input[type='text']"
    )
    return page.locator(selector).count() > 0


def is_receipt_page(page: Page) -> bool:
    """Return True when the current page is the initial receipt verification form."""
    return (
        page.locator("input[name='shop_code']").count() > 0
        or page.locator("input[name='receipt_code']").count() > 0
    )


def get_label_for_input(page: Page, input_el: Locator) -> tuple[Locator | None, str]:
    """Resolve associated <label> element and readable option text for a hidden/styled input."""
    input_id = input_el.get_attribute("id")
    if input_id:
        label = page.locator(f"label[for='{input_id}']").first
        if label.count() > 0:
            text = (label.inner_text() or label.text_content() or "").strip()
            return label, text

    parent_label = input_el.locator("xpath=ancestor::label[1]").first
    if parent_label.count() > 0:
        text = (parent_label.inner_text() or parent_label.text_content() or "").strip()
        return parent_label, text

    parent_li = input_el.locator("xpath=ancestor::li[1]").first
    if parent_li.count() > 0:
        text = (parent_li.inner_text() or parent_li.text_content() or "").strip()
        return None, text
    return None, ""


def click_styled_control(input_el: Locator, label_el: Locator | None) -> None:
    """Click the visible <label> when raw radio/checkbox inputs are hidden via CSS."""
    if label_el is not None and label_el.is_visible():
        label_el.click()
        return
    input_el.click(force=True)


def select_by_value_if_present(select_el: Locator, value: str) -> bool:
    """Select an <option> by exact value attribute if it exists in the dropdown."""
    option = select_el.locator(f"option[value='{value}']")
    if option.count() == 0:
        return False
    select_el.select_option(value=value)
    return True


def fill_receipt_page(page: Page, receipt: ReceiptInfo) -> None:
    """Fill missing receipt fields (especially visit minute) and accept agreement checkbox."""
    receipt_input = page.locator("input[name='receipt_code']").first
    if receipt_input.count() > 0:
        current_code = receipt_input.input_value().strip()
        if not current_code and receipt.receipt_code:
            receipt_input.fill(receipt.receipt_code)
            current_code = receipt.receipt_code
        if current_code and not receipt.shop_code:
            reparsed = parse_receipt_code(current_code)
            receipt = ReceiptInfo(
                receipt_code=current_code,
                shop_code=receipt.shop_code or reparsed.shop_code,
                month=receipt.month if receipt.month is not None else reparsed.month,
                day=receipt.day if receipt.day is not None else reparsed.day,
                hour=receipt.hour if receipt.hour is not None else reparsed.hour,
                minute=receipt.minute if receipt.minute is not None else reparsed.minute,
            )

    shop_input = page.locator("input[name='shop_code']").first
    if shop_input.count() > 0 and not shop_input.input_value().strip() and receipt.shop_code:
        shop_input.fill(receipt.shop_code)

    dropdown_targets: tuple[tuple[str, int | None], ...] = (
        ("select[name='month']", receipt.month),
        ("select[name='day']", receipt.day),
        ("#select_visit_hour", receipt.hour),
        ("#select_visit_minute", receipt.minute),
    )
    for selector, target_val in dropdown_targets:
        sel = page.locator(selector).first
        if sel.count() == 0:
            continue
        current_val = sel.input_value().strip()
        if current_val == "" and target_val is not None:
            select_by_value_if_present(sel, str(target_val))
        elif current_val == "" and sel.locator("option").count() > 0:
            first_val = sel.locator("option").first.get_attribute("value") or ""
            if first_val:
                sel.select_option(value=first_val)

    agree = page.locator("input[name='agree'], #check01").first
    if agree.count() > 0 and not agree.is_checked():
        label_el, _ = get_label_for_input(page, agree)
        click_styled_control(agree, label_el)


def group_inputs_by_name(inputs: Iterable[Locator]) -> dict[str, list[Locator]]:
    """Group radio or checkbox locators by their form field name, preserving DOM order."""
    grouped: dict[str, list[Locator]] = {}
    for idx, item in enumerate(inputs):
        name = item.get_attribute("name") or f"__unnamed_{idx}"
        grouped.setdefault(name, []).append(item)
    return grouped


def fill_radio_groups(page: Page) -> None:
    """Select the best-scoring option for every unanswered radio group on the page."""
    radios = page.locator("form[name='form1'] input[type='radio']").all()
    for items in group_inputs_by_name(radios).values():
        if any(r.is_checked() for r in items):
            continue
        best_score = -10_000
        best_choice: tuple[Locator, Locator | None] | None = None
        for radio in items:
            label_el, text = get_label_for_input(page, radio)
            score = score_option_text(text)
            if score > best_score:
                best_score = score
                best_choice = (radio, label_el)
        if best_choice is not None:
            click_styled_control(best_choice[0], best_choice[1])


def fill_checkbox_groups(page: Page) -> None:
    """Select positive choices for checkbox groups not already checked."""
    checkboxes = page.locator("form[name='form1'] input[type='checkbox']").all()
    for items in group_inputs_by_name(checkboxes).values():
        if any(cb.is_checked() for cb in items):
            continue
        scored: list[tuple[int, Locator, Locator | None]] = []
        for cb in items:
            label_el, text = get_label_for_input(page, cb)
            scored.append((score_option_text(text), cb, label_el))
        if not scored:
            continue
        scored.sort(key=lambda entry: entry[0], reverse=True)
        _, best_cb, best_label = scored[0]
        click_styled_control(best_cb, best_label)


def fill_unselected_selects(page: Page) -> None:
    """Fill any <select> element that currently has an empty value."""
    selects = page.locator("form[name='form1'] select").all()
    for sel in selects:
        if not sel.is_visible() or sel.input_value().strip() != "":
            continue
        options = sel.locator("option").all()
        best_val: str | None = None
        best_score = -10_000
        for opt in options:
            val = (opt.get_attribute("value") or "").strip()
            if not val:
                continue
            text = (opt.inner_text() or opt.text_content() or "").strip()
            score = score_option_text(text)
            if score > best_score:
                best_score = score
                best_val = val
        if best_val is not None:
            sel.select_option(value=best_val)


def fill_text_fields(page: Page, comment: str) -> None:
    """Fill visible empty text inputs and textareas when required by validation."""
    textareas = page.locator("form[name='form1'] textarea").all()
    for ta in textareas:
        if ta.is_visible() and not ta.input_value().strip():
            ta.fill(comment)

    text_inputs = page.locator("form[name='form1'] input[type='text']").all()
    for inp in text_inputs:
        if inp.is_visible() and not inp.input_value().strip():
            inp.fill(comment)


def fill_question_page(page: Page, config: SurveyConfig, force_fill_text: bool = False) -> None:
    """Fill all question widgets on a standard csfeedback survey page."""
    fill_radio_groups(page)
    fill_checkbox_groups(page)
    fill_unselected_selects(page)

    # Support range sliders if encountered on conditional branches.
    for slider in page.locator("form[name='form1'] input[type='range']").all():
        max_val = slider.get_attribute("max") or "10"
        slider.fill(max_val)

    if force_fill_text:
        fill_text_fields(page, config.comment)


def page_fingerprint(page: Page) -> str:
    """Build a lightweight fingerprint of the current page to detect failed advances."""
    progress = get_progress_text(page)
    main_el = page.locator("#main, form[name='form1']").first
    text = main_el.inner_text().strip() if main_el.count() else ""
    q_names = [
        el.get_attribute("name") or ""
        for el in page.locator("form[name='form1'] [name^='q_']").all()
    ]
    return f"{progress}|{','.join(q_names)}|{text[:300]}"


def extract_validation_code(page: Page) -> str | None:
    """Extract the survey completion validation code from the final page."""
    main_text = ""
    for sel in ("#main", "body"):
        loc = page.locator(sel).first
        if loc.count() > 0:
            main_text = loc.inner_text()
            break

    for pattern in CODE_REGEXES:
        match = pattern.search(main_text)
        if match:
            return match.group(1).strip()

    # Check highlighted/code elements as fallback (excluding footer ref codes).
    for sel in ("#main strong", "#main b", "#main .code", "#main .number", "#main p", "#main span"):
        for loc in page.locator(sel).all():
            txt = loc.inner_text().strip()
            if re.fullmatch(r"[A-Za-z0-9-]{5,20}", txt) and not txt.lower().startswith("ref:"):
                return txt
    return None


def detect_non_form_status(page: Page) -> tuple[str, str | None]:
    """Classify a terminal page that has no active survey form."""
    body_text = page.locator("#main").inner_text() if page.locator("#main").count() else page.locator("body").inner_text()
    code = extract_validation_code(page)
    progress = get_progress_text(page)
    lowered = body_text.lower()

    if code or "100%" in progress or "感谢" in body_text or "验证码" in body_text or "编号" in body_text:
        # Distinguish "already answered" error notice from a successful completion page.
        if any(word in lowered for word in TERMINAL_USED_INDICATORS) and not code:
            return "already_used_or_invalid", None
        return "completed", code

    if any(word in lowered for word in TERMINAL_USED_INDICATORS):
        return "already_used_or_invalid", None
    return "unknown_terminal", code


def click_and_advance(page: Page, btn: Locator, dialog_messages: list[str]) -> None:
    """Click the Next/Start button and wait for the POST form reload to settle."""
    before_dialogs = len(dialog_messages)
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
            btn.click()
    except PlaywrightTimeoutError:
        # If an alert blocked submission or the page updated in-place, give JS a brief settle window.
        if len(dialog_messages) == before_dialogs:
            page.wait_for_load_state("domcontentloaded")


def save_final_screenshot(page: Page, config: SurveyConfig, receipt: ReceiptInfo) -> str | None:
    """Save a full-page screenshot of the final or stopped state."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    stem = receipt.receipt_code or "hamasushi_result"
    path = config.output_dir / f"{stem}_result.png"
    page.screenshot(path=str(path), full_page=True)
    return str(path.resolve())


def run_survey(config: SurveyConfig) -> SurveyResult:
    """Execute the full end-to-end survey workflow and return the extracted code."""
    target_url = normalize_target_url(config.url)
    parsed_receipt = merge_receipt_overrides(
        parse_receipt_code(extract_receipt_from_url(target_url)),
        config,
    )
    dialog_messages: list[str] = []

    with sync_playwright() as playwright:
        browser = launch_browser(playwright, config)
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()

        def handle_dialog(dialog: Dialog) -> None:
            dialog_messages.append(dialog.message)
            if config.verbose:
                print(f"[弹窗提示] {dialog.message}")
            dialog.accept()

        page.on("dialog", handle_dialog)

        try:
            if config.verbose:
                print(f"[打开页面] {target_url}")
            page.goto(target_url, wait_until="domcontentloaded", timeout=25000)

            pages_visited = 0
            retry_same_page = False
            last_fp = ""

            for step in range(1, config.max_pages + 1):
                pages_visited = step
                if detect_captcha(page):
                    shot = save_final_screenshot(page, config, parsed_receipt)
                    return SurveyResult(
                        success=False,
                        status="captcha_blocked",
                        code=None,
                        progress=get_progress_text(page),
                        pages_visited=pages_visited,
                        screenshot_path=shot,
                        final_url=page.url,
                        page_text_excerpt="检测到人机验证/CAPTCHA，已停止自动操作，请手动完成。",
                    )

                progress = get_progress_text(page)
                progress_pct = parse_progress_percent(progress)
                advance_btn = find_advance_button(page)

                if advance_btn is None or not has_question_inputs(page):
                    status, code = detect_non_form_status(page)
                    shot = save_final_screenshot(page, config, parsed_receipt)
                    excerpt = (
                        page.locator("#main").inner_text().strip()
                        if page.locator("#main").count()
                        else page.locator("body").inner_text().strip()
                    )
                    return SurveyResult(
                        success=(status == "completed"),
                        status=status,
                        code=code,
                        progress=progress or ("100%" if status == "completed" else ""),
                        pages_visited=pages_visited,
                        screenshot_path=shot,
                        final_url=page.url,
                        page_text_excerpt=excerpt[:600],
                    )

                if config.verbose:
                    print(f"[第 {step} 页] 进度: {progress or '0%'} | URL: {page.url}")

                if is_receipt_page(page):
                    fill_receipt_page(page, parsed_receipt)
                else:
                    fill_question_page(page, config, force_fill_text=retry_same_page)

                if config.pause_before_submit and progress_pct is not None and progress_pct >= 95:
                    if not config.headless:
                        print("[暂停] 已到达最终提交前页面 (>=95%)，请在浏览器确认或按回车继续提交...")
                        input()
                    else:
                        shot = save_final_screenshot(page, config, parsed_receipt)
                        return SurveyResult(
                            success=False,
                            status="paused_before_submit",
                            code=None,
                            progress=progress,
                            pages_visited=pages_visited,
                            screenshot_path=shot,
                            final_url=page.url,
                            page_text_excerpt="按 --pause-before-submit 参数在提交前暂停。",
                        )

                current_fp = page_fingerprint(page)
                if config.step_delay > 0:
                    page.wait_for_timeout(int(config.step_delay * 1000))

                click_and_advance(page, advance_btn, dialog_messages)
                next_fp = page_fingerprint(page)
                has_error = page.locator("tr.error, .error").count() > 0

                if next_fp == current_fp or has_error:
                    if retry_same_page:
                        shot = save_final_screenshot(page, config, parsed_receipt)
                        excerpt = (
                            page.locator("#main").inner_text().strip()
                            if page.locator("#main").count()
                            else ""
                        )
                        return SurveyResult(
                            success=False,
                            status="validation_stuck",
                            code=None,
                            progress=get_progress_text(page),
                            pages_visited=pages_visited,
                            screenshot_path=shot,
                            final_url=page.url,
                            page_text_excerpt=excerpt[:600],
                        )
                    retry_same_page = True
                    if config.verbose:
                        print("[重试] 页面未前进或存在校验提示，补充字段后重试当前页...")
                else:
                    retry_same_page = False
                    last_fp = next_fp

            shot = save_final_screenshot(page, config, parsed_receipt)
            return SurveyResult(
                success=False,
                status="max_pages_exceeded",
                code=None,
                progress=get_progress_text(page),
                pages_visited=pages_visited,
                screenshot_path=shot,
                final_url=page.url,
                page_text_excerpt=last_fp[:600],
            )
        finally:
            context.close()
            browser.close()


def build_arg_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser for Hamasushi survey automation."""
    parser = argparse.ArgumentParser(
        description="滨寿司 (cn-hamasushi.csfeedback.net) 顾客满意度问卷自动填写并提取编号工具",
    )
    parser.add_argument("url", nargs="?", help="问卷链接或 RCN 小票编号")
    parser.add_argument("--headless", action="store_true", help="使用无头模式运行（不显示浏览器窗口）")
    parser.add_argument("--delay", type=float, default=0.2, help="每页提交前等待秒数（默认 0.2）")
    parser.add_argument("--max-pages", type=int, default=35, help="最大翻页保护上限（默认 35）")
    parser.add_argument("--comment", default=DEFAULT_COMMENT, help="当遇到必填文本题时填写的内容")
    parser.add_argument("--pause-before-submit", action="store_true", help="到 >=95%% 提交前暂停等待确认")
    parser.add_argument("--output-dir", default="output", help="完成页截图和 JSON 输出目录（默认 output）")
    parser.add_argument("--json-out", action="store_true", help="额外保存 JSON 格式结果文件")
    parser.add_argument("--browser", choices=["msedge", "chrome", "chromium"], help="指定浏览器引擎")
    parser.add_argument("--shop", help="手动覆盖 4 位店铺编号")
    parser.add_argument("--month", type=int, help="手动覆盖消费月份 (1-12)")
    parser.add_argument("--day", type=int, help="手动覆盖消费日期 (1-31)")
    parser.add_argument("--hour", type=int, help="手动覆盖消费小时 (0-23)")
    parser.add_argument("--minute", type=int, help="手动覆盖消费分钟 (0-59)")
    return parser


def main() -> int:
    """CLI entrypoint."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_arg_parser().parse_args()
    raw_url = args.url or input("请输入滨寿司问卷链接或 RCN 小票编号: ").strip()

    config = SurveyConfig(
        url=raw_url,
        headless=args.headless,
        step_delay=args.delay,
        max_pages=args.max_pages,
        comment=args.comment,
        pause_before_submit=args.pause_before_submit,
        output_dir=Path(args.output_dir),
        browser_channel=None if args.browser == "chromium" else args.browser,
        shop_code=args.shop,
        month=args.month,
        day=args.day,
        hour=args.hour,
        minute=args.minute,
    )

    result = run_survey(config)

    if args.json_out:
        config.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = config.output_dir / "latest_result.json"
        json_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 56)
    print(f"运行状态: {result.status} (成功: {result.success})")
    print(f"最终进度: {result.progress or '未知'} | 共经历页面: {result.pages_visited}")
    if result.code:
        print(f"提取到的编号/验证码: {result.code}")
    if result.screenshot_path:
        print(f"结果截图已保存: {result.screenshot_path}")
    print("-" * 56)
    print(result.page_text_excerpt)
    print("=" * 56)
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
