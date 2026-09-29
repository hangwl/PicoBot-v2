//! Windows-facing I/O for the PicoBot host.

#[cfg(windows)]
pub mod capture;
pub mod hid;
#[cfg(windows)]
pub mod perf;
pub mod serial;
#[cfg(windows)]
pub mod window;
