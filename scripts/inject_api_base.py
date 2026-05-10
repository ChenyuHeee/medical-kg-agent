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
if 'name="api-base"' in s:
    s = re.sub(r'<meta name="api-base"[^>]*>', tag, s)
else:
    s = s.replace("<head>", "<head>\n    " + tag, 1)
p.write_text(s, encoding="utf-8")
print("injected:", tag)
