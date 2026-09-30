//! The view stream's pictures: overlays drawn into the capture, the panel
//! (title band over the minimap), the title verification view, and the
//! wire frame.
//!
//! Wire format: `b"PBF1"` | u32 BE JSON length | JSON meta | JPEG.

use picobot_core::bot::LegViz;
use picobot_core::title::{title_scan, ScanOptions};
use picobot_core::vision::{Image, Region};
use serde_json::Value;

type Bgr = [u8; 3];

/// Everything drawn over a minimap-space image (minimap px).
#[derive(Debug, Clone, Default)]
pub struct Overlay {
    pub platforms: Vec<[f64; 4]>,
    pub ropes: Vec<[f64; 4]>,
    pub nav_edges: Vec<LegViz>,
    pub nav_plan: Vec<LegViz>,
    pub nav_route: Vec<LegViz>,
    pub anchors: Vec<(f64, f64)>,
    pub player: Option<(f64, f64)>,
    pub player_box: Option<(i32, i32, i32, i32)>,
    pub target: Option<(f64, f64)>,
    pub rune: Option<(f64, f64)>,
    pub hazard: Option<String>,
}

impl Overlay {
    /// Move everything by (dx, dy): the image is a panel or a whole window
    /// with the minimap at that offset.
    pub fn shift(&mut self, dx: f64, dy: f64) {
        if dx == 0.0 && dy == 0.0 {
            return;
        }
        for s in self.platforms.iter_mut().chain(self.ropes.iter_mut()) {
            *s = [s[0] + dx, s[1] + dy, s[2] + dx, s[3] + dy];
        }
        for l in self
            .nav_edges
            .iter_mut()
            .chain(self.nav_plan.iter_mut())
            .chain(self.nav_route.iter_mut())
        {
            *l = (l.0.clone(), l.1 + dx, l.2 + dy, l.3 + dx, l.4 + dy);
        }
        for p in self.anchors.iter_mut() {
            *p = (p.0 + dx, p.1 + dy);
        }
        for p in [&mut self.player, &mut self.target, &mut self.rune]
            .into_iter()
            .flatten()
        {
            *p = (p.0 + dx, p.1 + dy);
        }
        if let Some(b) = self.player_box.as_mut() {
            let (dx, dy) = (dx as i32, dy as i32);
            *b = (b.0 + dx, b.1 + dy, b.2 + dx, b.3 + dy);
        }
    }

    /// Only the non-minimap parts (the title view).
    pub fn clear_minimap(&mut self) {
        let hazard = self.hazard.take();
        *self = Overlay {
            hazard,
            ..Default::default()
        };
    }
}

fn put(img: &mut Image, x: i64, y: i64, c: Bgr) {
    if x >= 0 && y >= 0 && (x as usize) < img.width && (y as usize) < img.height {
        img.set_bgr(x as usize, y as usize, c);
    }
}

/// Outline of the rectangle `x..x+w`, `y..y+h`, clipped.
fn rect(img: &mut Image, x: i64, y: i64, w: i64, h: i64, c: Bgr) {
    let (x0, x1) = (x.max(0), (x + w).min(img.width as i64));
    let (y0, y1) = (y.max(0), (y + h).min(img.height as i64));
    if x1 <= x0 || y1 <= y0 {
        return;
    }
    for xx in x0..x1 {
        put(img, xx, y0, c);
        put(img, xx, y1 - 1, c);
    }
    for yy in y0..y1 {
        put(img, x0, yy, c);
        put(img, x1 - 1, yy, c);
    }
}

/// A square of radius `r` around (cx, cy).
fn square(img: &mut Image, cx: i64, cy: i64, r: i64, c: Bgr) {
    rect(img, cx - r, cy - r, 2 * r + 1, 2 * r + 1, c);
}

/// Outline inclusive bounds with a margin.
fn frame(img: &mut Image, b: (i32, i32, i32, i32), c: Bgr) {
    let (x0, y0, x1, y1) = (
        b.0 as i64 - 1,
        b.1 as i64 - 1,
        b.2 as i64 + 1,
        b.3 as i64 + 1,
    );
    let (x0, y0) = (x0.max(0), y0.max(0));
    let (x1, y1) = (x1.min(img.width as i64 - 1), y1.min(img.height as i64 - 1));
    if x0 > x1 || y0 > y1 {
        return;
    }
    rect(img, x0, y0, x1 - x0 + 1, y1 - y0 + 1, c);
}

fn cross(img: &mut Image, cx: i64, cy: i64, r: i64, c: Bgr) {
    for i in -r..=r {
        put(img, cx + i, cy, c);
        put(img, cx, cy + i, c);
    }
}

/// Translucent fill + solid border.
fn zone(img: &mut Image, x0: i64, y0: i64, x1: i64, y1: i64, c: Bgr) {
    let (x0, x1) = (x0.max(0), x1.min(img.width as i64));
    let (y0, y1) = (y0.max(0), y1.min(img.height as i64));
    if x1 <= x0 || y1 <= y0 {
        return;
    }
    for y in y0..y1 {
        for x in x0..x1 {
            let p = img.bgr(x as usize, y as usize);
            let m = |i: usize| (p[i] as f32 * 0.7 + c[i] as f32 * 0.3) as u8;
            img.set_bgr(x as usize, y as usize, [m(0), m(1), m(2)]);
        }
    }
    rect(img, x0, y0, x1 - x0, y1 - y0, c);
}

/// A 2px-thick segment, so it reads on the stream.
fn line(img: &mut Image, s: [f64; 4], c: Bgr) {
    let [x0, y0, x1, y1] = s;
    let n = (x1 - x0).abs().max((y1 - y0).abs()) as i64 + 1;
    for i in 0..=n {
        let t = i as f64 / n as f64;
        let x = (x0 + (x1 - x0) * t).round() as i64;
        let y = (y0 + (y1 - y0) * t).round() as i64;
        put(img, x, y, c);
        put(img, x, y + 1, c);
    }
}

fn nav_color(kind: &str) -> Bgr {
    match kind {
        "down_jump" => [0, 140, 255],
        "drop" => [0, 90, 200],
        "jump" => [255, 80, 255],
        "flash" => [200, 60, 200],
        "double_flash" => [160, 40, 160],
        "up_flash" => [80, 220, 80],
        "up_side_flash" => [140, 255, 140],
        "rope_lift" => [40, 160, 40],
        "climb_up" => [50, 150, 50],
        "climb_down" => [90, 90, 40],
        _ => [200, 200, 200],
    }
}

/// The 6x6 player glyph standing at feet (x, y), inclusive bounds.
fn glyph(x: f64, y: f64) -> (i32, i32, i32, i32) {
    let (x, y) = (x as i32, y as i32);
    (x - 3, y - 5, x + 2, y)
}

fn seg(l: &LegViz) -> [f64; 4] {
    [l.1, l.2, l.3, l.4]
}

/// Draw the overlay onto `img`.
pub fn annotate(img: &mut Image, o: &Overlay) {
    for s in &o.platforms {
        line(img, *s, [255, 200, 40]);
    }
    for s in &o.ropes {
        line(img, *s, [40, 90, 160]);
    }
    for l in &o.nav_edges {
        line(img, seg(l), nav_color(&l.0));
    }
    for l in &o.nav_plan {
        line(img, seg(l), [60, 170, 170]);
    }
    for l in &o.nav_route {
        line(img, seg(l), [0, 255, 255]);
        line(img, [l.1 + 1.0, l.2, l.3 + 1.0, l.4], [0, 255, 255]);
    }
    for &(x, y) in &o.anchors {
        frame(img, glyph(x, y), [255, 128, 0]);
    }
    if let Some((x, y)) = o.player {
        frame(
            img,
            o.player_box.unwrap_or_else(|| glyph(x, y)),
            [0, 255, 0],
        );
    }
    if let Some((x, y)) = o.target {
        cross(img, x as i64, y as i64, 4, [0, 0, 255]);
    }
    let mid = img.width as i64 / 2;
    match (o.rune, o.hazard.as_deref()) {
        (Some((x, y)), _) => square(img, x as i64, y as i64, 4, [255, 0, 255]),
        (None, Some("rune")) => square(img, mid, 6, 5, [255, 0, 255]),
        _ => {}
    }
    if o.hazard.as_deref() == Some("other players") {
        square(img, mid, 14, 5, [0, 255, 255]);
    }
}

/// The title band and the minimap composited into one panel image:
/// returns it with the minimap's offset in it. `band_xy` is the band's
/// top-left in window coordinates. The panel reaches the end of the
/// title text (long names aren't clipped by the map's width); the title
/// lines are boxed, the zone OCR reads is shaded, and a separator marks
/// the name|map boundary.
pub fn assemble_panel(
    band: Option<&Image>,
    map: &Image,
    region: Region,
    band_xy: (i32, i32),
) -> (Image, i64, i64) {
    const PAD: i64 = 6;
    let (rx, ry, rw, rh) = (
        region.0 as i64,
        region.1 as i64,
        region.2 as i64,
        region.3 as i64,
    );
    let (bx, by) = (band_xy.0 as i64, band_xy.1 as i64);
    let (lines, div) = match band {
        Some(b) if b.width > 0 && b.height > 0 => {
            let (lines, div) = title_scan(b, &ScanOptions::default());
            let lines: Vec<(i64, i64, i64, i64)> = lines
                .iter()
                .map(|l| {
                    (
                        l.0 as i64 + bx,
                        l.1 as i64 + by,
                        l.2 as i64 + bx,
                        l.3 as i64 + by,
                    )
                })
                .collect();
            (
                lines,
                div.map(|d| (d.0 as i64 + by, d.1 as i64 + bx, d.2 as i64 + bx)),
            )
        }
        _ => (Vec::new(), None),
    };
    let text = (!lines.is_empty()).then(|| {
        (
            lines.iter().map(|l| l.0).min().unwrap_or(0),
            lines.iter().map(|l| l.1).min().unwrap_or(0),
            lines.iter().map(|l| l.2).max().unwrap_or(0),
            lines.iter().map(|l| l.3).max().unwrap_or(0),
        )
    });
    let (x0, mut x1, y0) = match text {
        Some((tx0, ty0, tx1, _)) => (
            rx.min(tx0 - PAD).max(0),
            (rx + rw).max(tx1 + PAD),
            ry.min(ty0 - PAD).max(0),
        ),
        None => (rx, rx + rw, ry),
    };
    if let (Some(_), Some(d)) = (text, div) {
        // The divider's end is where the game clips the title.
        x1 = x1.max(d.2 + PAD);
    }
    let y1 = ry + rh;
    let (cw, ch) = ((x1 - x0).max(0) as usize, (y1 - y0).max(0) as usize);
    let mut canvas = Image::new(cw, ch);
    let mut blit = |src: &Image, sx0: i64, sy0: i64| {
        for y in 0..src.height as i64 {
            for x in 0..src.width as i64 {
                let (cx, cy) = (sx0 + x - x0, sy0 + y - y0);
                if cx >= 0 && cy >= 0 && (cx as usize) < cw && (cy as usize) < ch {
                    canvas.set_bgr(cx as usize, cy as usize, src.bgr(x as usize, y as usize));
                }
            }
        }
    };
    if let Some(b) = band {
        blit(b, bx, by);
    }
    blit(map, rx, ry);
    for l in &lines {
        rect(
            &mut canvas,
            l.0 - x0,
            l.1 - y0,
            l.2 - l.0,
            l.3 - l.1,
            [0, 255, 0],
        );
    }
    if let Some((tx0, ty0, tx1, ty1)) = text {
        zone(
            &mut canvas,
            tx0 - x0,
            ty0 - y0,
            tx1 - x0,
            ty1 - y0,
            [0, 120, 255],
        );
    }
    let sep = match (div, text) {
        (Some(d), _) if (y0..=y1).contains(&d.0) => Some(d.0),
        (_, Some(t)) => Some(t.3 + PAD),
        _ => None,
    };
    if let Some(s) = sep {
        for sy in [s - y0, s - y0 + 1] {
            for x in 0..cw as i64 {
                put(&mut canvas, x, sy, [255, 200, 40]);
            }
        }
    }
    rect(&mut canvas, rx - x0, ry - y0, rw, rh, [0, 255, 255]);
    (canvas, rx - x0, ry - y0)
}

/// The title verification view: text lines boxed, the OCR zone shaded.
pub fn annotate_title(band: &Image) -> Image {
    let mut out = band.clone();
    let (lines, _) = title_scan(band, &ScanOptions::default());
    for l in &lines {
        let (x0, y0, x1, y1) = (l.0 as i64, l.1 as i64, l.2 as i64, l.3 as i64);
        rect(&mut out, x0, y0, x1 - x0, y1 - y0, [0, 255, 0]);
    }
    if !lines.is_empty() {
        let x0 = lines.iter().map(|l| l.0).min().unwrap_or(0) as i64;
        let y0 = lines.iter().map(|l| l.1).min().unwrap_or(0) as i64;
        let x1 = lines.iter().map(|l| l.2).max().unwrap_or(0) as i64;
        let y1 = lines.iter().map(|l| l.3).max().unwrap_or(0) as i64;
        zone(&mut out, x0, y0, x1, y1, [0, 120, 255]);
    }
    out
}

pub fn encode_jpeg(img: &Image, quality: u8) -> Option<Vec<u8>> {
    if img.width == 0
        || img.height == 0
        || img.width > u16::MAX as usize
        || img.height > u16::MAX as usize
    {
        return None;
    }
    let mut out = Vec::new();
    jpeg_encoder::Encoder::new(&mut out, quality)
        .encode(
            &img.bgra,
            img.width as u16,
            img.height as u16,
            jpeg_encoder::ColorType::Bgra,
        )
        .ok()?;
    Some(out)
}

pub fn pack_frame(meta: &Value, jpeg: &[u8]) -> Vec<u8> {
    let head = meta.to_string().into_bytes();
    let mut out = Vec::with_capacity(8 + head.len() + jpeg.len());
    out.extend_from_slice(b"PBF1");
    out.extend_from_slice(&(head.len() as u32).to_be_bytes());
    out.extend_from_slice(&head);
    out.extend_from_slice(jpeg);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn frames_pack_magic_length_meta_jpeg() {
        let f = pack_frame(&json!({"event": "frame"}), b"JPG");
        assert_eq!(&f[..4], b"PBF1");
        let n = u32::from_be_bytes(f[4..8].try_into().unwrap()) as usize;
        assert_eq!(&f[8..8 + n], br#"{"event":"frame"}"#);
        assert_eq!(&f[8 + n..], b"JPG");
    }

    #[test]
    fn jpeg_encodes_bgra() {
        let mut img = Image::new(16, 8);
        img.set_bgr(3, 3, [0, 0, 255]);
        let j = encode_jpeg(&img, 70).unwrap();
        assert_eq!(&j[..2], &[0xFF, 0xD8]);
        assert!(encode_jpeg(&Image::new(0, 0), 70).is_none());
    }

    #[test]
    fn overlays_draw_clipped_and_shift() {
        let mut img = Image::new(40, 30);
        let mut o = Overlay {
            platforms: vec![[-5.0, 10.0, 60.0, 10.0]],
            anchors: vec![(20.0, 20.0)],
            player: Some((0.0, 0.0)),
            target: Some((39.0, 29.0)),
            hazard: Some("rune".into()),
            ..Default::default()
        };
        annotate(&mut img, &o);
        assert_eq!(img.bgr(0, 10), [255, 200, 40]);
        assert_eq!(img.bgr(39, 11), [255, 200, 40]);
        assert_eq!(img.bgr(16, 20), [255, 128, 0]); // anchor frame, left side
        o.shift(2.0, 3.0);
        assert_eq!(o.platforms[0], [-3.0, 13.0, 62.0, 13.0]);
        assert_eq!(o.anchors[0], (22.0, 23.0));
        o.clear_minimap();
        assert!(o.platforms.is_empty() && o.hazard.is_some());
    }

    #[test]
    fn a_panel_without_a_title_is_the_minimap() {
        let map = Image::new(30, 20);
        let (p, dx, dy) = assemble_panel(None, &map, (10, 40, 30, 20), (0, 0));
        assert_eq!((p.width, p.height, dx, dy), (30, 20, 0, 0));
        assert_eq!(p.bgr(0, 0), [0, 255, 255]); // the map-area box
    }
}
