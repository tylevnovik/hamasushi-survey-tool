package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"

	assets "github.com/tylevnovik/hamasushi-survey-tool"
)

const maxUploadBytes = 25 * 1024 * 1024

type solveURLReq struct {
	URL     string `json:"url"`
	Comment string `json:"comment"`
}

type solveImageReq struct {
	ImageBase64 string `json:"image_base64"`
	Comment     string `json:"comment"`
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func serveEmbeddedAsset(w http.ResponseWriter, assetPath, contentType string) {
	data, err := assets.PublicFS.ReadFile(assetPath)
	if err != nil {
		http.Error(w, "Not Found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", contentType)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}

func handleSolveURL(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, maxUploadBytes))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": "读取请求体失败"})
		return
	}
	var req solveURLReq
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": "无效的 JSON 请求格式。"})
		return
	}
	if strings.TrimSpace(req.URL) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": "请提供问卷链接或 RCN 小票编号。"})
		return
	}
	res, err := solveSurveyHTTP(req.URL, req.Comment, 35)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, res)
}

func handleSolveImage(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, maxUploadBytes+1))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": "读取图片失败"})
		return
	}
	if len(body) > maxUploadBytes {
		writeJSON(w, http.StatusRequestEntityTooLarge, map[string]any{"success": false, "error": "图片文件过大（上限 25MB）。"})
		return
	}

	var imageBytes []byte
	var comment string
	ct := strings.ToLower(r.Header.Get("Content-Type"))
	if strings.Contains(ct, "application/json") {
		var req solveImageReq
		if err := json.Unmarshal(body, &req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": "无效的图片 JSON 请求。"})
			return
		}
		b64 := strings.TrimSpace(req.ImageBase64)
		if strings.HasPrefix(b64, "data:") {
			if idx := strings.IndexByte(b64, ','); idx >= 0 {
				b64 = b64[idx+1:]
			}
		}
		if b64 == "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": "缺少图片数据。"})
			return
		}
		decodedBytes, err := base64.StdEncoding.DecodeString(b64)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": fmt.Sprintf("Base64 解码失败: %v", err)})
			return
		}
		imageBytes = decodedBytes
		comment = req.Comment
	} else if len(body) > 0 {
		imageBytes = body
	} else {
		writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": "上传图片内容为空。"})
		return
	}

	candidates, err := decodeQRFromBytes(imageBytes)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": err.Error()})
		return
	}
	decodedURL := pickSurveyPayload(candidates)
	if decodedURL == "" {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]any{
			"success": false,
			"error":   "未能从照片中识别出二维码，请尝试对准二维码裁切或拍清晰后再拖入。",
		})
		return
	}

	res, err := solveSurveyHTTP(decodedURL, comment, 35)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"success": false, "error": err.Error()})
		return
	}
	rawJSON, _ := json.Marshal(res)
	var outMap map[string]any
	_ = json.Unmarshal(rawJSON, &outMap)
	outMap["decoded_qr"] = decodedURL
	writeJSON(w, http.StatusOK, outMap)
}

func newServerMux() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
			return
		}
		if r.URL.Path == "/" || r.URL.Path == "/index.html" {
			serveEmbeddedAsset(w, "public/index.html", "text/html; charset=utf-8")
			return
		}
		http.NotFound(w, r)
	})
	mux.HandleFunc("/vendor/jsQR.js", func(w http.ResponseWriter, r *http.Request) {
		serveEmbeddedAsset(w, "public/vendor/jsQR.js", "application/javascript; charset=utf-8")
	})
	mux.HandleFunc("/api/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":         true,
			"mode":       "go-native",
			"opencv_qr":  true,
			"mode_label": "Go 单文件零依赖模式：内置 Edge + Go 原生 ZXing QR 双引擎识别",
		})
	})
	mux.HandleFunc("/api/solve", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			writeJSON(w, http.StatusOK, map[string]any{
				"ok":         true,
				"mode":       "go-native",
				"opencv_qr":  true,
				"mode_label": "Go 单文件零依赖模式：内置 Edge + Go 原生 ZXing QR 双引擎识别",
			})
		case http.MethodPost:
			handleSolveURL(w, r)
		default:
			http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		}
	})
	mux.HandleFunc("/api/solve-image", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
			return
		}
		handleSolveImage(w, r)
	})
	return mux
}

func findAppBrowser() string {
	candidates := []string{
		`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
		`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
		`C:\Program Files\Google\Chrome\Application\chrome.exe`,
	}
	for _, p := range candidates {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return ""
}

func launchDesktopWindow(targetURL string) {
	if exe := findAppBrowser(); exe != "" {
		cmd := exec.Command(exe, "--app="+targetURL, "--window-size=560,840")
		if cmd.Start() == nil {
			return
		}
	}
	_ = exec.Command("cmd", "/c", "start", "", targetURL).Start()
}

func localLANIP() string {
	conn, err := net.Dial("udp", "8.8.8.8:80")
	if err != nil {
		return "127.0.0.1"
	}
	defer conn.Close()
	if addr, ok := conn.LocalAddr().(*net.UDPAddr); ok {
		return addr.IP.String()
	}
	return "127.0.0.1"
}

func runServer(port int, lan, noBrowser bool) error {
	host := "127.0.0.1"
	if lan {
		host = "0.0.0.0"
	}
	ln, err := net.Listen("tcp", fmt.Sprintf("%s:%d", host, port))
	if err != nil {
		return fmt.Errorf("无法绑定端口: %w", err)
	}
	actualPort := ln.Addr().(*net.TCPAddr).Port
	localURL := fmt.Sprintf("http://127.0.0.1:%d/", actualPort)

	fmt.Println("========================================================")
	fmt.Println("滨寿司小票问卷助手（Go 单文件零依赖版）已启动！")
	fmt.Printf("窗口地址: %s\n", localURL)
	if lan {
		fmt.Printf("同局域网手机地址: http://%s:%d/\n", localLANIP(), actualPort)
	}
	fmt.Println("使用方式: 直接把手机小票照片拖入窗口，或 Ctrl+V 粘贴图片/链接！")
	fmt.Println("提示: 关闭此窗口即可退出后台服务。")
	fmt.Println("========================================================")

	if !noBrowser {
		go func() {
			time.Sleep(250 * time.Millisecond)
			launchDesktopWindow(localURL)
		}()
	}

	srv := &http.Server{Handler: newServerMux()}
	return srv.Serve(ln)
}
