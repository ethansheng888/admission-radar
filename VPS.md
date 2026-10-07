# VPS operation

Run a fixed release under `/opt/admission-radar/current` with an independent venv.
Use `config.vps.example.json`, absolute state paths and a dedicated unprivileged
`admission-radar` user. The systemd templates are in `deploy/systemd/`; they must
remain disabled until mail validation and the old sender is confirmed in standby.
Keep the GitHub workflow enabled as a manual backup; do not run two formal senders.

## Credentials

The system manager reads `/etc/admission-radar/admission-radar.env` (root:root,
0600) and passes its variables to the service. The program does not source .env.
The configuration directory and public TLS certificate must be readable by the
service group; the credential file itself must remain readable only by root.
Fill SMTP_USERNAME, SMTP_PASSWORD, CUFE_RECIPIENTS and BJTU_RECIPIENT privately.
Use the current Gmail 465 SSL sender, with a dedicated app password if available.
Never put credentials into arguments, Git, debug logs or screenshots.

## CUFE TLS exception

Ubuntu OpenSSL 3 rejects CUFE's legacy server handshake; the observed endpoint
also omits its intermediate certificate. The CUFE-specific adapter enables
SSL_OP_LEGACY_SERVER_CONNECT and disables renegotiation while retaining certificate
and hostname verification. It never changes the global OpenSSL configuration,
SMTP TLS, or BJTU TLS, and rejects disabled certificate verification.
The exception has security implications and should be removed when CUFE fixes
its endpoint. Redirects are rejected for manual review.

`certificates/cufe-intermediate.pem` is a PUBLIC Xcc Trust DV SSL CA certificate
obtained from the leaf certificate AIA URL
`http://repository.certum.pl/xinchacha2dv.cer`. It was verified against existing
system roots before use. SHA-256 fingerprint:
`B81D7010D7E495179BB5BB504223EF2EEF3056557EE3924533718169F1A670C8`.
Its expiration is June 30, 2027. Install it at the configured path; do not blindly
trust downloaded replacement certificates. Validate replacements and update
under controlled release management.

## Read-only commands

```sh
/opt/admission-radar/current/.venv/bin/python /opt/admission-radar/current/main.py --config /etc/admission-radar/config.json --preview
sudo -u admission-radar /opt/admission-radar/current/.venv/bin/python /opt/admission-radar/current/main.py --config /etc/admission-radar/config.json --status
systemctl list-timers --all 'admission-radar*'
systemctl show admission-radar.service -p Result -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp
journalctl -u admission-radar.service -n 100 --no-pager
```

Preview requires no SMTP credentials, does not open the announcement database,
and does not create application log/status files. Status reads the database and
reports counts, timestamps and uncertainty without exposing recipient addresses.
Run database queries as the service user so SQLite auxiliary files remain owned
by that user. Oneshot inactive between runs is normal.

## Notifications and state

VPS `track_recipient_deliveries=true` freezes recipients for each pending notice.
Only failed/unconfirmed targets are retried; already accepted recipients are not
retried because another recipient failed. New recipients receive future notices
only. Existing successful notices and baseline rows are never backfilled.
Pending delivery is attempted even if the current school fetch fails; the run
still reports that fetch failure. Known errors get up to three SMTP attempts;
uncertain submission is retained for a later cycle rather than immediately
resubmitting DATA. Uncertainty may still lead to a duplicate on a later retry.
SMTP acceptance does not establish inbox delivery, and SMTP/SQLite cannot make
the acceptance-and-state-update window atomic.

Public GitHub `config.cloud.json` defaults to legacy group tracking and never
populates the private recipient table. It retains the existing partial-refusal
duplication limitation. The VPS database contains email addresses and must not
be pushed to the public repository.

## Cutover

1. Build/test the fixed release and validate preview before changing old tasks.
2. Privately supply credentials; check configuration, authenticate, then send
   explicitly authorized test messages to both school groups. Confirm inboxes.
3. Publish the tested sender-switch code and workflow, keeping GitHub enabled.
   `config.sender.json` initially selects `github`, preserving current monitoring.
   Confirm both sender profiles read the shared policy. Then commit
   `active_sender: vps` to main, wait for all running/queued old sender jobs to
   finish and confirm final persistence. Observe an actual GitHub standby run:
   its read-only preview succeeds and formal state/mail steps are skipped.
   cron-job.org can continue dispatching the enabled standby workflow. Pause any
   other independent sender. Do not start VPS just because a policy commit exists.
4. Fetch the quiescent default branch's latest SQLite. Record commit, SHA-256,
   integrity/FK checks and school counts. Compare all legacy rows before and
   after schema migration. Preserve IDs, baseline and notification timestamps.
5. Install the database with service ownership and 0600 mode, create a private
   consistent pre-cutover backup, and record the cutover evidence privately.
6. Only after the old sender is in standby and quiescent create the root-controlled
   `/etc/admission-radar/cutover.ready` file. It is a deliberate startup gate,
   not a substitute for checking old execution state.
7. Start one formal service cycle, verify notification state, then enable the
   three project timers. Observe a REAL scheduled scan and check existing services.

Never bootstrap a missing production DB: preflight rejects missing or empty
existing school histories. Adding a new school requires an explicit baseline
migration; do not change the current two school IDs.

## Timers, logs and backups

Scan hourly at minute 12, health at minute 40, backup daily at 03:35, all in
Asia/Shanghai. Persistent catches up once; it does not replay each missed hour.
Formal scans share a flock lock and a 10-minute maximum runtime. Use the same
service for manual scans. File logs rotate at 2 MiB with five backups; leave
the existing global journal policy unchanged.

Backup uses SQLite Backup API, including committed WAL data; validates the result
before atomic publication or pruning. Keep 14 daily copies plus manual snapshots.
Failed backups do not prune prior valid copies. Off-host copies should go to
existing personal storage; same-host backups cannot protect against disk loss.
Health alerts go only to the CUFE owner group when issues change or recover.
Thresholds: 3 hours without fetch success, 3 hours old pending, 3 consecutive
failed cycles, and a backup older than 28 hours. SMTP outages or VPS outages
cannot be reliably reported using that same host and SMTP connection.

## Pause, resume and rollback

```sh
sudo systemctl stop admission-radar.timer
# Wait for the active service; do not kill an in-flight SMTP submission by default.
sudo systemctl start admission-radar.timer
sudo systemctl start admission-radar.service  # formal scan, may send mail
```

The GitHub workflow remains enabled. Standby runs keep the existing 30-day
heartbeat so prolonged standby does not leave the public repository inactive.
This writes only `state/heartbeat.txt`, never formal notice/delivery state.
Both formal sender profiles read the shared
`main/config.sender.json` through the GitHub Contents API before scanning and
again before each SMTP recipient batch. `--sender-check` reads no mail secrets or
database: exit 0 means this sender is selected, 3 means standby, and any error
refuses sending. Public VPS reads require no GitHub credential. GitHub runners
use their existing ephemeral token. Do not put a token in the policy file.
Missing/invalid policy and API/network failure refuse formal sending; this adds
GitHub API availability as a dependency. The switch is a MANUAL control, not a
lease or automatic failover. An explicit `--test-email` remains a separately
authorized diagnostic and never records formal delivery progress.

For updates stop project timers, wait for active project jobs, back up current
state, and switch to a tested compatible release. Keep the latest database;
restoring an old DB can lose delivery progress. To return to GitHub first stop
all VPS project timers and wait for active jobs, resolve uncertain/partial
deliveries, and export current compatible state using:

```sh
sudo -u admission-radar /opt/admission-radar/current/.venv/bin/python /opt/admission-radar/current/scripts/export_github_state.py /var/lib/admission-radar/radar.db /var/lib/admission-radar/github-state-YYYYMMDD-HHMM.db
```

The export includes only legacy website/notice tables and preserves IDs, baseline,
notification timestamps, pending and the SQLite sequence. It excludes the private
recipient table and refuses partial, uncertain or inconsistent delivery state.
Publish that verified snapshot to GitHub through an authorized path, verify
GitHub's recipient Secrets match the current groups, and ONLY THEN select
`active_sender: github`. Keep VPS stopped until the reverse handoff completes.
Never restore stale GitHub state blindly. Whole-host loss needs an existing
off-host snapshot; local backups cannot provide that. Keep compatible snapshots
in existing personal storage, and record their recovery point. No off-host
upload is configured by this release.

During handoff, wait for in-flight jobs to finish before changing an active
sender's state or enabling the other sender. For an unreachable VPS, establish
that it cannot continue sending before GitHub takes over. Reading a shared
switch is not an atomic lock with SMTP; manual handoff and uncertain SMTP outcomes
remain the boundaries where duplicates can occur.
