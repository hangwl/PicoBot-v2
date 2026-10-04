//! The lie detector's window, found by its title: "LIE DETECTOR" in the
//! game's yellow-green header text, anywhere in the game window. Found,
//! the bot pauses and alerts — the mini-game is the player's to solve.

use crate::vision::Image;

/// The title's pixels at the game's 1366x768 client size (`#` = text).
const TITLE: [&str; 9] = [
    "##.....##..#####.....#####...#####..######.#####...####..######...###....######.",
    "##.....##..#####.....######..#####..######.#####..#####..######..#####...#######",
    "##.....##..##........##..##..##.......##...##.....##.......##...##...##..##...##",
    "##.....##..##........##..##..##.......##...##.....##.......##...##...##..##...##",
    "##.....##..#####.....##..##..#####....##...#####..##.......##...##...##..##..###",
    "##.....##..#####.....##..##..#####....##...#####..##.......##...##...##..######.",
    "##.....##..##........##..##..##.......##...##.....##.......##...##...##..##..##.",
    "#####..##..#####.....######..#####....##...#####..#####....##....#####...##...##",
    "#####..##..#####.....#####...#####....##...#####...####....##.....###....##...##",
];
/// Share of the title's text pixels that must show, and the most of its
/// gaps that may be text-coloured.
const HIT: f64 = 0.85;
const STRAY: f64 = 0.1;

/// The header text's colour: green over red, almost no blue.
fn is_title_px(b: u8, g: u8, r: u8) -> bool {
    g >= 190 && r >= 160 && g > r && b <= 60
}

/// Top-left of the title's first letter, if the window shows it.
pub fn find_lie_detector(img: &Image) -> Option<(usize, usize)> {
    let (tw, th) = (TITLE[0].len(), TITLE.len());
    if img.width < tw || img.height < th {
        return None;
    }
    let mask: Vec<bool> = (0..img.width * img.height)
        .map(|i| {
            let [b, g, r] = img.bgr(i % img.width, i / img.width);
            is_title_px(b, g, r)
        })
        .collect();
    let at = |x: usize, y: usize| mask[y * img.width + x];
    let cells: Vec<(usize, usize, bool)> = TITLE
        .iter()
        .enumerate()
        .flat_map(|(y, row)| row.bytes().enumerate().map(move |(x, c)| (x, y, c == b'#')))
        .collect();
    let ink = cells.iter().filter(|c| c.2).count();
    let (need, allow) = (
        (ink as f64 * HIT).ceil() as usize,
        ((cells.len() - ink) as f64 * STRAY) as usize,
    );
    for y in 0..=img.height - th {
        for x in 0..=img.width - tw {
            // The title's top-left pixel is text: only text pixels anchor it.
            if !at(x, y) {
                continue;
            }
            let (mut hits, mut stray) = (0, 0);
            for &(cx, cy, text) in &cells {
                match (text, at(x + cx, y + cy)) {
                    (true, true) => hits += 1,
                    (false, true) => stray += 1,
                    _ => {}
                }
                if stray > allow {
                    break;
                }
            }
            if hits >= need && stray <= allow {
                return Some((x, y));
            }
        }
    }
    None
}
