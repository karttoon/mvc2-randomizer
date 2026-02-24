#!/usr/bin/env python3
"""
MvC2 Skin Curation Gallery — review and delete unwanted skins.

Shows skins one at a time. Thumbs up to keep, thumbs down to mark for deletion.
Left/Right arrows to navigate. "Delete All Marked" button to batch-delete.
Verdicts persist in gallery_verdicts.json so future --gallery-download runs
skip rejected skins and previously reviewed skins show their status on relaunch.

Usage:
    python gallery.py [skins_path]
    python gallery.py ./skins --port 8080
"""
import argparse
import http.server
import json
import os
import sys
import threading
import urllib.parse
import webbrowser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VERDICTS_FILE = os.path.join(SCRIPT_DIR, "gallery_verdicts.json")


def load_verdicts():
    """Load persisted verdicts from gallery_verdicts.json."""
    if os.path.isfile(VERDICTS_FILE):
        with open(VERDICTS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_verdicts(verdicts):
    """Save all verdicts to gallery_verdicts.json."""
    with open(VERDICTS_FILE, "w") as f:
        json.dump(verdicts, f, indent=1, sort_keys=True)


def scan_skins(root_dir):
    """Scan skins folder, return {character: [filenames]} sorted."""
    characters = {}
    for char_name in sorted(os.listdir(root_dir)):
        char_dir = os.path.join(root_dir, char_name)
        if not os.path.isdir(char_dir):
            continue
        pngs = sorted(f for f in os.listdir(char_dir) if f.lower().endswith('.png'))
        if pngs:
            characters[char_name] = pngs
    return characters


def build_html(all_skins, verdicts):
    """Build the single-page gallery HTML."""

    first_unreviewed = 0
    for i, s in enumerate(all_skins):
        if s["key"] not in verdicts:
            first_unreviewed = i
            break

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>MvC2 Skin Curation</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    background: #111; color: #eee; font-family: 'Segoe UI', system-ui, sans-serif;
    display: flex; flex-direction: column; height: 100vh; overflow: hidden;
}}
#topbar {{
    background: #1a1a2e; padding: 8px 16px; display: flex; align-items: center;
    gap: 16px; border-bottom: 2px solid #333; flex-shrink: 0;
}}
#topbar h1 {{ font-size: 18px; color: #e94560; }}
#stats {{ font-size: 13px; color: #888; margin-left: auto; }}
#stats .g {{ color: #4eff4e; font-weight: bold; }}
#stats .r {{ color: #ff4e4e; font-weight: bold; }}
#stats .w {{ color: #fff; font-weight: bold; }}

#main {{
    display: flex; flex: 1; overflow: hidden;
}}
#sidebar {{
    width: 220px; background: #1a1a1a; border-right: 1px solid #333;
    overflow-y: auto; flex-shrink: 0; padding: 8px 0;
}}
.char-btn {{
    display: block; width: 100%; padding: 6px 12px; background: none;
    border: none; color: #ccc; text-align: left; font-size: 13px;
    cursor: pointer; border-left: 3px solid transparent;
}}
.char-btn:hover {{ background: #252525; }}
.char-btn.active {{ background: #1a1a2e; color: #e94560; border-left-color: #e94560; }}
.char-btn.done {{ color: #666; }}
.char-info {{ float: right; font-size: 11px; }}
.char-info .g {{ color: #4eff4e; }}
.char-info .r {{ color: #ff4e4e; }}
.char-info .rem {{ color: #888; }}

#viewer {{
    flex: 1; display: flex; flex-direction: column; align-items: center;
    justify-content: center; padding: 20px; position: relative;
}}
#skin-img {{
    max-width: 95%; max-height: calc(100vh - 200px);
    image-rendering: pixelated; background: #222;
    border: 3px solid #333; border-radius: 4px;
}}
#skin-img.kept {{ border-color: #4eff4e; }}
#skin-img.deleted {{ border-color: #ff4e4e; }}
#info {{
    margin-top: 12px; text-align: center;
}}
#filename {{ font-size: 16px; font-weight: bold; color: #ddd; }}
#filename.deleted {{ text-decoration: line-through; color: #ff4e4e; }}
#position {{ color: #888; margin-top: 4px; font-size: 13px; }}
#verdict-label {{ margin-top: 4px; font-size: 14px; font-weight: bold; }}
#verdict-label.kept {{ color: #4eff4e; }}
#verdict-label.deleted {{ color: #ff4e4e; }}
#verdict-label.pending {{ color: #555; }}

#controls {{
    background: #1a1a2e; padding: 12px 16px; display: flex;
    justify-content: center; align-items: center; gap: 16px;
    border-top: 2px solid #333; flex-shrink: 0;
}}
.ctrl-btn {{
    padding: 10px 28px; border: 2px solid #444; border-radius: 6px;
    background: #222; color: #ccc; font-size: 15px; cursor: pointer;
    font-weight: bold;
}}
.ctrl-btn:hover {{ background: #333; }}
.ctrl-btn.keep {{ border-color: #4eff4e; color: #4eff4e; }}
.ctrl-btn.keep:hover {{ background: #1a3a1a; }}
.ctrl-btn.del {{ border-color: #ff4e4e; color: #ff4e4e; }}
.ctrl-btn.del:hover {{ background: #3a1a1a; }}
.ctrl-btn.nav {{ border-color: #555; color: #aaa; }}
.ctrl-btn.nav:hover {{ background: #2a2a2a; }}
.ctrl-btn.danger {{
    border-color: #ff2222; color: #ff2222; margin-left: 40px;
}}
.ctrl-btn.danger:hover {{ background: #3a0a0a; }}
.ctrl-btn.danger:disabled {{ opacity: 0.3; cursor: not-allowed; }}
kbd {{
    background: #333; padding: 2px 6px; border-radius: 3px; font-size: 11px;
    border: 1px solid #555; margin-left: 6px;
}}
</style>
</head>
<body>

<div id="topbar">
    <h1>MvC2 Skin Curation</h1>
    <div id="stats">
        Kept: <span class="g" id="kept-count">0</span> |
        Marked: <span class="r" id="del-count">0</span> |
        Remaining: <span class="w" id="remaining-count">0</span> |
        Total: <span id="total-count">0</span>
    </div>
</div>

<div id="main">
    <div id="sidebar"></div>
    <div id="viewer">
        <img id="skin-img" src="" alt="skin">
        <div id="info">
            <div id="filename"></div>
            <div id="position"></div>
            <div id="verdict-label" class="pending"></div>
        </div>
    </div>
</div>

<div id="controls">
    <button class="ctrl-btn del" onclick="doDelete()">Delete <kbd>N</kbd></button>
    <button class="ctrl-btn nav" onclick="goPrev()">Prev <kbd>&#8592;</kbd></button>
    <button class="ctrl-btn nav" onclick="goNext()">Next <kbd>&#8594;</kbd></button>
    <button class="ctrl-btn keep" onclick="doKeep()">Keep <kbd>Y</kbd></button>
    <button class="ctrl-btn danger" id="nuke-btn" onclick="doNuke()" disabled>
        Delete All Marked (<span id="nuke-count">0</span>)
    </button>
</div>

<script>
const ALL = {json.dumps(all_skins)};
const TOTAL = ALL.length;
const verdicts = {json.dumps(verdicts)};
let idx = {first_unreviewed};

function getDelCount() {{
    return Object.values(verdicts).filter(v => v === 'delete').length;
}}

function getCharStats() {{
    const stats = {{}};
    ALL.forEach(d => {{
        if (!stats[d.char]) stats[d.char] = {{ total: 0, kept: 0, deleted: 0, remaining: 0 }};
        stats[d.char].total++;
        const v = verdicts[d.key];
        if (v === 'keep') stats[d.char].kept++;
        else if (v === 'delete') stats[d.char].deleted++;
        else stats[d.char].remaining++;
    }});
    return stats;
}}

function updateStats() {{
    const vals = Object.values(verdicts);
    const kept = vals.filter(v => v === 'keep').length;
    const del = vals.filter(v => v === 'delete').length;
    document.getElementById('kept-count').textContent = kept;
    document.getElementById('del-count').textContent = del;
    document.getElementById('remaining-count').textContent = TOTAL - kept - del;
    document.getElementById('total-count').textContent = TOTAL;
    document.getElementById('nuke-count').textContent = del;
    document.getElementById('nuke-btn').disabled = del === 0;
}}

function buildSidebar() {{
    const sb = document.getElementById('sidebar');
    sb.innerHTML = '';
    const stats = getCharStats();
    const curChar = idx < ALL.length ? ALL[idx].char : '';
    Object.keys(stats).sort().forEach(c => {{
        const s = stats[c];
        const btn = document.createElement('button');
        const isDone = s.remaining === 0;
        btn.className = 'char-btn' + (c === curChar ? ' active' : '') + (isDone ? ' done' : '');
        btn.onclick = () => jumpToChar(c);
        let info = '';
        if (s.kept > 0) info += '<span class="g">' + s.kept + '</span> ';
        if (s.deleted > 0) info += '<span class="r">' + s.deleted + '</span> ';
        if (s.remaining > 0) info += '<span class="rem">' + s.remaining + '</span>';
        else info += 'done';
        btn.innerHTML = c.replace(/_/g, ' ') + '<span class="char-info">' + info + '</span>';
        sb.appendChild(btn);
    }});
}}

function jumpToChar(charName) {{
    let firstUnreviewed = -1, firstInChar = -1;
    for (let i = 0; i < ALL.length; i++) {{
        if (ALL[i].char === charName) {{
            if (firstInChar === -1) firstInChar = i;
            if (firstUnreviewed === -1 && !verdicts[ALL[i].key]) firstUnreviewed = i;
        }}
    }}
    idx = firstUnreviewed >= 0 ? firstUnreviewed : firstInChar;
    showCurrent();
}}

function showCurrent() {{
    if (idx < 0) idx = 0;
    if (idx >= ALL.length) idx = ALL.length - 1;
    const d = ALL[idx];
    const img = document.getElementById('skin-img');
    img.src = d.path;
    const v = verdicts[d.key];
    img.className = v === 'keep' ? 'kept' : v === 'delete' ? 'deleted' : '';

    const fn = document.getElementById('filename');
    fn.textContent = d.file;
    fn.className = v === 'delete' ? 'deleted' : '';

    document.getElementById('position').textContent =
        (idx + 1) + ' / ' + TOTAL + '  |  ' + d.char.replace(/_/g, ' ');

    const vl = document.getElementById('verdict-label');
    if (v === 'keep') {{ vl.textContent = 'KEPT'; vl.className = 'kept'; }}
    else if (v === 'delete') {{ vl.textContent = 'MARKED FOR DELETION'; vl.className = 'deleted'; }}
    else {{ vl.textContent = 'unreviewed'; vl.className = 'pending'; }}

    buildSidebar();
    updateStats();
}}

function setVerdict(verdict) {{
    if (idx >= ALL.length) return;
    const d = ALL[idx];
    verdicts[d.key] = verdict;
    fetch('/verdict', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ key: d.key, verdict: verdict }})
    }});
    showCurrent();
    advanceToNext();
}}

function advanceToNext() {{
    for (let i = idx + 1; i < ALL.length; i++) {{
        if (!verdicts[ALL[i].key]) {{ idx = i; showCurrent(); return; }}
    }}
    for (let i = 0; i < idx; i++) {{
        if (!verdicts[ALL[i].key]) {{ idx = i; showCurrent(); return; }}
    }}
    showCurrent();
}}

function doKeep() {{ setVerdict('keep'); }}
function doDelete() {{ setVerdict('delete'); }}
function goNext() {{ if (idx < ALL.length - 1) {{ idx++; showCurrent(); }} }}
function goPrev() {{ if (idx > 0) {{ idx--; showCurrent(); }} }}

function doNuke() {{
    const marked = Object.entries(verdicts)
        .filter(([k, v]) => v === 'delete')
        .map(([k]) => k);
    if (marked.length === 0) return;
    if (!confirm('Delete ' + marked.length + ' marked skins from disk?\\n\\nThis cannot be undone.')) return;

    fetch('/delete-all', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ keys: marked }})
    }}).then(r => r.json()).then(data => {{
        alert('Deleted ' + data.deleted + ' files.\\nSkip list updated.');
        location.reload();
    }});
}}

document.addEventListener('keydown', e => {{
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    switch(e.key) {{
        case 'y': case 'Y': case 'ArrowUp': doKeep(); break;
        case 'n': case 'N': case 'ArrowDown': doDelete(); break;
        case 'ArrowRight': case 'd': case 'D': goNext(); break;
        case 'ArrowLeft': case 'a': case 'A': goPrev(); break;
    }}
}});

showCurrent();
</script>
</body>
</html>"""


class GalleryHandler(http.server.BaseHTTPRequestHandler):
    root_dir = ""
    verdicts = {}

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            characters = scan_skins(self.root_dir)
            all_skins = []
            for char_name, files in characters.items():
                for fname in files:
                    key = f"{char_name}/{fname}"
                    all_skins.append({
                        "char": char_name,
                        "file": fname,
                        "key": key,
                        "path": f"/img/{char_name}/{urllib.parse.quote(fname)}",
                    })
            html = build_html(all_skins, self.verdicts)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
        elif self.path.startswith("/img/"):
            rel = urllib.parse.unquote(self.path[5:])
            filepath = os.path.join(self.root_dir, rel)
            if os.path.isfile(filepath):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                with open(filepath, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        if self.path == "/verdict":
            self.verdicts[body["key"]] = body["verdict"]
            save_verdicts(self.verdicts)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        elif self.path == "/delete-all":
            keys = body.get("keys", [])
            deleted = 0
            for key in keys:
                filepath = os.path.join(self.root_dir, key)
                if os.path.isfile(filepath):
                    os.remove(filepath)
                    deleted += 1
                # Keep verdict as 'delete' so gallery-download won't re-add
            save_verdicts(self.verdicts)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"deleted": deleted}).encode())
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="MvC2 Skin Curation Gallery")
    parser.add_argument("skins", nargs="?", default=None,
                        help="Path to skins folder (default: ./skins next to script)")
    parser.add_argument("--port", type=int, default=8420, help="Server port (default: 8420)")
    args = parser.parse_args()

    root_dir = os.path.abspath(args.skins) if args.skins else os.path.join(SCRIPT_DIR, "skins")
    if not os.path.isdir(root_dir):
        print(f"Error: skins folder not found: {root_dir}")
        sys.exit(1)

    characters = scan_skins(root_dir)
    total = sum(len(v) for v in characters.values())

    print("=" * 60)
    print("MvC2 Skin Curation Gallery")
    print("=" * 60)
    print(f"Skins:  {root_dir}")
    print(f"Total:  {total} skins across {len(characters)} characters")
    print()
    print("Controls:  Y/Up = Keep  |  N/Down = Mark Delete  |  Left/Right = Navigate")
    print()

    GalleryHandler.root_dir = root_dir

    # Load persisted verdicts; prune stale 'keep' entries for missing files
    # but retain 'delete' verdicts so gallery-download won't re-add them.
    saved = load_verdicts()
    pruned = {}
    stale = 0
    for key, verdict in saved.items():
        filepath = os.path.join(root_dir, key)
        if verdict == "delete" or os.path.isfile(filepath):
            pruned[key] = verdict
        else:
            stale += 1
    GalleryHandler.verdicts = pruned
    if pruned:
        kept = sum(1 for v in pruned.values() if v == "keep")
        marked = sum(1 for v in pruned.values() if v == "delete")
        print(f"Loaded {len(pruned)} saved verdicts ({kept} kept, {marked} rejected)")
        if stale:
            save_verdicts(pruned)
            print(f"  Pruned {stale} stale keep entries for missing files")
    print()

    server = http.server.HTTPServer(("127.0.0.1", args.port), GalleryHandler)
    url = f"http://127.0.0.1:{args.port}"

    print(f"Gallery running at {url}")
    print("Press Ctrl+C to stop\n")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
