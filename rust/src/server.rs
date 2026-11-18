//! Embedded HTTP server for the single-binary desktop drag-and-drop UI.

use crate::qr::{decode_qr_from_bytes, pick_survey_payload};
use crate::solver::solve_survey_http;
use base64::Engine;
use serde::Deserialize;
use serde_json::json;
use std::io::Read;
use std::net::{TcpListener, UdpSocket};
use std::path::Path;
use std::process::{Command, Stdio};
use std::thread;
use tiny_http::{Header, Method, Request, Response, Server, StatusCode};

const INDEX_HTML: &[u8] = include_bytes!("../../public/index.html");
const JSQR_JS: &[u8] = include_bytes!("../../public/vendor/jsQR.js");
const MAX_UPLOAD_BYTES: u64 = 25 * 1024 * 1024;

#[derive(Deserialize)]
struct SolveUrlBody {
    url: Option<String>,
    comment: Option<String>,
}

#[derive(Deserialize)]
struct SolveImageBody {
    image_base64: Option<String>,
    comment: Option<String>,
}

fn json_response(status: u16, body: serde_json::Value) -> Response<std::io::Cursor<Vec<u8>>> {
    let bytes = serde_json::to_vec(&body).unwrap_or_else(|_| b"{}".to_vec());
    Response::from_data(bytes)
        .with_status_code(StatusCode(status))
        .with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap())
        .with_header(Header::from_bytes("Cache-Control", "no-store").unwrap())
}

fn static_response(bytes: &[u8], content_type: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    Response::from_data(bytes.to_vec())
        .with_status_code(StatusCode(200))
        .with_header(Header::from_bytes("Content-Type", content_type).unwrap())
}

fn read_body_limited(req: &mut Request) -> Result<Vec<u8>, String> {
    let mut buf = Vec::new();
    req.as_reader()
        .take(MAX_UPLOAD_BYTES + 1)
        .read_to_end(&mut buf)
        .map_err(|e| format!("读取请求体失败: {e}"))?;
    if buf.len() as u64 > MAX_UPLOAD_BYTES {
        return Err("图片文件过大（上限 25MB）。".to_string());
    }
    Ok(buf)
}

fn handle_solve_url(req: &mut Request) -> Response<std::io::Cursor<Vec<u8>>> {
    let body = match read_body_limited(req) {
        Ok(b) => b,
        Err(e) => return json_response(413, json!({ "success": false, "error": e })),
    };
    let parsed: SolveUrlBody = match serde_json::from_slice(&body) {
        Ok(v) => v,
        Err(_) => return json_response(400, json!({ "success": false, "error": "无效的 JSON 请求格式。" })),
    };
    let target = parsed.url.unwrap_or_default();
    if target.trim().is_empty() {
        return json_response(400, json!({ "success": false, "error": "请提供问卷链接或 RCN 小票编号。" }));
    }
    match solve_survey_http(&target, parsed.comment.as_deref(), 35) {
        Ok(res) => json_response(200, serde_json::to_value(res).unwrap()),
        Err(err) => json_response(400, json!({ "success": false, "error": err })),
    }
}

fn handle_solve_image(req: &mut Request) -> Response<std::io::Cursor<Vec<u8>>> {
    let content_type = req
        .headers()
        .iter()
        .find(|h| h.field.equiv("Content-Type"))
        .map(|h| h.value.as_str().to_lowercase())
        .unwrap_or_default();

    let body = match read_body_limited(req) {
        Ok(b) => b,
        Err(e) => return json_response(413, json!({ "success": false, "error": e })),
    };

    let (image_bytes, comment) = if content_type.contains("application/json") {
        let parsed: SolveImageBody = match serde_json::from_slice(&body) {
            Ok(v) => v,
            Err(_) => return json_response(400, json!({ "success": false, "error": "无效的图片 JSON 请求。" })),
        };
        let raw_b64 = parsed.image_base64.unwrap_or_default();
        let trimmed = raw_b64.trim();
        let b64_part = if trimmed.starts_with("data:") {
            trimmed.split_once(',').map(|(_, after)| after).unwrap_or("")
        } else {
            trimmed
        };
        if b64_part.is_empty() {
            return json_response(400, json!({ "success": false, "error": "缺少图片数据。" }));
        }
        match base64::engine::general_purpose::STANDARD.decode(b64_part) {
            Ok(bytes) => (bytes, parsed.comment),
            Err(e) => return json_response(400, json!({ "success": false, "error": format!("Base64 解码失败: {e}") })),
        }
    } else if !body.is_empty() {
        (body, None)
    } else {
        return json_response(400, json!({ "success": false, "error": "上传图片内容为空。" }));
    };

    let candidates = match decode_qr_from_bytes(&image_bytes) {
        Ok(c) => c,
        Err(err) => return json_response(400, json!({ "success": false, "error": err })),
    };
    let Some(decoded) = pick_survey_payload(&candidates) else {
        return json_response(
            422,
            json!({
                "success": false,
                "error": "未能从照片中识别出二维码，请尝试对准二维码裁切或拍清晰后再拖入。"
            }),
        );
    };

    match solve_survey_http(&decoded, comment.as_deref(), 35) {
        Ok(res) => {
            let mut val = serde_json::to_value(res).unwrap();
            if let Some(obj) = val.as_object_mut() {
                obj.insert("decoded_qr".to_string(), serde_json::Value::String(decoded));
            }
            json_response(200, val)
        }
        Err(err) => json_response(400, json!({ "success": false, "error": err })),
    }
}

fn handle_request(mut req: Request) {
    let path = req.url().split('?').next().unwrap_or("/");
    let resp = match (req.method(), path) {
        (&Method::Get, "/") | (&Method::Get, "/index.html") => {
            static_response(INDEX_HTML, "text/html; charset=utf-8")
        }
        (&Method::Get, "/vendor/jsQR.js") => {
            static_response(JSQR_JS, "application/javascript; charset=utf-8")
        }
        (&Method::Get, "/api/health") | (&Method::Get, "/api/solve") => json_response(
            200,
            json!({
                "ok": true,
                "mode": "rust-native",
                "opencv_qr": true,
                "mode_label": "Rust 单文件零依赖模式：内置 Edge + Rust 原生 QR 双引擎识别"
            }),
        ),
        (&Method::Post, "/api/solve") => handle_solve_url(&mut req),
        (&Method::Post, "/api/solve-image") => handle_solve_image(&mut req),
        _ => Response::from_string("Not Found").with_status_code(StatusCode(404)),
    };
    let _ = req.respond(resp);
}

fn find_app_browser() -> Option<&'static str> {
    let candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ];
    candidates.into_iter().find(|p| Path::new(p).exists())
}

fn launch_desktop_window(url: &str) {
    if let Some(exe) = find_app_browser()
        && Command::new(exe)
            .args([format!("--app={url}"), "--window-size=560,840".to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .is_ok()
    {
        return;
    }
    let _ = Command::new("cmd").args(["/c", "start", "", url]).spawn();
}

fn pick_free_port(host: &str) -> Result<u16, String> {
    let listener = TcpListener::bind(format!("{host}:0"))
        .map_err(|e| format!("无法绑定端口: {e}"))?;
    Ok(listener.local_addr().map_err(|e| e.to_string())?.port())
}

fn local_lan_ip() -> String {
    if let Ok(sock) = UdpSocket::bind("0.0.0.0:0")
        && sock.connect("8.8.8.8:80").is_ok()
        && let Ok(addr) = sock.local_addr()
    {
        return addr.ip().to_string();
    }
    "127.0.0.1".to_string()
}

pub fn run_server(port: u16, lan: bool, no_browser: bool) -> Result<(), String> {
    let host = if lan { "0.0.0.0" } else { "127.0.0.1" };
    let actual_port = if port == 0 {
        pick_free_port(host)?
    } else {
        port
    };
    let bind_addr = format!("{host}:{actual_port}");
    let server = Server::http(&bind_addr).map_err(|e| format!("启动本地服务失败: {e}"))?;
    let local_url = format!("http://127.0.0.1:{actual_port}/");

    println!("========================================================");
    println!("滨寿司小票问卷助手（Rust 单文件零依赖版）已启动！");
    println!("窗口地址: {local_url}");
    if lan {
        println!("同局域网手机地址: http://{}:{actual_port}/", local_lan_ip());
    }
    println!("使用方式: 直接把手机小票照片拖入窗口，或 Ctrl+V 粘贴图片/链接！");
    println!("提示: 关闭此窗口即可退出后台服务。");
    println!("========================================================");

    if !no_browser {
        let url_clone = local_url.clone();
        thread::spawn(move || {
            thread::sleep(std::time::Duration::from_millis(250));
            launch_desktop_window(&url_clone);
        });
    }

    for req in server.incoming_requests() {
        thread::spawn(move || handle_request(req));
    }
    Ok(())
}
