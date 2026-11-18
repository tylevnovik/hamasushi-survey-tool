"""Fast pure-HTTP survey solver for Hamasushi China (csfeedback.net)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
import requests

from survey_common import (
    DEFAULT_COMMENT,
    TERMINAL_USED_INDICATORS,
    ReceiptInfo,
    extract_code_from_text,
    extract_receipt_from_url,
    has_captcha_markers,
    normalize_target_url,
    parse_receipt_code,
    score_option_text,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


@dataclass
class HttpSurveyResult:
    """Structured result returned by the pure-HTTP survey solver."""

    success: bool
    status: str
    code: str | None
    progress: str
    pages_visited: int
    elapsed_ms: int
    final_url: str
    target_url: str
    receipt_code: str
    page_text_excerpt: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _get_progress(soup: BeautifulSoup) -> str:
    bar = soup.select_one(".progress_bar")
    return bar.get_text(" ", strip=True) if bar else ""


def _resolve_label_text(form: Tag, inp: Tag) -> str:
    inp_id = inp.get("id")
    if inp_id:
        lbl = form.find("label", attrs={"for": inp_id})
        if lbl:
            return lbl.get_text(" ", strip=True)
    parent_lbl = inp.find_parent("label")
    if parent_lbl:
        return parent_lbl.get_text(" ", strip=True)
    parent_li = inp.find_parent("li")
    if parent_li:
        return parent_li.get_text(" ", strip=True)
    return ""


def _form_has_question_inputs(form: Tag | None) -> bool:
    if form is None:
        return False
    return bool(
        form.select(
            "input[type='radio'], input[type='checkbox'], select, textarea, input[type='text']"
        )
    )


def _build_receipt_payload(
    form: Tag,
    receipt: ReceiptInfo,
) -> list[tuple[str, str]]:
    payload: list[tuple[str, str]] = []
    receipt_inp = form.find("input", attrs={"name": "receipt_code"})
    code_val = (
        (receipt_inp.get("value") or "").strip() if receipt_inp else ""
    ) or receipt.receipt_code

    parsed = parse_receipt_code(code_val) if code_val else receipt
    shop_inp = form.find("input", attrs={"name": "shop_code"})
    shop_val = (
        (shop_inp.get("value") or "").strip() if shop_inp else ""
    ) or parsed.shop_code

    month_val = str(parsed.month) if parsed.month is not None else _selected_option_value(form, "month")
    day_val = str(parsed.day) if parsed.day is not None else _selected_option_value(form, "day")
    hour_val = str(parsed.hour) if parsed.hour is not None else _selected_option_value(form, "select_visit_hour")
    minute_val = (
        str(parsed.minute)
        if parsed.minute is not None
        else (_selected_option_value(form, "select_visit_minute") or "0")
    )
    visit_hour_val = f"{hour_val}:{int(minute_val):02d}" if hour_val != "" else ""

    for hidden in form.find_all("input", attrs={"type": "hidden"}):
        name = hidden.get("name")
        if not name:
            continue
        if name == "visit_hour" and visit_hour_val:
            payload.append((name, visit_hour_val))
        else:
            payload.append((name, hidden.get("value") or ""))

    if shop_inp is not None:
        payload.append(("shop_code", shop_val))
    if form.find("select", attrs={"name": "month"}):
        payload.append(("month", month_val))
    if form.find("select", attrs={"name": "day"}):
        payload.append(("day", day_val))
    if form.find("select", attrs={"name": "select_visit_hour"}):
        payload.append(("select_visit_hour", hour_val))
    if form.find("select", attrs={"name": "select_visit_minute"}):
        payload.append(("select_visit_minute", minute_val))
    if receipt_inp is not None:
        payload.append(("receipt_code", code_val))
    if form.find("input", attrs={"name": "agree"}):
        payload.append(("agree", "on"))
    return payload


def _selected_option_value(form: Tag, select_name: str) -> str:
    sel = form.find("select", attrs={"name": select_name})
    if not sel:
        return ""
    selected = sel.find("option", selected=True)
    if selected and selected.get("value") is not None:
        return str(selected.get("value"))
    first_opt = sel.find("option")
    return str(first_opt.get("value") or "") if first_opt else ""


def _build_question_payload(
    form: Tag,
    comment: str,
    force_fill_text: bool,
) -> list[tuple[str, str]]:
    payload: list[tuple[str, str]] = []

    for hidden in form.find_all("input", attrs={"type": "hidden"}):
        name = hidden.get("name")
        if name:
            payload.append((name, hidden.get("value") or ""))

    radio_groups: dict[str, list[Tag]] = {}
    for idx, radio in enumerate(form.select("input[type='radio']")):
        name = radio.get("name") or f"__radio_{idx}"
        radio_groups.setdefault(name, []).append(radio)

    for name, radios in radio_groups.items():
        if name.startswith("__radio_"):
            continue
        best_val = ""
        best_score = -10_000
        for r in radios:
            val = r.get("value")
            if val is None:
                continue
            score = score_option_text(_resolve_label_text(form, r))
            if score > best_score:
                best_score = score
                best_val = str(val)
        payload.append((name, best_val))

    cb_groups: dict[str, list[Tag]] = {}
    for idx, cb in enumerate(form.select("input[type='checkbox']")):
        name = cb.get("name") or f"__cb_{idx}"
        cb_groups.setdefault(name, []).append(cb)

    for name, cbs in cb_groups.items():
        if name.startswith("__cb_"):
            continue
        best_val = "on"
        best_score = -10_000
        for cb in cbs:
            val = cb.get("value") or "on"
            score = score_option_text(_resolve_label_text(form, cb))
            if score > best_score:
                best_score = score
                best_val = str(val)
        payload.append((name, best_val))

    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        selected = sel.find("option", selected=True)
        if selected and (selected.get("value") or "").strip():
            payload.append((name, str(selected.get("value"))))
            continue
        best_val = ""
        best_score = -10_000
        for opt in sel.find_all("option"):
            val = (opt.get("value") or "").strip()
            if not val:
                continue
            score = score_option_text(opt.get_text(" ", strip=True))
            if score > best_score:
                best_score = score
                best_val = val
        payload.append((name, best_val))

    for slider in form.select("input[type='range']"):
        name = slider.get("name")
        if name:
            payload.append((name, str(slider.get("max") or "10")))

    for ta in form.find_all("textarea"):
        name = ta.get("name")
        if not name:
            continue
        existing = ta.get_text() or ""
        payload.append((name, existing if existing.strip() else (comment if force_fill_text else "")))

    for txt in form.select("input[type='text']"):
        name = txt.get("name")
        if not name:
            continue
        existing = txt.get("value") or ""
        payload.append((name, existing if existing.strip() else (comment if force_fill_text else "")))

    return payload


def _extract_code_from_soup(soup: BeautifulSoup) -> str | None:
    main = soup.select_one("#main") or soup.body or soup
    text = main.get_text("\n", strip=True)
    code = extract_code_from_text(text)
    if code:
        return code

    for el in main.select("strong, b, .code, .number, p, span"):
        txt = el.get_text(strip=True)
        if re.fullmatch(r"[A-Za-z0-9-]{5,20}", txt) and not txt.lower().startswith("ref:"):
            return txt
    return None


def _page_fingerprint(soup: BeautifulSoup) -> str:
    progress = _get_progress(soup)
    q_names = [
        str(el.get("name") or "")
        for el in soup.select("form[name='form1'] [name^='q_']")
    ]
    main_text = (soup.select_one("#main") or soup).get_text(" ", strip=True)[:250]
    return f"{progress}|{','.join(q_names)}|{main_text}"


def solve_survey_http(
    raw_input: str,
    comment: str = DEFAULT_COMMENT,
    max_pages: int = 35,
    timeout: float = 15.0,
) -> HttpSurveyResult:
    """Submit the entire survey via pure HTTP requests and return the validation code."""
    started = time.perf_counter()
    target_url = normalize_target_url(raw_input)
    receipt_code = extract_receipt_from_url(target_url)
    receipt = parse_receipt_code(receipt_code)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )

    resp = session.get(target_url, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"

    retry_same_page = False
    for step in range(1, max_pages + 1):
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        main_el = soup.select_one("#main") or soup.body or soup
        main_text = main_el.get_text("\n", strip=True)

        if has_captcha_markers(main_text.lower(), html.lower()):
            return HttpSurveyResult(
                success=False,
                status="captcha_blocked",
                code=None,
                progress=_get_progress(soup),
                pages_visited=step,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                final_url=resp.url,
                target_url=target_url,
                receipt_code=receipt_code,
                page_text_excerpt="检测到人机验证/CAPTCHA，已停止自动操作，请手动完成。",
            )

        form = soup.find("form", attrs={"name": "form1"})
        if not _form_has_question_inputs(form):
            code = _extract_code_from_soup(soup)
            progress = _get_progress(soup)
            lowered = main_text.lower()
            is_used = any(word in lowered for word in TERMINAL_USED_INDICATORS) and not code
            status = (
                "already_used_or_invalid"
                if is_used
                else (
                    "completed"
                    if (code or "100%" in progress or "感谢" in main_text or "验证码" in main_text)
                    else "unknown_terminal"
                )
            )
            return HttpSurveyResult(
                success=(status == "completed"),
                status=status,
                code=code,
                progress=progress or ("100%" if status == "completed" else ""),
                pages_visited=step,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                final_url=resp.url,
                target_url=target_url,
                receipt_code=receipt_code,
                page_text_excerpt=main_text[:600],
            )

        assert form is not None
        is_receipt = bool(
            form.find("input", attrs={"name": "shop_code"})
            or form.find("input", attrs={"name": "receipt_code"})
        )
        payload = (
            _build_receipt_payload(form, receipt)
            if is_receipt
            else _build_question_payload(form, comment=comment, force_fill_text=retry_same_page)
        )

        prev_fp = _page_fingerprint(soup)
        action = str(form.get("action") or "")
        post_url = urljoin(resp.url, action) if action else resp.url
        resp = session.post(post_url, data=payload, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"

        next_soup = BeautifulSoup(resp.text, "html.parser")
        next_fp = _page_fingerprint(next_soup)
        has_error = bool(next_soup.select("tr.error, .error"))

        if next_fp == prev_fp or has_error:
            if retry_same_page:
                excerpt = (next_soup.select_one("#main") or next_soup).get_text("\n", strip=True)
                return HttpSurveyResult(
                    success=False,
                    status="validation_stuck",
                    code=None,
                    progress=_get_progress(next_soup),
                    pages_visited=step,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    final_url=resp.url,
                    target_url=target_url,
                    receipt_code=receipt_code,
                    page_text_excerpt=excerpt[:600],
                )
            retry_same_page = True
        else:
            retry_same_page = False

    return HttpSurveyResult(
        success=False,
        status="max_pages_exceeded",
        code=None,
        progress="",
        pages_visited=max_pages,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        final_url=resp.url,
        target_url=target_url,
        receipt_code=receipt_code,
        page_text_excerpt="超过最大翻页保护上限。",
    )
