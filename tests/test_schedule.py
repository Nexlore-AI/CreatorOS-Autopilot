# SPDX-License-Identifier: AGPL-3.0-or-later
"""Slot arithmetic — and the regression for the bug that published a whole day at once.

The shell pipeline computed slot times with BSD `date -j`, falling back to `0` when the
command failed. On Linux that made every slot compare as already past, so every video took
the "publish now" branch. The first test here is the one that would have caught it.
"""
import datetime as dt

import pytest

from creatoros_autopilot.core import schedule as s

IST = "Asia/Kolkata"


class TestNeverPublishesEarly:
    """A slot in the future must schedule. There is no input that makes it publish now."""

    def test_future_slot_schedules(self):
        slot = s.slot_datetime(dt.date(2026, 11, 18), "09:00", IST)
        now = s.slot_datetime(dt.date(2026, 11, 17), "06:30", IST)
        decision = s.decide(slot, now, "auto")
        assert decision.mode == s.SCHEDULE
        assert decision.at == slot
        assert not decision.publishes_immediately

    @pytest.mark.parametrize("offset_days", [0, 1, 2, 7])
    @pytest.mark.parametrize("slot_hhmm", ["00:01", "09:00", "13:00", "23:59"])
    def test_no_future_slot_ever_decides_now(self, offset_days, slot_hhmm):
        """The A-4 regression, swept across offsets and slot times.

        The shell bug was not that one case was wrong — it was that *every* case
        collapsed to `now` once the timestamp parse failed. So this asserts the property,
        not an example.
        """
        run_date = dt.date(2026, 11, 17)
        now = s.slot_datetime(run_date, "06:30", IST)
        pub_date = s.publish_date(run_date, offset_days)
        slot = s.slot_datetime(pub_date, slot_hhmm, IST)
        decision = s.decide(slot, now, "auto")
        if slot > now:
            assert decision.mode == s.SCHEDULE, f"{slot} is after {now} but decided {decision.mode}"
        else:
            assert decision.mode == s.NOW

    def test_offset_actually_moves_the_date(self):
        """The other half of A-4: the offset silently became zero off macOS."""
        assert s.publish_date(dt.date(2026, 11, 17), 1) == dt.date(2026, 11, 18)
        assert s.publish_date(dt.date(2026, 11, 17), 0) == dt.date(2026, 11, 17)
        assert s.publish_date(dt.date(2026, 12, 31), 1) == dt.date(2027, 1, 1)

    def test_past_slot_publishes_now(self):
        slot = s.slot_datetime(dt.date(2026, 11, 17), "09:00", IST)
        decision = s.decide(slot, slot + dt.timedelta(hours=2), "auto")
        assert decision.mode == s.NOW

    def test_draft_mode_never_publishes(self):
        slot = s.slot_datetime(dt.date(2026, 11, 17), "09:00", IST)
        for now in (slot - dt.timedelta(days=1), slot + dt.timedelta(days=1)):
            assert s.decide(slot, now, "draft").mode == s.DRAFT

    def test_naive_datetimes_are_refused(self):
        """A naive datetime is how a timezone bug gets in. Refuse it rather than guess."""
        with pytest.raises(ValueError):
            s.decide(dt.datetime(2026, 11, 18, 9, 0), dt.datetime.now(dt.timezone.utc), "auto")
        with pytest.raises(ValueError):
            s.decide(s.slot_datetime(dt.date(2026, 11, 18), "09:00", IST),
                     dt.datetime(2026, 11, 17, 6, 30), "auto")

    def test_unknown_publish_mode_is_refused(self):
        slot = s.slot_datetime(dt.date(2026, 11, 18), "09:00", IST)
        with pytest.raises(ValueError):
            s.decide(slot, None, "publish")


class TestSlots:
    def test_parse_times_normalises(self):
        assert s.parse_times("9:00, 13:5 ,18:00") == ["09:00", "13:05", "18:00"]
        assert s.parse_times(["09:00", "21:00"]) == ["09:00", "21:00"]

    def test_parse_times_rejects_rubbish(self):
        for bad in ("", "nine", "09:00:00:00", [" "]):
            with pytest.raises(ValueError):
                s.parse_times(bad)

    def test_slot_index_clamps_to_last(self):
        """More videos than slots reuses the last one, as the shell's `${x:-last}` did."""
        times = ["09:00", "13:00"]
        assert s.slot_time(1, times) == "09:00"
        assert s.slot_time(2, times) == "13:00"
        assert s.slot_time(5, times) == "13:00"

    def test_slot_index_is_one_based(self):
        with pytest.raises(ValueError):
            s.slot_time(0, ["09:00"])


class TestTimezones:
    def test_slot_is_wall_clock_in_the_zone(self):
        slot = s.slot_datetime(dt.date(2026, 11, 18), "09:00", IST)
        assert (slot.hour, slot.minute) == (9, 0)
        assert slot.utcoffset() == dt.timedelta(hours=5, minutes=30)

    def test_offset_is_resolved_per_date_not_once(self):
        """In a DST zone the same wall time has different offsets across the year."""
        winter = s.slot_datetime(dt.date(2026, 1, 15), "09:00", "America/New_York")
        summer = s.slot_datetime(dt.date(2026, 7, 15), "09:00", "America/New_York")
        assert winter.utcoffset() == dt.timedelta(hours=-5)
        assert summer.utcoffset() == dt.timedelta(hours=-4)
        assert winter.hour == summer.hour == 9

    def test_unknown_zone_names_the_windows_fix(self):
        with pytest.raises(ValueError, match="tzdata"):
            s.zone("Mars/Olympus_Mons")

    def test_isoformat_is_naive_wall_time(self):
        """Zernio takes the timestamp and the zone separately; an offset double-applies it."""
        slot = s.slot_datetime(dt.date(2026, 11, 18), "09:00", IST)
        assert s.isoformat(slot) == "2026-11-18T09:00:00"
        assert "+" not in s.isoformat(slot)


class TestDueGate:
    def test_due_only_after_the_run_time(self):
        before = dt.datetime(2026, 11, 17, 0, 0, tzinfo=dt.timezone.utc)   # 05:30 IST
        after = dt.datetime(2026, 11, 17, 2, 0, tzinfo=dt.timezone.utc)    # 07:30 IST
        assert not s.is_due("06:30", IST, before)
        assert s.is_due("06:30", IST, after)

    def test_gate_uses_the_configured_zone_not_the_machine(self):
        """The shell compared the machine clock to the run hour while deriving the date in
        IST. They only agree when the machine sits in that zone."""
        moment = dt.datetime(2026, 11, 17, 2, 0, tzinfo=dt.timezone.utc)
        assert s.is_due("06:30", IST, moment)          # 07:30 IST — due
        assert not s.is_due("06:30", "UTC", moment)    # 02:00 UTC — not due
