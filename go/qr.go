package main

import (
	"bytes"
	"fmt"
	"image"
	"image/color"
	_ "image/gif"
	_ "image/jpeg"
	_ "image/png"
	"math"
	"strings"

	"github.com/makiuchi-d/gozxing"
	"github.com/makiuchi-d/gozxing/qrcode"
	_ "golang.org/x/image/bmp"
	"golang.org/x/image/draw"
	_ "golang.org/x/image/webp"
)

func resizeMaxDim(src image.Image, maxDim int) image.Image {
	b := src.Bounds()
	w, h := b.Dx(), b.Dy()
	longest := w
	if h > longest {
		longest = h
	}
	if longest <= maxDim || longest == 0 {
		return src
	}
	scale := float64(maxDim) / float64(longest)
	nw := int(math.Max(1, math.Round(float64(w)*scale)))
	nh := int(math.Max(1, math.Round(float64(h)*scale)))
	dst := image.NewRGBA(image.Rect(0, 0, nw, nh))
	draw.BiLinear.Scale(dst, dst.Bounds(), src, b, draw.Over, nil)
	return dst
}

func cropAndScale2x(src image.Image, x0Frac, y0Frac, x1Frac, y1Frac float64) image.Image {
	b := src.Bounds()
	w, h := b.Dx(), b.Dy()
	r := image.Rect(
		b.Min.X+int(float64(w)*x0Frac),
		b.Min.Y+int(float64(h)*y0Frac),
		b.Min.X+int(float64(w)*x1Frac),
		b.Min.Y+int(float64(h)*y1Frac),
	)
	if r.Dx() < 20 || r.Dy() < 20 {
		return src
	}
	dst := image.NewRGBA(image.Rect(0, 0, r.Dx()*2, r.Dy()*2))
	draw.CatmullRom.Scale(dst, dst.Bounds(), src, r, draw.Over, nil)
	return dst
}

func boostContrastGray(src image.Image, factor float64) image.Image {
	b := src.Bounds()
	dst := image.NewGray(image.Rect(0, 0, b.Dx(), b.Dy()))
	for y := b.Min.Y; y < b.Max.Y; y++ {
		for x := b.Min.X; x < b.Max.X; x++ {
			c := color.GrayModel.Convert(src.At(x, y)).(color.Gray)
			v := (float64(c.Y)-128.0)*factor + 128.0
			if v < 0 {
				v = 0
			} else if v > 255 {
				v = 255
			}
			dst.SetGray(x-b.Min.X, y-b.Min.Y, color.Gray{Y: uint8(v)})
		}
	}
	return dst
}

func tryDecodeZxing(reader gozxing.Reader, img image.Image) string {
	bmp, err := gozxing.NewBinaryBitmapFromImage(img)
	if err != nil {
		return ""
	}
	hints := map[gozxing.DecodeHintType]interface{}{
		gozxing.DecodeHintType_TRY_HARDER: true,
	}
	res, err := reader.Decode(bmp, hints)
	if err != nil || res == nil {
		return ""
	}
	return strings.TrimSpace(res.GetText())
}

func decodeQRFromBytes(data []byte) ([]string, error) {
	img, _, err := image.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("无法读取图片格式: %w", err)
	}

	reader := qrcode.NewQRCodeReader()
	scaled := resizeMaxDim(img, 1600)
	boosted := boostContrastGray(scaled, 1.8)

	candidates := []image.Image{
		scaled,
		boosted,
		cropAndScale2x(scaled, 0.0, 0.40, 1.0, 1.0),
		cropAndScale2x(boosted, 0.0, 0.40, 1.0, 1.0),
		cropAndScale2x(scaled, 0.15, 0.25, 0.85, 0.80),
	}
	b := img.Bounds()
	if b.Dx() > 1600 || b.Dy() > 1600 {
		candidates = append(candidates, resizeMaxDim(img, 2600))
	}

	for _, view := range candidates {
		if text := tryDecodeZxing(reader, view); text != "" {
			return []string{text}, nil
		}
	}
	return nil, nil
}

func pickSurveyPayload(candidates []string) string {
	for _, item := range candidates {
		if m := surveyURLPattern.FindString(item); m != "" {
			return m
		}
	}
	for _, item := range candidates {
		if rcnSearchPattern.MatchString(item) {
			return strings.TrimSpace(item)
		}
	}
	if len(candidates) > 0 {
		return strings.TrimSpace(candidates[0])
	}
	return ""
}
