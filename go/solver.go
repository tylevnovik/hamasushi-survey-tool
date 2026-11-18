package main

import (
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strconv"
	"strings"
	"time"

	"golang.org/x/net/html"
)

const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"

// SurveyResult captures the outcome of solving a Hamasushi survey.
type SurveyResult struct {
	Success         bool   `json:"success"`
	Status          string `json:"status"`
	Code            string `json:"code,omitempty"`
	Progress        string `json:"progress"`
	PagesVisited    int    `json:"pages_visited"`
	ElapsedMs       int64  `json:"elapsed_ms"`
	FinalURL        string `json:"final_url"`
	TargetURL       string `json:"target_url"`
	ReceiptCode     string `json:"receipt_code"`
	PageTextExcerpt string `json:"page_text_excerpt"`
}

func attr(n *html.Node, key string) (string, bool) {
	for _, a := range n.Attr {
		if strings.EqualFold(a.Key, key) {
			return a.Val, true
		}
	}
	return "", false
}

func attrOr(n *html.Node, key, fallback string) string {
	if v, ok := attr(n, key); ok {
		return v
	}
	return fallback
}

func hasClass(n *html.Node, cls string) bool {
	val, ok := attr(n, "class")
	if !ok {
		return false
	}
	for _, part := range strings.Fields(val) {
		if part == cls {
			return true
		}
	}
	return false
}

func findFirst(root *html.Node, pred func(*html.Node) bool) *html.Node {
	var found *html.Node
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if found != nil {
			return
		}
		if pred(n) {
			found = n
			return
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
	}
	walk(root)
	return found
}

func findAll(root *html.Node, pred func(*html.Node) bool) []*html.Node {
	var out []*html.Node
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if pred(n) {
			out = append(out, n)
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
	}
	walk(root)
	return out
}

func visibleText(root *html.Node) string {
	var lines []string
	var walk func(*html.Node, bool)
	walk = func(n *html.Node, hidden bool) {
		if n.Type == html.ElementNode && (n.Data == "style" || n.Data == "script") {
			hidden = true
		}
		if !hidden && n.Type == html.TextNode {
			s := strings.TrimSpace(n.Data)
			if s != "" {
				lines = append(lines, s)
			}
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c, hidden)
		}
	}
	walk(root, false)
	return strings.Join(lines, "\n")
}

func cleanText(n *html.Node) string {
	return strings.Join(strings.Fields(visibleText(n)), " ")
}

func getProgress(doc *html.Node) string {
	el := findFirst(doc, func(n *html.Node) bool {
		return n.Type == html.ElementNode && hasClass(n, "progress_bar")
	})
	if el == nil {
		return ""
	}
	return cleanText(el)
}

func mainText(doc *html.Node) string {
	if el := findFirst(doc, func(n *html.Node) bool {
		return n.Type == html.ElementNode && attrOr(n, "id", "") == "main"
	}); el != nil {
		return visibleText(el)
	}
	if el := findFirst(doc, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "body"
	}); el != nil {
		return visibleText(el)
	}
	return visibleText(doc)
}

func resolveLabelText(form, inp *html.Node) string {
	if id, ok := attr(inp, "id"); ok && id != "" {
		lbl := findFirst(form, func(n *html.Node) bool {
			return n.Type == html.ElementNode && n.Data == "label" && attrOr(n, "for", "") == id
		})
		if lbl != nil {
			return cleanText(lbl)
		}
	}
	for p := inp.Parent; p != nil; p = p.Parent {
		if p.Type == html.ElementNode && (p.Data == "label" || p.Data == "li") {
			return cleanText(p)
		}
	}
	return ""
}

func formHasQuestionInputs(form *html.Node) bool {
	if form == nil {
		return false
	}
	return findFirst(form, func(n *html.Node) bool {
		if n.Type != html.ElementNode {
			return false
		}
		if n.Data == "select" || n.Data == "textarea" {
			return true
		}
		if n.Data == "input" {
			t := strings.ToLower(attrOr(n, "type", "text"))
			return t == "radio" || t == "checkbox" || t == "text" || t == "range"
		}
		return false
	}) != nil
}

func selectedOptionValue(form *html.Node, selectName string) string {
	sel := findFirst(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "select" && attrOr(n, "name", "") == selectName
	})
	if sel == nil {
		return ""
	}
	opts := findAll(sel, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "option"
	})
	for _, o := range opts {
		if _, ok := attr(o, "selected"); ok {
			return attrOr(o, "value", "")
		}
	}
	if len(opts) > 0 {
		return attrOr(opts[0], "value", "")
	}
	return ""
}

func buildReceiptPayload(form *html.Node, receipt ReceiptInfo) url.Values {
	vals := url.Values{}

	recInp := findFirst(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "input" && attrOr(n, "name", "") == "receipt_code"
	})
	codeVal := ""
	if recInp != nil {
		codeVal = strings.TrimSpace(attrOr(recInp, "value", ""))
	}
	if codeVal == "" {
		codeVal = receipt.ReceiptCode
	}
	parsed := receipt
	if codeVal != "" {
		parsed = parseReceiptCode(codeVal)
	}

	shopInp := findFirst(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "input" && attrOr(n, "name", "") == "shop_code"
	})
	shopVal := ""
	if shopInp != nil {
		shopVal = strings.TrimSpace(attrOr(shopInp, "value", ""))
	}
	if shopVal == "" {
		shopVal = parsed.ShopCode
	}

	monthVal := selectedOptionValue(form, "month")
	if parsed.HasMonth {
		monthVal = strconv.Itoa(parsed.Month)
	}
	dayVal := selectedOptionValue(form, "day")
	if parsed.HasDay {
		dayVal = strconv.Itoa(parsed.Day)
	}
	hourVal := selectedOptionValue(form, "select_visit_hour")
	if parsed.HasHour {
		hourVal = strconv.Itoa(parsed.Hour)
	}
	minVal := selectedOptionValue(form, "select_visit_minute")
	if minVal == "" {
		minVal = "0"
	}
	if parsed.HasMinute {
		minVal = strconv.Itoa(parsed.Minute)
	}

	visitHourVal := ""
	if hourVal != "" {
		mNum, _ := strconv.Atoi(minVal)
		visitHourVal = fmt.Sprintf("%s:%02d", hourVal, mNum)
	}

	for _, hidden := range findAll(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "input" && strings.EqualFold(attrOr(n, "type", ""), "hidden")
	}) {
		name := attrOr(hidden, "name", "")
		if name == "" {
			continue
		}
		if name == "visit_hour" && visitHourVal != "" {
			vals.Add(name, visitHourVal)
		} else {
			vals.Add(name, attrOr(hidden, "value", ""))
		}
	}

	if shopInp != nil {
		vals.Set("shop_code", shopVal)
	}
	for _, pair := range [][2]string{
		{"month", monthVal},
		{"day", dayVal},
		{"select_visit_hour", hourVal},
		{"select_visit_minute", minVal},
	} {
		if findFirst(form, func(n *html.Node) bool {
			return n.Type == html.ElementNode && n.Data == "select" && attrOr(n, "name", "") == pair[0]
		}) != nil {
			vals.Set(pair[0], pair[1])
		}
	}
	if recInp != nil {
		vals.Set("receipt_code", codeVal)
	}
	if findFirst(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "input" && attrOr(n, "name", "") == "agree"
	}) != nil {
		vals.Set("agree", "on")
	}
	return vals
}

func buildQuestionPayload(form *html.Node, comment string, forceFillText bool) url.Values {
	vals := url.Values{}

	for _, hidden := range findAll(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "input" && strings.EqualFold(attrOr(n, "type", ""), "hidden")
	}) {
		if name := attrOr(hidden, "name", ""); name != "" {
			vals.Add(name, attrOr(hidden, "value", ""))
		}
	}

	var radioOrder []string
	radioGroups := map[string][]*html.Node{}
	for _, r := range findAll(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "input" && strings.EqualFold(attrOr(n, "type", ""), "radio")
	}) {
		name := attrOr(r, "name", "")
		if name == "" {
			continue
		}
		if _, exists := radioGroups[name]; !exists {
			radioOrder = append(radioOrder, name)
		}
		radioGroups[name] = append(radioGroups[name], r)
	}
	for _, name := range radioOrder {
		bestVal := ""
		bestScore := -100000
		for _, r := range radioGroups[name] {
			val, ok := attr(r, "value")
			if !ok {
				continue
			}
			sc := scoreOptionText(resolveLabelText(form, r))
			if sc > bestScore {
				bestScore = sc
				bestVal = val
			}
		}
		vals.Add(name, bestVal)
	}

	var cbOrder []string
	cbGroups := map[string][]*html.Node{}
	for _, cb := range findAll(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "input" && strings.EqualFold(attrOr(n, "type", ""), "checkbox")
	}) {
		name := attrOr(cb, "name", "")
		if name == "" {
			continue
		}
		if _, exists := cbGroups[name]; !exists {
			cbOrder = append(cbOrder, name)
		}
		cbGroups[name] = append(cbGroups[name], cb)
	}
	for _, name := range cbOrder {
		bestVal := "on"
		bestScore := -100000
		for _, cb := range cbGroups[name] {
			val := attrOr(cb, "value", "on")
			if val == "" {
				val = "on"
			}
			sc := scoreOptionText(resolveLabelText(form, cb))
			if sc > bestScore {
				bestScore = sc
				bestVal = val
			}
		}
		vals.Add(name, bestVal)
	}

	for _, selEl := range findAll(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "select"
	}) {
		name := attrOr(selEl, "name", "")
		if name == "" {
			continue
		}
		opts := findAll(selEl, func(n *html.Node) bool {
			return n.Type == html.ElementNode && n.Data == "option"
		})
		chosen := ""
		for _, o := range opts {
			if _, ok := attr(o, "selected"); ok {
				if v := strings.TrimSpace(attrOr(o, "value", "")); v != "" {
					chosen = v
					break
				}
			}
		}
		if chosen == "" {
			bestScore := -100000
			for _, o := range opts {
				v := strings.TrimSpace(attrOr(o, "value", ""))
				if v == "" {
					continue
				}
				sc := scoreOptionText(cleanText(o))
				if sc > bestScore {
					bestScore = sc
					chosen = v
				}
			}
		}
		vals.Add(name, chosen)
	}

	for _, slider := range findAll(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "input" && strings.EqualFold(attrOr(n, "type", ""), "range")
	}) {
		if name := attrOr(slider, "name", ""); name != "" {
			vals.Add(name, attrOr(slider, "max", "10"))
		}
	}

	for _, ta := range findAll(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "textarea"
	}) {
		if name := attrOr(ta, "name", ""); name != "" {
			existing := visibleText(ta)
			if strings.TrimSpace(existing) != "" {
				vals.Add(name, existing)
			} else if forceFillText {
				vals.Add(name, comment)
			} else {
				vals.Add(name, "")
			}
		}
	}

	for _, txt := range findAll(form, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "input" && strings.EqualFold(attrOr(n, "type", "text"), "text")
	}) {
		if name := attrOr(txt, "name", ""); name != "" {
			existing := attrOr(txt, "value", "")
			if strings.TrimSpace(existing) != "" {
				vals.Add(name, existing)
			} else if forceFillText {
				vals.Add(name, comment)
			} else {
				vals.Add(name, "")
			}
		}
	}

	return vals
}

func extractCodeFromDoc(doc *html.Node) string {
	text := mainText(doc)
	if code := extractCodeFromText(text); code != "" {
		return code
	}
	container := findFirst(doc, func(n *html.Node) bool {
		return n.Type == html.ElementNode && attrOr(n, "id", "") == "main"
	})
	if container == nil {
		container = doc
	}
	for _, el := range findAll(container, func(n *html.Node) bool {
		if n.Type != html.ElementNode {
			return false
		}
		switch n.Data {
		case "strong", "b", "p", "span":
			return true
		default:
			return hasClass(n, "code") || hasClass(n, "number")
		}
	}) {
		cand := cleanText(el)
		if fallbackCodeRegex.MatchString(cand) && !strings.HasPrefix(strings.ToLower(cand), "ref:") {
			return cand
		}
	}
	return ""
}

func pageFingerprint(doc *html.Node) string {
	progress := getProgress(doc)
	var qNames []string
	for _, el := range findAll(doc, func(n *html.Node) bool {
		return n.Type == html.ElementNode && strings.HasPrefix(attrOr(n, "name", ""), "q_")
	}) {
		qNames = append(qNames, attrOr(el, "name", ""))
	}
	txt := strings.Join(strings.Fields(mainText(doc)), " ")
	runes := []rune(txt)
	if len(runes) > 220 {
		txt = string(runes[:220])
	}
	return fmt.Sprintf("%s|%s|%s", progress, strings.Join(qNames, ","), txt)
}

func doRequest(client *http.Client, method, target string, form url.Values) (string, string, error) {
	var body io.Reader
	if method == http.MethodPost {
		body = strings.NewReader(form.Encode())
	}
	req, err := http.NewRequest(method, target, body)
	if err != nil {
		return "", "", err
	}
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
	if method == http.MethodPost {
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	}

	resp, err := client.Do(req)
	if err != nil {
		return "", "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return "", "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	if err != nil {
		return "", "", err
	}
	return string(raw), resp.Request.URL.String(), nil
}

func truncateRunes(s string, max int) string {
	r := []rune(strings.TrimSpace(s))
	if len(r) > max {
		return string(r[:max])
	}
	return string(r)
}

func solveSurveyHTTP(rawInput, comment string, maxPages int) (SurveyResult, error) {
	started := time.Now()
	if strings.TrimSpace(comment) == "" {
		comment = defaultComment
	}
	targetURL, err := normalizeTargetURL(rawInput)
	if err != nil {
		return SurveyResult{}, err
	}
	receiptCode := extractReceiptFromURL(targetURL)
	receipt := parseReceiptCode(receiptCode)

	jar, _ := cookiejar.New(nil)
	client := &http.Client{
		Jar:     jar,
		Timeout: 15 * time.Second,
	}

	rawHTML, currentURL, err := doRequest(client, http.MethodGet, targetURL, nil)
	if err != nil {
		return SurveyResult{}, fmt.Errorf("请求问卷页面失败: %w", err)
	}

	retrySamePage := false
	for step := 1; step <= maxPages; step++ {
		doc, err := html.Parse(strings.NewReader(rawHTML))
		if err != nil {
			return SurveyResult{}, fmt.Errorf("解析问卷页面失败: %w", err)
		}
		text := mainText(doc)
		textLower := strings.ToLower(text)
		htmlLower := strings.ToLower(rawHTML)

		if hasCaptchaMarkers(textLower, htmlLower) {
			return SurveyResult{
				Success:         false,
				Status:          "captcha_blocked",
				Progress:        getProgress(doc),
				PagesVisited:    step,
				ElapsedMs:       time.Since(started).Milliseconds(),
				FinalURL:        currentURL,
				TargetURL:       targetURL,
				ReceiptCode:     receiptCode,
				PageTextExcerpt: "检测到人机验证/CAPTCHA，已停止自动操作，请手动完成。",
			}, nil
		}

		form := findFirst(doc, func(n *html.Node) bool {
			return n.Type == html.ElementNode && n.Data == "form" && attrOr(n, "name", "") == "form1"
		})
		if !formHasQuestionInputs(form) {
			code := extractCodeFromDoc(doc)
			prog := getProgress(doc)
			isUsed := false
			if code == "" {
				for _, u := range usedIndicators {
					if strings.Contains(textLower, u) {
						isUsed = true
						break
					}
				}
			}
			status := "unknown_terminal"
			if isUsed {
				status = "already_used_or_invalid"
			} else if code != "" || strings.Contains(prog, "100%") || strings.Contains(text, "感谢") || strings.Contains(text, "验证码") {
				status = "completed"
			}
			if prog == "" && status == "completed" {
				prog = "100%"
			}
			return SurveyResult{
				Success:         status == "completed",
				Status:          status,
				Code:            code,
				Progress:        prog,
				PagesVisited:    step,
				ElapsedMs:       time.Since(started).Milliseconds(),
				FinalURL:        currentURL,
				TargetURL:       targetURL,
				ReceiptCode:     receiptCode,
				PageTextExcerpt: truncateRunes(text, 600),
			}, nil
		}

		isReceipt := findFirst(form, func(n *html.Node) bool {
			if n.Type != html.ElementNode || n.Data != "input" {
				return false
			}
			nm := attrOr(n, "name", "")
			return nm == "shop_code" || nm == "receipt_code"
		}) != nil

		var payload url.Values
		if isReceipt {
			payload = buildReceiptPayload(form, receipt)
		} else {
			payload = buildQuestionPayload(form, comment, retrySamePage)
		}

		prevFP := pageFingerprint(doc)
		postURL := currentURL
		if action := attrOr(form, "action", ""); action != "" {
			if base, err := url.Parse(currentURL); err == nil {
				if joined, err := base.Parse(action); err == nil {
					postURL = joined.String()
				}
			}
		}

		rawHTML, currentURL, err = doRequest(client, http.MethodPost, postURL, payload)
		if err != nil {
			return SurveyResult{}, fmt.Errorf("提交问卷第 %d 页失败: %w", step, err)
		}

		nextDoc, err := html.Parse(strings.NewReader(rawHTML))
		if err != nil {
			return SurveyResult{}, fmt.Errorf("解析第 %d 页响应失败: %w", step, err)
		}
		nextFP := pageFingerprint(nextDoc)
		hasError := findFirst(nextDoc, func(n *html.Node) bool {
			return n.Type == html.ElementNode && hasClass(n, "error")
		}) != nil

		if nextFP == prevFP || hasError {
			if retrySamePage {
				return SurveyResult{
					Success:         false,
					Status:          "validation_stuck",
					Progress:        getProgress(nextDoc),
					PagesVisited:    step,
					ElapsedMs:       time.Since(started).Milliseconds(),
					FinalURL:        currentURL,
					TargetURL:       targetURL,
					ReceiptCode:     receiptCode,
					PageTextExcerpt: truncateRunes(mainText(nextDoc), 600),
				}, nil
			}
			retrySamePage = true
		} else {
			retrySamePage = false
		}
	}

	return SurveyResult{
		Success:         false,
		Status:          "max_pages_exceeded",
		PagesVisited:    maxPages,
		ElapsedMs:       time.Since(started).Milliseconds(),
		FinalURL:        currentURL,
		TargetURL:       targetURL,
		ReceiptCode:     receiptCode,
		PageTextExcerpt: "超过最大翻页保护上限。",
	}, nil
}
