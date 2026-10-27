---
name: zcode-ops
description: "批量读多个文件、项目结构速览、有界搜索、跨文件字面量替换——一次 Bash 调用替代多次 Read/Grep 往返，省 token 省时间（dsh-ops 思想的 ZCode 适配）。Use whenever: reading 3+ files at once, first look at a project's structure, searching where results may be large, bulk literal replace/rename across files, or the user mentions 省 token / 提速 / 批量读 / 批量替换 / 项目结构 / 项目概览。NOT for: single-file reads (use Read), fine-grained edits (use Edit), PDF/DOCX (built-in readers)。"
metadata: { "os": ["win32"], "requires": { "anyBins": ["python"] } }
---

# zcode-ops — 有界批量文件操作

dsh-ops（github.com/T-Auto/dsh-ops）验证过的省 token 思想，搬到 ZCode：**一次调用、有界输出、精确预算**。脚本零依赖（Python 3.8+ 标准库），stdout 恒为 UTF-8。

脚本：本技能目录下的 `scripts/ops.py`（安装后为 `~/.agents/skills/zcode-ops/scripts/ops.py`；下称 `$OPS`，Git Bash 里 `python "$OPS"` 调用，系统自带的 Python 3.8 即可）。

## 何时用 / 何时不用

| 场景 | 用什么 |
|---|---|
| 读 1 个文件、精确行范围 | 内置 Read |
| **读 3–32 个文件**（对照多个实现、探索代码） | `readfiles`，1 次 Bash 调用 |
| **项目第一眼**：结构、体量、热点目录 | `map` |
| **搜索且结果可能很大** | `search`（先 count/files 摸底，再定向 content） |
| 搜索要上下文行 | Bash `grep -n -C2`（脚本刻意不做，别重复） |
| **跨文件字面量替换/重命名** | `replace`（必须先 `--dry-run`） |
| 单文件精细编辑 | 内置 Edit |
| PDF/DOCX/XLSX | 内置 Read(PDF)/documents 技能 |

## 命令速查

```bash
OPS="$HOME/.agents/skills/zcode-ops/scripts/ops.py"

# 项目速览：顶层条目 + 递归文件数/体积
python "$OPS" map src --depth 1

# 批量读：path@offset:limit（offset/limit 可省，默认 1:200）
python "$OPS" readfiles "src/a.py@1:80" "src/b.py@40:" "README.md"

# 搜索三档：先摸底再取内容
python "$OPS" search "RPC_E_CALL_REJECTED" . --mode count --glob "*.py"
python "$OPS" search "RPC_E_CALL_REJECTED" scripts --mode content -i --limit 30

# 批量替换：先预览命中面，确认后加 --yes 才落盘
python "$OPS" replace "OldClassName" "NewClassName" src --glob "*.py" --dry-run
python "$OPS" replace "OldClassName" "NewClassName" src --glob "*.py" --yes
```

## 输出契约（读结果时按此理解）

- `readfiles`：行号从 1 起，按物理行（CRLF 算一行）；每文件头标 `enc=行数=字节数`；尾部续读提示给出精确 `path@offset:limit`；超预算的文件**不会静默丢失**，结尾列出待重读清单。
- `search`：末尾 summary 行 `scanned/matched/matches/shown/skipped_binary/errors`——向用户汇报覆盖面时引用它，不要凭感觉说"搜了全部"。
- 所有输出默认 48 KiB 预算，超出明确标注（`--budget` 可调）。
- 编码：逐文件 utf-8 → gbk 自动识别并在文件头标注；latin-1 兜底仅限只读且标 `lossy`；**写操作遇到非 utf-8/gbk 文件直接拒绝**（防乱码），BOM 与换行符原样保留。

## 纪律（源自 dsh-ops 的省 token 规则）

1. 未变化的文件不重读全文——改文件靠 Edit 的精确替换，不靠"再读一遍确认"。
2. 搜索先 `count`/`files` 模式确认规模，再对命中的少数文件定向 `content` 或 readfiles。
3. `replace` 不带 `--yes` 拒绝写盘（脚本强制）；先 dry-run 看命中面，命中文件数异常就收窄 `--glob`。
4. 读一批相关文件永远用 readfiles 一次拿走，不要 Read 循环。

## 已知边界（shortcut 注记）

- search 每次全量扫描（<1 万文件毫秒级；更大仓库先用 `--glob` 收窄，需要前缀截断优化时再升级）。
- 不遍历 `.git`/`node_modules`/隐藏目录（`--hidden` 放开，仅 search 支持）。
- 多行 OLD 在 CRLF 文件里需写成含 `\r\n` 的字面量；**建议只用单行 OLD**。
- `map --depth` 未实现多级树（默认就是顶层聚合）；需要深层结构用 search files 模式或 Bash `find`。
