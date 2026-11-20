#!/usr/bin/env python3
"""Keep Claude Desktop's Code session list identical across signed-in accounts.

Claude Desktop (macOS) keeps one session index per account under
~/Library/Application Support/Claude/claude-code-sessions/<account>/<org>/.
Switching accounts shows only that account's sessions, although the
conversations themselves (~/.claude/projects/*.jsonl) are shared.

This script copies session records between those folders:
  * while Claude is running: from the signed-in account to every other account,
    never writing the signed-in account's folders (the app owns them);
  * while Claude is not running: a two-way merge across all accounts.

A launchd agent runs it when a folder changes and every 30 seconds.

Unofficial. It relies on Claude Desktop's internal file layout, which can
change in any release. Requires macOS and Python 3.8+, no dependencies.

Commands:
  install     back up the session folders, install and start the launchd agent
  uninstall   stop and remove the launchd agent (session data is untouched)
  status      show accounts, session counts and whether they match
  sync        sync once now (--dry-run to only show what would change)
"""
import argparse
import fcntl
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

__version__ = "1.0.0"

HOME = Path.home()
APP_DATA = HOME / "Library/Application Support/Claude"
LABEL = "io.github.claude-desktop-session-sync"
INSTALL_DIR = HOME / ".local/share/claude-desktop-session-sync"
PLIST = HOME / "Library/LaunchAgents" / (LABEL + ".plist")
LOG = HOME / "Library/Logs/claude-desktop-session-sync.log"
LOG_LIMIT = 1024 * 1024
INTERVAL = 30

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TIME_FIELDS = ("lastActivityAt", "lastTurnEdgeAt", "lastFocusedAt")
DELETED = "deleted_"


def log(message, echo=False):
    if echo:
        print(message)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > LOG_LIMIT:
        os.replace(str(LOG), str(LOG) + ".old")
    with LOG.open("a") as out:
        out.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))


def discover(base):
    """Return {account_id: [session folder, ...]} for <account>/<org> folders.

    While switching accounts Claude briefly signs in with a mismatched
    account/org pair and leaves an empty folder for it. So when an account has
    folders with sessions, its empty folders are ignored; a brand-new account's
    only folder is kept even while empty, so it is filled before first use.
    Symlinked folders are skipped: Claude refuses to save into them.
    """
    accounts = {}
    if not base.is_dir():
        return accounts
    for account in sorted(base.iterdir()):
        if not UUID.match(account.name) or not account.is_dir() or account.is_symlink():
            continue
        folders = [org for org in sorted(account.iterdir())
                   if UUID.match(org.name) and org.is_dir() and not org.is_symlink()]
        used = [org for org in folders if any(org.glob("local_*.json"))]
        if used or folders:
            accounts[account.name] = used or folders
    return accounts


def symlinked_folders(base):
    if not base.is_dir():
        return []
    return [p for p in base.glob("*/*") if p.is_symlink() and UUID.match(p.name)]


def claude_running():
    out = subprocess.run(["/bin/ps", "-axo", "comm"], capture_output=True, text=True, check=True).stdout
    return any("/Claude.app/Contents/" in line for line in out.splitlines())


def signed_in_account(config):
    try:
        account = json.loads(config.read_text()).get("lastKnownAccountUuid")
    except (OSError, ValueError, AttributeError):
        return None
    return account if isinstance(account, str) and UUID.match(account) else None


class Folder:
    """One session folder: its valid records and deletion markers."""

    def __init__(self, path):
        self.path = path
        self.records = {}  # file name -> (version, path)
        self.deleted = set()  # session uuid
        for entry in path.iterdir():
            name = entry.name
            if name.startswith(DELETED) and entry.is_file():
                self.deleted.add(name[len(DELETED):])
            elif name.startswith("local_") and name.endswith(".json") and entry.is_file():
                version = record_version(entry)
                if version is not None:
                    self.records[name] = (version, entry)


def record_version(path):
    """Newer activity wins; equal activity (renamed, archived) falls back to mtime.

    Returns None for unreadable records or ones whose sessionId does not match
    the file name, so a half-written or foreign file is never copied.
    """
    try:
        record = json.loads(path.read_text())
        mtime = path.stat().st_mtime_ns
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("sessionId") != path.name[:-len(".json")]:
        return None
    times = []
    for field in TIME_FIELDS:
        value = record.get(field)
        times.append(value if isinstance(value, (int, float)) else 0)
    return tuple(times) + (mtime,)


def copy_atomic(src, dst):
    stat = src.stat()
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".session-sync-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(src.read_bytes())
        os.chmod(tmp, 0o600)
        os.utime(tmp, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(tmp, str(dst))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def merge(src, dst, dry_run=False):
    """Bring dst up to date with src. Writes only to dst. Returns change descriptions."""
    changes = []
    for uuid in sorted(src.deleted - dst.deleted):
        changes.append("tombstone %s" % uuid)
        if not dry_run:
            copy_atomic(src.path / (DELETED + uuid), dst.path / (DELETED + uuid))
        dst.deleted.add(uuid)
    for uuid in sorted(dst.deleted):
        name = "local_%s.json" % uuid
        if name in dst.records:
            changes.append("delete %s" % name)
            if not dry_run:
                dst.records[name][1].unlink()
            del dst.records[name]
    for name, (version, path) in sorted(src.records.items()):
        if name[len("local_"):-len(".json")] in dst.deleted:
            continue
        current = dst.records.get(name)
        if current is None or version > current[0]:
            changes.append(("add %s" if current is None else "update %s") % name)
            target = dst.path / name
            if not dry_run:
                copy_atomic(path, target)
            dst.records[name] = (version, target)
    return changes


def plan(accounts, running, active):
    """Return (source folders, target folders), or None when it is unsafe to sync."""
    if running:
        if active is None:
            return None
        sources = accounts.get(active, [])
        targets = [f for account, folders in accounts.items() if account != active for f in folders]
        return sources, targets
    every = [f for folders in accounts.values() for f in folders]
    return every, every


def sync(base, config, running, dry_run=False, echo=False):
    accounts = discover(base)
    if sum(len(f) for f in accounts.values()) < 2:
        return 0
    pairs = plan(accounts, running, signed_in_account(config) if running else None)
    if pairs is None:
        # Not logged: this repeats every run until Claude quits. `status` reports it.
        if echo:
            print("skipped: Claude is running but the signed-in account is unknown")
        return 0
    folders = {}

    def folder(path):
        if path not in folders:
            folders[path] = Folder(path)
        return folders[path]

    sources, targets = pairs
    total = 0
    # Every source is merged into every target directly, so one pass converges.
    for src in sources:
        for dst in targets:
            if src == dst:
                continue
            changes = merge(folder(src), folder(dst), dry_run)
            total += len(changes)
            if changes:
                summary = "%s -> %s: %d change(s)" % (short(src), short(dst), len(changes))
                if dry_run:
                    print(summary + "".join("\n  " + c for c in changes))
                else:
                    log(summary, echo)
    return total


def short(folder):
    return "%s/%s" % (folder.parent.name[:8], folder.name[:8])


def cmd_sync(args):
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    with (INSTALL_DIR / "lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        total = sync(args.base, args.config, claude_running(), args.dry_run, echo=args.verbose)
        if args.dry_run or args.verbose:
            print("%d change(s)%s" % (total, " would be made" if args.dry_run else ""))


def launchctl(*args):
    return subprocess.run(["/bin/launchctl"] + list(args), capture_output=True, text=True)


def python_for_launchd():
    """Prefer the system Python; fall back to the one running this command."""
    system = "/usr/bin/python3"
    has_clt = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True).returncode == 0
    if has_clt and os.path.exists(system):
        return system
    return sys.executable


def cmd_install(args):
    if sys.platform != "darwin":
        sys.exit("Only macOS is supported.")
    accounts = discover(args.base)
    if not accounts:
        sys.exit("No Claude Desktop Code sessions found under %s.\n"
                 "Open Claude Desktop and use the Code tab once, then retry." % args.base)
    if not args.no_backup:
        backup = HOME / ("claude-code-sessions-backup-" + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copytree(str(args.base), str(backup), symlinks=True)
        print("Backed up session folders to %s" % backup)

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    script = INSTALL_DIR / "claude_session_sync.py"
    if not script.exists() or not script.samefile(__file__):
        shutil.copyfile(__file__, str(script))
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with PLIST.open("wb") as out:
        plistlib.dump({
            "Label": LABEL,
            "ProgramArguments": [python_for_launchd(), "-I", str(script), "sync",
                                 "--base", str(args.base), "--config", str(args.config)],
            # New accounts appear as new folders under base; the interval catches the rest.
            "WatchPaths": [str(args.base)] + [str(f) for fs in accounts.values() for f in fs],
            "StartInterval": INTERVAL,
            "ThrottleInterval": 3,
            "RunAtLoad": True,
            "ProcessType": "Background",
            "StandardErrorPath": str(LOG) + ".err",
        }, out)
    domain = "gui/%d" % os.getuid()
    launchctl("bootout", "%s/%s" % (domain, LABEL))
    result = launchctl("bootstrap", domain, str(PLIST))
    if result.returncode:
        sys.exit("launchctl bootstrap failed: %s" % result.stderr.strip())
    print("Installed launchd agent %s" % LABEL)
    if len(accounts) < 2:
        print("Only one account found so far. Sign in to another account in Claude Desktop;"
              " it is picked up automatically.")
    if claude_running():
        print("Claude is running, so sessions flow from the signed-in account to the others now."
              " Sessions that only exist in other accounts are merged the next time Claude quits.")
    time.sleep(2)
    cmd_status(args)


def cmd_uninstall(args):
    launchctl("bootout", "gui/%d/%s" % (os.getuid(), LABEL))
    if PLIST.exists():
        PLIST.unlink()
    shutil.rmtree(str(INSTALL_DIR), ignore_errors=True)
    print("Removed the launchd agent. Session folders and %s are left as they are." % LOG)


def cmd_status(args):
    loaded = launchctl("print", "gui/%d/%s" % (os.getuid(), LABEL)).returncode == 0
    running = claude_running()
    active = signed_in_account(args.config)
    print("claude-desktop-session-sync %s" % __version__)
    print("launchd agent: %s" % ("loaded" if loaded else "not installed"))
    print("Claude Desktop: %s, signed-in account %s" % (
        "running" if running else "not running", active[:8] if active else "unknown"))
    accounts = discover(args.base)
    contents = {}
    for account, folders in accounts.items():
        for f in folders:
            contents[f] = {p.name: p.read_bytes() for p in f.glob("local_*.json")}
            mark = " (signed in)" if account == active else ""
            print("  %s  %d sessions%s" % (short(f), len(contents[f]), mark))
    if len(contents) > 1:
        names = set().union(*contents.values())
        differ = [n for n in names if len({c.get(n) for c in contents.values()}) > 1]
        print("sessions that differ between folders: %d" % len(differ))
    if running and active is None:
        print("warning: cannot tell which account is signed in, so nothing syncs until Claude quits")
    elif running and active not in accounts:
        print("warning: the signed-in account has no session folder yet; use the Code tab once")
    for link in symlinked_folders(args.base):
        print("warning: %s is a symlink; Claude cannot save into it, and it is not synced" % link)
    if LOG.exists():
        lines = LOG.read_text().splitlines()[-5:]
        if lines:
            print("recent log (%s):\n  %s" % (LOG, "\n  ".join(lines)))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="claude_session_sync.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "uninstall", "status", "sync"):
        p = sub.add_parser(name)
        p.add_argument("--base", type=Path, default=APP_DATA / "claude-code-sessions",
                       help=argparse.SUPPRESS)
        p.add_argument("--config", type=Path, default=APP_DATA / "config.json",
                       help=argparse.SUPPRESS)
        if name == "install":
            p.add_argument("--no-backup", action="store_true",
                           help="skip the backup of the session folders")
        if name == "sync":
            p.add_argument("--dry-run", action="store_true", help="only show what would change")
            p.add_argument("--verbose", action="store_true", help="print changes as they happen")
    args = parser.parse_args(argv)
    {"install": cmd_install, "uninstall": cmd_uninstall,
     "status": cmd_status, "sync": cmd_sync}[args.command](args)


if __name__ == "__main__":
    main()
