// ==UserScript==
// @name         滨寿司问卷自动填写助手 (Hamasushi Survey AutoFill)
// @namespace    https://github.com/tylevnovik/hamasushi-survey-tool
// @version      1.2.0
// @description  自动填写滨寿司满意度问卷：补全小票时间分钟、优先好评作答、自动翻页并提取复制验证码
// @author       tylevnovik
// @license      MIT
// @homepageURL  https://github.com/tylevnovik/hamasushi-survey-tool
// @supportURL   https://github.com/tylevnovik/hamasushi-survey-tool/issues
// @downloadURL  https://github.com/tylevnovik/hamasushi-survey-tool/releases/latest/download/hamasushi_autofill.user.js
// @updateURL    https://github.com/tylevnovik/hamasushi-survey-tool/releases/latest/download/hamasushi_autofill.user.js
// @match        https://cn-hamasushi.csfeedback.net/sv/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  const STORAGE_ACTIVE = "hama_autofill_active";
  const STORAGE_LAST_FP = "hama_autofill_last_fp";
  const STORAGE_RETRY = "hama_autofill_retry";
  const DEFAULT_COMMENT = "菜品新鲜美味，服务态度很好，整体体验非常满意。";

  const CAPTCHA_KEYWORDS = [
    "cf-turnstile",
    "g-recaptcha",
    "h-captcha",
    "recaptcha",
    "turnstile",
    "hcaptcha",
    "verify you are human",
    "请完成安全验证",
    "人机验证",
  ];

  const EXACT_SCORES = {
    "是": 35,
    on: 35,
    "否": -60,
    "无": -50,
    "没有": -90,
    "其他": -20,
  };

  const SCORE_RULES = [
    ["10\u3000非常推荐", 120],
    ["非常推荐", 115],
    ["非常满意", 110],
    ["十分满意", 110],
    ["满意", 100],
    ["肯定会", 95],
    ["愿意", 90],
    ["正好", 90],
    ["合适", 85],
    ["中国", 75],
    ["堂食", 70],
    ["几乎每天", 65],
    ["大约每周", 60],
    ["技术人员", 55],
    ["20～29岁", 55],
    ["20-29", 55],
    ["男性", 50],
    ["一个人", 50],
    ["6,000", 45],
    ["金枪鱼", 45],
    ["还可以", 40],
    ["应该会", 40],
    ["不太满意", -100],
    ["不满意", -110],
    ["完全不推荐", -120],
    ["不愿意", -90],
    ["不确定", -40],
    ["不会", -90],
    ["不好说", -40],
    ["太大", -60],
    ["稍大", -30],
    ["稍小", -30],
    ["太小", -60],
    ["太硬", -60],
    ["稍硬", -30],
    ["稍软", -30],
    ["太软", -60],
    ["不想回复", -80],
    ["未接受服务", -80],
    ["以上都不是", -100],
  ];

  function parseReceipt(code) {
    const m = (code || "").trim().match(/^RCN(\d{2})(\d{2})(\d{2})(\d{2})\d{4}(\d{4})\d+$/i);
    if (!m) return null;
    return {
      month: parseInt(m[1], 10),
      day: parseInt(m[2], 10),
      hour: parseInt(m[3], 10),
      minute: parseInt(m[4], 10),
      shopCode: m[5],
    };
  }

  function scoreOptionText(raw) {
    const normalized = (raw || "").replace(/\s+/g, " ").trim();
    if (!normalized) return -10;
    const lowered = normalized.toLowerCase();
    if (Object.prototype.hasOwnProperty.call(EXACT_SCORES, lowered)) {
      return EXACT_SCORES[lowered];
    }
    let best = 0;
    for (const [kw, weight] of SCORE_RULES) {
      if (lowered.includes(kw.toLowerCase())) {
        if (weight < 0) return weight;
        if (weight > best) best = weight;
      }
    }
    if (normalized.startsWith("10")) best = Math.max(best, 120);
    return best;
  }

  function detectCaptcha() {
    const text = (document.body?.innerText || "").toLowerCase();
    const html = (document.documentElement?.innerHTML || "").toLowerCase();
    return CAPTCHA_KEYWORDS.some((kw) => text.includes(kw) || html.includes(kw));
  }

  function getProgressText() {
    return document.querySelector(".progress_bar")?.textContent?.trim() || "";
  }

  function getAdvanceButton() {
    const selectors = [
      'form[name="form1"] .btn_wrap a.btn',
      'form[name="form1"] a.btn.type02',
      'form[name="form1"] a.btn.type03',
      'form[name="form1"] button[type="submit"]',
      'form[name="form1"] input[type="submit"]',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function hasQuestionInputs() {
    return Boolean(
      document.querySelector(
        'form[name="form1"] input[type="radio"], ' +
          'form[name="form1"] input[type="checkbox"], ' +
          'form[name="form1"] select, ' +
          'form[name="form1"] textarea, ' +
          'form[name="form1"] input[type="text"]'
      )
    );
  }

  function resolveLabelForInput(input) {
    if (input.id) {
      const label = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
      if (label) return { label, text: label.textContent || "" };
    }
    const parentLabel = input.closest("label");
    if (parentLabel) return { label: parentLabel, text: parentLabel.textContent || "" };
    const parentLi = input.closest("li");
    return { label: null, text: parentLi ? parentLi.textContent || "" : "" };
  }

  function clickInputControl(input, label) {
    if (label) {
      label.click();
      return;
    }
    input.checked = true;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.click();
  }

  function setSelectValue(sel, value) {
    if (!sel || value === null || value === undefined) return false;
    const strVal = String(value);
    const exists = Array.from(sel.options).some((o) => o.value === strVal);
    if (!exists) return false;
    sel.value = strVal;
    sel.dispatchEvent(new Event("input", { bubbles: true }));
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function fillReceiptPage() {
    const receiptInput = document.querySelector('input[name="receipt_code"]');
    const urlSeg = location.pathname.split("/").filter(Boolean).pop() || "";
    const code = receiptInput?.value?.trim() || (urlSeg.toUpperCase().startsWith("RCN") ? urlSeg : "");
    if (receiptInput && !receiptInput.value.trim() && code) {
      receiptInput.value = code;
    }
    const parsed = parseReceipt(code);

    const shopInput = document.querySelector('input[name="shop_code"]');
    if (shopInput && !shopInput.value.trim() && parsed?.shopCode) {
      shopInput.value = parsed.shopCode;
    }

    const targets = [
      ['select[name="month"]', parsed?.month],
      ['select[name="day"]', parsed?.day],
      ["#select_visit_hour", parsed?.hour],
      ["#select_visit_minute", parsed?.minute],
    ];
    for (const [selQuery, val] of targets) {
      const sel = document.querySelector(selQuery);
      if (!sel || sel.value.trim() !== "") continue;
      if (!setSelectValue(sel, val) && sel.options.length > 0) {
        setSelectValue(sel, sel.options[0].value);
      }
    }

    const agree = document.querySelector('input[name="agree"], #check01');
    if (agree && !agree.checked) {
      const { label } = resolveLabelForInput(agree);
      clickInputControl(agree, label);
    }
  }

  function groupByName(nodes) {
    const groups = new Map();
    nodes.forEach((node, idx) => {
      const key = node.getAttribute("name") || `__unnamed_${idx}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(node);
    });
    return groups;
  }

  function fillQuestionPage(forceFillText) {
    const form = document.querySelector('form[name="form1"]');
    if (!form) return;

    const radios = Array.from(form.querySelectorAll('input[type="radio"]'));
    for (const items of groupByName(radios).values()) {
      if (items.some((r) => r.checked)) continue;
      let bestScore = -10000;
      let best = null;
      for (const r of items) {
        const { label, text } = resolveLabelForInput(r);
        const s = scoreOptionText(text);
        if (s > bestScore) {
          bestScore = s;
          best = { r, label };
        }
      }
      if (best) clickInputControl(best.r, best.label);
    }

    const checkboxes = Array.from(form.querySelectorAll('input[type="checkbox"]'));
    for (const items of groupByName(checkboxes).values()) {
      if (items.some((c) => c.checked)) continue;
      const scored = items
        .map((c) => {
          const { label, text } = resolveLabelForInput(c);
          return { s: scoreOptionText(text), c, label };
        })
        .sort((a, b) => b.s - a.s);
      if (scored.length > 0) clickInputControl(scored[0].c, scored[0].label);
    }

    for (const sel of form.querySelectorAll("select")) {
      if (sel.value.trim() !== "") continue;
      const opts = Array.from(sel.options).filter((o) => o.value.trim() !== "");
      if (opts.length === 0) continue;
      opts.sort((a, b) => scoreOptionText(b.textContent) - scoreOptionText(a.textContent));
      setSelectValue(sel, opts[0].value);
    }

    for (const slider of form.querySelectorAll('input[type="range"]')) {
      slider.value = slider.max || "10";
      slider.dispatchEvent(new Event("input", { bubbles: true }));
      slider.dispatchEvent(new Event("change", { bubbles: true }));
    }

    if (forceFillText) {
      for (const ta of form.querySelectorAll("textarea, input[type='text']")) {
        if (!ta.value.trim()) {
          ta.value = DEFAULT_COMMENT;
          ta.dispatchEvent(new Event("input", { bubbles: true }));
        }
      }
    }
  }

  function extractCode() {
    const main = document.querySelector("#main") || document.body;
    const text = main?.innerText || "";
    const patterns = [
      /(?:验证码|调查编号|确认编号|兑换码|编号|Code)[：:\s]*([A-Za-z0-9-]{4,24})/i,
      /(?:NO\.?|Number)[：:\s]*([A-Za-z0-9-]{4,24})/i,
    ];
    for (const pat of patterns) {
      const m = text.match(pat);
      if (m) return m[1].trim();
    }
    return null;
  }

  function pageFingerprint() {
    const progress = getProgressText();
    const names = Array.from(document.querySelectorAll('form[name="form1"] [name^="q_"]'))
      .map((el) => el.getAttribute("name"))
      .join(",");
    const snippet = (document.querySelector("#main")?.innerText || "").slice(0, 200);
    return `${progress}|${names}|${snippet}`;
  }

  function stopAutoRun() {
    sessionStorage.setItem(STORAGE_ACTIVE, "0");
    sessionStorage.removeItem(STORAGE_LAST_FP);
    sessionStorage.removeItem(STORAGE_RETRY);
  }

  function createPanel() {
    const panel = document.createElement("div");
    panel.id = "hama-autofill-panel";
    panel.style.cssText = [
      "position:fixed",
      "right:16px",
      "bottom:16px",
      "z-index:99999",
      "width:268px",
      "padding:12px 14px",
      "border-radius:10px",
      "background:#0f2933",
      "color:#f5fafa",
      "font-family:system-ui,-apple-system,sans-serif",
      "font-size:13px",
      "box-shadow:0 8px 24px rgba(0,0,0,0.28)",
      "border:1px solid #17bdc6",
    ].join(";");

    panel.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <strong style="font-size:14px;color:#52e3ea;">滨寿司问卷助手</strong>
        <span id="hama-progress-badge" style="font-size:12px;background:#164654;padding:2px 7px;border-radius:999px;">-</span>
      </div>
      <div id="hama-status" style="font-size:12px;line-height:1.45;color:#cde7ea;margin-bottom:10px;">就绪</div>
      <div id="hama-code-box" style="display:none;margin-bottom:10px;padding:8px;border-radius:6px;background:#173d47;text-align:center;">
        <div style="font-size:11px;color:#9ed6dc;">提取到验证码</div>
        <div id="hama-code-val" style="font-size:20px;font-weight:700;letter-spacing:1px;color:#ffd24c;margin:4px 0;"></div>
        <button id="hama-copy-btn" type="button" style="cursor:pointer;border:none;border-radius:5px;padding:4px 10px;background:#ffd24c;color:#1b1b1b;font-weight:600;font-size:12px;">复制编号</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;">
        <button id="hama-auto-btn" type="button" style="cursor:pointer;border:none;border-radius:6px;padding:8px;background:#17bdc6;color:#fff;font-weight:600;">一键自动填答并拿编号</button>
        <div style="display:flex;gap:6px;">
          <button id="hama-once-btn" type="button" style="flex:1;cursor:pointer;border:1px solid #357684;border-radius:6px;padding:6px;background:#153844;color:#e5f7f9;font-size:12px;">仅填充本页</button>
          <button id="hama-stop-btn" type="button" style="flex:1;cursor:pointer;border:1px solid #8a4b4b;border-radius:6px;padding:6px;background:#3a1f24;color:#ffd5d5;font-size:12px;">停止自动</button>
        </div>
      </div>
    `;
    document.body.appendChild(panel);
    return panel;
  }

  function updateStatus(text) {
    const el = document.getElementById("hama-status");
    if (el) el.textContent = text;
    const badge = document.getElementById("hama-progress-badge");
    if (badge) badge.textContent = getProgressText() || "0%";
  }

  function showCode(code) {
    const box = document.getElementById("hama-code-box");
    const val = document.getElementById("hama-code-val");
    if (box && val) {
      val.textContent = code;
      box.style.display = "block";
    }
  }

  function runCurrentPage(autoAdvance) {
    if (detectCaptcha()) {
      stopAutoRun();
      updateStatus("检测到人机验证，已暂停自动填写，请手动处理。");
      return;
    }

    const advanceBtn = getAdvanceButton();
    if (!advanceBtn || !hasQuestionInputs()) {
      stopAutoRun();
      const code = extractCode();
      if (code) {
        showCode(code);
        updateStatus("问卷已完成！已自动提取验证码。");
      } else {
        updateStatus("当前为结束或提示页，请检查页面内容。");
      }
      return;
    }

    const fp = pageFingerprint();
    const lastFp = sessionStorage.getItem(STORAGE_LAST_FP) || "";
    const hasError = Boolean(document.querySelector("tr.error, .error"));
    let retryCount = parseInt(sessionStorage.getItem(STORAGE_RETRY) || "0", 10);

    if (autoAdvance && fp === lastFp) {
      if (retryCount >= 1) {
        stopAutoRun();
        updateStatus("页面未推进（可能存在未识别的必填项），已停止，请手动检查。");
        return;
      }
      retryCount += 1;
      sessionStorage.setItem(STORAGE_RETRY, String(retryCount));
    } else if (fp !== lastFp) {
      retryCount = 0;
      sessionStorage.setItem(STORAGE_RETRY, "0");
    }

    const forceFillText = hasError || retryCount > 0;
    if (document.querySelector('input[name="shop_code"], input[name="receipt_code"]')) {
      fillReceiptPage();
    } else {
      fillQuestionPage(forceFillText);
    }

    if (!autoAdvance) {
      updateStatus("已完成本页填充，请确认后点击页面按钮继续。");
      return;
    }

    sessionStorage.setItem(STORAGE_LAST_FP, fp);
    updateStatus("正在自动填充并进入下一页...");
    setTimeout(() => {
      if (sessionStorage.getItem(STORAGE_ACTIVE) === "1") {
        advanceBtn.click();
      }
    }, 220);
  }

  createPanel();
  updateStatus(sessionStorage.getItem(STORAGE_ACTIVE) === "1" ? "自动填答进行中..." : "点击下方按钮开始");

  document.getElementById("hama-auto-btn")?.addEventListener("click", () => {
    sessionStorage.setItem(STORAGE_ACTIVE, "1");
    sessionStorage.removeItem(STORAGE_LAST_FP);
    sessionStorage.setItem(STORAGE_RETRY, "0");
    runCurrentPage(true);
  });

  document.getElementById("hama-once-btn")?.addEventListener("click", () => {
    stopAutoRun();
    runCurrentPage(false);
  });

  document.getElementById("hama-stop-btn")?.addEventListener("click", () => {
    stopAutoRun();
    updateStatus("已停止自动推进。");
  });

  document.getElementById("hama-copy-btn")?.addEventListener("click", async () => {
    const code = document.getElementById("hama-code-val")?.textContent || "";
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      updateStatus(`已复制验证码: ${code}`);
    } catch {
      updateStatus(`请手动复制验证码: ${code}`);
    }
  });

  if (sessionStorage.getItem(STORAGE_ACTIVE) === "1") {
    setTimeout(() => runCurrentPage(true), 250);
  } else {
    const code = extractCode();
    if (code && !getAdvanceButton()) {
      showCode(code);
      updateStatus("问卷已完成！已提取验证码。");
    }
  }
})();
