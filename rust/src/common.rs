//! Shared receipt parsing, URL normalization, option scoring, and code extraction helpers.

use regex::Regex;
use std::sync::OnceLock;
use url::Url;

pub const BASE_SURVEY_URL: &str = "https://cn-hamasushi.csfeedback.net/sv/zh";
pub const DEFAULT_COMMENT: &str = "菜品新鲜美味，服务态度很好，整体体验非常满意。";

pub const CAPTCHA_INDICATORS: &[&str] = &[
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
];

pub const TERMINAL_USED_INDICATORS: &[&str] = &[
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
];

const EXACT_SCORES: &[(&str, i32)] = &[
    ("是", 35),
    ("on", 35),
    ("否", -60),
    ("无", -50),
    ("没有", -90),
    ("其他", -20),
];

const OPTION_SCORE_RULES: &[(&str, i32)] = &[
    ("10\u{3000}非常推荐", 120),
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
];

#[derive(Debug, Clone, Default)]
pub struct ReceiptInfo {
    pub receipt_code: String,
    pub shop_code: String,
    pub month: Option<u32>,
    pub day: Option<u32>,
    pub hour: Option<u32>,
    pub minute: Option<u32>,
}

pub fn survey_url_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)https?://[^\s\x22'<>]*csfeedback\.net/sv/[^\s\x22'<>]+").unwrap()
    })
}

pub fn rcn_search_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)RCN\d{16,}").unwrap())
}

pub fn normalize_target_url(raw: &str) -> Result<String, String> {
    let cleaned = raw.trim();
    if cleaned.is_empty() {
        return Err("问卷链接或小票编号不能为空。".to_string());
    }
    if let Some(m) = survey_url_regex().find(cleaned) {
        return Ok(m.as_str().to_string());
    }
    if let Some(m) = rcn_search_regex().find(cleaned) {
        return Ok(format!("{BASE_SURVEY_URL}/{}", m.as_str().to_uppercase()));
    }
    if cleaned.starts_with("http://") || cleaned.starts_with("https://") {
        return Ok(cleaned.to_string());
    }
    Ok(format!("{BASE_SURVEY_URL}/{}", cleaned.trim_start_matches('/')))
}

pub fn extract_receipt_from_url(raw_url: &str) -> String {
    if let Ok(parsed) = Url::parse(raw_url)
        && let Some(last) = parsed.path_segments().and_then(|s| s.filter(|p| !p.is_empty()).next_back())
        && last.to_uppercase().starts_with("RCN")
    {
        return last.to_string();
    }
    String::new()
}

pub fn parse_receipt_code(code: &str) -> ReceiptInfo {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| {
        Regex::new(r"(?i)^(RCN)(\d{2})(\d{2})(\d{2})(\d{2})\d{4}(\d{4})\d+$").unwrap()
    });
    let trimmed = code.trim();
    let mut info = ReceiptInfo {
        receipt_code: trimmed.to_string(),
        ..Default::default()
    };
    let Some(caps) = re.captures(trimmed) else {
        return info;
    };

    let month: u32 = caps[2].parse().unwrap_or(0);
    let day: u32 = caps[3].parse().unwrap_or(0);
    let hour: u32 = caps[4].parse().unwrap_or(99);
    let minute: u32 = caps[5].parse().unwrap_or(99);

    if (1..=12).contains(&month) {
        info.month = Some(month);
    }
    if (1..=31).contains(&day) {
        info.day = Some(day);
    }
    if hour <= 23 {
        info.hour = Some(hour);
    }
    if minute <= 59 {
        info.minute = Some(minute);
    }
    info.shop_code = caps[6].to_string();
    info
}

pub fn score_option_text(text: &str) -> i32 {
    let normalized: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized.is_empty() {
        return -10;
    }
    let lowered = normalized.to_lowercase();
    for (exact, weight) in EXACT_SCORES {
        if lowered == *exact {
            return *weight;
        }
    }
    let mut score = 0;
    for (kw, weight) in OPTION_SCORE_RULES {
        if lowered.contains(&kw.to_lowercase()) {
            if *weight < 0 {
                return *weight;
            }
            if *weight > score {
                score = *weight;
            }
        }
    }
    if normalized.starts_with("10") {
        score = score.max(120);
    }
    score
}

pub fn extract_code_from_text(text: &str) -> Option<String> {
    static RE1: OnceLock<Regex> = OnceLock::new();
    static RE2: OnceLock<Regex> = OnceLock::new();
    let re1 = RE1.get_or_init(|| {
        Regex::new(r"(?i)(?:验证码|调查编号|确认编号|兑换码|编号|Code)[：:\s]*([A-Za-z0-9-]{4,24})").unwrap()
    });
    let re2 = RE2.get_or_init(|| {
        Regex::new(r"(?i)(?:NO\.?|Number)[：:\s]*([A-Za-z0-9-]{4,24})").unwrap()
    });

    for re in [re1, re2] {
        if let Some(caps) = re.captures(text) {
            return Some(caps[1].trim().to_string());
        }
    }
    None
}

pub fn has_captcha_markers(text_lower: &str, html_lower: &str) -> bool {
    CAPTCHA_INDICATORS
        .iter()
        .any(|marker| text_lower.contains(marker) || html_lower.contains(marker))
}
