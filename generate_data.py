"""Synthetic data generator for CareLoop.

Simulates the cervical screening -> referral -> visit -> treatment cascade.
The 'attended' outcome is driven by realistic factors (distance, transport cost,
prior missed visits, reminder response, days since referral, age, income band),
so a model has genuine signal to learn. All data is synthetic.
"""
import os
import random
import sqlite3
from datetime import date, timedelta

import numpy as np

SEED = 42
N_SCREENED = 5000
rng = np.random.default_rng(SEED)
random.seed(SEED)

WARDS = ["Biashara", "Gitothua", "Mwihoko", "Mwiki", "Kahawa Sukari", "Ruiru Township"]
SCREEN_TYPES = ["VIA", "HPV", "Pap smear"]
REASONS = ["transport", "work_family", "anxiety", "other_clinic", "other", "none"]
FIRST = ["Wanjiku", "Akinyi", "Njeri", "Atieno", "Wambui", "Muthoni", "Nyambura",
         "Chebet", "Achieng", "Wangari", "Mumbi", "Naliaka", "Kemunto", "Moraa"]
LAST = ["Kamau", "Otieno", "Mwangi", "Njoroge", "Odhiambo", "Kariuki", "Wafula",
        "Nyaga", "Omondi", "Kimani", "Mutua", "Achola", "Gitau", "Cheruiyot"]


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def main():
    os.makedirs("data", exist_ok=True)
    path = "data/careloop.db"
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE patients (
            patient_id INTEGER PRIMARY KEY, name TEXT, age INTEGER, ward TEXT,
            distance_km REAL, income_band INTEGER, has_phone_app INTEGER,
            screening_type TEXT, screened_on TEXT, abnormal INTEGER);
        CREATE TABLE referrals (
            referral_id INTEGER PRIMARY KEY, patient_id INTEGER, referred_on TEXT,
            appointment_on TEXT, prior_missed INTEGER, reminders_sent INTEGER,
            reminders_responded INTEGER, reschedule_reason TEXT,
            attended INTEGER, treatment_started INTEGER);
        CREATE TABLE contacts (
            contact_id INTEGER PRIMARY KEY, referral_id INTEGER, channel TEXT,
            sent_on TEXT, responded INTEGER);
        """
    )

    today = date(2026, 11, 3)
    pid = 0
    rid = 0
    cid = 0
    for _ in range(N_SCREENED):
        pid += 1
        age = int(np.clip(rng.normal(39, 9), 25, 65))
        ward = random.choice(WARDS)
        distance = float(np.round(np.clip(rng.gamma(2.0, 3.0), 0.3, 30), 1))
        income = int(rng.choice([1, 2, 3], p=[0.45, 0.40, 0.15]))  # 1 = lowest
        app = int(rng.random() < (0.25 + 0.15 * (income - 1)))
        stype = random.choices(SCREEN_TYPES, weights=[0.55, 0.35, 0.10])[0]
        screened_on = today - timedelta(days=int(rng.integers(20, 180)))
        abnormal = int(rng.random() < 0.25)  # ~25% positivity

        db.execute(
            "INSERT INTO patients VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, f"{random.choice(FIRST)} {random.choice(LAST)}", age, ward,
             distance, income, app, stype, screened_on.isoformat(), abnormal),
        )
        if not abnormal:
            continue

        rid += 1
        referred_on = screened_on + timedelta(days=int(rng.integers(0, 5)))
        appt = referred_on + timedelta(days=int(rng.integers(7, 28)))
        prior_missed = int(rng.choice([0, 1, 2], p=[0.7, 0.22, 0.08]))
        reminders_sent = int(rng.integers(0, 4))
        responded = int(sum(rng.random() < (0.35 + 0.25 * app) for _ in range(reminders_sent)))
        days_wait = (appt - referred_on).days

        # True drop-off drivers (what the model should discover)
        logit = (-0.2
                 + 0.12 * distance
                 + 0.55 * prior_missed
                 + 0.04 * days_wait
                 - 0.45 * (income - 1)
                 - 0.55 * responded
                 + 0.02 * max(age - 45, 0)
                 + rng.normal(0, 0.5))
        p_miss = float(sigmoid(logit - 1.6))
        attended = int(rng.random() > p_miss)

        reason = "none"
        if not attended or rng.random() < 0.2:
            reason = random.choices(
                REASONS[:-1],
                weights=[0.35 + 0.02 * distance, 0.25, 0.15, 0.05, 0.20],
            )[0]

        treatment = int(attended and rng.random() < 0.87)
        db.execute(
            "INSERT INTO referrals VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, pid, referred_on.isoformat(), appt.isoformat(), prior_missed,
             reminders_sent, responded, reason, attended, treatment),
        )
        for k in range(reminders_sent):
            cid += 1
            ch = "app" if app and rng.random() < 0.5 else "sms"
            db.execute(
                "INSERT INTO contacts VALUES (?,?,?,?,?)",
                (cid, rid, ch, (referred_on + timedelta(days=k * 3 + 1)).isoformat(),
                 int(k < responded)),
            )

    db.commit()

    q = lambda s: db.execute(s).fetchone()[0]
    screened = q("SELECT COUNT(*) FROM patients")
    referred = q("SELECT COUNT(*) FROM referrals")
    attended = q("SELECT SUM(attended) FROM referrals")
    treated = q("SELECT SUM(treatment_started) FROM referrals")
    print(f"Screened:  {screened}")
    print(f"Referred:  {referred} ({referred / screened:.1%} of screened)")
    print(f"Attended:  {attended} ({attended / referred:.1%} of referred)")
    print(f"Treatment: {treated} ({treated / attended:.1%} of attended, "
          f"{treated / referred:.1%} of referred)")
    db.close()


if __name__ == "__main__":
    main()
