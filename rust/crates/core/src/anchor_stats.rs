//! Per-anchor patrol statistics for the dashboard: visits, missed
//! landings toward each anchor, and skips by reason. Every anchor is
//! planned once per loop, so one that falls behind is being skipped or
//! missed rather than out-drawn by the loop policy.
//!
//! Plain data: the host owns it and decides how it is shared between
//! threads; each change returns `true` so the owner can refresh the
//! dashboard.

use std::collections::HashMap;

use serde::Serialize;
use serde_json::{Map, Value};

/// One anchor's record, serialised as the dashboard expects:
/// `{"name", "visits", "last", "misses", "skips": {reason: n}}`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct AnchorRow {
    pub name: String,
    pub visits: u32,
    /// Epoch seconds of the last visit.
    pub last: Option<f64>,
    pub misses: u32,
    /// Reason → count, in first-seen order.
    pub skips: Map<String, Value>,
}

impl AnchorRow {
    fn empty(name: &str) -> Self {
        AnchorRow {
            name: name.into(),
            visits: 0,
            last: None,
            misses: 0,
            skips: Map::new(),
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct AnchorStats {
    /// map → anchor → record
    data: HashMap<String, HashMap<String, AnchorRow>>,
}

impl AnchorStats {
    fn rec(&mut self, map: &str, anchor: &str) -> &mut AnchorRow {
        self.data
            .entry(map.to_owned())
            .or_default()
            .entry(anchor.to_owned())
            .or_insert_with(|| AnchorRow::empty(anchor))
    }

    pub fn visit(&mut self, map: Option<&str>, anchor: &str, now: f64) -> bool {
        let Some(map) = map.filter(|m| !m.is_empty()) else {
            return false;
        };
        let r = self.rec(map, anchor);
        r.visits += 1;
        r.last = Some(now);
        true
    }

    pub fn miss(&mut self, map: Option<&str>, anchor: &str) -> bool {
        let Some(map) = map.filter(|m| !m.is_empty()) else {
            return false;
        };
        self.rec(map, anchor).misses += 1;
        true
    }

    pub fn skip(&mut self, map: Option<&str>, anchor: &str, why: &str) -> bool {
        let Some(map) = map.filter(|m| !m.is_empty()) else {
            return false;
        };
        let skips = &mut self.rec(map, anchor).skips;
        let n = skips.get(why).and_then(Value::as_u64).unwrap_or(0);
        skips.insert(why.to_owned(), (n + 1).into());
        true
    }

    /// One row per anchor of the map, in the given order.
    pub fn rows(&self, map: Option<&str>, anchors: &[String]) -> Vec<AnchorRow> {
        let Some(map) = map.filter(|m| !m.is_empty()) else {
            return Vec::new();
        };
        let per = self.data.get(map);
        anchors
            .iter()
            .map(|a| {
                per.and_then(|p| p.get(a))
                    .cloned()
                    .unwrap_or_else(|| AnchorRow::empty(a))
            })
            .collect()
    }

    pub fn reset(&mut self, map: &str) {
        self.data.remove(map);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn counts_visits_misses_and_skips_by_reason() {
        let mut st = AnchorStats::default();
        assert!(st.visit(Some("m"), "a0", 100.0));
        st.visit(Some("m"), "a0", 110.0);
        st.miss(Some("m"), "a1");
        st.skip(Some("m"), "a1", "no route");
        st.skip(Some("m"), "a1", "no route");
        let names: Vec<String> = ["a0", "a1", "a2"].map(String::from).to_vec();
        let rows = st.rows(Some("m"), &names);
        assert_eq!((rows[0].visits, rows[0].last), (2, Some(110.0)));
        assert_eq!(rows[1].misses, 1);
        assert_eq!(rows[2].visits, 0); // listed, never seen
        assert_eq!(
            serde_json::to_value(&rows[1]).unwrap(),
            json!({"name": "a1", "visits": 0, "last": null, "misses": 1, "skips": {"no route": 2}})
        );
    }

    #[test]
    fn reset_and_no_map_is_ignored() {
        let mut st = AnchorStats::default();
        assert!(!st.visit(None, "a0", 1.0));
        st.visit(Some("m"), "a0", 1.0);
        st.reset("m");
        let names = vec!["a0".to_owned()];
        assert_eq!(st.rows(Some("m"), &names)[0].visits, 0);
        assert!(st.rows(None, &names).is_empty());
    }
}
