//! Crash-safe file writes.

use std::fs;
use std::io::Write;
use std::path::Path;
use std::thread::sleep;
use std::time::Duration;

/// Write `text` so readers see the old or the new file, never half of one:
/// write a temporary file next to it, then rename it over the target. On
/// Windows the rename can briefly fail while antivirus or an indexer holds
/// the target, so it is retried a few times.
pub fn write_text_atomic(path: &Path, text: &str) -> std::io::Result<()> {
    let dir = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("file");
    let tmp = dir.join(format!(".{name}.{}.tmp", std::process::id()));
    let result = (|| {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(text.as_bytes())?;
        f.sync_all()?;
        drop(f);
        let mut attempt = 0;
        loop {
            match fs::rename(&tmp, path) {
                Ok(()) => return Ok(()),
                Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied && attempt < 4 => {
                    attempt += 1;
                    sleep(Duration::from_millis(50 * attempt));
                }
                Err(e) => return Err(e),
            }
        }
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}
