//! Evidence for detection and movement work, saved while the bot runs:
//! minimap crops (lossless) with the numbers and context that explain
//! them, under `debug/frames/<ts><suffix>/`:
//!
//! - `_dotlost` — the player dot couldn't be read;
//! - `_offplatform` — the player stood off every drawn platform (with an
//!   `overlay.png` of the drawn geometry and the last leg).

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use picobot_core::vision::Image;
use serde_json::{json, Value};

const DIR: &str = "debug/frames";
/// Saves closer together than this are skipped (one loss is one story).
const MIN_GAP: Duration = Duration::from_secs(2);
/// Newest captures kept, per kind.
const KEEP: usize = 200;

pub struct Evidence {
    root: PathBuf,
    suffix: &'static str,
    last: Option<Instant>,
}

impl Evidence {
    pub fn dot_lost() -> Self {
        Self::at(DIR, "_dotlost")
    }

    pub fn off_platform() -> Self {
        Self::at(DIR, "_offplatform")
    }

    pub fn at(root: impl Into<PathBuf>, suffix: &'static str) -> Self {
        Evidence {
            root: root.into(),
            suffix,
            last: None,
        }
    }

    /// Save `images` (name → picture, as `<name>.png`) and `meta`. Errors
    /// only cost the capture, so they are dropped.
    pub fn save(&mut self, images: &[(&str, &Image)], meta: &Value) {
        if self.last.is_some_and(|t| t.elapsed() < MIN_GAP) {
            return;
        }
        self.last = Some(Instant::now());
        let dir = self.root.join(format!("{}{}", stamp(), self.suffix));
        if fs::create_dir_all(&dir).is_err() {
            return;
        }
        for (name, img) in images {
            let _ = write_png(&dir.join(format!("{name}.png")), img);
        }
        let _ = fs::write(
            dir.join("meta.json"),
            serde_json::to_string_pretty(meta).unwrap_or_default(),
        );
        prune(&self.root, self.suffix);
    }
}

/// `ctx` with `more`'s fields added.
pub fn merged(mut ctx: Value, more: Value) -> Value {
    if let (Some(m), Some(c)) = (ctx.as_object_mut(), more.as_object()) {
        m.extend(c.iter().map(|(k, v)| (k.clone(), v.clone())));
    }
    ctx
}

/// How many pixels match the dot colour at the detector's tolerance and
/// looser ones, and the closest pixel to it: tells a missing dot from a
/// changed colour. Distances are the detector's: the sum of the three
/// channel differences (it accepts under 30).
pub fn pixel_stats(img: &Image, player: [u8; 3]) -> Value {
    let mut within = [0usize; 3];
    let limits = [30, 60, 120];
    let mut best = (i32::MAX, 0usize, 0usize);
    for y in 0..img.height {
        for x in 0..img.width {
            let px = img.bgr(x, y);
            let d: i32 = (0..3)
                .map(|i| (i32::from(px[i]) - i32::from(player[i])).abs())
                .sum();
            for (n, t) in within.iter_mut().zip(limits) {
                if d < t {
                    *n += 1;
                }
            }
            if d < best.0 {
                best = (d, x, y);
            }
        }
    }
    let nearest = img.bgr(best.1, best.2);
    json!({
        "size": [img.width, img.height],
        "player_bgr": player,
        "pixels_within_sum": { "30": within[0], "60": within[1], "120": within[2] },
        "closest": { "distance_sum": best.0, "at": [best.1, best.2], "bgr": nearest },
    })
}

fn write_png(path: &Path, img: &Image) -> std::io::Result<()> {
    let mut rgb = Vec::with_capacity(img.width * img.height * 3);
    for y in 0..img.height {
        for x in 0..img.width {
            let [b, g, r] = img.bgr(x, y);
            rgb.extend_from_slice(&[r, g, b]);
        }
    }
    let file = std::io::BufWriter::new(fs::File::create(path)?);
    let mut enc = png::Encoder::new(file, img.width as u32, img.height as u32);
    enc.set_color(png::ColorType::Rgb);
    enc.set_depth(png::BitDepth::Eight);
    let mut w = enc.write_header().map_err(std::io::Error::other)?;
    w.write_image_data(&rgb).map_err(std::io::Error::other)
}

/// Drop the oldest `suffix` captures beyond `KEEP`.
fn prune(root: &Path, suffix: &str) {
    let Ok(rd) = fs::read_dir(root) else { return };
    let mut dirs: Vec<PathBuf> = rd
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.is_dir()
                && p.file_name()
                    .is_some_and(|n| n.to_string_lossy().ends_with(suffix))
        })
        .collect();
    dirs.sort();
    let extra = dirs.len().saturating_sub(KEEP);
    for d in &dirs[..extra] {
        let _ = fs::remove_dir_all(d);
    }
}

/// `YYYYMMDD-HHMMSS-mmm` (UTC), so names sort by time.
fn stamp() -> String {
    let d = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let (secs, ms) = (d.as_secs() as i64, d.subsec_millis());
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    // Civil date from days since 1970-01-01 (Hinnant).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}{month:02}{day:02}-{:02}{:02}{:02}-{ms:03}",
        rem / 3600,
        rem / 60 % 60,
        rem % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stamps_sort_and_have_the_expected_shape() {
        let s = stamp();
        assert_eq!(s.len(), 19);
        assert!(s.starts_with("20"));
    }

    #[test]
    fn a_loss_is_saved_as_a_png_with_its_numbers_and_throttled() {
        let root = std::env::temp_dir().join(format!("lostdot-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let mut s = Evidence::at(&root, "_dotlost");
        let img = Image::new(12, 8);
        let meta = |map| merged(pixel_stats(&img, [12, 240, 239]), json!({ "map": map }));
        s.save(&[("frame", &img)], &meta("m"));
        s.save(&[("frame", &img)], &meta("again")); // too soon
        let dirs: Vec<_> = fs::read_dir(&root).unwrap().flatten().collect();
        assert_eq!(dirs.len(), 1);
        let d = dirs[0].path();
        assert!(d.to_string_lossy().ends_with("_dotlost"));
        let meta: Value =
            serde_json::from_str(&fs::read_to_string(d.join("meta.json")).unwrap()).unwrap();
        assert_eq!(
            (meta["map"].as_str(), meta["size"][0].as_u64()),
            (Some("m"), Some(12))
        );
        assert_eq!(&fs::read(d.join("frame.png")).unwrap()[1..4], b"PNG");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn the_stats_use_the_detectors_distance() {
        let mut img = Image::new(4, 1);
        img.set_bgr(0, 0, [0, 239, 254]); // the real dot vs the configured colour
        let s = pixel_stats(&img, [12, 240, 239]);
        assert_eq!(s["closest"]["distance_sum"], 28);
        assert_eq!(s["pixels_within_sum"]["30"], 1);
    }

    #[test]
    fn only_the_newest_captures_of_a_kind_are_kept() {
        let root = std::env::temp_dir().join(format!("lostdot-prune-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        for i in 0..KEEP + 3 {
            fs::create_dir_all(root.join(format!("2026{i:04}_dotlost"))).unwrap();
        }
        fs::create_dir_all(root.join("other_ocr")).unwrap();
        fs::create_dir_all(root.join("1_offplatform")).unwrap();
        prune(&root, "_dotlost");
        assert_eq!(fs::read_dir(&root).unwrap().count(), KEEP + 2);
        assert!(!root.join("20260000_dotlost").exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn stats_tell_a_missing_dot_from_a_recoloured_one() {
        let mut img = Image::new(10, 10);
        img.set_bgr(3, 4, [12, 200, 239]); // off by 40 on one channel
        let v = pixel_stats(&img, [12, 240, 239]);
        assert_eq!(v["pixels_within_sum"]["30"], 0);
        assert_eq!(v["pixels_within_sum"]["60"], 1);
        assert_eq!(v["closest"]["at"], json!([3, 4]));
        assert_eq!(v["closest"]["distance_sum"], 40);
    }
}
