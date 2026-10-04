//! Record key timing while the game window has the focus, per keyboard, to
//! compare a person's typing with the bot's (the Pico is a second
//! keyboard to Windows).
//!
//!     cargo run --release -p picobot-io --example key_timing -- [seconds] [out.csv] [delay] [window title]
//!
//! Uses Raw Input (a global keyboard hook sees nothing while the game
//! runs). Only presses made while the game window is in front are kept, as
//! key codes and times in memory; the CSV is written only if a path is
//! given (`-` skips it). Recording starts after `delay` seconds (default
//! 10), so there is time to click into the game after launching it.
//! Defaults: 120 s, no CSV, the title from ../config.json.

use std::collections::HashMap;
use std::path::Path;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use picobot_core::config::AppConfig;
use picobot_io::window::GameWindow;
use windows::core::w;
use windows::Win32::Foundation::{HANDLE, HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::Input::{
    GetRawInputData, GetRawInputDeviceInfoW, RegisterRawInputDevices, HRAWINPUT, RAWINPUT,
    RAWINPUTDEVICE, RAWINPUTHEADER, RIDEV_INPUTSINK, RIDI_DEVICENAME, RID_INPUT, RIM_TYPEKEYBOARD,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW, PostThreadMessageW,
    RegisterClassW, HWND_MESSAGE, MSG, WINDOW_EX_STYLE, WINDOW_STYLE, WM_INPUT, WM_QUIT, WNDCLASSW,
};

const RI_KEY_BREAK: u16 = 1;

#[derive(Clone, Copy)]
struct Ev {
    t: f64,
    dev: u32,
    vk: u32,
    down: bool,
}

struct Rec {
    start: Instant,
    window: GameWindow,
    events: Vec<Ev>,
    held: HashMap<(u32, u32), f64>,
    /// Raw device handle → 1-based index, and its name.
    devices: HashMap<isize, u32>,
    names: Vec<String>,
    seen: u32,
    elsewhere: u32,
}

static REC: OnceLock<Mutex<Rec>> = OnceLock::new();

fn device_name(h: HANDLE) -> String {
    unsafe {
        let mut size = 0u32;
        GetRawInputDeviceInfoW(Some(h), RIDI_DEVICENAME, None, &mut size);
        let mut buf = vec![0u16; size as usize + 1];
        let n = GetRawInputDeviceInfoW(
            Some(h),
            RIDI_DEVICENAME,
            Some(buf.as_mut_ptr() as *mut _),
            &mut size,
        );
        if n == u32::MAX {
            return "?".into();
        }
        String::from_utf16_lossy(&buf[..(n as usize).min(buf.len())])
            .trim_end_matches('\0')
            .to_owned()
    }
}

unsafe fn on_input(lparam: LPARAM) {
    let mut size = 0u32;
    let head = std::mem::size_of::<RAWINPUTHEADER>() as u32;
    GetRawInputData(HRAWINPUT(lparam.0 as _), RID_INPUT, None, &mut size, head);
    let mut buf = vec![0u8; size as usize];
    let got = GetRawInputData(
        HRAWINPUT(lparam.0 as _),
        RID_INPUT,
        Some(buf.as_mut_ptr() as *mut _),
        &mut size,
        head,
    );
    if got == u32::MAX || (got as usize) < std::mem::size_of::<RAWINPUTHEADER>() {
        return;
    }
    let raw = &*(buf.as_ptr() as *const RAWINPUT);
    if raw.header.dwType != RIM_TYPEKEYBOARD.0 {
        return;
    }
    let k = raw.data.keyboard;
    let down = k.Flags & RI_KEY_BREAK == 0;
    let Some(m) = REC.get() else { return };
    let mut r = m.lock().unwrap();
    r.seen += 1;
    if !r.window.is_active() {
        r.elsewhere += 1;
        return;
    }
    let handle = raw.header.hDevice;
    let key = handle.0 as isize;
    let dev = match r.devices.get(&key) {
        Some(d) => *d,
        None => {
            let d = r.devices.len() as u32 + 1;
            r.devices.insert(key, d);
            r.names.push(device_name(handle));
            d
        }
    };
    let t = r.start.elapsed().as_secs_f64();
    let vk = k.VKey as u32;
    if down {
        // Auto-repeat is the OS's, not the typist's.
        if r.held.insert((dev, vk), t).is_none() {
            r.events.push(Ev { t, dev, vk, down });
        }
    } else if r.held.remove(&(dev, vk)).is_some() {
        r.events.push(Ev { t, dev, vk, down });
    }
}

unsafe extern "system" fn wndproc(hwnd: HWND, msg: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    if msg == WM_INPUT {
        on_input(l);
    }
    DefWindowProcW(hwnd, msg, w, l)
}

fn pct(sorted: &[f64], p: f64) -> f64 {
    sorted[((sorted.len() - 1) as f64 * p).round() as usize]
}

fn line(name: &str, mut v: Vec<f64>) {
    if v.is_empty() {
        println!("  {name}: none");
        return;
    }
    v.sort_by(f64::total_cmp);
    println!(
        "  {name}: n={} p5={:.0} p25={:.0} p50={:.0} p75={:.0} p95={:.0} ms",
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
    let (mut cross, mut same) = (Vec::new(), Vec::new());
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
    println!("  {} presses", downs.len());
    line("hold (down to up)", holds);
    line("different keys, down to down (<0.5s)", cross.clone());
    line("same key, down to down (<0.5s)", same);
    if !cross.is_empty() {
        println!(
            "  different-key gaps under 10 ms: {:.1}% ({} of {})",
            100.0 * under10 as f64 / cross.len() as f64,
            under10,
            cross.len()
        );
    }
}

fn main() {
    let mut args = std::env::args().skip(1);
    let secs: f64 = args.next().and_then(|s| s.parse().ok()).unwrap_or(120.0);
    let out = args.next().filter(|p| p != "-");
    let delay: f64 = args.next().and_then(|s| s.parse().ok()).unwrap_or(10.0);
    let title = args.next().unwrap_or_else(|| {
        AppConfig::load(Path::new("../config.json"))
            .0
            .default_target_window
    });
    let window = GameWindow::find(&title).unwrap_or_else(|| panic!("no window titled {title:?}"));
    println!("starting in {delay:.0} s: click into the game now");
    std::thread::sleep(Duration::from_secs_f64(delay));
    REC.set(Mutex::new(Rec {
        start: Instant::now(),
        window,
        events: Vec::new(),
        held: HashMap::new(),
        devices: HashMap::new(),
        names: Vec::new(),
        seen: 0,
        elsewhere: 0,
    }))
    .ok();
    unsafe {
        let inst = GetModuleHandleW(None).expect("module").into();
        let class = WNDCLASSW {
            lpfnWndProc: Some(wndproc),
            hInstance: inst,
            lpszClassName: w!("picobot_key_timing"),
            ..Default::default()
        };
        RegisterClassW(&class);
        let hwnd = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("picobot_key_timing"),
            w!(""),
            WINDOW_STYLE(0),
            0,
            0,
            0,
            0,
            Some(HWND_MESSAGE),
            None,
            Some(inst),
            None,
        )
        .expect("window");
        let dev = RAWINPUTDEVICE {
            usUsagePage: 1,
            usUsage: 6,
            dwFlags: RIDEV_INPUTSINK,
            hwndTarget: hwnd,
        };
        RegisterRawInputDevices(&[dev], std::mem::size_of::<RAWINPUTDEVICE>() as u32)
            .expect("register raw input");
        let thread = GetCurrentThreadId();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs_f64(secs));
            let _ = PostThreadMessageW(thread, WM_QUIT, WPARAM(0), LPARAM(0));
        });
        println!("recording presses in {title:?} for {secs:.0} s...");
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            DispatchMessageW(&msg);
        }
    }
    let r = REC.get().unwrap().lock().unwrap();
    println!(
        "raw input saw {} key events; {} were while another window had the focus",
        r.seen, r.elsewhere
    );
    for (i, name) in r.names.iter().enumerate() {
        let dev = i as u32 + 1;
        println!("keyboard {dev}: {name}");
        let mine: Vec<Ev> = r.events.iter().copied().filter(|e| e.dev == dev).collect();
        summarize(&mine);
    }
    if let Some(path) = out {
        let mut csv = String::from("t,keyboard,vk,event\n");
        for e in &r.events {
            csv += &format!(
                "{:.4},{},{},{}\n",
                e.t,
                e.dev,
                e.vk,
                if e.down { "down" } else { "up" }
            );
        }
        std::fs::write(&path, csv).expect("write csv");
        println!("wrote {path}");
    }
}
