//! Manages the long-lived GreekRoomEngine sidecar process.
//!
//! Per the architecture doc (§4): the sidecar starts once with the app and
//! stays alive for the session, so NLP resources (Wildebeest, Uroman, etc.)
//! are loaded once rather than per-call. Requests/responses are correlated
//! by the `id` field defined in the shared JSON protocol (protocol.py).

use serde::Serialize;
use serde_json::Value;
use std::collections::{HashMap, VecDeque};
use std::io::Write;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tokio::sync::{oneshot, Mutex};

type PendingMap = Arc<Mutex<HashMap<String, oneshot::Sender<Value>>>>;
type LogBuffer = Arc<Mutex<VecDeque<LogEntry>>>;

/// A diagnostics-panel entry: sidecar lifecycle events (spawn/terminate/
/// respawn), request timeouts, and stderr lines relayed from the Python
/// process (which is where its own unhandled-exception tracebacks land —
/// see stdio_transport.py). Kept as a bounded in-memory ring buffer plus a
/// best-effort append to disk, since the in-memory copy is lost if the
/// whole Tauri process (not just the sidecar) goes down.
#[derive(Clone, Serialize)]
pub struct LogEntry {
    pub ts_ms: u64,
    pub level: String,
    pub message: String,
}

const LOG_CAPACITY: usize = 400;

/// How long the sidecar gets to notice its stdin closed and exit on its own
/// before it is forced. It only reaches the `for line in sys.stdin` loop
/// between requests, so this is deliberately short: the window is already
/// gone by the time RunEvent::Exit fires, and a user closing the app should
/// not wait on a check that is still running.
const GRACEFUL_EXIT_WAIT: std::time::Duration = std::time::Duration::from_millis(1500);

/// Force-terminate the sidecar and everything under it. Returns whether a
/// process actually had to be killed, so a clean stdin-close exit can be told
/// apart from a forced one in the log.
///
/// This kills the *tree* on purpose. A PyInstaller onefile binary is a
/// bootloader process that spawns the real Python interpreter as a child, and
/// terminating the bootloader alone leaves that child running — still holding
/// an open handle on bridge-engine.exe. That is the orphan state that makes
/// the next `tauri dev` fail inside tauri-build with a bare
/// `PermissionDenied: Access is denied.` while it tries to refresh the copy of
/// that executable under target/.
#[cfg(windows)]
fn kill_process_tree(pid: u32) -> bool {
    use std::os::windows::process::CommandExt;
    // CREATE_NO_WINDOW: a release build is windows_subsystem = "windows" and
    // has no console, so spawning taskkill would flash one up as the app dies.
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        // A non-zero status here is the *expected* result of a clean exit:
        // taskkill fails because the pid is already gone.
        .map(|status| status.success())
        .unwrap_or(false)
}

/// UNVERIFIED: Windows is the only target this project has actually run on.
/// PyInstaller's POSIX bootloader forwards signals to its child, so signalling
/// the pid we spawned should be enough without a tree walk.
#[cfg(not(windows))]
fn kill_process_tree(pid: u32) -> bool {
    std::process::Command::new("kill")
        .args(["-9", &pid.to_string()])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn append_log_file(app: &AppHandle, entry: &LogEntry) {
    let Ok(dir) = app.path().app_log_dir() else { return };
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("engine-events.log"))
    {
        let _ = writeln!(file, "{} [{}] {}", entry.ts_ms, entry.level, entry.message.replace('\n', " "));
    }
}

async fn record_log(log: &LogBuffer, app: &AppHandle, level: &str, message: String) {
    let entry = LogEntry { ts_ms: now_ms(), level: level.to_string(), message };
    {
        let mut buf = log.lock().await;
        buf.push_back(entry.clone());
        while buf.len() > LOG_CAPACITY {
            buf.pop_front();
        }
    }
    let _ = app.emit("engine-log", &entry);
    append_log_file(app, &entry);
}

fn request_timeout_seconds(method: &str) -> u64 {
    match method {
        "project.import" => 300,
        // Inspecting a whole Bible parses every Scripture file and computes
        // source fingerprints for duplicate detection. Project Home may also
        // discover a large pre-Beta-3 managed library on its first run.
        "project.inspectImport" | "project.list" => 180,
        // Opening a book loads TranslationCoreProject and then builds the
        // PassageSemanticRuntime synchronously (open and migrate the semantic
        // DB, replay invalidations, sync text revisions and alignment state).
        // After #99 that is about 1 s for Genesis from source and about 4 s
        // for a lazy sibling's first open on a fast NVMe machine, and the
        // installed app has measured about 2x the bench. It is whole-file
        // local I/O, so a scanned, spinning or sync-backed disk multiplies
        // it, and a false timeout here leaves the UI with no project while
        // Python finishes opening one. Same class as inspectImport (#112
        // step one; step two builds the runtime lazily).
        "project.open" => 180,
        // The first check for a book starts the isolated structural
        // checker, whose own hard timeout is 120 seconds. Keep enough
        // headroom for process startup/report parsing.
        "verse.runChecks" => 150,
        // A single call to an OpenAI-compatible endpoint: ai_client.py's own HTTP
        // timeout is 240s (with retries on transient 5xx/429).
        //
        // alignment.crossVerse.aiPropose (#146) runs the offline gap scan and
        // corpus pass first and then makes one such call, so it belongs to this
        // class and not to its interactive sibling alignment.crossVerse.propose,
        // which must keep the default 30s.
        "alignment.aiPropose"
        | "alignment.crossVerse.aiPropose"
        | "correction.createProposal"
        | "correction.regenerateProposal" => 260,
        // The automatic window alignment (#219) makes TWO such calls in a row
        // (source-first, then target-first), then the offline corpus pass and
        // the writes: twice the single-call class plus headroom.
        "alignment.window.autoAlign" => 540,
        // A whole-Bible report payload (tens of thousands of rows) takes a
        // while to serialize and ship over stdio; the export writes it
        // back out. report.status/report.cancel stay interactive.
        "report.get" | "report.export" => 180,
        // The book QA report. Measured on Genesis of a real Hindi IRV import
        // (50 chapters, 1533 verses): build_book_report() takes ~113 s, almost
        // all of it project_scan and exception_first_queue doing per-verse
        // workbench reads on a fresh connection each time (#143, and #113
        // underneath it). At the default 30 s the request always failed on a
        // full-length book while the single-threaded sidecar stayed blocked
        // until Python finished anyway -- so the timeout bought nothing and
        // cost a misleading error. It is no longer issued on project open;
        // the dashboard asks for it explicitly and warns about the wait.
        "project.report" => 180,
        // A whole-Bible triage map is one record per finding across every
        // book; the same serialize-and-ship cost as report.get, so the same
        // class. triage.run only starts a background job and returns a
        // snapshot -- it must stay interactive, and so must status/cancel,
        // or cancelling a long run becomes impossible.
        "triage.results" => 180,
        // The one Scripture-changing correction action, and the only method
        // here whose cost tracks project size rather than a provider call.
        // Before it returns it persists the application intent and PREPARED
        // invalidation, takes a full sqlite backup of the project's semantic
        // DB plus a whole-file SHA-256 of it, then runs the translationCore
        // apply: reconciliation, marker handling, word-bank movement, the
        // verse-edit audit, tN/tW verseEdits, a filesystem backup and a
        // journal write.
        //
        // Measured end to end through the real service (Stage 9B.3b fixture,
        // NVMe, warm cache): 0.45s at the 0.57MB DB a two-verse project
        // produces, 1.20s at 100MB, 1.60s at 200MB, with the backup itself
        // scaling linearly at about 5ms per MB. 30s is not tight on that
        // hardware -- but it is whole-file I/O, so antivirus scanning the
        // copy, a spinning disk or a sync-backed folder can cost an order of
        // magnitude more, and a 200MB DB needs only ~20x slower I/O to cross
        // 30s. A false timeout is the bad direction here: Rust stops waiting
        // while Python is mid-apply, so the user is told the correction
        // failed when it may already be written and journalled. 180 is the
        // table's existing class for expensive local I/O (report.get,
        // project.inspectImport) and leaves ~110x headroom on the measurement.
        //
        // getApplicationStatus and reanalyzeAffected are deliberately not
        // here: the first is a ledger read and the second only starts a
        // background job, so both must stay interactive at the default.
        "correction.applyProposal" => 180,
        _ => 30,
    }
}

pub struct EngineSidecar {
    child: Arc<Mutex<Option<CommandChild>>>,
    pending: PendingMap,
    app: Mutex<Option<AppHandle>>,
    start_lock: Mutex<()>,
    generation: Arc<AtomicU64>,
    log: LogBuffer,
    started_once: Arc<AtomicBool>,
    /// Set by shutdown() before stdin is closed, so the reader task can tell
    /// an expected exit from a crash. Without it every clean app close files
    /// an "error" in the diagnostics panel.
    shutting_down: Arc<AtomicBool>,
}

impl EngineSidecar {
    pub fn new() -> Self {
        Self {
            child: Arc::new(Mutex::new(None)),
            pending: Arc::new(Mutex::new(HashMap::new())),
            app: Mutex::new(None),
            start_lock: Mutex::new(()),
            generation: Arc::new(AtomicU64::new(0)),
            log: Arc::new(Mutex::new(VecDeque::new())),
            started_once: Arc::new(AtomicBool::new(false)),
            shutting_down: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Most recent diagnostics entries, oldest first, for the frontend's
    /// diagnostics panel and for `get_engine_log` at startup (live updates
    /// after that arrive via the "engine-log" event instead of polling).
    pub async fn recent_log(&self, limit: usize) -> Vec<LogEntry> {
        let buf = self.log.lock().await;
        let skip = buf.len().saturating_sub(limit);
        buf.iter().skip(skip).cloned().collect()
    }

    /// Stop the sidecar as the app exits. Synchronous, and called from the
    /// RunEvent::Exit handler on the main thread — there is no async runtime
    /// left to await on by then.
    ///
    /// **Closing stdin is the mechanism, not killing.** `run_stdio_loop`
    /// iterates `for line in sys.stdin`, so EOF ends the loop and Python
    /// returns from main on its own, which also lets the PyInstaller
    /// bootloader delete its own _MEI temp directory — a forced kill leaks one
    /// per run. Dropping `CommandChild` drops the `stdin_writer` it owns, and
    /// that is what closes the pipe; there is no explicit close in the API.
    ///
    /// Before this existed nothing ever stopped the sidecar: the process is
    /// designed to stay alive for the whole session and `CommandChild` was
    /// simply dropped with the app, so every Ctrl+C'd `tauri dev` left a live
    /// bridge-engine.exe behind. See `kill_process_tree` for why the backstop
    /// has to take the children too.
    pub fn shutdown(&self, app: &AppHandle) {
        self.shutting_down.store(true, Ordering::SeqCst);
        let Some(child) = self.child.blocking_lock().take() else {
            return;
        };
        let pid = child.pid();
        drop(child);
        std::thread::sleep(GRACEFUL_EXIT_WAIT);
        let forced = kill_process_tree(pid);
        // record_log is async and emits to a frontend that no longer exists;
        // append straight to the log file instead, which is the copy that
        // survives the process anyway.
        append_log_file(app, &LogEntry {
            ts_ms: now_ms(),
            level: "info".to_string(),
            message: format!(
                "Sidecar stopped on app exit (pid {pid}, {})",
                if forced { "forced after the grace period" } else { "exited on stdin close" },
            ),
        });
    }

    /// Spawn the sidecar binary and start the background reader task that
    /// demultiplexes stdout lines back to whichever `send_request` call is
    /// waiting on that response `id`.
    pub async fn start(&self, app: &AppHandle) -> Result<(), String> {
        *self.app.lock().await = Some(app.clone());
        let _start_guard = self.start_lock.lock().await;
        if self.child.lock().await.is_some() {
            return Ok(());
        }

        let mut sidecar_command = app
            .shell()
            .sidecar("bridge-engine")
            .map_err(|e| format!("failed to resolve sidecar: {e}"))?;

        // The bundled tN/tW/tA/UHB/UGNT snapshot ships via bundle.resources
        // (tauri.conf.json) rather than inside bridge-engine.spec's onefile
        // archive now — see that spec's own comment for why (PyInstaller's
        // onefile bootloader used to re-extract all ~45MB of it on every
        // single launch). main.py reads this flag into
        // BRIDGE_BUNDLED_RESOURCES_DIR before tc_ai_bridge's resource
        // resolvers run. A missing/unresolvable resource_dir() (should not
        // happen in a real build) just means the sidecar falls back to its
        // own sys._MEIPASS/source-tree resolution, same as before this
        // change — not fatal to startup.
        // tauri.conf.json declares "resources/" (array form), which
        // preserves that source folder name under the resolved resource
        // root — i.e. the bundled tree lands at resource_dir()/resources,
        // not resource_dir() itself.
        if let Ok(resources_dir) = app.path().resource_dir() {
            let resources_dir = resources_dir.join("resources");
            sidecar_command = sidecar_command.args(["--resources-dir", &resources_dir.to_string_lossy()]);
        }

        let (mut rx, child) = sidecar_command
            .spawn()
            .map_err(|e| format!("failed to spawn sidecar: {e}"))?;

        *self.child.lock().await = Some(child);
        let process_generation = self.generation.fetch_add(1, Ordering::SeqCst) + 1;

        // A respawn (as opposed to the very first startup) means something
        // killed the previous process out from under an open project —
        // self.project on the new process is blank, so the frontend needs
        // telling: it can't tell from a generic log line alone.
        let is_respawn = self.started_once.swap(true, Ordering::SeqCst);
        record_log(
            &self.log, app, "info",
            if is_respawn { "Sidecar restarted".to_string() } else { "Sidecar started".to_string() },
        ).await;
        if is_respawn {
            let _ = app.emit("engine-respawned", ());
        }

        let pending = self.pending.clone();
        let child_slot = self.child.clone();
        let generation = self.generation.clone();
        let log = self.log.clone();
        let shutting_down = self.shutting_down.clone();
        let app_for_reader = app.clone();
        tauri::async_runtime::spawn(async move {
            while let Some(event) = rx.recv().await {
                match event {
                    CommandEvent::Stdout(line_bytes) => {
                        let line = String::from_utf8_lossy(&line_bytes);
                        if let Ok(value) = serde_json::from_str::<Value>(line.trim()) {
                            if let Some(id) = value.get("id").and_then(|v| v.as_str()) {
                                // "__ready__" is the startup handshake, not a
                                // real request/response pairing — ignore it here.
                                if id == "__ready__" {
                                    continue;
                                }
                                let mut map = pending.lock().await;
                                if let Some(sender) = map.remove(id) {
                                    let _ = sender.send(value);
                                }
                            }
                        }
                    }
                    CommandEvent::Stderr(line_bytes) => {
                        let line = String::from_utf8_lossy(&line_bytes).to_string();
                        eprintln!("[bridge-engine] {line}");
                        // stdio_transport.py sends its own tracebacks here (see
                        // "[unhandled]"); everything else is routine logging —
                        // still worth keeping for the diagnostics panel, just
                        // not worth alarming over.
                        //
                        // "[trace]" lines are the per-phase timings project.open
                        // and import_project emit on every successful call (#99).
                        // Logging a normal open as a warning made the diagnostics
                        // panel read as though something had gone wrong each time.
                        let level = if line.contains("[unhandled]") || line.to_lowercase().contains("traceback") {
                            "error"
                        } else if line.contains("[trace]") {
                            "info"
                        } else {
                            "warn"
                        };
                        record_log(&log, &app_for_reader, level, line).await;
                    }
                    CommandEvent::Terminated(payload) => {
                        eprintln!("[bridge-engine] terminated: {:?}", payload);
                        // An exit during shutdown is the point of shutdown, not
                        // a fault to raise in the diagnostics panel.
                        let expected = shutting_down.load(Ordering::SeqCst);
                        record_log(
                            &log, &app_for_reader,
                            if expected { "info" } else { "error" },
                            if expected {
                                format!("Sidecar exited during shutdown: {:?}", payload)
                            } else {
                                format!("Sidecar process terminated: {:?}", payload)
                            },
                        ).await;
                        break;
                    }
                    _ => {}
                }
            }

            // Only the reader belonging to the currently registered process
            // may clear it. This prevents a late termination event from an
            // older process from erasing a newly restarted child.
            if generation.load(Ordering::SeqCst) == process_generation {
                *child_slot.lock().await = None;
                // Dropping every sender wakes in-flight calls immediately
                // with a closed-channel error instead of making them wait for
                // the full request timeout.
                pending.lock().await.clear();
            }
        });

        Ok(())
    }

    /// Send one JSON-RPC-style request to the sidecar and await its
    /// matching response by `id`. Timeout protects the UI from hanging
    /// forever if the sidecar crashes mid-request.
    pub async fn send_request(&self, method: &str, params: Value) -> Result<Value, String> {
        if self.child.lock().await.is_none() {
            let app = self
                .app
                .lock()
                .await
                .clone()
                .ok_or_else(|| "sidecar has not been initialized".to_string())?;
            self.start(&app).await?;
        }

        let id = uuid::Uuid::new_v4().to_string();
        let request = serde_json::json!({ "id": id, "method": method, "params": params });

        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id.clone(), tx);

        {
            let mut child_guard = self.child.lock().await;
            let child = match child_guard.as_mut() {
                Some(child) => child,
                None => {
                    self.pending.lock().await.remove(&id);
                    return Err(
                        "sidecar stopped before the request was sent; retry to restart it".into(),
                    );
                }
            };
            let mut line = request.to_string();
            line.push('\n');
            if let Err(error) = child.write(line.as_bytes()) {
                // The process may have died before its termination event was
                // delivered. Clear the stale handle so the next request can
                // start a fresh sidecar instead of repeatedly writing to it.
                *child_guard = None;
                self.pending.lock().await.remove(&id);
                return Err(format!(
                    "failed to write to sidecar stdin: {error}; retry to restart it"
                ));
            }
        }

        // A whole-Bible import can parse and normalize dozens of files. Keep
        // the normal interactive timeout short, but give that bounded local
        // operation enough time to finish on slower disks.
        let timeout_seconds = request_timeout_seconds(method);
        match tokio::time::timeout(std::time::Duration::from_secs(timeout_seconds), rx).await {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(_)) => Err("sidecar response channel closed unexpectedly".into()),
            Err(_) => {
                self.pending.lock().await.remove(&id);
                if let Some(app) = self.app.lock().await.clone() {
                    record_log(
                        &self.log, &app, "error",
                        format!("Request '{method}' timed out after {timeout_seconds}s"),
                    ).await;
                }
                Err(format!("sidecar request '{method}' timed out"))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::request_timeout_seconds;

    /// The shutdown path's forced backstop, exercised against real processes.
    /// Only on Windows: it is the sole target this project has run on, and the
    /// POSIX branch is marked unverified for that reason.
    #[cfg(windows)]
    mod kill_process_tree {
        use super::super::kill_process_tree;
        use std::process::{Command, Stdio};

        fn sleeper() -> std::process::Child {
            // cmd.exe runs ping.exe as a separate child, which is the same
            // two-process shape PyInstaller's bootloader makes — and the shape
            // CommandChild::kill() only ever took the first half of.
            // ping, not timeout: timeout.exe aborts immediately when stdin is
            // redirected, so the "process" would already be dead on arrival.
            Command::new("cmd")
                .args(["/c", "ping -n 30 127.0.0.1"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .expect("spawn a throwaway process to kill")
        }

        #[test]
        fn reports_true_and_terminates_a_live_process() {
            let mut child = sleeper();
            assert!(kill_process_tree(child.id()), "taskkill should report success");
            // wait() returning at all proves it died rather than sleeping 30s.
            let status = child.wait().expect("reap the killed process");
            assert!(!status.success(), "a terminated process must not exit cleanly");
        }

        #[test]
        fn reports_false_once_the_process_is_already_gone() {
            // This is the expected result of a clean stdin-close exit, and it
            // is what shutdown() logs as "exited on stdin close" — so getting
            // it backwards would quietly mislabel every normal shutdown.
            let mut child = sleeper();
            let pid = child.id();
            assert!(kill_process_tree(pid));
            let _ = child.wait();
            assert!(!kill_process_tree(pid), "a dead pid has nothing to kill");
        }
    }

    #[test]
    fn large_project_discovery_and_inspection_have_bounded_headroom() {
        assert_eq!(request_timeout_seconds("project.inspectImport"), 180);
        assert_eq!(request_timeout_seconds("project.list"), 180);
        assert_eq!(request_timeout_seconds("project.open"), 180);
        assert_eq!(request_timeout_seconds("project.import"), 300);
        assert_eq!(request_timeout_seconds("ping"), 30);
    }

    #[test]
    fn a_provider_call_gets_headroom_while_its_offline_sibling_stays_interactive() {
        // #146: the two cross-verse proposers differ only by a suffix, and
        // getting them the wrong way round is silent -- the AI one would die at
        // 30s mid-request, the offline one would hang the UI for four minutes.
        assert_eq!(request_timeout_seconds("alignment.crossVerse.aiPropose"), 260);
        assert_eq!(request_timeout_seconds("alignment.window.autoAlign"), 540);
        assert_eq!(request_timeout_seconds("alignment.crossVerse.propose"), 30);
        assert_eq!(request_timeout_seconds("alignment.crossVerse.link"), 30);
    }

    #[test]
    fn interactive_check_requests_keep_the_short_timeout() {
        // Beta 7 fixes dispatcher blocking at its source.  Extending these
        // timeouts would hide the regression and leave cancel/status unusable.
        assert_eq!(request_timeout_seconds("checks.status"), 30);
        assert_eq!(request_timeout_seconds("checks.cancel"), 30);
        assert_eq!(request_timeout_seconds("check.listForVerse"), 30);
    }

    #[test]
    fn report_polling_stays_interactive_while_payload_transfer_has_headroom() {
        assert_eq!(request_timeout_seconds("report.generate"), 30);
        assert_eq!(request_timeout_seconds("report.status"), 30);
        assert_eq!(request_timeout_seconds("report.cancel"), 30);
        assert_eq!(request_timeout_seconds("report.get"), 180);
        assert_eq!(request_timeout_seconds("report.export"), 180);
        // The book QA report measured ~113 s on a real full-length book, so the
        // default 30 s could only ever fail there (#143).
        assert_eq!(request_timeout_seconds("project.report"), 180);
    }

    #[test]
    fn triage_polling_stays_interactive_while_the_results_map_has_headroom() {
        // triage.run returns a job snapshot immediately; only the verdict map
        // is large. Cancel must never queue behind a whole-Bible payload.
        assert_eq!(request_timeout_seconds("triage.run"), 30);
        assert_eq!(request_timeout_seconds("triage.status"), 30);
        assert_eq!(request_timeout_seconds("triage.cancel"), 30);
        assert_eq!(request_timeout_seconds("triage.override"), 30);
        assert_eq!(request_timeout_seconds("triage.results"), 180);
    }

    #[test]
    fn correction_reads_stay_interactive_while_provider_wording_has_headroom() {
        assert_eq!(request_timeout_seconds("correction.getEligibility"), 30);
        assert_eq!(request_timeout_seconds("correction.getReviewContext"), 30);
        assert_eq!(request_timeout_seconds("correction.editProposal"), 30);
        assert_eq!(request_timeout_seconds("correction.createProposal"), 260);
        assert_eq!(
            request_timeout_seconds("correction.regenerateProposal"),
            260
        );
    }

    /// The Scripture-changing apply is the one correction method whose cost
    /// tracks project size, so it must not sit on the `_ => 30` default it
    /// fell through to before. Its two siblings must stay interactive: a
    /// ledger read and a background-job start have nothing to wait for, and
    /// giving them a long timeout would only delay reporting a dead sidecar.
    #[test]
    fn correction_apply_has_room_for_a_full_semantic_db_backup() {
        assert_eq!(request_timeout_seconds("correction.applyProposal"), 180);
        assert_eq!(
            request_timeout_seconds("correction.getApplicationStatus"),
            30
        );
        assert_eq!(request_timeout_seconds("correction.reanalyzeAffected"), 30);
    }

    /// Stage 9B.4 verification reads already-persisted Stage 6B/7/8 records and
    /// evaluates them in memory; acknowledgement is one small CAS transaction.
    /// None of the three copies a file or calls a provider, so all three belong
    /// on the interactive default -- a long timeout would only delay reporting
    /// a dead sidecar to a reviewer waiting on a verdict.
    #[test]
    fn correction_verification_methods_stay_interactive() {
        assert_eq!(request_timeout_seconds("correction.verifyApplication"), 30);
        assert_eq!(request_timeout_seconds("correction.getVerification"), 30);
        assert_eq!(
            request_timeout_seconds("correction.acknowledgeCorrected"),
            30
        );
    }
}
