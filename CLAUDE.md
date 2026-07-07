# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Mahjong Copilot is a desktop AI assistant for Majsoul (雀魂), based on the Mortal model and MJAI protocol. It intercepts Majsoul websocket traffic via a MITM proxy, feeds game events to an AI bot, and displays step-by-step guidance (with optional in-browser overlay) and/or auto-plays via browser automation. Supports 3-person and 4-person mahjong. GPL v3 licensed.

## Commands

```bash
# Setup (Python 3.11 recommended)
python -m venv venv
source venv/bin/activate            # Windows: CALL venv\Scripts\activate.bat
pip install -r requirements.txt "numpy<2"   # numpy is unpinned but numpy 2.x breaks the pinned torch 2.2.1
PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium

# Run (PLAYWRIGHT_BROWSERS_PATH=0 is needed at runtime too — the app does not set it)
PLAYWRIGHT_BROWSERS_PATH=0 python main.py

# Run with DEBUG-level logging (default is INFO); the flag is an exact `-debug` (not argparse)
PLAYWRIGHT_BROWSERS_PATH=0 python main.py -debug

# Lint (config in .pylintrc: max-line-length=120)
pylint <file_or_package>

# Build Windows executable (PyInstaller)
scripts/generate_exe.bat
```

There is no test suite (`test/` is gitignored). Verification is manual — run the app.

Runtime artifacts are created in the repo root and gitignored: `settings.json` (auto-created with defaults), `models/` (Mortal model weight files go here), `log/`, `records/` (saved game logs), `browser_data/`, `mitm_config/`, `temp/`.

## Architecture

Data flow (see `assets/design_struct.png`):

```
Majsoul server ⇄ mitm.py (mitmproxy) ⇄ game client (Playwright browser or proxied desktop client)
                     │ WSMessage queue
                     ▼
bot_manager.py (BotManager thread) — liqi.py parses protobuf → routes by websocket flow (lobby vs game)
                     ▼
game/game_state.py (GameState) — converts Majsoul "liqi" messages to MJAI protocol events
                     ▼
bot/ (Bot) — MJAI in → MJAI reaction out
                     ▼
game/automation.py → game/browser.py (clicks, overlay)   +   gui/ (tkinter display)
```

- **Two protocols**: Majsoul's protobuf-based "liqi" protocol ([liqi.py](liqi.py), definitions in [liqi_proto/](liqi_proto/)) and the MJAI protocol (https://mjai.app) used as the bot interface. `GameState` is the translation layer and holds per-game/per-kyoku state.
- **Bots** ([bot/](bot/)): `Bot` abstract class in [bot/bot.py](bot/bot.py); implementations selected by `bot/factory.py:get_bot()` from `settings.model_type` — `"Local"` (Mortal weights via compiled libriichi), `"AkagiOT"` (online server), `"MJAPI"` (online API). Each bot declares `supported_modes` (`GameMode.MJ4P`/`MJ3P`). Adding a model type: implement `Bot`, register in `factory.py` and `MODEL_TYPE_STRINGS`.
- **libriichi / libriichi3p**: precompiled Rust binaries (.pyd/.so) providing the Mortal engine bindings. 4P falls back to the `riichi` pip package if `libriichi` is absent; 3P requires binaries manually placed in [libriichi3p/](libriichi3p/).
- **MJAI reach quirk**: a self-`reach` reaction gets the follow-up discard attached under a `reach_dahai` key (see `BotMjai.react`), and the next incoming self-reach message is ignored to avoid double-feeding the engine.
- **Automation** ([game/automation.py](game/automation.py)): converts an MJAI reaction into `ActionStep` lists (delays, mouse moves, clicks). Screen positions are constants in 16×9 units, scaled to the browser viewport at execution time. In-game steps are re-verified each step and cancelled if the action expired (e.g. someone else Pon'd before your Chi). Failed automation is retried from `BotManager._loop_post_msg`.
- **Auto-join session / auto-loop** ([gui/main_gui.py](gui/main_gui.py) `_build_auto_panel` + `_update_auto_loop`/`_update_auto_ui`): a dedicated panel (its own grid row) has the Auto Join + Auto Loop toggles, two minutes inputs (`auto_join_timer`, `auto_loop_interval`), live countdowns, a status line, and Save + Start/Stop. Start opens the browser, enables auto-join, and sets `_session_end_at = now + auto_join_timer*60`; the GUI update tick (main thread, ~50ms) runs the state machine off timestamps (no widget-owned timer). On expiry it stops auto-join and, once `is_in_game()` has stayed False for `_AUTO_CLOSE_GRACE_SEC` (debounced against matchmaking/disconnect gaps), closes the browser via `BotManager.close_browser()`. If `enable_auto_loop` is set it waits for the browser to fully stop, waits `auto_loop_interval` minutes, then relaunches + re-enables auto-join + restarts the session countdown. `_cancel_session()` clears all state (Stop, or manually toggling Auto Join off). Note `GameBrowser.stop(False)` keeps the thread ref so `is_running()` stays truthful during teardown (a relaunch must not race the dying thread).
- **Threading model**: tkinter GUI runs on the main thread and polls `BotManager` state; `BotManager._run` loops in its own thread consuming the mitm message queue; `GameBrowser` runs Playwright (sync API) in its own thread fed by an action queue; mitm, proxinject, automation tasks, and the updater each run in their own threads. Cross-thread communication is via queues and flags (e.g. `bot_need_update`), not direct calls.
- **Settings** ([common/settings.py](common/settings.py)): attribute names must match the keys in `settings.json` — saving iterates instance variables. New settings need a default value and validator in `Settings.__init__`, plus GUI exposure in [gui/settings_window.py](gui/settings_window.py).
- **Localization** ([common/lan_str.py](common/lan_str.py)): `LanStr` is the English base class; other languages subclass it and register in `LAN_OPTIONS`. Any user-visible string must be added to `LanStr` (and translated in subclasses), accessed via `settings.lan()`.
- **proxinject** ([proxinject.py](proxinject.py)): Windows-only; injects the SOCKS5 proxy into the Majsoul desktop client process. Enabling it forces mitm into SOCKS5 mode and disables the upstream proxy.
- **Dashboard** ([dashboard/](dashboard/)): optional LAN web dashboard (Flask, served in a thread from `BotManager.start`), reachable at `http://mahjongsoul.local:<dashboard_port>` — the hostname is advertised via mDNS/zeroconf ([dashboard/server.py](dashboard/server.py)). The page ([dashboard/page.py](dashboard/page.py), inline HTML) polls `/api/state` once a second. `SessionStats` ([dashboard/stats.py](dashboard/stats.py)) accumulates per-session placements/points/rank; `BotManager` feeds it by parsing `oauth2Login` (account/rank) and `NotifyGameEndResult` (placement, `gradingScore`) via [dashboard/mj_parse.py](dashboard/mj_parse.py) — note liqi fields are camelCase (`totalPoint`, `partPoint1`), and end-game placement is derived by sorting players on `totalPoint`. Live game view reads existing `BotManager`/`GameState` accessors. Toggle with `enable_dashboard` in settings. Port binding is resilient (`DashboardServer._make_server` probes with its own socket first because werkzeug's `make_server` calls `sys.exit(1)` on a bind failure instead of raising): the configured `dashboard_port` falls back to 8080 then an OS-assigned free port. `dashboard_bind_ip` binds a specific local IP instead of all interfaces (`SO_REUSEADDR` lets it share e.g. port 80 with another `0.0.0.0:80` app when the machine has a second IP); the hostname is advertised pointing at that IP. `BotManager.dashboard_url()` returns the actually-bound URL (shown live in the GUI status bar).
- **Game recorder** ([game/game_record.py](game/game_record.py)): when `enable_game_record` is set, `GameState` accumulates the full game event stream in `mjai_msgs_recorded`, and `BotManager` writes it on game end to `records/*.mjson` — JSONL in MJAI format (one event per line), suitable for Mortal-style training datasets. Records shorter than `MIN_RECORD_EVENTS` (aborted/empty games) are skipped.
- **Logging** ([common/log_helper.py](common/log_helper.py)): single `majsoul_copilot` logger, configured once by `LogHelper.config_logging(debug=...)` from `main.py`. `debug` (set by the `-debug` launch flag) gates the level: DEBUG when present, else INFO — the logger level is the first gate, so DEBUG is dropped before handlers. `SingleLineFormatter` enforces fixed-width, one-physical-line-per-record output (newlines in a message/traceback are flattened to `\n`) so the log stays column-aligned and grep-able; console gets TTY-only ANSI color for WARNING+, the file never. `QueueHandler` exists for a future in-GUI viewer but is currently unused (the GUI just opens the log file). Note: liqi parse suppresses the `.lq.Route.*` keepalive flood ([liqi.py](liqi.py) `KEEPALIVE_SERVICES`), and unknown-message parse warnings are single-line with the exception type.
