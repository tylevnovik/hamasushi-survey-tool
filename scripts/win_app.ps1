<#
.SYNOPSIS
Zero-install Hamasushi survey autofill desktop app using Windows built-in PowerShell (.NET) + Edge.
#>
param(
    [int]$Port = 0,
    [switch]$NoBrowser,
    [string]$SolveUrl = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Net.Http
Add-Type -AssemblyName System.Web
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$LocalPublic = Join-Path $PSScriptRoot "public"
$ParentPublic = Join-Path (Split-Path $PSScriptRoot -Parent) "public"
$PublicDir = if (Test-Path $LocalPublic) { $LocalPublic } else { $ParentPublic }
$BaseSurveyUrl = "https://cn-hamasushi.csfeedback.net/sv/zh"
$DefaultComment = "菜品新鲜美味，服务态度很好，整体体验非常满意。"

$CaptchaIndicators = @(
    "cf-turnstile", "g-recaptcha", "h-captcha", "recaptcha", "turnstile",
    "hcaptcha", "verify you are human", "请完成安全验证", "人机验证",
    "滑动拼图", "点选验证码"
)

$UsedIndicators = @(
    "已回答", "已参与", "已使用", "已提交", "超过期限",
    "已过期", "无效", "无法回答", "not found", "expired"
)

$ExactScores = @{
    "是"   = 35
    "on"   = 35
    "否"   = -60
    "无"   = -50
    "没有" = -90
    "其他" = -20
}

$ScoreRules = @(
    @("10　非常推荐", 120),
    @("非常推荐", 115),
    @("非常满意", 110),
    @("十分满意", 110),
    @("满意", 100),
    @("肯定会", 95),
    @("愿意", 90),
    @("正好", 90),
    @("合适", 85),
    @("中国", 75),
    @("堂食", 70),
    @("几乎每天", 65),
    @("大约每周", 60),
    @("技术人员", 55),
    @("20～29岁", 55),
    @("20-29", 55),
    @("男性", 50),
    @("一个人", 50),
    @("6,000", 45),
    @("金枪鱼", 45),
    @("还可以", 40),
    @("应该会", 40),
    @("不太满意", -100),
    @("不满意", -110),
    @("完全不推荐", -120),
    @("不愿意", -90),
    @("不确定", -40),
    @("不会", -90),
    @("不好说", -40),
    @("太大", -60),
    @("稍大", -30),
    @("稍小", -30),
    @("太小", -60),
    @("太硬", -60),
    @("稍硬", -30),
    @("稍软", -30),
    @("太软", -60),
    @("不想回复", -80),
    @("未接受服务", -80),
    @("以上都不是", -100)
)

function Strip-HtmlText {
    param([string]$Html)
    if ([string]::IsNullOrEmpty($Html)) { return "" }
    $noScripts = [regex]::Replace($Html, "<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", "IgnoreCase,Singleline")
    $noTags = [regex]::Replace($noScripts, "<[^>]+>", " ")
    $decoded = [System.Net.WebUtility]::HtmlDecode($noTags)
    return ([regex]::Replace($decoded, "\s+", " ")).Trim()
}

function Parse-HtmlAttributes {
    param([string]$TagText)
    $attrs = @{}
    $matches = [regex]::Matches($TagText, '([a-zA-Z0-9_:-]+)\s*(?:=\s*(?:"([^"]*)"|''([^'']*)''|([^\s>]+)))?')
    foreach ($m in $matches) {
        $key = $m.Groups[1].Value.ToLowerInvariant()
        if ($key -in @("input", "form", "select", "option", "textarea", "label")) { continue }
        $val = if ($m.Groups[2].Success) { $m.Groups[2].Value }
               elseif ($m.Groups[3].Success) { $m.Groups[3].Value }
               elseif ($m.Groups[4].Success) { $m.Groups[4].Value }
               else { "" }
        $attrs[$key] = [System.Net.WebUtility]::HtmlDecode($val)
    }
    return $attrs
}

function Normalize-TargetUrl {
    param([string]$RawInput)
    $cleaned = if ($RawInput) { $RawInput.Trim() } else { "" }
    if (-not $cleaned) { throw "问卷链接或小票编号不能为空。" }

    $urlMatch = [regex]::Match($cleaned, "https?://[^\s""'<>]*csfeedback\.net/sv/[^\s""'<>]+", "IgnoreCase")
    if ($urlMatch.Success) { return $urlMatch.Value }

    $rcnMatch = [regex]::Match($cleaned, "RCN\d{16,}", "IgnoreCase")
    if ($rcnMatch.Success) { return "$BaseSurveyUrl/$($rcnMatch.Value.ToUpperInvariant())" }

    if ($cleaned -match "^https?://") { return $cleaned }
    return "$BaseSurveyUrl/$($cleaned.TrimStart('/'))"
}

function Parse-ReceiptCode {
    param([string]$Code)
    $info = @{
        receipt_code = ($Code | ForEach-Object { $_.Trim() })
        shop_code    = ""
        month        = $null
        day          = $null
        hour         = $null
        minute       = $null
    }
    $m = [regex]::Match($info.receipt_code, "^RCN(\d{2})(\d{2})(\d{2})(\d{2})\d{4}(\d{4})\d+$", "IgnoreCase")
    if ($m.Success) {
        $mo = [int]$m.Groups[1].Value
        $dy = [int]$m.Groups[2].Value
        $hr = [int]$m.Groups[3].Value
        $mn = [int]$m.Groups[4].Value
        if ($mo -ge 1 -and $mo -le 12) { $info.month = $mo }
        if ($dy -ge 1 -and $dy -le 31) { $info.day = $dy }
        if ($hr -ge 0 -and $hr -le 23) { $info.hour = $hr }
        if ($mn -ge 0 -and $mn -le 59) { $info.minute = $mn }
        $info.shop_code = $m.Groups[5].Value
    }
    return $info
}

function Get-OptionScore {
    param([string]$Text)
    $norm = ([regex]::Replace(($Text + ""), "\s+", " ")).Trim()
    if (-not $norm) { return -10 }
    $lower = $norm.ToLowerInvariant()
    if ($ExactScores.ContainsKey($lower)) { return $ExactScores[$lower] }

    $best = 0
    foreach ($rule in $ScoreRules) {
        $kw = $rule[0].ToLowerInvariant()
        $w = [int]$rule[1]
        if ($lower.Contains($kw)) {
            if ($w -lt 0) { return $w }
            if ($w -gt $best) { $best = $w }
        }
    }
    if ($norm.StartsWith("10")) { $best = [Math]::Max($best, 120) }
    return $best
}

function Get-ProgressText {
    param([string]$Html)
    $m = [regex]::Match($Html, '<div[^>]*class=["''][^"'']*progress_bar[^"'']*["''][^>]*>(.*?)</div>', "IgnoreCase,Singleline")
    if ($m.Success) { return (Strip-HtmlText $m.Groups[1].Value) }
    return ""
}

function Extract-ValidationCode {
    param([string]$Html)
    $mainHtml = $Html
    $mainM = [regex]::Match($Html, '<main[^>]*>(.*?)</main>', "IgnoreCase,Singleline")
    if ($mainM.Success) { $mainHtml = $mainM.Groups[1].Value }
    $text = Strip-HtmlText $mainHtml

    $patterns = @(
        '(?:验证码|调查编号|确认编号|兑换码|编号|Code)[：:\s]*([A-Za-z0-9-]{4,24})',
        '(?:NO\.?|Number)[：:\s]*([A-Za-z0-9-]{4,24})'
    )
    foreach ($pat in $patterns) {
        $m = [regex]::Match($text, $pat, "IgnoreCase")
        if ($m.Success) { return $m.Groups[1].Value.Trim() }
    }

    $tagMatches = [regex]::Matches($mainHtml, '<(?:strong|b|p|span)[^>]*>(.*?)</(?:strong|b|p|span)>', "IgnoreCase,Singleline")
    foreach ($tm in $tagMatches) {
        $cand = (Strip-HtmlText $tm.Groups[1].Value)
        if ($cand -match '^[A-Za-z0-9-]{5,20}$' -and -not $cand.ToLowerInvariant().StartsWith("ref:")) {
            return $cand
        }
    }
    return $null
}

function Get-LabelMap {
    param([string]$FormHtml)
    $labels = @{}
    $matches = [regex]::Matches($FormHtml, '<label[^>]*for=["'']([^"'']+)["''][^>]*>(.*?)</label>', "IgnoreCase,Singleline")
    foreach ($m in $matches) {
        $id = $m.Groups[1].Value
        $labels[$id] = Strip-HtmlText $m.Groups[2].Value
    }
    return $labels
}

function Get-SelectOptions {
    param([string]$FormHtml)
    $selects = [System.Collections.Generic.List[object]]::new()
    $selMatches = [regex]::Matches($FormHtml, '<select([^>]*)>(.*?)</select>', "IgnoreCase,Singleline")
    foreach ($sm in $selMatches) {
        $attrs = Parse-HtmlAttributes $sm.Groups[1].Value
        $name = $attrs["name"]
        if (-not $name) { continue }
        $opts = [System.Collections.Generic.List[object]]::new()
        $optMatches = [regex]::Matches($sm.Groups[2].Value, '<option([^>]*)>(.*?)</option>', "IgnoreCase,Singleline")
        foreach ($om in $optMatches) {
            $oattrs = Parse-HtmlAttributes $om.Groups[1].Value
            $opts.Add(@{
                value    = if ($oattrs.ContainsKey("value")) { $oattrs["value"] } else { Strip-HtmlText $om.Groups[2].Value }
                text     = Strip-HtmlText $om.Groups[2].Value
                selected = $om.Groups[1].Value -match '\bselected\b'
            })
        }
        $selects.Add(@{ name = $name; options = $opts })
    }
    return $selects
}

function Build-FormPayload {
    param(
        [string]$FormHtml,
        [hashtable]$Receipt,
        [string]$Comment,
        [bool]$ForceFillText
    )

    $pairs = [System.Collections.Generic.List[System.Collections.Generic.KeyValuePair[string, string]]]::new()
    $inputs = [System.Collections.Generic.List[hashtable]]::new()
    foreach ($im in [regex]::Matches($FormHtml, '<input([^>]*)>', "IgnoreCase")) {
        $attrs = Parse-HtmlAttributes $im.Groups[1].Value
        $inputs.Add($attrs)
    }

    $selects = Get-SelectOptions $FormHtml
    $textareas = [System.Collections.Generic.List[hashtable]]::new()
    foreach ($tm in [regex]::Matches($FormHtml, '<textarea([^>]*)>(.*?)</textarea>', "IgnoreCase,Singleline")) {
        $attrs = Parse-HtmlAttributes $tm.Groups[1].Value
        $attrs["text"] = [System.Net.WebUtility]::HtmlDecode($tm.Groups[2].Value)
        $textareas.Add($attrs)
    }

    $hasQuestions = ($inputs | Where-Object { $_["type"] -in @("radio", "checkbox", "text", "range") }).Count -gt 0 -or
                    $selects.Count -gt 0 -or $textareas.Count -gt 0
    if (-not $hasQuestions) {
        return @{ has_inputs = $false; pairs = $pairs }
    }

    $isReceipt = ($inputs | Where-Object { $_["name"] -in @("shop_code", "receipt_code") }).Count -gt 0
    $labels = Get-LabelMap $FormHtml

    if ($isReceipt) {
        $shopInp = $inputs | Where-Object { $_["name"] -eq "shop_code" } | Select-Object -First 1
        $recInp = $inputs | Where-Object { $_["name"] -eq "receipt_code" } | Select-Object -First 1
        $codeVal = if ($recInp -and $recInp["value"]) { $recInp["value"].Trim() } else { $Receipt.receipt_code }
        $parsed = if ($codeVal) { Parse-ReceiptCode $codeVal } else { $Receipt }
        $shopVal = if ($shopInp -and $shopInp["value"]) { $shopInp["value"].Trim() } else { $parsed.shop_code }

        $selMap = @{}
        foreach ($s in $selects) {
            $selOpt = $s.options | Where-Object { $_.selected } | Select-Object -First 1
            if (-not $selOpt) { $selOpt = $s.options | Select-Object -First 1 }
            $selMap[$s.name] = if ($selOpt) { $selOpt.value } else { "" }
        }

        $moVal = if ($null -ne $parsed.month) { [string]$parsed.month } else { $selMap["month"] }
        $dyVal = if ($null -ne $parsed.day) { [string]$parsed.day } else { $selMap["day"] }
        $hrVal = if ($null -ne $parsed.hour) { [string]$parsed.hour } else { $selMap["select_visit_hour"] }
        $mnVal = if ($null -ne $parsed.minute) { [string]$parsed.minute } elseif ($selMap["select_visit_minute"]) { $selMap["select_visit_minute"] } else { "0" }
        $vhVal = if ($hrVal -ne "") { "{0}:{1:D2}" -f [int]$hrVal, [int]$mnVal } else { "" }

        foreach ($inp in $inputs) {
            if ($inp["type"] -eq "hidden" -and $inp["name"]) {
                $v = if ($inp["name"] -eq "visit_hour" -and $vhVal) { $vhVal } else { [string]$inp["value"] }
                $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new($inp["name"], $v))
            }
        }
        if ($shopInp) { $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new("shop_code", $shopVal)) }
        if ($selMap.ContainsKey("month")) { $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new("month", $moVal)) }
        if ($selMap.ContainsKey("day")) { $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new("day", $dyVal)) }
        if ($selMap.ContainsKey("select_visit_hour")) { $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new("select_visit_hour", $hrVal)) }
        if ($selMap.ContainsKey("select_visit_minute")) { $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new("select_visit_minute", $mnVal)) }
        if ($recInp) { $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new("receipt_code", $codeVal)) }
        if (($inputs | Where-Object { $_["name"] -eq "agree" }).Count -gt 0) {
            $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new("agree", "on"))
        }
        return @{ has_inputs = $true; pairs = $pairs }
    }

    foreach ($inp in $inputs) {
        if ($inp["type"] -eq "hidden" -and $inp["name"]) {
            $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new($inp["name"], [string]$inp["value"]))
        }
    }

    $radioOrder = [System.Collections.Generic.List[string]]::new()
    $radioGroups = @{}
    foreach ($inp in $inputs) {
        if ($inp["type"] -eq "radio" -and $inp["name"]) {
            $n = $inp["name"]
            if (-not $radioGroups.ContainsKey($n)) {
                $radioGroups[$n] = [System.Collections.Generic.List[hashtable]]::new()
                $radioOrder.Add($n)
            }
            $radioGroups[$n].Add($inp)
        }
    }
    foreach ($name in $radioOrder) {
        $bestVal = ""
        $bestScore = -10000
        foreach ($r in $radioGroups[$name]) {
            $lbl = if ($r["id"] -and $labels.ContainsKey($r["id"])) { $labels[$r["id"]] } else { "" }
            $sc = Get-OptionScore $lbl
            if ($sc -gt $bestScore) {
                $bestScore = $sc
                $bestVal = [string]$r["value"]
            }
        }
        $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new($name, $bestVal))
    }

    $cbOrder = [System.Collections.Generic.List[string]]::new()
    $cbGroups = @{}
    foreach ($inp in $inputs) {
        if ($inp["type"] -eq "checkbox" -and $inp["name"]) {
            $n = $inp["name"]
            if (-not $cbGroups.ContainsKey($n)) {
                $cbGroups[$n] = [System.Collections.Generic.List[hashtable]]::new()
                $cbOrder.Add($n)
            }
            $cbGroups[$n].Add($inp)
        }
    }
    foreach ($name in $cbOrder) {
        $bestVal = "on"
        $bestScore = -10000
        foreach ($c in $cbGroups[$name]) {
            $lbl = if ($c["id"] -and $labels.ContainsKey($c["id"])) { $labels[$c["id"]] } else { "" }
            $sc = Get-OptionScore $lbl
            if ($sc -gt $bestScore) {
                $bestScore = $sc
                $bestVal = if ($c["value"]) { [string]$c["value"] } else { "on" }
            }
        }
        $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new($name, $bestVal))
    }

    foreach ($s in $selects) {
        $selOpt = $s.options | Where-Object { $_.selected -and $_.value.Trim() -ne "" } | Select-Object -First 1
        if ($selOpt) {
            $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new($s.name, $selOpt.value))
            continue
        }
        $bestVal = ""
        $bestScore = -10000
        foreach ($opt in $s.options) {
            if (-not $opt.value.Trim()) { continue }
            $sc = Get-OptionScore $opt.text
            if ($sc -gt $bestScore) {
                $bestScore = $sc
                $bestVal = $opt.value
            }
        }
        $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new($s.name, $bestVal))
    }

    foreach ($inp in $inputs) {
        if ($inp["type"] -eq "range" -and $inp["name"]) {
            $maxVal = if ($inp["max"]) { [string]$inp["max"] } else { "10" }
            $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new($inp["name"], $maxVal))
        }
        elseif ($inp["type"] -eq "text" -and $inp["name"]) {
            $existing = [string]$inp["value"]
            $val = if ($existing.Trim()) { $existing } elseif ($ForceFillText) { $Comment } else { "" }
            $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new($inp["name"], $val))
        }
    }

    foreach ($ta in $textareas) {
        if ($ta["name"]) {
            $existing = [string]$ta["text"]
            $val = if ($existing.Trim()) { $existing } elseif ($ForceFillText) { $Comment } else { "" }
            $pairs.Add([System.Collections.Generic.KeyValuePair[string, string]]::new($ta["name"], $val))
        }
    }

    return @{ has_inputs = $true; pairs = $pairs }
}

function Invoke-SolveSurvey {
    param(
        [string]$RawInput,
        [string]$Comment = $DefaultComment,
        [int]$MaxPages = 35
    )

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $targetUrl = Normalize-TargetUrl $RawInput
    $uri = [Uri]$targetUrl
    $segs = $uri.AbsolutePath.Split("/", [System.StringSplitOptions]::RemoveEmptyEntries)
    $rcn = if ($segs.Length -gt 0 -and $segs[$segs.Length - 1] -match "^RCN") { $segs[$segs.Length - 1] } else { "" }
    $receipt = Parse-ReceiptCode $rcn

    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.UseCookies = $true
    $handler.CookieContainer = New-Object System.Net.CookieContainer
    $handler.AllowAutoRedirect = $true
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(15)
    $client.DefaultRequestHeaders.TryAddWithoutValidation("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36") | Out-Null

    try {
        $resp = $client.GetAsync($targetUrl).Result
        $resp.EnsureSuccessStatusCode() | Out-Null
        $html = $resp.Content.ReadAsStringAsync().Result
        $currentUrl = $resp.RequestMessage.RequestUri.AbsoluteUri
        $retrySamePage = $false

        for ($step = 1; $step -le $MaxPages; $step++) {
            $lowerHtml = $html.ToLowerInvariant()
            $mainM = [regex]::Match($html, '<main[^>]*>(.*?)</main>', "IgnoreCase,Singleline")
            $mainHtml = if ($mainM.Success) { $mainM.Groups[1].Value } else { $html }
            $mainText = Strip-HtmlText $mainHtml
            $lowerText = $mainText.ToLowerInvariant()

            foreach ($marker in $CaptchaIndicators) {
                if ($lowerText.Contains($marker) -or $lowerHtml.Contains($marker)) {
                    return @{
                        success           = $false
                        status            = "captcha_blocked"
                        code              = $null
                        progress          = Get-ProgressText $html
                        pages_visited     = $step
                        elapsed_ms        = [int]$sw.ElapsedMilliseconds
                        final_url         = $currentUrl
                        receipt_code      = $rcn
                        page_text_excerpt = "检测到人机验证/CAPTCHA，已停止自动操作，请手动完成。"
                    }
                }
            }

            $formM = [regex]::Match($html, '<form[^>]*name=["'']form1["'']([^>]*)>(.*?)</form>', "IgnoreCase,Singleline")
            $built = if ($formM.Success) {
                Build-FormPayload -FormHtml $formM.Groups[2].Value -Receipt $receipt -Comment $Comment -ForceFillText $retrySamePage
            } else {
                @{ has_inputs = $false; pairs = $null }
            }

            if (-not $built.has_inputs) {
                $code = Extract-ValidationCode $html
                $prog = Get-ProgressText $html
                $isUsed = $false
                foreach ($u in $UsedIndicators) {
                    if ($lowerText.Contains($u) -and -not $code) { $isUsed = $true; break }
                }
                $status = if ($isUsed) { "already_used_or_invalid" }
                          elseif ($code -or $prog.Contains("100%") -or $mainText.Contains("感谢") -or $mainText.Contains("验证码")) { "completed" }
                          else { "unknown_terminal" }
                return @{
                    success           = ($status -eq "completed")
                    status            = $status
                    code              = $code
                    progress          = if ($prog) { $prog } elseif ($status -eq "completed") { "100%" } else { "" }
                    pages_visited     = $step
                    elapsed_ms        = [int]$sw.ElapsedMilliseconds
                    final_url         = $currentUrl
                    receipt_code      = $rcn
                    page_text_excerpt = $mainText.Substring(0, [Math]::Min(600, $mainText.Length))
                }
            }

            $prevFp = (Get-ProgressText $html) + "|" + $mainText.Substring(0, [Math]::Min(220, $mainText.Length))
            $formAttrs = Parse-HtmlAttributes $formM.Groups[1].Value
            $postUrl = if ($formAttrs["action"]) { ([Uri]::new([Uri]$currentUrl, $formAttrs["action"])).AbsoluteUri } else { $currentUrl }

            $encodedItems = foreach ($kv in $built.pairs) {
                "$([System.Uri]::EscapeDataString($kv.Key))=$([System.Uri]::EscapeDataString($kv.Value))"
            }
            $formBody = $encodedItems -join "&"
            $content = New-Object System.Net.Http.StringContent($formBody, [System.Text.Encoding]::UTF8, "application/x-www-form-urlencoded")
            $resp = $client.PostAsync($postUrl, $content).Result
            $resp.EnsureSuccessStatusCode() | Out-Null
            $html = $resp.Content.ReadAsStringAsync().Result
            $currentUrl = $resp.RequestMessage.RequestUri.AbsoluteUri

            $nextMainM = [regex]::Match($html, '<main[^>]*>(.*?)</main>', "IgnoreCase,Singleline")
            $nextMainHtml = if ($nextMainM.Success) { $nextMainM.Groups[1].Value } else { $html }
            $nextMainText = Strip-HtmlText $nextMainHtml
            $nextFp = (Get-ProgressText $html) + "|" + $nextMainText.Substring(0, [Math]::Min(220, $nextMainText.Length))
            $hasError = $html -match 'class=["''][^"'']*\berror\b'

            if ($nextFp -eq $prevFp -or $hasError) {
                if ($retrySamePage) {
                    return @{
                        success           = $false
                        status            = "validation_stuck"
                        code              = $null
                        progress          = Get-ProgressText $html
                        pages_visited     = $step
                        elapsed_ms        = [int]$sw.ElapsedMilliseconds
                        final_url         = $currentUrl
                        receipt_code      = $rcn
                        page_text_excerpt = $nextMainText.Substring(0, [Math]::Min(600, $nextMainText.Length))
                    }
                }
                $retrySamePage = $true
            } else {
                $retrySamePage = $false
            }
        }

        return @{
            success           = $false
            status            = "max_pages_exceeded"
            code              = $null
            progress          = ""
            pages_visited     = $MaxPages
            elapsed_ms        = [int]$sw.ElapsedMilliseconds
            final_url         = $currentUrl
            receipt_code      = $rcn
            page_text_excerpt = "超过最大翻页保护上限。"
        }
    }
    finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

function Send-JsonResponse {
    param(
        [System.Net.HttpListenerResponse]$Response,
        [int]$StatusCode,
        [object]$Payload
    )
    $json = $Payload | ConvertTo-Json -Depth 6 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $Response.StatusCode = $StatusCode
    $Response.ContentType = "application/json; charset=utf-8"
    $Response.ContentLength64 = $bytes.Length
    $Response.Headers["Cache-Control"] = "no-store"
    $Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $Response.OutputStream.Close()
}

function Send-StaticFile {
    param(
        [System.Net.HttpListenerResponse]$Response,
        [string]$RelPath
    )
    $cleanRel = $RelPath.TrimStart('/').Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    if (-not $cleanRel) { $cleanRel = "index.html" }
    $fullPath = [System.IO.Path]::GetFullPath((Join-Path $PublicDir $cleanRel))
    $baseFull = [System.IO.Path]::GetFullPath($PublicDir)
    if (-not $fullPath.StartsWith($baseFull, [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path $fullPath -PathType Leaf)) {
        $Response.StatusCode = 404
        $Response.OutputStream.Close()
        return
    }
    $ext = [System.IO.Path]::GetExtension($fullPath).ToLowerInvariant()
    $Response.ContentType = switch ($ext) {
        ".html" { "text/html; charset=utf-8" }
        ".js"   { "application/javascript; charset=utf-8" }
        ".css"  { "text/css; charset=utf-8" }
        ".json" { "application/json; charset=utf-8" }
        default { "application/octet-stream" }
    }
    $bytes = [System.IO.File]::ReadAllBytes($fullPath)
    $Response.StatusCode = 200
    $Response.ContentLength64 = $bytes.Length
    $Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $Response.OutputStream.Close()
}

function Find-AppBrowser {
    $candidates = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Google\Chrome\Application\chrome.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

if ($SolveUrl) {
    $res = Invoke-SolveSurvey -RawInput $SolveUrl
    Write-Output ($res | ConvertTo-Json -Depth 6)
    exit $(if ($res.success) { 0 } else { 1 })
}

if ($Port -eq 0) {
    $tcp = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $tcp.Start()
    $Port = $tcp.LocalEndpoint.Port
    $tcp.Stop()
}

$listener = New-Object System.Net.HttpListener
$prefix = "http://127.0.0.1:$Port/"
$listener.Prefixes.Add($prefix)
$listener.Start()

Write-Host "========================================================"
Write-Host "滨寿司小票问卷助手（Windows 免安装极速版）已启动！"
Write-Host "运行引擎: Windows 内置 PowerShell (.NET) + Edge（无需 Python）"
Write-Host "窗口地址: $prefix"
Write-Host "使用方式: 拖入手机小票照片、Ctrl+V 粘贴截图/链接，或摄像头扫码！"
Write-Host "提示: 关闭此窗口即可退出后台服务。"
Write-Host "========================================================"

if (-not $NoBrowser) {
    $browserExe = Find-AppBrowser
    if ($browserExe) {
        Start-Process -FilePath $browserExe -ArgumentList "--app=$prefix", "--window-size=560,840" | Out-Null
    } else {
        Start-Process $prefix | Out-Null
    }
}

try {
    while ($listener.IsListening) {
        $ctx = $listener.GetContext()
        $req = $ctx.Request
        $res = $ctx.Response
        try {
            $path = $req.Url.AbsolutePath
            if ($req.HttpMethod -eq "GET" -and ($path -eq "/api/health" -or $path -eq "/api/solve")) {
                Send-JsonResponse $res 200 @{
                    ok        = $true
                    mode      = "windows-powershell"
                    opencv_qr = $false
                }
            }
            elseif ($req.HttpMethod -eq "POST" -and $path -eq "/api/solve") {
                $reader = New-Object System.IO.StreamReader($req.InputStream, [System.Text.Encoding]::UTF8)
                $rawBody = $reader.ReadToEnd()
                $data = $rawBody | ConvertFrom-Json
                $target = if ($data.url) { ([string]$data.url).Trim() } else { "" }
                if (-not $target) {
                    Send-JsonResponse $res 400 @{ success = $false; error = "请提供问卷链接或 RCN 小票编号。" }
                } else {
                    $comment = if ($data.comment) { [string]$data.comment } else { $DefaultComment }
                    $out = Invoke-SolveSurvey -RawInput $target -Comment $comment
                    Send-JsonResponse $res 200 $out
                }
            }
            elseif ($req.HttpMethod -eq "GET") {
                Send-StaticFile $res $path
            }
            else {
                $res.StatusCode = 404
                $res.OutputStream.Close()
            }
        }
        catch {
            try {
                Send-JsonResponse $res 500 @{ success = $false; error = $_.Exception.Message }
            } catch {}
        }
    }
}
finally {
    $listener.Stop()
    $listener.Close()
}
