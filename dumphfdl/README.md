# dumbhfdl.py

`dumbhfdl.py` is a wrapper/harness for [dumphfdl](https://github.com/szpajder/dumphfdl) which attempts to pick "good" frequencies to listen to. It does this by regularly querying the [airframes](https://airframes.io/) service that aggregates the current state of the HFDL ground stations. It applies a ranked order and fits as many "useful" frequencies into the declared available sample rate. When a change is detected, `dumphfdl` is restarted with the new list.

(The picking can also be done without interfering with `dumphfdl` and output to the console)

This script requires a couple of third party libraries. `click` and `requests`. Many Linux distributions have packages
for these, or you can use `pip install -r requirements.txt` to install them from Pypi. Consider using a virtualenv.

All code in `airframes_adjacent` is licensed under the 3-clause BSD license. See the file `LICENSE` for details

## Simple uses

```
dumbhfdl.py scan
```

Retrieves the current active frequencies from Airframes, and displays the "best" set of frequencies. It assumes you are either okay with defaults, or have Environment Variables set.

```
dumbhfdl.py run
```

This assumes that you accept the defaults, or have the Environment Variables set (see below). This makes it possible for this script to be set up in a traditional Debian-style systemd service unit (configered through a file in `/etc/default`).


## Options

### Common 

`--core-ids TEXT` 

A list of the stations to use as the "core" list in frequency picking. Order matters. Either Station IDs or Station Names may be used. If IDs are used, they may be separated by commas (`,`), semi-colons (`;`), or plus signs (`+`). If Station Names are used, only plus signs or semi-colons may be used (since Station Names include commas). If using Station Names, remember to quote the parameter value on a command line. See also the `DUMPHFDL_CORE_IDS` environment variable. If you do not want to have any core stations, overriding values from the environment, pass a period (`.`). You can also pass a JSON-style list.

Core stations are the stations (in order) that you most want to hear traffic to/from. These will generally be the closest stations to you. You don't want more than 5 or maybe 6 stations or else you risk skewing towards emptier frequencies. I based my list on a brief analysis of messages received in empirical listening scan (switching between bands) over several days. I skewed towards the destinations of AC Downlinks as opposed to GS Uplinks. The latter is (to me) more often less interesting ACKs and squitters, while downlink messages carry positions, messages and state data.

(A version of the empirical scanner script is also included in the source repo)

Examples: 
```
--core-ids 4,1,17,3,7
--core-ids "Riverhead, New York;San Francisco, California;Canarias, Spain;Reykjavik, Iceland;Shannon, Ireland"
```

`--fringe-ids TEXT`

A list of the stations to use as the "fringe" list in frequency picking. Order matters. Either Station IDs or Station Names may be used. If IDs are used, they may be separated by commas (`,`), semi-colons (`;`), or plus signs (`+`). If Station Names are used, only plus signs or semi-colons may be used (since Station Names include commas). If using Station Names, remember to quote the parameter value on a command line. See also the `DUMPHFDL_FRINGE_IDS` environment variable. If you do not want to have any core stations, overriding values from the environment, pass a period (`.`) or an empty JSON list `[]`.

Fringe stations are ones that you may expect to observe some traffic from/to, but much less frequently than core stations. You do not want to make these core, or you could skew the frequency picking towards these less fruitful frequencies. You still want to include these if possible, though. If you have CPU constraints, this can even be an empty list.

Examples: 
```
--fringe-ids 11,2,13
--fringe-ids '["Albrook, Panama","Molokai, Hawaii","Santa Cruz, Bolivia"]'
```

`--skip-fill`

If present, frequency picking will not attempt to fill in any active frequencies from any station not in the core or fringe list.

`--max-samples kHz`

The maximum sample size for your radio, or that you wish to use for other reasons. Value is specified in kilohertz. See also the `DUMPHFDL_MAX_SAMPLES` environment variable.

Example: `--max-samples 10000`

`--ignore-ranges TEXT`

Ranges of frequencies to ignore, and not consider for frequency picking, most likey due to your local RF environment. Comma-separated set of intervals. See also the `DUMPHFDL_IGNORE_RANGES` environment variable. All values in kilohertz. It may also be a JSON list of range tuples.

Examples: 
```
--ignore-ranges 0-5000,20000-30000
--ignore-ranges '[[0,5000],[10000,12000]]'
```

`--gs-cache`

When specified, `dumbhfdl.py` will write out a JSON file with the same structure as the Airframes.io ground station endpoint. It will try to use this file to rebuild the ground station list when started and Airframes is not available. This eliminates the need to build the list up from squitters every time. See also the `DUMPHFDL_AIRFRAMES_CACHE` environment variable.

Example:
```
--gs-cache /tmp/ground-stations.json
```

`--sample-rates`

Allows you to provide a comma-separated list of specific sample rates. Only these sample rates will be used in dumphfdl command lines. This means that the effective maximum sample rate is the lower of the largest of these sample rates, and the value of `--max-samples`. See also the `DUMPHFDL_SAMPLE_RATES` environment variable.

*Specify the sample rates in actual samples, not kSamples or MSamples*.

```
--sample-rates 912000,768000,456000,384000,256000,192000
```

Sets the available sample rates to those for an Airspy HF+ Discovery.


### `run` Specific

Many of these options are passed through to `dumphfdl`. Others are used to configure other `dumphfdl` parameters.

`-s TEXT`, `--station-id TEXT`

This is the same as the `dumphfdl` option. It sets the published station name when feeding to `airframes.io`. It has no default (though see the `DUMPHFDL_STATION_ID` environment variable). If it is not passed (or read from the environment), no feeding will be done.

Example: `-s MY-XSTN1-HFDL`

`--antenna TEXT`

This is the same as the `dumphfdl` option. It is passed through to the command line. If omitted, it is read from the environment (`DUMPHFDL_ANTENNA`).

Example: `--antenna B`

`--statsd TEXT`

This is the same as the `dumphfdl` option. If it is set, statsd gauges and counters will be updated per `dumphfdl`. If this is set, noise floor stats for observed frequencies will also be sent to the statsd server every 30 seconds.

`--quiet`

Suppresses packet logging to stdout. If omitted, human readable packet summaries will be output.

`--system-table PATH`

This is the same as the `dumphfdl` option. See also the `DUMPHFDL_SYSTABLE` environment variable, which is consulted if this option is omitted.

Example: `--system-table /usr/local/share/dumphfdl/systable.conf`

`--system-table-save PATH`

This is the same as the `dumphfdl` option. See also the `DUMPHFDL_SYSTABLE_UPDATE_LOCATION` environment variable, which is used if this option is omitted. If neither exists, but `--system-table` is set, the value used is `$HOME/.local/share/dumbhfdl/`.

Example: `--system-table-save /tmp/dumphfdl`


`--log-path PATH`

Save JSON packet data to `PATH`. See also the `DUMPHFDL_LOG_PATH` environment variable, which is used if this option is omitted. If neither exists, `$HOME/.local/share/dumbhfdl` is used. Currently, packet logs are always written and rotated daily.

Example: `--log-path /tmp/dumphfdl`

`--watch-interval SECONDS`

The number of seconds `dumphfdl` listens before the frequency list is repicked. See also the `DUMPHFDL_WATCH_INTERVAL` environment variable. The default is 600.

Example: `--watch-interval 3600`

`--sdr-settle INTEGER`

The number of seconds this script will wait between stopping `dumphfdl` and restarting it with a new frequency list. This is needed by some SDRs that require time to reset internal state (I'm looking at you RSPdx). See also the `DUMPHFDL_SDR_SETTLE` environment variable. The default is 5.

Example: `--sdr-settle 10`

`--soapysdr TEXT`

This is the same as the `dumphfdl` option. It is passed through unaltered, and allows you to select the appropriate soapysdr driver and options. See also the `DUMPHFDL_SOAPYSDR` environment variable.

`--device-settings TEXT`

This is the same as the `dumphfdl` option. It is passed through unaltered, and allows you to select the appropriate receiver options. See also the `DUMPHFDL_DEVICE_SETTINGS` environment variable.

`--acars-hub HOST:PORT`

If this is set, or the fallback `DUMPHFDRL_ACARS_HUB` environment variable is available, it will be used to set up a JSON feed to the given `HOST` on UDP port `PORT`. This is generally intended for sending data to `acars_router` or `acarshub`.

Example: `--acars-hub localhost:9999`

### `scan` Specific

This is much simpler, and does not involke `dumphfdl` at all.

`--core`

If set, only the Core station list is used to build a frequency list. Fringe and Other stations are ignored.

`--named`

If set, only the Core and Fringe station lists are used to build the frequency list. Other stations are ignored. Similar to the `--skip-fill` option of the `run` subcommand above.

`--experiments` 

Show other possible frequency lists based on experimental strategies. These are discussed (briefly) below.

## Environment Variables

For the `run` command, most options can also be set as environment variables. This allows use of a `/etc/default/dumbhfdl` (or similar) file in a systemd service unit, and other cases where keeping configuration in a file is preferred. In a file, you can define them along the following lines:

```
DUMPHFDL_SOAPYSDR="driver=sdrplay"
DUMPHFDL_ANTENNA="B"
DUMPHFDL_DEVICE_SETTINGS="rfnotch_ctrl=true,dabnotch_ctrl=true,agc_setpoint=-30,biasT_ctrl=true"
DUMPHFDL_CORE_IDS='4,1,17,3,7'
DUMPHFDL_FRINGE_IDS='["Albrook, Panama","Molokai, Hawaii","Santa Cruz, Bolivia"]'
DUMPHFDL_IGNORE_RANGES=0-5000
DUMPHFDL_MAX_SAMPLES=9250
DUMPHFDL_WATCH_INTERVAL=600
DUMPHFDL_SDR_SETTLE=10
DUMPHFDL_SYSTABLE='/usr/local/share/dumphfdl/systable.conf'
# DUMPHFDL_SYSTABLE_UPDATES_LOCATION uses computed default
# DUMPHFDL_LOG_PATH uses computed default
DUMPHFDL_STATION_ID='MY-XYZA1-HFDL'
DUMPHFDL_STATSD='stats.example:8125'
DUMPHFDL_ACARS_HUB='localhost:5556'
DUMPHFDL_AIRFRAMES_CACHE=/tmp/ground-stations.json
# Airspy Discovery HF+ DUMPHFDL_SAMPLE_RATES=912000,768000,456000,384000,256000,192000
```

### Tip

If you are using `bash` and you want to avoid polluting your environment with these variables, you can use something like the following to help. This assumes that your environment variables are set in a file named `.env`.

```
bash$ (set -o allexport; source .env; set +o allexport ; /path/to/dumbhfdl.py run)
```

The parentheses are necessary as that makes the whole line a sub-shell. When it exits, the changes to the environment are removed. `allexport` tells the subshell that any variable assignment affects the environment (automatically `export`s them).

## Theory of Operation

HFDL operates on a variety of frequencies in the HF band (1.6 - 30MHz). The ideal frequencies for both aircraft and ground stations changes depending on time of day and geomagnetic conditions. The general trend can be predicted, but specifics are always variable. The HFDL system itself provides an internal management mechanism that coordinates frequency assignments between ground stations. We do not have access to that system. However, planes also need to know what frequencies to use, so every ground station broadcasts a (partial) list of active frequencies of the HFDL system at the start of each 32 second frame. These "squitters" can be aggregated over several frames to provide a complete list of operating frequencies that changes as conditions change. [Airframes](https://airframes.io) provides an [API endpoint](https://api.airframes.io/hfdl/ground-stations) that has the latest aggregated list available. `dumbhfdl.py` will keep and update its own copy of this file if the `--gs-cache` option (or env var) is set.

### Why is this important?

Most SDRs do not have the available sample rates to listen to all of the HFDL frequencies at once, so we have to be choosy. Available CPU power can also be a limiting factor. `dumbhfdl.py` automates much of the process of selecting the best set of frequencies. It's optimized for my SDRPlay RSPdx, but it should work (perhaps with minor modifications) on any HF-capable SDR that `dumphfdl` (the underlying processor) can use.

### What does it do?

When it is started, the script queries the Airframes HFDL Ground Stations API for the list of current frequencies. It then performs a ranked selection of frequencies, trying for the best and most coverage. It starts (or restarts) `dumphfdl` with the new frequency list. While `dumphfdl` is running, it listens to the squitters, updating its own list. Every ten minutes (by default), it attempts to requery Airframes for an updated list, and repeats the frequency selection. If Airframes is not available, the frequency list aggregated from HFDL Ground Station squitters is used. If that is not available (usually when Airframes is not available when the script is first started), a full list of active frequencies is used. This is suboptimal, but is usually enough to bootstrap to a frequency where squitters can be aggregated and used "for real" on the next refresh.

### How do I rank ground stations?

There are two sets of stations: `core` and `fringe`. The Core stations are the most important; they determine the initial best bands. Fringe stations are used as hints to expand it for more coverage. Finally, all the other ground stations are considered because propagation can do wonderful things, and if there's room in your sample window (and your CPU), use it!

The easiest way to pick core and fringe stations is to consider the list at the [Ground Station Active Frequencies](https://api.airframes.io/hfdl/ground-stations) list (if you can read JSON), or from the list on the `HFDL` tab of the Airframes [About Page](https://app.airframes.io/about). Pick the three or four stations closest to you, in order of distance. This is not *always* the best list, but it is a great start. This is your core list. Pick the next three or four stations in distance for the fringe list. Set them in your call to `dumbhfdl.py` or in its environment/file. Three or four sations is a good number because HFDL is designed so that three stations should be reachable from any (trafficked) point on the globe.

A more sophisticated way is to spend a few days with an empirical sampler script (a fragmentary (and not necessarily complete) example is provided in the `empirical.py` file). Change bands every couple of minutes and count the packets by station over the run time. I weight downlink (from aircraft) over uplink (from ground station) as the latter will squitter regularly (every 32 seconds) anyways. While useful, it's not as interesting as data from planes. With enough data, the "important" (to your location) stations will become obvious.

### But how does it WORK?

The script starts with the first core station, and builds two lists. One from the high end, and one from the low end. It tries to add the next nearest frequency from each core station in turn. If that frequency fits into the sample window (or the sample window can be expanded to encompass it), it is added to the candidate list. Once the core stations have been evaluated, the list with the largest number of frequencies is selected as the working list.

To the working list, the fringe stations' frequencies are also added in a similar manner. They can expand the sample window. Finally, if configured, the "faint hope" stations' frequencies are added... if they fit into the sample window.

In practise, this generally means that you'll end up with most or all of the frequencies from one band or another (all the 6MHz frequencies, or all the 21MHz frequencies). My station's RSPdx can safely handle about 8MHz of bandwidth, so I usually cover several bands at once.

There are also some additional experimental pickers (enabled with `--experiments`) that do not affect the actual list chosen, but display a few other possible lists. I have found that they're generally not any better than the high/low method described above, but they can be interesting to check out.

### I don't like your parameters

Go ahead and change the defaults, or modify the code! I've tried to be flexible, but it is largely focused on what works for me. You can constructively suggest changes (through the repository Issues page, or as Pull Requests), but I can't provide any sort of SLA. I do hang out on the [Airframes Discord](https://discord.gg/airframes) (as `@KuupaOrk`) from time to time. Remember the terms of the `LICENSE` file if you plan to distribute your customised version (TL;DR: 3-clause BSD).

### Why "dumb" hfdl?

Because it amused, and as clever as the script might be, I'm sure there are smarter ways to do what it's doing. But mostly because I found it amusing.

# Further Reading

- https://www.icao.int/safety/acp/inactive%20working%20groups%20library/amcp%205/item-1e.pdf
