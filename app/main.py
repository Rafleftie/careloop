"""CareLoop backend (FastAPI). Serves the coordinator dashboard, the patient risk
worklist and patient detail pages, plus a small JSON API.

Run:  uvicorn app.main:app --reload      (from the careloop folder)
"""
import json
import math
import os
import sqlite3
from datetime import date, datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.i18n import REASON_ICONS, RESCHEDULE_REASONS, T

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "data", "careloop.db")
METRICS = os.path.join(ROOT, "model", "artifacts", "metrics.json")

app = FastAPI(title="CareLoop")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

CHW = {  # synthetic community health workers by ward
    "Biashara": "Joyce Kemunto", "Gitothua": "Joyce Kemunto",
    "Mwihoko": "Peter Otieno", "Mwiki": "Peter Otieno",
    "Kahawa Sukari": "Grace Wafula", "Ruiru Township": "Grace Wafula",
}
TIMES = ["8:30 AM", "9:00 AM", "9:30 AM", "10:00 AM", "10:30 AM", "11:00 AM"]
REASON_LABELS = {
    "transport": "Transport cost", "work_family": "Work or family",
    "anxiety": "Fear or anxiety", "other_clinic": "Went to another clinic", "other": "Other",
    "prefer_not": "Prefer not to say",
}
PAGE_SIZE = 10
FACILITY = "Ruiru Sub-County Hospital"
COORDINATOR = "Nurse Mary Wanjiku, R.N."

LEVEL_STYLE = {  # red only for High, amber for Medium, green for Low
    "High": {"chip": "bg-error-container text-on-error-container", "text": "text-error",
             "bar": "stroke-error", "soft": "bg-error-container/40"},
    "Medium": {"chip": "bg-amber-100 text-amber-800", "text": "text-amber-700",
               "bar": "stroke-amber-500", "soft": "bg-amber-50"},
    "Low": {"chip": "bg-emerald-100 text-emerald-800", "text": "text-emerald-700",
            "bar": "stroke-emerald-500", "soft": "bg-emerald-50"},
}


def get_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db


def demo_today(db) -> date:
    return date.fromisoformat(db.execute("SELECT value FROM meta WHERE key='today'").fetchone()[0])


def resched_map(db):
    rows = db.execute("""SELECT r.* FROM reschedules r
        JOIN (SELECT referral_id, MAX(id) AS mid FROM reschedules GROUP BY referral_id) m ON r.id = m.mid""").fetchall()
    return {r["referral_id"]: dict(r) for r in rows}


def stamp(today: date) -> str:
    return datetime.combine(today, datetime.now().time()).isoformat(timespec="seconds")


def get_consent(db, rid):
    r = db.execute("SELECT * FROM consents WHERE referral_id = ?", (rid,)).fetchone()
    if r:
        return {"sms": bool(r["sms"]), "chw": bool(r["chw"]), "research": bool(r["research"]), "recorded": True}
    return {"sms": True, "chw": True, "research": False, "recorded": False}  # clinic defaults until the patient chooses


def ensure_tables():
    """Tables for the patient-side flow. Safe to run on every start."""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS reschedules (id INTEGER PRIMARY KEY AUTOINCREMENT, referral_id INTEGER,
            reason TEXT, new_date TEXT, new_time TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS consents (referral_id INTEGER PRIMARY KEY, sms INTEGER, chw INTEGER,
            research INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS sms_log (id INTEGER PRIMARY KEY AUTOINCREMENT, referral_id INTEGER,
            direction TEXT, kind TEXT, text TEXT, status TEXT, created_at TEXT);
    """)
    has_worklist = db.execute("SELECT name FROM sqlite_master WHERE name='worklist'").fetchone()
    if has_worklist and db.execute("SELECT COUNT(*) FROM sms_log").fetchone()[0] == 0:
        today = demo_today(db)
        for d in load_worklist(db, today):  # history of reminders already sent before today
            body = sms_texts(d)["en"]
            cs = db.execute("SELECT * FROM contacts WHERE referral_id = ? ORDER BY sent_on", (d["referral_id"],)).fetchall()
            for c in cs:
                when = sh(c["sent_on"], d["shift_days"]).isoformat() + "T08:00:00"
                db.execute("INSERT INTO sms_log (referral_id, direction, kind, text, status, created_at) VALUES (?,?,?,?,?,?)",
                           (d["referral_id"], "out", "reminder", body, "Replied" if c["responded"] else "No reply", when))
                if c["responded"]:
                    db.execute("INSERT INTO sms_log (referral_id, direction, kind, text, status, created_at) VALUES (?,?,?,?,?,?)",
                               (d["referral_id"], "in", "reply", "YES", "Received", when.replace("T08", "T10")))
    db.commit()
    db.close()


def sh(d: str, days: int) -> date:
    return date.fromisoformat(d) + timedelta(days=days)


def fmt(d: date, pattern: str) -> str:
    return d.strftime(pattern).replace(" 0", " ")


def recommend(d: dict) -> dict:
    """Rule-based next action. The model scores risk; these rules turn the score and
    its reasons into a concrete step. A high score means MORE support, never less."""
    level, chw = d["risk_level"], d["chw"].split()[0]
    silent = d["reminders_sent"] >= 2 and d["reminders_responded"] == 0
    rr = d.get("resched_reason")
    if rr == "anxiety":
        return {"type": "call", "title": "Nurse phone call before the visit",
                "detail": "The patient said they feel anxious or afraid. A nurse call first was promised in the app.",
                "button": "Call patient", "short": "Nurse call (anxiety)"}
    if rr == "transport":
        return {"type": "offer_transport", "title": "Offer transport support if available",
                "detail": "The patient said the fare is the barrier. Check whether a transport voucher can be offered.",
                "button": "Offer transport support", "short": "Transport support"}
    if level == "High":
        if silent and d["distance_km"] < 10:
            return {"type": "call", "title": "Nurse phone call within 2 days",
                    "detail": "No replies to reminders, so a direct call is the quickest way to find out what is in the way.",
                    "button": "Call patient", "short": "Nurse call"}
        return {"type": "assign_visit", "title": "Assign a CHW home visit within 3 days",
                "detail": f"{d['chw']} covers {d['ward']} ward and can talk through transport options in person.",
                "button": f"Assign home visit ({chw})", "short": "CHW home visit"}
    if level == "Medium":
        if d["distance_km"] >= 8 or d["income_band"] == 1:
            return {"type": "offer_transport", "title": "Offer transport support if available",
                    "detail": "Distance and cost are the likely barriers. Check whether a transport voucher can be offered.",
                    "button": "Offer transport support", "short": "Transport support"}
        return {"type": "sms", "title": "Send a reminder and confirm the slot",
                "detail": "A short SMS and a quick confirmation is usually enough at this risk level.",
                "button": "Send SMS reminder", "short": "SMS + confirm slot"}
    return {"type": "sms", "title": "Routine reminder",
            "detail": "Standard reminders are enough. No extra outreach needed.",
            "button": "Send routine reminder", "short": "Routine reminder"}


def sms_texts(d: dict) -> dict:
    en = (f"Hello {d['first']}, this is a reminder about your appointment at Ruiru Hospital on "
          f"{d['appt_label']} at {d['appt_time']}. Reply YES to confirm or call 0712 345 678.")
    sw = (f"Habari {d['first']}, hii ni ukumbusho wa miadi yako katika Hospitali ya Ruiru "
          f"tarehe {d['appt'].day} {d['appt'].strftime('%b')} saa {d['appt_time']}. Jibu NDIO kuthibitisha au piga 0712 345 678.")
    seg = lambda t: 1 if len(t) <= 160 else math.ceil(len(t) / 153)
    return {"en": en, "sw": sw, "en_len": len(en), "sw_len": len(sw),
            "en_seg": seg(en), "sw_seg": seg(sw)}


def enrich(row, today: date, rs=None) -> dict:
    d = dict(row)
    pid, shift = d["patient_id"], d["shift_days"]
    d["code"] = f"CL-{pid:04d}"
    d["phone"] = f"+254 7{(pid * 37) % 100:02d} *** {(pid * 91) % 1000:03d}"
    d["chw"] = CHW[d["ward"]]
    d["first"] = d["name"].split()[0]
    d["initials"] = "".join(w[0] for w in d["name"].split()[:2])
    d["screened"] = sh(d["screened_on"], shift)
    d["referred"] = sh(d["referred_on"], shift)
    d["appt"] = sh(d["appointment_on"], shift)
    d["appt_time"] = TIMES[pid % len(TIMES)]
    d["rescheduled"], d["resched_reason"] = False, None
    if rs and d["referral_id"] in rs:  # the patient picked a new slot in the app
        r = rs[d["referral_id"]]
        d["appt"], d["appt_time"] = date.fromisoformat(r["new_date"]), r["new_time"]
        d["rescheduled"], d["resched_reason"] = True, r["reason"]
    d["screened_label"] = fmt(d["screened"], "%b %d, %Y")
    d["referred_label"] = fmt(d["referred"], "%b %d, %Y")
    d["appt_label"] = fmt(d["appt"], "%a %d %b")
    d["appt_short"] = fmt(d["appt"], "%b %d")
    d["days_since_ref"] = (today - d["referred"]).days
    d["days_to_appt"] = (d["appt"] - today).days
    d["reasons"] = json.loads(d["top_reasons"])
    d["pct"] = round(d["risk_score"] * 100)
    d["style"] = LEVEL_STYLE[d["risk_level"]]
    d["rec"] = recommend(d)
    return d


WORKLIST_SQL = """
SELECT w.referral_id, w.shift_days, w.status, p.patient_id, p.name, p.age, p.ward,
       p.distance_km, p.income_band, p.screening_type, p.screened_on,
       r.referred_on, r.appointment_on, r.prior_missed, r.reminders_sent,
       r.reminders_responded, s.risk_score, s.risk_level, s.top_reasons
FROM worklist w
JOIN referrals r USING (referral_id)
JOIN patients p USING (patient_id)
JOIN risk_scores s USING (referral_id)
"""


def load_worklist(db, today, status="open"):
    rows = db.execute(WORKLIST_SQL + " WHERE w.status = ? ORDER BY s.risk_score DESC", (status,)).fetchall()
    rs = resched_map(db)
    return [enrich(r, today, rs) for r in rows]


def filter_worklist(items, q="", ward="", screening="", risk=""):
    q = q.strip().lower()
    out = []
    for d in items:
        if q and q not in d["name"].lower() and q not in d["code"].lower():
            continue
        if ward and d["ward"] != ward:
            continue
        if screening and d["screening_type"] != screening:
            continue
        if risk and d["risk_level"] != risk:
            continue
        out.append(d)
    return out


def cascade(db):
    q = lambda s: db.execute(s).fetchone()[0] or 0
    screened = q("SELECT COUNT(*) FROM patients")
    referred = q("SELECT COUNT(*) FROM referrals")
    attended = q("SELECT SUM(attended) FROM referrals")
    treated = q("SELECT SUM(treatment_started) FROM referrals")
    return {
        "screened": screened, "referred": referred, "attended": attended, "treated": treated,
        "referral_rate": round(100 * referred / screened, 1),
        "attend_rate": round(100 * attended / referred, 1),
        "treat_rate": round(100 * treated / attended, 1),
        "overall_rate": round(100 * treated / referred, 1),
        "lost_before_visit": referred - attended,
        "lost_after_visit": attended - treated,
    }


# ---------------------------------------------------------------- pages
@app.get("/")
def home():
    return RedirectResponse("/dashboard")


@app.get("/dashboard")
def dashboard(request: Request):
    db = get_db()
    today = demo_today(db)
    items = load_worklist(db, today)
    casc = cascade(db)
    attrition = db.execute(
        """SELECT reschedule_reason AS reason, COUNT(*) AS n FROM referrals
           WHERE attended = 0 AND reschedule_reason != 'none' GROUP BY 1 ORDER BY n DESC""").fetchall()
    total_lost = sum(r["n"] for r in attrition) or 1
    attr = [{"label": REASON_LABELS.get(r["reason"], r["reason"]), "n": r["n"],
             "pct": round(100 * r["n"] / total_lost)} for r in attrition]
    try:
        with open(METRICS) as f:
            metrics = json.load(f)
    except FileNotFoundError:
        metrics = None
    counts = {lv: sum(1 for d in items if d["risk_level"] == lv) for lv in ("High", "Medium", "Low")}
    n = lambda sql: db.execute(sql).fetchone()[0]
    live = {
        "reschedules": n("SELECT COUNT(*) FROM reschedules"),
        "reminders": n("SELECT COUNT(*) FROM sms_log WHERE kind='reminder_new'"),
        "confirmed": n("SELECT COUNT(*) FROM sms_log WHERE kind='reply_new'"),
        "escalated": n("SELECT COUNT(*) FROM actions WHERE type='escalate'"),
        "attended": n("SELECT COUNT(*) FROM worklist WHERE status='attended'"),
        "reasons": [{"label": REASON_LABELS.get(r["reason"], r["reason"]), "n": r["c"]} for r in db.execute(
            "SELECT reason, COUNT(*) c FROM reschedules GROUP BY reason ORDER BY c DESC")],
    }
    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "dashboard", "facility": FACILITY, "coordinator": COORDINATOR,
        "today": fmt(today, "%b %d, %Y"), "casc": casc, "attr": attr, "metrics": metrics,
        "counts": counts, "open_total": len(items), "live": live,
        "month": today.strftime("%B %Y"),
    })


@app.get("/patients")
def patients(request: Request, q: str = "", ward: str = "", screening: str = "",
             risk: str = "", page: int = 1):
    db = get_db()
    today = demo_today(db)
    items = load_worklist(db, today)
    filtered = filter_worklist(items, q, ward, screening, risk)
    pages = max(1, math.ceil(len(filtered) / PAGE_SIZE))
    page = min(max(page, 1), pages)
    start = (page - 1) * PAGE_SIZE
    return templates.TemplateResponse(request, "patients.html", {
        "active": "patients", "facility": FACILITY, "coordinator": COORDINATOR,
        "rows": filtered[start:start + PAGE_SIZE], "top5": items[:5],
        "total": len(filtered), "start": start + 1 if filtered else 0,
        "end": min(start + PAGE_SIZE, len(filtered)), "page": page, "pages": pages,
        "wards": sorted(CHW.keys()), "q": q, "ward": ward, "screening": screening, "risk": risk,
        "month": today.strftime("%b %Y"),
    })


@app.get("/patients/{referral_id}")
def patient_detail(request: Request, referral_id: int):
    db = get_db()
    today = demo_today(db)
    row = db.execute(WORKLIST_SQL + " WHERE w.referral_id = ?", (referral_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Patient not found in the demo worklist")
    d = enrich(row, today, resched_map(db))
    d["sms"] = sms_texts(d)

    events = [{"when": datetime.combine(d["screened"], datetime.min.time()).replace(hour=11, minute=30),
               "title": "Abnormal screening result recorded", "icon": "clinical_notes", "tone": "ok",
               "text": f"{d['screening_type']} screening at the outreach clinic."},
              {"when": datetime.combine(d["referred"], datetime.min.time()).replace(hour=9),
               "title": "Referral to Ruiru Sub-County Hospital", "icon": "sync_alt", "tone": "ok",
               "text": f"Referred by {COORDINATOR.split(',')[0]}. Appointment booked for {d['appt_label']}."}]
    contacts = db.execute("SELECT * FROM contacts WHERE referral_id = ? ORDER BY sent_on", (referral_id,)).fetchall()
    for i, c in enumerate(contacts, 1):
        events.append({
            "when": datetime.combine(sh(c["sent_on"], d["shift_days"]), datetime.min.time()).replace(hour=8),
            "title": f"{'SMS' if c['channel'] == 'sms' else 'App'} reminder #{i} sent",
            "icon": "sms" if c["channel"] == "sms" else "notifications",
            "tone": "ok" if c["responded"] else "warn",
            "text": "Patient replied." if c["responded"] else "No reply recorded."})
    for r_ in db.execute("SELECT * FROM reschedules WHERE referral_id = ? ORDER BY id", (referral_id,)):
        nd = date.fromisoformat(r_["new_date"])
        events.append({"when": datetime.fromisoformat(r_["created_at"]), "title": "Patient asked to change the visit",
                       "icon": "event_repeat", "tone": "warn",
                       "text": f"Reason: {REASON_LABELS.get(r_['reason'], r_['reason'])}. New visit: {fmt(nd, '%a %d %b')}, {r_['new_time']}."})
    for m in db.execute("SELECT * FROM sms_log WHERE referral_id = ? AND kind IN ('reminder_new','reply_new')", (referral_id,)):
        events.append({"when": datetime.fromisoformat(m["created_at"]),
                       "title": "SMS reminder sent (simulated)" if m["kind"] == "reminder_new" else "Patient confirmed: will attend",
                       "icon": "sms" if m["kind"] == "reminder_new" else "thumb_up", "tone": "ok",
                       "text": m["text"] if m["kind"] == "reminder_new" else "Reply received: YES."})
    cons = db.execute("SELECT * FROM consents WHERE referral_id = ?", (referral_id,)).fetchone()
    if cons:
        events.append({"when": datetime.fromisoformat(cons["created_at"]), "title": "Patient updated privacy choices",
                       "icon": "lock", "tone": "ok",
                       "text": f"SMS {'on' if cons['sms'] else 'off'}, CHW sharing {'on' if cons['chw'] else 'off'}, research {'on' if cons['research'] else 'off'}."})
    for a in db.execute("SELECT * FROM actions WHERE referral_id = ? ORDER BY action_id", (referral_id,)):
        events.append({"when": datetime.fromisoformat(a["created_at"]), "title": a["message"],
                       "icon": "task_alt", "tone": "ok", "text": f"Logged by {COORDINATOR.split(',')[0]}."})
    events.sort(key=lambda e: e["when"], reverse=True)
    for e in events:
        e["label"] = fmt(e["when"], "%b %d, %Y") + (" • " + e["when"].strftime("%I:%M %p").lstrip("0") if e["when"].hour else "")

    max_pts = max([r["points"] for r in d["reasons"]], default=1) or 1
    for r in d["reasons"]:
        r["width"] = max(8, round(100 * r["points"] / max_pts))
    return templates.TemplateResponse(request, "patient_detail.html", {
        "active": "patients", "facility": FACILITY, "coordinator": COORDINATOR,
        "d": d, "events": events, "today": fmt(today, "%b %d, %Y"), "consent": get_consent(db, referral_id),
        "reason_label": REASON_LABELS.get(d["resched_reason"], ""),
        "dash": round(188.5 * (1 - d["risk_score"]), 1),
    })


# ---------------------------------------------------------------- JSON API
def public(d: dict) -> dict:
    keys = ["referral_id", "code", "name", "age", "ward", "screening_type", "risk_score",
            "risk_level", "reasons", "days_since_ref", "distance_km", "chw", "status"]
    out = {k: d[k] for k in keys}
    out["appointment"] = d["appt"].isoformat()
    out["recommended_action"] = d["rec"]["title"]
    return out


@app.get("/api/patients")
def api_patients(q: str = "", ward: str = "", screening: str = "", risk: str = ""):
    db = get_db()
    items = filter_worklist(load_worklist(db, demo_today(db)), q, ward, screening, risk)
    return {"count": len(items), "patients": [public(d) for d in items]}


@app.get("/api/patients/{referral_id}")
def api_patient(referral_id: int):
    db = get_db()
    row = db.execute(WORKLIST_SQL + " WHERE w.referral_id = ?", (referral_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    d = enrich(row, demo_today(db), resched_map(db))
    return {**public(d), "recommendation": d["rec"], "sms": sms_texts(d)}


@app.get("/api/cascade")
def api_cascade():
    return cascade(get_db())


class ActionIn(BaseModel):
    type: str
    language: str = "en"


ACTION_TEXT = {
    "assign_visit": "Home visit assigned to {chw}",
    "call": "Phone call logged",
    "offer_transport": "Transport support offered",
    "sms": "SMS reminder sent ({lang}) - simulated",
    "mark_attended": "Marked as attended",
    "escalate": "Escalated to {chw}: no reply to reminders",
}


@app.post("/api/patients/{referral_id}/actions")
def api_action(referral_id: int, body: ActionIn):
    if body.type not in ACTION_TEXT:
        raise HTTPException(400, "Unknown action")
    db = get_db()
    today = demo_today(db)
    row = db.execute(WORKLIST_SQL + " WHERE w.referral_id = ?", (referral_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    d = enrich(row, today, resched_map(db))
    lang = "Kiswahili" if body.language == "sw" else "English"
    message = ACTION_TEXT[body.type].format(chw=d["chw"], lang=lang)
    stamp = datetime.combine(today, datetime.now().time()).isoformat(timespec="seconds")
    db.execute("INSERT INTO actions (referral_id, type, message, created_at) VALUES (?,?,?,?)",
               (referral_id, body.type, message, stamp))
    if body.type == "mark_attended":
        db.execute("UPDATE worklist SET status='attended' WHERE referral_id=?", (referral_id,))
    db.commit()
    return {"ok": True, "message": message}


ensure_tables()


# ---------------------------------------------------------------- patient app (mobile)
def lang_of(request: Request) -> str:
    l = request.query_params.get("lang", "en")
    return l if l in T else "en"


def patient_ctx(request: Request, rid: int):
    db = get_db()
    today = demo_today(db)
    row = db.execute(WORKLIST_SQL + " WHERE w.referral_id = ?", (rid,)).fetchone()
    if not row:
        raise HTTPException(404, "Patient not found in the demo worklist")
    d = enrich(row, today, resched_map(db))
    lang = lang_of(request)
    return db, today, d, lang, T[lang]


def slot_options(d, today, t):
    seen, out = set(), []
    for off in (2, 3, 5, 8):
        dt = today + timedelta(days=off)
        while dt.weekday() >= 5 or dt in seen:
            dt += timedelta(days=1)
        seen.add(dt)
        i = len(out)
        tag = t["earliest"] if i == 0 else (t["this_week"] if dt.isocalendar()[1] == today.isocalendar()[1] else t["next_week"])
        out.append({"iso": dt.isoformat(), "date": f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}",
                    "time": TIMES[(d["patient_id"] + i) % len(TIMES)], "left": (d["patient_id"] + i * 3) % 7 + 2, "tag": tag})
    return out


def patient_page(request, rid, template, extra=None, active="journey"):
    db, today, d, lang, t = patient_ctx(request, rid)
    consent = get_consent(db, rid)
    confirmed = db.execute("SELECT COUNT(*) FROM sms_log WHERE referral_id=? AND kind='reply_new'", (rid,)).fetchone()[0] > 0
    ctx = {"d": d, "t": t, "lang": lang, "active": active, "consent": consent, "confirmed": confirmed,
           "rid": rid, "chw_first": d["chw"].split()[0], "base": f"/patient/{rid}",
           "attended": d["status"] == "attended"}
    ctx.update(extra(db, today, d, t) if extra else {})
    return templates.TemplateResponse(request, template, ctx)


@app.get("/patient/{rid}")
def patient_home(request: Request, rid: int):
    return patient_page(request, rid, "patient_home.html")


@app.get("/patient/{rid}/reschedule")
def patient_reschedule(request: Request, rid: int):
    def extra(db, today, d, t):
        return {"slots": slot_options(d, today, t), "reasons": RESCHEDULE_REASONS, "icons": REASON_ICONS}
    return patient_page(request, rid, "patient_reschedule.html", extra, "appt")


@app.get("/patient/{rid}/consent")
def patient_consent(request: Request, rid: int):
    return patient_page(request, rid, "patient_consent.html", None, "privacy")


@app.get("/patient/{rid}/messages")
def patient_messages(request: Request, rid: int):
    def extra(db, today, d, t):
        rows = db.execute("SELECT * FROM sms_log WHERE referral_id=? ORDER BY created_at, id", (rid,)).fetchall()
        msgs = []
        for r in rows:
            dt = datetime.fromisoformat(r["created_at"])
            msgs.append({"dir": r["direction"], "text": r["text"], "when": fmt(dt, "%b %d") + " " + dt.strftime("%I:%M %p").lstrip("0")})
        return {"msgs": msgs}
    return patient_page(request, rid, "patient_messages.html", extra, "msgs")


class RescheduleIn(BaseModel):
    reason: str
    date: str
    time: str


class ConsentIn(BaseModel):
    sms: bool
    chw: bool
    research: bool


@app.post("/api/patient/{rid}/reschedule")
def api_reschedule(rid: int, body: RescheduleIn):
    if body.reason not in RESCHEDULE_REASONS:
        raise HTTPException(400, "A reason is required")
    db = get_db()
    today = demo_today(db)
    if not db.execute("SELECT 1 FROM worklist WHERE referral_id=?", (rid,)).fetchone():
        raise HTTPException(404, "Not found")
    try:
        nd = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(400, "Bad date")
    if nd < today:
        raise HTTPException(400, "Date is in the past")
    db.execute("INSERT INTO reschedules (referral_id, reason, new_date, new_time, created_at) VALUES (?,?,?,?,?)",
               (rid, body.reason, body.date, body.time, stamp(today)))
    db.commit()
    return {"ok": True, "when": f"{nd.strftime('%a')} {nd.day} {nd.strftime('%b')}, {body.time}"}


@app.post("/api/patient/{rid}/consent")
def api_consent(rid: int, body: ConsentIn):
    db = get_db()
    if not db.execute("SELECT 1 FROM worklist WHERE referral_id=?", (rid,)).fetchone():
        raise HTTPException(404, "Not found")
    db.execute("INSERT OR REPLACE INTO consents VALUES (?,?,?,?,?)",
               (rid, int(body.sms), int(body.chw), int(body.research), stamp(demo_today(db))))
    db.commit()
    return {"ok": True}


@app.post("/api/patient/{rid}/confirm")
def api_confirm(rid: int):
    db = get_db()
    if not db.execute("SELECT 1 FROM worklist WHERE referral_id=?", (rid,)).fetchone():
        raise HTTPException(404, "Not found")
    db.execute("INSERT INTO sms_log (referral_id, direction, kind, text, status, created_at) VALUES (?,?,?,?,?,?)",
               (rid, "in", "reply_new", "YES", "Received", stamp(demo_today(db))))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- SMS outbox (simulated)
def outbox_state(db, today):
    items = load_worklist(db, today)
    out_ct = dict(db.execute("SELECT referral_id, COUNT(*) FROM sms_log WHERE direction='out' GROUP BY 1").fetchall())
    in_ct = dict(db.execute("SELECT referral_id, COUNT(*) FROM sms_log WHERE direction='in' GROUP BY 1").fetchall())
    escalated = {r[0] for r in db.execute("SELECT referral_id FROM actions WHERE type='escalate'")}
    sent_today = {r[0] for r in db.execute(
        "SELECT referral_id FROM sms_log WHERE direction='out' AND substr(created_at,1,10)=?", (today.isoformat(),))}
    due, skipped, escalate, escalate_blocked = [], [], [], []
    for d in items:
        rid = d["referral_id"]
        c = get_consent(db, rid)
        d["consent"] = c
        d["n_out"], d["n_in"] = out_ct.get(rid, 0), in_ct.get(rid, 0)
        if d["days_to_appt"] <= 3 and rid not in sent_today and d["n_in"] == 0:
            (due if c["sms"] else skipped).append(d)
        if d["n_out"] >= 2 and d["n_in"] == 0 and rid not in escalated and d["days_to_appt"] <= 10:
            (escalate if c["chw"] else escalate_blocked).append(d)
    return items, due, skipped, escalate, escalate_blocked


@app.get("/outbox")
def outbox(request: Request):
    db = get_db()
    today = demo_today(db)
    items, due, skipped, escalate, blocked = outbox_state(db, today)
    rows = db.execute("""SELECT l.*, p.name FROM sms_log l JOIN referrals r USING (referral_id)
                         JOIN patients p USING (patient_id) ORDER BY l.created_at DESC, l.id DESC LIMIT 25""").fetchall()
    log = []
    for r in rows:
        dt = datetime.fromisoformat(r["created_at"])
        log.append({**dict(r), "when": fmt(dt, "%b %d") + " " + dt.strftime("%I:%M %p").lstrip("0")})
    return templates.TemplateResponse(request, "outbox.html", {
        "active": "outbox", "facility": FACILITY, "coordinator": COORDINATOR,
        "due": due, "skipped": skipped, "escalate": escalate, "blocked": blocked, "log": log,
    })


@app.post("/api/outbox/send-due")
def api_send_due():
    db = get_db()
    today = demo_today(db)
    _, due, skipped, _, _ = outbox_state(db, today)
    for d in due:
        db.execute("INSERT INTO sms_log (referral_id, direction, kind, text, status, created_at) VALUES (?,?,?,?,?,?)",
                   (d["referral_id"], "out", "reminder_new", sms_texts(d)["en"], "Sent (simulated)", stamp(today)))
    db.commit()
    return {"sent": len(due), "skipped_no_consent": len(skipped)}


@app.post("/api/outbox/escalate")
def api_escalate():
    db = get_db()
    today = demo_today(db)
    _, _, _, esc, blocked = outbox_state(db, today)
    for d in esc:
        db.execute("INSERT INTO actions (referral_id, type, message, created_at) VALUES (?,?,?,?)",
                   (d["referral_id"], "escalate", ACTION_TEXT["escalate"].format(chw=d["chw"]), stamp(today)))
    db.commit()
    return {"escalated": len(esc), "blocked_no_consent": len(blocked)}
