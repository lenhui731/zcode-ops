#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""zcode-ops -- bounded batch file tools for ZCode (ideas adapted from dsh-ops, MIT).

Subcommands:
  map        Bounded project survey: top-level entries with recursive file counts + sizes.
  readfiles  Batch-read up to 32 files: PATH[@OFFSET[:LIMIT]], numbered lines, byte budget.
  search     Bounded search: --mode files|count|content, glob filters, byte budget.
  replace    Literal cross-file replace; --dry-run preview; --yes to write; encoding kept.

Encoding: per-file utf-8 -> gbk (labeled). Reads may fall back to latin-1 (read-only,
labeled lossy). Writes only touch utf-8/gbk files, are atomic, and preserve BOM + line
endings. stdout is always UTF-8.
Python >= 3.8, stdlib only.
"""

import argparse
import codecs
import fnmatch
import os
import re
import sys
import time
from pathlib import Path

OUT_BUDGET_DEFAULT = 48 * 1024
LINE_CHAR_CAP = 2000
CONTENT_LINE_CAP = 400
MAX_READ_FILES = 32
READ_DEFAULT_LIMIT = 200
SEARCH_DEFAULT_LIMIT = 100
REPLACE_MAX_FILES = 100
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".zcode", ".agents", ".vscode",
    ".idea", "venv", ".venv", "target", "dist", "build",
}
ENC_CHAIN = ("utf-8", "gbk")
BOM = codecs.BOM_UTF8


class Budget:
    """Byte-budgeted output buffer; add() returns False once the cap is hit."""

    def __init__(self, cap):
        self.cap = cap
        self.used = 0
        self.parts = []
        self.overflow = False

    def add(self, text):
        if self.overflow:
            return False
        n = len(text.encode("utf-8"))
        if self.used + n > self.cap:
            self.overflow = True
            return False
        self.used += n
        self.parts.append(text)
        return True

    def text(self):
        return "".join(self.parts)


def out_setup():
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = __import__("io").TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace")


def split_lines(text):
    """Physical lines on \n only (CRLF counts as one line; trailing LF adds none)."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [ln[:-1] if ln.endswith("\r") else ln for ln in lines]


def clip(s, cap, suffix="…"):
    return s if len(s) <= cap else s[:cap] + suffix


def decode_bytes(data, write_safe=False):
    """Return (text, enc_label). write_safe rejects latin-1 fallback (lossless roundtrip
    of raw bytes is possible, but a match could span multi-byte chars -> corruption)."""
    if data.startswith(BOM):
        rest = data[len(BOM):]
        if write_safe:
            try:
                return rest.decode("utf-8"), "utf-8-sig"
            except UnicodeDecodeError:
                return None, None
        try:
            return rest.decode("utf-8"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    for enc in ENC_CHAIN:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    if write_safe:
        return None, None
    return data.decode("latin-1"), "latin-1(lossy)"


def match_globs(rel, name, globs):
    if not globs:
        return True
    pos = [g for g in globs if not g.startswith("!")]
    neg = [g[1:] for g in globs if g.startswith("!")]

    def hit(p):
        return fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p)
    if pos and not any(hit(p) for p in pos):
        return False
    return not any(hit(p) for p in neg)


def walk_files(root, hidden):
    rootp = Path(root)
    if rootp.is_file():
        yield rootp, rootp.name
        return
    pruned = 0
    for dirpath, dirnames, filenames in os.walk(rootp):
        if not hidden:
            keep = [d for d in dirnames
                    if d not in SKIP_DIRS and not d.startswith(".")]
            pruned += len(dirnames) - len(keep)
            dirnames[:] = keep
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = p.relative_to(rootp).as_posix()
            yield p, rel


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


# ---------------------------------------------------------------- map

def cmd_map(args):
    t0 = time.perf_counter()
    root = Path(args.root)
    if not root.is_dir():
        print("map: root is not a directory: %s" % root)
        return 2
    top = {}          # name -> [is_dir, files, bytes, child_dirs set]
    order = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        dirnames.sort()
        rel = Path(dirpath).relative_to(root)
        depth = len(rel.parts)
        for name in sorted(filenames):
            try:
                size = (Path(dirpath) / name).stat().st_size
            except OSError:
                size = 0
            if depth == 0:
                key = name
                if key not in top:
                    top[key] = [False, 0, 0, set()]
                    order.append(key)
                top[key][1] += 1
                top[key][2] += size
            else:
                key = rel.parts[0]
                if key not in top:
                    top[key] = [True, 0, 0, set()]
                    order.append(key)
                top[key][1] += 1
                top[key][2] += size
                if depth >= 2:
                    top[key][3].add(rel.parts[1])
    entries = sorted(order, key=lambda k: (not top[k][0], k))
    shown = entries[:args.max_entries]
    for key in shown:
        is_dir, files, size, subdirs = top[key]
        if is_dir:
            subs = ", ".join(sorted(subdirs)[:8])
            more = ", …(+%d)" % (len(subdirs) - 8) if len(subdirs) > 8 else ""
            print("%-40s %6d files  %9s   dirs: %s%s"
                  % (key + "/", files, human(size), subs, more))
        else:
            print("%-40s %15s" % (key, human(size)))
    hidden_n = len(entries) - len(shown)
    if hidden_n > 0:
        print("… (+%d more entries, --max-entries to raise)" % hidden_n)
    total_files = sum(v[1] for v in top.values())
    total_size = sum(v[2] for v in top.values())
    print("[zcode-ops] map: %d files, %s, %d top-level entries, %s"
          % (total_files, human(total_size), len(entries),
             "elapsed=%.2fs" % (time.perf_counter() - t0)))
    return 0


# ------------------------------------------------------------ readfiles

def parse_spec(spec):
    m = re.match(r"^(.*?)@(\d+)(?::(\d+))?$", spec)
    if m:
        path, off, lim = m.group(1), int(m.group(2)), m.group(3)
        return path, off, int(lim) if lim else READ_DEFAULT_LIMIT
    return spec, 1, READ_DEFAULT_LIMIT


def cmd_readfiles(args):
    specs = [parse_spec(s) for s in args.files]
    if len(specs) > MAX_READ_FILES:
        print("readfiles: %d files requested, cap is %d"
              % (len(specs), MAX_READ_FILES))
        return 2
    b = Budget(args.budget)
    pending = []
    for i, (path, off, lim) in enumerate(specs, 1):
        if b.overflow:
            pending.append(path)
            continue
        p = Path(path)
        if not p.exists():
            b.add("===== %s =====\n[error: NOT_FOUND]\n" % path)
            continue
        if p.is_dir():
            b.add("===== %s =====\n[error: IS_DIR — use map/search]\n" % path)
            continue
        try:
            data = p.read_bytes()
        except OSError as e:
            b.add("===== %s =====\n[error: %s]\n" % (path, e))
            continue
        if b"\x00" in data[:8192]:
            b.add("===== %s =====\n[binary, %s — use Read tool or hex view]\n"
                  % (path, human(len(data))))
            continue
        text, enc = decode_bytes(data)
        lines = split_lines(text)
        total = len(lines)
        b.add("===== %s (enc=%s, %d lines, %s) =====\n"
              % (path, enc, total, human(len(data))))
        if off > total:
            b.add("[offset %d beyond EOF (%d lines)]\n" % (off, total))
            continue
        end = min(total, off - 1 + lim)
        truncated = False
        for n in range(off, end + 1):
            line = clip(lines[n - 1], LINE_CHAR_CAP,
                        " …(+%d chars)" % (len(lines[n - 1]) - LINE_CHAR_CAP))
            if not b.add("%d\t%s\n" % (n, line)):
                truncated = True
                break
        if truncated:
            b.add("…[output budget reached mid-file — continue: %s@%d:%d]\n"
                  % (path, n, lim))
            pending.extend(spec[0] for spec in specs[i:])
            break
        if end < total:
            b.add("----- [%d/%d] %s: shown %d-%d of %d lines; "
                  "continue: %s@%d:%d -----\n"
                  % (i, len(specs), path, off, end, total, path, end + 1, lim))
        else:
            b.add("----- [%d/%d] %s: complete (%d lines) -----\n"
                  % (i, len(specs), path, total))
    if pending:
        b.add("[zcode-ops] %d file(s) NOT shown (budget %s): re-call with %s\n"
              % (len(pending), human(args.budget),
                 " ".join("'%s'" % p for p in pending[:5])))
    print(b.text(), end="")
    print("[zcode-ops] readfiles: %d/%d files served, out=%s/%s"
          % (len(specs) - len(pending), len(specs),
             human(b.used), human(args.budget)))
    return 0


# --------------------------------------------------------------- search

def cmd_search(args):
    t0 = time.perf_counter()
    try:
        rx = (re.compile(args.pattern, re.IGNORECASE if args.i else 0)
              if args.regex else
              re.compile(re.escape(args.pattern), re.IGNORECASE if args.i else 0))
    except re.error as e:
        print("search: bad regex: %s" % e)
        return 2
    b = Budget(args.budget)
    scanned = matched = hits = shown = skipped_bin = errors = 0
    rootp = Path(args.root)
    for p, rel in walk_files(args.root, args.hidden):
        if rootp.is_dir() and not match_globs(rel, p.name, args.glob):
            continue
        try:
            data = p.read_bytes()
        except OSError:
            errors += 1
            continue
        scanned += 1
        if b"\x00" in data[:8192]:
            skipped_bin += 1
            continue
        text, _enc = decode_bytes(data)
        lines = split_lines(text)
        file_hits = 0
        for lineno, line in enumerate(lines, 1):
            if rx.search(line):
                file_hits += 1
                hits += 1
                if args.mode == "content" and shown < args.limit and not b.overflow:
                    if b.add("%s:%d: %s\n" % (p.as_posix(), lineno,
                                              clip(line, CONTENT_LINE_CAP))):
                        shown += 1
        if file_hits:
            matched += 1
            if args.mode == "count":
                b.add("%-60s %d\n" % (p.as_posix(), file_hits))
            elif args.mode == "files":
                b.add("%s\n" % p.as_posix())
    if args.mode == "count":
        b.add("%-60s %d\n" % ("TOTAL", hits))
    print(b.text(), end="")
    if b.overflow:
        print("…[output budget reached — narrow --glob/root, raise --budget, "
              "or use --mode count/files first]")
    print("[zcode-ops] search: pattern=%r mode=%s scanned=%d matched=%d "
          "matches=%d shown=%d skipped_binary=%d errors=%d out=%s/%s %s"
          % (args.pattern, args.mode, scanned, matched, hits, shown,
             skipped_bin, errors, human(b.used), human(args.budget),
             "elapsed=%.2fs" % (time.perf_counter() - t0)))
    return 0


# -------------------------------------------------------------- replace

def cmd_replace(args):
    if not args.old:
        print("replace: empty OLD text refused")
        return 2
    if args.old == args.new:
        print("replace: OLD == NEW, nothing to do")
        return 2
    if not args.dry_run and not args.yes:
        print("replace: refusing to write without --yes. "
              "Run with --dry-run first to preview, then add --yes.")
        return 2
    rootp = Path(args.root)
    plan, err_items, capped = [], [], False
    n_seen = 0
    for p, rel in walk_files(args.root, False):
        if rootp.is_dir():
            if not match_globs(rel, p.name, args.glob):
                continue
            n_seen += 1
            if n_seen > args.max_files:
                capped = True
                break
        try:
            data = p.read_bytes()
        except OSError as e:
            err_items.append("%s: read error %s" % (p, e))
            continue
        if b"\x00" in data[:8192]:
            continue  # binary: silently out of scope
        text, enc = decode_bytes(data, write_safe=True)
        if text is None:
            err_items.append("%s: unknown encoding (not utf-8/gbk) — use Edit tool" % p)
            continue
        count = text.count(args.old)
        if count:
            first_line = next(ln for ln in split_lines(text) if args.old in ln)
            plan.append((p, text, enc, count, clip(first_line.strip(), 120)))
    for p, _t, enc, count, preview in plan:
        print("%-58s enc=%-9s hits=%-4d | %s"
              % (str(p), enc, count, preview))
    total = sum(c for _, _, _, c, _ in plan)
    print("[zcode-ops] replace %s: %d file(s), %d hit(s), %d error(s)%s"
          % ("(dry-run)" if args.dry_run else "(write)", len(plan), total,
             len(err_items),
             "; candidate cap %d reached — narrow --glob" % args.max_files
             if capped else ""))
    for e in err_items:
        print("  [error] %s" % e)
    if args.dry_run or not plan:
        return 0
    changed = replaced = 0
    for p, text, enc, _c, _p in plan:
        new_text = text.replace(args.old, args.new)
        try:
            payload = (BOM + new_text.encode("utf-8")
                       if enc == "utf-8-sig" else new_text.encode(enc))
        except UnicodeEncodeError as e:
            print("  [error] %s: NEW text not encodable as %s: %s"
                  % (p, enc, e))
            continue
        tmp = p.with_name(p.name + ".zops-%d-tmp" % os.getpid())
        try:
            tmp.write_bytes(payload)
            os.replace(str(tmp), str(p))
            changed += 1
            replaced += text.count(args.old)
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            print("  [error] %s: write failed %s" % (p, e))
    print("[zcode-ops] replace done: %d file(s) changed, %d replacement(s)"
          % (changed, replaced))
    return 0


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(prog="ops.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--budget", type=int, default=OUT_BUDGET_DEFAULT,
                       help="stdout byte budget (default %d)" % OUT_BUDGET_DEFAULT)

    p = sub.add_parser("map", help="bounded project survey")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--max-entries", type=int, default=100)
    common(p)
    p.set_defaults(func=cmd_map)

    p = sub.add_parser("readfiles", help="batch-read files: PATH[@OFFSET[:LIMIT]]")
    p.add_argument("files", nargs="+", metavar="PATH[@OFF[:LIMIT]]")
    common(p)
    p.set_defaults(func=cmd_readfiles)

    p = sub.add_parser("search", help="bounded search")
    p.add_argument("pattern")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--mode", choices=["content", "count", "files"],
                   default="content")
    p.add_argument("--glob", action="append",
                   help="glob filter, repeatable; '!' prefix excludes")
    p.add_argument("-i", action="store_true", help="ignore case")
    p.add_argument("--regex", action="store_true",
                   help="pattern is a regex (default: literal)")
    p.add_argument("--limit", type=int, default=SEARCH_DEFAULT_LIMIT,
                   help="max content lines shown (default %d)" % SEARCH_DEFAULT_LIMIT)
    p.add_argument("--hidden", action="store_true",
                   help="include dot/dependency directories")
    common(p)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("replace", help="literal cross-file replace")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--glob", action="append",
                   help="glob filter, repeatable; '!' prefix excludes")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.add_argument("--yes", action="store_true",
                   help="actually write (requires a prior --dry-run by discipline)")
    p.add_argument("--max-files", type=int, default=REPLACE_MAX_FILES)
    common(p)
    p.set_defaults(func=cmd_replace)

    args = ap.parse_args()
    out_setup()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
