//! Title OCR: PaddleOCR's text recogniser (the ONNX model RapidOCR ships)
//! run on each title line.
//!
//! The band is segmented first ([`title_crop`]), so the recogniser only
//! sees the white title lines, never icons or map content. Each line is
//! resized to the model's 48 px height, normalised to [-1, 1] and padded
//! right; the output is decoded greedily (CTC: best class per step,
//! repeats merged, blanks dropped). No text lines, or no confident line,
//! reads as None.

use std::path::{Path, PathBuf};

use ort::session::Session;
use ort::value::Tensor;
use picobot_core::title::title_crop;
use picobot_core::vision::Image;

const HEIGHT: usize = 48;
/// The narrowest input the model is fed (its reference shape, 320 px).
const MIN_RATIO: f64 = 320.0 / 48.0;
const LINE_PAD: usize = 4;
const MARGIN: usize = 8;

/// Where the recogniser model is looked for under the data folder: a copy
/// in `models/`, else the one RapidOCR installed in the Python venv.
pub fn find_model(root: &Path) -> Option<PathBuf> {
    const NAME: &str = "PP-OCRv6_rec_small.onnx";
    [
        root.join("models").join(NAME),
        root.join(".venv/Lib/site-packages/rapidocr/models")
            .join(NAME),
    ]
    .into_iter()
    .find(|p| p.is_file())
}

/// Near-white, as the title renders.
const WHITE: u8 = 170;
/// A line whose text ends this close to the crop's edge runs into the
/// faded tail the client clips: keep it all.
const TAIL_PX: usize = 12;

/// The width to read of rows `top..bottom`: up to this line's own bright
/// text plus the margin, so faint background art beside a shorter line
/// isn't read as letters.
fn trim_width(crop: &Image, top: usize, bottom: usize) -> usize {
    let last = (0..crop.width)
        .rev()
        .find(|&x| (top..bottom).any(|y| crop.bgr(x, y).into_iter().min().unwrap_or(0) > WHITE));
    match last {
        Some(x) if crop.width - x > MARGIN + TAIL_PX => (x + 1 + MARGIN).min(crop.width),
        _ => crop.width,
    }
}

pub struct TitleReader {
    session: Session,
    /// Class index → text: blank first, a space last.
    chars: Vec<String>,
    pub min_confidence: f32,
}

impl TitleReader {
    pub fn load(model: &Path) -> Result<Self, String> {
        let session = Session::builder()
            .and_then(|b| b.with_intra_threads(2).map_err(Into::into))
            .and_then(|mut b| b.commit_from_file(model))
            .map_err(|e| format!("{}: {e}", model.display()))?;
        let list = session
            .metadata()
            .ok()
            .and_then(|m| m.custom("character"))
            .ok_or("the model carries no character list")?;
        let mut chars = vec![String::new()];
        chars.extend(list.lines().map(str::to_owned));
        chars.push(" ".into());
        Ok(TitleReader {
            session,
            chars,
            min_confidence: 0.6,
        })
    }

    /// The model's input: `[1, 3, 48, W]` floats, channels in capture
    /// order.
    fn prepare(line: &Image) -> (Vec<f32>, usize) {
        let ratio = line.width as f64 / line.height.max(1) as f64;
        let width = (HEIGHT as f64 * ratio.max(MIN_RATIO)) as usize;
        let resized_w = ((HEIGHT as f64 * ratio).ceil() as usize).clamp(1, width);
        let img = line.resize(resized_w, HEIGHT);
        let mut data = vec![0f32; 3 * HEIGHT * width];
        for c in 0..3 {
            for y in 0..HEIGHT {
                for x in 0..resized_w {
                    let v = img.bgr(x, y)[c] as f32 / 255.0;
                    data[(c * HEIGHT + y) * width + x] = (v - 0.5) / 0.5;
                }
            }
        }
        (data, width)
    }

    /// Text and mean confidence of one line image.
    pub fn recognize(&mut self, line: &Image) -> Result<(String, f32), String> {
        let (data, width) = TitleReader::prepare(line);
        let input =
            Tensor::from_array(([1usize, 3, HEIGHT, width], data)).map_err(|e| e.to_string())?;
        let outputs = self
            .session
            .run(ort::inputs![input])
            .map_err(|e| e.to_string())?;
        let (shape, probs) = outputs[0]
            .try_extract_tensor::<f32>()
            .map_err(|e| e.to_string())?;
        let (steps, classes) = (shape[1] as usize, shape[2] as usize);
        let mut text = String::new();
        let mut confs = Vec::new();
        let mut prev = usize::MAX;
        for t in 0..steps {
            let row = &probs[t * classes..(t + 1) * classes];
            let (best, p) =
                row.iter()
                    .enumerate()
                    .fold((0, f32::MIN), |b, (i, &v)| if v > b.1 { (i, v) } else { b });
            if best != 0 && best != prev {
                text.push_str(self.chars.get(best).map_or("", String::as_str));
                confs.push(p);
            }
            prev = best;
        }
        let conf = if confs.is_empty() {
            0.0
        } else {
            confs.iter().sum::<f32>() / confs.len() as f32
        };
        Ok((text, conf))
    }

    /// The title in a band capture, or None.
    pub fn read(&mut self, band: &Image) -> Option<String> {
        let (crop, rows) = title_crop(band, LINE_PAD, MARGIN)?;
        let mut parts = Vec::new();
        for (i, &(y0, y1)) in rows.iter().enumerate() {
            // Padding, but never into the neighbouring line.
            let above = if i > 0 { rows[i - 1].1 } else { 0 };
            let below = rows.get(i + 1).map_or(crop.height, |r| r.0);
            let top = y0.saturating_sub(LINE_PAD).max(above);
            let bottom = (y1 + LINE_PAD).min(below);
            let line = crop.crop(0, top, trim_width(&crop, top, bottom), bottom - top);
            if let Ok((text, conf)) = self.recognize(&line) {
                if conf >= self.min_confidence && !text.trim().is_empty() {
                    parts.push(text.trim().to_owned());
                }
            }
        }
        let name = parts.join(" ");
        (!name.is_empty()).then_some(name)
    }
}
