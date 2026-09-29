//! One error type for loading and saving PicoBot's files.

use std::fmt;
use std::io;

#[derive(Debug)]
pub enum Error {
    /// Reading or writing a file failed.
    Io(io::Error),
    /// The JSON was valid but didn't describe what we expected.
    Format(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Io(e) => write!(f, "{e}"),
            Error::Format(msg) => f.write_str(msg),
        }
    }
}

impl std::error::Error for Error {}

// `?` on an io::Result or a serde_json::Result inside a function returning
// our Result converts through these.
impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Error::Io(e)
    }
}

impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Error::Format(e.to_string())
    }
}

pub type Result<T> = std::result::Result<T, Error>;

/// Shorthand for a format error.
pub(crate) fn bad<T>(msg: impl Into<String>) -> Result<T> {
    Err(Error::Format(msg.into()))
}
