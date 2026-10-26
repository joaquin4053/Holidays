import asyncio

from app import timing
from app.models import Usage


async def test_steps_land_in_the_tracked_trip_also_from_parallel_tasks():
    usage = Usage()
    timing.track(usage)

    async def work(label):
        with timing.step("live", label, cache=False) as t:
            await asyncio.sleep(0.01)
            t["items"] = 3

    await asyncio.gather(work("airbnb"), work("tripcom"))
    assert sorted(t["label"] for t in usage.timings) == ["airbnb", "tripcom"]
    assert all(t["seconds"] >= 0.01 and t["items"] == 3 and t["kind"] == "live" for t in usage.timings)


def test_step_without_tracked_trip_is_harmless():
    timing._current.set(None)
    with timing.step("prep", "x") as t:
        t["chars"] = 10
    assert t["seconds"] >= 0
