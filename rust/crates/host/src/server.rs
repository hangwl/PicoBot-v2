//! The dashboard's two listeners: the WebSocket (normally :8765) and the
//! HTTP server for the built app (normally :8000). Each takes the next
//! free port when its own is busy; the page learns the live WS port from
//! a `<meta>` the HTTP server fills in.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, SocketAddrV6, TcpListener as StdListener};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use axum::body::Body;
use axum::extract::ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade};
use axum::extract::{ConnectInfo, State};
use axum::http::{header, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::Router;
use serde_json::json;
use socket2::{Domain, Socket, Type};

use crate::clients::Out;
use crate::host::Host;

/// Bind `[::]:port` accepting IPv4 too (Tailscale names resolve to both a
/// 100.x and an fd7a:: address), or plain IPv4 without IPv6. Windows'
/// default already refuses a second bind to a port in use.
fn bind(port: u16) -> std::io::Result<(StdListener, bool)> {
    let v6 = (|| {
        let s = Socket::new(Domain::IPV6, Type::STREAM, None)?;
        s.set_only_v6(false)?;
        s.bind(&SocketAddr::V6(SocketAddrV6::new(Ipv6Addr::UNSPECIFIED, port, 0, 0)).into())?;
        s.listen(128)?;
        Ok::<_, std::io::Error>(s)
    })();
    let (s, dual) = match v6 {
        Ok(s) => (s, true),
        Err(e) if e.kind() == std::io::ErrorKind::AddrInUse => return Err(e),
        Err(_) => {
            let s = Socket::new(Domain::IPV4, Type::STREAM, None)?;
            s.bind(&SocketAddr::from(([0, 0, 0, 0], port)).into())?;
            s.listen(128)?;
            (s, false)
        }
    };
    s.set_nonblocking(true)?;
    Ok((s.into(), dual))
}

fn bind_one(addr: SocketAddr) -> std::io::Result<StdListener> {
    let domain = if addr.is_ipv4() {
        Domain::IPV4
    } else {
        Domain::IPV6
    };
    let s = Socket::new(domain, Type::STREAM, None)?;
    s.bind(&addr.into())?;
    s.listen(128)?;
    s.set_nonblocking(true)?;
    Ok(s.into())
}

/// One listener per address on `port`. An IPv6 address that can't be
/// bound (no IPv6 stack) is skipped; any other failure drops them all.
fn bind_scoped(addrs: &[IpAddr], port: u16) -> std::io::Result<Vec<StdListener>> {
    let mut out = Vec::new();
    for ip in addrs {
        match bind_one(SocketAddr::new(*ip, port)) {
            Ok(l) => out.push(l),
            Err(_) if ip.is_ipv6() => {}
            Err(e) => return Err(e),
        }
    }
    Ok(out)
}

/// This machine's IPv4 addresses (Tailscale flagged), Tailscale first.
fn local_ipv4s() -> Vec<(bool, Ipv4Addr)> {
    let mut found: Vec<(bool, Ipv4Addr)> = Vec::new();
    // Connecting a UDP socket sends nothing; it only picks the route.
    for probe in ["100.100.100.100:53", "8.8.8.8:53"] {
        if let Ok(s) = std::net::UdpSocket::bind("0.0.0.0:0") {
            if s.connect(probe).is_ok() {
                if let Ok(SocketAddr::V4(a)) = s.local_addr() {
                    let ip = *a.ip();
                    let tail = ip.octets()[0] == 100 && (64..128).contains(&ip.octets()[1]);
                    if !ip.is_loopback() && !ip.is_unspecified() && !found.iter().any(|f| f.1 == ip)
                    {
                        found.push((tail, ip));
                    }
                }
            }
        }
    }
    found.sort_by_key(|f| !f.0);
    found
}

/// The addresses to serve on for a `bind` setting; empty means every
/// interface (`all`, or a setting that isn't understood).
pub fn bind_addrs(mode: &str) -> Vec<IpAddr> {
    let loopback = [
        IpAddr::V4(Ipv4Addr::LOCALHOST),
        IpAddr::V6(Ipv6Addr::LOCALHOST),
    ];
    let mode = mode.trim().to_lowercase();
    let extra: Vec<IpAddr> = match mode.as_str() {
        "all" | "" => return Vec::new(),
        "loopback" => Vec::new(),
        "auto" => local_ipv4s().into_iter().map(|f| f.1.into()).collect(),
        "tailscale" => local_ipv4s()
            .into_iter()
            .filter(|f| f.0)
            .map(|f| f.1.into())
            .collect(),
        other => match other.parse::<Ipv4Addr>() {
            Ok(ip) => vec![ip.into()],
            Err(_) => return Vec::new(),
        },
    };
    loopback.into_iter().chain(extra).collect()
}

/// The first free port of `base..base + attempts`, bound on `addrs`
/// (every interface when empty). The flag says whether the wildcard
/// listener also takes IPv6.
pub fn bind_from(
    base: u16,
    attempts: u16,
    addrs: &[IpAddr],
) -> std::io::Result<(Vec<StdListener>, u16, bool)> {
    let mut last = None;
    for off in 0..attempts {
        let tried = if addrs.is_empty() {
            bind(base + off).map(|(l, dual)| (vec![l], dual))
        } else {
            bind_scoped(addrs, base + off).map(|ls| (ls, true))
        };
        match tried {
            Ok((ls, dual)) => {
                let port = ls[0].local_addr()?.port();
                return Ok((ls, port, dual));
            }
            Err(e) => last = Some(e),
        }
    }
    Err(last.unwrap_or_else(|| std::io::Error::other("no port to try")))
}

// -- WebSocket ------------------------------------------------------------------------

pub fn ws_router(host: Arc<Host>) -> Router {
    Router::new().fallback(ws_upgrade).with_state(host)
}

async fn ws_upgrade(
    ws: WebSocketUpgrade,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    State(host): State<Arc<Host>>,
) -> Response {
    ws.on_upgrade(move |socket| client(socket, addr, host))
}

fn peer_name(addr: SocketAddr) -> String {
    let ip = addr.ip().to_canonical();
    format!("{ip}:{}", addr.port())
}

async fn client(mut socket: WebSocket, addr: SocketAddr, host: Arc<Host>) {
    let mut reg = host.clients.register(&peer_name(addr));
    let hello = format!("dash|{}", json!({"event": "hello", "protocol": 1}));
    if socket.send(Message::Text(hello.into())).await.is_err() {
        host.client_gone(reg.id);
        return;
    }
    host.client_connected(reg.id);
    let slot = reg.slot.clone();
    loop {
        tokio::select! {
            biased;
            out = reg.rx.recv() => match out {
                Some(Out::Text(t)) => {
                    if socket.send(Message::Text(t.into())).await.is_err() {
                        break;
                    }
                }
                Some(Out::Close(code, reason)) => {
                    let frame = CloseFrame { code, reason: reason.into() };
                    let _ = socket.send(Message::Close(Some(frame))).await;
                    break;
                }
                None => break,
            },
            incoming = socket.recv() => match incoming {
                Some(Ok(Message::Text(t))) => {
                    let msg = t.as_str().trim();
                    if let Some(nonce) = msg.strip_prefix("ping|") {
                        let pong = format!("pong|{nonce}");
                        if socket.send(Message::Text(pong.into())).await.is_err() {
                            break;
                        }
                    } else if !msg.is_empty() {
                        host.on_message(reg.id, msg);
                    }
                }
                Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                Some(Ok(_)) => {}
            },
            _ = slot.ready.notified() => {
                let frame = slot.frame.lock().unwrap().take();
                if let Some(data) = frame {
                    *slot.sending_since.lock().unwrap() = Some(Instant::now());
                    let sent = socket.send(Message::Binary(data.to_vec().into())).await;
                    *slot.sending_since.lock().unwrap() = None;
                    if sent.is_err() {
                        break;
                    }
                }
            }
        }
    }
    host.client_gone(reg.id);
}

// -- HTTP -----------------------------------------------------------------------------

#[derive(Clone)]
pub struct Http {
    pub host: Arc<Host>,
    pub static_dir: PathBuf,
}

pub fn http_router(http: Http) -> Router {
    Router::new().fallback(serve).with_state(http)
}

fn mime(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "js" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" => "application/json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "ico" => "image/x-icon",
        "woff2" => "font/woff2",
        "webmanifest" => "application/manifest+json",
        _ => "application/octet-stream",
    }
}

fn reply(status: StatusCode, ctype: &str, body: Vec<u8>, no_cache: bool) -> Response {
    let mut r = Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, ctype)
        .header(header::CONTENT_LENGTH, body.len());
    if no_cache {
        r = r.header(header::CACHE_CONTROL, "no-cache");
    }
    r.body(Body::from(body))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

const NOT_BUILT: &str = "<html><body style='background:#121212;color:#eee;font-family:sans-serif'>\
<h3 style='margin:16px'>Dashboard not built</h3>\
<p style='margin:16px'>Run <code>npm install</code> then <code>npm run build</code> in the \
<code>web</code> folder, then reload this page.</p></body></html>";

async fn serve(
    State(http): State<Http>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    uri: Uri,
) -> Response {
    let path = uri.path().to_owned();
    let resp = respond(&http, &path, addr.ip().to_canonical().is_loopback());
    let msg = format!(
        "http: {} GET {path} → {}",
        addr.ip().to_canonical(),
        resp.status().as_u16()
    );
    http.host.bus.emit_full("http", &msg, Some("debug"), None);
    resp
}

/// `/health`, a built file, or `index.html` with the live WS port filled
/// in (also the SPA fallback for unknown routes).
fn respond(http: &Http, path: &str, local: bool) -> Response {
    let ws_port = http.host.ws_port();
    if path == "/health" {
        if !local {
            return reply(
                StatusCode::OK,
                "application/json",
                br#"{"ok": true}"#.to_vec(),
                true,
            );
        }
        let dist = http.static_dir.join("index.html").exists();
        let t = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        let body = json!({"ok": true, "ws_port": ws_port, "dashboard_built": dist, "time": (t * 1000.0).round() / 1000.0});
        return reply(
            StatusCode::OK,
            "application/json",
            body.to_string().into_bytes(),
            true,
        );
    }
    let index = http.static_dir.join("index.html");
    let rel = path.trim_start_matches('/');
    if !rel.is_empty()
        && !rel
            .split('/')
            .any(|p| p == ".." || p.contains('\\') || p.contains(':'))
    {
        let file = http.static_dir.join(rel);
        if file != index && file.is_file() {
            if let Ok(data) = std::fs::read(&file) {
                return reply(StatusCode::OK, mime(&file), data, false);
            }
        }
    }
    match std::fs::read_to_string(&index) {
        Ok(html) => {
            let html = html
                .replace("REPLACE_WS_PORT", &ws_port.to_string())
                .replace("REPLACE_WS_SCHEME", "ws");
            reply(
                StatusCode::OK,
                "text/html; charset=utf-8",
                html.into_bytes(),
                true,
            )
        }
        Err(_) if path == "/" => reply(
            StatusCode::OK,
            "text/html; charset=utf-8",
            NOT_BUILT.into(),
            true,
        ),
        Err(_) => reply(
            StatusCode::NOT_FOUND,
            "text/plain; charset=utf-8",
            b"Not Found".to_vec(),
            false,
        ),
    }
}

/// Dashboard URLs for this machine's addresses, Tailscale (100.64/10)
/// first; only those in `bound` when the listeners are scoped.
pub fn local_urls(port: u16, bound: &[IpAddr]) -> Vec<String> {
    let urls: Vec<String> = local_ipv4s()
        .into_iter()
        .filter(|(_, ip)| bound.is_empty() || bound.contains(&IpAddr::V4(*ip)))
        .map(|(tail, ip)| {
            format!(
                "http://{ip}:{port} ({})",
                if tail { "tailscale" } else { "lan" }
            )
        })
        .collect();
    if urls.is_empty() {
        vec![format!("http://localhost:{port}")]
    } else {
        urls
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bind_modes_pick_addresses() {
        assert!(bind_addrs("all").is_empty());
        let loop_only = bind_addrs("loopback");
        assert!(loop_only.iter().all(|a| a.is_loopback()));
        assert!(loop_only.contains(&IpAddr::V4(Ipv4Addr::LOCALHOST)));
        let one = bind_addrs("192.0.2.7");
        assert!(one.contains(&"192.0.2.7".parse::<IpAddr>().unwrap()));
        assert!(one.iter().filter(|a| !a.is_loopback()).count() == 1);
        assert!(bind_addrs("nonsense").is_empty());
    }

    #[test]
    fn a_scoped_bind_stays_on_loopback_and_skips_busy_ports() {
        let loop_only = bind_addrs("loopback");
        let (first, port, _) = bind_from(38_500, 5, &loop_only).unwrap();
        assert!(first
            .iter()
            .all(|l| l.local_addr().unwrap().ip().is_loopback()));
        // The same base again takes the next port.
        let (_second, next, _) = bind_from(port, 5, &loop_only).unwrap();
        assert_ne!(port, next);
    }
}
