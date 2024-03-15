#!/usr/bin/env python3
# freq_counter.py - quick summary utility for HFDL packets.
# copyright 2024 Kuupa Ork <kuupaork+github@ork.rodeo>
# see LICENSE for terms of use (TL;DR: BSD 3-clause)
"""
A very quick script that will troll through `dumphfdl` json logs, and provide counts of packets received in each
direction on each frequency from/to each ground station. Output is JSON only.
"""

import json
import pathlib

from collections import defaultdict

data_by_freq = {}
data_by_station = {}

def add_data(file_path):
    with open(file_path, 'r') as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                print(e)
                continue
            hfdl = data['hfdl']
            freq = hfdl['freq']
            pdu = hfdl.get("lpdu") or hfdl['spdu']
            src = pdu['src']
            dst = pdu.get('dst')
            bin_name = None
            if src['type'] == 'Ground station':
                station = f'{src["id"]}:{src["name"]}'
                bin_name = 'src'
            elif dst and dst['type'] == 'Ground station':
                station = f'{dst["id"]}:{dst["name"]}'
                bin_name = 'dst'
            else:
                print(pdu)

            if bin_name:
                bin = (data_by_freq
                    .setdefault(freq, {})
                    .setdefault(station, defaultdict(lambda: 0))
                )
                bin[bin_name] += 1
                bin = (data_by_station
                    .setdefault(station, {})
                    .setdefault(freq, defaultdict(lambda: 0))
                )
                bin[bin_name] += 1

if __name__ == '__main__':
    import sys
    for f in sys.argv[1:]:
        file_path = pathlib.Path(f)
        add_data(file_path)

    data = {"by_freq": data_by_freq, "by_station": data_by_station}
    print(json.dumps(data, indent=4))
