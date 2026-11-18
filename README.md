# 滨寿司满意度问卷自动填写与提取编号工具 (Hamasushi Survey Tool)

面向 **滨寿司中国（`cn-hamasushi.csfeedback.net`）** 顾客满意度问卷的自动化填答工具。

> **这个问卷有什么用？**
> 每次在滨寿司消费拿到的收银小票底部都有一个问卷二维码/链接，手动填写完整套问卷大约有 20 页题目。**填完问卷把生成的验证码写回这张小票上，下次到店消费时就能直接当「立减 10 元（-10 块）」优惠券使用，而且可以和美团、抖音代金券叠加使用**（具体兑换及使用门槛以小票与门店实际说明为准）。

---

## 推荐使用方式（支持单文件 `.exe` 零依赖直接运行）

### 方式一：单文件 Rust 原生可执行程序 `hamasushi-tool.exe`（最推荐，不用脚本也不用装任何环境）

使用 Rust 编译生成单个独立 Windows 可执行文件（约 5 MB，源码位于 [rust/](rust)），网页界面、离线二维码解码库与 HTTP 填答引擎全部打包在一个 `.exe` 里，**无 Python、无脚本、无外部依赖**：

1. 前往 [Releases 最新版本](https://github.com/tylevnovik/hamasushi-survey-tool/releases/latest) 直接下载 **`hamasushi-tool.exe`**。
2. **双击打开**即可弹出独立桌面窗口：
   - **直接拖入照片**：把手机拍的小票照片（微信保存的、截图等）拖进窗口；
   - **Ctrl+V 粘贴**：直接粘贴截图、图片或链接；
   - **打开摄像头扫码**：小票对准摄像头扫一下；
   - **三层识别与极速填答**：前端（Edge `BarcodeDetector` + 内置 `jsQR`）与 Rust 原生 `rqrr` 后端双引擎识别小票二维码，约 4~5 秒完成 20 页问卷并显示超大号验证码。
3. 也支持把小票照片直接拖到 `hamasushi-tool.exe` 图标上，或命令行使用：
   ```powershell
   hamasushi-tool.exe --solve "小票照片.jpg"
   hamasushi-tool.exe --solve "RCN你的小票编号" --json
   hamasushi-tool.exe --lan   # 允许同一 Wi-Fi 下手机访问
   ```

> 备选：如果你不想下载编译好的 `.exe`，也可下载 Release 中的 **`hamasushi-win-noinstall.zip`** 解压双击 `免安装直接启动.bat`（纯 Windows 自带 PowerShell），或用 Python 桌面增强版（`启动拖拽小票工具.bat`，支持 OpenCV 解码）。

---

### 方式二：部署成个人手机 H5 网页（Vercel 一键部署，自带摄像头扫码）

后台已改造为无浏览器依赖的纯 Python HTTP 引擎，完整适配 **Vercel Serverless Functions**（[api/solve.py](api/solve.py) + [public/index.html](public/index.html)）：

- [![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/tylevnovik/hamasushi-survey-tool)
- 部署完成后你会获得一个免费的专属 `https://xxx.vercel.app` 网址，收藏到手机桌面或微信收藏里即可：
  - 手机打开页面 → 点 **“📷 打开摄像头直接扫码”** 或直接从相册选小票照片 → **3~5 秒直接出验证码**，手机端免装任何插件也不用开电脑。

---

## 其他使用方式

### 方式三：Tampermonkey 油猴脚本（适合手机 Safari / 支持扩展的浏览器）

1. 先在浏览器安装 **Tampermonkey（篡改猴）**、**Violentmonkey（暴力猴）** 或 iOS Safari 的 **Userscripts** 扩展。
2. 点击直装链接即可安装：
   - **Release 直装链接（跟随最新版本）**：
     [点击安装 hamasushi_autofill.user.js](https://github.com/tylevnovik/hamasushi-survey-tool/releases/latest/download/hamasushi_autofill.user.js)
   - **Release 发布页**：
     [查看 Releases](https://github.com/tylevnovik/hamasushi-survey-tool/releases/latest)
3. 打开任意 `https://cn-hamasushi.csfeedback.net/sv/...` 页面，点击右下角 **“一键自动填答并拿编号”**。

### 方式四：Python 命令行工具（[hamasushi_survey.py](hamasushi_survey.py)）

默认使用极速 HTTP 引擎（约 3~5 秒），也支持直接传入小票照片或切换 Playwright 浏览器引擎截图：

```powershell
# 极速模式（支持完整 URL、RCN 编号，或直接传小票照片路径）
uv run python hamasushi_survey.py "https://cn-hamasushi.csfeedback.net/sv/zh/RCN你的小票编号"
uv run --extra desktop python hamasushi_survey.py --qr "C:\path\to\receipt.jpg"

# Playwright 完整浏览器模式（可保存完成页截图或在 95% 提交前人工确认）
uv run --all-extras python hamasushi_survey.py "RCN你的小票编号" --engine playwright --headless --json-out
```

---

## 核心技术与策略说明

- **RCN 小票编号自动解析**（[survey_common.py](survey_common.py)）：自动解析到店月/日、时/分与 4 位店铺编号，补全原网页未自动预填的“分钟”字段（`visit_hour`）并勾选 `同意本规程约定`。
- **智能正向作答策略**：优先选择 `10 非常推荐`、`非常满意`、`满意`、`肯定会`、`愿意`；遇寿司饭大小/口感等双向尺度题会自动选 `正好`；选填文本框默认留空，仅在出现必填校验提示时自动补全简短好评并重试一次。
- **人机验证保护**：如遇 CAPTCHA / 人机验证，立即停止自动操作并提示手动处理，不进行任何绕过行为。

---

## 本地开发与测试

```powershell
# 安装完整桌面与浏览器测试依赖
uv sync --all-extras

# 运行单元测试、OpenCV 合成小票扫码测试、桌面服务接口及真实浏览器 UI 测试
uv run python test_hamasushi_survey.py
```

---

## 注意事项与免责声明

- 本工具仅用于个人实际消费后的问卷便捷填答辅助，请勿用于恶意刷取或批量滥用行为。
- 验证码及优惠兑换规则以滨寿司门店与小票实际说明为准。
- 开源协议：[MIT License](LICENSE)（前端内置二维码解码库 `public/vendor/jsQR.js` 基于 Apache-2.0 协议）
