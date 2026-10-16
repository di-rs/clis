"""Bounded first/latest and sampled extrema for repeated native progress probes."""

import json
import math
from typing import Any


def bounded(value: Any, limit: int) -> Any:
    data = json.dumps(value, allow_nan=False, indent=2, sort_keys=True)
    if len(data.encode()) > limit:
        raise RuntimeError("progress evidence section exceeded bound")
    return json.loads(data)


def natural(value: Any) -> int:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    if type(value) is int and value >= 0:
        return int(value)
    raise RuntimeError("invalid resource counter")


def memory_peak(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    cap = current["memory.max"]
    if cap != "max":
        natural(cap)
    if previous and previous["memory_max"] != cap:
        raise RuntimeError("resource cap changed between observations")
    events: dict[str, int] = {}
    for line in current["memory.events"].splitlines():
        name, value = line.split()
        if (
            name
            not in {"low", "high", "max", "oom", "oom_kill", "oom_group_kill", "sock_throttled"}
            or name in events
        ):
            raise RuntimeError("invalid memory event counter")
        events[name] = natural(value)
    if not {"oom", "oom_kill"} <= events.keys():
        raise RuntimeError("missing OOM counters")
    older = previous.get("memory_events_max", {})
    if previous and older.keys() != events.keys():
        raise RuntimeError("memory counter set changed")
    return {
        "memory_max": cap,
        "memory_current_max": max(
            natural(current["memory.current"]), previous.get("memory_current_max", 0)
        ),
        "memory_events_max": {
            name: max(value, older.get(name, value)) for name, value in events.items()
        },
    }


def record_progress(
    observations: dict[str, Any], progress: dict[str, Any], resources: dict[str, Any]
) -> None:
    # Build replacements privately so failed bounds/validation leave prior proof intact.
    current = bounded(resources, 32768)
    probe = bounded(progress, 2048)
    if (
        set(probe) != {"http_seconds", "roundtrip_seconds", "terminal_marker_observed"}
        or probe["terminal_marker_observed"] is not True
    ):
        raise RuntimeError("invalid application progress observation")
    for field in ("http_seconds", "roundtrip_seconds"):
        value = probe[field]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
        ):
            raise RuntimeError("invalid application progress timing")
    old_resources = observations.get("resources", {})
    peaks = bounded(
        old_resources.get("sampled_peaks", {"containers": {}, "systemd": {}, "filesystems": {}}),
        32768,
    )
    seen: set[str] = set()
    for item in current["containers"]:
        identifier = item["id"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise RuntimeError("invalid resource identity")
        seen.add(identifier)
        prior = peaks["containers"].get(identifier, {})
        if prior and (prior["name"] != item["name"] or prior["driver"] != item["driver"]):
            raise RuntimeError("resource identity configuration changed")
        if type(item["oom_killed"]) is not bool:
            raise RuntimeError("invalid resource OOM observation")
        updated = dict(
            prior,
            name=item["name"],
            driver=item["driver"],
            oom_killed_observed=item["oom_killed"] or prior.get("oom_killed_observed", False),
        )
        if item["memory"] is None:
            transition = item["transition"]
            before, after = transition["before"], transition["after"]
            if before["Id"] != after["Id"] or not (
                after["Restarting"]
                or any(
                    before[key] != after[key]
                    for key in ("Pid", "StartedAt", "RestartCount", "Running", "Restarting")
                )
            ):
                raise RuntimeError("unverified missing resource sample")
            updated.update(
                unavailable_samples=prior.get("unavailable_samples", 0) + 1,
                latest_transition=transition,
            )
        else:
            updated.update(memory_peak(prior if "memory_max" in prior else {}, item["memory"]))
            updated["pids_current_max"] = max(
                natural(item["memory"]["pids.current"]), prior.get("pids_current_max", 0)
            )
        peaks["containers"][identifier] = updated
    for unit, memory in current["systemd"].items():
        peaks["systemd"][unit] = memory_peak(peaks["systemd"].get(unit, {}), memory)
    for path, usage in current["filesystems"].items():
        size, free = natural(usage["bytes"]), natural(usage["free"])
        prior = peaks["filesystems"].get(path, {})
        if free > size or (prior and prior["bytes"] != size):
            raise RuntimeError("filesystem capacity changed between observations")
        peaks["filesystems"][path] = {
            "bytes": size,
            "free_min": min(free, prior.get("free_min", free)),
        }
    peaks["driver_max_rss_kib"] = max(
        natural(current["driver"]["max_rss_kib"]), peaks.get("driver_max_rss_kib", 0)
    )
    bounded(peaks, 32768)
    old_progress = observations.get("progress", {})
    summary = {
        "count": old_progress.get("count", 0) + 1,
        "first": old_progress.get("first", probe),
        "latest": probe,
        "http_seconds_max": max(probe["http_seconds"], old_progress.get("http_seconds_max", 0)),
        "roundtrip_seconds_max": max(
            probe["roundtrip_seconds"], old_progress.get("roundtrip_seconds_max", 0)
        ),
    }
    observations.update(
        progress=summary,
        resources={
            "first": old_resources.get("first", current),
            "latest": current,
            "sampled_peaks": peaks,
        },
    )
