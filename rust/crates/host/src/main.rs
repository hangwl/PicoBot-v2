//! `picobot` — the Rust host: dashboard server, serial link, and (as the
//! migration lands) the bot, map monitor and frame streamer.
//!
//! ```text
//! picobot [--root DIR] [--port COM3|auto] [--window TITLE] [--ws N] [--http N]
//! picobot [--root DIR] --notify-test     # send one Telegram alert and exit
//! ```
//!
//! `--root` is the folder holding `config.json`, `maps/`, the reach files
//! and `web/dist` (default: `PICOBOT_ROOT`, else the current folder). It
//! shares them with the Python host — run one host at a time.

mod botbody;
mod bus;
mod clients;
mod commands;
mod evidence;
mod feed;
mod frames;
mod host;
mod server;
mod solves;
mod streamer;
mod telegram;

use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

use picobot_core::config::AppConfig;

struct Args {
    root: Option<PathBuf>,
    port: Option<String>,
    window: Option<String>,
    ws: Option<u16>,
    http: Option<u16>,
    notify_test: bool,
}

const USAGE: &str =
    "usage: picobot [--root DIR] [--port COM3|auto] [--window TITLE] [--ws N] [--http N] [--notify-test]";

fn parse_args() -> Result<Args, String> {
    let mut a = Args {
        root: None,
        port: None,
        window: None,
        ws: None,
        http: None,
        notify_test: false,
    };
    let mut it = std::env::args().skip(1);
    while let Some(flag) = it.next() {
        let mut val = || it.next().ok_or(format!("{flag} needs a value"));
        match flag.as_str() {
            "--root" => a.root = Some(val()?.into()),
            "--port" => a.port = Some(val()?),
            "--window" => a.window = Some(val()?),
            "--ws" => a.ws = Some(val()?.parse().map_err(|_| "--ws needs a port")?),
            "--http" => a.http = Some(val()?.parse().map_err(|_| "--http needs a port")?),
            "--notify-test" => a.notify_test = true,
            "-h" | "--help" => return Err(USAGE.into()),
            other => return Err(format!("unknown argument: {other}\n{USAGE}")),
        }
    }
    Ok(a)
}

fn main() -> ExitCode {
    let args = match parse_args() {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}");
            return ExitCode::from(2);
        }
    };
    let root = args
        .root
        .or_else(|| std::env::var_os("PICOBOT_ROOT").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."));
    // config.json, maps/ and the reach files are relative paths.
    if let Err(e) = std::env::set_current_dir(&root) {
        eprintln!("can't use {}: {e}", root.display());
        return ExitCode::from(2);
    }
    let root = std::env::current_dir().unwrap_or(root);
    picobot_io::window::init_dpi_awareness();

    let (mut config, err) = AppConfig::load(&root.join("config.json"));
    if let Some(e) = err {
        eprintln!("config.json unreadable ({e}) — using defaults");
    }
    if args.notify_test {
        let bus = std::sync::Arc::new(bus::Bus::default());
        let tg = telegram::Telegram::new(&config.bot_token, &config.chat_id, bus);
        return match tg.send("PicoBot (Rust host): test alert — Telegram works.") {
            telegram::Sent::Ok => {
                println!("Telegram: test alert sent");
                ExitCode::SUCCESS
            }
            other => {
                eprintln!("Telegram: {other:?}");
                ExitCode::FAILURE
            }
        };
    }
    if let Some(ws) = args.ws {
        config.ws_port = ws.into();
    }
    if let Some(http) = args.http {
        config.http_port = http.into();
    }
    let mut port = args
        .port
        .or_else(|| Some(config.serial_port.clone()).filter(|p| !p.is_empty()));
    if port.as_deref() == Some("auto") {
        port = match picobot_io::hid_transport::channels().as_slice() {
            [one] if one.count == 1 => Some(one.spec.clone()),
            _ => picobot_io::serial::discover_data_port(None, Duration::from_millis(1500)),
        };
        match &port {
            Some(p) => println!("discovered Pico DATA port: {p}"),
            None => {
                eprintln!("no Pico DATA port discovered");
                return ExitCode::from(2);
            }
        }
    }
    let changed = port.as_ref().is_some_and(|p| *p != config.serial_port)
        || args
            .window
            .as_ref()
            .is_some_and(|w| *w != config.default_target_window);
    if let Some(p) = &port {
        config.serial_port = p.clone();
    }
    if let Some(w) = &args.window {
        config.default_target_window = w.clone();
    }
    if changed {
        if let Err(e) = config.save(&root.join("config.json")) {
            eprintln!("saving config.json failed: {e}");
        }
    }
    if config.ws_tls {
        eprintln!("ws_tls is set, but the Rust host serves plain ws:// for now");
    }
    let (ws_base, http_base) = (config.ws_port as u16, config.http_port as u16);
    let bind_mode = config.bind.clone();
    let window = config.default_target_window.clone();
    let host = host::Host::new(root.clone(), config, window);
    host.bus.subscribe(|e| {
        if e["level"] != "debug" {
            println!(
                "[{}] {}",
                e["kind"].as_str().unwrap_or(""),
                e["msg"].as_str().unwrap_or("")
            );
        }
    });
    host.request_title();
    if let Some(p) = &port {
        // Degraded-tolerant: a stale remembered port must not keep the
        // dashboard from coming up.
        host.open_serial(p);
    }

    let rt = match tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
    {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("async runtime: {e}");
            return ExitCode::FAILURE;
        }
    };
    let served = rt.block_on(async {
        let scope = server::bind_addrs(&bind_mode);
        let (ws, ws_port, _) =
            server::bind_from(ws_base, 10, &scope).map_err(|e| format!("ws: {e}"))?;
        let (http, http_port, dual) =
            server::bind_from(http_base, 5, &scope).map_err(|e| format!("http: {e}"))?;
        host.set_ws_port(ws_port);
        host.set_http_port(http_port);
        if ws_port != ws_base {
            host.bus.emit_level(
                "remote",
                &format!("ws: port {ws_base} busy — serving on {ws_port}"),
                "warn",
            );
        }
        if http_port != http_base {
            host.bus.emit_level(
                "http",
                &format!("http: port {http_base} busy — serving on {http_port}"),
                "warn",
            );
        }
        let listeners = |ls: Vec<std::net::TcpListener>| {
            ls.into_iter()
                .map(|l| tokio::net::TcpListener::from_std(l).map_err(|e| e.to_string()))
                .collect::<Result<Vec<_>, _>>()
        };
        let (ws, http) = (listeners(ws)?, listeners(http)?);
        let ws_app =
            server::ws_router(host.clone()).into_make_service_with_connect_info::<SocketAddr>();
        let static_dir = root.join("web").join("dist");
        let http_app = server::http_router(server::Http {
            host: host.clone(),
            static_dir,
        })
        .into_make_service_with_connect_info::<SocketAddr>();
        for l in ws {
            let app = ws_app.clone();
            tokio::spawn(async move { axum::serve(l, app).await });
        }
        for l in http {
            let app = http_app.clone();
            tokio::spawn(async move { axum::serve(l, app).await });
        }
        host.bus.emit("status", &format!("ws port: {ws_port}"));
        host.bus.emit(
            "status",
            &format!(
                "dashboard: {}",
                server::local_urls(http_port, &scope).join(", ")
            ),
        );
        println!(
            "picobot (rust) running — ws :{ws_port}, http :{http_port}{}. Ctrl+C to quit.",
            if dual { "" } else { ", IPv4 only" }
        );
        let _streamer = streamer::Streamer::start(host.clone());
        let _recorder = solves::RuneRecorder::start(host.clone());
        tokio::signal::ctrl_c().await.map_err(|e| e.to_string())
    });
    if let Err(e) = served {
        eprintln!("{e}");
        return ExitCode::FAILURE;
    }
    println!("shutting down");
    host.shutdown();
    ExitCode::SUCCESS
}
