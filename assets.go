// Package assets embeds the shared web UI assets into the Go binary.
package assets

import "embed"

//go:embed public/index.html public/vendor/jsQR.js
var PublicFS embed.FS
