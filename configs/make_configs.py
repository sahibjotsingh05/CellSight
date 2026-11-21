"""Build the CONFIG sustained-fault CSVs from the supplied Normal operations data.

Each output repeats the normal readings for 30 minutes (180 frames, 10 s apart) and
applies one fault from the first frame, so every bus shows its scenario at all times
instead of only near the end of a six-minute file. The supplied data/ files are only read.
The utility gives each bus one CONFIG file by default and uses them for Simulate faults.

    python configs/make_configs.py
"""
import csv
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / 'data' / 'Programming - Competitors Dataset   - Normal operations .csv'
FRAMES = 180
START = datetime(2026, 1, 1, 12, 0, 0)


def low_voltage(row):
    if row['Module ID'] == 'M03':  # 3.28-3.31 V: below the 3.35 V critical limit
        row['Voltage (V)'] = f"{float(row['Voltage (V)']) - 0.385:.3f}"


def multiple_heating(row):
    if row['Module ID'] in ('M04', 'M05', 'M06'):  # 43-48 °C: above the 40 °C warning
        row['Temperature (C)'] = f"{float(row['Temperature (C)']) + 16:.2f}"


def thermal(row):
    if row['Module ID'] == 'M07':  # 60-65 °C: at or above the 60 °C immediate critical
        row['Temperature (C)'] = f"{float(row['Temperature (C)']) + 33:.2f}"


def sensor_failure(row):
    if row['Module ID'] == 'M11':
        row['Temperature (C)'] = row['Voltage (V)'] = ''
        row['Sensor Status'] = 'MISSING'


SCENARIOS = {'Normal operations': None, 'Low Voltage': low_voltage,
             'Multiple module heating': multiple_heating, 'Thermal rise': thermal,
             'Sensor failure': sensor_failure}


def main():
    with SOURCE.open(newline='') as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        rows = [{key.strip(): value for key, value in row.items()} for row in reader]
    stamps = sorted({row['Timestamp'] for row in rows})
    frames = [[row for row in rows if row['Timestamp'] == stamp] for stamp in stamps]
    for name, fault in SCENARIOS.items():
        path = ROOT / f'CONFIG - {name}.csv'
        with path.open('w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for i in range(FRAMES):
                stamp = (START + timedelta(seconds=10 * i)).strftime('%Y-%m-%d %H:%M:%S')
                for source in frames[i % len(frames)]:
                    row = dict(source, Timestamp=stamp)
                    if fault:
                        fault(row)
                    writer.writerow([row[key.strip()] for key in header])
        print(path.name)


if __name__ == '__main__':
    main()
