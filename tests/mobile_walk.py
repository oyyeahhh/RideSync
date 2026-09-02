"""Walk CarpoolSync as an admin and as a parent, on a phone and on a laptop.

Produces full-page screenshots plus per-page metrics (horizontal overflow, tap
targets under 40px, text under 12px, unlabelled inputs, console errors) in
SHOTS_DIR (default ./mobile_shots). Needs a server on 127.0.0.1:3000 against a
fresh DATA_DIR, and `pip install playwright && playwright install chromium`."""
import json
import re
import os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3000"
OUT = os.environ.get("SHOTS_DIR", "mobile_shots")
os.makedirs(OUT, exist_ok=True)
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2,
         "is_mobile": True, "has_touch": True,
         "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"}
LAPTOP = {"viewport": {"width": 1280, "height": 800}}
report = {}

METRICS_JS = """
() => {
  const de = document.documentElement;
  const overflow = de.scrollWidth - de.clientWidth;
  const small = [];
  const els = document.querySelectorAll('a,button,input,select,textarea,[role=button],label[for]');
  els.forEach(el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || cs.visibility === 'hidden' || cs.display === 'none') return;
    if (r.top > window.innerHeight * 3) return;
    if ((r.height < 40 || r.width < 40) && !(el.tagName === 'A' && r.width > 120)) {
      small.push({tag: el.tagName, text: (el.innerText || el.value || el.getAttribute('aria-label') || el.placeholder || '').trim().slice(0, 40), w: Math.round(r.width), h: Math.round(r.height)});
    }
  });
  const tiny = [];
  document.querySelectorAll('body *').forEach(el => {
    if (!el.innerText || !el.innerText.trim()) return;
    if (el.children.length && [...el.children].some(c => c.innerText && c.innerText.trim())) return;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs && fs < 12) tiny.push({text: el.innerText.trim().slice(0, 40), fs});
  });
  const vp = document.querySelector('meta[name=viewport]');
  const imgsNoAlt = [...document.querySelectorAll('img')].filter(i => !i.hasAttribute('alt')).length;
  const inputsNoLabel = [...document.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(i => {
    if (i.getAttribute('aria-label') || i.getAttribute('aria-labelledby')) return false;
    if (i.id && document.querySelector(`label[for="${i.id}"]`)) return false;
    return !i.closest('label');
  }).length;
  return {overflow, small: small.slice(0, 12), smallCount: small.length, tiny: tiny.slice(0, 8), tinyCount: tiny.length,
          viewport: vp ? vp.getAttribute('content') : null, imgsNoAlt, inputsNoLabel,
          title: document.title, h1: (document.querySelector('h1')||{}).innerText || null};
}
"""


def snap(page, name, dev):
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
    page.wait_for_timeout(600)
    page.screenshot(path=f"{OUT}/{name}_{dev}.png", full_page=True)
    m = page.evaluate(METRICS_JS)
    m["console"] = errors[:8]
    m["url"] = page.url
    report[f"{name}_{dev}"] = m
    print(f"{name:22s} {dev:6s} overflow={m['overflow']:>4} small={m['smallCount']:>3} tiny={m['tinyCount']:>3} errs={len(errors)}  {page.url}")


def csrf_header(page):
    return {"X-CSRFToken": next((c["value"] for c in page.context.cookies() if c["name"] == "csrf_token"), "")}


def admin_walk(pw, dev, opts):
    b = pw.chromium.launch()
    ctx = b.new_context(**opts)
    page = ctx.new_page()

    page.goto(BASE + "/about"); snap(page, "about", dev)
    page.goto(BASE + "/login"); snap(page, "login", dev)
    page.goto(BASE + "/forgot-password"); snap(page, "forgot", dev)
    page.goto(BASE + "/create-group"); snap(page, "create_group", dev)
    page.goto(BASE + "/nope"); snap(page, "404", dev)

    # create group only once (phone run); laptop run logs in
    page.goto(BASE + "/create-group")
    if dev == "phone":
        page.fill('[name=group_name]', "Maplewood Soccer")
        page.fill('[name=name]', "Orly Test")
        page.fill('[name=family_name]', "Nadler")
        page.fill('[name=email]', "admin@example.com")
        page.fill('[name=phone]', "5551234567")
        page.fill('[name=password]', "Sunflower99")
        page.fill('[name=address]', "12 Maple St, Maplewood, NJ")
        page.fill('[name=child_name]', "Avi")
        page.check('[name=sms_consent]')
        page.click('button[type=submit]')
        page.wait_for_load_state("networkidle")
        snap(page, "welcome", dev)
    else:
        page.goto(BASE + "/login")
        page.fill('[name=email]', "admin@example.com")
        page.fill('[name=password]', "Sunflower99")
        page.click('button[type=submit]')
        page.wait_for_load_state("networkidle")

    page.goto(BASE + "/"); snap(page, "dash_admin_empty" if dev == "phone" else "dash_admin", dev)

    if dev == "phone":
        # add trips via API using the page's cookies
        hdr = csrf_header(page)
        page.request.post(BASE + "/schedule/add", data=json.dumps({
            "date": "2026-09-10", "destination_name": "Maplewood Field",
            "destination_address": "300 Valley St, Maplewood, NJ",
            "arrival_time": "16:30", "return_time": "18:00"}),
            headers={**hdr, "Content-Type": "application/json"})
        page.request.post(BASE + "/schedule/add-recurring", data=json.dumps({
            "start_date": "2026-09-14", "end_date": "2026-10-09", "weekdays": [0, 2, 4],
            "destination_name": "Maplewood Field", "destination_address": "300 Valley St, Maplewood, NJ",
            "arrival_time": "16:30"}),
            headers={**hdr, "Content-Type": "application/json"})
        # invite a parent (Twilio is unconfigured so the link lands in the session)
        page.goto(BASE + "/")
        tok = page.get_attribute('#inviteForm [name=csrf_token]', 'value')
        page.request.post(BASE + "/invite", form={"csrf_token": tok, "phone": "5559876543", "family_id": ""}, max_redirects=0)
        page.goto(BASE + "/")
        snap(page, "dash_admin_invited", dev)
        html = page.content()
        m = re.search(r'(http://127\.0\.0\.1:3000/signup\?token=[A-Za-z0-9_\-]+)', html)
        report["invite_link"] = m.group(1) if m else None
        print("invite link:", report["invite_link"])
        page.goto(BASE + "/"); snap(page, "dash_admin", dev)

    # drive page (admin sees drive_url)
    html = page.content()
    m = re.search(r'href="(/drive/[A-Za-z0-9_\-]+)"', html)
    if m:
        page.goto(BASE + m.group(1)); snap(page, "drive", dev)
    page.goto(BASE + "/admin/users"); snap(page, "admin_users", dev)
    page.goto(BASE + "/admin/system"); snap(page, "admin_system", dev)
    page.goto(BASE + "/admin/display-url"); snap(page, "display_url", dev)
    html = page.content()
    m = re.search(r'(/display/[A-Za-z0-9_\-]+)', html)
    if m:
        page.goto(BASE + m.group(1)); snap(page, "display_bulletin", dev)
    page.goto(BASE + "/profile"); snap(page, "profile", dev)
    page.goto(BASE + "/bulletin"); snap(page, "bulletin", dev)
    # open the Add Trip modal on the dashboard
    page.goto(BASE + "/")
    btn = page.query_selector('button:has-text("Add Trip"), button:has-text("Add trip"), [onclick*="openModal"], [onclick*="openTripModal"]')
    if btn:
        try:
            btn.click(force=True, timeout=5000); page.wait_for_timeout(400); snap(page, "dash_add_trip_modal", dev)
        except Exception as e:
            print("add-trip button not clickable on", dev, str(e)[:80])
    b.close()


def parent_walk(pw, dev, opts):
    link = report.get("invite_link")
    if not link:
        print("no invite link, skipping parent walk"); return
    b = pw.chromium.launch()
    ctx = b.new_context(**opts)
    page = ctx.new_page()
    if dev == "phone":
        page.goto(link); snap(page, "signup", dev)
        page.fill('[name=name]', "Dana Cohen")
        page.fill('[name=email]', "dana@example.com")
        page.fill('[name=family_name]', "Cohen")
        page.fill('[name=child_name]', "Noa")
        page.fill('[name=password]', "Sunflower99")
        page.fill('[name=address]', "48 Oak Ave, Maplewood, NJ")
        page.check('[name=sms_consent]')
        page.click('button[type=submit]')
        page.wait_for_load_state("networkidle")
        snap(page, "signup_done", dev)
    else:
        page.goto(BASE + "/login")
        page.fill('[name=email]', "dana@example.com")
        page.fill('[name=password]', "Sunflower99")
        page.click('button[type=submit]')
        page.wait_for_load_state("networkidle")
    page.goto(BASE + "/"); snap(page, "dash_parent", dev)
    page.goto(BASE + "/profile"); snap(page, "profile_parent", dev)
    b.close()


with sync_playwright() as pw:
    admin_walk(pw, "phone", PHONE)
    parent_walk(pw, "phone", PHONE)
    admin_walk(pw, "laptop", LAPTOP)
    parent_walk(pw, "laptop", LAPTOP)

json.dump(report, open(f"{OUT}/report.json", "w"), indent=1)
print("\nsaved", len([k for k in report if k != 'invite_link']), "pages")
