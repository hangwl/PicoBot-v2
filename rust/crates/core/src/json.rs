//! JSON helpers that match the Python host's behaviour.
//!
//! The Python code reads config leniently (`bool(x)`, `float(x)`, `int(x)`)
//! and writes with `json.dumps(indent=N)`, which escapes every non-ASCII
//! character. These helpers do the same so both hosts read and write
//! identical files.

use serde::Serialize;
use serde_json::ser::{PrettyFormatter, Serializer};
use serde_json::Value;

/// Python's `bool(x)` for a JSON value.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python's `float(x)`: numbers, and strings that parse as numbers.
pub fn as_f64(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        _ => None,
    }
}

/// Python's `int(x)`: integers, floats (truncated), integer strings.
pub fn as_i64(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)),
        Value::String(s) => s.trim().parse().ok(),
        Value::Bool(b) => Some(i64::from(*b)),
        _ => None,
    }
}

/// Python's `str(x)` for the values config files actually hold.
pub fn as_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        other => other.to_string(),
    }
}

/// `str(v) if v else None` — the Python idiom for optional keys.
pub fn opt_string(v: Option<&Value>) -> Option<String> {
    v.filter(|v| truthy(v)).map(as_string)
}

/// Pretty JSON exactly as `json.dumps(value, indent=indent)` writes it.
pub fn dumps(value: &Value, indent: usize) -> String {
    let pad = vec![b' '; indent];
    let mut out = Vec::new();
    let mut ser = Serializer::with_formatter(&mut out, PrettyFormatter::with_indent(&pad));
    value
        .serialize(&mut ser)
        .expect("serialising a JSON value can't fail");
    ascii_escape(&String::from_utf8(out).expect("serde_json writes UTF-8"))
}

/// Python's `ensure_ascii`: non-ASCII characters become `\uXXXX` (a
/// surrogate pair above U+FFFF). They can only occur inside strings, so
/// escaping the whole text is safe.
fn ascii_escape(text: &str) -> String {
    if text.is_ascii() {
        return text.to_owned();
    }
    let mut out = String::with_capacity(text.len() + 16);
    for c in text.chars() {
        if c.is_ascii() {
            out.push(c);
        } else {
            let mut units = [0u16; 2];
            for unit in c.encode_utf16(&mut units) {
                out.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn dumps_matches_python_indent_and_escapes() {
        let v = json!({"name": "Limina — 2-6", "pos": [0.25, 1.0], "empty": [], "none": null});
        assert_eq!(
            dumps(&v, 2),
            "{\n  \"name\": \"Limina \\u2014 2-6\",\n  \"pos\": [\n    0.25,\n    1.0\n  ],\n  \"empty\": [],\n  \"none\": null\n}"
        );
    }

    #[test]
    fn python_style_coercions() {
        assert!(!truthy(&json!(0)) && truthy(&json!("x")) && !truthy(&json!([])));
        assert_eq!(as_i64(&json!(3.9)), Some(3));
        assert_eq!(as_f64(&json!("1.5")), Some(1.5));
        assert_eq!(opt_string(Some(&json!(""))), None);
    }
}
