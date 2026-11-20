# Installing claude-desktop-session-sync (for AI agents)

You are installing a background job on the user's Mac that keeps Claude Desktop's Code session list identical across their signed-in accounts. Follow these steps in order. Stop and report to the user if any check fails; do not improvise fixes to Claude's data.

## Rules

- Do not edit, move or delete anything under `~/Library/Application Support/Claude/` or `~/.claude/`. The script copies session records by itself; hand edits can make sessions disappear from Claude.
- Do not quit or restart Claude Desktop. You may be running inside it, and the install works while it is open.
- Do not run `sudo`.

## 1. Check the machine

```bash
sw_vers -productName
python3 --version
ls "$HOME/Library/Application Support/Claude/claude-code-sessions"
```

Expected: `macOS`, Python 3.8 or newer, and at least one folder named like a UUID. If the sessions folder is missing, tell the user to open the Code tab in Claude Desktop once and stop here.

## 2. Install

```bash
mkdir -p ~/.local/share/claude-desktop-session-sync
curl -fsSL https://raw.githubusercontent.com/JackybigW/claude-desktop-session-sync/main/claude_session_sync.py -o ~/.local/share/claude-desktop-session-sync/claude_session_sync.py
python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py install
```

Expected output: a `Backed up session folders to …` line, `Installed launchd agent io.github.claude-desktop-session-sync`, then a status block.

## 3. Verify

Wait about 10 seconds, then:

```bash
python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py status
```

Expected: `launchd agent: loaded`, and one line per account folder. When Claude is running, every account other than the signed-in one should have at least as many sessions as the signed-in one, and `sessions that differ between folders` should be 0, or close to it if Claude is saving right now. Run `status` once more after another 30 seconds if it isn't.

If only one account is listed, that is fine: the user has only used one account so far, and others are picked up automatically when they sign in.

## 4. Report to the user

Tell them, in their language:

- It is installed and where the backup is.
- How many accounts and sessions `status` shows.
- To switch accounts as usual in Claude Desktop, waiting for the current reply to finish first.
- Sessions that exist only in a non-signed-in account show up after they switch to it or quit Claude once.
- To stop it: `python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py uninstall`

## Updating

Run the three commands in step 2 again. `install` replaces the agent in place and makes a fresh backup (add `--no-backup` to skip it).
