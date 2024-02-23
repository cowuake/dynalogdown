#!/usr/bin/env python3

import json
import os
import requests
import re
import sys
import urllib.parse
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

EMOTION_HAPPY = ":-D"
EMOTION_PLEASED = ":-)"
EMOTION_UNCERTAIN = ":-/"
EMOTION_UNHAPPY = ":-("
EMOTION_LOVELY = ":-*"
EMOTION_INDIFFERENT = ":-|"
PADDING_CHAR = "#"

HEADER = r"""
     ____ ____ ____ ____ ____ ____ ____ ____ ____ ____ ____
    ||D |||y |||n |||a |||l |||o |||g |||d |||o |||w |||n ||
    ||__|||__|||__|||__|||__|||__|||__|||__|||__|||__|||__||
    |/__\|/__\|/__\|/__\|/__\|/__\|/__\|/__\|/__\|/__\|/__\|

"""


def main():
    try:
        welcome()
        config = read_config()
        now = datetime.now().astimezone(ZoneInfo(config.time_zone)).isoformat()

        [time_from, time_final] = [
            datetime.fromisoformat(time).astimezone(ZoneInfo(config.time_zone))
            for time in [config.start, config.end]
        ]
        time_to = time_final
        dt = time_to - time_from

        query = build_query(config)
        path = init_log_file(config, query, now)

        while True:
            while True:
                print(
                    f"[{EMOTION_INDIFFERENT}] Searching in the interval "
                    + f'{time_from.isoformat(timespec="milliseconds")} - {time_to.isoformat(timespec="milliseconds")}'
                )
                aggregate_url = get_aggregate_url(time_from, time_to, config, query)

                # Check the number of log entries available in the specified time interval
                response = get_response(aggregate_url, config)
                n_entries = count_log_lines(response.text, config.source)

                if n_entries == 0:
                    [time_from, time_to] = [time_to, time_from + dt]
                    continue

                if n_entries < 250 and time_to + config.magic_factor * dt < time_final:
                    print(
                        f"[{EMOTION_PLEASED}] Found {n_entries} < 250 log entries within the time interval. Increasing delta."
                    )
                    dt *= config.magic_factor
                    time_to = time_from + dt
                    continue

                if n_entries < 1000:
                    break

                print(
                    f"[{EMOTION_UNCERTAIN}] Found {n_entries} > 1000 log entries within the time interval. Reducing delta."
                )
                dt /= 2
                time_to = time_from + dt

            search_url = get_search_url(time_from, time_to, config, query)
            response = get_response(search_url, config)
            data = json.loads(response.text)
            with open(path, "a") as f:
                save_fields(data, "content", f)

            print(
                f"[{EMOTION_HAPPY}] Written {n_entries} log entries on '{config.file}'."
            )

            if time_to >= time_final:
                break

            [time_from, time_to] = [time_to, time_from + dt]

        say_goodbye(f"[{EMOTION_LOVELY}] All is done. Farewell!")
    except Exception as e:
        say_goodbye(f"[{EMOTION_UNHAPPY}] Sorry, something went wrong: {e}.")


@dataclass
class Config:
    base_url: str = None
    cookie: str = None
    namespace: str = None
    token: str = None
    start: str = None
    end: str = None
    source: str = None
    magic_factor: float = 1.1
    time_zone: str = "Europe/Rome"
    file: str = "output.txt"
    directory: str = "."
    pod: str = None
    query: str = None


def read_config():
    config = ConfigParser()
    config.read("config.ini")
    cfg = Config()
    cfg.base_url = config.get("Connection", "baseurl").strip()
    cfg.cookie = config.get("Connection", "cookie").strip()
    cfg.namespace = config.get("Log", "namespace").strip()
    cfg.token = config.get("Connection", "token").strip()
    cfg.start = config.get("Log", "start").strip()
    cfg.end = config.get("Log", "end").strip()
    cfg.source = config.get("Log", "source").strip().lower().replace("\\", "\\\\")
    cfg.magic_factor = float(config.get("Nerd zone", "magicfactor").strip())
    cfg.time_zone = config.get("Nerd zone", "timezone").strip()
    cfg.file = config.get("Output", "file").strip()
    cfg.directory = config.get("Output", "directory").strip()
    cfg.pod = config.get("Log", "pod").strip()
    cfg.query = config.get("Override", "query", fallback="").strip()
    return cfg


def encode_time_qs(key, time_string):
    return urllib.parse.urlencode({key: time_string})


def get_headers(config):
    return {
        "accept": "application/json; charset=utf-8",
        "accept-language": "en-US,en;q=0.9,it;q=0.8",
        "content-type": "application/json; charset=utf-8",
        "cookie": config.cookie,
        "x-csrftoken": config.token,
    }


def build_query(config):
    if config.query:
        return config.query
    namespace_query = (
        f'k8s.namespace.name="{config.namespace}"' if config.namespace else ""
    )
    pod_query = f'k8s.pod.name="{config.pod}"' if config.pod else ""
    source_query = f'log.source="{config.source}"' if config.source else ""
    query = " AND ".join(
        [filter for filter in [namespace_query, pod_query, source_query] if filter]
    )
    return query


def get_aggregate_url(start, end, config, query):
    query_from = encode_time_qs("from", start.isoformat(timespec="milliseconds"))
    query_to = encode_time_qs("to", end.isoformat(timespec="milliseconds"))
    url = f"{config.base_url}/rest/v2/logs/aggregate?&maxGroupValues=100&timeBuckets=1&groupBy=log.source&{query_from}&{query_to}"
    if query:
        url += f"&query={query}"
    return url


def get_search_url(start, end, config, query):
    query_from = encode_time_qs("from", start.isoformat(timespec="milliseconds"))
    query_to = encode_time_qs("to", end.isoformat(timespec="milliseconds"))
    url = f"{config.base_url}/rest/v2/logs/search?{query_from}&{query_to}&limit=1000&sort=timestamp"
    if query:
        url += f"&query={query}"
    return url


def get_response(url, config):
    response = requests.request("GET", url, headers=get_headers(config), data={})
    validate_response(response)
    return response


def welcome():
    print(HEADER)


def init_log_file(config, query, now):
    Path(config.directory).mkdir(parents=True, exist_ok=True)
    path = os.path.join(config.directory, config.file)

    lines = HEADER.splitlines() + [
        f"SOURCE NAMESPACE:         {config.namespace}",
        f"SOURCE POD:               {config.pod}",
        f"SOURCE LOG FILE:          {config.source}".replace("\\\\", "\\"),
        f"TIME INTERVAL:            {config.start} - {config.end}",
        f"ORIGINALLY WRITTEN TO:    {path}",
        f"DYNALOGDOWN RUN AT:       {now}",
        "",
    ]
    hspace = 3
    hfill = 3
    n_columns = max([len(line) for line in lines])

    lines = [
        line.ljust(n_columns + hspace, " ").rjust(n_columns + 2 * hspace, " ")
        for line in lines
    ]
    lines = [
        line.ljust(n_columns + 2 * hspace + hfill, PADDING_CHAR).rjust(
            n_columns + 2 * (hspace + hfill), PADDING_CHAR
        )
        for line in lines
    ]
    extra_line = PADDING_CHAR * len(lines[0]) + "\n"
    lines = (
        [extra_line]
        + [line + "\n" for line in lines]
        + [extra_line, "\n", f"{PADDING_CHAR*hfill + ' '*hspace}QUERY: {query}\n", "\n"]
    )

    with open(path, "w") as f:
        f.writelines(lines)

    return path


def say_goodbye(message=None):
    if message is not None:
        print(message)
    input("Press Enter to exit...")
    sys.exit()


def validate_response(response):
    if response.status_code != 200:
        say_goodbye(
            f"[{EMOTION_UNHAPPY}] Could not fulfill the job: the server returned {response.status_code} ({response.reason})."
        )


def count_log_lines(text, source_name):
    pattern = (
        r"(?P<leading>.*?)"
        + re.escape(f'"{source_name}"')
        + r":\s?(?P<number>\d+)(?P<trailing>.*?)"
    )
    match = re.search(pattern, text)
    if match:
        return int(match.group("number"))
    else:
        # say_goodbye('Failed to count log entries.')
        return 0


def save_fields(data, field, file):
    if isinstance(data, dict):
        for key, value in data.items():
            if key == field:
                file.write(str(value))
            save_fields(value, field, file)
    elif isinstance(data, list):
        for item in data:
            save_fields(item, field, file)


if __name__ == "__main__":
    main()
