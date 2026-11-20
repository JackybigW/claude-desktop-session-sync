# claude-desktop-session-sync

切换账号时保留 Claude 桌面版的 **Code** 会话。

macOS 版 Claude 每个账号各有一份会话列表。从工作账号切到个人账号，Code 侧边栏就空了，其实对话都还在磁盘上，而且各账号共用。这个工具在后台让所有账号的会话列表保持一致，切换后看到的是同一批会话，标题、归档状态和父子关系都在。

> 非官方工具，与 Anthropic 无关。它读取并复制 Claude 数据目录里的文件，这些文件的格式可能随版本变化。

## 安装

### 交给 AI agent

把下面这段发给 Claude Code、Codex 或任何能执行命令的 agent：

```text
Install claude-desktop-session-sync on this Mac by following
https://github.com/JackybigW/claude-desktop-session-sync/blob/main/INSTALL.md
```

### 自己运行

```bash
mkdir -p ~/.local/share/claude-desktop-session-sync && curl -fsSL https://raw.githubusercontent.com/JackybigW/claude-desktop-session-sync/main/claude_session_sync.py -o ~/.local/share/claude-desktop-session-sync/claude_session_sync.py && python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py install
```

要求：macOS；Claude 桌面版至少用过一次 Code tab；Python 3.8 以上（Xcode 命令行工具自带的即可），无其他依赖。

`install` 会先把会话目录备份到 `~/claude-code-sessions-backup-<时间>`，再安装 launchd 后台任务并显示状态。不需要其他配置，账号会自动发现，之后新加的账号也一样。

## 使用

照常在 Claude 里切换账号，切换前等当前回复结束。

```bash
python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py status         # 账号、会话数、不一致条数
python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py sync --dry-run  # 预览同步会改什么
python3 ~/.local/share/claude-desktop-session-sync/claude_session_sync.py uninstall      # 停止同步
```

正常的 `status` 是各账号会话数相同，且 `sessions that differ between folders: 0`。Claude 运行时有一两条暂时不一致，几秒内会补齐。

## 原理

| 数据 | 位置 | 是否按账号隔离 |
|-|-|-|
| 对话记录 | `~/.claude/projects/<项目>/<id>.jsonl` | 否，共用 |
| Code 会话列表 | `~/Library/Application Support/Claude/claude-code-sessions/<账号>/<组织>/local_*.json` | 是 |

只需要同步会话列表。launchd 任务在这些目录变化时运行，另外每 30 秒兜底一次：

- **Claude 运行中：**从当前登录的账号（读 Claude `config.json` 里的 `lastKnownAccountUuid`）复制到其他所有账号，不写当前账号的目录，因为它归 App 管。判断不出当前账号就不同步。
- **Claude 未运行：**所有账号双向合并。
- **谁覆盖谁：**依次比较 `lastActivityAt`、`lastTurnEdgeAt`、`lastFocusedAt`，较新的胜出；都相同（改名、归档）时才看文件修改时间。
- **删除：**删除会话时 Claude 会留下 `deleted_<id>` 标记。标记会被同步并删掉对应记录，删掉的会话不会复活。
- **安全：**解析失败或 `sessionId` 和文件名对不上的记录会跳过。先写临时文件再改名替换，权限 0600。

文件按字节原样复制，脚本从不修改记录内容。Claude 加载列表时会逐条校验，不合格的直接丢掉，所以手改的记录可能让会话消失（在 `~/Library/Logs/Claude/main.log` 里搜 `Dropping session`）。

为什么不用软链接把账号目录连起来？Claude 保存时用 `O_NOFOLLOW` 打开账号目录，软链接会报 `ENOTDIR`，新会话就存不下来。

## 限制

- 只支持 macOS。
- 只同步 Code 会话。Cowork 会话、定时任务和账号级设置（侧边栏分组、连接器）不同步。
- 保存后几秒内就切换，最新一次改动可能晚一轮到。对话内容不受影响。
- 切换那一刻仍在运行的任务会怎样，没有测试过。
- Claude 运行时，只存在于未登录账号里的会话，要等你切到那个账号之后、或 Claude 退出时，才会同步到当前账号。

## 排查

- `launchd agent: not installed`：重新运行 `install`。
- 磁盘上有记录但 Claude 里看不到：查 `~/Library/Logs/Claude/main.log` 里的 `Dropping session … (field: …)`。
- Claude 日志里有 `Failed to save session … ENOTDIR`：某个账号目录被换成了软链接。退出 Claude，把软链接换回普通目录，再运行 `sync`。
- 恢复：先退出 Claude，再从备份里单独复制需要的 `local_*.json`。不要用旧备份整体覆盖，会丢掉之后的所有进度。
- 同步日志：`~/Library/Logs/claude-desktop-session-sync.log`。

## 许可

MIT
