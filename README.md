# 滨寿司满意度问卷自动填写与提取编号工具 (Hamasushi Survey Tool)

面向 **滨寿司中国（`cn-hamasushi.csfeedback.net`）** 顾客满意度问卷的自动化填答工具，提供 **Tampermonkey 油猴浏览器脚本** 与 **Python Playwright 命令行工具** 两种使用方式，可自动补全小票信息、快速完成问卷并提取最终优惠验证码。

---

## 功能特性

- **RCN 小票编号自动解析**：自动识别 `RCN` 小票编号中的到店日期（月/日）、时间（时/分）与 4 位店铺编号，重点补全页面未自动选中的“分钟”下拉框，并勾选 `同意本规程约定`。
- **智能正向作答策略**：通过关键词优先级评分，自动选择 `10 非常推荐`、`非常满意`、`满意`、`肯定会`、`愿意` 等好评选项；遇到寿司饭大小/口感等双向尺度题时会自动选择 `正好`，避免误选投诉项。
- **隐藏表单控件兼容**：直接触发关联 `<label>` 点击与 `change` 事件，完整兼容原站隐藏原生 `<input>` 的表单交互逻辑。
- **跨页自动推进与防卡死保护**：选填文本框默认留空；若触发必填校验或表单未前进，最多补全简短好评重试一次，避免死循环。
- **人机验证保护**：如遇 CAPTCHA / 人机验证，立即停止自动操作并提示手动处理，不进行任何绕过行为。
- **结果自动提取**：在 `100%` 完成页自动提取验证码（如 `Sxxxx`），油猴脚本支持一键复制，Python 工具支持保存完整页面截图与 JSON 结果。

---

## 方式一：Tampermonkey 油猴脚本（推荐日常使用）

### 直接安装（GitHub Release）

1. 先在浏览器（Edge / Chrome / Firefox）安装 **Tampermonkey（篡改猴）** 或 **Violentmonkey（暴力猴）** 扩展。
2. 点击以下链接即可直接弹出油猴安装确认页：
   - **Release 直装链接（自动跟随最新版本）**：
     [点击安装 hamasushi_autofill.user.js](https://github.com/tylevnovik/hamasushi-survey-tool/releases/latest/download/hamasushi_autofill.user.js)
   - **Release 发布页**：
     [查看 Releases](https://github.com/tylevnovik/hamasushi-survey-tool/releases/latest)
   - **Raw 直链备用**：
     [raw.githubusercontent.com 安装链接](https://raw.githubusercontent.com/tylevnovik/hamasushi-survey-tool/main/hamasushi_autofill.user.js)

### 使用方法

打开任意滨寿司问卷链接（`https://cn-hamasushi.csfeedback.net/sv/...`）后，页面右下角会出现 **“滨寿司问卷助手”** 悬浮面板：

- **一键自动填答并拿编号**：自动补全小票时间并勾选协议，通过 `sessionStorage` 跨页面保持自动填答状态直至 `100%`，完成后在面板高亮显示验证码并提供 **复制编号** 按钮。
- **仅填充本页**：只帮当前页面选好选项，适合需要人工核对再翻页的场景。
- **停止自动**：随时中止自动翻页。

源码位置：[hamasushi_autofill.user.js](hamasushi_autofill.user.js)

---

## 方式二：Python Playwright 命令行工具

适合批量处理、无头运行或保存完成页截图留档。源码位置：[hamasushi_survey.py](hamasushi_survey.py)

### 环境准备

推荐使用 [`uv`](https://docs.astral.sh/uv/)（需要 Python `>= 3.10`）：

```powershell
git clone https://github.com/tylevnovik/hamasushi-survey-tool.git
cd hamasushi-survey-tool
uv sync
```

> 默认优先调用本机已安装的 **Microsoft Edge** 或 **Google Chrome**，无需额外下载 Chromium；如果本机两者均未安装，可执行 `uv run playwright install chromium`。

### 运行示例

```powershell
# 传入完整问卷链接（默认显示浏览器窗口）
uv run python hamasushi_survey.py "https://cn-hamasushi.csfeedback.net/sv/zh/RCN你的小票编号"

# 只传 RCN 小票编号 + 无头模式 + 导出 JSON 结果
uv run python hamasushi_survey.py "RCN你的小票编号" --headless --json-out

# 不带参数运行（交互式输入链接或编号）
uv run python hamasushi_survey.py
```

### 常用命令行参数

| 参数 | 说明 |
| --- | --- |
| `url` | 问卷完整 URL 或 `RCN...` 小票编号（可选，不传则运行后提示输入） |
| `--headless` | 使用无头模式运行，不弹出浏览器窗口 |
| `--delay 0.2` | 每页点击下一步前的等待秒数（默认 `0.2`） |
| `--pause-before-submit` | 到达 `>= 95%` 提交前暂停等待人工确认 |
| `--comment "..."` | 遇到必填文本题时填写的评价内容 |
| `--output-dir output` | 完成页截图与 `latest_result.json` 保存目录（默认 `output`） |
| `--json-out` | 额外将执行结果导出为 `latest_result.json` |
| `--browser msedge\|chrome\|chromium` | 指定使用的浏览器内核 |
| `--shop` / `--month` / `--day` / `--hour` / `--minute` | 手动覆盖从 `RCN` 编号解析出的门店号或到店时间 |

---

## 本地测试

运行单元测试（包含 `RCN` 编号解析、选项评分优先级及完成页提取验证）：

```powershell
uv run python test_hamasushi_survey.py
```

校验油猴脚本语法（需安装 Node.js）：

```powershell
node --check hamasushi_autofill.user.js
```

---

## 注意事项与免责声明

- 本工具仅用于个人实际消费后的问卷便捷填答辅助，请勿用于恶意刷取或批量滥用行为。
- 验证码及优惠兑换规则以滨寿司门店与小票实际说明为准。
- 开源协议：[MIT License](LICENSE)
