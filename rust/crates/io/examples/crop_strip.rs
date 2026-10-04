//! Cut the rune strip area (x 370..995, y 160..300) out of a window capture.
//!
//!     cargo run --release -p picobot-io --example crop_strip -- <window.png> <out.png>

fn main() {
    let mut a = std::env::args().skip(1);
    let (src, dst) = (a.next().expect("window.png"), a.next().expect("out.png"));
    let img = image::open(src).expect("open").to_rgb8();
    image::imageops::crop_imm(&img, 370, 160, 625, 140)
        .to_image()
        .save(dst)
        .expect("save");
}
