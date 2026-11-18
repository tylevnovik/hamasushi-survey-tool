"""Multi-pass QR code decoding utilities optimized for phone photos of receipts."""

from __future__ import annotations

import io
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image, ImageOps

from survey_common import SURVEY_URL_PATTERN


def _load_bgr_image(image_bytes: bytes) -> np.ndarray:
    """Decode raw image bytes while honoring mobile EXIF orientation tags."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        oriented = ImageOps.exif_transpose(img).convert("RGB")
        rgb = np.array(oriented)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _resize_max_dim(img: np.ndarray, max_dim: int) -> np.ndarray:
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= max_dim:
        return img
    scale = max_dim / float(longest)
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _try_detect(detector: cv2.QRCodeDetector, mat: np.ndarray) -> list[str]:
    results: list[str] = []
    try:
        ok, decoded_info, _, _ = detector.detectAndDecodeMulti(mat)
        if ok and decoded_info:
            results.extend(item.strip() for item in decoded_info if item and item.strip())
    except cv2.error:
        pass

    if not results:
        try:
            text, _, _ = detector.detectAndDecode(mat)
            if text and text.strip():
                results.append(text.strip())
        except cv2.error:
            pass
    return results


def _candidate_views(bgr: np.ndarray) -> list[np.ndarray]:
    """Generate progressive image variants for challenging receipt lighting and angles."""
    views: list[np.ndarray] = []
    scaled = _resize_max_dim(bgr, 1600)
    gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)

    views.append(scaled)
    views.append(gray)

    # Full resolution if the QR code is small on a high-res phone photo.
    if max(bgr.shape[:2]) > 1600:
        views.append(cv2.cvtColor(_resize_max_dim(bgr, 2800), cv2.COLOR_BGR2GRAY))

    # Local contrast enhancement helps thermal receipt print on uneven backgrounds.
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    views.append(enhanced)

    # Adaptive threshold handles shadows / warm indoor restaurant lighting.
    thresh = cv2.adaptiveThreshold(
        enhanced,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35,
        8,
    )
    views.append(thresh)

    # Receipt QR codes typically sit in the lower half or middle section; crop & upscale 2x.
    h, w = gray.shape[:2]
    crops = (
        gray[int(h * 0.45) :, :],
        gray[int(h * 0.25) : int(h * 0.80), :],
        enhanced[int(h * 0.45) :, :],
    )
    for crop in crops:
        if crop.size > 0 and max(crop.shape[:2]) < 2400:
            views.append(cv2.resize(crop, (0, 0), fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC))
    return views


def decode_qr_from_bytes(image_bytes: bytes) -> list[str]:
    """Decode one or more QR payloads from raw image bytes."""
    bgr = _load_bgr_image(image_bytes)
    detector = cv2.QRCodeDetector()

    for view in _candidate_views(bgr):
        found = _try_detect(detector, view)
        if found:
            return found
    return []


def decode_qr_from_path(image_path: str | Path) -> list[str]:
    """Read an image file from disk and decode QR payloads."""
    data = Path(image_path).read_bytes()
    return decode_qr_from_bytes(data)


def pick_survey_payload(candidates: list[str]) -> str | None:
    """Pick the most likely Hamasushi survey URL or RCN code from decoded QR strings."""
    for item in candidates:
        if SURVEY_URL_PATTERN.search(item):
            return SURVEY_URL_PATTERN.search(item).group(0)  # type: ignore[union-attr]
    for item in candidates:
        if re.search(r"RCN\d{16,}", item, re.IGNORECASE):
            return item.strip()
    return candidates[0].strip() if candidates else None
