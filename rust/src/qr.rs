//! Pure-Rust multi-pass QR code decoder for phone receipt photos.

use crate::common::{rcn_search_regex, survey_url_regex};
use image::imageops::{contrast, resize, FilterType};
use image::{DynamicImage, GrayImage};

fn try_rqrr(gray: &GrayImage) -> Vec<String> {
    let mut prepared = rqrr::PreparedImage::prepare(gray.clone());
    let grids = prepared.detect_grids();
    let mut out = Vec::new();
    for grid in grids {
        if let Ok((_meta, content)) = grid.decode() {
            let trimmed = content.trim().to_string();
            if !trimmed.is_empty() {
                out.push(trimmed);
            }
        }
    }
    out
}

fn resize_max_dim(img: &GrayImage, max_dim: u32) -> GrayImage {
    let (w, h) = img.dimensions();
    let longest = w.max(h);
    if longest <= max_dim {
        return img.clone();
    }
    let scale = max_dim as f32 / longest as f32;
    let nw = ((w as f32) * scale).round().max(1.0) as u32;
    let nh = ((h as f32) * scale).round().max(1.0) as u32;
    resize(img, nw, nh, FilterType::Triangle)
}

fn candidate_views(dyn_img: &DynamicImage) -> Vec<GrayImage> {
    let full_gray = dyn_img.to_luma8();
    let scaled = resize_max_dim(&full_gray, 1600);
    let (w, h) = scaled.dimensions();

    let mut views = Vec::new();
    views.push(scaled.clone());

    if full_gray.width().max(full_gray.height()) > 1600 {
        views.push(resize_max_dim(&full_gray, 2600));
    }

    let boosted = contrast(&scaled, 35.0);
    views.push(boosted.clone());

    // Crop bottom half and center region where receipt QR codes typically appear, then upscale 2x.
    let bottom_y = (h as f32 * 0.40) as u32;
    if h > bottom_y + 20 {
        let crop = image::imageops::crop_imm(&scaled, 0, bottom_y, w, h - bottom_y).to_image();
        let up = resize(&crop, crop.width() * 2, crop.height() * 2, FilterType::CatmullRom);
        views.push(up);

        let crop_boost = image::imageops::crop_imm(&boosted, 0, bottom_y, w, h - bottom_y).to_image();
        views.push(resize(
            &crop_boost,
            crop_boost.width() * 2,
            crop_boost.height() * 2,
            FilterType::CatmullRom,
        ));
    }

    let cx = (w as f32 * 0.15) as u32;
    let cy = (h as f32 * 0.25) as u32;
    let cw = (w as f32 * 0.70) as u32;
    let ch = (h as f32 * 0.55) as u32;
    if cw > 40 && ch > 40 && cx + cw <= w && cy + ch <= h {
        let mid = image::imageops::crop_imm(&scaled, cx, cy, cw, ch).to_image();
        views.push(resize(&mid, mid.width() * 2, mid.height() * 2, FilterType::CatmullRom));
    }

    views
}

pub fn decode_qr_from_bytes(image_bytes: &[u8]) -> Result<Vec<String>, String> {
    let dyn_img = image::load_from_memory(image_bytes)
        .map_err(|e| format!("无法读取图片格式: {e}"))?;

    for view in candidate_views(&dyn_img) {
        let found = try_rqrr(&view);
        if !found.is_empty() {
            return Ok(found);
        }
    }
    Ok(Vec::new())
}

pub fn pick_survey_payload(candidates: &[String]) -> Option<String> {
    for item in candidates {
        if let Some(m) = survey_url_regex().find(item) {
            return Some(m.as_str().to_string());
        }
    }
    for item in candidates {
        if rcn_search_regex().is_match(item) {
            return Some(item.trim().to_string());
        }
    }
    candidates.first().map(|s| s.trim().to_string())
}
