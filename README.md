# Codex Auto Resume

> **致谢**：本项目基于开源仓库 [feifeigong/codex-auto-resume](https://github.com/feifeigong/codex-auto-resume) 进行升级迭代开发，新增了图形界面（GUI）、会话管理增强与界面美化等功能。衷心感谢原作者 [feifeigong](https://github.com/feifeigong) 的出色工作与开源贡献。

Codex Plus 额度用完时，当前对话会停住。这个工具会在额度回来后，**在原来那条对话里自动继续**，不会新开聊天。

Windows 和 macOS 都能用。

---

## 30 秒上手

需要：Python 3.10+，本机已经登录 Codex。

```bash
git clone https://github.com/feifeigong/codex-auto-resume.git
cd codex-auto-resume
```

Windows：

```powershell
python run.py doctor
python run.py install-autostart
```

macOS：

```bash
python3 run.py doctor
python3 run.py install-autostart
```

`doctor` 看到 `app_server OK` 就说明能读到额度和会话。  
`install-autostart` 会装开机自启，并立刻在后台开始干活。

之后额度用完你不用管。额度回来后，打开 Codex 里**原来那条停住的对话**就行。

只想先试一下、不装开机自启：Windows 用 `python run.py watch`，Mac 用 `python3 run.py watch`。这个窗口别关，关掉就停。

---

## GUI 图形界面（Windows）

项目自带一个图形化操作界面，无需额外安装依赖（使用 Python 自带 tkinter）。

```powershell
python gui.py
```

界面包含四个页签：

| 页签 | 功能 |
|---|---|
| 仪表盘 | 实时显示 5 小时额度 / 周额度 / 重置卡数量、守护进程与开机自启状态，一键启动/停止守护、立即扫描、安装/移除开机自启 |
| 会话管理 | 列出所有被追踪的对话（thread_id、状态、续跑次数），可启用/停用单个会话、打开该会话的续跑日志 |
| 配置 | 可视化编辑检查间隔、回看时长、续跑指令等参数，点「保存配置」生效 |
| 日志 | 实时滚动查看 daemon.log 尾部内容 |

注意：GUI 内嵌守护与命令行 `watch` 共用同一把进程锁，两者互斥，不要同时开启。

---

## 使用方法（稍微详细一点）

下面 Windows 用 `python`，Mac 用 `python3`。先进入刚才 clone 下来的目录。

### 常用命令

```text
run.py doctor              检查环境，跑一次就行
run.py status              看当前额度和跟踪中的对话
run.py watch               前台挂着跑，关窗口就停
run.py install-autostart   开机后自动在后台跑（推荐）
run.py uninstall-autostart 关掉后台，并取消开机自启
```

真正续跑的是 `watch`。开机自启也是在后台跑它。`doctor` / `status` 只是给人看的。

### 额度用完之后

什么都不用做。保持 `watch` 开着，或者已经装过开机自启。

额度恢复后，去 Codex App 点进**原来那条对话**。成功时同一条聊天里会出现续跑提示，然后 Codex 接着干。不要去找新开的聊天。

### Codex 目录不在默认位置时

默认会读 `CODEX_HOME`，没有的话 Mac 一般是 `~/.codex`。  
如果你把 Codex 家目录放在别处（比如 Windows 的 `D:\codex`），第一次运行后打开配置文件，把 `codex_home` 改成实际路径。

配置文件在：

- Windows：`%LOCALAPPDATA%\vibcoding\codex-auto-resume\config.json`
- macOS：`~/Library/Application Support/vibcoding/codex-auto-resume/config.json`

### 重置卡（Plus）

- 5 小时额度没了、周额度还在：只等 5 小时，**不会刷卡**
- 周额度也没了，并且账户里还有重置卡：自动用 **1 张**，然后仍在原来的对话里续
- 如果那时额度其实已经回来了（比如别人/工具已经帮你刷过）：再读一次额度，确认后**不扣卡**

不想自动刷卡，把配置里的 `auto_redeem_weekly_reset` 改成 `false`。

### 已经在用这个工具，想更新

```bash
cd codex-auto-resume
git pull
```

然后重新装一次自启，让后台跑上新代码：

```text
run.py uninstall-autostart
run.py install-autostart
```

### 常见问题

**关了 watch 窗口还会自动续吗？**  
不会。要么窗口一直开着，要么执行过 `install-autostart`。不要两个一起开。

**Desktop 正开着那条对话，还能自动续吗？**  
能。续不进去时会改成往原来那条对话里排队发消息。

**会不会新建一条对话，或者续错最近一条？**  
不会。只按原来的 thread 续，不用 `--last`。

**我自己已经在原对话里又发过消息了？**  
它会认为你已经接手，不再自动发。

**很久以前停住的对话会不会被拉起来？**  
默认只看最近 36 小时。

**额度已经回来了，对话却不续？**  
0.2.2 之前，一条对话自动续过一次就再也不会续。现在每一轮额度用尽再恢复，都会再续一次。某条对话不想再自动续，用 `disable <thread_id>`。

**额度还没回来，对话里却反复出现续跑提示？**  
0.2.3 之前，额度接口偶发失败时，会把会话里的旧记录当成已经恢复，于是在限额期间连发。现在只有实时额度接口确认恢复后才发，而且同一轮限额只发一次。

**开机自启会不会一直弹 Terminal 窗口？**  
旧版会。现在已经修掉了：后台不再用 `tasklist` / `codex.cmd` 去扫进程。

**Desktop 和 CLI 都能用吗？**  
能。它们共用同一套会话。续跑发生在原对话里，打开那条聊天就能看到。

---

## 配置（一般不用改）

| 字段 | 含义 | 默认 |
|---|---|---|
| `codex_home` | Codex 数据目录 | `CODEX_HOME`，否则 `~/.codex` |
| `lookback_hours` | 只处理最近多少小时内被打断的对话 | `36` |
| `max_auto_windows` | 同一轮中断最多自动续几次（不再是终身次数） | `1` |
| `poll_seconds` | 检查间隔（秒） | `30` |
| `resume_prompt` | 恢复后发给原对话的提示 | 见配置文件 |
| `auto_redeem_weekly_reset` | 周额度用尽时是否自动用 1 张重置卡 | `true` |
| `reset_credit_cooldown_seconds` | 刷卡失败后的冷却，防止连打 | `600` |

日志：

- Windows：`%LOCALAPPDATA%\vibcoding\codex-auto-resume\daemon.log`
- macOS：上面 Application Support 目录里的 `daemon.log`，以及 `~/Library/Logs/codex-auto-resume*.log`

---

## 这次更新了什么

更完整的条目见 [CHANGELOG.md](CHANGELOG.md)。

**新功能**

- 周额度也用尽时，可以自动用 1 张重置卡，然后在原对话续跑
- 5 小时没了但周额度还在时，只等待，不浪费卡
- 刷卡前会再读一次额度；已经恢复的话不扣卡
- 续跑时不再停下来等人确认
- `status` 可以看到周额度和重置卡数量

**修复**

- Windows 开机自启每隔约 30 秒闪一下 Terminal
- 后台改为无窗口检测进程，并复用同一次 Codex 连接
- 桌面端占着原对话时，改为排队发到原来那条聊天，不再把失败当成已经续过
- 同时只允许一个 `watch`，避免重复续跑
- 额度再回来时，不会因为这条对话已经续过一次而终身停住；同一轮中断仍只续一次
- 额度接口读失败时，不再把旧会话记录当成已经恢复，避免限额期间反复发送续跑提示

---

## 开发者

```bash
python -m unittest discover -s tests -v
```

不需要登录 Codex。周额度刷卡相关测试用的是假客户端，不会扣真实重置卡。
