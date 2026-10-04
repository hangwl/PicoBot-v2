//! How far apart do two key presses arrive at Windows when the host sends
//! them `gap` ms apart? Sends F8 then F9 pairs through the Pico and reads
//! the arrival times back with Raw Input (only those two keys are kept).
//!
//!     cargo run --release -p picobot-io --example chord_probe -- COM8 [reps]
//!
//! Focus an empty Notepad first: the keys go to whatever is in front.
//! "acked" waits for each command's ACK (the real path); "piped" sends the
//! second command without waiting for the first. "hid" goes through
//! `HidController` with every different-key pair a chord, as the bot does
//! when `chord_gap_chance` fires (the asked gap is ignored).

use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use picobot_core::timing::{key_gap_for, monotonic};
use picobot_io::hid::HidController;
use picobot_io::serial::SerialLink;
use windows::core::w;
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::Input::{
    GetRawInputData, RegisterRawInputDevices, HRAWINPUT, RAWINPUT, RAWINPUTDEVICE, RAWINPUTHEADER,
    RIDEV_INPUTSINK, RID_INPUT, RIM_TYPEKEYBOARD,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW, PostThreadMessageW,
    RegisterClassW, HWND_MESSAGE, MSG, WINDOW_EX_STYLE, WINDOW_STYLE, WM_INPUT, WM_QUIT, WNDCLASSW,
};

const VK_A: u16 = 0x77; // F8
const VK_B: u16 = 0x78; // F9

static START: OnceLock<Instant> = OnceLock::new();
static SEEN: Mutex<Vec<(f64, u16)>> = Mutex::new(Vec::new());

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
    if got == u32::MAX {
        return;
    }
    let raw = &*(buf.as_ptr() as *const RAWINPUT);
    if raw.header.dwType != RIM_TYPEKEYBOARD.0 {
        return;
    }
    let k = raw.data.keyboard;
    if k.Flags & 1 == 0 && (k.VKey == VK_A || k.VKey == VK_B) {
        let t = START.get().unwrap().elapsed().as_secs_f64();
        SEEN.lock().unwrap().push((t, k.VKey));
    }
}

unsafe extern "system" fn wndproc(hwnd: HWND, msg: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    if msg == WM_INPUT {
        on_input(l);
    }
    DefWindowProcW(hwnd, msg, w, l)
}

/// Sleep until `t`, spinning the last stretch (thread sleeps overshoot).
fn wait_until(t: Instant) {
    while let Some(left) = t.checked_duration_since(Instant::now()) {
        if left > Duration::from_millis(3) {
            std::thread::sleep(left - Duration::from_millis(2));
        } else {
            std::hint::spin_loop();
        }
    }
}

struct Case {
    mode: &'static str,
    gap_ms: f64,
}

fn main() {
    let mut args = std::env::args().skip(1);
    let port = args.next().expect("port, e.g. COM8");
    let reps: usize = args.next().and_then(|s| s.parse().ok()).unwrap_or(25);
    START.set(Instant::now()).ok();
    let link = Arc::new(SerialLink::open(&port).expect("open the port"));
    assert!(link.wait_ready(Duration::from_secs(3)), "no PICO_READY");

    let cases: Vec<Case> = [
        ("acked", 0.0),
        ("acked", 5.0),
        ("acked", 8.0),
        ("acked", 12.0),
        ("acked", 20.0),
        ("piped", 0.0),
        ("piped", 3.0),
        ("piped", 6.0),
        ("hid", 0.0),
    ]
    .iter()
    .map(|&(mode, gap_ms)| Case { mode, gap_ms })
    .collect();

    unsafe {
        let inst = GetModuleHandleW(None).expect("module").into();
        let class = WNDCLASSW {
            lpfnWndProc: Some(wndproc),
            hInstance: inst,
            lpszClassName: w!("picobot_chord_probe"),
            ..Default::default()
        };
        RegisterClassW(&class);
        let hwnd = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("picobot_chord_probe"),
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
    }
    println!("sending F8/F9 pairs in 3 s: focus an empty Notepad now");
    std::thread::sleep(Duration::from_secs(3));

    let thread = unsafe { GetCurrentThreadId() };
    let sender = std::thread::spawn(move || {
        let l = link.clone();
        let mut hid = HidController::with(
            move |p| l.send_acked(p, Duration::from_millis(500)).is_ok(),
            |s| std::thread::sleep(Duration::from_secs_f64(s.max(0.0))),
            Some(Box::new(|same| key_gap_for(same, 1.0))),
            monotonic,
        );
        // (case index, rep) → index of the first event in SEEN's order.
        let mut order = Vec::new();
        for (ci, c) in cases.iter().enumerate() {
            for _ in 0..reps {
                let n0 = SEEN.lock().unwrap().len();
                let first = "hid|key|down|f8";
                let second = "hid|key|down|f9";
                let t0 = Instant::now();
                let at = t0 + Duration::from_secs_f64(c.gap_ms / 1000.0);
                if c.mode == "hid" {
                    hid.key_down("f8");
                    hid.key_down("f9");
                    std::thread::sleep(Duration::from_millis(40));
                    hid.key_up("f8");
                    hid.key_up("f9");
                    std::thread::sleep(Duration::from_millis(150));
                    order.push((ci, n0));
                    continue;
                }
                if c.mode == "acked" {
                    let _ = link.send_acked(first, Duration::from_millis(500));
                    wait_until(at);
                    let _ = link.send_acked(second, Duration::from_millis(500));
                } else {
                    let _ = link.send(first);
                    wait_until(at);
                    let _ = link.send(second);
                }
                std::thread::sleep(Duration::from_millis(40));
                let _ = link.send_acked("hid|key|up|f8", Duration::from_millis(500));
                let _ = link.send_acked("hid|key|up|f9", Duration::from_millis(500));
                std::thread::sleep(Duration::from_millis(150));
                order.push((ci, n0));
            }
        }
        std::thread::sleep(Duration::from_millis(300));
        unsafe {
            let _ = PostThreadMessageW(thread, WM_QUIT, WPARAM(0), LPARAM(0));
        }
        (cases, order)
    });
    unsafe {
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            DispatchMessageW(&msg);
        }
    }
    let (cases, order) = sender.join().unwrap();
    let seen = SEEN.lock().unwrap().clone();
    println!("{} key-down events arrived\n", seen.len());
    println!("mode   asked(ms)  arrived gap F8→F9 (ms): min / median / max   pairs");
    for (ci, c) in cases.iter().enumerate() {
        let mut gaps = Vec::new();
        for &(i, n0) in order.iter().filter(|(i, _)| *i == ci) {
            let _ = i;
            let a = seen.get(n0);
            let b = seen.get(n0 + 1);
            if let (Some(a), Some(b)) = (a, b) {
                if a.1 == VK_A && b.1 == VK_B {
                    gaps.push((b.0 - a.0) * 1e3);
                }
            }
        }
        gaps.sort_by(f64::total_cmp);
        if gaps.is_empty() {
            println!("{:<6} {:>8.0}   none arrived", c.mode, c.gap_ms);
            continue;
        }
        println!(
            "{:<6} {:>8.0}   {:>6.1} / {:>6.1} / {:>6.1}   {}",
            c.mode,
            c.gap_ms,
            gaps[0],
            gaps[gaps.len() / 2],
            gaps[gaps.len() - 1],
            gaps.len()
        );
    }
}
