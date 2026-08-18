#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MORPHWORKS 即梦视频批量工具 v0.1（候选实现）

流程：
  plan      读取主题文件夹生成批量计划（不碰浏览器）
  prepare   按计划把单条配置填入即梦页面（首尾帧/模型/时长/比例/提示词），只填不点生成
  generate  点击「生成」（必须显式调用，默认不自动点）
  poll      轮询生成状态
  download  捕获/点击下载视频
  archive   把视频移回来源主题文件夹 + 重命名 + 更新账本

门禁（用户明确要求 2026-08-05）：
  - 批量提交前先输出计划并确认所有配置当时的积分统计
  - 只有明确授权可以自动点击「生成」时才自动执行，否则默认人工确认

依赖：本机 Kimi WebBridge daemon（127.0.0.1:10086）+ 已登录即梦的 Chrome 标签。
"""

import argparse
import base64
import csv
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import time
import urllib.request

OUT_ROOT = pathlib.Path("/Users/solo/Desktop/AI工作室/VideoHub/Morphworks")
WB = "http://127.0.0.1:10086/command"
SESSION = "jimeng-test"
VIDEO_URL = "https://jimeng.jianying.com/ai-tool/generate/?type=video"
ACCOUNT_BOOK = OUT_ROOT / "_ACCOUNT_BOOK.csv"
DEFAULT_MODEL = "即梦 Seedance 2.0 mini"
DEFAULT_DURATION = 10
DEFAULT_ASPECT = "9:16"
DEFAULT_VARIANT = "signature"


# ---------------------------------------------------------------- WebBridge
def wb(action, args=None, timeout=90):
    body = {"action": action, "session": SESSION}
    if args is not None:
        body["args"] = args
    req = urllib.request.Request(
        WB, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def eval_js(code):
    resp = wb("evaluate", {"code": code})
    if not resp.get("ok"):
        raise RuntimeError("evaluate failed: %s" % resp.get("error"))
    return resp["data"].get("value")


def click_js(js_expr):
    """点击页面元素（通过 evaluate 直接 click）。"""
    return eval_js(js_expr)


# ---------------------------------------------------------------- 内容包解析
def parse_content_pack(md_path):
    """从内容包.md 提取 Core/Upgraded/Signature 的最终视频提示词。"""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    variants = {}
    cur = None
    pending = False
    for ln in lines:
        m = re.match(r"^#+\s*\d+\.?\s*Model\s+\d+\s*[｜|]\s*(Core|Upgraded|Signature)\b", ln)
        if m:
            cur = m.group(1).lower()
            variants.setdefault(cur, "")
            pending = False
            continue
        if ln.strip() == "## 最终视频提示词":
            pending = True
            continue
        if pending and cur:
            if ln.strip() == "" and variants[cur].strip():
                pending = False
                continue
            if ln.strip().startswith("#") and variants[cur].strip():
                pending = False
                continue
            variants[cur] += ln.strip() + " "
    return {k: re.sub(r"\s+", " ", v).strip() for k, v in variants.items() if v.strip()}


def load_meta(folder):
    meta = {}
    p = folder / "meta.yaml"
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ":" in ln and not ln.lstrip().startswith(("publish", "douyin", "xhs", "video_account", "bilibili", "published", "url", "date")):
                k, v = ln.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta


def load_account_book():
    rows = {}
    if ACCOUNT_BOOK.exists():
        with ACCOUNT_BOOK.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows[row.get("id", "")] = row
    return rows


def _is_archived(e):
    """已归档 = 账本 status=video_ready 且目标文件真实存在（账本可能预填文件名）。"""
    if e.get("status") != "video_ready":
        return False
    fname = e.get("video_filename") or ""
    if not fname:
        return False
    return (pathlib.Path(e["folder"]) / fname).exists()


def build_manifest(variant=DEFAULT_VARIANT):
    """扫描主题文件夹，输出批量计划条目。"""
    entries = []
    book = load_account_book()
    folders = []
    for child in sorted(OUT_ROOT.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "meta.yaml").exists():
            folders.append(child)  # 旧式：直接主题文件夹
        else:
            # 系列容器：如 钢铁造物_20260805/<主题>_20260805
            folders.extend(sorted(f for f in child.iterdir() if f.is_dir() and (f / "meta.yaml").exists()))
    seen = set()
    for folder in folders:
        if folder in seen:
            continue
        seen.add(folder)
        meta_path = folder / "meta.yaml"
        if not meta_path.exists():
            continue
        meta = load_meta(folder)
        tid = meta.get("id", "")
        status = meta.get("status", "")
        if status not in ("frames_ready", "video_ready", "draft"):
            continue
        pack = parse_content_pack(folder / "内容包.md")
        if not pack:
            continue
        prompt = pack.get(variant, "")
        if not prompt:
            print("  [skip] %s 无 %s 档提示词" % (folder.name, variant))
            continue
        pack_text = (folder / "内容包.md").read_text(encoding="utf-8")
        m_ratio = re.search(r"比例[:：]\s*([\d:]+)", pack_text)
        m_dur = re.search(r"时长[:：]\s*([\d.]+)\s*秒", pack_text)
        aspect = m_ratio.group(1).strip() if m_ratio else DEFAULT_ASPECT
        duration = int(float(m_dur.group(1))) if m_dur else DEFAULT_DURATION
        book_row = book.get(tid, {})
        frame_ratio = "9:16"  # 当前首尾帧图规格 1024x1820
        if aspect != frame_ratio:
            print("  [note] %s 内容包比例=%s 与首尾帧图比例=%s 不一致；首尾帧模式比例锁定自动，输出将跟随图片比例" % (folder.name, aspect, frame_ratio))
        entries.append({
            "id": tid,
            "theme": meta.get("title", folder.name),
            "series": meta.get("series", ""),
            "date": meta.get("date", ""),
            "folder": str(folder),
            "variant": variant,
            "prompt": prompt,
            "frame_start": str(folder / "frame_start.png"),
            "frame_end": str(folder / "frame_end.png"),
            "model": DEFAULT_MODEL,
            "duration": duration,
            "aspect": aspect,
            "status": book_row.get("status", status) or status,
            "video_filename": book_row.get("video_filename", "") or "",
        })
    return entries


# ---------------------------------------------------------------- 页面填充
def _set_mode_first_last():
    """把参考模式切到首尾帧。"""
    r = eval_js("""(() => {
      const cb=[...document.querySelectorAll('[role=combobox]')].find(e=>e.getClientRects().length && (e.innerText||'').includes('全能参考')||(e.innerText||'').includes('首尾帧'));
      if(!cb) return 'no-mode-combo';
      ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(t=>{try{cb.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}))}catch(e){}});
      return 'opened';
    })()""")
    time.sleep(0.8)
    if r != "opened":
        return r
    opt = eval_js("""(() => {
      const o=[...document.querySelectorAll('.lv-select-option')].find(e=>e.getClientRects().length && (e.textContent||'').trim()==='首尾帧');
      if(!o) return 'no-option';
      o.click(); return 'clicked';
    })()""")
    return opt


def _set_model(model=DEFAULT_MODEL):
    r = eval_js("""(() => {
      const cbs=[...document.querySelectorAll('[role=combobox]')];
      const cb=cbs.find(e=>e.getClientRects().length && /Seedance|模型/.test(e.innerText||''));
      if(!cb) return 'no-model-combo';
      ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(t=>{try{cb.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}))}catch(e){}});
      return 'opened';
    })()""")
    time.sleep(0.8)
    opt = eval_js("""(() => {
      const target = %s;
      const o=[...document.querySelectorAll('.lv-select-option')].find(e=>e.getClientRects().length && (e.textContent||'').includes(target));
      if(!o) return 'no-option';
      o.click(); return 'clicked';
    })()""" % json.dumps(model))
    return opt


def _set_duration(sec=DEFAULT_DURATION):
    r = eval_js("""(() => {
      const b=[...document.querySelectorAll('button')].find(x=>x.getClientRects().length && /^\\d+s$/.test((x.innerText||'').trim()));
      if(!b) return 'no-duration-btn';
      b.click(); return 'opened';
    })()""")
    time.sleep(1.0)
    # 1) 刻度按钮精确命中（0/5/10/15…）
    tick = eval_js("""(() => {
      const t=String(%d);
      const b=[...document.querySelectorAll('button')].find(x=>x.getClientRects().length && (x.innerText||'').trim()===t && x.offsetWidth<60);
      if(!b) return 'no-tick';
      b.click(); return 'clicked';
    })()""" % sec)
    if tick != "clicked":
        # 2) 非刻度秒数（如 12s）→ 滑块轨道点击：x = left + w * sec / max
        info = eval_js("""(() => {
          const road=document.querySelector('[class*=slider-road]') || document.querySelector('[class*=slider-visual-rail]') || document.querySelector('[class*=slider-wrap]');
          if(!road) return null;
          const r=road.getBoundingClientRect();
          const ticks=[...document.querySelectorAll('button')].filter(b=>/^\\d+$/.test((b.innerText||'').trim())&&b.getClientRects().length).map(b=>parseInt(b.innerText,10));
          const max=Math.max.apply(null, ticks);
          return JSON.stringify({x:Math.round(r.x), y:Math.round(r.y+r.height/2), w:Math.round(r.width), max});
        })()""")
        info = json.loads(info) if info else None
        if not info or not info.get("max"):
            _close_panels()
            return "no-slider"
        cx = info["x"] + int(info["w"] * sec / info["max"])
        cy = info["y"]
        wb("cdp", {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1}})
        wb("cdp", {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1}})
        time.sleep(0.8)
        # 若未生效，再点一次（面板可能重新定位）
        check_now = eval_js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>x.getClientRects().length && /^\\d+s$/.test((x.innerText||'').trim()));
          return b? (b.innerText||'').trim() : null;
        })()""")
        if check_now != "%ds" % sec:
            wb("cdp", {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1}})
            wb("cdp", {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1}})
            time.sleep(0.8)
        tick = "slider-click:%d" % sec
    _close_panels()
    # 3) 校验时长按钮文本
    check = eval_js("""(() => {
      const b=[...document.querySelectorAll('button')].find(x=>x.getClientRects().length && /^\\d+s$/.test((x.innerText||'').trim()));
      return b? (b.innerText||'').trim() : null;
    })()""")
    if check != "%ds" % sec:
        raise RuntimeError("时长设置失败：期望 %ds，实际 %s" % (sec, check))
    return tick


def _set_aspect(aspect=DEFAULT_ASPECT):
    r = eval_js("""(() => {
      const b=[...document.querySelectorAll('button')].find(x=>x.getClientRects().length && /720P/.test(x.innerText||''));
      if(!b) return 'no-aspect-btn';
      b.click(); return 'opened';
    })()""")
    time.sleep(0.8)
    opt = eval_js("""(() => {
      const radios=[...document.querySelectorAll('[role=radio]')];
      const o=radios.find(e=>e.getClientRects().length && (e.textContent||'').trim()===%s);
      if(!o) return 'no-radio';
      o.click(); return 'clicked';
    })()""" % json.dumps(aspect))
    eval_js("""(() => { document.body.click(); return 'ok'; })()""")
    return opt


def _upload_frame(idx, img_path, name):
    """按 input 索引注入图片（每次注入前重新查询，避免 React 重渲染导致引用失效）。"""
    b64 = base64.b64encode(pathlib.Path(img_path).read_bytes()).decode()
    js = f"""(() => {{
      const input=document.querySelectorAll('input[type=file]')[{idx}];
      if(!input) return 'no-input';
      const bin=atob('{b64}');
      const bytes=new Uint8Array(bin.length);
      for(let i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);
      const file=new File([bytes], {json.dumps(name)}, {{type:'image/png'}});
      const dt=new DataTransfer(); dt.items.add(file);
      input.files=dt.files;
      input.dispatchEvent(new Event('change',{{bubbles:true}}));
      return 'injected';
    }})()"""
    return eval_js(js)


def _fill_prompt(prompt):
    js = """(() => {
      const ta=document.querySelector('textarea.prompt-textarea, textarea');
      if(!ta) return 'no-textarea';
      const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
      setter.call(ta, %s);
      ta.dispatchEvent(new Event('input',{bubbles:true}));
      ta.dispatchEvent(new Event('change',{bubbles:true}));
      return 'filled:'+ta.value.length;
    })()""" % json.dumps(prompt)
    return eval_js(js)


def _close_panels():
    """点击提示词输入框收起 popover（实测可靠）。"""
    return eval_js("""(() => {
      const ta=document.querySelector('textarea.prompt-textarea, textarea');
      if(!ta) return 'no-ta';
      const r=ta.getBoundingClientRect();
      ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(t=>{try{ta.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,clientX:r.x+20,clientY:r.y+20,view:window}))}catch(e){}});
      return 'closed';
    })()""")


def _clear_conversation_input():
    """清空同会话复用时的提示词与首尾帧预览。"""
    ta = "no-ta"
    for _ in range(6):
        ta = eval_js("""(() => {
          const ta=document.querySelector('textarea.prompt-textarea, textarea');
          if(!ta) return 'no-ta';
          const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
          setter.call(ta,'');
          ta.dispatchEvent(new Event('input',{bubbles:true}));
          return 'ta-cleared';
        })()""")
        if "ta-cleared" in str(ta):
            break
        time.sleep(2)
    if "ta-cleared" not in str(ta):
        raise RuntimeError("同会话复用：找不到输入框（%s），中止避免误提交" % ta)
    rm = eval_js("""(() => {
      const btns=[...document.querySelectorAll('[class*=remove-button]')].filter(b=>b.getClientRects().length);
      btns.forEach(b=>b.click());
      return 'removed:'+btns.length;
    })()""")
    time.sleep(1.2)
    return ta, rm


def prepare_in_conversation(ws_url, entry):
    """在既有会话内复用输入区提交新条目（清空 -> 重设配置 -> 传图 -> 填提示词）。"""
    print("  (复用会话 %s)" % ws_url.split("workspace=")[-1][:14])
    wb("navigate", {"url": ws_url})
    time.sleep(4)
    # 导航回会话后模式会重置为“全能参考”，必须先切回首尾帧再清空/配置
    mode = None
    for attempt in range(3):
        mode = _set_mode_first_last()
        print("  mode(attempt %d):" % (attempt + 1), mode)
        if mode == "clicked":
            break
        time.sleep(5)
        wb("navigate", {"url": ws_url})
        time.sleep(5)
    if mode != "clicked":
        raise RuntimeError("切首尾帧模式失败: %s" % mode)
    time.sleep(1)
    print("  clear:", _clear_conversation_input())
    time.sleep(1)
    print("  model:", _set_model(entry["model"]))
    time.sleep(0.8)
    print("  duration:", _set_duration(entry["duration"]))
    _close_panels()
    time.sleep(0.6)
    print("  frame_start:", _upload_frame(0, entry["frame_start"], "frame_start.png"))
    time.sleep(2)
    print("  frame_end:", _upload_frame(0, entry["frame_end"], "frame_end.png"))
    time.sleep(2)
    v = _verify_frames_loaded()
    print("  frames_verify:", v)
    if json.loads(v).get("loaded") != [True, True]:
        # 缺帧重试一次：重新按 index 0 注入
        print("  缺帧重试...")
        time.sleep(2)
        _upload_frame(0, entry["frame_end"], "frame_end.png")
        time.sleep(2)
        v = _verify_frames_loaded()
        print("  frames_verify(retry):", v)
        if json.loads(v).get("loaded") != [True, True]:
            raise RuntimeError("首尾帧未全部加载，停止提交: %s" % v)
    print("  prompt:", _fill_prompt(entry["prompt"]))
    time.sleep(0.8)
    return _read_credit_cost()


def _read_credit_cost():
    return eval_js("""(() => {
      const el=document.querySelector('[class*=actual-credits]');
      const orig=document.querySelector('[class*=original-credits]');
      const btn=[...document.querySelectorAll('button')].find(b=>b.getClientRects().length && /submit/.test((b.className||'').toString()));
      return JSON.stringify({cost: el? (el.textContent||'').trim():null, original: orig? (orig.textContent||'').trim():null, hasSubmit:!!btn});
    })()""")


def _gen_status():
    """返回生成状态: waiting / generating / done / error。"""
    raw = eval_js("""(() => {
      const txt=document.body.innerText;
      const hasCancel=!![...document.querySelectorAll('button,[class*=cancel]')].find(e=>e.getClientRects().length && /取消生成/.test(e.textContent||''));
      const dl=[...document.querySelectorAll('button,a')].find(e=>e.getClientRects().length && /下载/.test((e.innerText||e.getAttribute('aria-label')||'')));
      const vids=[...document.querySelectorAll('video')].filter(v=>v.getClientRects().length && v.readyState>=2 && v.duration>0 && !/(loading-animation|record-loading)/.test(v.currentSrc||v.src||''));
      return JSON.stringify({hasCancel, hasDownload:!!dl, vidSrc:vids.length? (vids[0].currentSrc||vids[0].src||'').slice(0,200):'', vidCount:vids.length, snippet:txt.slice(0,120)});
    })()""")
    info = json.loads(raw)
    if (info.get("hasCancel") and not info.get("hasDownload")) or (not info.get("hasCancel") and not info.get("hasDownload") and not info.get("vidCount")):
        return "generating", info
    if info.get("hasDownload") or (info.get("vidCount") and not info.get("hasCancel")):
        return "done", info
    return "unknown", info


def _gen_status_turn(entry):
    """按提示词标记定位该轮 record，判定其视频状态（同会话多轮安全）。"""
    marker = re.sub(r"\s+", " ", entry["prompt"])[:60]
    raw = eval_js("""(() => {
      const vp=[...document.querySelectorAll('[class*=viewport]')].find(e=>e.getClientRects().length&&e.scrollHeight>e.clientHeight+100);
      if(vp) vp.scrollTop=vp.scrollHeight;
      let best=null;
      [...document.querySelectorAll('*')].forEach(e=>{const t=(e.textContent||'').replace(/\\s+/g,' '); if(t.includes(%s)&&t.length<2000){ if(!best||t.length<best.len) best={el:e,len:t.length}; }});
      if(!best) return JSON.stringify({state:'no-record'});
      let p=best.el;
      for(let k=0;k<12&&p;k++){
        const cls=(p.className||'').toString();
        if(/record-/.test(cls) && !/record-header|record-list|record-content/.test(cls)){
          const v=[...p.querySelectorAll('video')].find(x=>x.getClientRects().length&&x.readyState>=2&&x.duration>0&&!/(loading-animation|record-loading)/.test(x.currentSrc||x.src||''));
          const txt=p.innerText||'';
          if(v) return JSON.stringify({state:'done', vid:v.currentSrc||v.src});
          if(/取消生成|排队|生成中|等待/.test(txt)) return JSON.stringify({state:'generating'});
          return JSON.stringify({state:'pending'});
        }
        p=p.parentElement;
      }
      return JSON.stringify({state:'no-record'});
    })()""" % json.dumps(marker))
    info = json.loads(raw)
    return info.get("state"), info


def poll_turn(entry, max_minutes=20, interval=25):
    t0 = time.time()
    while time.time() - t0 < max_minutes * 60:
        state, info = _gen_status_turn(entry)
        print("[%s] turn-state=%s" % (time.strftime("%H:%M:%S"), state), flush=True)
        if state == "done":
            return info
        time.sleep(interval)
    raise RuntimeError("poll_turn timeout after %d min" % max_minutes)


def _video_url():
    return eval_js("""(() => {
      const v=[...document.querySelectorAll('video')].find(x=>x.getClientRects().length && (x.currentSrc||x.src) && x.readyState>=2 && !/(loading-animation|record-loading)/.test(x.currentSrc||x.src||''));
      return v? (v.currentSrc||v.src) : null;
    })()""")


def _read_balance():
    raw = eval_js("""(() => { const el=document.querySelector('[class*=credit-amount-text]'); return el? (el.textContent||'').trim() : null; })()""")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _verify_submitted(balance_before=None):
    """生成点击后必须出现数字 workspace，且（若提供余额）积分已扣减。"""
    last = ""
    for _ in range(12):
        time.sleep(2)
        last = eval_js("location.href") or ""
        m = re.search(r"workspace=(\d+)", last)
        if m:
            ws = "https://jimeng.jianying.com/ai-tool/generate/?type=video&workspace=" + m.group(1)
            if balance_before is None:
                return ws
            bal = _read_balance()
            if bal is not None and bal < balance_before:
                return ws
            # 积分未变化，可能仍在确认或未提交成功；继续等待观察
    raise RuntimeError("提交未成功（无数字 workspace 或积分未扣减）: %s" % last)


def _verify_page_matches(entry):
    """下载前校验当前页面含目标提示词标记，防止下错视频。"""
    marker = entry["prompt"][:18]
    body = eval_js("document.body.innerText") or ""
    if marker not in body:
        raise RuntimeError("页面内容与 %s 不匹配（缺提示词标记），停止下载以免错视频" % entry["id"])


def poll(max_minutes=20, interval=30):
    t0 = time.time()
    while time.time() - t0 < max_minutes * 60:
        status, info = _gen_status()
        print("[%s] %s" % (time.strftime("%H:%M:%S"), status), info.get("snippet", "")[:60])
        if status == "done":
            print("  video:", info.get("vidSrc", "")[:200])
            return info
        time.sleep(interval)
    raise RuntimeError("poll timeout after %d min" % max_minutes)


def _verify_frames_loaded():
    """严格校验首尾帧槽位：img 存在、complete 且 naturalWidth>0。"""
    return eval_js("""(() => {
      const slots=[...document.querySelectorAll('[class*=reference-group-content]')].filter(e=>{const r=e.getBoundingClientRect(); return r.width>0&&r.height>0;});
      const imgs=slots.map(e=>{const img=e.querySelector('img'); return img? (img.complete && img.naturalWidth>0): false;});
      return JSON.stringify({count:slots.length, loaded:imgs});
    })()""")


def prepare(entry):
    print("== prepare %s (%s)" % (entry["theme"], entry["id"]))
    wb("navigate", {"url": VIDEO_URL})
    time.sleep(4)
    mode = None
    for attempt in range(3):
        mode = _set_mode_first_last()
        print("  mode(attempt %d):" % (attempt + 1), mode)
        if mode == "clicked":
            break
        time.sleep(5)
        wb("navigate", {"url": VIDEO_URL})
        time.sleep(5)
    if mode != "clicked":
        raise RuntimeError("切首尾帧模式失败: %s" % mode)
    time.sleep(1.2)
    print("  model:", _set_model(entry["model"]))
    time.sleep(0.8)
    print("  duration:", _set_duration(entry["duration"]))
    _close_panels()
    time.sleep(0.6)
    print("  aspect(首尾帧模式锁定自动):", _set_aspect(entry["aspect"]))
    _close_panels()
    time.sleep(0.6)
    print("  frame_start:", _upload_frame(0, entry["frame_start"], "frame_start.png"))
    time.sleep(2)
    print("  frame_end:", _upload_frame(0, entry["frame_end"], "frame_end.png"))
    time.sleep(2)
    v = _verify_frames_loaded()
    print("  frames_verify:", v)
    if json.loads(v).get("loaded") != [True, True]:
        print("  缺帧重试...")
        time.sleep(2)
        _upload_frame(0, entry["frame_end"], "frame_end.png")
        time.sleep(2)
        v = _verify_frames_loaded()
        print("  frames_verify(retry):", v)
        if json.loads(v).get("loaded") != [True, True]:
            raise RuntimeError("首尾帧未全部加载，停止提交: %s" % v)
    time.sleep(0.8)
    print("  prompt:", _fill_prompt(entry["prompt"]))
    time.sleep(0.8)
    cost = _read_credit_cost()
    print("  cost:", cost)
    return cost


# ---------------------------------------------------------------- 命令实现
def cmd_plan(args):
    entries = build_manifest(args.variant)
    print("MORPHWORKS 即梦视频批量计划（variant=%s）" % args.variant)
    print("%-18s %-12s %-4s %-12s %-6s %-6s %s" % ("id", "theme", "len", "model", "dur", "ratio", "status"))
    for e in entries:
        print("%-18s %-12s %-4d %-12s %-6s %-6s %s" % (
            e["id"], e["theme"], len(e["prompt"]), e["model"], "%ds" % e["duration"], e["aspect"],
            e["video_filename"] and "video:%s" % e["video_filename"] or e["status"]))
    print("total:", len(entries))
    return entries


def cmd_prepare(args):
    entries = build_manifest(args.variant)
    targets = [e for e in entries if not args.id or e["id"] == args.id]
    if not targets:
        print("no target entries")
        return
    print("批量计划（积分统计以页面当时显示为准）:")
    print("%-18s %-12s %-10s %-8s %s" % ("id", "theme", "model", "dur", "预计积分"))
    for e in targets:
        cost_info = prepare(e)
        cost = "?"
        try:
            cost = json.loads(cost_info).get("cost", "?")
        except Exception:
            pass
        e["credit_info"] = cost_info
        e["credit_cost"] = cost
        print("%-18s %-12s %-16s %-8s %s" % (e["id"], e["theme"], e["model"], "%ds" % e["duration"], cost))
        (OUT_ROOT / ".jimeng_prep.json").write_text(
            json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  -> 已填页未点生成；积分=%s；prep 存于 .jimeng_prep.json" % cost)


def cmd_generate(args):
    r = None
    for attempt in range(3):
        r = eval_js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>x.getClientRects().length && /submit/.test((x.className||'').toString()));
          if(!b) return 'no-submit';
          if(b.disabled) return 'disabled';
          b.click(); return 'clicked';
        })()""")
        print("generate(attempt %d):" % (attempt + 1), r, flush=True)
        if r == "clicked":
            return
        time.sleep(4)
    raise RuntimeError("generate click failed: %s" % r)


def cmd_poll(args):
    return poll(args.max_minutes, args.interval)


def cmd_run_one(args):
    """单条端到端：generate -> poll -> download -> archive。"""
    entry = json.loads((OUT_ROOT / ".jimeng_prep.json").read_text(encoding="utf-8"))
    print("== run_one %s" % entry["id"])
    cmd_generate(args)
    ws_url = _verify_submitted()
    wb("navigate", {"url": ws_url})
    time.sleep(5)
    info = poll(args.max_minutes, args.interval)
    _verify_page_matches(entry)
    url = info.get("vidSrc") or _video_url()
    if not url:
        raise RuntimeError("未拿到视频地址")
    out = pathlib.Path("/tmp/jimeng_dl") / (entry["id"] + ".mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, out.open("wb") as f:
        shutil.copyfileobj(r, f)
    print("downloaded:", out, out.stat().st_size)
    archive(entry, out)


def cmd_run_all(args):
    """整批：prepare -> generate -> poll -> download -> archive（逐条串行）。"""
    entries = build_manifest(args.variant)
    todo = [e for e in entries if not _is_archived(e)]
    print("整批待执行 %d 条" % len(todo), flush=True)
    for e in todo:
        print("=== [%s] %s" % (e["id"], e["theme"]), flush=True)
        cost_info = prepare(e)
        try:
            e["credit_cost"] = str(json.loads(cost_info).get("cost", "?"))
        except Exception:
            e["credit_cost"] = "?"
        (OUT_ROOT / ".jimeng_prep.json").write_text(
            json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  积分=%s，点击生成" % e["credit_cost"], flush=True)
        cmd_generate(args)
        time.sleep(6)
        info = poll(args.max_minutes, args.interval)
        url = info.get("vidSrc") or _video_url()
        if not url:
            print("  !! %s 未拿到视频地址，跳过" % e["id"], flush=True)
            continue
        out = pathlib.Path("/tmp/jimeng_dl") / (e["id"] + ".mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://jimeng.jianying.com/"})
        with urllib.request.urlopen(req, timeout=180) as r, out.open("wb") as f:
            shutil.copyfileobj(r, f)
        print("  下载完成 %d bytes" % out.stat().st_size, flush=True)
        archive(e, out)
        print("--- [%s] %s 归档完成" % (e["id"], e["theme"]), flush=True)
    print("整批执行完毕", flush=True)


def cmd_run_pipeline(args):
    """流水线模式：提交阶段逐条进入生产即提交下一条；收集阶段逐条回查下载归档。"""
    state_path = OUT_ROOT / ".jimeng_pipeline.json"
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    entries = build_manifest(args.variant)
    by_id = {e["id"]: e for e in entries}
    # ---- 阶段 A：提交所有未进入生产的条目
    for e in entries:
        if _is_archived(e):
            print("skip %s (已归档)" % e["id"], flush=True)
            continue
        st = state.get(e["id"], {})
        if st.get("status") in ("generating", "done"):
            print("skip submit %s (%s)" % (e["id"], st.get("status")), flush=True)
            continue
        print("=== submit %s %s" % (e["id"], e["theme"]), flush=True)
        cost_info = prepare(e)
        try:
            e["credit_cost"] = str(json.loads(cost_info).get("cost", "?"))
        except Exception:
            e["credit_cost"] = "?"
        (OUT_ROOT / ".jimeng_prep.json").write_text(
            json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  积分=%s，点击生成" % e["credit_cost"], flush=True)
        cmd_generate(args)
        ws_url = _verify_submitted()
        url = ws_url
        state[e["id"]] = {"url": url, "status": "generating", "credit_cost": e["credit_cost"]}
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  -> 已进入生产，提交下一条；workspace=%s" % url.split("workspace=")[-1][:20], flush=True)
    # ---- 阶段 B：逐条回查 -> 下载 -> 归档
    for e in entries:
        st = state.get(e["id"])
        if not st or st.get("status") != "generating":
            continue
        print("=== collect %s %s" % (e["id"], e["theme"]), flush=True)
        wb("navigate", {"url": st["url"]})
        time.sleep(5)
        info = poll(args.max_minutes, args.interval)
        _verify_page_matches(e)
        url = info.get("vidSrc") or _video_url()
        if not url:
            print("  !! %s 未拿到视频地址" % e["id"], flush=True)
            st["status"] = "missing-url"
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        out = pathlib.Path("/tmp/jimeng_dl") / (e["id"] + ".mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://jimeng.jianying.com/"})
        with urllib.request.urlopen(req, timeout=180) as r, out.open("wb") as f:
            shutil.copyfileobj(r, f)
        print("  下载完成 %d bytes" % out.stat().st_size, flush=True)
        e["credit_cost"] = st.get("credit_cost", "60")
        archive(e, out)
        st["status"] = "done"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print("--- [%s] %s 归档完成" % (e["id"], e["theme"]), flush=True)
    print("流水线执行完毕", flush=True)


def cmd_run_batch_conv(args):
    """同会话批量：每 N 条共用一个会话（串行提交+轮询）；断点续跑防重复提交。"""
    batch_n = max(1, args.batch_size)
    state_path = OUT_ROOT / ".jimeng_batchconv.json"
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    entries = build_manifest(args.variant)
    todo = [e for e in entries if not _is_archived(e)]
    todo.sort(key=lambda e: (e.get("series") or "", e.get("theme") or ""))
    print("同会话批量：%d 条，每 %d 条一个会话" % (len(todo), batch_n), flush=True)

    def save_state():
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    seen_md5 = set()

    def collect(e, ws_url):
        wb("navigate", {"url": ws_url})
        time.sleep(5)
        info = poll_turn(e, args.max_minutes, args.interval)
        _verify_page_matches(e)
        url = info.get("vid") or _video_url()
        if not url:
            raise RuntimeError("未拿到视频地址")
        out = pathlib.Path("/tmp/jimeng_dl") / (e["id"] + ".mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://jimeng.jianying.com/"})
        with urllib.request.urlopen(req, timeout=180) as r, out.open("wb") as f:
            shutil.copyfileobj(r, f)
        md5 = hashlib.md5(out.read_bytes()).hexdigest()
        if md5 in seen_md5:
            raise RuntimeError("重复视频（md5=%s），拒绝归档，避免错片入库" % md5)
        seen_md5.add(md5)
        print("  下载完成 %d bytes" % out.stat().st_size, flush=True)
        archive(e, out)

    # ---- 恢复/收集已提交但未归档的条目（防重复提交）
    for e in todo:
        st = state.get(e["id"])
        if st and st.get("status") == "generating":
            print("=== 恢复收集 [%s] %s" % (e["id"], e["theme"]), flush=True)
            try:
                collect(e, st["url"])
                st["status"] = "done"
                save_state()
                print("--- [%s] 恢复归档完成" % e["id"], flush=True)
            except Exception as exc:
                st["status"] = "error"
                st["reason"] = str(exc)[:200]
                save_state()
                print("  !! [%s] 恢复失败: %s" % (e["id"], exc), flush=True)

    # ---- 提交新的（未进入生产的）
    pending = [e for e in todo if state.get(e["id"], {}).get("status") not in ("generating", "done")]
    ws_url = None
    in_batch = 0
    for e in pending:
        if in_batch % batch_n == 0:
            ws_url = None  # 新会话
        print("=== [%s] %s (会话内第 %d 条)" % (e["id"], e["theme"], in_batch % batch_n + 1), flush=True)
        try:
            if ws_url is None:
                cost_info = prepare(e)
            else:
                cost_info = prepare_in_conversation(ws_url, e)
            try:
                e["credit_cost"] = str(json.loads(cost_info).get("cost", "?"))
            except Exception:
                e["credit_cost"] = "?"
            (OUT_ROOT / ".jimeng_prep.json").write_text(
                json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
            print("  积分=%s，点击生成" % e["credit_cost"], flush=True)
            bal_before = _read_balance()
            cmd_generate(args)
            ws_url = _verify_submitted(bal_before)
            state[e["id"]] = {"url": ws_url, "status": "generating", "credit_cost": e["credit_cost"]}
            save_state()
            print("  -> 已进入生产，workspace=%s" % ws_url.split("workspace=")[-1][:16], flush=True)
            collect(e, ws_url)
            state[e["id"]]["status"] = "done"
            save_state()
            in_batch += 1
            print("--- [%s] %s 归档完成" % (e["id"], e["theme"]), flush=True)
        except Exception as exc:
            # 失败隔离：记录原因，不阻塞后续条目
            if e["id"] in state and state[e["id"]].get("status") == "generating":
                state[e["id"]]["status"] = "error"
                state[e["id"]]["reason"] = str(exc)[:200]
            save_state()
            in_batch += 1
            print("  !! [%s] %s 失败: %s" % (e["id"], e["theme"], exc), flush=True)
            continue
    save_state()
    print("同会话批量执行完毕", flush=True)


def archive(entry, src):
    """视频回写来源文件夹 + 重命名 + 更新账本/meta。"""
    folder = pathlib.Path(entry["folder"])
    date = entry.get("date") or ""
    if not date:
        m = re.search(r"_(\d{8})$", folder.name)
        date = m.group(1) if m else time.strftime("%Y%m%d")
    theme = entry["theme"]
    name = "%s_%s_未发布.mp4" % (date, theme)
    dst = folder / name
    shutil.move(str(src), str(dst))
    print("archived:", dst)
    # 更新账本
    book_path = ACCOUNT_BOOK
    rows = list(csv.DictReader(book_path.open(encoding="utf-8")))
    fieldnames = list(rows[0].keys()) if rows else ["id", "theme", "date", "video_filename", "status", "credits", "platform_published"]
    for row in rows:
        if row.get("id") == entry["id"]:
            row["video_filename"] = name
            row["status"] = "video_ready"
            prev = 0
            try:
                prev = int(row.get("credits") or 0)
            except ValueError:
                pass
            video_cost = entry.get("credit_cost") or 0
            try:
                video_cost = int(video_cost)
            except (TypeError, ValueError):
                video_cost = 0
            row["credits"] = str(prev + video_cost)
    with book_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    # 更新 meta.yaml status
    meta_path = folder / "meta.yaml"
    if meta_path.exists():
        text = meta_path.read_text(encoding="utf-8")
        text = re.sub(r"^status:.*$", "status: video_ready", text, flags=re.M)
        if "credits_video:" not in text:
            text = re.sub(r"^(credits_used:.*)$", r"\1\ncredits_video: %s" % (entry.get("credit_cost") or 0), text, flags=re.M)
        meta_path.write_text(text, encoding="utf-8")
    print("ledger updated: _ACCOUNT_BOOK.csv + meta.yaml")


def main():
    ap = argparse.ArgumentParser(description="MORPHWORKS 即梦视频批量工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--variant", default=DEFAULT_VARIANT)
    p.add_argument("--id")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("prepare")
    p.add_argument("--variant", default=DEFAULT_VARIANT)
    p.add_argument("--id")
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("generate")
    p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("run-one")
    p.add_argument("--max-minutes", type=int, default=20)
    p.add_argument("--interval", type=int, default=30)
    p.set_defaults(fn=cmd_run_one)
    p = sub.add_parser("poll")
    p.add_argument("--max-minutes", type=int, default=20)
    p.add_argument("--interval", type=int, default=30)
    p.set_defaults(fn=cmd_poll)
    p = sub.add_parser("run-all")
    p.add_argument("--variant", default=DEFAULT_VARIANT)
    p.add_argument("--max-minutes", type=int, default=20)
    p.add_argument("--interval", type=int, default=25)
    p.set_defaults(fn=cmd_run_all)
    p = sub.add_parser("run-pipeline")
    p.add_argument("--variant", default=DEFAULT_VARIANT)
    p.add_argument("--max-minutes", type=int, default=20)
    p.add_argument("--interval", type=int, default=25)
    p.set_defaults(fn=cmd_run_pipeline)
    p = sub.add_parser("run-batch-conv")
    p.add_argument("--variant", default=DEFAULT_VARIANT)
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--max-minutes", type=int, default=20)
    p.add_argument("--interval", type=int, default=25)
    p.set_defaults(fn=cmd_run_batch_conv)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
