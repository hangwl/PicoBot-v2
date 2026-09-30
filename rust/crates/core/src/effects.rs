//! What a skill does to the character, as measured: cast in the air during
//! a flash jump (how far it moves the landing, how long it holds the
//! character up) or from standing on the ground (how far it moves them).
//!
//! Stored as the reach-file profile `skill_effects`, rows
//! `{skill, where, n, dx, hang, rise, sd}`: `where` is `air` (the default
//! when absent) or `ground`; `dx` is the shift in the direction of the
//! jump or the way the character faces (negative = pulled back), `hang`
//! the airtime added (s), `rise` the peak height change (px).

use serde_json::Value;

#[derive(Debug, Clone, PartialEq)]
pub struct SkillEffect {
    pub skill: String,
    pub dx: f64,
    pub hang: f64,
    pub rise: f64,
    pub n: u64,
    /// Measured standing on the ground, not in a flash.
    pub on_ground: bool,
}

/// A skill that moves the landing less than this (px) is treated as not
/// moving it.
pub const MOVES_PX: f64 = 3.0;

impl SkillEffect {
    pub fn from_rows(rows: &[Value]) -> Vec<SkillEffect> {
        rows.iter()
            .filter_map(|r| {
                Some(SkillEffect {
                    skill: r.get("skill")?.as_str()?.to_owned(),
                    dx: r.get("dx")?.as_f64()?,
                    hang: r.get("hang").and_then(Value::as_f64).unwrap_or(0.0),
                    rise: r.get("rise").and_then(Value::as_f64).unwrap_or(0.0),
                    n: r.get("n").and_then(Value::as_u64).unwrap_or(0),
                    on_ground: r.get("where").and_then(Value::as_str) == Some("ground"),
                })
            })
            .filter(|e| e.n > 0)
            .collect()
    }

    /// Does casting it keep the landing within `(back, forward)` px of the
    /// plan? `back` is how far it may pull the landing short (positive
    /// number), `forward` how far past.
    pub fn fits(&self, back: f64, forward: f64) -> bool {
        self.dx >= -back && self.dx <= forward
    }
}

/// The effect of a skill from landings with and without it: the mean
/// shift (px, forward), extra airtime (s) and peak change (px).
pub struct Glide {
    pub dx: f64,
    pub air: f64,
    pub peak: f64,
}

pub fn effect_of(skill: &str, base: &[Glide], with: &[Glide]) -> Option<(SkillEffect, f64)> {
    if base.is_empty() || with.is_empty() {
        return None;
    }
    let mean = |v: &[Glide], f: fn(&Glide) -> f64| v.iter().map(f).sum::<f64>() / v.len() as f64;
    let dxs: Vec<f64> = with.iter().map(|g| g.dx).collect();
    let m = mean(with, |g| g.dx);
    let sd = (dxs.iter().map(|d| (d - m).powi(2)).sum::<f64>() / dxs.len() as f64).sqrt();
    let eff = SkillEffect {
        skill: skill.to_owned(),
        dx: m - mean(base, |g| g.dx),
        hang: mean(with, |g| g.air) - mean(base, |g| g.air),
        rise: mean(with, |g| g.peak) - mean(base, |g| g.peak),
        n: with.len() as u64,
        on_ground: false,
    };
    Some((eff, sd))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn g(dx: f64, air: f64, peak: f64) -> Glide {
        Glide { dx, air, peak }
    }

    #[test]
    fn effect_is_the_difference_from_a_plain_flash() {
        let base = [g(30.0, 0.5, 12.0), g(32.0, 0.5, 12.0)];
        let with = [g(10.0, 0.8, 15.0), g(12.0, 0.9, 15.0)];
        let (e, sd) = effect_of("cadena", &base, &with).unwrap();
        assert_eq!((e.dx, e.n), (-20.0, 2));
        assert!((e.hang - 0.35).abs() < 1e-9 && (e.rise - 3.0).abs() < 1e-9);
        assert!((sd - 1.0).abs() < 1e-9);
        assert!(effect_of("x", &[], &with).is_none());
    }

    #[test]
    fn rows_parse_and_empty_ones_drop() {
        let rows = [
            json!({"skill": "a", "n": 3, "dx": -12.0, "hang": 0.4, "rise": 1.0}),
            json!({"skill": "b", "n": 0, "dx": 5.0}),
            json!({"n": 2, "dx": 5.0}),
        ];
        let e = SkillEffect::from_rows(&rows);
        assert_eq!(e.len(), 1);
        assert_eq!(e[0].skill, "a");
    }

    #[test]
    fn fits_checks_both_sides() {
        let e = SkillEffect {
            skill: "a".into(),
            dx: -12.0,
            hang: 0.0,
            rise: 0.0,
            n: 1,
            on_ground: false,
        };
        assert!(e.fits(15.0, 5.0));
        assert!(!e.fits(10.0, 5.0));
        assert!(!SkillEffect { dx: 9.0, ..e }.fits(15.0, 5.0));
    }
}
