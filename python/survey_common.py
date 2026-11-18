"""Shared receipt parsing, option scoring, and code extraction helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse

BASE_SURVEY_URL = "https://cn-hamasushi.csfeedback.net/sv/zh"
DEFAULT_COMMENT = "菜品新鲜美味，服务态度很好，整体体验非常满意。"

RECEIPT_PATTERN = re.compile(
    r"^(RCN)(\d{2})(\d{2})(\d{2})(\d{2})\d{4}(\d{4})\d+$",
    re.IGNORECASE,
)

SURVEY_URL_PATTERN = re.compile(
    r"https?://[^\s\"'<>]*csfeedback\.net/sv/[^\s\"'<>]+",
    re.IGNORECASE,
)

CAPTCHA_INDICATORS: tuple[str, ...] = (
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

TERMINAL_USED_INDICATORS: tuple[str, ...] = (
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


def normalize_target_url(raw_input: str) -> str:
    """Accept a full URL, text containing a survey URL, or a bare RCN receipt code."""
    cleaned = raw_input.strip()
    if not cleaned:
        raise ValueError("问卷链接或小票编号不能为空。")

    url_match = SURVEY_URL_PATTERN.search(cleaned)
    if url_match:
        return url_match.group(0)

    rcn_match = re.search(r"RCN\d{16,}", cleaned, re.IGNORECASE)
    if rcn_match:
        return f"{BASE_SURVEY_URL}/{rcn_match.group(0).upper()}"

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


def extract_code_from_text(text: str) -> str | None:
    """Extract the validation code from completion page text."""
    for pattern in CODE_REGEXES:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def has_captcha_markers(text_lower: str, html_lower: str) -> bool:
    """Return True if known CAPTCHA or human-verification markers appear."""
    return any(
        indicator in text_lower or indicator in html_lower
        for indicator in CAPTCHA_INDICATORS
    )
