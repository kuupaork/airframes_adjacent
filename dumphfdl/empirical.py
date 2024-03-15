#!/usr/bin/env python3
# empirical.py - a frequency scanner for dumphfdl
# copyright 2024 Kuupa Ork <kuupaork+github@ork.rodeo>
# see LICENSE for terms of use (TL;DR: BSD 3-clause)
"""
A simple-ish script (that may no longer be working) to empirically scan HFDL frequency bands and acquire packet counts.
This can be used to pick CORE and FRINGE frequencies for `dumphfdl`, or for other amusing purposes.
"""

from collections import defaultdict, namedtuple

import json
import pathlib
import subprocess
import tempfile
import time

import click   # apt install python3-click

# The path to where the logs are stored. These are used later to compile some simple stats.
LOG_LOCATION = './'
# SYSTEM_TABLE_WRITABLE_LOCATION = './'
# set this if you want to share your packets with airframes during empirical scan.
STATION_NAME = None  # 'XX-XYZA0-HFDL'
# set this if you want to push stats to a statsd server during empirical scan
STATSD_SERVER = None  #'stats.lan:8125'

# bands are named for their MHz frequency
bands = {
    2: [2941, 2944, 2992, 2998, 3007, 3016, 3455, 3497, 3900],
    4: [4654, 4660, 4681, 4687],
    5: [5451, 5502, 5508, 5514, 5529, 5538, 5544, 5547, 5583, 5589, 5622, 5652, 5655, 5720],
    6: [6529, 6532, 6535, 6559, 6565, 6589, 6596, 6619, 6628, 6646, 6652, 6661, 6712],
    8: [8825, 8834, 8843, 8885, 8886, 8894, 8912, 8921, 8927, 8936, 8939, 8942, 8948, 8957, 8977],
    10: [10027, 10060, 10063, 10066, 10075, 10081, 10084, 10087, 10093],
    11: [11184, 11306, 11312, 11318, 11321, 11327, 11348, 11354, 11384, 11387],
    12: [12276],
    13: [13264, 13270, 13276, 13303, 13312, 13315, 13321, 13324, 13342, 13351, 13354],
    15: [15025],
    17: [17901, 17912, 17916, 17919, 17922, 17928, 17934, 17958, 17967, 17985],
    21: [21928, 21931, 21934, 21937, 21949, 21955, 21982, 21990, 21997],
}
bands_reverse = {}
for _band, _freqs in bands.items():
    for _freq in _freqs:
        bands_reverse[_freq] = _band


def populate_band_groups():
    band_groups = {}
    for start, end in [
        # Set these based on your maxmimum bandwidth (max Sample rate * 0.8) to group bands.
        # overlaps are okay. These are just groups. You set the ones you want to scan further down.
            [4,5],
            [6,8],
            [8,11],
            [8,13],
            [10,13],
            [10,15],
            [10,17],
            [11,17],
            [13,17],
            [15,17],
            [17,21],
        ]:
        name = f'{start}-{end}'
        freqs = []
        for band in range(start, end+1):
            freqs.extend(bands.get(band, []))
        band_groups[name] = freqs
        band_groups.update({ str(band): freqs for band, freqs in bands.items()})
    return band_groups

band_groups = populate_band_groups()
# this is where you select the band_groups defined above that you want to check, each string is the start and end
# (inclusive) frequency band to include.
test_groups = ["5-10", "10-15", "13-17", "17-21"]

SAMPLE_TIME = 120
# The amount of useful bandwidth inside a given sample size. The remainder is in the shoulders of the anti-aliasing
# bandpass filter and signals may be attenuated. Adjust for your radio and or sense of danger.
WINDOW_PASS = 0.825

# the sample rates supported by your SDR. These are for RSPdx, with 10M excluded, as it can be
# a bit buggy. Technically, the RSPdx supports arbitrary sample rates between 1M and 10M as well,
# but for the purposes of the sampler, we only use discrete advertised ones.
sample_rates = [
    62500, 96000, 125000, 192000, 250000, 384000, 500000, 768000, 
    1000000, 2000000, 2048000, 3000000, 4000000, 5000000, 6000000, 7000000, 8000000, 9000000
]
bandpasses = { WINDOW_PASS * rate: rate for rate in sample_rates }

MAXIMUM_SAMPLE_SIZE = max(sample_rates)

def get_sample_rate(freqs):
    """
    Biased for RSPdx, it picks the next largest sample window size from a known set of ones accepted by (my|your) SDR.
    for the RSPdx, it can use any arbitrary size between 2M and 10M, and this is used as a fallback. It tries to pick
    the smallest sample size without hitting the bandpass filter shoulders.
    """
    min_freq = min(freqs)
    max_freq = max(freqs)
    range = max_freq - min_freq
    bandpass_needed = range * 1000 / WINDOW_PASS
    found = sys.maxsize
    for b in bandpasses:
        if b > bandpass_needed:
            found = min(b, found)
    result = bandpasses.get(found, found)
    if result > 2_000_000:
        result = int(bandpass_needed)
        result += result % 1000
    return result


def command(band_name, log_file=None):
    """
    Generates the dumphfdl command args. This is not nearly as configurable as the main `dumbhfdl.py` script, so you
    will have to slog through this (or the declared globals) to configure this for your use. It's only here as an
    example.
    """
    freqs = band_groups[band_name]
    sample_rate = get_sample_rate(freqs)
    if sample_rate not in sample_rates and (2000000 > sample_rate) or (sample_rate > 10_000_000):
        raise ValueError(f"Needed Sample Rate not available ({sample_rate} for {band_name})")
    if log_file:
        local_log = ['--output', f'decoded:json:file:path={log_file}',]
    else:
        local_log = []
    if STATSD_SERVER:
        statsd_args = [
             '--statsd', STATSD_SERVER,
            '--noise-floor-stats-interval', '30',
        ]
    else:
        statsd_args = []
    if STATION_NAME:
        station_args = [
            '--station-id', STATION_NAME,
            '--output', 'decoded:json:tcp:address=feed.airframes.io,port=5556',
        ]
    else:
        station_args = []

    # Adjust these for your station. (This is a cheap hack without all the features of the maint ool)
    dump_cmd = [
        'dumphfdl',
        '--soapysdr', 'driver=sdrplay',
        '--antenna', 'Antenna B',
        '--device-settings', 'rfnotch_ctrl=true,dabnotch_ctrl=true,agc_setpoint=-30,biasT_ctrl=true',
        # '--gain-elements', 'IFGR=30,RFGR=0',  # autogain is fine
        # '--system-table', '/usr/local/share/dumphfdl/systable.conf',
        # '--system-table-save', f'{SYSTEM_TABLE_WRITABLE_LOCATION}/hfdl-systable-new.conf',
        '--sample-rate', str(sample_rate),
        '--output', f'decoded:json:file:path={LOG_LOCATION}/hfdl_json.log,rotate=daily',
        '--output', 'decoded:text:file:path=/dev/stdout',
    ] + station_args + local_log + statsd_args + [str(f) for f in freqs]

    return dump_cmd


def run_command(cmd, timeout=None):
    if timeout:
        cmd = ['timeout', f'{timeout}s'] + cmd
    try:
        result = subprocess.run(cmd)  # , capture_output=True)
        # print(result.stdout)
        # print(result.stderr)
        return result
    except KeyboardInterrupt:
        subprocess.run(['pkill', 'dumphfdl'])
        raise


def test_band(band_name, tempdirname):
    cmd = command(band_name, tempdirname)
    return run_command(cmd, SAMPLE_TIME)


def test_band_groups():
    results = {}
    with tempfile.TemporaryDirectory() as tempdirname:
        for name in test_groups:
            time.sleep(2)
            log_file = f'{tempdirname}/hfdl-{name}.log'
            test_band(name, log_file)
            time.sleep(2)
            counts = defaultdict(lambda: 0)
            try:
                log_text = pathlib.Path(log_file).read_text()
                for line in log_text.split('\n'):
                    try:
                        packet = json.loads(line)
                    except:
                        continue
                    hfdl_packet = packet['hfdl']
                    app_packet = hfdl_packet.get('lpdu', {}) or hfdl_packet.get('spdu', {})
                    source = app_packet.get('src', {}).get('type', None)
                    if source == 'Ground station':
                        counts['uplink'] += 1
                    elif source == 'Aircraft':
                        counts['downlink'] += 1
                    else:
                        counts['unknown'] += 1
            except FileNotFoundError:
                pass
            results[name] = counts
    return results


def best_band(results):
    maxima = None
    maxima_name = None
    for name, counts in results.items():
        if (
            not maxima or
            counts['downlink'] > maxima['downlink'] or
            ( counts['downlink'] == maxima['downlink'] and (
                counts['uplink'] > maxima['uplink'] or
                ( counts['uplink'] == maxima['uplink'] and counts['unknown'] > maxima['unknown'])
            ))):
            maxima_name = name
            maxima = counts
    if maxima_name:
        return maxima_name
    else:
        raise ValueError("No valid bands found")


def read_files(file_list):
    """
    Bin packet counts from a recent period (day? week?) by frequency band and hour of day.
    hour of day bin: ((hfdl.t.sec // 3600) % 24) [unix_ts]
    frequency_bin: bands_reverse[hfdl.freq // 1000]
    """
    hours = defaultdict(dict)
    for fn in file_list:
        fp = pathlib.Path(fn)
        lines = fp.read_text().split('\n')
        packets = []
        for line in lines:
            if line:
                try:
                    packets.append(json.loads(line))
                except json.decoder.JSONDecodeError:
                    print(f"junk packet: {line}")
        for packet in packets:
            utc_sec = int(packet['hfdl']['t']['sec'])
            freq = packet['hfdl']['freq'] // 1000
            sig = packet['hfdl']['sig_level']
            noise = packet['hfdl']['noise_level']
            hour_bin = (utc_sec // 3600) % 24
            band = bands_reverse[freq]
            band_stats = hours[hour_bin].setdefault(band, {'count': 0, 'snr': 0})
            avg_snr = band_stats['snr'] * band_stats['count']
            band_stats['count'] += 1
            band_stats['snr'] = (avg_snr + sig - noise) / band_stats['count']
    return hours


def compile_stats():
    home = pathlib.Path(LOG_LOCATION)
    files = home.glob('hfdl*json*.log')
    return read_files(files)


def print_stats(stats):
    print('\t', "\t".join(str(x) for x in bands.keys()))
    for hour in sorted(list(stats.keys())):
        hour_stats = [hour]
        for band in bands:
            hour_stats.append(stats[hour].get(band, {}).get('count', '-'))
        print("\t".join(str(x) for x in hour_stats))


def print_snr(stats):
    print('\t', "\t".join(str(x) for x in bands.keys()))
    for hour in sorted(list(stats.keys())):
        hour_stats = [hour]
        for band in bands:
            hour_stats.append(stats[hour].get(band, {}).get('snr', 0))
        print(f"{hour_stats[0]}\t" + "\t".join(f"{x:0.2f}" if x else '' for x in hour_stats[1:]))


def select_best():
    results = test_band_groups()
    s = {k : f'{v["downlink"]}/{v["uplink"]}/{v["unknown"]}' for k, v in results.items()}
    print(s)
    try:
        name = best_band(results)
    except ValueError:
        print("Best band = None")
        raise
    else:
        print(f'Best band = {name}')
    return name


@click.group()
def hfdl():
    pass


@hfdl.command()
def best():
    """Displays the best frequency set from a single empirical scan of available bands"""
    return select_best()


@hfdl.command()
def test():
    """Continuously cycles through frequency groups, building up packet logs for later analysis."""
    while True:
        try:
            select_best()
        except ValueError as e:
            print(e)


@hfdl.command()
def freqs():
    """Displays the frequency groups that the script can check"""
    print("\n".join(f"{name} -> {', '.join(str(f) for f in freqs)}" for name, freqs in band_groups.items()))


@hfdl.command()
def rates():
    """Displays the required sample size for each known frequency group"""
    for name, freqs in band_groups.items():
        found = get_sample_rate(freqs)
        if found > MAXIMUM_SAMPLE_SIZE:
            found = 'NOT POSSIBLE'
        print(f"band {name} requires {found} samples/second")


@hfdl.command()
def stats():
    """Shows a cheesy table binning packet counts by band and hour of day"""
    s = compile_stats()
    print_stats(s)
    # for h in sorted(s.keys()):
    #     print (f"{h}h = {json.dumps(s[h], indent=4)}")
    # print(json.dumps(compile_stats(), indent=4))

@hfdl.command()
def snr():
    """Shows a cheesy table with average SNR by band and hour of day"""
    s = compile_stats()
    print_snr(s)


if __name__ == '__main__':
    hfdl()
