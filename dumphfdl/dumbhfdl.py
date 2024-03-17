#!/usr/bin/env python3
# dumbhfdl.py - a frequency-picking harness for dumphfdl.
# copyright 2024 Kuupa Ork <kuupaork+github@ork.rodeo>
# see LICENSE for terms of use (TL;DR: BSD 3-clause)

# system libraries
import asyncio.subprocess
import bisect
import datetime
import functools
import itertools
import json
import logging
import os
import pathlib
import re
import signal
import sys
import threading
import time
# third party
import click        # apt install python3-click or `pip install click` or https://pypi.org/project/click/
import requests     # apt install python3-requests or `pip install requests` or https://pypi.org/project/requests/

# all frequencies/bandwidths are in kHz
import fallback


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(sys.argv[0].rsplit('/', 1)[-1].rsplit('.', 1)[0] if __name__ == '__main__' else __name__)

# The maximum practical kSamples/sec to accept. For my RSPdx, the technical limit is 10000 (10MS/s) but practical
# experimentation shows that using this rate causes occasional data streaming errors, so I back it off a little bit
# Set with `--max-samples` on command line or environment variable `DUMPHFDL_MAX_SAMPLES`
MAXIMUM_SAMPLE_SIZE = 9250

# A naive factor to apply to the Sample Size to account for an aliasing filter the radio may use.
FILTER_FACTOR = 0.8

# The URL to retrieve Ground Station from
GROUND_STATION_URL = 'https://api.airframes.io/hfdl/ground-stations'
# How long to cache ground station updates (seconds)
GS_EXPIRY = 6 * 3600
#
# How often (seconds) to update the frequency list (and probably restart dumphfdl)
# Set with `--watch-interval` on command line or environment variable `DUMPHFDL_WATCH_INTERVAL`)
WATCH_INTERVAL = 600
#
# Cooldown between stopping dumphfdl and starting it again. SDRPlay radios need something like this. (seconds)
# Set with `--sdr-settle` on command line or environment variable `DUMPHFDL_SDR_SETTLE`)
SDR_SETTLE_TIME = 5


# if FILL_OTHER_STATIONS is True, the chooser will add any frequencies from other stations
# that fit in the allowed bandwidth, but the core stations determine where the watch group lies.
# Set this to False if you are concerned about CPU usage.
# DST (no SRC for any of these): NZ: 69, Bahrain: 12, Korea: 2, Guam: 6. All others nil.
# you can set this to False by passing `--skip-fill` on the command line
FILL_OTHER_STATIONS = True


# Try some other picking strategies for different results. Can be invoked by using `--experimental`. Does not effect
# choices made to actually run listener.
EXPERIMENTAL = False

# dumphfdl config
# base path for files to be written (if any)
DUMB_SHARE_PATH = pathlib.Path.home() / '.local/share/dumbhfdl/'
# The system location of the systable.conf list of names. Can be set to None, in which case lookup will not be done.
# Can be set using `--system_table` option on command line or the `DUMPHFDL_SYSTABLE` environment variable
SYSTABLE_LOCATION = '/usr/local/share/dumphfdl/systable.conf'
# The location where an updated systable.conf will be written. If None, no updates will be saved.
# Can be set using `--system_table-save` option on command line 
# or the `DUMPHFDL_SYSTABLE_UPDATES` environment variable
SYSTABLE_UPDATES_PATH = f'{DUMB_SHARE_PATH}/hfdl-systable-new.conf'
# The default location where logs will be written. If None, no packet logs will be kept. 
# Override with `--log-path` option or the `DUMPHFDL_LOG_PATH` environment variable.
LOG_PATH = DUMB_SHARE_PATH / "logs"


def bandwidth_for_interval(interval):
    return interval[-1] - interval[0]


class GroundStationWatcher:
    core_ids = []
    fringe_ids = []
    skip_fill = False
    watch_interval = 600
    _max_sample_size = 20000
    _sample_rates = None

    def __init__(self, ground_stations, on_update=None):
        self.ground_stations = ground_stations
        self.active_stations = {}
        self.task = None
        self.on_update = on_update
        self.last = []

    def set_ignore_ranges(self, data):
        if data.startswith('['):
            try:
                self.ignore_ranges = json.loads(data)
            except json.JSONDecodeError:
                pass
        elif '-' in data:
            self.ignore_ranges = [(int(s),int(e)) for s, e in (r.split('-') for r in data.split(','))]
        elif not data:
            self.ignore_ranges = []
        else:
            raise ValueError(f'unsupported ignored ranges {data}')

    async def run(self):
        while True:
            logger.info("refreshing")
            self.refresh()
            await asyncio.sleep(self.watch_interval)

    def stop(self):
        if self.task:
            self.task.cancel()
            self.task = None

    def start(self):
        self.task = loop.create_task(self.run())
        return self.task

    def remote(self, url):
        data = {}
        if url:
            response = requests.get(url)
            try: 
                data = response.json()
            except json.JSONDecodeError:
                pass
        return data

    def parse_active_stations(self, data):
        active_stations = {}
        for station in data.get("ground_stations", []):
            name = station["name"]
            sid = str(station['id'])
            active_stations[name] = sorted(map(int, station['frequencies'].get("active", [])))
            active_stations[sid] = active_stations[name]
        return active_stations

    def refresh(self):
        sources = [
            ('Airframes Ground Station URL', lambda: self.remote(GROUND_STATION_URL)),
            ('Cached Squitter Data', lambda: self.ground_stations.pruned_dict()),
            ('Backup Ground Station URL', lambda: self.remote(os.getenv('DUMPHFDL_BACKUP_URL'))),
            ('All Allocated Frequencies', lambda: fallback.ALL_FREQUENCIES),
        ]
        logger.info('refreshing ground station data')
        for name, source in sources:
            ground_station_data = source()
            parsed = self.parse_active_stations(ground_station_data) if ground_station_data else {}
            if all(len(parsed.get(core_id, '')) > 0 for core_id in self.core_ids):
                logger.info(f'Using {name}')
                return self.update_active_stations(parsed)
            else:
                logger.info(f'Cannot update from {name}')
        else:
            raise ValueError('No frequency sources are valid')

    def reconcile_samples(self):
        max_window = 30000  # we will not need a sample size larger than this!
        if self._sample_rates:
            max_window = self._sample_rates[-1] // 1000
        self.maximum_bandwidth = min(max_window, self._max_sample_size) * FILTER_FACTOR

    @property
    def max_sample_size(self):
        return self._max_sample_size

    @max_sample_size.setter
    def max_sample_size(self, new_value):
        self._max_sample_size = int(new_value)
        self.reconcile_samples()

    @property
    def sample_rates(self):
        return self._sample_rates

    @sample_rates.setter
    def sample_rates(self, windows):
        self._sample_rates = windows
        self.reconcile_samples()

    def update_active_stations(self, data):
        self.active_stations = data
        best_pool = self.best_pool()
        best_frequencies = list(best_pool)
        if best_frequencies != self.last:
            self.last = best_frequencies
            if callable(self.on_update):
                self.on_update(best_frequencies)
        else:
            logger.info("frequencies unchanged")
        return best_frequencies

    def create_pool(self, seed=None):
        return FrequencyPool(seed=seed, ignored_ranges=self.ignore_ranges, maximum_bandwidth=self.maximum_bandwidth)

    def best_pool(self):
        """
        Builds a pool of the "best" frequencies fitting within the identified maximum bandwidth.
        1. For each active station in the core station list
            - skip ignored frequency ranges
            - add the frequency if the pool can be expanded to cover a frequency and remain in bandwidth limits.
        2. Step 1 is performed twice, once with ascending frequency lists, and once with descending.
        3. The core pool from (2) with the most frequencies wins (ties resolve in favour of the ascending (low) freqs)
        4. Fringe stations' active frequencies may then be added in a similar manner. (Can be disabled)
        5. Finally, any other stations' active frequencies may then be added. (Can be disabled)

        This mechanism has a few caveats:
        - 21 MHz is somewhat selected against, as its distance from other bands reduces the other bands that may 
          also be covered.
        - If low frequency bands are considered, they may skew against the middle bands (10, 11, 13MHz). At my station,
          there's a lot of noise and not much signal below 6MHz, so I've marked that range as ignored and it works well
        """
        # build core range
        core_stations = [self.active_stations.get(n, []) for n in self.core_ids]
        low_pool = self.create_pool()
        low_pool.add_stations(core_stations, pivot=0)
        low = list(low_pool)

        high_pool = self.create_pool()
        high_pool.add_stations(core_stations, pivot=-1)
        high = list(high_pool)
        actual_pool = high_pool if len(high_pool) > len(low_pool) else low_pool
        if EXPERIMENTAL:
            logger.info("low pool:", low)
            logger.info("high pool:", high)

        # Fringe stations don't determine pool range, but fill in frequencies more likely to be heard
        # actual = build_freq_list(FRINGE_STATIONS, pool=actual)
        # don't need pivot here.
        actual_pool.add_stations([self.active_stations.get(n, []) for n in self.fringe_ids])

        # now fill in the others "just in case"
        if not self.skip_fill:
            # a bit wasteful, but these are all small enough that completely readding everything won't hurt.
            actual_pool.add_stations(self.active_stations.values())
        return actual_pool

    def experimental_pools(self):
        # experimental pools can be generated with the `--experimental` flag. They currently never are used (though
        # the results may duplicate the high or low pools above.
        def experimental_middle_pool():
            # "middle pool" works a bit differently to the high and low pools above. Instead of ranking the Core
            # stations, it combines all their frequencies into a single group. It takes the middle entry from the
            # list and works outwards from it until the bandwidth is filled.
            core_stations = [self.active_stations.get(n, []) for n in self.core_ids]
            core_freqs = sorted(itertools.chain(*core_stations))
            middle_pool = self.create_pool()
            middle_pool.extend(core_freqs)
            return middle_pool

        def experimental_iterate_core():
            # "iterate core" is similar to "middle pool", but instead of picking just one frequency to build out from,
            # each "core" frequency is tried in turn, building a pool outward from it. The unique pools generated by
            # this mechanism are yielded.
            core_station_active = [self.active_stations.get(name, []) for name in self.core_ids]
            core_freqs = sorted(itertools.chain(*core_station_active))
            seen = []
            for ix in range(0, len(core_freqs)):
                pool = self.create_pool()
                pool.extend(core_freqs, pivot=ix)
                freqs = list(pool)
                if freqs not in seen:
                    seen.append(freqs)
                    yield pool

        if EXPERIMENTAL:
            middle_pool = experimental_middle_pool()
            logger.info("[experimental] middle pool:", list(middle_pool), ('(unranked)'))
            logger.info('[experimental] iterate-core pools:')
            for pool in experimental_iterate_core():
                logger.info('    ', list(pool))
            logger.info('===')


class GroundStations:
    path = None

    def __init__(self, path=None):
        self.stations_by_id = {}
        self.name_lookup = {}
        self.last = None
        if (path):
            self.path = pathlib.Path(path)
            self.load()

    def load(self):
        if self.path and self.path.exists():
            s = self.path.read_text()
            if s:
                self.last = s
                self.merge_airframes(json.loads(s))
                self.prune_expired()

    def save(self):
        if self.path:
            current = json.dumps(self.dict(), indent=4)
            if current != self.last:  # very naive
                logger.info('saving station cache')
                self.path.write_text(current)
                self.last = current

    def update_lookups(self):
        self.name_lookup = {gs['name']: gs for gs in self.stations_by_id.values()}

    def merge_station(self, station):
        try:
            gs = self[station['id']]
        except KeyError:
            self.stations_by_id[station['id']] = station
        else:
            if gs['last_updated'] < station['last_updated']:
                gs.update(station)
        self.update_lookups()

    def prune_expired(self):
        now = datetime.datetime.now().timestamp()
        horizon = now - GS_EXPIRY
        for gs in list(self.stations_by_id.values()):
            if gs['last_updated'] < horizon:
                logger.info(f'pruning {gs["id"]} ({horizon} > {gs["last_updated"]})')
                del self.stations_by_id[gs['id']]
        self.update_lookups()

    def merge_squitter(self, squitter):
        base = squitter.get('hfdl', {}).get('spdu', {}).get('gs_status', [])
        last_updated = squitter.get('hfdl', {}).get('t', {}).get('sec', 0)
        ground_stations = []
        for station in base:
            freqs = sorted(map(int, (sf['freq'] for sf in station['freqs'])))
            gs = {
                'id': station['gs']['id'],
                'name': station['gs']['name'],
                'frequencies': {
                    'active': freqs,
                },
                'last_updated': last_updated
            }
            self.merge_station(gs)
        self.prune_expired()
        self.save()

    def merge_airframes(self, airframes):
        for gs in airframes.get('ground_stations', []):
            gs['frequencies']['active'] = sorted(map(int, gs['frequencies'].get("active", [])))
            self.merge_station(gs)
        self.prune_expired()
        self.save()

    def dict(self):
        return {'ground_stations': list(self.stations_by_id.values())}

    def pruned_dict(self):
        self.prune_expired()
        return self.dict()

    def __getitem__(self, key):
        try:
            gsid = int(key)
        except ValueError:
            gsid = key
            lookup = self.stations_by_name
        else:
            lookup = self.stations_by_id
        return lookup[key]

    def __contains__(self, key):
        try:
            return int(key) in self.stations_by_id
        except ValueError:
            pass
        return key in self.name_lookup


class SquitterWatcher:
    def __init__(self, log_file, on_update=None):
        self.log_file = pathlib.Path(log_file)
        self.on_update = on_update or self.default_update
        self.enabled = False

    async def run(self):
        self.enabled = True
        while self.enabled and not self.log_file.exists():
            await asyncio.sleep(1)
        if not self.enabled:
            return
        with open(self.log_file) as log:
            log.seek(0, 2)
            while self.enabled:
                line = log.readline()
                if not line:
                    await asyncio.sleep(1)
                    continue
                if '"gs_status"' in line:
                    data = json.loads(line)
                    self.on_update(data)

    def default_update(self, update):
        logger.info(update)


def balancing_iter(sources, targets=None, pivot=None):
    if not targets:
        targets = sources
    if pivot is None:
        pivot = len(targets) // 2

    if targets and pivot < len(targets):
        pivot_freq = targets[pivot]
        source_pivot = bisect.bisect_left(sources, pivot_freq)
    elif pivot == -1:
        source_pivot = -1
    else:
        source_pivot = 0

    low = list(reversed(sources[0:source_pivot]))
    high = sources[source_pivot:]
    if not targets:
        zipper = []
    elif (pivot % len(targets)) >= len(targets) // 2:
        zipper = itertools.zip_longest(high, low)
    else:
        zipper = itertools.zip_longest(low, high)
    for next_freqs in zipper:
        yield from next_freqs


class FrequencyPool:
    def __init__(self, seed=None, ignored_ranges=None, maximum_bandwidth=None):
        self.frequencies = list(seed) if seed else []
        self.ignored_ranges = ignored_ranges or []
        self.maximum_bandwidth = maximum_bandwidth or 2000
        self.filters = [
            self.filter_none,
            self.filter_duplicates,
            self.filter_ignored_ranges,
            self.filter_bandwidth,
        ]

    def add(self, frequency):
        for filter in self.filters:
            if not filter(frequency):
                break
        else:
            bisect.insort(self.frequencies, frequency)
        return frequency in self.frequencies

    def extend(self, frequencies, pivot=None):
        for frequency in balancing_iter(frequencies, self.frequencies, pivot):
            self.add(frequency)

    def filter_none(self, frequency):
        return frequency is not None

    def filter_duplicates(self, frequency):
        return frequency not in self.frequencies

    def filter_ignored_ranges(self, frequency):
        for interval_start, interval_end in self.ignored_ranges:
            if interval_start <= frequency <= interval_end:
                return False
        return True

    def filter_bandwidth(self, frequency):
        return self.can_cover_bandwidth(frequency, self.frequencies)

    def __iter__(self):
        yield from self.frequencies

    def __len__(self):
        return len(self.frequencies)

    def add_stations(self, stations, pivot=None):
        for station in stations:
            self.extend(station, pivot)

    def can_cover_bandwidth(self, frequency, others):
        return (
                not others
                or others[0] <= frequency <= others[-1]
                or (others[0] > frequency and others[-1] - frequency < self.maximum_bandwidth)
                or (others[-1] < frequency and frequency - others[0] < self.maximum_bandwidth)
            )


def sample_rate_for(sample_size, sample_rates):
    sample_size *= 1000
    if not sample_rates:
        return sample_size
    current = 0
    ix = bisect.bisect_right(sample_rates, sample_size)
    if ix >= len(sample_rates):
        raise ValueError(f'cannot fulfill desired sample rate: {sample_size} from {sample_rates}')
    return sample_rates[ix]


class HFDLListener:
    # Set this to your airframes.io station name.
    # Usual format is "<2-initals>-<nearest ICAO airport code><index>-HFDL"
    # Can be passed as the environment variable `DUMPHFDL_STATION_ID`
    station_id = None
    # The --antenna parameter for `dumphfdl`. Can be passed as environment variable `DUMPHFDL_ANTENNA`
    antenna = None
    # If you use a statsd server, set this to "<host>:<port>". As `--statsd` param, or `DUMPHFDL_STATSD` env var.
    statsd_server = None
    # --quiet flag will suppress logging of packets to stdout
    quiet = False
    system_table = None
    system_table_save = None
    log_path = None
    sdr_settle = 5
    soapysdr = None
    device_settings = None
    acars_hub = None
    ground_stations = None
    ground_station_updater = None
    ground_station_log = pathlib.Path(f'{DUMB_SHARE_PATH}') / 'current.log'
    sample_rates = None

    def __init__(self, ground_stations, sample_rates):
        self.process = None
        self.ground_stations = ground_stations
        self.sample_rates = sample_rates

    def command(self, frequencies):
        #
        # If you use a different configuration, you'll have to adjust these to match your system.
        sample_rate = sample_rate_for(int(bandwidth_for_interval(frequencies) / FILTER_FACTOR), self.sample_rates)
        dump_cmd = [
            'dumphfdl',
            '--sample-rate', str(sample_rate),
        ]
        if self.device_settings:
            dump_cmd.extend(['--device-settings', self.device_settings,])
        if self.soapysdr:
            dump_cmd.extend(['--soapysdr', self.soapysdr,])
        if not self.quiet:
            dump_cmd.extend(['--output', 'decoded:text:file:path=/dev/stdout',])
        if self.antenna:
            dump_cmd.extend(['--antenna', 'Antenna B',])
        if self.statsd_server:
            dump_cmd.extend([
                '--statsd', self.statsd_server,
                '--noise-floor-stats-interval', '30',
            ])
        if self.system_table:
            dump_cmd.extend(['--system-table', self.system_table,])
        if self.system_table_save:
            dump_cmd.extend(['--system-table-save', self.system_table_save,])
        if self.station_id:
            dump_cmd.extend(['--station-id', self.station_id,])
        if self.acars_hub:
            host, port = self.acars_hub.split(':')
            dump_cmd.extend(['--output', f'decoded:json:tcp:address={host},port={port}'])
        elif self.station_id:
            dump_cmd.extend(['--output', 'decoded:json:tcp:address=feed.airframes.io,port=5556',])
        if self.log_path:
            dump_cmd.extend(['--output', f'decoded:json:file:path={self.log_path}/hfdl.json.log,rotate=daily',])
        # special file for ground_station_updater.
        dump_cmd.extend(['--output', f'decoded:json:file:path={self.ground_station_log}'])
        # TEMP
        dump_cmd.extend(['--output', 'decoded:basestation:tcp:address=yto.lan,port=30025'])
        # /TEMP
        dump_cmd += [str(f) for f in frequencies]
        return dump_cmd

    def on_exit(self):
        return asyncio.ensure_future(self.process.wait())

    def stop(self):
        logger.info("stopping")
        if self.process:
            self.process.send_signal(signal.SIGKILL)

    async def start(self, frequencies):
        logger.info("starting")
        cmd = self.command(frequencies)
        logger.debug(cmd)
        if self.ground_station_updater:
            self.ground_station_updater.enabled = False
        if self.ground_station_log and self.ground_station_log.exists():
            self.ground_station_log.unlink()
        self.process = await asyncio.create_subprocess_exec(*cmd)
        # if/when we become interested in looking for errors in output...
        # stdout=asyncio.subprocess.PIPE
        # stderr=asyncio.subprocess.PIPE
        self.on_exit().add_done_callback(self.exited)
        return self.process.returncode  # `None` if still running

    async def restart(self, frequencies):
        async def actual_restart(_=None):
            logger.info("SDR settling")
            await asyncio.sleep(self.sdr_settle)
            logger.info("SDR settled")
            await self.start(frequencies)
            logger.info("restarted")
            self.ground_station_updater = SquitterWatcher(self.ground_station_log, self.ground_stations.merge_squitter)
            asyncio.ensure_future(self.ground_station_updater.run())

        def exited(self, _=None):
            loop.create_task(actual_restart(_))

        logger.info("restarting")
        if self.process:
            self.on_exit().add_done_callback(exited)
            self.stop()
        else:
            await actual_restart()

    def exited(self, _=None):
        # here we might do some post-mortem, but nothing for now.
        self.process = None
        logger.info("exited")

    def kill(self):
        if self.process:
            self.process.terminate()
            self.process = None


async def busy():
    while True:
        await asyncio.sleep(1)


def split_stations(stations):
    if not stations or stations == '.':
        return []
    if stations.startswith('['):
        # could be JSON
        try:
            return json.loads(stations)
        except json.JSONDecodeError:
            pass
    if '+' in stations or ';' in stations:
        return re.split('[+;]', stations)
    s = stations.split(',')
    # it could still be a station name, not an id.
    try:
        int(s[0])
    except ValueError:
        return stations
    return s


def common_params(func):
    @click.option('--core-ids', default=os.getenv('DUMPHFDL_CORE_IDS', []))
    @click.option('--fringe-ids', default=os.getenv('DUMPHFDL_FRINGE_IDS', []))
    @click.option('--skip-fill', is_flag=True)
    @click.option('--max-samples', type=int, default=os.getenv('DUMPHFDL_MAX_SAMPLES', MAXIMUM_SAMPLE_SIZE))
    @click.option('--ignore-ranges', default=os.getenv('DUMPHFDL_IGNORE_RANGES', []))
    @click.option('--gs-cache', help='Airframes Station Data path', default=os.getenv('DUMPHFDL_AIRFRAMES_CACHE'))
    @click.option('--sample-rates', help='the sample sizes supported by your radio', default=os.getenv('DUMPHFDL_SAMPLE_RATES', ''))
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    if not ctx.invoked_subcommand:
        run()


@main.command()
@common_params
@click.option('-s', '--station-id', default=os.getenv('DUMPHFDL_STATION_ID'))
@click.option('--antenna', default=os.getenv('DUMPHFDL_ANTENNA'))
@click.option('--statsd', default=os.getenv('DUMPHFDL_STATSD'))
@click.option('--quiet', help='Suppress packet logging to stdout.', is_flag=True)
@click.option('--system-table', default=os.getenv('DUMPHFDL_SYSTABLE', SYSTABLE_LOCATION))
@click.option('--system-table-save', default=os.getenv('DUMPHFDL_SYSTABLE_UPDATES', SYSTABLE_UPDATES_PATH))
@click.option('--log-path', default=os.getenv('DUMPHFDL_LOG_PATH', LOG_PATH))
@click.option('--watch-interval', default=os.getenv('DUMPHFDL_WATCH_INTERVAL', WATCH_INTERVAL))
@click.option('--sdr-settle', default=int(os.getenv('DUMPHFDL_SDR_SETTLE', SDR_SETTLE_TIME)))
@click.option('--soapysdr', default=os.getenv('DUMPHFDL_SOAPYSDR'))
@click.option('--device-settings', default=os.getenv('DUMPHFDL_DEVICE_SETTINGS'))
@click.option('--acars-hub', default=os.getenv('DUMPHFDL_ACARS_HUB'))
def run(core_ids, fringe_ids, skip_fill, max_samples, ignore_ranges, gs_cache, sample_rates,
        station_id, antenna, statsd, quiet, system_table, system_table_save, log_path,
        watch_interval, sdr_settle, soapysdr, device_settings, acars_hub
    ):

    ground_stations = GroundStations(gs_cache)
    sample_rates = sorted(int(x) for x in sample_rates.split(',') if x)

    listener = HFDLListener(ground_stations, sample_rates)
    listener.station_id = station_id
    listener.antenna = antenna
    listener.statsd_server = statsd
    listener.quiet = quiet
    listener.system_table = system_table
    listener.system_table_save = system_table_save
    listener.log_path = log_path
    listener.sdr_settle = sdr_settle
    listener.soapysdr = soapysdr
    listener.device_settings = device_settings
    listener.acars_hub = acars_hub

    def frequencies_updated(new_frequencies):
        logger.info(f'frequencies: {new_frequencies} = {bandwidth_for_interval(new_frequencies)}')
        loop.create_task(listener.restart(new_frequencies))

    watcher = GroundStationWatcher(ground_stations, frequencies_updated)
    watcher.core_ids = split_stations(core_ids)
    watcher.fringe_ids = split_stations(fringe_ids)
    watcher.skip_fill = skip_fill
    watcher.watch_interval = int(watch_interval)
    watcher.max_sample_size = max_samples
    watcher.sample_rates = sample_rates
    watcher.set_ignore_ranges(ignore_ranges)
    # watcher.start()

    try:
        loop.run_until_complete(watcher.run())
    except asyncio.CancelledError:
        listener.kill()
    except KeyboardInterrupt:
        listener.kill()


@main.command()
@common_params
@click.option('--core', help='Build pool from Core stations only', is_flag=True)
@click.option('--named', help='Build pool from Core and Fringe stations only', is_flag=True)
@click.option('--experiments', help='show other possible pools based on experimental strategies.', is_flag=True)
def scan(
        core_ids, fringe_ids, skip_fill, max_samples, ignore_ranges, gs_cache, sample_rates,
        core, named, experiments
    ):
    global FRINGE_STATIONS, FILL_OTHER_STATIONS, EXPERIMENTAL
    EXPERIMENTAL = experiments
    if core:
        FRINGE_STATIONS = []
    FILL_OTHER_STATIONS = not (named or core)

    ground_stations = GroundStations(gs_cache)
    sample_rates = sorted(int(x) for x in sample_rates.split(',') if x)

    watcher = GroundStationWatcher(ground_stations)
    watcher.core_ids = split_stations(core_ids)
    watcher.fringe_ids = split_stations(fringe_ids)
    watcher.skip_fill = skip_fill
    watcher.max_sample_size = max_samples
    watcher.sample_rates = sample_rates
    watcher.set_ignore_ranges(ignore_ranges)

    freqs = watcher.refresh()
    bandwidth = int(bandwidth_for_interval(freqs))
    samples = int(bandwidth / 0.8)
    sample_rate = sample_rate_for(samples, watcher.sample_rates)
    watcher.experimental_pools()
    logger.info(f"Best Frequencies: {freqs}")
    logger.info(f"Required bandwidth: {bandwidth}kHz")
    logger.info(f"Required Samples/Second: {sample_rate}")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    main()
 