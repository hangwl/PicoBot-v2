//! Process CPU time, for benchmarks that care about CPU rather than wall
//! time (a screen capture mostly *waits* for the compositor).

use windows::Win32::Foundation::FILETIME;
use windows::Win32::System::Threading::{GetCurrentProcess, GetProcessTimes};

/// User + kernel CPU seconds this process has used.
pub fn process_cpu_seconds() -> f64 {
    let (mut c, mut e, mut k, mut u) = (
        FILETIME::default(),
        FILETIME::default(),
        FILETIME::default(),
        FILETIME::default(),
    );
    let secs =
        |f: FILETIME| ((f.dwHighDateTime as u64) << 32 | f.dwLowDateTime as u64) as f64 * 1e-7;
    unsafe {
        if GetProcessTimes(GetCurrentProcess(), &mut c, &mut e, &mut k, &mut u).is_err() {
            return 0.0;
        }
    }
    secs(k) + secs(u)
}
