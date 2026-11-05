"""Create the demo worklist: 48 synthetic patients with UPCOMING appointments.

The historical cohort already has outcomes (attended / missed). For the live demo we
need patients whose visit has not happened yet, so we pick 48 referrals (a mix of
High / Medium / Low risk), shift all of their dates so the appointment falls in the
next 2-20 days, and hide the outcome. Scores come from the out-of-fold model run.
Also creates the `meta` and `actions` tables used by the app.
"""
import os
import random
import sqlite3
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "careloop.db")
TODAY = date(2026, 11, 3)
QUOTA = {"High": 12, "Medium": 16, "Low": 20}


def main():
    random.seed(7)
    db = sqlite3.connect(DB)
    db.executescript(
        """
        DROP TABLE IF EXISTS worklist;
        DROP TABLE IF EXISTS meta;
        DROP TABLE IF EXISTS actions;
        DROP TABLE IF EXISTS reschedules;
        DROP TABLE IF EXISTS consents;
        DROP TABLE IF EXISTS sms_log;
        CREATE TABLE worklist (referral_id INTEGER PRIMARY KEY, shift_days INTEGER, status TEXT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE actions (action_id INTEGER PRIMARY KEY AUTOINCREMENT, referral_id INTEGER,
                              type TEXT, message TEXT, created_at TEXT);
        """
    )
    db.execute("INSERT INTO meta VALUES ('today', ?)", (TODAY.isoformat(),))

    rows = db.execute(
        """SELECT r.referral_id, r.referred_on, r.appointment_on, r.reminders_sent, s.risk_level
           FROM referrals r JOIN risk_scores s USING (referral_id)"""
    ).fetchall()
    pools = {k: [] for k in QUOTA}
    for rid, ref, appt, sent, level in rows:
        wait = (date.fromisoformat(appt) - date.fromisoformat(ref)).days
        # every reminder must already be in the past once the dates are shifted
        max_off = min(20, wait - 3 * max(sent - 1, 0) - 1)
        if max_off >= 2:
            pools[level].append((rid, appt, max_off))

    chosen = 0
    for level, n in QUOTA.items():
        for rid, appt, max_off in random.sample(pools[level], n):
            offset = random.randint(2, max_off)
            shift = (TODAY + timedelta(days=offset) - date.fromisoformat(appt)).days
            db.execute("INSERT INTO worklist VALUES (?,?, 'open')", (rid, shift))
            chosen += 1
    db.commit()
    print(f"Worklist created with {chosen} patients (as of {TODAY}).")
    for level in QUOTA:
        print(level, db.execute(
            "SELECT COUNT(*) FROM worklist w JOIN risk_scores s USING(referral_id) WHERE s.risk_level=?",
            (level,)).fetchone()[0])
    db.close()


if __name__ == "__main__":
    main()
