# 图标修复复盘与知识沉淀

## 一、问题现象

为 tkinter GUI 应用设计图标后，三处显示不一致：

| 位置 | 预期 | 实际（初始） |
|------|------|-------------|
| exe 文件图标 | 蓝绿循环箭头 | 黄色 Python 默认图标 |
| 窗口左上角 | 蓝绿循环箭头 | 默认羽毛笔/空方块 |
| 任务栏/Alt-Tab | 蓝绿循环箭头 | 默认 Python 图标 |

## 二、根因分析

### 1. PowerShell 字符串替换静默失败（最大坑）

用 PowerShell here-string 做代码替换时，因为换行符差异（CRLF vs LF），`$old` 字符串和文件内容不匹配，`.Replace()` 静默不生效——**没有报错，代码里根本没写进去**。

```
# 错误做法：here-string 里的换行和文件实际换行不一致
$old = "        self.title(APP_TITLE)`n        self.minsize(..."
$c = $c.Replace($old, $new)  # 不匹配，静默失败
```

**教训**：
- 改代码优先用 Edit 工具（基于精确匹配，失败会报错）
- 用 PowerShell 改代码后，必须 Grep 验证目标字符串真的存在
- 不要假设"命令执行了 = 代码改了"

### 2. AppUserModelID 时机错误

任务栏图标在 Windows 上由 AppUserModelID 决定。这个 ID 必须在**创建任何窗口之前**设置，否则 Windows 会把进程归类到"Python 解释器"名下，用 Python 的默认图标。

```python
# 错误：在 __init__ 里、title() 之后才设置
self.title(APP_TITLE)
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(...)  # 太晚

# 正确：模块加载时、main() 调用前就设置
APP_TITLE = "..."
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CodexAutoResume.App")
```

### 3. iconbitmap 调用时机过早

在 `__init__` 开头调用 `self.iconbitmap()`，此时 Tk 窗口还没完全初始化，设置不生效。

**解决**：用 `self.after(10, self._set_app_icon)` 延迟到事件循环启动后。

### 4. 图标设计在小尺寸下不可辨

第一版图标：细弧线 + 中心小圆点。在 16x16（任务栏/窗口标题栏）下就是一个空方块，用户以为没生效。

**教训**：
- 图标设计必须考虑最小尺寸（16x16）
- 线条要粗（至少占画面 10-14%）
- 造型要极简，不要有太多细节
- 对比要强（白底彩色 / 彩底白形）

### 5. PyInstaller --icon 路径问题

相对路径 `--icon=assets\app.ico` 在某些场景下解析不对，用绝对路径更可靠。

```powershell
$icoPath = Resolve-Path "assets\app.ico"
pyinstaller --icon="$icoPath" ...
```

### 6. Windows 图标缓存

即使 exe 嵌入了正确图标，Windows 资源管理器和任务栏可能仍显示旧缓存。

**解决**：
- 删除 `%LOCALAPPDATA%\IconCache.db`
- 运行 `ie4uinit.exe -show` 刷新
- 或重启 explorer

## 三、正确做法清单

### tkinter + PyInstaller 应用图标完整方案

```python
# 1. 模块级：设置 AppUserModelID（必须在窗口创建前）
try:
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MyApp.App")
except Exception:
    pass

# 2. __init__ 里：延迟设置窗口图标
def __init__(self):
    super().__init__()
    self.title("...")
    self.after(10, self._set_app_icon)
    # ... 其他初始化 ...

# 3. _set_app_icon 兼容源码和打包模式
def _set_app_icon(self):
    import sys
    from pathlib import Path
    base = getattr(sys, "_MEIPASS", None)  # PyInstaller 临时目录
    if base is None:
        base = str(Path(__file__).resolve().parent.parent.parent)
    ico = Path(base) / "assets" / "app.ico"
    if ico.exists():
        self.iconbitmap(default=str(ico))
```

```powershell
# 4. 打包：绝对路径图标 + 数据文件
pyinstaller --onefile --windowed `
    --icon="E:\...\assets\app.ico" `
    --add-data "assets;assets" `
    gui.py
```

## 四、可复用经验

1. **改代码后必验证**：Grep 确认目标字符串存在，不要凭命令输出推断
2. **Windows 应用图标三要素**：AppUserModelID（早设）+ iconbitmap（晚设）+ exe 嵌入图标（打包时）
3. **图标设计**：最小尺寸 16x16 下线条粗、造型简、对比强
4. **打包路径**：用绝对路径，`sys._MEIPASS` 兼容 onefile
5. **缓存问题**：改图标后清 IconCache + ie4uinit
