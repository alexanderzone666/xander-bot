"""
Daily random schedule generator - stdlib only (runs fast, no pip install needed).

Each calendar day gets its own random plan, deterministically seeded by the date,
so every hourly run of the workflow independently agrees on today's plan:

  Slot 1 (market)      -> random hour 7-11 UTC
  Slot 2 (story)       -> random hour 12-16 UTC
  Slot 3 (motivation)  -> random hour 17-22 UTC
  Bonus 4th post       -> ~30% of days, random unused hour 7-22, random type (2 or 3)

Plus a small random sleep (0-9 min; GitHub Actions adds its own natural 5-20 min delay on top, so real posting times vary organically) per post so timestamps look human (e.g. 9:23).

Usage (called by GitHub Actions every hour):
  python scheduler.py            -> prints "SLOT=<n> DELAY=<minutes>" or "SLOT=skip"
  python scheduler.py --show     -> prints today's full plan (for your curiosity)
"""

import sys
import random
import datetime as dt


def todays_plan(date=None):
    date = date or dt.datetime.now(dt.timezone.utc).date()
    rng = random.Random(date.toordinal() * 7919)  # deterministic per-day seed

    plan = {
        rng.randint(7, 11): (1, rng.randint(0, 9)),    # market
        rng.randint(12, 16): (2, rng.randint(0, 9)),   # story
        rng.randint(17, 22): (3, rng.randint(0, 9)),   # motivation
    }

    # ~30% of days: a 4th bonus post (story or motivation) at an unused hour
    if rng.random() < 0.30:
        free_hours = [h for h in range(7, 23) if h not in plan]
        bonus_hour = rng.choice(free_hours)
        bonus_slot = rng.choice([2, 3])
        plan[bonus_hour] = (bonus_slot, rng.randint(0, 9))

    return plan


def main():
    plan = todays_plan()

    if "--show" in sys.argv:
        print(f"Plan for {dt.datetime.now(dt.timezone.utc).date()} (UTC):")
        for hour in sorted(plan):
            slot, minute = plan[hour]
            names = {1: "market+chart", 2: "story+image", 3: "motivation"}
            print(f"  {hour:02d}:{minute:02d}  slot {slot} ({names[slot]})")
        return

    hour = dt.datetime.now(dt.timezone.utc).hour
    if hour in plan:
        slot, minute = plan[hour]
        print(f"SLOT={slot} DELAY={minute}")
    else:
        print("SLOT=skip")


if __name__ == "__main__":
    main()
