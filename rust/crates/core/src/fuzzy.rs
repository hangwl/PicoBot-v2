//! Fuzzy title matching: how well an OCR'd title names a stored map.
//!
//! [`ratio`] is Python's `difflib.SequenceMatcher(None, a, b).ratio()`
//! (Ratcliff–Obershelp: twice the matched characters over the total), on
//! the short strings titles are (no auto-junk below 200 characters).

use std::collections::HashMap;

use crate::title::normalize_name;

/// A sibling map's title scores at most this: similar is not the same map.
pub const SIBLING_CAP: f64 = 0.6;

fn longest_match(
    a: &[char],
    b2j: &HashMap<char, Vec<usize>>,
    alo: usize,
    ahi: usize,
    blo: usize,
    bhi: usize,
) -> (usize, usize, usize) {
    let (mut besti, mut bestj, mut best) = (alo, blo, 0);
    let mut j2len: HashMap<usize, usize> = HashMap::new();
    for (i, c) in a.iter().enumerate().take(ahi).skip(alo) {
        let mut next = HashMap::new();
        for &j in b2j.get(c).map(Vec::as_slice).unwrap_or(&[]) {
            if j < blo {
                continue;
            }
            if j >= bhi {
                break;
            }
            let k = j
                .checked_sub(1)
                .and_then(|p| j2len.get(&p))
                .copied()
                .unwrap_or(0)
                + 1;
            next.insert(j, k);
            if k > best {
                (besti, bestj, best) = (i + 1 - k, j + 1 - k, k);
            }
        }
        j2len = next;
    }
    (besti, bestj, best)
}

/// Total size of the matching blocks.
fn matched(a: &[char], b: &[char]) -> usize {
    let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
    for (j, c) in b.iter().enumerate() {
        b2j.entry(*c).or_default().push(j);
    }
    let mut total = 0;
    let mut queue = vec![(0, a.len(), 0, b.len())];
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (i, j, k) = longest_match(a, &b2j, alo, ahi, blo, bhi);
        if k > 0 {
            total += k;
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                queue.push((i + k, ahi, j + k, bhi));
            }
        }
    }
    total
}

/// `difflib.SequenceMatcher(None, a, b).ratio()`.
pub fn ratio(a: &str, b: &str) -> f64 {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    let n = a.len() + b.len();
    if n == 0 {
        return 1.0;
    }
    2.0 * matched(&a, &b) as f64 / n as f64
}

/// Letter runs and digit runs, lowercased.
fn tokens(text: &str) -> Vec<String> {
    let lower = text.to_lowercase();
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut digits = false;
    for c in lower.chars() {
        let (letter, digit) = (c.is_ascii_lowercase(), c.is_ascii_digit());
        if (letter || digit) && !cur.is_empty() && digit == digits {
            cur.push(c);
            continue;
        }
        if !cur.is_empty() {
            out.push(std::mem::take(&mut cur));
        }
        if letter || digit {
            cur.push(c);
            digits = digit;
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

fn is_num(t: &str) -> bool {
    t.chars().all(|c| c.is_ascii_digit())
}

/// `word` appears among `words` allowing an OCR misread, or inside
/// `joined` (the OCR dropped a space and merged two words).
fn word_in(word: &str, words: &[&str], joined: &str) -> bool {
    joined.contains(word)
        || words
            .iter()
            .any(|w| word.contains(w) || ratio(word, w) >= 0.75)
}

/// Two titles name *different* maps of one family rather than one title
/// misread: another number, an extra real word, or a complete read that
/// stops where the stored title goes on. OCR noise stays forgiven: short
/// junk tokens, split or merged words, a title clipped mid-word.
pub fn looks_like_sibling(ocr: &str, candidate: &str) -> bool {
    let (o, c) = (tokens(ocr), tokens(candidate));
    if o.is_empty() || c.is_empty() {
        return false;
    }
    let o_nums: Vec<&String> = o.iter().filter(|t| is_num(t)).collect();
    let c_nums: Vec<&String> = c.iter().filter(|t| is_num(t)).collect();
    if !o_nums.is_empty() && !c_nums.is_empty() && o_nums != c_nums {
        return true;
    }
    let c_words: Vec<&str> = c
        .iter()
        .filter(|t| !is_num(t))
        .map(String::as_str)
        .collect();
    let o_words: Vec<&str> = o
        .iter()
        .filter(|t| !is_num(t))
        .map(String::as_str)
        .collect();
    let (c_joined, o_joined) = (c.concat(), o.concat());
    // Words before the stored title starts: a region prefix or icon residue.
    let start = o.iter().position(|t| ratio(t, &c[0]) >= 0.75).unwrap_or(0);
    let tail = &o[start..];
    for (n, w) in tail.iter().enumerate() {
        if is_num(w) || w.chars().count() < 4 || word_in(w, &c_words, &c_joined) {
            continue;
        }
        let len = w.chars().count();
        let clipped = n == tail.len() - 1
            && c_words
                .iter()
                .any(|cw| ratio(w, &cw.chars().take(len).collect::<String>()) >= 0.75);
        if clipped {
            continue;
        }
        return true; // an extra real word
    }
    let last = &o[o.len() - 1];
    if last.chars().count() >= 3 {
        if let Some(at) = c.iter().rposition(|t| t == last) {
            let goes_on = c[at + 1..]
                .iter()
                .any(|t| is_num(t) || (t.chars().count() >= 4 && !word_in(t, &o_words, &o_joined)));
            if goes_on {
                return true; // a complete read, and the stored title goes on
            }
        }
    }
    false
}

/// 0–1 similarity of an OCR'd title to a stored name: the best of
/// containment (weighted by how much of the read the candidate covers),
/// prefix similarity for titles the client clipped, and whole-string
/// similarity for misreads; a sibling is capped at [`SIBLING_CAP`].
pub fn title_score(ocr_text: &str, candidate: &str) -> f64 {
    let (ocr, cand) = (normalize_name(ocr_text), normalize_name(candidate));
    if ocr.is_empty() || cand.is_empty() {
        return 0.0;
    }
    if ocr == cand {
        return 1.0;
    }
    let (lo, lc) = (ocr.chars().count(), cand.chars().count());
    let mut score = ratio(&ocr, &cand);
    if lc >= 5 && ocr.contains(&cand) {
        score = score.max(0.85 + 0.15 * lc as f64 / lo as f64);
    }
    if lo >= 6 && lo < lc {
        let prefix = ratio(&ocr, &cand.chars().take(lo).collect::<String>());
        score = score.max(0.85 * prefix + 0.15 * lo as f64 / lc as f64);
    }
    if score > SIBLING_CAP && looks_like_sibling(ocr_text, candidate) {
        score = SIBLING_CAP;
    }
    score.min(1.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ratio_matches_difflib_examples() {
        assert_eq!(ratio("abcd", "bcde"), 0.75);
        assert_eq!(ratio("", ""), 1.0);
        assert_eq!(ratio("abc", ""), 0.0);
        // difflib docs: SequenceMatcher(None, "abxcd", "abcd").ratio() = 0.888...
        assert!((ratio("abxcd", "abcd") - 8.0 / 9.0).abs() < 1e-12);
    }

    #[test]
    fn tokens_split_letters_and_digits() {
        assert_eq!(tokens("Limina : 1-5 East"), ["limina", "1", "5", "east"]);
        assert_eq!(tokens("Ramparts2"), ["ramparts", "2"]);
    }

    #[test]
    fn siblings_are_capped_misreads_are_not() {
        assert_eq!(title_score("Ramparts 2", "Ramparts 3"), SIBLING_CAP);
        assert!(title_score("Lake of Obliviom", "Lake of Oblivion") > 0.9);
        assert_eq!(title_score("", "x"), 0.0);
    }
}
