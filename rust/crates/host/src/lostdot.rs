//! Evidence for detection work: the minimap crop whenever the player dot
//! can't be read while the bot runs, saved losslessly with the numbers
//! that explain the miss. Kept under `debug/frames/<ts>_dotlost/`.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use picobot_core::vision::Image;
use serde_json::{json, Value};

const DIR: &str = "debug/frames";
const SUFFIX: &str = "_dotlost";
/// Saves closer together than this are skipped (one loss is one story).
const MIN_GAP: Duration = Duration::from_secs(2);
/// Newest captures kept.
const KEEP: usize = 200;

pub struct LostDotSaver {
    root: PathBuf,
    last: Option<Instant>,
}

impl Default for LostDotSaver {
    fn default() -> Self {
        Self::at(DIR)
    }
}

impl LostDotSaver {
    pub fn at(root: impl Into<PathBuf>) -> Self {
        LostDotSaver {
            root: root.into(),
            last: None,
        }
    }

    /// Save `img` (the minimap crop) with `ctx` about what the bot was doing.
    /// Errors only cost the capture, so they are dropped.
    pub fn save(&mut self, img: &Image, player: [u8; 3], ctx: Value) {
        if self.last.is_some_and(|t| t.elapsed() < MIN_GAP) {
            return;
        }
        self.last = Some(Instant::now());
        let dir = self.root.join(format!("{}{SUFFIX}", stamp()));
        if fs::create_dir_all(&dir).is_err() {
            return;
        }
        let mut meta = pixel_stats(img, player);
        if let (Some(m), Some(c)) = (meta.as_object_mut(), ctx.as_object()) {
            m.extend(c.iter().map(|(k, v)| (k.clone(), v.clone())));
        }
        let _ = write_png(&dir.join("frame.png"), img);
        let _ = fs::write(
            dir.join("meta.json"),
            serde_json::to_string_pretty(&meta).unwrap_or_default(),
        );
        prune(&self.root);
    }
}

/// How many pixels match the dot colour at a few tolerances, and the
/// closest pixel to it: tells a missing dot from a changed colour.
fn pixel_stats(img: &Image, player: [u8; 3]) -> Value {
    let mut within = [0usize; 3];
    let tols = [10, 30, 60];
    let mut best = (i32::MAX, 0usize, 0usize);
    for y in 0..img.height {
        for x in 0..img.width {
            let px = img.bgr(x, y);
            let d = (0..3)
                .map(|i| (i32::from(px[i]) - i32::from(player[i])).abs())
                .max()
                .unwrap_or(0);
            for (n, t) in within.iter_mut().zip(tols) {
                if d <= t {
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
        "pixels_within": { "10": within[0], "30": within[1], "60": within[2] },
        "closest": { "distance": best.0, "at": [best.1, best.2], "bgr": nearest },
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

/// Drop the oldest captures beyond `KEEP`.
fn prune(root: &Path) {
    let Ok(rd) = fs::read_dir(root) else { return };
    let mut dirs: Vec<PathBuf> = rd
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.is_dir()
                && p.file_name()
                    .is_some_and(|n| n.to_string_lossy().ends_with(SUFFIX))
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
        let mut s = LostDotSaver::at(&root);
        let img = Image::new(12, 8);
        s.save(&img, [12, 240, 239], json!({ "map": "m" }));
        s.save(&img, [12, 240, 239], json!({ "map": "again" })); // too soon
        let dirs: Vec<_> = fs::read_dir(&root).unwrap().flatten().collect();
        assert_eq!(dirs.len(), 1);
        let d = dirs[0].path();
        assert!(d.to_string_lossy().ends_with(SUFFIX));
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
    fn only_the_newest_captures_are_kept() {
        let root = std::env::temp_dir().join(format!("lostdot-prune-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        for i in 0..KEEP + 3 {
            fs::create_dir_all(root.join(format!("2026{i:04}{SUFFIX}"))).unwrap();
        }
        fs::create_dir_all(root.join("other_ocr")).unwrap();
        prune(&root);
        assert_eq!(fs::read_dir(&root).unwrap().count(), KEEP + 1);
        assert!(!root.join(format!("20260000{SUFFIX}")).exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn stats_tell_a_missing_dot_from_a_recoloured_one() {
        let mut img = Image::new(10, 10);
        img.set_bgr(3, 4, [12, 200, 239]); // off by 40 on one channel
        let v = pixel_stats(&img, [12, 240, 239]);
        assert_eq!(v["pixels_within"]["10"], 0);
        assert_eq!(v["pixels_within"]["60"], 1);
        assert_eq!(v["closest"]["at"], json!([3, 4]));
        assert_eq!(v["closest"]["distance"], 40);
    }
}
