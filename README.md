# claude-desktop-session-sync

Keep your Claude Desktop **Code** sessions when you switch accounts.

Claude Desktop on macOS keeps a separate session list per account. Switch from your work account to your personal one and the Code sidebar is empty, even though every conversation is still on disk and shared between accounts. This tool keeps the session lists of all your accounts identical in the background, so switching accounts shows the same sessions, with their titles, archive state and parent/child links.

[中文说明](README.zh-CN.md)

> Unofficial and not affiliated with Anthropic. It reads and copies files inside Claude Desktop's data folder, whose layout can change in any release.

## Install

### Ask your AI agent

Paste this into Claude Code, Codex or any agent with shell access:

```text
Install claude-desktop-session-sync on this Mac by following
https://github.com/JackybigW/claude-desktop-session-sync/blob/main/INSTALL.md
```

### Or run it yourself

```bash
mkdir -p ~/.local/share/claude-desktop-session-sync && curl -fsSL https://raw.githubusercontent.com/JackybigW/claude-desktop-session-sync/main/claude_session_sync.py -o ~/.local/share/claude-desktop-session-sync/claude_session_sync.py && python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py install
```

Requirements: macOS, Claude Desktop with the Code tab used at least once, Python 3.8+ (the one from Xcode Command Line Tools is fine). No other dependencies.

`install` backs up your session folders to `~/claude-code-sessions-backup-<time>`, installs a launchd agent and prints the status. Nothing else needs to be configured: accounts are discovered automatically, including ones you add later.

## Use

Switch accounts in Claude Desktop as usual. Wait for the current reply to finish before switching.

```bash
python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py status       # accounts, counts, mismatches
python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py sync --dry-run  # what a sync would change
python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py uninstall    # stop syncing
```

A healthy `status` shows the same session count for every account and `sessions that differ between folders: 0`. A difference of one or two while Claude is running catches up within seconds.

## How it works

| Data | Location | Per account? |
|-|-|-|
| Conversations | `~/.claude/projects/<project>/<id>.jsonl` | No, shared |
| Code session list | `~/Library/Application Support/Claude/claude-code-sessions/<account>/<org>/local_*.json` | Yes |

Only the session list needs syncing. A launchd agent runs the script whenever one of those folders changes, and every 30 seconds:

- **Claude running:** records are copied from the signed-in account (read from `lastKnownAccountUuid` in Claude's `config.json`) to every other account. The signed-in account's folders are never written, because the app owns them. If the signed-in account can't be determined, nothing is synced.
- **Claude not running:** a two-way merge across all accounts.
- **Which copy wins:** the record with the later `lastActivityAt`, then `lastTurnEdgeAt`, then `lastFocusedAt`; file modification time only breaks ties (renames, archiving).
- **Deletions:** Claude leaves a `deleted_<id>` marker when you delete a session. Markers are copied and the matching record removed, so deleted sessions don't come back.
- **Safety:** records that fail to parse or whose `sessionId` doesn't match the file name are skipped. Writes go to a temp file and are renamed into place with mode 0600.

Files are copied byte for byte; the script never edits a record. Claude validates every record when it loads the list and silently drops ones it doesn't accept, so hand-edited records can make sessions disappear (look for `Dropping session` in `~/Library/Logs/Claude/main.log`).

Why not symlink the account folders together? Claude opens the account folder with `O_NOFOLLOW` when saving, so saves into a symlinked folder fail with `ENOTDIR` and new sessions are never written.

## Limits

- macOS only.
- Only Code sessions. Cowork sessions, scheduled tasks and account-level settings (sidebar groups, connectors) are not synced.
- If you switch within a few seconds of a save, the newest change may arrive one run later. Conversations are unaffected.
- What happens to a task that is still running at the moment you switch is untested.
- While Claude is running, sessions that exist only in an inactive account reach the signed-in one when you switch to it or when Claude quits.

## Troubleshooting

- `launchd agent: not installed`: run `install` again.
- Sessions missing in Claude but present on disk: check `~/Library/Logs/Claude/main.log` for `Dropping session … (field: …)`.
- `Failed to save session … ENOTDIR` in Claude's log: an account folder was replaced by a symlink. Quit Claude, replace the symlink with a real folder, run `sync`.
- Restoring: quit Claude, then copy individual `local_*.json` files back from the backup. Don't overwrite the whole folder with an old backup; you'd lose everything since.
- Sync log: `~/Library/Logs/claude-desktop-session-sync.log`.

## Development

```bash
python3 -m unittest discover -s tests
```

The tests use temporary folders and never touch Claude's data. `sync --dry-run` is a safe way to try changes against real data.

## License

MIT
