//! Safety alerts to Telegram (`bot_token` + `chat_id` in `config.json`).
//!
//! Each message goes out on its own thread: the bot never waits on the
//! network while it handles a hazard. The token is part of every request
//! URL, so it is scrubbed from anything that reaches the log.

use std::sync::Arc;
use std::time::Duration;

use crate::bus::Bus;

#[derive(Clone)]
pub struct Telegram {
    token: String,
    chat_id: String,
    bus: Arc<Bus>,
}

/// What a send came to.
#[derive(Debug, PartialEq)]
pub enum Sent {
    Ok,
    /// Telegram answered with an error (its description).
    Refused(u16, String),
    /// The request never got an answer.
    Failed(String),
}

impl Telegram {
    pub fn new(token: &str, chat_id: &str, bus: Arc<Bus>) -> Self {
        Telegram {
            token: token.trim().to_owned(),
            chat_id: chat_id.trim().to_owned(),
            bus,
        }
    }

    pub fn configured(&self) -> bool {
        !self.token.is_empty() && !self.chat_id.is_empty()
    }

    fn scrub(&self, text: &str) -> String {
        if self.token.is_empty() {
            text.to_owned()
        } else {
            text.replace(&self.token, "<token>")
        }
    }

    fn call(&self, method: &str) -> ureq::RequestBuilder<ureq::typestate::WithBody> {
        let agent: ureq::Agent = ureq::Agent::config_builder()
            .timeout_global(Some(Duration::from_secs(10)))
            .http_status_as_error(false)
            .build()
            .into();
        agent.post(format!(
            "https://api.telegram.org/bot{}/{method}",
            self.token
        ))
    }

    /// Send `text` now (blocking, up to 10s).
    pub fn send(&self, text: &str) -> Sent {
        if !self.configured() {
            return Sent::Failed("Telegram isn't configured".into());
        }
        let got = self
            .call("sendMessage")
            .query("chat_id", &self.chat_id)
            .query("text", text)
            .send_empty();
        match got {
            Ok(mut resp) => {
                let status = resp.status().as_u16();
                if status == 200 {
                    return Sent::Ok;
                }
                let body = resp.body_mut().read_to_string().unwrap_or_default();
                let why = serde_json::from_str::<serde_json::Value>(&body)
                    .ok()
                    .and_then(|v| v["description"].as_str().map(str::to_owned))
                    .unwrap_or(body);
                Sent::Refused(status, self.scrub(&why))
            }
            Err(e) => Sent::Failed(self.scrub(&e.to_string())),
        }
    }

    /// Send on a background thread; the outcome goes to the event log.
    pub fn send_async(&self, text: &str) {
        if !self.configured() {
            return;
        }
        let (me, text) = (self.clone(), text.to_owned());
        let _ = std::thread::Builder::new()
            .name("Telegram".into())
            .spawn(move || match me.send(&text) {
                Sent::Ok => me
                    .bus
                    .emit_level("notify", "Telegram message sent", "debug"),
                Sent::Refused(code, why) => me.bus.emit_level(
                    "notify",
                    &format!("Telegram refused the message ({code}): {why}"),
                    "warn",
                ),
                Sent::Failed(why) => {
                    me.bus
                        .emit_level("notify", &format!("Telegram send failed: {why}"), "warn")
                }
            });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unconfigured_sends_nothing() {
        let t = Telegram::new(" ", "1", Arc::new(Bus::default()));
        assert!(!t.configured());
        assert!(matches!(t.send("x"), Sent::Failed(_)));
    }

    #[test]
    fn the_token_never_reaches_the_log() {
        let t = Telegram::new("123:SECRET", "1", Arc::new(Bus::default()));
        assert_eq!(
            t.scrub("https://api.telegram.org/bot123:SECRET/sendMessage failed"),
            "https://api.telegram.org/bot<token>/sendMessage failed"
        );
    }
}
