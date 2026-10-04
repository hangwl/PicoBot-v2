//! Record key timing while the game window has the focus, to compare a
//! person's typing with the bot's (the Pico's keys arrive like any other).
//!
//!     cargo run --release -p picobot-io --example key_timing -- [seconds] [out.csv] [window title]
//!
//! A low-level keyboard hook sees every key, so only presses made while the
//! game window is in front are kept (as key codes and times, in memory;
//! the CSV is written only if a path is given). Defaults: 120 s, no CSV,
//! the title from ../config.json.

use std::collections::HashMap;
use std::path::Path;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use picobot_core::config::AppConfig;
use picobot_io::window::GameWindow;
use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, GetMessageW, PostThreadMessageW, SetWindowsHookExW, UnhookWindowsHookEx,
    KBDLLHOOKSTRUCT, MSG, WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_QUIT, WM_SYSKEYDOWN,
    WM_SYSKEYUP,
};

#[derive(Clone, Copy)]
struct Ev {
    t: f64,
    vk: u32,
    down: bool,
}

struct Rec {
    start: Instant,
    window: GameWindow,
    events: Vec<Ev>,
    held: HashMap<u32, f64>,
}

static REC: OnceLock<Mutex<Rec>> = OnceLock::new();

unsafe extern "system" fn hook(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code >= 0 {
        let k = &*(lparam.0 as *const KBDLLHOOKSTRUCT);
        let msg = wparam.0 as u32;
        let down = matches!(msg, WM_KEYDOWN | WM_SYSKEYDOWN);
        let up = matches!(msg, WM_KEYUP | WM_SYSKEYUP);
        let rec = REC.get().filter(|_| down || up);
        if let Some(m) = rec {
            let mut r = m.lock().unwrap();
            if r.window.is_active() {
                let t = r.start.elapsed().as_secs_f64();
                if down {
                    // Auto-repeat is the OS's, not the typist's.
                    if r.held.insert(k.vkCode, t).is_none() {
                        r.events.push(Ev {
                            t,
                            vk: k.vkCode,
                            down,
                        });
                    }
                } else if r.held.remove(&k.vkCode).is_some() {
                    r.events.push(Ev {
                        t,
                        vk: k.vkCode,
                        down,
                    });
                }
            }
        }
    }
    CallNextHookEx(None, code, wparam, lparam)
}

fn pct(sorted: &[f64], p: f64) -> f64 {
    sorted[((sorted.len() - 1) as f64 * p).round() as usize]
}

fn line(name: &str, mut v: Vec<f64>) {
    if v.is_empty() {
        println!("{name}: none");
        return;
    }
    v.sort_by(f64::total_cmp);
    println!(
        "{name}: n={} p5={:.0} p25={:.0} p50={:.0} p75={:.0} p95={:.0} ms",
        v.len(),
        pct(&v, 0.05) * 1e3,
        pct(&v, 0.25) * 1e3,
        pct(&v, 0.5) * 1e3,
        pct(&v, 0.75) * 1e3,
        pct(&v, 0.95) * 1e3
    );
}

fn summarize(events: &[Ev]) {
    let mut down_at: HashMap<u32, f64> = HashMap::new();
    let mut holds = Vec::new();
    for e in events {
        if e.down {
            down_at.insert(e.vk, e.t);
        } else if let Some(t0) = down_at.remove(&e.vk) {
            holds.push(e.t - t0);
        }
    }
    let downs: Vec<&Ev> = events.iter().filter(|e| e.down).collect();
    let mut cross = Vec::new();
    let mut same = Vec::new();
    for w in downs.windows(2) {
        let gap = w[1].t - w[0].t;
        if gap < 0.5 {
            if w[1].vk == w[0].vk {
                same.push(gap);
            } else {
                cross.push(gap);
            }
        }
    }
    let under10 = cross.iter().filter(|g| **g < 0.010).count();
    println!("{} presses", downs.len());
    line("hold (down to up)", holds);
    line("different keys, down to down (<0.5s)", cross.clone());
    line("same key, down to down (<0.5s)", same);
    if !cross.is_empty() {
        println!(
            "different-key gaps under 10 ms: {:.1}% ({} of {})",
            100.0 * under10 as f64 / cross.len() as f64,
            under10,
            cross.len()
        );
    }
}

fn main() {
    let mut args = std::env::args().skip(1);
    let secs: f64 = args.next().and_then(|s| s.parse().ok()).unwrap_or(120.0);
    let out = args.next();
    let title = args.next().unwrap_or_else(|| {
        AppConfig::load(Path::new("../config.json"))
            .0
            .default_target_window
    });
    let window = GameWindow::find(&title).unwrap_or_else(|| panic!("no window titled {title:?}"));
    REC.set(Mutex::new(Rec {
        start: Instant::now(),
        window,
        events: Vec::new(),
        held: HashMap::new(),
    }))
    .ok();
    let thread = unsafe { GetCurrentThreadId() };
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs_f64(secs));
        unsafe {
            let _ = PostThreadMessageW(thread, WM_QUIT, WPARAM(0), LPARAM(0));
        }
    });
    println!("recording presses in {title:?} for {secs:.0} s...");
    let h = unsafe { SetWindowsHookExW(WH_KEYBOARD_LL, Some(hook), None, 0) }.expect("hook");
    let mut msg = MSG::default();
    unsafe {
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {}
        let _ = UnhookWindowsHookEx(h);
    }
    let events = REC.get().unwrap().lock().unwrap().events.clone();
    summarize(&events);
    if let Some(path) = out {
        let mut csv = String::from("t,vk,event\n");
        for e in &events {
            csv += &format!(
                "{:.4},{},{}\n",
                e.t,
                e.vk,
                if e.down { "down" } else { "up" }
            );
        }
        std::fs::write(&path, csv).expect("write csv");
        println!("wrote {path}");
    }
}
