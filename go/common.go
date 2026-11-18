package main

import (
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

const (
	baseSurveyURL  = "https://cn-hamasushi.csfeedback.net/sv/zh"
	defaultComment = "菜品新鲜美味，服务态度很好，整体体验非常满意。"
)

var (
	surveyURLPattern = regexp.MustCompile(`(?i)https?://[^\s"'<>]*csfeedback\.net/sv/[^\s"'<>]+`)
	rcnSearchPattern = regexp.MustCompile(`(?i)RCN\d{16,}`)
	receiptPattern   = regexp.MustCompile(`(?i)^(RCN)(\d{2})(\d{2})(\d{2})(\d{2})\d{4}(\d{4})\d+$`)
	codeRegexes      = []*regexp.Regexp{
		regexp.MustCompile(`(?i)(?:验证码|调查编号|确认编号|兑换码|编号|Code)[：:\s]*([A-Za-z0-9-]{4,24})`),
		regexp.MustCompile(`(?i)(?:NO\.?|Number)[：:\s]*([A-Za-z0-9-]{4,24})`),
	}
	fallbackCodeRegex = regexp.MustCompile(`^[A-Za-z0-9-]{5,20}$`)
)

var captchaIndicators = []string{
	"cf-turnstile", "g-recaptcha", "h-captcha", "recaptcha", "turnstile",
	"hcaptcha", "verify you are human", "请完成安全验证", "人机验证",
	"滑动拼图", "点选验证码",
}

var usedIndicators = []string{
	"已回答", "已参与", "已使用", "已提交", "超过期限",
	"已过期", "无效", "无法回答", "not found", "expired",
}

var exactScores = map[string]int{
	"是":  35,
	"on": 35,
	"否":  -60,
	"无":  -50,
	"没有": -90,
	"其他": -20,
}

type scoreRule struct {
	keyword string
	weight  int
}

var optionScoreRules = []scoreRule{
	{"10\u3000非常推荐", 120},
	{"非常推荐", 115},
	{"非常满意", 110},
	{"十分满意", 110},
	{"满意", 100},
	{"肯定会", 95},
	{"愿意", 90},
	{"正好", 90},
	{"合适", 85},
	{"中国", 75},
	{"堂食", 70},
	{"几乎每天", 65},
	{"大约每周", 60},
	{"技术人员", 55},
	{"20～29岁", 55},
	{"20-29", 55},
	{"男性", 50},
	{"一个人", 50},
	{"6,000", 45},
	{"金枪鱼", 45},
	{"还可以", 40},
	{"应该会", 40},
	{"不太满意", -100},
	{"不满意", -110},
	{"完全不推荐", -120},
	{"不愿意", -90},
	{"不确定", -40},
	{"不会", -90},
	{"不好说", -40},
	{"太大", -60},
	{"稍大", -30},
	{"稍小", -30},
	{"太小", -60},
	{"太硬", -60},
	{"稍硬", -30},
	{"稍软", -30},
	{"太软", -60},
	{"不想回复", -80},
	{"未接受服务", -80},
	{"以上都不是", -100},
}

// ReceiptInfo holds parsed store/date/time fields from an RCN receipt code.
type ReceiptInfo struct {
	ReceiptCode string
	ShopCode    string
	Month       int
	HasMonth    bool
	Day         int
	HasDay      bool
	Hour        int
	HasHour     bool
	Minute      int
	HasMinute   bool
}

func normalizeTargetURL(raw string) (string, error) {
	cleaned := strings.TrimSpace(raw)
	if cleaned == "" {
		return "", fmt.Errorf("问卷链接或小票编号不能为空。")
	}
	if m := surveyURLPattern.FindString(cleaned); m != "" {
		return m, nil
	}
	if m := rcnSearchPattern.FindString(cleaned); m != "" {
		return baseSurveyURL + "/" + strings.ToUpper(m), nil
	}
	if strings.HasPrefix(cleaned, "http://") || strings.HasPrefix(cleaned, "https://") {
		return cleaned, nil
	}
	return baseSurveyURL + "/" + strings.TrimLeft(cleaned, "/"), nil
}

func extractReceiptFromURL(rawURL string) string {
	u, err := url.Parse(rawURL)
	if err != nil {
		return ""
	}
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	if len(parts) > 0 {
		last := parts[len(parts)-1]
		if strings.HasPrefix(strings.ToUpper(last), "RCN") {
			return last
		}
	}
	return ""
}

func parseReceiptCode(code string) ReceiptInfo {
	trimmed := strings.TrimSpace(code)
	info := ReceiptInfo{ReceiptCode: trimmed}
	m := receiptPattern.FindStringSubmatch(trimmed)
	if len(m) < 7 {
		return info
	}
	if mo, err := strconv.Atoi(m[2]); err == nil && mo >= 1 && mo <= 12 {
		info.Month = mo
		info.HasMonth = true
	}
	if dy, err := strconv.Atoi(m[3]); err == nil && dy >= 1 && dy <= 31 {
		info.Day = dy
		info.HasDay = true
	}
	if hr, err := strconv.Atoi(m[4]); err == nil && hr >= 0 && hr <= 23 {
		info.Hour = hr
		info.HasHour = true
	}
	if mn, err := strconv.Atoi(m[5]); err == nil && mn >= 0 && mn <= 59 {
		info.Minute = mn
		info.HasMinute = true
	}
	info.ShopCode = m[6]
	return info
}

func scoreOptionText(text string) int {
	normalized := strings.Join(strings.Fields(text), " ")
	if normalized == "" {
		return -10
	}
	lowered := strings.ToLower(normalized)
	if w, ok := exactScores[lowered]; ok {
		return w
	}
	score := 0
	for _, rule := range optionScoreRules {
		if strings.Contains(lowered, strings.ToLower(rule.keyword)) {
			if rule.weight < 0 {
				return rule.weight
			}
			if rule.weight > score {
				score = rule.weight
			}
		}
	}
	if strings.HasPrefix(normalized, "10") && score < 120 {
		score = 120
	}
	return score
}

func extractCodeFromText(text string) string {
	for _, re := range codeRegexes {
		if m := re.FindStringSubmatch(text); len(m) > 1 {
			return strings.TrimSpace(m[1])
		}
	}
	return ""
}

func hasCaptchaMarkers(textLower, htmlLower string) bool {
	for _, marker := range captchaIndicators {
		if strings.Contains(textLower, marker) || strings.Contains(htmlLower, marker) {
			return true
		}
	}
	return false
}
