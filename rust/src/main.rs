//! Single-binary Hamasushi survey solver & embedded drag-and-drop desktop app.

mod common;
mod qr;
mod server;
mod solver;

use crate::qr::{decode_qr_from_bytes, pick_survey_payload};
use crate::solver::solve_survey_http;
use std::env;
use std::fs;
use std::io::{self, BufRead};
use std::path::Path;
use std::process::ExitCode;

fn is_image_path(arg: &str) -> bool {
    let path = Path::new(arg);
    if !path.is_file() {
        return false;
    }
    let Some(ext) = path.extension().and_then(|s| s.to_str()) else {
        return false;
    };
    matches!(
        ext.to_lowercase().as_str(),
        "jpg" | "jpeg" | "png" | "webp" | "bmp" | "gif"
    )
}

fn solve_direct(target: &str, json_output: bool, pause_on_exit: bool) -> ExitCode {
    let resolved = if is_image_path(target) {
        match fs::read(target)
            .map_err(|e| format!("读取图片失败: {e}"))
            .and_then(|bytes| decode_qr_from_bytes(&bytes))
        {
            Ok(candidates) => match pick_survey_payload(&candidates) {
                Some(url) => {
                    if !json_output {
                        println!("[二维码识别] {url}");
                    }
                    url
                }
                None => {
                    eprintln!("未能从图片中识别出二维码: {target}");
                    return ExitCode::from(1);
                }
            },
            Err(err) => {
                eprintln!("{err}");
                return ExitCode::from(1);
            }
        }
    } else {
        target.to_string()
    };

    match solve_survey_http(&resolved, None, 35) {
        Ok(res) => {
            if json_output {
                println!("{}", serde_json::to_string_pretty(&res).unwrap());
            } else {
                println!("========================================================");
                println!("运行状态: {} (成功: {})", res.status, res.success);
                println!("最终进度: {} | 共经历页面: {} | 耗时: {} ms", res.progress, res.pages_visited, res.elapsed_ms);
                if let Some(code) = &res.code {
                    println!("提取到的验证码: {code}");
                }
                println!("--------------------------------------------------------");
                println!("{}", res.page_text_excerpt);
                println!("========================================================");
            }
            if pause_on_exit && !json_output {
                println!("按回车键退出...");
                let _ = io::stdin().lock().read_line(&mut String::new());
            }
            if res.success { ExitCode::SUCCESS } else { ExitCode::from(1) }
        }
        Err(err) => {
            eprintln!("执行失败: {err}");
            if pause_on_exit && !json_output {
                println!("按回车键退出...");
                let _ = io::stdin().lock().read_line(&mut String::new());
            }
            ExitCode::from(1)
        }
    }
}

fn print_help() {
    println!("滨寿司小票问卷工具 (Rust 单文件免安装版)");
    println!("用法:");
    println!("  hamasushi-tool.exe                  直接启动桌面拖拽窗口（默认）");
    println!("  hamasushi-tool.exe <照片路径|链接|RCN> 直接识别并提取验证码");
    println!("选项:");
    println!("  --port <端口>     指定本地服务端口（默认自动分配）");
    println!("  --lan             允许同一局域网手机访问");
    println!("  --no-browser      仅启动服务，不自动打开窗口");
    println!("  --solve <目标>    命令行直跑指定链接/编号/图片");
    println!("  --json            命令行直跑时输出 JSON");
    println!("  -h, --help        显示帮助");
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    let mut port: u16 = 0;
    let mut lan = false;
    let mut no_browser = false;
    let mut json_output = false;
    let mut solve_target: Option<String> = None;
    let mut positional: Option<String> = None;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-h" | "--help" => {
                print_help();
                return ExitCode::SUCCESS;
            }
            "--lan" => lan = true,
            "--no-browser" => no_browser = true,
            "--json" => json_output = true,
            "--port" => {
                i += 1;
                if i >= args.len() {
                    eprintln!("--port 缺少端口号");
                    return ExitCode::from(1);
                }
                port = match args[i].parse() {
                    Ok(p) => p,
                    Err(_) => {
                        eprintln!("无效端口号: {}", args[i]);
                        return ExitCode::from(1);
                    }
                };
            }
            "--solve" => {
                i += 1;
                if i >= args.len() {
                    eprintln!("--solve 缺少参数");
                    return ExitCode::from(1);
                }
                solve_target = Some(args[i].clone());
            }
            other if !other.starts_with('-') && positional.is_none() => {
                positional = Some(other.to_string());
            }
            unknown => {
                eprintln!("未知参数: {unknown}");
                return ExitCode::from(1);
            }
        }
        i += 1;
    }

    if let Some(target) = solve_target {
        return solve_direct(&target, json_output, false);
    }
    if let Some(target) = positional {
        let pause = is_image_path(&target) && !json_output;
        return solve_direct(&target, json_output, pause);
    }

    match server::run_server(port, lan, no_browser) {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("{err}");
            ExitCode::from(1)
        }
    }
}

#[cfg(test)]
mod tests {
    use crate::common::{parse_receipt_code, score_option_text};

    #[test]
    fn parses_synthetic_rcn_code() {
        let info = parse_receipt_code("RCN0512142801118888654321");
        assert_eq!(info.month, Some(5));
        assert_eq!(info.day, Some(12));
        assert_eq!(info.hour, Some(14));
        assert_eq!(info.minute, Some(28));
        assert_eq!(info.shop_code, "8888");
    }

    #[test]
    fn scores_options_prefer_positive_and_exact_fit() {
        assert!(score_option_text("10\u{3000}非常推荐") > score_option_text("0\u{3000}完全不推荐"));
        assert!(score_option_text("满意") > score_option_text("不太满意"));
        assert!(score_option_text("正好") > score_option_text("太大"));
        assert!(score_option_text("正好") > score_option_text("太硬"));
        assert!(score_option_text("是") > score_option_text("否"));
        assert!(score_option_text("无线网络很棒") >= 0);
    }
}
