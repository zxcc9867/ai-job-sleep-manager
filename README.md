# AI Job Sleep Manager

**English** | [한국어](README.ko.md) | [日本語](README.ja.md)

**Keep your Windows PC awake while AI development jobs run, then request sleep or a normal shutdown after the jobs finish.**

For developers who leave Codex or Claude Code working while they step away. The app watches observed job activity rather than treating an open AI window as an active job.

> **Status: early implementation; real-world validation in progress.** The automated test suite passes. Building and starting the Windows app have been verified in observation mode. Actual PC sleep/resume/shutdown and complete unattended workflows across all target environments have not been verified.

![Dashboard showing main jobs, child jobs and power status](docs/images/running.png)

### Simple daily controls

- Set the delay and **Sleep / Normal shutdown** directly on the dashboard, then press **저장** (Save). Saving does not turn management on.
- Press the main **자동 관리 켜기 / 끄기** (Turn management on / off) button. The same panel clearly shows whether management is enabled, the applied policy, and whether the app is previewing or controlling power.
- Open **추가 설정** (Additional settings) for the execution mode, AI detection, theme and startup options. **목록 옵션** (List options) reveals completed and previous jobs; **세부 기록 보기** (Detailed records) reveals timestamps, connection evidence and session IDs.

![Paused management with a clear start button and previous-failure explanation](docs/images/failure.png)


*Screenshots show the real Korean UI with isolated demonstration data. They do not show private conversations or prove successful PC sleep/shutdown. README language selection does not change the app's UI language.*

## Contents

- [Features](#features)
- [Supported environments](#supported-environments)
- [Quick start](#quick-start)
- [Connect and configure](#connect-and-configure)
- [Observation and actual power control](#observation-and-actual-power-control)
- [Sleep and shutdown rules](#sleep-and-shutdown-rules)
- [Recovery after a Windows restart](#recovery-after-a-windows-restart)
- [Local data and privacy](#local-data-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Development and packaging](#development-and-packaging)
- [Verification and limitations](#verification-and-limitations)

## Features

### Understand which jobs remain

Each row always shows an AI icon **and its name**: a terminal mark for Codex and a starburst for Claude Code. The selected-job header also shows the AI, project and whether it is a main or child job. These are app identification symbols, not downloaded official logos.

Display identities live in `sleep_manager/providers.py`. A future adapter can reuse this registry; an unregistered provider gets a generic AI icon and its ID, never another AI's name. Adding a display identity alone does **not** add job detection for that AI.

UUID-only automatic titles use the observed latest instruction when available. The detail panel puts current activity first, uses regular text for long instructions and keeps your scroll position when the same job refreshes.

The dashboard separates main jobs from child jobs, with counts for running, awaiting input, unknown and completed states. Expand a parent to see its children; select a row to read the latest instruction and public progress explanation. Completed jobs are hidden by default and can be shown with the checkbox.

- **Instruction → activity:** distinguish the conversation title from your latest instruction, such as “continue,” and the observed work, such as reading a file or running a command.
- **Public updates and timestamps:** show the AI's most recent public response, the last observed activity and a short tool-activity history. Older activity is marked as awaiting new records; silence is not treated as completion.
- **Codex plans:** show steps only when Codex explicitly shares a supported plan. Progress percentages and remaining time are not invented.
- **Child work:** confirmed Codex parent IDs connect related jobs. If a child's direct instruction is unavailable, a known parent's instruction is labeled separately. Remaining child work can be read from the parent detail panel.
- **Clean instructions:** attachment headers and known internal context messages are excluded. Image-only messages receive an attachment label. Confirmed internal Codex guardian approval sessions are excluded from development-job counts.
- **Names:** Codex titles and project names come from local metadata. You can assign a custom name with `이름 지정` (Name). Claude supports the project name and custom names; automatic Claude conversation titles are not implemented.

![Latest instruction and public progress detail](docs/images/activity.png)

### Current connections and historical unconfirmed records

Unfinished records restored when the app starts are separated from the current list when there is no current connection evidence. Use **목록 옵션 → 이전 실행 기록 보기** (Show historical unconfirmed and interrupted records) to inspect them. The notice shows how many are hidden and explicitly states that they still block automatic power transitions.

**작업 새로고침** (Recheck connections) reads new lifecycle events and checks Claude's local session registry. A Claude connection is confirmed only when the session ID, PID and exact Windows process creation time match; stale PID files and reused PIDs do not prove a connection. A connected process is not evidence that its job is running or finished. The selected detail shows connection evidence and the last status-event time. New events bring a restored job back into the current list. Active jobs are never hidden just because they are old.

This is a display separation, not deletion or automatic completion. Missing/inaccessible registry data remains unconfirmed. The existing unknown-state power blocker is preserved; hiding an old record does not enable shutdown. After checking the original AI window, you can select an inactive unknown or interrupted job and explicitly confirm that it ended. The app does not automatically discard unresolved background jobs.

### Claude Code task content

For Claude sessions already observed through hooks, the app matches the session ID to a local file under `.claude/projects/*/<session-id>.jsonl`. It shows the latest user instruction, the latest public assistant text, and observed tool activity such as file reads, edits, searches and terminal commands.

![Claude instruction and public response, loaded through the display reader](docs/images/claude.png)

- Tool results, injected metadata and private thinking blocks are not displayed as user instructions. A new instruction clears the previous response.
- Transcript files alone do not create running jobs or establish completion. Hooks remain responsible for Claude lifecycle state.
- Missing, unreadable or unmatched transcripts are left unavailable. Reads are bounded to the most recent 32 MiB per file, with a 64 MiB read budget per refresh. A user-message boundary must be found before responses are shown.
- When a new prompt hook arrives before its transcript entry, the old request is withheld, allowing a two-second timestamp difference between hook and transcript records.
- Claude's latest public text can be a final answer. Its presence does not mean that the job is still running or has finished.
- Claude automatic titles, detailed plans and child-agent transcript content are not implemented. Remote transcripts are not fetched.

### Readable controls and saved-setting feedback

The app uses larger Korean fonts, stronger text contrast, wider rows and additional line spacing. Under `추가 설정 → 화면·시작` (Open settings → Display and startup), choose `밝은 모드 / 다크 모드` (Light / Dark) for the theme and `기본 / 크게 / 아주 크게` (Default / Large / Extra large) for text size. Both apply immediately and survive restarts without changing power settings, job states or unsaved power-setting edits. Open diagnostics and dropdown menus also follow the selected theme.

![Dark mode and theme selection](docs/images/dark-settings.png)

Wide windows show the job list and detail side by side; narrow windows stack them. Scroll the page for controls below the fold, and use the list's horizontal scrollbar or resize columns for long titles.

After `저장` (Save settings), a checkmark, timestamp and saved values appear near the button. Edited values are labeled unsaved, while the header continues to show the applied policy. Failed saves retain the previous policy. Saving a time/action does not change the execution mode.

![Mode selection, saved values and readable settings](docs/images/settings.png)

## Supported environments

The target is **local, native Windows AI development work**.

| Environment | Integration | Verification scope |
| --- | --- | --- |
| Codex desktop | Hooks and local lifecycle logs | Running jobs observed; full completion/wait/resume workflow pending |
| Claude Code in Claude desktop | Claude Code hooks; matching local transcripts for display | Desktop end-to-end workflow pending |
| Codex CLI in a VS Code terminal | Hooks and local lifecycle logs | Separate CLI lifecycle observed; normal completion validation pending |
| Claude Code in a VS Code terminal | Hooks; matching local transcripts for display | Hook reception and local content extraction verified separately; complete VS Code workflow pending |

Ordinary Claude chat, Cowork, browser chat, WSL and remote machines are outside automatic connection support. An implemented adapter does not mean every workflow is verified. The app does not execute AI tasks, approve permissions or deploy your projects.

## Quick start

Requirements: Windows, local Codex or Claude Code, and **Python 3.10+ with Tkinter** for source execution. No external Python package is required at runtime; no AI API key or server account is needed by this app.

In PowerShell:

```powershell
git clone https://github.com/zxcc9867/ai-job-sleep-manager.git
cd ai-job-sleep-manager
python app.py --dry-run
```

Alternatively, download and extract GitHub's **Code → Download ZIP**, then run the last command from that folder. Check Tkinter with `python -m tkinter` if needed.

`--dry-run` starts **observation mode**: no sleep-prevention request, sleep or shutdown is performed. Windows can still sleep according to its own policy. You can explicitly enable actual power control from the app. The Connect button still modifies hook configuration in observation mode.

To launch with the saved execution mode, close any instance using the same data directory and run:

```powershell
python app.py
```

`launch.cmd` starts the packaged app if available, otherwise the source app. Without `--dry-run`, it restores the saved mode; a new installation starts in observation mode. The Git repository does not include executable bundles. Build the folder-based app using the instructions below; there is currently no installer, auto-update system or published executable release download.

### Buttons with a clear purpose

Settings are grouped into **Power behavior**, **Codex·Claude detection** and **Display/startup**. The light interface uses dark text and one blue accent; buttons include nearby descriptions and hover help.

| Korean button | What happens when clicked |
| --- | --- |
| `자동 관리 켜기 / 끄기` | Enable / stop sleep protection during AI work and the configured completion action |
| `Codex·Claude 감지 설정` | Back up existing AI settings and add job start/end notifications to this app |
| `이 앱의 감지 설정 제거` | Remove this app's notifications and stop automatic management |
| `작업 새로고침` | Read the latest job records; never infer completion |
| `이전 실행에서 완료를 확인하지 못한 작업 보기` | Show older jobs without a confirmed end; hiding them does not remove power blockers |
| `상태·오류 확인` | Open a read-only summary and detailed record, with a local copy button |
| `로그 보기` (main screen) | Open detailed AI status events and power-action logs directly; settings and management stay unchanged |

![AI detection settings and button explanations](docs/images/detection.png)

![Readable status and error summary](docs/images/diagnostics.png)

## Connect and configure

1. Choose a permanent program location; hook configuration includes that path.
2. Start in observation mode and open `설정 열기` (Open settings) → `Codex·Claude 감지` (AI detection).
3. Click `Codex·Claude 감지 설정` (Connect AI job detection). Existing user settings are preserved and privately backed up before observer hooks are added.
4. Restart the AI apps and terminal sessions, and complete any hook trust review requested by those tools. Already-running sessions can have missing events.
5. Run a short task and check the job list and `상태·오류 확인` (Diagnostics). A saved connection configuration is not proof that events are arriving.
6. Choose a delay of **1–120 whole minutes** and `절전` (Sleep) or `정상 종료` (Normal shutdown), then click `저장` (Save settings). The default delay is 10 minutes and the default action is sleep.
7. In Settings → Power behavior, select `실제 전원 제어` (Actual power control). Selection applies immediately. Windows requests administrator consent; approve it to reopen the app using the same data directory. Cancelling keeps the existing mode and pause state.
8. Check the applied mode and `자동 관리 켜기` (Enable automatic management). Switching modes does not resume previously paused management. Actual shutdown privilege is still checked separately.
9. Keep the app open or minimized. External sleep-prevention requests can defer sleep, but do not block normal shutdown.

`이 앱의 감지 설정 제거` (Disconnect) removes only matching observer hooks for the current launch path and pauses management. Connect does not change AI approval or deployment policies.

## Observation and actual power control

Use **Settings → Power behavior → select a mode**. There is no extra Apply button. If actual control was saved, a normal unelevated launch also requests administrator consent. Cancelling opens that launch in observation mode without overwriting saved settings. [Windows UAC behavior](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shellexecutew).

| Mode | Behavior |
| --- | --- |
| `관찰 모드` — Observation | Displays jobs and simulated timing; no physical power requests |
| `실제 전원 제어` — Actual power control | Prevents automatic sleep while observed jobs run; requests the configured transition once its conditions are satisfied |

Switching to actual control rechecks activity and starts a fresh full delay. It never immediately sleeps or shuts down just because an observation-mode timer expired. Returning to observation releases this app's physical sleep-prevention request. Failed mode changes report the error rather than claiming success. Pause and unknown-state blockers remain in effect after switching.

**Mode changes are saved for the next ordinary launch.** A new installation starts in observation mode. An explicit `--dry-run` launch forces observation for that launch, even if actual control was saved. The settings panel shows the applied mode and the next ordinary launch mode. Saved delay, completion action, pause state and text size persist independently.

## Recovery after a Windows restart

If Windows restarts and an observed AI job has no confirmed end event from before that restart, the app marks it **Unconfirmed before restart** when it next launches. The record alone cannot tell whether the job had finished before Windows restarted. The old job remains a power-transition blocker. Select it, check the original Codex or Claude window and its work, then use `이 작업이 끝났음을 확인` (Confirm job ended) only when you know it has stopped. The app records that confirmation and starts a fresh full delay; it does not infer completion from elapsed time or from a missing process.

![Unconfirmed job and manual end confirmation after a restart](docs/images/recovery.png)

`설정 열기` (Power settings) includes an optional **Start when signing in to Windows** switch. It starts the app after user sign-in; it cannot keep the PC awake or resume an AI job before sign-in. Diagnostics records limited local power decisions, mode changes and restart detection without prompt or response bodies. Actual Windows shutdown and a Windows Update restart remain distinct events: the app cannot prevent every system-initiated restart.

## Sleep and shutdown rules

| Situation | Sleep selected | Normal shutdown selected |
| --- | --- | --- |
| No jobs have been observed yet | Wait; no countdown | Wait; no countdown |
| At least one main/child/background job is running | Prevent automatic sleep; cancel countdown | Same |
| Jobs await human input/approval; none are running | Eligible for sleep after the delay | Do not schedule shutdown; allow existing Windows sleep policy |
| All observed work has finished | Request sleep after the delay | Request normal shutdown after the delay |
| A pre-restart job has no confirmed end, or its state is unknown | Hold the transition until the user verifies the original AI session and confirms that job has ended | Same |
| New activity or mouse/keyboard input during countdown | Cancel/reset the countdown | Same |
| Job state is unknown | Hold transition; temporary recovery protection | Do not request shutdown |
| Pause or close this app | Release this app's prevention request | Same |

![Simulated sleep countdown](docs/images/countdown.png)

![Paused monitoring](docs/images/paused.png)

If uncertainty persists with no other running work, recovery protection lasts **30 minutes by default**. After that, this app releases its prevention request without requesting sleep or shutdown, leaving Windows policy in control. Continued job protection is not guaranteed in that state. Unknown state is never silently converted to completed.

![Unknown state requiring attention](docs/images/unknown.png)

Other applications' power requests and permission/query failures can block **sleep**. Sleep retries are approximately 30 seconds apart; inspect Diagnostics. Display blanking is allowed; the app does not keep the monitor lit. These requests do not block normal shutdown.

### Normal shutdown is a request, not forced termination

![Simulated normal-shutdown countdown](docs/images/shutdown.png)

Shutdown uses Windows `ExitWindowsEx(EWX_POWEROFF)` without `EWX_FORCE` or `EWX_FORCEIFHUNG`. It does not kill processes or automatically save every application's documents. Applications can require user action or block shutdown.

Normal shutdown rechecks AI jobs, user input and settings immediately before the Windows call. Chrome, ChatGPT, Claude and driver sleep-prevention requests, or a failed `powercfg` query, do not block shutdown. Permission readiness is checked against the actual `SeShutdownPrivilege`; administrator status alone is not a readiness result.

Known preflight timeouts and SQLite locks are rechecked every **30 seconds, up to 10 failed checks**. New work, input or changed settings cancel that retry cycle and restart the full delay. Permanent errors stop management and retain their cause. After the Windows call starts, the app never retries automatically, including when privilege restoration fails and delivery is uncertain.

The last action state and the paused setting are persisted together before submission. The dashboard shows execution mode, management state, applied settings and the next action. Diagnostics includes version, run ID, PID, the exact SQLite path and persisted state. A restart or update does not enable previously paused management. “Shutdown requested” means Windows accepted the request, not that the PC has been confirmed powered off.

For a daytime acceptance test, save your work, reopen the updated app, choose actual power control and a one-minute normal-shutdown delay, and explicitly resume management. Run a short AI job and let it finish without further keyboard or mouse input. Check that the PC powers off; after starting it again, compare the app's `shutdown_requested` audit and Windows System shutdown events. No actual shutdown test has been completed by the agent.

## Local data and privacy

| Item | Location or policy |
| --- | --- |
| App data | `%LOCALAPPDATA%\AIJobSleepManager\events.sqlite3` |
| Persisted | Provider/session/turn/child IDs, status events, timestamps, settings, text size, project folder names and custom job names |
| Not copied to the app database | Prompt bodies, source-code bodies, AI response bodies or typed keyboard content |
| Claude hook configuration | `%USERPROFILE%\.claude\settings.json` |
| Codex hook configuration | `%USERPROFILE%\.codex\hooks.json` |
| Configuration backups | Unique files beside the original configuration, restricted to the user |

Public instructions and responses are shortened to 500 characters each and held only in memory for display. They are not included in diagnostics or transmitted to an external server. Private thinking and raw tool inputs/outputs are not retained as activity details. The original AI tools manage their own transcript storage. Hook handlers do not inject messages or decisions into AI output.

Codex display metadata also uses local titles/project/parent information and explicit plans. Missing latest Codex instructions can be recovered from up to 32 MiB of recent log data only when a current-turn boundary is proven. Display data never substitutes for lifecycle evidence.

Codex lifecycle recovery is separate: after a large log gap, the observer scans up to 32 MiB for explicit turn start/end and matched input-wait events. This prevents a missed start from leaving an active job at **Needs verification**. Missing, malformed or inconsistent evidence remains unknown; assistant text and recent file activity are not proof of execution or completion.

Automatic connection targets default user paths. If `CODEX_HOME` or `CLAUDE_CONFIG_DIR` is set, the Connect UI stops rather than changing a guessed location; manual custom-path setup is not yet documented. Display readers can use these configured roots, but this does not install hooks there.

Before moving the app: disconnect at the old location, restart AI sessions, close the manager, then reconnect from the new location. Move the **entire** executable folder, including both EXEs and `_internal`. Deleting the program folder does not remove local data or configuration backups. Do not delete entire AI configuration files just to uninstall the observer.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Claude job is visible but its content is missing | A matching local transcript and recent user-message boundary are needed. Check `claude_display_status` in Diagnostics. Child-agent details and remote transcripts are not supported. |
| No job appears | Connect, restart AI sessions, complete hook trust review and check new events in Diagnostics. A transcript alone does not register a running Claude job. |
| Generic job title | Use project name/session ID to identify it and assign a custom name. Claude automatic titles are unavailable. |
| Settings saved but no shutdown | Check observation mode, paused management, unknown jobs, input/approval waits and actual shutdown privilege. Saving does not enable actual control. External sleep-prevention requests do not block normal shutdown. |
| `unobserved` appears as a job | Update to the current build. `unobserved` was a placeholder created from an open AI process, not a real session. Open apps no longer create job rows; use **Connect AI job detection** and start a new AI session if work is not detected. |
| Countdown keeps resetting | New work or user input resets the full delay. |
| No administrator button | Select Actual power control; Windows permission approval and relaunch are handled automatically. Cancelling preserves the existing mode. |
| Management paused after restart | Pause state persists, including after a shutdown request. Resume when ready. |
| Already-running message | Use the existing window; change its mode in Power settings. |
| Detection stopped after moving the app | Remove the old path's hooks and reconnect at the new path. |

Read-only diagnostics:

```powershell
python app.py --diagnose
powercfg /a
powercfg /requests
```

Power-request inspection may require administrator permissions. A bounded CLI observation run (maximum 60 seconds):

```powershell
python app.py --dry-run --observe-seconds 10
```

## Development and packaging

Tests use substitute power boundaries and do not put the PC to sleep or shut it down:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py sleep_manager scripts
```

There is currently no separate linter or static type-check configuration.

Build on Windows:

```powershell
.\build.ps1
```

This creates `.build-env`, installs the pinned **PyInstaller 6.22.3** build dependency, and packages the app. Dependency downloads require network access.

```text
dist/AIJobSleepManager/
├── AIJobSleepManager.exe
├── AIJobSleepHook.exe
└── _internal/
```

The resulting folder runs without a separate Python installation. It is unsigned and must be kept together.

```powershell
.\dist\AIJobSleepManager\AIJobSleepManager.exe --dry-run
```

To regenerate the eight standard demonstration screenshots on a visible Windows desktop (Pillow is needed only for capture):

```powershell
python -m venv .docs-env
.\.docs-env\Scripts\python.exe -m pip install Pillow
.\.docs-env\Scripts\python.exe scripts\capture_screenshots.py
```

Capture uses temporary data and substitute power inputs; windows briefly come to the foreground. It does not alter AI settings or perform power transitions.

### Source layout

```text
app.py                       # GUI, hook, diagnostics and observation entry point
sleep_manager/
  engine.py                  # Job state and timing policy
  controller.py              # Event ingestion and pre-transition checks
  integrations.py            # Hook normalization and installation
  codex_logs.py              # Codex lifecycle observation
  claude_details.py          # Display-only Claude transcript reader
  request_history.py         # Bounded latest Codex instruction recovery
  activity_view.py            # Ephemeral public activity
  job_view.py                # Titles, groups and display rows
  storage.py                 # Local metadata/settings database
  power.py                   # Windows power and input boundaries
  ui.py                      # Tkinter interface
  startup.py                 # Optional current-user sign-in launch
scripts/capture_screenshots.py
tests/
docs/images/
README.md / README.ko.md / README.ja.md
build.ps1 / AIJobSleepManager.spec / launch.cmd
```

## Verification and limitations

As of **2026-10-04**:

| Check | Result |
| --- | --- |
| Automated tests | 245 tests pass, including theme persistence/switching, bounded shutdown retries, cancellation, persistent stop reasons and duplicate-request prevention; power transitions are mocked |
| Claude local content extraction | Instructions and public responses extracted from 5 locally matched sessions; separate from lifecycle verification |
| Source compilation, packaging and GUI smoke test | Passed; rebuilt GUI and hook installed after backups, with the existing shortcut path preserved; isolated observation-mode launch passed |
| Windows prevention request create/set/clear/close | Previously verified with real APIs; sleep call excluded |
| Packaged hook stdin handling and body removal | Verified; packaged hook → isolated SQLite → controller countdown → one simulated shutdown → paused-state restoration passed |
| Shutdown privilege readiness | Actual SeShutdownPrivilege probe available in this Windows session; no shutdown call made |
| Codex desktop running-state observation | Verified from local records |
| Codex CLI 0.139.0 / Claude Code 2.1.259 historical checks | Lifecycle reception observed; normal-work validation was limited by model/version or account-credit issues |
| All four environments: finish, wait, resume | Incomplete |
| Actual PC sleep/resume/shutdown and three unattended nights | Not tested |

Log formats are version-dependent. Claude Stop is a completion candidate before other hooks can continue work; it is not proof that every background activity is finished. Not all long-running servers, child jobs or sessions are guaranteed to be discoverable. Display readers cannot fill those lifecycle gaps.

Manual sleep, closing a laptop lid, low battery and Modern Standby have separate OS policies. Passive use such as video playback or downloads is not fully detected; other power requests are used as a safeguard for sleep only. Event arrival and Windows transition cannot be perfectly atomic. Pausing or closing this app does not release requests owned by other applications.
