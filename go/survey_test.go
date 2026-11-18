package main

import (
	"bytes"
	"encoding/json"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/makiuchi-d/gozxing"
	"github.com/makiuchi-d/gozxing/qrcode"
	"golang.org/x/net/html"
)

func TestParseReceiptCode(t *testing.T) {
	info := parseReceiptCode("RCN0512142801118888654321")
	if !info.HasMonth || info.Month != 5 || !info.HasDay || info.Day != 12 {
		t.Fatalf("unexpected date: %+v", info)
	}
	if !info.HasHour || info.Hour != 14 || !info.HasMinute || info.Minute != 28 {
		t.Fatalf("unexpected time: %+v", info)
	}
	if info.ShopCode != "8888" {
		t.Fatalf("unexpected shop code: %q", info.ShopCode)
	}
}

func TestScoreOptionText(t *testing.T) {
	if scoreOptionText("10\u3000非常推荐") <= scoreOptionText("0\u3000完全不推荐") {
		t.Fatal("expected 10 非常推荐 > 0 完全不推荐")
	}
	if scoreOptionText("满意") <= scoreOptionText("不太满意") {
		t.Fatal("expected 满意 > 不太满意")
	}
	if scoreOptionText("正好") <= scoreOptionText("太大") {
		t.Fatal("expected 正好 > 太大")
	}
	if scoreOptionText("正好") <= scoreOptionText("太硬") {
		t.Fatal("expected 正好 > 太硬")
	}
	if scoreOptionText("是") <= scoreOptionText("否") {
		t.Fatal("expected 是 > 否")
	}
	if scoreOptionText("无线网络很棒") < 0 {
		t.Fatal("unexpected negative score on substring")
	}
}

func TestBuildQuestionPayloadPreservesRepeatedQ(t *testing.T) {
	raw := `
	<form name="form1" method="post" action="">
	  <input type="hidden" name="page" value="3" />
	  <input type="hidden" name="q[]" value="27263" />
	  <input type="hidden" name="q[]" value="27264" />
	  <input id="r1" name="q_27263" value="1" type="radio"><label for="r1">太大</label>
	  <input id="r2" name="q_27263" value="3" type="radio"><label for="r2">正好</label>
	  <textarea name="q_27279"></textarea>
	</form>`
	doc, err := html.Parse(strings.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	form := findFirst(doc, func(n *html.Node) bool {
		return n.Type == html.ElementNode && n.Data == "form"
	})
	vals := buildQuestionPayload(form, "好评", false)
	if len(vals["q[]"]) != 2 || vals["q[]"][0] != "27263" || vals["q[]"][1] != "27264" {
		t.Fatalf("q[] not preserved: %+v", vals["q[]"])
	}
	if vals.Get("q_27263") != "3" {
		t.Fatalf("expected 正好 (3), got %q", vals.Get("q_27263"))
	}
	if vals.Get("q_27279") != "" {
		t.Fatalf("expected empty optional textarea, got %q", vals.Get("q_27279"))
	}
}

func TestDecodeSyntheticReceiptQR(t *testing.T) {
	target := "https://cn-hamasushi.csfeedback.net/sv/zh/RCN0512142801118888654321"
	writer := qrcode.NewQRCodeWriter()
	matrix, err := writer.Encode(target, gozxing.BarcodeFormat_QR_CODE, 260, 260, nil)
	if err != nil {
		t.Fatal(err)
	}

	// Simulate a phone receipt photo with QR placed in the bottom section.
	canvas := image.NewRGBA(image.Rect(0, 0, 1000, 1400))
	draw.Draw(canvas, canvas.Bounds(), &image.Uniform{color.RGBA{185, 185, 185, 255}}, image.Point{}, draw.Src)
	draw.Draw(canvas, image.Rect(200, 100, 800, 1300), &image.Uniform{color.White}, image.Point{}, draw.Src)
	draw.Draw(canvas, image.Rect(370, 920, 630, 1180), matrix, image.Point{}, draw.Src)

	var buf bytes.Buffer
	if err := png.Encode(&buf, canvas); err != nil {
		t.Fatal(err)
	}
	candidates, err := decodeQRFromBytes(buf.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	if got := pickSurveyPayload(candidates); got != target {
		t.Fatalf("expected %q, got %q", target, got)
	}
}

func TestEmbeddedServerRoutes(t *testing.T) {
	mux := newServerMux()

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "滨寿司问卷验证码助手") {
		t.Fatalf("unexpected / response: %d", rec.Code)
	}

	reqJS := httptest.NewRequest(http.MethodGet, "/vendor/jsQR.js", nil)
	recJS := httptest.NewRecorder()
	mux.ServeHTTP(recJS, reqJS)
	if recJS.Code != http.StatusOK || recJS.Body.Len() < 50000 {
		t.Fatalf("unexpected jsQR.js response: %d len=%d", recJS.Code, recJS.Body.Len())
	}

	reqHealth := httptest.NewRequest(http.MethodGet, "/api/solve", nil)
	recHealth := httptest.NewRecorder()
	mux.ServeHTTP(recHealth, reqHealth)
	var health map[string]any
	if err := json.Unmarshal(recHealth.Body.Bytes(), &health); err != nil || health["mode"] != "go-native" {
		t.Fatalf("unexpected health: %s", recHealth.Body.String())
	}

	var blankBuf bytes.Buffer
	_ = png.Encode(&blankBuf, image.NewRGBA(image.Rect(0, 0, 120, 120)))
	reqImg := httptest.NewRequest(http.MethodPost, "/api/solve-image", &blankBuf)
	reqImg.Header.Set("Content-Type", "image/png")
	recImg := httptest.NewRecorder()
	mux.ServeHTTP(recImg, reqImg)
	if recImg.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422 on blank image, got %d", recImg.Code)
	}
}
