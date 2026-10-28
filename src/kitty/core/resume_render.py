"""Offline, script-disabled Chromium rendering of a single audited snapshot.

No browser profile or HTTP server is shared with application automation. All
resource loads are blocked; only audited, inlined local CSS reaches Chromium.
Generated CSS text, counters and custom markers are unsupported because DOM fact
checks cannot establish their claim ownership. Private profiles are removed only
after orderly browser closure or termination of the entire owned process group.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unicodedata
from html.parser import HTMLParser
from contextlib import ExitStack
from pathlib import Path

from pypdf import PdfReader
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed


class _Snapshot(HTMLParser):
    def __init__(self, source: Path, folder: Path):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.folder = folder
        self.parts: list[str] = []
        self.resources: list[dict] = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {'script', 'iframe', 'frame', 'frameset', 'object', 'embed', 'base',
                   'img', 'video', 'audio', 'source', 'svg', 'math', 'form', 'input',
                   'template', 'noscript', 'canvas', 'applet', 'portal', 'fencedframe'}:
            raise ValueError(f'Unsafe or unsupported resume element: {tag}')
        for key, value in attrs:
            if key.startswith('on') or key in {'src', 'srcset', 'srcdoc', 'ping', 'background'}:
                raise ValueError(f'Unsafe resume attribute: {key}')
            if key == 'style':
                audit_css(value or '')
            if key == 'href' and tag != 'link':
                if tag != 'a' or re.match(r'(?is)^\s*(?:https?://|mailto:|tel:|#)', value or '') is None:
                    raise ValueError('Resume links must use https/http, mailto, tel, or a fragment')
        if tag == 'meta' and 'http-equiv' in values:
            raise ValueError('HTTP-equiv metadata is not allowed in resume snapshots')
        if tag == 'link':
            if values.get('rel', '').lower() != 'stylesheet':
                raise ValueError('Only local stylesheet links are supported')
            href = values.get('href', '')
            if not href or re.search(r'[:?#\\]', href) or Path(href).is_absolute():
                raise ValueError('Resume stylesheet must be a relative local CSS file')
            path = (self.source.parent / href).resolve()
            if not path.is_relative_to(self.source.parent.resolve()) or path.suffix.lower() != '.css':
                raise ValueError('Resume stylesheet must stay inside the source directory')
            raw = path.read_bytes()
            css = raw.decode('utf-8')
            audit_css(css)
            name = f'style-{len(self.resources)}.css'
            (self.folder / name).write_bytes(raw)
            self.resources.append({'source_path': str(path), 'snapshot': name,
                                   'hash': hashlib.sha256(raw).hexdigest()})
            media = values.get('media', '')
            self.parts.append(f'<style media="{html.escape(media, quote=True)}">{css}</style>')
        else:
            self.parts.append(self.get_starttag_text())
            if tag == 'style':
                self.in_style = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'style':
            self.in_style = False
        if tag != 'link':
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        if self.in_style:
            audit_css(data)
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f'&{name};')

    def handle_charref(self, name):
        self.parts.append(f'&#{name};')

    def handle_comment(self, data):
        self.parts.append(f'<!--{data}-->')

    def handle_decl(self, decl):
        if decl.lower() != 'doctype html':
            raise ValueError('Only an HTML doctype is supported')
        self.parts.append('<!DOCTYPE html>')


def audit_css(css: str) -> None:
    # Reject escapes rather than trying to second-guess CSS tokenization. This
    # blocks escaped url/import/function spellings as well as ordinary ones.
    text = re.sub(r'/\*.*?\*/', '', css, flags=re.S).lower()
    if '\\' in text or '<' in text or re.search(
        r'url\s*\(|@import\b|expression\s*\(|behavior\s*:|-moz-binding|image-set\s*\(', text
    ):
        raise ValueError('Resume CSS must be offline: no URLs, imports, escapes or executable CSS')
    # DOM-only fact comparisons cannot assign generated text to its owning claim.
    # Reject the feature altogether, including counters and custom list markers.
    if re.search(r'(?<![\w-])content\s*:|\bcounters?\s*\(|\bcounter-(?:reset|increment|set|style)\b', text):
        raise ValueError('Resume CSS generated content and counters cannot be fact-validated')
    for marker in re.finditer(r'\blist-style(?:-type)?\s*:\s*([^;}]+)', text):
        if any(word not in {'none', 'disc', 'circle', 'square', 'decimal',
                            'decimal-leading-zero', 'lower-roman', 'upper-roman',
                            'lower-alpha', 'upper-alpha', 'lower-latin', 'upper-latin',
                            'inside', 'outside', '!important'}
               for word in marker[1].split()):
            raise ValueError('Resume CSS custom list markers cannot be fact-validated')


def freeze_html(candidate: str, source: Path, folder: Path) -> tuple[str, list[dict]]:
    parser = _Snapshot(source, folder)
    parser.feed(candidate)
    parser.close()
    return ''.join(parser.parts), parser.resources


class _CDP:
    def __init__(self, socket):
        self.socket = socket
        self.serial = 0

    def call(self, method, **params):
        self.serial += 1
        self.socket.send(json.dumps({'id': self.serial, 'method': method, 'params': params}))
        while True:
            result = json.loads(self.socket.recv(timeout=30))
            if result.get('id') != self.serial:
                continue
            if 'error' in result:
                raise RuntimeError(f'Chromium {method}: {result["error"]["message"]}')
            return result.get('result', {})

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', expression=expression, returnByValue=True,
                           awaitPromise=True)
        if 'exceptionDetails' in result:
            raise RuntimeError('Unable to inspect resume layout')
        return result['result'].get('value')


def _inches(value: str) -> float:
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(in|pt|px|mm|cm)?\s*', value)
    if not match or (not match[2] and float(match[1]) != 0):
        raise ValueError(f'Unsupported @page dimension {value!r}; use physical lengths')
    return float(match[1]) * {'in': 1, 'pt': 1/72, 'px': 1/96, 'mm': 1/25.4,
                             'cm': 1/2.54, None: 1}[match[2]]


def _page_geometry(rules: list[dict]) -> tuple[float, float, list[float]]:
    width, height, margins = 8.5, 11.0, [0.4] * 4
    for rule in rules:
        if rule['selector']:
            raise ValueError('Named or pseudo @page rules are not supported for one-page validation')
        size = rule['size'].lower().split()
        if size:
            if size in [['letter'], ['auto']]:
                width, height = 8.5, 11
            elif size == ['a4']:
                width, height = 210/25.4, 297/25.4
            elif len(size) == 2:
                width, height = map(_inches, size)
            else:
                raise ValueError('Use Letter, A4, or explicit portrait @page size')
        for i, value in enumerate(rule['margins']):
            if value:
                margins[i] = _inches(value)
    if not (7 <= width <= 9 and 10 <= height <= 12) or min(margins) < 0:
        raise ValueError('Resume must use readable Letter/A4-sized portrait paper')
    return width, height, margins


_LAYOUT_JS = r'''(() => {
 const errors = [], chunks = [], boxes = [];
 const rgb = color => {const m=color.match(/[\d.]+/g);return m ? m.map(Number) : [];};
 const luminance = c => c.slice(0,3).map(v=>v/255).map(v=>v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4)
   .reduce((sum,v,i)=>sum+v*[0.2126,0.7152,0.0722][i],0);
 const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
 let node;
 while (node = walker.nextNode()) {
   const text = node.textContent.trim();
   if (!text || ['STYLE', 'SCRIPT'].includes(node.parentElement.tagName)) continue;
   chunks.push(text);
   const range = document.createRange(); range.selectNodeContents(node);
   const rects = [...range.getClientRects()].filter(r => r.width && r.height);
   const style = getComputedStyle(node.parentElement);
   if (!rects.length || style.visibility !== 'visible' || Number(style.opacity) === 0)
     errors.push('Hidden text: ' + text.slice(0, 70));
   if (parseFloat(style.fontSize) < 10.66)
     errors.push('Text smaller than 8pt: ' + text.slice(0, 70));
   if (style.color === 'rgba(0, 0, 0, 0)') errors.push('Transparent text');
   let background = [255,255,255];
   for(let p=node.parentElement;p;p=p.parentElement) {
     const s=getComputedStyle(p), c=rgb(s.backgroundColor);
     if(s.backgroundImage !== 'none') errors.push('Background images cannot be contrast-validated');
     if(c.length===3 || (c.length===4 && c[3]===1)) {background=c;break;}
     if(c.length===4 && c[3]>0) errors.push('Translucent background cannot be contrast-validated');
   }
   const foreground=rgb(style.color), a=luminance(foreground), b=luminance(background);
   if(foreground.length<3 || (foreground.length===4 && foreground[3]<0.9) ||
      (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05)<4.5)
     errors.push('Insufficient text contrast: '+text.slice(0,70));
   for (const r of rects) {
     if (r.left < -1 || r.top < -1 || r.right > innerWidth + 1 || r.bottom > innerHeight + 1)
       errors.push('Text outside printable page: ' + text.slice(0, 70));
     for (let p = node.parentElement; p; p = p.parentElement) {
       const s = getComputedStyle(p), b = p.getBoundingClientRect();
       if (s.display === 'none' || s.visibility !== 'visible' || Number(s.opacity) < 0.9)
         errors.push('Hidden/faded ancestor: ' + text.slice(0, 70));
       if (s.transform !== 'none' || s.clipPath !== 'none' || s.clip !== 'auto' ||
           s.filter !== 'none' || (s.zoom !== '1' && s.zoom !== 'normal'))
         errors.push('Transformed, filtered or clipped text cannot be validated');
       if ((/(hidden|clip|scroll|auto)/.test(s.overflowX) && (r.left < b.left-1 || r.right > b.right+1)) ||
           (/(hidden|clip|scroll|auto)/.test(s.overflowY) && (r.top < b.top-1 || r.bottom > b.bottom+1)))
         errors.push('Clipped text: ' + text.slice(0, 70));
     }
     boxes.push({left:r.left,right:r.right,top:r.top,bottom:r.bottom,text});
   }
 }
 for(let i=0;i<boxes.length;i++) for(let j=i+1;j<boxes.length;j++) {
   const a=boxes[i], b=boxes[j];
   const w=Math.min(a.right,b.right)-Math.max(a.left,b.left);
   const h=Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top);
   if(w>2 && h>Math.min(a.bottom-a.top,b.bottom-b.top)*0.35)
     errors.push('Overlapping text: '+a.text.slice(0,40)+' / '+b.text.slice(0,40));
 }
 return {errors:[...new Set(errors)], chunks, text_boxes:boxes.length};
})()'''


def _browser_group_running(process: subprocess.Popen) -> bool:
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    # Linux can retain orphaned zombies (notably under container PID 1). They
    # cannot write profile data and must not be mistaken for running children.
    if Path('/proc/self/stat').exists():
        for entry in Path('/proc').iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / 'stat').read_text().rpartition(') ')[2].split()
            except (FileNotFoundError, ProcessLookupError):
                continue
            if int(fields[2]) == process.pid and fields[0] not in {'Z', 'X'}:
                return True
        return False
    return True


def _wait_browser_group(process: subprocess.Popen, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        process.poll()  # Reap our direct child as soon as it exits.
        if not _browser_group_running(process):
            process.wait()
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _stop_browser(process: subprocess.Popen) -> None:
    # Browser.close gets the first opportunity to flush and stop every child.
    if _wait_browser_group(process, 5):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if _wait_browser_group(process, 5):
            return
    raise RuntimeError('Offline Chromium processes did not stop; retaining their private profile')


def render_pdf(snapshot: Path, pdf: Path) -> dict:
    """Render one offline HTML file, returning blocking validation diagnostics."""
    executable = os.environ.get('KITTY_CHROMIUM') or next(
        (p for name in ('chromium', 'chromium-browser', 'google-chrome')
         if (p := shutil.which(name))), None)
    if not executable:
        raise RuntimeError('Install Chromium (or set KITTY_CHROMIUM) for offline PDF rendering')
    import base64
    profile = tempfile.mkdtemp(prefix='kitty-resume-')
    browser_ws = None
    browser_connections = ExitStack()
    stopped = True
    try:
        snapshot_html, _ = freeze_html(snapshot.read_text(), snapshot, Path(profile))
        args = [executable, '--headless=new', f'--user-data-dir={profile}',
                '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                '--disable-background-networking', '--disable-component-update',
                '--disable-sync', '--no-first-run', '--no-default-browser-check',
                '--disable-extensions', '--disable-breakpad', '--disable-domain-reliability',
                '--host-resolver-rules=MAP * ~NOTFOUND', 'about:blank']
        # Chromium's stderr names the cause (sandbox, missing library) when startup fails.
        log = Path(profile) / 'kitty-chromium-stderr.log'
        with log.open('wb') as stderr:
            process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=stderr,
                                       start_new_session=True)
        stopped = False
        try:
            portfile = Path(profile) / 'DevToolsActivePort'
            # A first launch on a fresh machine can spend many seconds building caches.
            deadline = time.monotonic() + 60
            while not portfile.exists():
                status = process.poll()
                if status is not None or time.monotonic() > deadline:
                    reason = (f'exited with status {status}' if status is not None
                              else 'did not open its debugging port within 60 seconds')
                    detail = log.read_bytes()[-2000:].decode(errors='replace').strip()
                    raise RuntimeError(f'Offline Chromium {reason}; check KITTY_CHROMIUM'
                                       + (f'. Chromium stderr: {detail}' if detail else ''))
                time.sleep(0.05)
            port, browser_path = portfile.read_text().splitlines()[:2]
            browser_ws = browser_connections.enter_context(
                connect(f'ws://127.0.0.1:{port}{browser_path}', max_size=32*1024*1024)
            )
            browser = _CDP(browser_ws)
            target = browser.call('Target.createTarget', url='about:blank')['targetId']
            with connect(f'ws://127.0.0.1:{port}/devtools/page/{target}', max_size=32*1024*1024) as ws:
                cdp = _CDP(ws)
                cdp.call('Page.enable')
                cdp.call('Network.enable')
                cdp.call('Network.setBlockedURLs', urls=['*'])
                cdp.call('Network.setBypassServiceWorker', bypass=True)
                cdp.call('Emulation.setScriptExecutionDisabled', value=True)
                cdp.call('Emulation.setEmulatedMedia', media='print')
                frame = cdp.call('Page.getFrameTree')['frameTree']['frame']['id']
                cdp.call('Page.setDocumentContent', frameId=frame, html=snapshot_html)
                rules = cdp.evaluate('''(() => {const out=[]; function visit(rules) {
                  for(const r of rules) {if(r.type===6) out.push({selector:r.selectorText,
                  size:r.style.getPropertyValue('size'), margins:['top','right','bottom','left'].map(
                  x=>r.style.getPropertyValue('margin-'+x))});
                  else if(r.cssRules && (!r.conditionText || matchMedia(r.conditionText).matches)) visit(r.cssRules);}}
                  for(const s of document.styleSheets) visit(s.cssRules);return out;})()''')
                width, height, margins = _page_geometry(rules)
                top, right, bottom, left = margins
                cdp.call('Emulation.setDeviceMetricsOverride', width=round((width-left-right)*96),
                         height=round((height-top-bottom)*96), deviceScaleFactor=1, mobile=False)
                cdp.evaluate('document.fonts.ready.then(() => true)')
                layout = cdp.evaluate(_LAYOUT_JS)
                result = cdp.call('Page.printToPDF', printBackground=True, displayHeaderFooter=False,
                                  preferCSSPageSize=True, scale=1, paperWidth=width, paperHeight=height,
                                  marginTop=top, marginRight=right, marginBottom=bottom, marginLeft=left)
                pdf.write_bytes(base64.b64decode(result['data']))
        finally:
            try:
                if browser_ws is not None:
                    try:
                        browser.call('Browser.close')
                    except (ConnectionClosed, OSError, TimeoutError):
                        # Closing the browser may close CDP before its reply.
                        # Process/group shutdown below is the actual barrier.
                        pass
                    finally:
                        browser_connections.close()
            finally:
                _stop_browser(process)
                stopped = True
    finally:
        if stopped:
            shutil.rmtree(profile)
    reader = PdfReader(pdf)
    errors = layout['errors']
    if len(reader.pages) != 1:
        errors.append(f'Expected one page, rendered {len(reader.pages)}; shorten content without shrinking text')
    extracted = '\n'.join(page.extract_text() or '' for page in reader.pages)
    def normalized(text):
        return ''.join(c for c in unicodedata.normalize('NFKC', text) if not c.isspace()).casefold()
    haystack = normalized(extracted)
    cursor = 0
    for chunk in layout['chunks']:
        needle = normalized(chunk)
        position = haystack.find(needle, cursor)
        if position < 0:
            errors.append('PDF text is missing or out of source order: ' + chunk[:90])
        else:
            cursor = position + len(needle)
    if not haystack or not layout['chunks']:
        errors.append('PDF must contain extractable resume text')
    return {'valid': not errors, 'errors': errors, 'pages': len(reader.pages),
            'text_boxes': layout['text_boxes'], 'minimum_font_pt': 8,
            'offline': True, 'scale': 1, 'extracted_text': extracted}
