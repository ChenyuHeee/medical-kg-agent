#!/usr/bin/env python3
"""Inject <meta name="api-base" content="$API_BASE"> into src/web/index.html."""
import os, re, pathlib, sys
api = os.environ.get("API_BASE", "").strip()
if not api:
    print("API_BASE empty; skipping")
    sys.exit(0)
p = pathlib.Path("src/web/index.html")
s = p.read_text(encoding="utf-8")
tag = f'<meta name="api-base" content="{api}">'
# Operate only inside <head>...</head> so JS comments mentioning "api-base" aren't touched.
m = re.search(r"<head[^>]*>", s)
if not m:
    print("ERR: no <head> tag"); sys.exit(1)
head_close = s.find("</head>", m.end())
head_seg = s[m.end():head_close]
head_seg = re.sub(r'\s*<meta\s+name="api-base"[^>]*>', "", head_seg)
new_head = m.group(0) + "\n    " + tag + head_seg
s = s[:m.start()] + new_head + s[head_close:]
p.write_text(s, encoding="utf-8")
print("injected:", tag)
