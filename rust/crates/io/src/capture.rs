//! Screen capture with GDI: `BitBlt` the screen into a reusable DIB and
//! copy the pixels out (BGRA, top-down).
//!
//! A grabber holds raw GDI handles, so it is neither `Send` nor `Sync`:
//! the compiler keeps each thread on its own grabber (the Python host
//! needed a thread-local for the same rule). The bitmap is reused while the
//! capture size stays the same, so a steady minimap capture allocates
//! nothing on the GDI side.

use std::ptr::null_mut;

use picobot_core::vision::Image;
use windows::Win32::Graphics::Gdi::{
    BitBlt, CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GetDC, ReleaseDC,
    SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, CAPTUREBLT, DIB_RGB_COLORS, HBITMAP, HDC,
    HGDIOBJ, SRCCOPY,
};

use crate::window::init_dpi_awareness;

pub struct ScreenGrabber {
    /// Include layered (overlay) windows in the copy — slower under
    /// desktop composition; the game itself isn't layered.
    pub layered: bool,
    screen: HDC,
    mem: HDC,
    bitmap: Option<(HBITMAP, HGDIOBJ, *mut u8, i32, i32)>,
}

impl ScreenGrabber {
    pub fn new() -> Self {
        init_dpi_awareness();
        unsafe {
            let screen = GetDC(None);
            let mem = CreateCompatibleDC(Some(screen));
            ScreenGrabber {
                layered: false,
                screen,
                mem,
                bitmap: None,
            }
        }
    }

    /// A DIB of `w × h` selected into the memory DC (reused when the size
    /// is unchanged).
    fn surface(&mut self, w: i32, h: i32) -> Option<*mut u8> {
        if let Some((_, _, bits, bw, bh)) = self.bitmap {
            if (bw, bh) == (w, h) {
                return Some(bits);
            }
        }
        self.free_bitmap();
        let bmi = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: w,
                biHeight: -h, // negative: rows top-down
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        let mut bits: *mut core::ffi::c_void = null_mut();
        unsafe {
            let bmp =
                CreateDIBSection(Some(self.mem), &bmi, DIB_RGB_COLORS, &mut bits, None, 0).ok()?;
            let old = SelectObject(self.mem, HGDIOBJ(bmp.0));
            self.bitmap = Some((bmp, old, bits as *mut u8, w, h));
        }
        Some(bits as *mut u8)
    }

    fn free_bitmap(&mut self) {
        if let Some((bmp, old, ..)) = self.bitmap.take() {
            unsafe {
                SelectObject(self.mem, old);
                let _ = DeleteObject(HGDIOBJ(bmp.0));
            }
        }
    }

    /// Capture `(left, top, width, height)` of the virtual desktop.
    pub fn capture(&mut self, left: i32, top: i32, width: i32, height: i32) -> Option<Image> {
        if width <= 0 || height <= 0 {
            return None;
        }
        let bits = self.surface(width, height)?;
        unsafe {
            let rop = if self.layered {
                SRCCOPY | CAPTUREBLT
            } else {
                SRCCOPY
            };
            BitBlt(
                self.mem,
                0,
                0,
                width,
                height,
                Some(self.screen),
                left,
                top,
                rop,
            )
            .ok()?;
            let len = width as usize * height as usize * 4;
            let mut bgra = std::slice::from_raw_parts(bits, len).to_vec();
            // GDI leaves alpha undefined; make it opaque.
            for px in bgra.as_chunks_mut::<4>().0 {
                px[3] = 255;
            }
            Some(Image {
                width: width as usize,
                height: height as usize,
                bgra,
            })
        }
    }
}

impl Default for ScreenGrabber {
    fn default() -> Self {
        ScreenGrabber::new()
    }
}

impl Drop for ScreenGrabber {
    fn drop(&mut self) {
        self.free_bitmap();
        unsafe {
            let _ = DeleteDC(self.mem);
            ReleaseDC(None, self.screen);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn captures_a_desktop_patch_and_reuses_the_surface() {
        let mut g = ScreenGrabber::new();
        let a = g.capture(0, 0, 16, 8).expect("desktop capture");
        assert_eq!((a.width, a.height, a.bgra.len()), (16, 8, 16 * 8 * 4));
        assert!(a.bgra.as_chunks::<4>().0.iter().all(|p| p[3] == 255));
        assert!(g.capture(10, 10, 16, 8).is_some()); // same size: same DIB
        assert!(g.capture(0, 0, 0, 8).is_none());
    }
}
