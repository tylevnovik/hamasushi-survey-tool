# 滨寿司满意度问卷自动填写与提取编号工具 (Hamasushi Survey Tool)

面向 **滨寿司中国（`cn-hamasushi.csfeedback.net`）** 顾客满意度问卷的自动化填答工具。

> **这个问卷有什么用？**
> 每次在滨寿司消费拿到的收银小票底部都有一个问卷二维码/链接，手动填写完整套问卷大约有 20 页题目。**填完问卷把生成的验证码写回这张小票上，下次到店消费时就能直接当「立减 10 元（-10 块）」优惠券使用，而且可以和美团、抖音代金券叠加使用**（具体兑换及使用门槛以小票与门店实际说明为准）。

---

## 推荐使用方式

### 方式一：单文件免安装原生程序（Rust / Go 双版本，不用脚本也不用装环境）

提供 **Rust**（约 5.1 MB，源码在 [rust/](rust)）和 **Go**（约 9.5 MB，源码在 [go/](go)）两个单文件版本。网页界面、离线二维码解码库与 HTTP 填答引擎全部打包在**这一个可执行文件里**，**无 Python、无脚本、无外部依赖**：

1. 前往 [Releases 最新版本](https://github.com/tylevnovik/hamasushi-survey-tool/releases/latest) 直接下载对应平台文件（本地编译后也保存在 `dist/`）：
   - Windows（Rust 版，体积更小）：**`hamasushi-tool.exe`**
   - Windows（Go 版）：**`hamasushi-tool-go.exe`**
   - macOS (Apple Silicon) / Linux（Go 跨平台编译版）：**`hamasushi-tool-darwin-arm64`** / **`hamasushi-tool-linux-amd64`**
2. **双击打开**即可弹出独立桌面窗口：
   - **直接拖入照片**：把手机拍的小票照片（微信保存的、截图等）拖进窗口；
   - **Ctrl+V 粘贴**：直接粘贴截图、图片或链接；
   - **打开摄像头扫码**：小票对准摄像头扫一下；
   - **双引擎识别与极速填答**：前端（Edge `BarcodeDetector` + 内置 `jsQR`）与原生后端（Rust `rqrr` / Go `gozxing`）双引擎识别小票二维码，约 4~5 秒完成 20 页问卷并显示超大号验证码。
3. 也支持把小票照片直接拖到 `.exe` 图标上，或命令行运行：
   ```powershell
   hamasushi-tool.exe --solve "小票照片.jpg"
   hamasushi-tool-go.exe --solve "RCN你的小票编号" --json
   hamasushi-tool-go.exe --lan   # 允许同一 Wi-Fi 下手机访问
   ```

---

### 方式二：部署成个人手机 H5 网页（Vercel 一键部署，自带摄像头扫码）

后台为无浏览器依赖的纯 Python HTTP 引擎，适配 **Vercel Serverless Functions**（[api/solve.py](api/solve.py) + [public/index.html](public/index.html)）：

- [![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/tylevnovik/hamasushi-survey-tool)
- 部署完成后你会获得一个免费的专属 `https://xxx.vercel.app` 网址，收藏到手机桌面或微信收藏里即可：
  - 手机打开页面 → 点 **“📷 打开摄像头直接扫码”** 或从相册选小票照片 → **约 4~5 秒直接出验证码**，手机端免装任何插件也不用开电脑。

---

### 方式三：Tampermonkey 油猴脚本（适合手机 Safari / 支持扩展的浏览器）

1. 在浏览器安装 **Tampermonkey（篡改猴）**、**Violentmonkey（暴力猴）** 或 iOS Safari 的 **Userscripts** 扩展。
2. 点击直装链接安装：[点击安装 hamasushi_autofill.user.js](https://github.com/tylevnovik/hamasushi-survey-tool/releases/latest/download/hamasushi_autofill.user.js)
3. 打开任意 `https://cn-hamasushi.csfeedback.net/sv/...` 页面，点击右下角 **“一键自动填答并拿编号”**。

---

### 方式四：免安装 PowerShell 版 & Python 工具

- **Windows PowerShell 免安装版**（[scripts/win_app.ps1](scripts/win_app.ps1)）：下载 Release 中的 `hamasushi-win-noinstall.zip` 解压，或直接双击 [scripts/免安装直接启动.bat](scripts/免安装直接启动.bat)。
- **Python 桌面增强版**（支持本地 OpenCV 多尺度二维码解码）：双击 [scripts/启动拖拽小票工具.bat](scripts/启动拖拽小票工具.bat) 或运行：
  ```powershell
  uv sync --all-extras
  uv run --extra desktop python python/desktop_app.py
  ```
- **Python 命令行**（[python/hamasushi_survey.py](python/hamasushi_survey.py)）：
  ```powershell
  uv run python python/hamasushi_survey.py "https://cn-hamasushi.csfeedback.net/sv/zh/RCN你的小票编号"
  uv run --extra desktop python python/hamasushi_survey.py --qr "C:\path\to\receipt.jpg"
  uv run --all-extras python python/hamasushi_survey.py "RCN你的小票编号" --engine playwright --headless --json-out
  ```

---

## 仓库目录结构

```text
hamasushi-survey-tool/
├── api/                         # Vercel Serverless 后端接口
│   └── solve.py
├── public/                      # 共用 H5 / 桌面端前端页面与离线 jsQR
│   ├── index.html
│   └── vendor/jsQR.js
├── go/                          # Go 单文件实现（根目录下 go.mod / assets.go 供 //go:embed）
├── rust/                        # Rust 单文件实现
├── python/                      # Python CLI、桌面服务、OpenCV 二维码识别与测试
├── scripts/                     # Windows 启动脚本与 PowerShell 免安装版
└── hamasushi_autofill.user.js   # Tampermonkey 油猴脚本
```

---

## 本地构建与测试

```powershell
# Python 单元测试与双二进制/PowerShell/Edge UI 集成测试
uv run python python/test_hamasushi_survey.py

# Go 编译与单测
go vet ./...
go test -v ./...
go build -ldflags="-s -w" -o dist/hamasushi-tool-go.exe ./go

# Rust 编译与单测
cd rust
cargo test
cargo build --release
```

---

## 注意事项与免责声明

- 本工具仅用于个人实际消费后的问卷便捷填答辅助，请勿用于恶意刷取或批量滥用行为。
- 验证码及优惠兑换规则以滨寿司门店与小票实际说明为准。
- 开源协议：[MIT License](LICENSE)（前端内置二维码解码库 `public/vendor/jsQR.js` 基于 Apache-2.0 协议）
