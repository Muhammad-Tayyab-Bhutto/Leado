#!/usr/bin/env python3
"""Storyboard-driven demo video recorder.

    python scripts/demo/recorder.py storyboard.json out.mp4 [--keep] [--until SCENE_ID]

Requirements: google-chrome and ffmpeg (with libass) on PATH, plus
`pip install -r scripts/demo/requirements.txt` and a Piper voice model
(set DEMO_VOICE to the .onnx path).

Drives headless Chrome over CDP at 1920x1080, records frames with real timing,
animates a visible cursor, generates offline neural narration (piper), burns in
captions, and muxes everything with ffmpeg. See SKILL.md for the storyboard format.
"""
import base64, json, os, re, shutil, subprocess, sys, tempfile, threading, time, urllib.request, wave

import websocket

PIPER = os.environ.get("DEMO_PIPER") or shutil.which("piper") or os.path.join(os.path.dirname(sys.executable), "piper")
VOICE = os.environ.get("DEMO_VOICE", "")
W, H = 1920, 1080              # output pixels
CSS_W = int(os.environ.get("DEMO_CSS_WIDTH", "1440"))  # layout width: smaller = larger, more readable UI
SCALE = W / CSS_W; CSS_H = round(H / SCALE)
FPS = int(os.environ.get("DEMO_FPS", "12"))

CURSOR_JS = r"""
(() => { if (window.__demoCursor) return; const mk = () => {
  if (!document.body) return setTimeout(mk, 30);
  const c = document.createElement('div'); c.id = '__demo_cursor';
  c.style.cssText = 'position:fixed;z-index:2147483647;left:0;top:0;width:30px;height:30px;pointer-events:none;transition:transform .55s cubic-bezier(.22,.8,.3,1);';
  c.innerHTML = '<svg width="30" height="30" viewBox="0 0 24 24"><path d="M3 2l7 19 2.6-7.6L20 11z" fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const p = (window.__demoPos || [W0, H0]); c.style.transform = `translate(${p[0]}px,${p[1]}px)`;
  document.body.appendChild(c); window.__demoCursor = c; };
  mk(); })();
"""

OVERLAY_JS = r"""
((html, ms, kind) => { const o = document.createElement('div');
  o.style.cssText = kind === 'card'
    ? 'position:fixed;inset:0;z-index:2147483646;display:flex;flex-direction:column;align-items:center;justify-content:center;background:radial-gradient(circle at 30% 20%,#1e3a8a 0%,#0b1020 55%,#05070f 100%);color:#fff;font-family:Inter,system-ui,sans-serif;text-align:center;opacity:0;transition:opacity .5s'
    : 'position:fixed;left:50%;top:28px;transform:translateX(-50%);z-index:2147483646;background:#0f172aee;color:#fff;border:1px solid #334155;border-radius:12px;padding:14px 22px;font:600 20px Inter,system-ui,sans-serif;box-shadow:0 10px 40px #0008;opacity:0;transition:opacity .3s';
  o.innerHTML = html; document.body.appendChild(o); requestAnimationFrame(() => o.style.opacity = 1);
  if (ms > 0) setTimeout(() => { o.style.opacity = 0; setTimeout(() => o.remove(), 600) }, ms);
  return true; })
"""


class Browser:
    def __init__(self, workdir):
        self.port = int(os.environ.get("DEMO_CDP_PORT", "9444"))
        self.proc = subprocess.Popen(["google-chrome", "--headless=new", f"--remote-debugging-port={self.port}",
                                      f"--window-size={W},{H}", "--no-first-run", "--no-default-browser-check",
                                      "--hide-scrollbars", f"--user-data-dir={workdir}/chrome", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json")); break
            except Exception:
                time.sleep(0.25)
        url = next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(url, max_size=None)
        self.n, self.lock, self.pending, self.events = 0, threading.Lock(), {}, []
        self.reader = threading.Thread(target=self._read, daemon=True); self.reader.start()
        self.pos = [CSS_W // 2, CSS_H // 2]
        self.cmd("Page.enable"); self.cmd("Runtime.enable")
        self.cmd("Emulation.setDeviceMetricsOverride", width=CSS_W, height=CSS_H, deviceScaleFactor=SCALE, mobile=False)
        self.cmd("Page.addScriptToEvaluateOnNewDocument", source=CURSOR_JS.replace("W0", "window.innerWidth/2").replace("H0", "window.innerHeight/2"))

    def _read(self):
        while True:
            try: m = json.loads(self.ws.recv())
            except Exception: return
            if "id" in m:
                ev = self.pending.pop(m["id"], None)
                if ev: ev[1].update(m); ev[0].set()
            else:
                self.events.append(m)
                if m.get("method") == "Page.javascriptDialogOpening":
                    msg = m["params"].get("message", "")
                    threading.Thread(target=self._accept_dialog, args=(msg,), daemon=True).start()

    def _accept_dialog(self, msg):
        self.cmd("Page.handleJavaScriptDialog", accept=True)
        time.sleep(0.15)
        self.overlay(f"✅ Human confirmed: {esc(msg)}", 2600)

    def cmd(self, method, timeout=60, **params):
        with self.lock:
            self.n += 1; i = self.n
            ev = (threading.Event(), {}); self.pending[i] = ev
            self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        ev[0].wait(timeout)
        return ev[1]

    def js(self, expr, timeout=60):
        r = self.cmd("Runtime.evaluate", timeout=timeout, expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("result", {}).get("value")

    def overlay(self, html, ms=2500, kind="toast"):
        return self.js(f"({OVERLAY_JS})({json.dumps(html)}, {int(ms)}, {json.dumps(kind)})")

    def screenshot(self):
        r = self.cmd("Page.captureScreenshot", format="jpeg", quality=82, timeout=20)
        return base64.b64decode(r["result"]["data"]) if "result" in r else None

    # -- element helpers -------------------------------------------------
    def find(self, sel=None, text=None, timeout=15):
        """Returns centre coordinates of the first visible match (CSS selector or visible text)."""
        q = json.dumps(sel); t = json.dumps(text)
        expr = f"""(() => {{ const vis = e => {{ const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2 && getComputedStyle(e).visibility !== 'hidden'; }};
          let els = {q} ? [...document.querySelectorAll({q})] : [...document.querySelectorAll('button,a,[role=button],input[type=submit],label,span,div,li,h1,h2,h3,b,td')];
          if ({t}) els = els.filter(e => (e.innerText || e.value || '').trim().toLowerCase().includes({t}.toLowerCase()));
          els = els.filter(vis);
          const act = e => /^(BUTTON|A|INPUT|LABEL)$/.test(e.tagName) || e.getAttribute('role') === 'button' || e.hasAttribute('data-act') ? 0 : 1;
          const depth = e => {{ let n = 0; while (e = e.parentElement) n++; return n; }};
          if ({t} && !{q}) els.sort((a, b) => ((a.innerText || '').trim().length - (b.innerText || '').trim().length) || (act(a) - act(b)) || (depth(b) - depth(a)));
          const e = els[0]; if (!e) return null; e.scrollIntoView({{block: 'center', behavior: 'instant'}}); window.__demoTarget = e;
          const r = e.getBoundingClientRect(); return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; }})()"""
        end = time.time() + timeout
        while time.time() < end:
            p = self.js(expr)
            if p: return p
            time.sleep(0.3)
        raise RuntimeError(f"element not found: sel={sel} text={text}")

    def move(self, x, y, dur=0.6):
        self.js(f"(() => {{ window.__demoPos=[{x},{y}]; const c=document.getElementById('__demo_cursor'); if(c) c.style.transform='translate({x}px,{y}px)'; }})()")
        self.pos = [x, y]; time.sleep(dur)

    def click(self, sel=None, text=None, trusted=False):
        x, y = self.find(sel, text)
        self.move(x, y, 0.35)
        x, y = self.find(sel, text)  # re-measure: layouts shift (scrolling chat boxes, re-renders)
        self.move(x, y, 0.25)
        if trusted:  # real input events (needed on third-party pages such as PayPal checkout)
            self.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y); time.sleep(0.08)
            for t in ("mousePressed", "mouseReleased"):
                self.cmd("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
        else:      # click whatever is actually under the cursor
            ok = self.js(f"(() => {{ const c=document.getElementById('__demo_cursor'); if(c) c.style.display='none';"
                         f" let e=window.__demoTarget; if(!e || !e.isConnected) e=document.elementFromPoint({x},{y});"
                         f" if(c) c.style.display=''; if(!e) return false; e.click(); return true; }})()")
            if not ok: raise RuntimeError(f"nothing clickable at {x},{y} for sel={sel} text={text}")
        time.sleep(0.25)

    def type(self, sel, text, delay=0.045, trusted=False):
        self.click(sel, trusted=trusted)
        self.js(f"(() => {{ const e=document.querySelector({json.dumps(sel)}); if (e) {{ e.focus(); e.value=''; }} }})()")
        for ch in text:
            self.cmd("Input.insertText", text=ch); time.sleep(delay)

    def enter(self):
        for t in ("keyDown", "keyUp"):
            self.cmd("Input.dispatchKeyEvent", type=t, key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13,
                     **({"text": "\r"} if t == "keyDown" else {}))

    def goto(self, url, settle=1.5):
        self.cmd("Page.navigate", url=url); time.sleep(settle)

    def wait_url(self, pattern, timeout=90):
        end = time.time() + timeout
        while time.time() < end:
            u = self.js("location.href") or ""
            if re.search(pattern, u): return u
            time.sleep(0.4)
        raise RuntimeError(f"timed out waiting for url ~ {pattern}")

    def close(self):
        try: self.proc.terminate()
        except Exception: pass


def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Recorder(threading.Thread):
    def __init__(self, b, d):
        super().__init__(daemon=True); self.b, self.d, self.frames, self.stop_ev, self.t0 = b, d, [], threading.Event(), None
        self.fps = FPS  # can be lowered per scene (e.g. heavy third-party pages during a sped-up span)

    def run(self):
        self.t0 = time.time(); i = 0
        while not self.stop_ev.is_set():
            t = time.time()
            try: img = self.b.screenshot()
            except Exception: img = None
            if img:
                p = os.path.join(self.d, f"f{i:06d}.jpg"); open(p, "wb").write(img)
                self.frames.append((p, t - self.t0)); i += 1
            time.sleep(max(0, 1 / self.fps - (time.time() - t)))


def tts(text, path):
    if not VOICE or not os.path.exists(VOICE):
        raise SystemExit("Set DEMO_VOICE to a Piper voice model (.onnx), e.g. en_US-ryan-high from "
                         "https://huggingface.co/rhasspy/piper-voices")
    subprocess.run([PIPER, "-m", VOICE, "-f", path, "--sentence-silence", "0.25"], input=text.encode(), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with wave.open(path) as w: return w.getnframes() / w.getframerate()


def run_actions(b, actions, ctx):
    for a in actions:
        k = a["do"]
        if k == "goto": b.goto(a["url"].format(**ctx), a.get("settle", 1.5))
        elif k == "click": b.click(a.get("sel"), a.get("text"))
        elif k == "type": b.type(a["sel"], a["text"].format(**ctx), a.get("delay", 0.045))
        elif k == "move": x, y = b.find(a.get("sel"), a.get("text")); b.move(x, y)
        elif k == "wait": time.sleep(a["s"])
        elif k == "wait_for": b.find(a.get("sel"), a.get("text"), a.get("timeout", 30))
        elif k == "wait_url": ctx["last_url"] = b.wait_url(a["pattern"], a.get("timeout", 90))
        elif k == "eval":
            v = b.js(a["js"].format(**ctx)) if a.get("format") else b.js(a["js"])
            if a.get("save"): ctx[a["save"]] = v
        elif k == "scroll": b.js(f"window.scrollBy({{top:{a.get('y', 400)},behavior:'smooth'}})"); time.sleep(a.get("s", 0.9))
        elif k == "scroll_to": b.find(a.get("sel"), a.get("text")); time.sleep(0.6)
        elif k == "card": b.overlay(f"<div style='font-size:84px;font-weight:800;letter-spacing:-2px'>{a['title']}</div>"
                                    f"<div style='font-size:32px;margin-top:20px;color:#93c5fd;max-width:1150px;line-height:1.35'>{a.get('subtitle', '')}</div>",
                                    int(a.get("ms", 0)), "card")
        elif k == "toast": b.overlay(a["html"], int(a.get("ms", 2500)))
        elif k == "uncard": b.js("document.querySelectorAll('div[style*=\"inset: 0\"]').forEach(e=>{e.style.opacity=0;setTimeout(()=>e.remove(),500)})"); time.sleep(0.5)
        elif k == "paypal_approve": paypal_approve(b, a, ctx)
        else: raise ValueError(f"unknown action {k}")


def paypal_approve(b, a, ctx):
    """Log in to a PayPal sandbox approval page as the buyer and approve (checkout or subscription)."""
    email, pw = os.environ["DEMO_PAYPAL_BUYER_EMAIL"], os.environ["DEMO_PAYPAL_BUYER_PASSWORD"]
    if "business.example.com" in email:
        raise RuntimeError("DEMO_PAYPAL_BUYER_EMAIL is the sandbox *Business* (merchant) account; use the *Personal* buyer account")
    b.wait_url(r"paypal\.com", 60); time.sleep(3)
    label = lambda: b.overlay("⏩ Sped up · real PayPal sandbox login &amp; approval", 0)
    label()
    def try_click(cands, timeout=6):
        for sel, text in cands:
            try: b.find(sel, text, timeout=timeout); b.click(sel, text, trusted=True); return True
            except RuntimeError: continue
        return False
    try:
        b.find("#email", timeout=25)
        b.type("#email", email, 0.03, trusted=True); time.sleep(0.4)
        b.enter(); time.sleep(3)
        b.find("#password", timeout=20)
        b.type("#password", pw, 0.03, trusted=True); time.sleep(0.4)
        b.enter()
    except RuntimeError:
        pass  # already logged in from an earlier scene
    time.sleep(4)
    pat = a.get("return_pattern", r"/return")
    buttons = [("#payment-submit-btn", None), ("[data-testid=submit-button-initial]", None), ("#consentButton", None),
               (None, "Agree & Subscribe"), (None, "Agree & Continue"), (None, "Complete Purchase"), (None, "Pay Now"),
               (None, "Subscribe"), (None, "Continue")]
    end = time.time() + a.get("timeout", 240)
    while time.time() < end:  # multi-step flows: payment method -> review/consent -> return
        if re.search(pat, b.js("location.href") or ""): break
        before = b.js("location.href + '|' + document.body.innerText.length")
        if b.js("/choose a way to pay/i.test(document.body.innerText) && document.querySelectorAll('input[type=radio]').length > 0"):
            try:  # pick the first funding source (sandbox bank) explicitly; card funding is flaky for subscriptions
                b.click("input[type=radio]", trusted=True); time.sleep(0.8)
            except RuntimeError:
                try: b.click(None, "Checking", trusted=True); time.sleep(0.8)  # styled radios: click the bank's label
                except RuntimeError: pass
        if try_click(buttons, 3):
            for _ in range(20):  # wait for navigation or a re-render before deciding on the next click
                time.sleep(0.5)
                cur = b.js("location.href + '|' + document.body.innerText.length")
                if cur != before or re.search(pat, (cur or "")): break
            if not re.search(pat, b.js("location.href") or ""): label()
        else:
            time.sleep(1)
    ctx["last_url"] = b.wait_url(pat, 30)
    time.sleep(a.get("settle", 2.5))


def srt_time(t):
    ms = int(round(t * 1000)); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def chunks(text, n=9):
    words = text.split(); return [" ".join(words[i:i + n]) for i in range(0, len(words), n)]


def main():
    if len(sys.argv) < 3: raise SystemExit(__doc__)
    sb = json.load(open(sys.argv[1])); out = os.path.abspath(sys.argv[2]); keep = "--keep" in sys.argv
    work = tempfile.mkdtemp(prefix="demo_"); fdir = os.path.join(work, "frames"); os.makedirs(fdir)
    ctx = dict(sb.get("vars", {})); ctx.update({k: v for k, v in os.environ.items() if k.startswith("DEMO_")})
    print(f"work dir: {work}")
    # 1. narration first, so each scene knows its length
    scenes = sb["scenes"]
    if "--until" in sys.argv:  # dry-run a prefix of the storyboard
        stop = sys.argv[sys.argv.index("--until") + 1]
        scenes = scenes[:[s.get("id") for s in scenes].index(stop) + 1]
    for i, s in enumerate(scenes):
        s["_wav"] = os.path.join(work, f"s{i:02d}.wav") if s.get("say") else None
        s["_dur"] = tts(s["say"], s["_wav"]) if s.get("say") else 0
        print(f"  narration {i:02d}: {s['_dur']:.1f}s  {s.get('id', '')}")
    b = Browser(work)
    try:
        run_actions(b, sb.get("setup", []), ctx)          # unrecorded setup (login, seed, etc.)
        rec = Recorder(b, fdir); rec.start(); time.sleep(0.3)
        for i, s in enumerate(scenes):
            s["_start"] = time.time() - rec.t0
            rec.fps = float(s.get("fps", FPS))
            run_actions(b, s.get("actions", []), ctx)
            spoken_end = s["_start"] + s["_dur"] + s.get("pad", 0.5)
            while time.time() - rec.t0 < max(spoken_end, s["_start"] + s.get("min", 0)): time.sleep(0.05)
            s["_end"] = time.time() - rec.t0
            print(f"  scene {i:02d} {s.get('id', '')}: {s['_start']:.1f}-{s['_end']:.1f}s")
        time.sleep(0.6); rec.stop_ev.set(); rec.join()
    finally:
        b.close()
    raw_total = rec.frames[-1][1] + 1 / FPS
    # sped-up spans: the part of a "squeeze" scene after its narration ends plays at `squeeze`x
    spans = [(s["_start"] + s["_dur"] + 0.4, s["_end"], float(s["squeeze"])) for s in scenes
             if s.get("squeeze") and s["_end"] > s["_start"] + s["_dur"] + 0.4]
    def tmap(t):
        return t - sum(max(0.0, min(t, b1) - a1) * (1 - 1 / k) for a1, b1, k in spans)
    total = tmap(raw_total)
    print(f"recorded {len(rec.frames)} frames, {raw_total:.1f}s raw -> {total:.1f}s after speed-ups")
    # 2. video track with true per-frame durations
    lst = os.path.join(work, "frames.txt")
    with open(lst, "w") as f:
        for j, (p, t) in enumerate(rec.frames):
            nxt = rec.frames[j + 1][1] if j + 1 < len(rec.frames) else t + 1 / FPS
            d = tmap(nxt) - tmap(t)
            if d < 0.004: continue  # dropped inside a sped-up span
            f.write(f"file '{p}'\nduration {d:.4f}\n")
        f.write(f"file '{rec.frames[-1][0]}'\n")
    # 3. captions
    srt = os.path.join(work, "captions.srt"); k = 1
    for s in scenes: s["_start"] = tmap(s["_start"])  # narration/captions follow the mapped timeline
    with open(srt, "w") as f:
        for s in scenes:
            if not s.get("say") or s.get("no_caption"): continue
            parts = chunks(s["say"]); per = s["_dur"] / max(1, len(parts))
            for j, part in enumerate(parts):
                a0 = s["_start"] + j * per; f.write(f"{k}\n{srt_time(a0)} --> {srt_time(a0 + per)}\n{part}\n\n"); k += 1
    # 4. audio: each scene's narration placed at its start time
    inputs, filt = [], []
    for s in [s for s in scenes if s["_wav"]]:
        inputs += ["-i", s["_wav"]]
        filt.append(f"[{len(filt) + 1}:a]adelay={int(s['_start'] * 1000)}|{int(s['_start'] * 1000)}[a{len(filt)}]")
    mix = "".join(f"[a{i}]" for i in range(len(filt))) + f"amix=inputs={len(filt)}:normalize=0,apad,atrim=0:{total:.2f}[aout]"
    style = "FontName=DejaVu Sans,FontSize=12,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H30000000,BorderStyle=3,BackColour=&H30000000,Outline=6,Shadow=0,MarginV=28"
    vf = "fps=30,format=yuv420p" + ("" if sb.get("captions") is False else f",subtitles={srt}:force_style='{style}'")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, *inputs,
           "-filter_complex", ";".join(filt + [mix]), "-map", "0:v", "-map", "[aout]", "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    shutil.copy(srt, os.path.splitext(out)[0] + ".srt")
    print(f"\nwrote {out} ({total:.1f}s)")
    if total > sb.get("max_seconds", 180): print(f"WARNING: video is {total:.0f}s, over the {sb.get('max_seconds', 180)}s limit")
    if not keep: shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
