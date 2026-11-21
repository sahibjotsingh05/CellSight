from collections import defaultdict
from datetime import datetime
from pathlib import Path
import csv
import json
import statistics


def temperature_rate(points):
    times = [(time - points[0][0]).total_seconds() / 60 for time, _ in points]
    values = [value for _, value in points]
    mean_time = statistics.mean(times)
    mean_value = statistics.mean(values)
    return sum(
        (time - mean_time) * (value - mean_value)
        for time, value in zip(times, values)
    ) / sum((time - mean_time) ** 2 for time in times)


def inspect_file(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = [
            {key.strip(): value.strip() for key, value in row.items()}
            for row in csv.DictReader(stream)
        ]
    by_module = defaultdict(list)
    all_temperatures = []
    all_voltages = []
    missing = 0
    for row in rows:
        time = datetime.strptime(row["Timestamp"], "%Y-%m-%d %H:%M:%S")
        temperature = float(row["Temperature (C)"]) if row["Temperature (C)"] else None
        voltage = float(row["Voltage (V)"]) if row["Voltage (V)"] else None
        by_module[row["Module ID"]].append((time, temperature, voltage))
        if temperature is not None:
            all_temperatures.append(temperature)
        if voltage is not None:
            all_voltages.append(voltage)
        missing += temperature is None or voltage is None
    maximum_rate = float("-inf")
    first_warnings = {}
    first_critical = {}
    for module, points in by_module.items():
        points.sort()
        counts = defaultdict(int)
        for index, (time, temperature, voltage) in enumerate(points):
            window = points[max(0, index - 5):index + 1]
            valid_window = len(window) == 6 and all(value is not None for _, value, _ in window)
            rate = temperature_rate([(stamp, value) for stamp, value, _ in window]) if valid_window else None
            if rate is not None:
                maximum_rate = max(maximum_rate, rate)
            conditions = {
                "temperature_warning": temperature is not None and temperature >= 40,
                "temperature_critical": temperature is not None and temperature >= 55,
                "low_voltage_warning": voltage is not None and voltage <= 3.55,
                "low_voltage_critical": voltage is not None and voltage <= 3.35,
                "high_voltage_warning": voltage is not None and voltage >= 4.15,
                "high_voltage_critical": voltage is not None and voltage > 4.20,
                "rise_warning": rate is not None and rate >= 1.5,
                "hot_rise_critical": temperature is not None and temperature >= 50 and rate is not None and rate >= 3,
            }
            for name, active in conditions.items():
                counts[name] = counts[name] + 1 if active else 0
            warning = any(counts[name] >= 2 for name in ("temperature_warning", "low_voltage_warning", "high_voltage_warning")) or counts["rise_warning"] >= 3
            critical = any(counts[name] >= 2 for name in ("temperature_critical", "low_voltage_critical", "high_voltage_critical")) or counts["hot_rise_critical"] >= 3 or (temperature is not None and temperature >= 60)
            if warning or critical:
                first_warnings.setdefault(module, time.isoformat(sep=" "))
            if critical:
                first_critical.setdefault(module, time.isoformat(sep=" "))
    return {
        "source": path.name,
        "rows": len(rows),
        "temperature_c_range": [min(all_temperatures), max(all_temperatures)],
        "voltage_v_range": [min(all_voltages), max(all_voltages)],
        "maximum_six_sample_temperature_rate_c_per_min": round(maximum_rate, 6),
        "missing_temperature_or_voltage_rows": missing,
        "candidate_first_hazard_warning": first_warnings,
        "candidate_first_critical": first_critical,
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(json.dumps({
        "scope": "Read-only supplied-data profile and candidate activation-rule probe; not the application detector or a complete validation suite.",
        "files": [inspect_file(path) for path in sorted((root / "data").glob("*.csv"))],
    }, indent=2))
