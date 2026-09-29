//! Where summons are, and for how long.
//!
//! Summons are positional: the bot places them at anchors as it passes,
//! and an anchor holds at most one live summon of any kind. Each cast lives
//! for the skill's uptime; a skill can have up to `charges` instances out
//! at once, and casting one more removes its oldest (as the game does).
//! The bot can't see summons, so this is bookkeeping from cast times.

use crate::skills::Skill;

#[derive(Debug, Clone, PartialEq)]
pub struct Placement {
    pub skill: String,
    pub anchor: String,
    pub placed: f64,
    pub expires: f64,
}

#[derive(Debug, Clone, Default)]
pub struct SummonTracker {
    placed: Vec<Placement>,
}

impl SummonTracker {
    pub fn reset(&mut self) {
        self.placed.clear();
    }

    fn prune(&mut self, now: f64) {
        self.placed.retain(|p| p.expires > now);
    }

    pub fn active(&mut self, now: f64) -> &[Placement] {
        self.prune(now);
        &self.placed
    }

    pub fn anchor_free(&mut self, anchor: &str, now: f64) -> bool {
        self.prune(now);
        self.placed.iter().all(|p| p.anchor != anchor)
    }

    /// Record a cast at `anchor`; returns the placement the game removed
    /// to make room (that skill's oldest), if any.
    pub fn place(&mut self, skill: &Skill, anchor: &str, now: f64) -> Option<Placement> {
        self.prune(now);
        let mine = self.placed.iter().filter(|p| p.skill == skill.name).count();
        let gone = if mine >= skill.charges as usize {
            let oldest = self
                .placed
                .iter()
                .enumerate()
                .filter(|(_, p)| p.skill == skill.name)
                .min_by(|a, b| a.1.placed.total_cmp(&b.1.placed))
                .map(|(i, _)| i);
            oldest.map(|i| self.placed.remove(i))
        } else {
            None
        };
        self.placed.push(Placement {
            skill: skill.name.clone(),
            anchor: anchor.to_owned(),
            placed: now,
            expires: now + skill.uptime(),
        });
        gone
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::skills::SkillKind;

    fn orb(charges: u32) -> Skill {
        Skill {
            cooldown: 60.0,
            duration: 90.0,
            charges,
            kind: SkillKind::Summon,
            ..Skill::new("orb", "d")
        }
    }

    #[test]
    fn expiry_frees_the_anchor_and_reset_clears() {
        let mut t = SummonTracker::default();
        t.place(&orb(1), "a0", 0.0);
        assert!(!t.anchor_free("a0", 50.0));
        assert!(t.anchor_free("a0", 91.0));
        t.place(&orb(1), "a1", 100.0);
        t.reset();
        assert!(t.active(100.0).is_empty());
    }

    #[test]
    fn one_more_than_charges_removes_the_oldest() {
        let mut t = SummonTracker::default();
        assert!(t.place(&orb(2), "a0", 0.0).is_none());
        assert!(t.place(&orb(2), "a1", 10.0).is_none());
        let gone = t.place(&orb(2), "a2", 20.0).unwrap();
        assert_eq!(gone.anchor, "a0");
        assert!(t.anchor_free("a0", 20.0));
    }
}
