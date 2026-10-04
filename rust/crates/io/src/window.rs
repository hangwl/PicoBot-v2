//! The game window: found by title, measured by its client area.
//!
//! Every pixel coordinate below this layer is client-relative: captures
//! never include the OS title bar or borders. The handle is re-found by
//! title when the game restarts (a dead handle would otherwise break every
//! capture until the host restarts).

use std::sync::Once;
use std::time::{Duration, Instant};

use windows::core::BOOL;
use windows::Win32::Foundation::{HWND, LPARAM, POINT, RECT};
use windows::Win32::Graphics::Gdi::ClientToScreen;
use windows::Win32::UI::HiDpi::{
    SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
};
use windows::Win32::UI::WindowsAndMessaging::{
    EnumWindows, GetClientRect, GetForegroundWindow, GetWindowTextLengthW, GetWindowTextW,
    IsIconic, IsWindow, IsWindowVisible, SetForegroundWindow, ShowWindow, SW_RESTORE,
};

/// Work in physical pixels, as the capture does (call before any window
/// or screen query; repeated calls are harmless).
pub fn init_dpi_awareness() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| unsafe {
        let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    });
}

fn title_of(hwnd: HWND) -> String {
    unsafe {
        let len = GetWindowTextLengthW(hwnd);
        if len <= 0 {
            return String::new();
        }
        let mut buf = vec![0u16; len as usize + 1];
        let n = GetWindowTextW(hwnd, &mut buf);
        String::from_utf16_lossy(&buf[..n.max(0) as usize])
    }
}

/// Visible top-level windows with a title: `(handle, title)`.
pub fn list_windows() -> Vec<(isize, String)> {
    unsafe extern "system" fn collect(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let out = &mut *(lparam.0 as *mut Vec<(isize, String)>);
        if IsWindowVisible(hwnd).as_bool() {
            let title = title_of(hwnd);
            if !title.trim().is_empty() {
                out.push((hwnd.0 as isize, title));
            }
        }
        BOOL(1)
    }
    let mut out: Vec<(isize, String)> = Vec::new();
    unsafe {
        let _ = EnumWindows(Some(collect), LPARAM(&mut out as *mut _ as isize));
    }
    out
}

/// The window titled `title`: an exact (trimmed) match first, else the
/// first title containing it — how the Python host found it.
fn find(title: &str) -> Option<isize> {
    let windows = list_windows();
    let want = title.trim();
    windows
        .iter()
        .find(|(_, t)| t.trim() == want)
        .or_else(|| windows.iter().find(|(_, t)| t.contains(title)))
        .map(|(h, _)| *h)
}

pub struct GameWindow {
    pub title: String,
    /// The window handle, kept as an integer: `HWND` wraps a raw pointer,
    /// which isn't `Send`.
    hwnd: isize,
    last_lookup: Option<Instant>,
}

impl GameWindow {
    const RELOOKUP: Duration = Duration::from_secs(1);

    pub fn find(title: &str) -> Option<Self> {
        init_dpi_awareness();
        Some(GameWindow {
            title: title.to_owned(),
            hwnd: find(title)?,
            last_lookup: None,
        })
    }

    fn hwnd(&self) -> HWND {
        HWND(self.hwnd as *mut _)
    }

    /// The handle, re-found by title (at most once a second) if it died.
    fn live(&mut self) -> Option<HWND> {
        if unsafe { IsWindow(Some(self.hwnd())).as_bool() } {
            return Some(self.hwnd());
        }
        if self
            .last_lookup
            .is_some_and(|t| t.elapsed() < Self::RELOOKUP)
        {
            return None;
        }
        self.last_lookup = Some(Instant::now());
        self.hwnd = find(&self.title)?;
        Some(self.hwnd())
    }

    /// `(left, top, right, bottom)` of the client area in screen pixels.
    pub fn client_rect(&mut self) -> Option<(i32, i32, i32, i32)> {
        let hwnd = self.live()?;
        let mut rc = RECT::default();
        let mut pt = POINT { x: 0, y: 0 };
        unsafe {
            GetClientRect(hwnd, &mut rc).ok()?;
            if !ClientToScreen(hwnd, &mut pt).as_bool() {
                return None;
            }
        }
        Some((
            pt.x,
            pt.y,
            pt.x + rc.right - rc.left,
            pt.y + rc.bottom - rc.top,
        ))
    }

    /// Whether the game has the keyboard focus: the cached handle first,
    /// else by title (a restarted game still counts).
    pub fn is_active(&self) -> bool {
        let fg = unsafe { GetForegroundWindow() };
        if fg.0.is_null() {
            return false;
        }
        fg.0 as isize == self.hwnd
            || (!unsafe { IsWindow(Some(self.hwnd())) }.as_bool()
                && title_of(fg).trim() == self.title.trim())
    }

    /// Bring the game to the front (best effort; `is_active` is the truth).
    pub fn activate(&mut self) {
        if let Some(hwnd) = self.live() {
            unsafe {
                if IsIconic(hwnd).as_bool() {
                    let _ = ShowWindow(hwnd, SW_RESTORE);
                }
                let _ = SetForegroundWindow(hwnd);
            }
        }
    }
}
