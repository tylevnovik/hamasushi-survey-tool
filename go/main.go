package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func isImagePath(arg string) bool {
	info, err := os.Stat(arg)
	if err != nil || info.IsDir() {
		return false
	}
	ext := strings.ToLower(filepath.Ext(arg))
	switch ext {
	case ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif":
		return true
	default:
		return false
	}
}

func waitEnterIfNeeded(pause bool) {
	if !pause {
		return
	}
	fmt.Print("按回车键退出...")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
}

func solveDirect(target string, jsonOutput, pauseOnExit bool) int {
	resolved := target
	if isImagePath(target) {
		bytes, err := os.ReadFile(target)
		if err != nil {
			fmt.Fprintf(os.Stderr, "读取图片失败: %v\n", err)
			waitEnterIfNeeded(pauseOnExit && !jsonOutput)
			return 1
		}
		candidates, err := decodeQRFromBytes(bytes)
		if err != nil {
			fmt.Fprintf(os.Stderr, "%v\n", err)
			waitEnterIfNeeded(pauseOnExit && !jsonOutput)
			return 1
		}
		resolved = pickSurveyPayload(candidates)
		if resolved == "" {
			fmt.Fprintf(os.Stderr, "未能从图片中识别出二维码: %s\n", target)
			waitEnterIfNeeded(pauseOnExit && !jsonOutput)
			return 1
		}
		if !jsonOutput {
			fmt.Printf("[二维码识别] %s\n", resolved)
		}
	}

	res, err := solveSurveyHTTP(resolved, "", 35)
	if err != nil {
		fmt.Fprintf(os.Stderr, "执行失败: %v\n", err)
		waitEnterIfNeeded(pauseOnExit && !jsonOutput)
		return 1
	}

	if jsonOutput {
		out, _ := json.MarshalIndent(res, "", "  ")
		fmt.Println(string(out))
	} else {
		fmt.Println("========================================================")
		fmt.Printf("运行状态: %s (成功: %v)\n", res.Status, res.Success)
		fmt.Printf("最终进度: %s | 共经历页面: %d | 耗时: %d ms\n", res.Progress, res.PagesVisited, res.ElapsedMs)
		if res.Code != "" {
			fmt.Printf("提取到的验证码: %s\n", res.Code)
		}
		fmt.Println("--------------------------------------------------------")
		fmt.Println(res.PageTextExcerpt)
		fmt.Println("========================================================")
	}
	waitEnterIfNeeded(pauseOnExit && !jsonOutput)
	if res.Success {
		return 0
	}
	return 1
}

func main() {
	port := flag.Int("port", 0, "指定本地服务端口（默认自动分配）")
	lan := flag.Bool("lan", false, "允许同一局域网手机访问")
	noBrowser := flag.Bool("no-browser", false, "仅启动服务，不自动打开窗口")
	solveFlag := flag.String("solve", "", "命令行直跑指定链接/编号/图片")
	jsonOutput := flag.Bool("json", false, "命令行直跑时输出 JSON")
	flag.Parse()

	if *solveFlag != "" {
		os.Exit(solveDirect(*solveFlag, *jsonOutput, false))
	}
	if flag.NArg() > 0 {
		target := flag.Arg(0)
		os.Exit(solveDirect(target, *jsonOutput, isImagePath(target)))
	}
	if err := runServer(*port, *lan, *noBrowser); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
