//! Fast pure-Rust HTTP survey solver using ureq + scraper.

use crate::common::{
    DEFAULT_COMMENT, ReceiptInfo, TERMINAL_USED_INDICATORS, extract_code_from_text,
    extract_receipt_from_url, has_captcha_markers, normalize_target_url, parse_receipt_code,
    score_option_text,
};
use regex::Regex;
use scraper::{ElementRef, Html, Selector};
use serde::Serialize;
use std::collections::HashMap;
use std::sync::OnceLock;
use std::time::{Duration, Instant};
use ureq::{Agent, ResponseExt};
use url::Url;

const USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36";

#[derive(Debug, Clone, Serialize)]
pub struct SurveyResult {
    pub success: bool,
    pub status: String,
    pub code: Option<String>,
    pub progress: String,
    pub pages_visited: usize,
    pub elapsed_ms: u128,
    pub final_url: String,
    pub target_url: String,
    pub receipt_code: String,
    pub page_text_excerpt: String,
}

fn sel(pattern: &str) -> Selector {
    Selector::parse(pattern).expect("valid static CSS selector")
}

fn clean_text(el: ElementRef<'_>) -> String {
    el.text().collect::<Vec<_>>().join(" ").split_whitespace().collect::<Vec<_>>().join(" ")
}

fn get_progress(doc: &Html) -> String {
    doc.select(&sel(".progress_bar"))
        .next()
        .map(clean_text)
        .unwrap_or_default()
}

fn visible_text(el: ElementRef<'_>) -> String {
    el.descendants()
        .filter_map(|node| {
            let txt = node.value().as_text()?;
            let hidden = node.ancestors().any(|anc| {
                anc.value()
                    .as_element()
                    .is_some_and(|e| matches!(e.name(), "style" | "script"))
            });
            if hidden { None } else { Some(txt.trim()) }
        })
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn main_text(doc: &Html) -> String {
    if let Some(m) = doc.select(&sel("#main")).next() {
        return visible_text(m);
    }
    if let Some(b) = doc.select(&sel("body")).next() {
        return visible_text(b);
    }
    String::new()
}

fn resolve_label_text(form: ElementRef<'_>, inp: ElementRef<'_>) -> String {
    if let Some(id) = inp.value().attr("id") {
        let escaped = id.replace('"', "\\\"");
        if let Ok(lbl_sel) = Selector::parse(&format!("label[for=\"{escaped}\"]"))
            && let Some(lbl) = form.select(&lbl_sel).next()
        {
            return clean_text(lbl);
        }
    }
    for anc in inp.ancestors() {
        if let Some(el) = ElementRef::wrap(anc) {
            let name = el.value().name();
            if name == "label" || name == "li" {
                return clean_text(el);
            }
        }
    }
    String::new()
}

fn form_has_question_inputs(form: ElementRef<'_>) -> bool {
    form.select(&sel(
        "input[type='radio'], input[type='checkbox'], select, textarea, input[type='text']",
    ))
    .next()
    .is_some()
}

fn selected_option_value(form: ElementRef<'_>, select_name: &str) -> String {
    let sel_q = Selector::parse(&format!("select[name=\"{select_name}\"]")).unwrap();
    let Some(sel_el) = form.select(&sel_q).next() else {
        return String::new();
    };
    let opt_sel = sel("option");
    if let Some(selected) = sel_el.select(&opt_sel).find(|o| o.value().attr("selected").is_some()) {
        return selected.value().attr("value").unwrap_or_default().to_string();
    }
    sel_el
        .select(&opt_sel)
        .next()
        .and_then(|o| o.value().attr("value"))
        .unwrap_or_default()
        .to_string()
}

fn build_receipt_payload(form: ElementRef<'_>, receipt: &ReceiptInfo) -> Vec<(String, String)> {
    let mut payload = Vec::new();

    let rec_inp = form.select(&sel("input[name='receipt_code']")).next();
    let code_val = rec_inp
        .and_then(|el| el.value().attr("value"))
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string)
        .unwrap_or_else(|| receipt.receipt_code.clone());

    let parsed = if code_val.is_empty() {
        receipt.clone()
    } else {
        parse_receipt_code(&code_val)
    };

    let shop_inp = form.select(&sel("input[name='shop_code']")).next();
    let shop_val = shop_inp
        .and_then(|el| el.value().attr("value"))
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string)
        .unwrap_or_else(|| parsed.shop_code.clone());

    let month_val = parsed
        .month
        .map(|v| v.to_string())
        .unwrap_or_else(|| selected_option_value(form, "month"));
    let day_val = parsed
        .day
        .map(|v| v.to_string())
        .unwrap_or_else(|| selected_option_value(form, "day"));
    let hour_val = parsed
        .hour
        .map(|v| v.to_string())
        .unwrap_or_else(|| selected_option_value(form, "select_visit_hour"));
    let minute_val = parsed.minute.map(|v| v.to_string()).unwrap_or_else(|| {
        let s = selected_option_value(form, "select_visit_minute");
        if s.is_empty() { "0".to_string() } else { s }
    });

    let visit_hour_val = if !hour_val.is_empty() {
        let m_num: u32 = minute_val.parse().unwrap_or(0);
        format!("{hour_val}:{m_num:02}")
    } else {
        String::new()
    };

    for hidden in form.select(&sel("input[type='hidden']")) {
        let Some(name) = hidden.value().attr("name") else {
            continue;
        };
        let val = if name == "visit_hour" && !visit_hour_val.is_empty() {
            visit_hour_val.clone()
        } else {
            hidden.value().attr("value").unwrap_or_default().to_string()
        };
        payload.push((name.to_string(), val));
    }

    if shop_inp.is_some() {
        payload.push(("shop_code".to_string(), shop_val));
    }
    if form.select(&sel("select[name='month']")).next().is_some() {
        payload.push(("month".to_string(), month_val));
    }
    if form.select(&sel("select[name='day']")).next().is_some() {
        payload.push(("day".to_string(), day_val));
    }
    if form.select(&sel("select[name='select_visit_hour']")).next().is_some() {
        payload.push(("select_visit_hour".to_string(), hour_val));
    }
    if form.select(&sel("select[name='select_visit_minute']")).next().is_some() {
        payload.push(("select_visit_minute".to_string(), minute_val));
    }
    if rec_inp.is_some() {
        payload.push(("receipt_code".to_string(), code_val));
    }
    if form.select(&sel("input[name='agree']")).next().is_some() {
        payload.push(("agree".to_string(), "on".to_string()));
    }
    payload
}

pub fn build_question_payload(
    form: ElementRef<'_>,
    comment: &str,
    force_fill_text: bool,
) -> Vec<(String, String)> {
    let mut payload = Vec::new();

    for hidden in form.select(&sel("input[type='hidden']")) {
        if let Some(name) = hidden.value().attr("name") {
            payload.push((
                name.to_string(),
                hidden.value().attr("value").unwrap_or_default().to_string(),
            ));
        }
    }

    let mut radio_order: Vec<String> = Vec::new();
    let mut radio_groups: HashMap<String, Vec<ElementRef<'_>>> = HashMap::new();
    for radio in form.select(&sel("input[type='radio']")) {
        let Some(name) = radio.value().attr("name") else {
            continue;
        };
        if !radio_groups.contains_key(name) {
            radio_order.push(name.to_string());
        }
        radio_groups.entry(name.to_string()).or_default().push(radio);
    }

    for name in radio_order {
        let mut best_val = String::new();
        let mut best_score = i32::MIN;
        for r in &radio_groups[&name] {
            let Some(val) = r.value().attr("value") else {
                continue;
            };
            let sc = score_option_text(&resolve_label_text(form, *r));
            if sc > best_score {
                best_score = sc;
                best_val = val.to_string();
            }
        }
        payload.push((name, best_val));
    }

    let mut cb_order: Vec<String> = Vec::new();
    let mut cb_groups: HashMap<String, Vec<ElementRef<'_>>> = HashMap::new();
    for cb in form.select(&sel("input[type='checkbox']")) {
        let Some(name) = cb.value().attr("name") else {
            continue;
        };
        if !cb_groups.contains_key(name) {
            cb_order.push(name.to_string());
        }
        cb_groups.entry(name.to_string()).or_default().push(cb);
    }

    for name in cb_order {
        let mut best_val = "on".to_string();
        let mut best_score = i32::MIN;
        for c in &cb_groups[&name] {
            let val = c.value().attr("value").unwrap_or("on");
            let sc = score_option_text(&resolve_label_text(form, *c));
            if sc > best_score {
                best_score = sc;
                best_val = val.to_string();
            }
        }
        payload.push((name, best_val));
    }

    let opt_sel = sel("option");
    for sel_el in form.select(&sel("select")) {
        let Some(name) = sel_el.value().attr("name") else {
            continue;
        };
        if let Some(selected) = sel_el
            .select(&opt_sel)
            .find(|o| o.value().attr("selected").is_some())
            && let Some(val) = selected.value().attr("value")
            && !val.trim().is_empty()
        {
            payload.push((name.to_string(), val.to_string()));
            continue;
        }
        let mut best_val = String::new();
        let mut best_score = i32::MIN;
        for opt in sel_el.select(&opt_sel) {
            let val = opt.value().attr("value").unwrap_or_default().trim();
            if val.is_empty() {
                continue;
            }
            let sc = score_option_text(&clean_text(opt));
            if sc > best_score {
                best_score = sc;
                best_val = val.to_string();
            }
        }
        payload.push((name.to_string(), best_val));
    }

    for slider in form.select(&sel("input[type='range']")) {
        if let Some(name) = slider.value().attr("name") {
            let max_val = slider.value().attr("max").unwrap_or("10");
            payload.push((name.to_string(), max_val.to_string()));
        }
    }

    for ta in form.select(&sel("textarea")) {
        if let Some(name) = ta.value().attr("name") {
            let existing = ta.text().collect::<String>();
            let val = if !existing.trim().is_empty() {
                existing
            } else if force_fill_text {
                comment.to_string()
            } else {
                String::new()
            };
            payload.push((name.to_string(), val));
        }
    }

    for txt in form.select(&sel("input[type='text']")) {
        if let Some(name) = txt.value().attr("name") {
            let existing = txt.value().attr("value").unwrap_or_default();
            let val = if !existing.trim().is_empty() {
                existing.to_string()
            } else if force_fill_text {
                comment.to_string()
            } else {
                String::new()
            };
            payload.push((name.to_string(), val));
        }
    }

    payload
}

fn extract_code_from_doc(doc: &Html) -> Option<String> {
    static FALLBACK_RE: OnceLock<Regex> = OnceLock::new();
    let fallback_re = FALLBACK_RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9-]{5,20}$").unwrap());

    let text = main_text(doc);
    if let Some(code) = extract_code_from_text(&text) {
        return Some(code);
    }

    let container = doc
        .select(&sel("#main"))
        .next()
        .or_else(|| doc.select(&sel("body")).next())?;

    for el in container.select(&sel("strong, b, .code, .number, p, span")) {
        let txt = clean_text(el);
        if fallback_re.is_match(&txt) && !txt.to_lowercase().starts_with("ref:") {
            return Some(txt);
        }
    }
    None
}

fn page_fingerprint(doc: &Html) -> String {
    let progress = get_progress(doc);
    let q_names: Vec<String> = doc
        .select(&sel("form[name='form1'] [name^='q_']"))
        .filter_map(|el| el.value().attr("name").map(ToString::to_string))
        .collect();
    let snippet: String = main_text(doc)
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(220)
        .collect();
    format!("{progress}|{}|{snippet}", q_names.join(","))
}

pub fn solve_survey_http(
    raw_input: &str,
    comment: Option<&str>,
    max_pages: usize,
) -> Result<SurveyResult, String> {
    let started = Instant::now();
    let comment_text = comment.unwrap_or(DEFAULT_COMMENT);
    let target_url = normalize_target_url(raw_input)?;
    let receipt_code = extract_receipt_from_url(&target_url);
    let receipt = parse_receipt_code(&receipt_code);

    let config = Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(15)))
        .user_agent(USER_AGENT)
        .build();
    let agent: Agent = config.into();

    let mut resp = agent
        .get(&target_url)
        .header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        .call()
        .map_err(|e| format!("请求问卷页面失败: {e}"))?;

    let mut current_url = resp.get_uri().to_string();
    let mut html = resp
        .body_mut()
        .read_to_string()
        .map_err(|e| format!("读取响应内容失败: {e}"))?;

    let mut retry_same_page = false;
    let form_sel = sel("form[name='form1']");

    for step in 1..=max_pages {
        let doc = Html::parse_document(&html);
        let text = main_text(&doc);
        let text_lower = text.to_lowercase();
        let html_lower = html.to_lowercase();

        if has_captcha_markers(&text_lower, &html_lower) {
            return Ok(SurveyResult {
                success: false,
                status: "captcha_blocked".to_string(),
                code: None,
                progress: get_progress(&doc),
                pages_visited: step,
                elapsed_ms: started.elapsed().as_millis(),
                final_url: current_url,
                target_url,
                receipt_code,
                page_text_excerpt: "检测到人机验证/CAPTCHA，已停止自动操作，请手动完成。".to_string(),
            });
        }

        let form_opt = doc.select(&form_sel).next();
        let has_inputs = form_opt.map(form_has_question_inputs).unwrap_or(false);

        if !has_inputs {
            let code = extract_code_from_doc(&doc);
            let progress = get_progress(&doc);
            let is_used = TERMINAL_USED_INDICATORS
                .iter()
                .any(|w| text_lower.contains(w))
                && code.is_none();

            let status = if is_used {
                "already_used_or_invalid"
            } else if code.is_some()
                || progress.contains("100%")
                || text.contains("感谢")
                || text.contains("验证码")
            {
                "completed"
            } else {
                "unknown_terminal"
            };

            let excerpt: String = text.trim().chars().take(600).collect();
            return Ok(SurveyResult {
                success: status == "completed",
                status: status.to_string(),
                code,
                progress: if !progress.is_empty() {
                    progress
                } else if status == "completed" {
                    "100%".to_string()
                } else {
                    String::new()
                },
                pages_visited: step,
                elapsed_ms: started.elapsed().as_millis(),
                final_url: current_url,
                target_url,
                receipt_code,
                page_text_excerpt: excerpt,
            });
        }

        let form = form_opt.unwrap();
        let is_receipt = form
            .select(&sel("input[name='shop_code'], input[name='receipt_code']"))
            .next()
            .is_some();

        let payload = if is_receipt {
            build_receipt_payload(form, &receipt)
        } else {
            build_question_payload(form, comment_text, retry_same_page)
        };

        let prev_fp = page_fingerprint(&doc);
        let action = form.value().attr("action").unwrap_or("");
        let post_url = if action.is_empty() {
            current_url.clone()
        } else {
            Url::parse(&current_url)
                .and_then(|base| base.join(action))
                .map(|u| u.to_string())
                .unwrap_or_else(|_| current_url.clone())
        };

        let form_refs: Vec<(&str, &str)> = payload
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect();

        resp = agent
            .post(&post_url)
            .header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
            .send_form(form_refs)
            .map_err(|e| format!("提交问卷第 {step} 页失败: {e}"))?;

        current_url = resp.get_uri().to_string();
        html = resp
            .body_mut()
            .read_to_string()
            .map_err(|e| format!("读取第 {step} 页响应失败: {e}"))?;

        let next_doc = Html::parse_document(&html);
        let next_fp = page_fingerprint(&next_doc);
        let has_error = next_doc.select(&sel("tr.error, .error")).next().is_some();

        if next_fp == prev_fp || has_error {
            if retry_same_page {
                let excerpt: String = main_text(&next_doc).trim().chars().take(600).collect();
                return Ok(SurveyResult {
                    success: false,
                    status: "validation_stuck".to_string(),
                    code: None,
                    progress: get_progress(&next_doc),
                    pages_visited: step,
                    elapsed_ms: started.elapsed().as_millis(),
                    final_url: current_url,
                    target_url,
                    receipt_code,
                    page_text_excerpt: excerpt,
                });
            }
            retry_same_page = true;
        } else {
            retry_same_page = false;
        }
    }

    Ok(SurveyResult {
        success: false,
        status: "max_pages_exceeded".to_string(),
        code: None,
        progress: String::new(),
        pages_visited: max_pages,
        elapsed_ms: started.elapsed().as_millis(),
        final_url: current_url,
        target_url,
        receipt_code,
        page_text_excerpt: "超过最大翻页保护上限。".to_string(),
    })
}
