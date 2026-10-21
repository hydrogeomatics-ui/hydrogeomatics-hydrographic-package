#!/usr/bin/env python3
"""
HYDROGEOMATICS SBES / MBES SURVEY - single-file Python bathymetry survey utility.

This application supports GNSS/SBES logging, MBES raw packet logging, survey
planning, HYPACK-style RAW export, runline navigation, DXF/ENC background
display, post-processing helpers, and XYZ/GeoJSON export.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import queue
import re
import select
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from math import atan2, cos, degrees, floor, radians, sin, sqrt
from pathlib import Path
from typing import Any, Iterable, Optional
from xml.sax.saxutils import escape


APP_TITLE = "HYDROGEOMATICS — HYDROGEOMATICS"
DEFAULT_SOUND_SPEED = 1500.0
EARTH_RADIUS_KM = 6371.0088
NMEA_RE = re.compile(r"[$!][A-Z0-9]{2,6}[^$!\r\n]*(?:\*[0-9A-Fa-f]{2})?")
APP_DIR = Path(__file__).resolve().parent

SS510_BAUD_RATES = ("4800", "9600", "19200", "38400", "57600", "115200")
SS510_COMMANDS = (
    ("Reset", "$PAMTC,RESET"),
    ("Info", "$PAMTC,INFO,Q"),
    ("Version", "$PAMTC,QV"),
    ("Part/Serial", "$PAMTC,QPS"),
    ("POST", "$PAMTC,POST"),
    ("Query Output Mode", "$PAMTC,OPTION,Q,OUTPUTMC"),
    ("Query Range Default", "$PAMTC,OPTION,Q,RANGEDEFAULT"),
    ("Query Sound Speed", "$PAMTC,OPTION,Q,SOSTW"),
    ("Query Pings/Sec", "$PAMTC,OPTION,Q,PINGSPS"),
    ("Query Pulses/Ping", "$PAMTC,OPTION,Q,PULSESPP"),
    ("Query Depth Offset", "$PAMTC,OPTION,Q,DOFFSET"),
    ("Query Temp Offset", "$PAMTC,OPTION,Q,TOFFSET"),
    ("Query Depth Filter", "$PAMTC,OPTION,Q,DFILTER"),
    ("Query Sample Filter", "$PAMTC,OPTION,Q,SFILTER"),
)
SS510_QUICK_SET = (
    ("Output", "$PAMTC,OPTION,SET,OUTPUTMC", "PING"),
    ("Range", "$PAMTC,OPTION,SET,RANGEDEFAULT", "1"),
    ("SOSTW", "$PAMTC,OPTION,SET,SOSTW", "1500"),
    ("Pings", "$PAMTC,OPTION,SET,PINGSPS", "8,8,4,3"),
    ("Pulses", "$PAMTC,OPTION,SET,PULSESPP", "5,30,40,60"),
    ("D Offset", "$PAMTC,OPTION,SET,DOFFSET", "0"),
    ("T Offset", "$PAMTC,OPTION,SET,TOFFSET", "0"),
    ("D Filter", "$PAMTC,OPTION,SET,DFILTER", "0"),
    ("S Filter", "$PAMTC,OPTION,SET,SFILTER", "0"),
)
DEFAULT_SETTINGS_PATH = APP_DIR / "HYDROGEOMATICS_settings.json"
DEFAULT_PROJECT_FOLDER = APP_DIR / "SurveyPlan_Project"
GNSS_SENTENCE_OPTIONS = ["GGA", "RMC", "GLL", "VTG", "GSA", "GSV", "ZDA", "GST", "HDT", "THS", "GNS", "GBS", "BESTPOSA", "BESTPOS"]
SBES_SENTENCE_OPTIONS = ["DPT", "DBT", "DBS", "DBK", "MTW", "VBW", "ODOM", "ECHOTRAC", "DESO25", "PLAIN_DEPTH"]
GENERIC_DEVICE_SENTENCE_OPTIONS = ["NMEA", "NON_NMEA", "PLAIN_DEPTH"]


def ascii_sentence_type(line: str) -> str:
    text = str(line or "").strip()
    if not text:
        return ""
    if text.startswith("$"):
        body = text.split("*", 1)[0]
        fields = body.split(",")
        header = fields[0].lstrip("$").upper()
        if len(header) >= 5:
            return header
        return header or "NMEA"
    first = re.split(r"[,\s]+", text, maxsplit=1)[0].strip().upper()
    cleaned = re.sub(r"[^A-Z0-9_./:-]+", "", first)
    if not cleaned:
        return "NON_NMEA"
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned):
        return "PLAIN_DEPTH"
    return cleaned[:32]


def normalized_sentence_type(sentence_type: str) -> str:
    text = str(sentence_type or "").strip().upper()
    if text.startswith("$"):
        text = ascii_sentence_type(text)
    if len(text) >= 5 and text[:2].isalpha():
        return text[-3:]
    return text


PROJECT_SUBFOLDERS = ("RAW", "EDIT", "SORT")


def default_raw_folder() -> Path:
    return DEFAULT_PROJECT_FOLDER / "RAW"


def project_root_from_raw_folder(raw_folder: str | Path | None = None) -> Path:
    """Return the main project folder, treating RAW/raw as the raw-data subfolder."""
    raw_path = Path(raw_folder or default_raw_folder())
    if raw_path.name.upper() == "RAW":
        return raw_path.parent
    return raw_path


def safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(value or "").strip())
    text = re.sub(r"\s+", "_", text).strip("._- ")
    return text or "HYDROGEOMATICS_Project"


def ensure_project_subfolders_from_raw(raw_folder: str | Path | None = None) -> tuple[Path, Path, Path, Path]:
    """Create the standard project folder structure and return base, RAW, EDIT, SORT paths."""
    project_folder = project_root_from_raw_folder(raw_folder)
    raw_path = project_folder / "RAW"
    edit_path = project_folder / "EDIT"
    sort_path = project_folder / "SORT"
    project_folder.mkdir(parents=True, exist_ok=True)
    for folder in (raw_path, edit_path, sort_path):
        folder.mkdir(parents=True, exist_ok=True)
    return project_folder, raw_path, edit_path, sort_path


def discover_project_options() -> list[str]:
    """Find available project folders from the app folder and common HYPACK locations."""
    roots = [
        DEFAULT_PROJECT_FOLDER,
        APP_DIR,
        APP_DIR.parent,
        Path(r"C:\HYPACK 2018\Projects"),
        Path(r"C:\HYPACK\Projects"),
    ]
    found: list[str] = []
    seen: set[str] = set()
    for root in roots:
        candidates = [root]
        if root.exists() and root.is_dir():
            try:
                candidates.extend([item for item in root.iterdir() if item.is_dir()])
            except Exception:
                pass
        for candidate in candidates:
            try:
                if not candidate.exists() or not candidate.is_dir():
                    continue
                has_project_shape = any((candidate / name).exists() for name in PROJECT_SUBFOLDERS) or any(candidate.glob("*.json"))
                if candidate == DEFAULT_PROJECT_FOLDER or has_project_shape:
                    key = str(candidate.resolve()).lower()
                    if key not in seen:
                        seen.add(key)
                        found.append(str(candidate))
            except Exception:
                continue
    return found


GRID_FILE_EXTENSIONS = (".grd", ".grid", ".gtx", ".gsb", ".ktd", ".csv", ".xyz", ".brd", ".lnw", ".dxf", ".dwg", ".000", ".enc", ".s57", ".kmz", ".kml")


def discover_grid_options(project_folder: str | Path | None = None) -> list[str]:
    """Find available grid/background/support files in the selected project and common project folders."""
    roots = []
    if project_folder:
        roots.append(project_root_from_raw_folder(project_folder))
    roots.extend(Path(item) for item in discover_project_options())
    roots.extend([APP_DIR, Path(r"C:\HYPACK 2018\Projects")])
    found: list[str] = []
    seen: set[str] = set()
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
            for item in root.rglob("*"):
                if not item.is_file() or item.suffix.lower() not in GRID_FILE_EXTENSIONS:
                    continue
                key = str(item.resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    found.append(str(item))
                if len(found) >= 250:
                    return found
        except Exception:
            continue
    return found


class CardinalPoint(str, Enum):
    NORTH = "N"
    SOUTH = "S"
    EAST = "E"
    WEST = "W"


@dataclass
class Satellite:
    prn: Optional[int] = None
    elevation: Optional[int] = None
    azimuth: Optional[int] = None
    signal_to_noise_ratio: Optional[int] = None
    satellite_type: str = "GPS"


@dataclass
class GPSPosition:
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_m: Optional[float] = None
    speed_knots: Optional[float] = None
    course_degrees: Optional[float] = None
    heading_degrees: Optional[float] = None
    altitude_m: Optional[float] = None
    ktd_cell_k: Optional[float] = None
    rtk_reduced_height: Optional[float] = None
    rtk_reduced_depth: Optional[float] = None
    altitude_m: Optional[float] = None
    hdop: Optional[float] = None
    vdop: Optional[float] = None
    pdop: Optional[float] = None
    geo_height_m: Optional[float] = None
    utc_datetime: Optional[datetime] = None
    fix_quality: Optional[int] = None
    satellites_used: Optional[int] = None
    mode1: Optional[str] = None
    mode2: Optional[str] = None
    raw_sentences: list[str] = field(default_factory=list)

    @property
    def is_fix(self) -> bool:
        if self.fix_quality is not None:
            return self.fix_quality > 0
        if self.mode2 is not None:
            return self.mode2.upper() in {"A", "D", "E", "F", "R"}
        return self.latitude is not None and self.longitude is not None

    @property
    def speed_kmh(self) -> Optional[float]:
        return knots_to_kmh(self.speed_knots) if self.speed_knots is not None else None

    def latitude_dms(self) -> Optional[tuple[int, int, float, CardinalPoint]]:
        if self.latitude is None:
            return None
        return decimal_degrees_to_dms(self.latitude, CardinalPoint.NORTH, CardinalPoint.SOUTH)

    def longitude_dms(self) -> Optional[tuple[int, int, float, CardinalPoint]]:
        if self.longitude is None:
            return None
        return decimal_degrees_to_dms(self.longitude, CardinalPoint.EAST, CardinalPoint.WEST)

    def distance_to_km(self, other: "GPSPosition") -> Optional[float]:
        if None in (self.latitude, self.longitude, other.latitude, other.longitude):
            return None
        return distance_km(self.latitude, self.longitude, other.latitude, other.longitude)

    def bearing_to(self, other: "GPSPosition") -> Optional[float]:
        if None in (self.latitude, self.longitude, other.latitude, other.longitude):
            return None
        return bearing_degrees(self.latitude, self.longitude, other.latitude, other.longitude)

    def clone(self) -> "GPSPosition":
        copied = GPSPosition(**{key: value for key, value in self.__dict__.items() if key != "raw_sentences"})
        copied.raw_sentences = list(self.raw_sentences)
        return copied


@dataclass
class Offsets:
    antenna_x: float = 0.0
    antenna_y: float = 0.0
    antenna_z: float = 0.0
    transducer_x: float = 0.0
    transducer_y: float = 0.0
    transducer_z: float = 0.0


@dataclass
class SoundVelocityProfile:
    default_velocity_mps: float = DEFAULT_SOUND_SPEED
    source_file: Optional[str] = None
    points: list[tuple[float, float]] = field(default_factory=list)

    def velocity_at_depth(self, depth_m: float) -> float:
        if not self.points:
            return self.default_velocity_mps
        points = sorted((float(depth), float(velocity)) for depth, velocity in self.points)
        if depth_m <= points[0][0]:
            return points[0][1]
        if depth_m >= points[-1][0]:
            return points[-1][1]
        for (d1, v1), (d2, v2) in zip(points, points[1:]):
            if d1 <= depth_m <= d2:
                ratio = 0.0 if d2 == d1 else (depth_m - d1) / (d2 - d1)
                return v1 + ratio * (v2 - v1)
        return self.default_velocity_mps


@dataclass
class BathymetrySample:
    x: float
    y: float
    depth: float
    time_utc: Optional[str] = None
    tide: float = 0.0
    draft: float = 0.0
    sound_velocity_mps: Optional[float] = None
    corrected_depth: Optional[float] = None


@dataclass
class MapShape:
    kind: str
    points: list[tuple[float, float]]
    text: str = ""
    color: str = "#1f2937"
    width: int = 2
    attrs: dict[str, object] = field(default_factory=dict)


@dataclass
class HydroProject:
    name: str = "SurveyPlan Project"
    offsets: Offsets = field(default_factory=Offsets)
    svp: SoundVelocityProfile = field(default_factory=SoundVelocityProfile)
    shapes: list[MapShape] = field(default_factory=list)
    bathymetry: list[BathymetrySample] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "offsets": self.offsets.__dict__,
            "svp": {
                "default_velocity_mps": self.svp.default_velocity_mps,
                "source_file": self.svp.source_file,
                "points": self.svp.points,
            },
            "shapes": [shape.__dict__ for shape in self.shapes],
            "bathymetry": [sample.__dict__ for sample in self.bathymetry],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "HydroProject":
        offsets = Offsets(**dict(payload.get("offsets", {})))
        svp_payload = dict(payload.get("svp", {}))
        svp = SoundVelocityProfile(
            default_velocity_mps=float(svp_payload.get("default_velocity_mps", DEFAULT_SOUND_SPEED)),
            source_file=svp_payload.get("source_file"),
            points=[tuple(point) for point in svp_payload.get("points", [])],
        )
        shapes = [
            MapShape(
                kind=item.get("kind", "line"),
                points=[tuple(point) for point in item.get("points", [])],
                text=item.get("text", ""),
                color=item.get("color", "#1f2937"),
                width=int(item.get("width", 2)),
                attrs=dict(item.get("attrs", {})),
            )
            for item in payload.get("shapes", [])
        ]
        bathymetry = [BathymetrySample(**item) for item in payload.get("bathymetry", [])]
        return cls(
            name=str(payload.get("name", "SurveyPlan Project")),
            offsets=offsets,
            svp=svp,
            shapes=shapes,
            bathymetry=bathymetry,
        )


def _direction_sign(direction: CardinalPoint | str) -> int:
    value = CardinalPoint(str(direction).upper())
    return -1 if value in (CardinalPoint.SOUTH, CardinalPoint.WEST) else 1


def float_mod(value: float, modulus: float) -> float:
    return value - floor(value / modulus) * modulus


def dms_to_decimal_degrees(
    degrees_value: int,
    minutes: int,
    seconds: float,
    direction: CardinalPoint | str,
) -> float:
    return _direction_sign(direction) * (abs(degrees_value) + minutes / 60.0 + seconds / 3600.0)


def dm_to_dms(dm_value: float) -> tuple[int, int, float]:
    degrees_value = int(abs(dm_value) // 100)
    minute_value = abs(dm_value) - degrees_value * 100
    minutes = int(minute_value)
    seconds = (minute_value - minutes) * 60.0
    return degrees_value, minutes, seconds


def dms_to_dm(degrees_value: int, minutes: int, seconds: float) -> float:
    return abs(degrees_value) * 100.0 + minutes + seconds / 60.0


def decimal_degrees_to_dms(
    value: float,
    positive_direction: CardinalPoint,
    negative_direction: CardinalPoint,
) -> tuple[int, int, float, CardinalPoint]:
    direction = positive_direction if value >= 0 else negative_direction
    value = abs(value)
    degrees_value = int(value)
    minute_float = (value - degrees_value) * 60.0
    minutes = int(minute_float)
    seconds = (minute_float - minutes) * 60.0
    return degrees_value, minutes, seconds, direction


def knots_to_kmh(value: float) -> float:
    return value * 1.852


def kmh_to_knots(value: float) -> float:
    return value / 1.852


def km_to_nautical_miles(value: float) -> float:
    return value / 1.852


def nautical_miles_to_km(value: float) -> float:
    return value * 1.852


def meters_to_feet(value: float) -> float:
    return value * 3.28083989501312


def feet_to_meters(value: float) -> float:
    return value / 3.28083989501312


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2.0) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * atan2(sqrt(a), sqrt(1.0 - a))


def bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    d_lambda = radians(lon2 - lon1)
    y = sin(d_lambda) * cos(phi2)
    x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(d_lambda)
    return (degrees(atan2(y, x)) + 360.0) % 360.0


def make_utc_datetime(
    date_value: Optional[tuple[int, int, int]],
    time_value: Optional[tuple[int, int, float]],
) -> Optional[datetime]:
    if date_value is None or time_value is None:
        return None
    day, month, year = date_value
    hour, minute, second_float = time_value
    second = int(second_float)
    microsecond = int(round((second_float - second) * 1_000_000))
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=timezone.utc)


def correct_depth(
    raw_depth_m: float,
    draft_m: float = 0.0,
    tide_m: float = 0.0,
    measured_velocity_mps: Optional[float] = None,
    profile_velocity_mps: float = DEFAULT_SOUND_SPEED,
    reference_velocity_mps: float = DEFAULT_SOUND_SPEED,
) -> float:
    velocity = measured_velocity_mps or profile_velocity_mps or reference_velocity_mps
    velocity_scale = velocity / reference_velocity_mps if reference_velocity_mps else 1.0
    return raw_depth_m * velocity_scale + draft_m - tide_m


def process_bathymetry_samples(samples: list[BathymetrySample], svp: SoundVelocityProfile) -> list[BathymetrySample]:
    processed: list[BathymetrySample] = []
    for sample in samples:
        velocity = sample.sound_velocity_mps or svp.velocity_at_depth(sample.depth)
        processed.append(
            BathymetrySample(
                x=sample.x,
                y=sample.y,
                depth=sample.depth,
                time_utc=sample.time_utc,
                tide=sample.tide,
                draft=sample.draft,
                sound_velocity_mps=velocity,
                corrected_depth=correct_depth(
                    sample.depth,
                    draft_m=sample.draft,
                    tide_m=sample.tide,
                    measured_velocity_mps=velocity,
                ),
            )
        )
    return processed


def save_project(path: str | Path, project: HydroProject) -> None:
    Path(path).write_text(json.dumps(project.to_dict(), indent=2), encoding="utf-8")


def load_project(path: str | Path) -> HydroProject:
    return HydroProject.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _strip_sentence(sentence: str) -> str:
    return sentence.strip().lstrip("$!")


def nmea_checksum(sentence: str) -> int:
    body = _strip_sentence(sentence).split("*", 1)[0]
    value = 0
    for char in body:
        value ^= ord(char)
    return value


def nmea_checksum_str(sentence: str) -> str:
    return f"{nmea_checksum(sentence):02X}"


def add_nmea_checksum(sentence: str, terminator: bool = True) -> str:
    body = _strip_sentence(sentence).split("*", 1)[0]
    result = f"${body}*{nmea_checksum(body):02X}"
    return result + ("\r\n" if terminator else "")


def is_nmea_checksum_ok(sentence: str) -> bool:
    if "*" not in sentence:
        return True
    body, given = sentence.strip().rsplit("*", 1)
    return nmea_checksum_str(body) == given[:2].upper()


def extract_nmea_sentences(raw: str) -> list[str]:
    return [match.group(0).strip() for match in NMEA_RE.finditer(raw)]


def legacy_float(value: str) -> Optional[float]:
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def legacy_int(value: str) -> Optional[int]:
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_legacy_coord(value: str, direction: str) -> Optional[float]:
    raw = legacy_float(value)
    if raw is None or not direction:
        return None
    degrees_value = int(raw // 100)
    minutes = raw - degrees_value * 100
    decimal = degrees_value + minutes / 60.0
    return -decimal if direction.upper() in {"S", "W"} else decimal


def parse_utc_time(value: str) -> Optional[tuple[int, int, float]]:
    if not value or len(value.strip()) < 6:
        return None
    value = value.strip()
    try:
        return int(value[0:2]), int(value[2:4]), float(value[4:])
    except ValueError:
        return None


def parse_utc_date(value: str) -> Optional[tuple[int, int, int]]:
    if not value or len(value) != 6:
        return None
    try:
        day = int(value[0:2])
        month = int(value[2:4])
        year = int(value[4:6])
    except ValueError:
        return None
    year += 2000 if year < 80 else 1900
    return day, month, year


def nmea_fields(sentence: str) -> tuple[str, list[str]]:
    body = _strip_sentence(sentence).split("*", 1)[0]
    parts = body.split(",")
    return parts[0].upper(), parts[1:]


@dataclass
class NMEAParser:
    position: GPSPosition = field(default_factory=GPSPosition)
    satellites: list[Satellite] = field(default_factory=list)
    track: list[GPSPosition] = field(default_factory=list)

    def parse(self, sentence: str) -> Optional[GPSPosition]:
        if not is_nmea_checksum_ok(sentence):
            return None
        command, fields = nmea_fields(sentence)
        handler = getattr(self, f"_parse_{command[-3:].lower()}", None)
        if handler is None:
            return None
        before = self.position.clone()
        result = handler(fields)
        if result and self.position.latitude is not None and self.position.longitude is not None:
            self.position.raw_sentences.append(sentence.strip())
            if (
                before.latitude != self.position.latitude
                or before.longitude != self.position.longitude
                or before.utc_datetime != self.position.utc_datetime
            ):
                self.track.append(self.position.clone())
        return self.position if result else None

    def feed(self, raw: str) -> list[GPSPosition]:
        results: list[GPSPosition] = []
        for sentence in extract_nmea_sentences(raw):
            parsed = self.parse(sentence)
            if parsed is not None:
                results.append(parsed.clone())
        return results

    def _parse_gga(self, fields: list[str]) -> bool:
        if len(fields) < 9:
            return False
        time_value = parse_utc_time(fields[0])
        lat = parse_legacy_coord(fields[1], fields[2])
        lon = parse_legacy_coord(fields[3], fields[4])
        if lat is not None:
            self.position.latitude = lat
        if lon is not None:
            self.position.longitude = lon
        self.position.fix_quality = legacy_int(fields[5])
        self.position.satellites_used = legacy_int(fields[6])
        self.position.hdop = legacy_float(fields[7])
        self.position.altitude_m = legacy_float(fields[8])
        if len(fields) > 10:
            self.position.geo_height_m = legacy_float(fields[10])
        if time_value and self.position.utc_datetime:
            old = self.position.utc_datetime
            self.position.utc_datetime = make_utc_datetime((old.day, old.month, old.year), time_value)
        return True

    def _parse_rmc(self, fields: list[str]) -> bool:
        if len(fields) < 9:
            return False
        time_value = parse_utc_time(fields[0])
        self.position.mode2 = fields[1] or None
        lat = parse_legacy_coord(fields[2], fields[3])
        lon = parse_legacy_coord(fields[4], fields[5])
        if lat is not None:
            self.position.latitude = lat
        if lon is not None:
            self.position.longitude = lon
        self.position.speed_knots = legacy_float(fields[6])
        self.position.course_degrees = legacy_float(fields[7])
        self.position.utc_datetime = make_utc_datetime(parse_utc_date(fields[8]), time_value) or self.position.utc_datetime
        return True

    def _parse_gll(self, fields: list[str]) -> bool:
        if len(fields) < 5:
            return False
        lat = parse_legacy_coord(fields[0], fields[1])
        lon = parse_legacy_coord(fields[2], fields[3])
        if lat is not None:
            self.position.latitude = lat
        if lon is not None:
            self.position.longitude = lon
        self.position.mode2 = fields[5] if len(fields) > 5 and fields[5] else fields[4] or None
        return True

    def _parse_vtg(self, fields: list[str]) -> bool:
        if len(fields) < 7:
            return False
        self.position.course_degrees = legacy_float(fields[0])
        self.position.speed_knots = legacy_float(fields[4])
        speed_kmh = legacy_float(fields[6])
        if self.position.speed_knots is None and speed_kmh is not None:
            self.position.speed_knots = kmh_to_knots(speed_kmh)
        return True

    def _parse_gsa(self, fields: list[str]) -> bool:
        if len(fields) < 15:
            return False
        self.position.mode1 = fields[0] or None
        self.position.mode2 = fields[1] or None
        self.position.pdop = legacy_float(fields[14]) if len(fields) > 14 else None
        self.position.hdop = legacy_float(fields[15]) if len(fields) > 15 else self.position.hdop
        self.position.vdop = legacy_float(fields[16]) if len(fields) > 16 else self.position.vdop
        return True

    def _parse_gsv(self, fields: list[str]) -> bool:
        if len(fields) < 3:
            return False
        total = legacy_int(fields[2])
        if total == 0:
            self.satellites.clear()
        for index in range(3, len(fields), 4):
            chunk = fields[index : index + 4]
            if len(chunk) < 4:
                continue
            self.satellites.append(
                Satellite(
                    prn=legacy_int(chunk[0]),
                    elevation=legacy_int(chunk[1]),
                    azimuth=legacy_int(chunk[2]),
                    signal_to_noise_ratio=legacy_int(chunk[3]),
                )
            )
        return True


def export_gpx(track: Iterable[GPSPosition], path: str | Path, name: str = "SurveyPlan Track") -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="surveyplan" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <trk>",
        f"    <name>{escape(name)}</name>",
        "    <trkseg>",
    ]
    for point in track:
        if point.latitude is None or point.longitude is None:
            continue
        lines.append(f'      <trkpt lat="{point.latitude:.10f}" lon="{point.longitude:.10f}">')
        if point.altitude_m is not None:
            lines.append(f"        <ele>{point.altitude_m:.3f}</ele>")
        if isinstance(point.utc_datetime, datetime):
            lines.append(f"        <time>{point.utc_datetime.isoformat().replace('+00:00', 'Z')}</time>")
        lines.append("      </trkpt>")
    lines.extend(["    </trkseg>", "  </trk>", "</gpx>", ""])
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def gps_position_to_dict(position: GPSPosition) -> dict[str, object]:
    return {
        "latitude": position.latitude,
        "longitude": position.longitude,
        "altitude_m": position.altitude_m,
        "speed_knots": position.speed_knots,
        "speed_kmh": position.speed_kmh,
        "course_degrees": position.course_degrees,
        "heading_degrees": position.heading_degrees,
        "hdop": position.hdop,
        "vdop": position.vdop,
        "pdop": position.pdop,
        "fix_quality": position.fix_quality,
        "satellites_used": position.satellites_used,
        "is_fix": position.is_fix,
        "utc_datetime": position.utc_datetime.isoformat() if position.utc_datetime else None,
    }


@dataclass
class SerialSettings:
    title: str
    port: str = "COM1"
    baud_rate: int = 4800
    data_width: int = 8
    stop_bits: float = 1.0
    parity: str = "None"
    nmea_log: str = ""
    enabled: bool = True
    recording_rate_hz: float = 1.0
    sentence_options: list[str] = field(default_factory=list)
    selected_sentences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "port": self.port,
            "baud_rate": self.baud_rate,
            "data_width": self.data_width,
            "stop_bits": self.stop_bits,
            "parity": self.parity,
            "nmea_log": self.nmea_log,
            "enabled": self.enabled,
            "recording_rate_hz": self.recording_rate_hz,
            "sentence_options": list(self.sentence_options),
            "selected_sentences": list(self.selected_sentences),
        }


@dataclass
class SensorSettings(SerialSettings):
    sbes_message: str = "NMEA DPT"
    sbes_format: str = "DPT"

    def to_dict(self) -> dict[str, object]:
        data = super().to_dict()
        data.update({"sbes_message": self.sbes_message, "sbes_format": self.sbes_format})
        return data


RTK_TIDE_METHODS = [
    "Not using RTK tide",
    "(K-N) from KTD file",
    "N from geoid model, K from KTD file",
    "N from geoid model, K from VDatum",
    "N from geoid model, K from user value",
    "(K-N) from user value",
]


@dataclass
class GeodeticSettings:
    use_projection: bool = True
    use_tm_projection: bool = True
    use_lcc_projection: bool = False
    projection_name: str = "WGS84 / UTM Zone 42"
    datum_name: str = "WGS84"
    ellipsoid: str = "WGS84"
    utm_zone: str = "42"
    fixed_utm_reference_zone: bool = False
    lcc_zone: str = ""
    central_meridian: float = 69.0
    latitude_origin: float = 0.0
    scale_factor: float = 0.9996
    false_easting: float = 500000.0
    false_northing: float = 0.0
    selected_grid_path: str = ""
    grid_name: str = "UTM North"
    zone_label: str = "Zone 42(66E-72E)"
    distance_unit: str = "Meter"
    depth_unit: str = "same as horizontal"
    elevation_mode_positive_up: bool = False
    semi_major_axis: float = 6378137.0
    flattening: float = 298.257223563
    delta_x: float = 0.0
    delta_y: float = 0.0
    delta_z: float = 0.0
    delta_rx: float = 0.0
    delta_ry: float = 0.0
    delta_rz: float = 0.0
    delta_scale: float = 0.0
    datum_shift_file: str = ""
    projection_method: str = "Transverse Mercator"
    reference_latitude: float = 0.0
    local_grid_adjustment: bool = False
    local_grid_file: str = ""
    rtk_tide_method: str = "Not using RTK tide"
    ellipsoidal_height_reference: float = 0.0
    chart_datum_level: float = 0.0
    geoid_separation_n: float = 0.0
    ktd_k_value: float = 0.0
    ktd_file_path: str = ""
    geoid_model_file: str = ""
    vdatum_zone: str = ""
    vdatum_surface_file: str = ""
    itrf_wgs84_epoch: str = ""
    vdatum_k_value: float = 0.0
    user_k_value: float = 0.0
    user_k_minus_n: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "use_projection": self.use_projection,
            "use_tm_projection": self.use_tm_projection,
            "use_lcc_projection": self.use_lcc_projection,
            "projection_name": self.projection_name,
            "datum_name": self.datum_name,
            "ellipsoid": self.ellipsoid,
            "utm_zone": self.utm_zone,
            "fixed_utm_reference_zone": self.fixed_utm_reference_zone,
            "lcc_zone": self.lcc_zone,
            "central_meridian": self.central_meridian,
            "latitude_origin": self.latitude_origin,
            "scale_factor": self.scale_factor,
            "false_easting": self.false_easting,
            "false_northing": self.false_northing,
            "selected_grid_path": self.selected_grid_path,
            "grid_name": self.grid_name,
            "zone_label": self.zone_label,
            "distance_unit": self.distance_unit,
            "depth_unit": self.depth_unit,
            "elevation_mode_positive_up": self.elevation_mode_positive_up,
            "semi_major_axis": self.semi_major_axis,
            "flattening": self.flattening,
            "delta_x": self.delta_x,
            "delta_y": self.delta_y,
            "delta_z": self.delta_z,
            "delta_rx": self.delta_rx,
            "delta_ry": self.delta_ry,
            "delta_rz": self.delta_rz,
            "delta_scale": self.delta_scale,
            "datum_shift_file": self.datum_shift_file,
            "projection_method": self.projection_method,
            "reference_latitude": self.reference_latitude,
            "local_grid_adjustment": self.local_grid_adjustment,
            "local_grid_file": self.local_grid_file,
            "rtk_tide_method": self.rtk_tide_method,
            "ellipsoidal_height_reference": self.ellipsoidal_height_reference,
            "chart_datum_level": self.chart_datum_level,
            "geoid_separation_n": self.geoid_separation_n,
            "ktd_k_value": self.ktd_k_value,
            "ktd_file_path": self.ktd_file_path,
            "geoid_model_file": self.geoid_model_file,
            "vdatum_zone": self.vdatum_zone,
            "vdatum_surface_file": self.vdatum_surface_file,
            "itrf_wgs84_epoch": self.itrf_wgs84_epoch,
            "vdatum_k_value": self.vdatum_k_value,
            "user_k_value": self.user_k_value,
            "user_k_minus_n": self.user_k_minus_n,
        }


@dataclass
class AppSettings:
    gps: SerialSettings
    sensor: SensorSettings
    geodetic: GeodeticSettings
    raw_folder: str = field(default_factory=lambda: str(default_raw_folder()))
    dxf_background_path: str = ""
    enc_background_path: str = ""
    kmz_background_path: str = ""
    nav_view_scale: str = "1:200"
    ui_font_size: int = 10
    ui_button_font_size: int = 10
    ui_menu_font_size: int = 10
    ui_contrast: str = "Standard"
    ui_color_scheme: str = "Nautical Light"
    share_gnss_sbes_input: bool = False
    extra_devices: list[SerialSettings] = field(default_factory=list)
    locked: bool = False

    @classmethod
    def defaults(cls) -> "AppSettings":
        return cls(
            gps=SerialSettings(
                "GNSS",
                port="COM1",
                baud_rate=115200,
                data_width=8,
                stop_bits=1,
                parity="N",
                nmea_log="HYDRO_GNSS.raw",
                recording_rate_hz=1.0,
                sentence_options=list(GNSS_SENTENCE_OPTIONS),
                selected_sentences=list(GNSS_SENTENCE_OPTIONS),
            ),
            sensor=SensorSettings(
                "SBES",
                port="COM2",
                baud_rate=38400,
                data_width=8,
                stop_bits=1,
                parity="N",
                nmea_log="HYDRO_SBES.raw",
                recording_rate_hz=1.0,
                sbes_message="NMEA DPT",
                sbes_format="DPT",
                sentence_options=list(SBES_SENTENCE_OPTIONS),
                selected_sentences=["DPT", "DBT", "DBS", "DBK", "PLAIN_DEPTH"],
            ),
            geodetic=GeodeticSettings(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "locked": self.locked,
            "gps": self.gps.to_dict(),
            "sensor": self.sensor.to_dict(),
            "geodetic": self.geodetic.to_dict(),
            "raw_folder": self.raw_folder,
            "dxf_background_path": self.dxf_background_path,
            "enc_background_path": self.enc_background_path,
            "kmz_background_path": self.kmz_background_path,
            "nav_view_scale": self.nav_view_scale,
            "ui_font_size": self.ui_font_size,
            "ui_button_font_size": self.ui_button_font_size,
            "ui_menu_font_size": self.ui_menu_font_size,
            "ui_contrast": self.ui_contrast,
            "ui_color_scheme": self.ui_color_scheme,
            "share_gnss_sbes_input": self.share_gnss_sbes_input,
            "extra_devices": [device.to_dict() for device in self.extra_devices],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "AppSettings":
        data = json.loads(path.read_text(encoding="utf-8"))
        settings = cls.defaults()
        settings.locked = bool(data.get("locked", False))
        settings.raw_folder = str(data.get("raw_folder", settings.raw_folder))
        settings.dxf_background_path = str(data.get("dxf_background_path", getattr(settings, "dxf_background_path", "")) or "")
        settings.enc_background_path = str(data.get("enc_background_path", getattr(settings, "enc_background_path", "")) or "")
        settings.kmz_background_path = str(data.get("kmz_background_path", getattr(settings, "kmz_background_path", "")) or "")
        settings.nav_view_scale = str(data.get("nav_view_scale", getattr(settings, "nav_view_scale", "1:200")) or "1:200")
        settings.ui_font_size = int(data.get("ui_font_size", getattr(settings, "ui_font_size", 10)) or 10)
        settings.ui_button_font_size = int(data.get("ui_button_font_size", getattr(settings, "ui_button_font_size", 10)) or 10)
        settings.ui_menu_font_size = int(data.get("ui_menu_font_size", getattr(settings, "ui_menu_font_size", 10)) or 10)
        settings.ui_contrast = str(data.get("ui_contrast", getattr(settings, "ui_contrast", "Standard")) or "Standard")
        settings.ui_color_scheme = str(data.get("ui_color_scheme", getattr(settings, "ui_color_scheme", "Nautical Light")) or "Nautical Light")
        settings.share_gnss_sbes_input = bool(data.get("share_gnss_sbes_input", getattr(settings, "share_gnss_sbes_input", False)))
        for target, source in [(settings.gps, data.get("gps", {})), (settings.sensor, data.get("sensor", {}))]:
            for key, value in source.items():
                if hasattr(target, key):
                    setattr(target, key, value)
        settings.gps.title = "GNSS"
        geo = data.get("geodetic", {})
        for key, value in geo.items():
            if hasattr(settings.geodetic, key):
                setattr(settings.geodetic, key, value)
        settings.extra_devices = []
        for item in data.get("extra_devices", []):
            if not isinstance(item, dict):
                continue
            device = SerialSettings(
                title=str(item.get("title", f"Device {len(settings.extra_devices) + 1}")),
                port=str(item.get("port", "COM1")),
                baud_rate=int(item.get("baud_rate", 4800)),
                data_width=int(item.get("data_width", 8)),
                stop_bits=float(item.get("stop_bits", 1)),
                parity=str(item.get("parity", "N")),
                nmea_log=str(item.get("nmea_log", "")),
                enabled=bool(item.get("enabled", True)),
                recording_rate_hz=float(item.get("recording_rate_hz", 1.0) or 1.0),
                sentence_options=list(item.get("sentence_options", GENERIC_DEVICE_SENTENCE_OPTIONS)),
                selected_sentences=list(item.get("selected_sentences", item.get("sentence_options", GENERIC_DEVICE_SENTENCE_OPTIONS))),
            )
            settings.extra_devices.append(device)
        return settings


def load_app_settings() -> AppSettings:
    if DEFAULT_SETTINGS_PATH.exists():
        try:
            return AppSettings.load(DEFAULT_SETTINGS_PATH)
        except Exception:
            return AppSettings.defaults()
    return AppSettings.defaults()


@dataclass
class MBESHardwareSetup:
    project_folder: str = field(default_factory=lambda: str(DEFAULT_PROJECT_FOLDER))
    survey_name: str = "MBES Survey"
    vessel_name: str = ""
    operator_name: str = ""
    hardware_raw_profile: str = "Kongsberg ALL/KMALL"
    sonar_make_model: str = "Kongsberg / Teledyne / Norbit MBES"
    native_raw_format: str = "KMALL / ALL / S7K / XTF"
    positioning_model: str = "GNSS / INS"
    motion_model: str = "MRU / INS"
    heading_model: str = "Gyro / INS"
    svp_sensor_model: str = "SVP / surface sound speed"
    tide_source: str = "Tide gauge / RTK tide"
    time_sync_source: str = "GNSS UTC / PPS / NTP"
    horizontal_crs: str = "WGS84 / UTM"
    vertical_datum: str = "Chart Datum"
    require_sonar: bool = True
    require_positioning: bool = True
    require_motion: bool = True
    require_heading: bool = True
    require_svp: bool = True
    require_tide: bool = False
    require_time_sync: bool = True
    sonar_udp_port: int = 4001
    nmea_udp_port: int = 4002
    motion_udp_port: int = 4003
    heading_udp_port: int = 4004
    log_sonar: bool = True
    log_nmea: bool = True
    log_motion: bool = True
    log_heading: bool = True
    gnss_route_enabled: bool = True
    gnss_serial_port: str = "COM1"
    gnss_baud_rate: int = 115200
    gnss_output_protocol: str = "UDP"
    gnss_output_host: str = "127.0.0.1"
    gnss_output_port: int = 4002
    sbes_route_enabled: bool = False
    sbes_serial_port: str = "COM2"
    sbes_baud_rate: int = 38400
    sbes_output_protocol: str = "UDP"
    sbes_output_host: str = "127.0.0.1"
    sbes_output_port: int = 4010
    transducer_x_m: float = 0.0
    transducer_y_m: float = 0.0
    transducer_z_m: float = 0.0
    antenna_x_m: float = 0.0
    antenna_y_m: float = 0.0
    antenna_z_m: float = 0.0
    roll_bias_deg: float = 0.0
    pitch_bias_deg: float = 0.0
    heading_bias_deg: float = 0.0
    latency_s: float = 0.0
    draft_m: float = 0.0
    tide_m: float = 0.0
    sound_speed_mps: float = DEFAULT_SOUND_SPEED
    svp_file: str = ""
    tide_file: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "MBESHardwareSetup":
        setup = cls()
        for key, value in payload.items():
            if hasattr(setup, key):
                current = getattr(setup, key)
                try:
                    if isinstance(current, bool):
                        setattr(setup, key, bool(value))
                    elif isinstance(current, int) and not isinstance(current, bool):
                        setattr(setup, key, int(value))
                    elif isinstance(current, float):
                        setattr(setup, key, float(value))
                    else:
                        setattr(setup, key, str(value))
                except Exception:
                    setattr(setup, key, value)
        return setup


def mbes_utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def mbes_file_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def mbes_packet_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def mbes_project_paths(project_folder: str | Path) -> dict[str, Path]:
    base = Path(project_folder or DEFAULT_PROJECT_FOLDER)
    paths = {
        "base": base,
        "raw": base / "RAW" / "MBES",
        "processed": base / "PROCESSED" / "MBES",
        "exports": base / "EXPORT" / "MBES",
        "logs": base / "LOGS" / "MBES",
        "sort": base / "SORTED" / "MBES",
        "matrix": base / "MATRIX" / "MBES",
        "svp": base / "SVP",
        "tide": base / "TIDE",
        "backup": base / "BACKUP" / "MBES",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


HYSWEEP_COLLECT_WORKFLOW = [
    "Create or select the project and confirm RAW, LOGS, SVP, TIDE, MATRIX, SORTED, EXPORT, and BACKUP folders.",
    "Configure HYPACK/HYSWEEP hardware drivers: MBES sonar, GNSS/INS, MRU, gyro/heading, SVP or surface sound speed, tide/water level, and time sync.",
    "Enter vessel geometry, sonar head offsets, antenna offsets, draft, latency, and patch-test roll/pitch/yaw values in one place only.",
    "Set serial-to-UDP/TCP routing for GNSS or SBES feeds when the MBES workflow needs network streams; use Generic Attitude-style ASCII parsing where required.",
    "Run static configuration checks for required components, ports, CRS, datum, correction files, offsets, and sound-speed ranges.",
    "Run live data checks with systems powered: sonar packets, navigation, motion/heave-pitch-roll, heading, and routed feeds should all show packet counts.",
    "Open live survey views: main navigation, profile, waterfall, coverage/cross section, QC, time series, TPU, message/status, device control, and MRU display.",
    "During acquisition, log native sonar packets plus navigation, motion, heading, tide/draft, sound velocity, offsets, and UTC/PPS/NTP timing metadata.",
    "Use matrix/coverage, targets, water column when needed, and backup/overlap rules to protect line data during long survey days.",
]


HYSWEEP_PROCESS_WORKFLOW = [
    "Preserve native RAW/HSX files, then load the survey line set or catalog into the editor/processor.",
    "Review read parameters: device selections, survey information, presort/GPS filters, sonar processing options, matrix/cloud settings, TPU, and offsets.",
    "Apply or verify tide/RTK tide, dynamic draft, sound velocity/refraction, heave/iHeave, heading, pitch, roll, latency, and patch-test corrections.",
    "Run patch-test review on dedicated lines before applying final roll, pitch, yaw, and latency corrections to production data.",
    "Clean in stages: basic filters, GPS filters, sweep filters, matrix filters, manual point/area editing, and flagged/golden sounding review.",
    "Calculate TPU and gridded uncertainty surfaces; use CUBE-style node review where available to resolve multiple hypotheses and low-confidence cells.",
    "Run crossline, coverage, density, uncertainty, and statistics checks before final deliverables.",
    "Save edited data to HS2/HS2X or project-native format, then export cleaned XYZ/CSV, matrix/grid, CUBE surface values, GeoTIFF/BAG/S-102 where supported, metadata, and QC/certification reports.",
]


MBES_PROCESSOR_PROFILES: dict[str, dict[str, object]] = {
    "Kongsberg ALL/KMALL": {
        "hardware": "Kongsberg EM/KM multibeam series",
        "raw_formats": ["ALL", "KMALL", "GSF", "ASCII/XYZ after conversion"],
        "preferred_processors": ["Qimera 2", "CARIS HIPS & SIPS", "BeamworX AutoClean", "MB-System"],
        "default_processor": "Qimera",
        "workflow": [
            "Preserve ALL/KMALL files as the legal raw record.",
            "Import raw files and extract vessel offsets, sonar settings, navigation, motion, heading, and sound speed.",
            "Run patch test, wobble review, navigation review, sound speed/refraction review, TPU, cleaning, gridding, and final export.",
        ],
        "exports": ["Cleaned XYZ/CSV", "GSF", "BAG", "GeoTIFF", "S-102 where licensed", "QC report"],
        "internal_support": "HYDROGEOMATICS internal processor supports only converted CSV/ASCII soundings for this profile.",
    },
    "Teledyne RESON S7K": {
        "hardware": "Teledyne RESON SeaBat / T-series multibeam",
        "raw_formats": ["S7K", "PDS logdata", "GSF", "ASCII/XYZ after conversion"],
        "preferred_processors": ["Teledyne PDS", "Qimera 2", "CARIS HIPS & SIPS", "BeamworX AutoClean"],
        "default_processor": "PDS",
        "workflow": [
            "Use PDS or compatible processor to import S7K with navigation, motion, heading, SVP, tide, offsets, snippets/backscatter where available.",
            "Run SeaBat-specific QC, patch test, TPU, beam cleaning, and grid/surface generation.",
            "Export cleaned soundings, grids, metadata, and certification report back to HYDROGEOMATICS.",
        ],
        "exports": ["PDS project/logdata", "Cleaned XYZ/CSV", "GSF", "BAG", "GeoTIFF", "QC report"],
        "internal_support": "HYDROGEOMATICS internal processor supports only converted CSV/ASCII soundings for this profile.",
    },
    "NORBIT S7K/WBMS": {
        "hardware": "NORBIT WBMS / iWBMS multibeam",
        "raw_formats": ["S7K", "WBMS vendor logs", "GSF", "ASCII/XYZ after conversion"],
        "preferred_processors": ["Qimera 2", "CARIS HIPS & SIPS", "BeamworX AutoClean", "NORBIT DCT/vendor workflow"],
        "default_processor": "Qimera",
        "workflow": [
            "Use NORBIT DCT/vendor acquisition files or S7K export as the raw import source.",
            "Verify embedded motion/INS, GNSS, heading, SVP, offsets, time sync, and uncertainty parameters.",
            "Process in Qimera/CARIS/AutoClean for cleaning, TPU, surface creation, and deliverables.",
        ],
        "exports": ["S7K", "Cleaned XYZ/CSV", "GSF", "BAG", "GeoTIFF", "QC report"],
        "internal_support": "HYDROGEOMATICS internal processor supports only converted CSV/ASCII soundings for this profile.",
    },
    "R2Sonic SONIC": {
        "hardware": "R2Sonic SONIC 2020/2022/2024/2026 series",
        "raw_formats": ["R2Sonic/vendor raw", "XTF", "GSF", "ASCII/XYZ after conversion"],
        "preferred_processors": ["Qimera 2", "CARIS HIPS & SIPS", "HYPACK/HYSWEEP", "PDS", "BeamworX AutoClean"],
        "default_processor": "Qimera",
        "workflow": [
            "Preserve SONIC raw/snippets/backscatter files and acquisition metadata.",
            "Import into Qimera/CARIS/HYSWEEP/PDS depending project requirement and available format.",
            "Run patch test, navigation/motion QC, TPU, cleaning, backscatter where required, and final gridded deliverables.",
        ],
        "exports": ["Cleaned XYZ/CSV", "GSF", "HS2/HS2X if HYPACK workflow", "BAG", "GeoTIFF", "QC report"],
        "internal_support": "HYDROGEOMATICS internal processor supports only converted CSV/ASCII soundings for this profile.",
    },
    "EdgeTech Bathymetric": {
        "hardware": "EdgeTech 6205 / 4600 bathymetric and side scan systems",
        "raw_formats": ["JSF/vendor data", "S7K or XTF where exported", "GSF", "ASCII/XYZ after conversion"],
        "preferred_processors": ["EdgeTech Discover/Discover Bathymetric", "CARIS HIPS & SIPS", "Qimera 2", "HYPACK/HYSWEEP", "PDS"],
        "default_processor": "Vendor software",
        "workflow": [
            "Use EdgeTech Discover/vendor workflow for acquisition and first-pass validation.",
            "Export to supported hydrographic processor format for refraction, offsets, TPU, cleaning, side scan/backscatter, and deliverables.",
            "Preserve original vendor raw plus exported processing files.",
        ],
        "exports": ["XTF", "S7K/GSF where available", "Cleaned XYZ/CSV", "GeoTIFF imagery", "QC report"],
        "internal_support": "HYDROGEOMATICS internal processor supports only converted CSV/ASCII soundings for this profile.",
    },
    "WASSP": {
        "hardware": "WASSP multibeam systems",
        "raw_formats": ["WASSP vendor data", "GSF", "ASCII/XYZ after conversion"],
        "preferred_processors": ["WASSP vendor processor", "Qimera 2", "CARIS HIPS & SIPS", "HYPACK/HYSWEEP"],
        "default_processor": "Vendor software",
        "workflow": [
            "Process first with WASSP/vendor tools when proprietary corrections or format conversion are required.",
            "Export GSF/ASCII/XYZ for third-party cleaning, gridding, and reporting.",
            "Run coverage, crossline, uncertainty, and metadata checks before final delivery.",
        ],
        "exports": ["GSF", "Cleaned XYZ/CSV", "Grid/GeoTIFF", "QC report"],
        "internal_support": "HYDROGEOMATICS internal processor supports GSF only after conversion to CSV/ASCII soundings.",
    },
    "Cerulean Surveyor 240-16": {
        "hardware": "Cerulean Sonar Surveyor 240-16 compact 240 kHz MBES",
        "raw_formats": ["SonarView SVLOG", "SonarView SVLZ compressed log", "CSV point cloud", "GSF beta export"],
        "preferred_processors": ["SonarView", "HYDROGEOMATICS Internal Processor", "ReefMaster", "CloudCompare/MeshLab", "Qimera 2 or MB-System via verified GSF/CSV"],
        "default_processor": "HYDROGEOMATICS Internal",
        "workflow": [
            "Use SonarView/SonarLink as the acquisition logger and preserve .svlog/.svlz files as the raw source archive.",
            "Configure a SonarView session with Surveyor plus NMEA heading/position or vehicle heading/position, then verify Ethernet discovery, IP/subnet, NTP time, pitch/roll, GNSS, and heading.",
            "Export CSV point cloud for direct HYDROGEOMATICS processing; export GSF only where the beta exchange file has been checked against control lines and processor import results.",
            "Set measured sound speed during SonarView export and record NTP source, vehicle offsets, mount orientation, patch/validation lines, and crossline checks.",
            "Run HYDROGEOMATICS CSV import, TPU estimation, cleaning, gridding, QC, and certification-style report while keeping SonarView logs as the legal raw record.",
        ],
        "exports": ["SVLOG/SVLZ raw archive", "CSV point cloud", "GSF beta", "Processed CSV/XYZ", "Grid CSV/GeoTIFF where generated", "QC report"],
        "internal_support": "HYDROGEOMATICS internal processor supports Cerulean Surveyor CSV point-cloud exports directly; SVLOG/SVLZ are preserved as raw archives, and GSF beta should be converted or validated before certification use.",
        "notes": [
            "Surveyor is Ethernet based and normally uses SonarView discovery/session configuration rather than a simple UDP-only sonar feed.",
            "Cerulean network timing depends on NTP/system time rather than PPS; use a GPS-disciplined NTP source for field work when possible.",
            "Client or IHO acceptance still requires independent validation, patch/offset checks, crosslines, sound-speed control, uncertainty documentation, and retained raw logs.",
        ],
    },
    "Generic GSF": {
        "hardware": "Any MBES converted to Generic Sensor Format",
        "raw_formats": ["GSF"],
        "preferred_processors": ["Qimera 2", "CARIS HIPS & SIPS", "BeamworX AutoClean", "MB-System", "EIVA NaviEdit/NaviModel"],
        "default_processor": "Qimera",
        "workflow": [
            "Use GSF as an exchange/archive format after preserving the original vendor raw.",
            "Process/clean in GSF-capable tools and export final surfaces and soundings.",
        ],
        "exports": ["Cleaned XYZ/CSV", "BAG", "GeoTIFF", "Grid", "QC report"],
        "internal_support": "HYDROGEOMATICS internal processor requires GSF to be converted to CSV/ASCII first.",
    },
    "HYPACK HS2/HS2X": {
        "hardware": "HYPACK/HYSWEEP processed multibeam project",
        "raw_formats": ["HSX", "HS2", "HS2X", "RAW", "LOG"],
        "preferred_processors": ["HYPACK/HYSWEEP", "BeamworX AutoClean", "CARIS HIPS & SIPS", "Qimera 2"],
        "default_processor": "HYPACK/HYSWEEP",
        "workflow": [
            "Use HYSWEEP Editor/64-bit Editor for read parameters, corrections, cleaning, TPU/CUBE, and HS2/HS2X output.",
            "Use AutoClean/Qimera/CARIS for additional point cloud cleaning or final product generation where required.",
        ],
        "exports": ["HS2", "HS2X", "XYZ", "Matrix/Grid", "GeoTIFF", "QC report"],
        "internal_support": "HYDROGEOMATICS internal processor supports exported CSV/XYZ from this profile.",
    },
    "ASCII/XYZ": {
        "hardware": "Any sonar exported to simple sounding table",
        "raw_formats": ["CSV", "TXT", "XYZ"],
        "preferred_processors": ["HYDROGEOMATICS Internal Processor", "BeamworX AutoClean", "Qimera 2 Clean", "CARIS"],
        "default_processor": "HYDROGEOMATICS Internal",
        "workflow": [
            "Use this profile when proprietary decoding has already been completed in vendor software.",
            "Run HYDROGEOMATICS internal TPU, filtering, gridding, visualization, and certification report.",
        ],
        "exports": ["Cleaned CSV", "Grid CSV", "XYZ", "GeoJSON", "Certification JSON"],
        "internal_support": "Fully supported by the HYDROGEOMATICS internal CSV/ASCII processor.",
    },
}


def mbes_enabled_sources(setup: MBESHardwareSetup) -> list[tuple[str, int, bool]]:
    return [
        ("sonar_native", int(setup.sonar_udp_port), bool(setup.log_sonar)),
        ("nmea_nav", int(setup.nmea_udp_port), bool(setup.log_nmea)),
        ("motion_mru", int(setup.motion_udp_port), bool(setup.log_motion)),
        ("heading", int(setup.heading_udp_port), bool(setup.log_heading)),
    ]


def mbes_check_udp_port_available(port: int) -> tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", int(port)))
        return True, "UDP port can be opened for listening"
    except Exception as exc:
        return False, f"UDP port cannot be opened: {exc}"
    finally:
        sock.close()


def mbes_add_check(checks: list[dict[str, str]], status: str, item: str, detail: str) -> None:
    checks.append({"status": status, "item": item, "detail": detail})


def mbes_validate_hardware_setup(setup: MBESHardwareSetup) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    paths = mbes_project_paths(setup.project_folder)
    try:
        test_path = paths["logs"] / ".mbes_write_test"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink(missing_ok=True)
        mbes_add_check(checks, "PASS", "Project folders", f"Writable project structure at {paths['base']}")
    except Exception as exc:
        mbes_add_check(checks, "FAIL", "Project folders", f"Cannot write to project folder: {exc}")

    required_text = [
        ("Survey name", setup.survey_name),
        ("Hardware/raw profile", getattr(setup, "hardware_raw_profile", "")),
        ("Sonar make/model", setup.sonar_make_model),
        ("Native raw format", setup.native_raw_format),
        ("Horizontal CRS", setup.horizontal_crs),
        ("Vertical datum", setup.vertical_datum),
    ]
    for label, value in required_text:
        if str(value or "").strip():
            mbes_add_check(checks, "PASS", label, str(value))
        else:
            mbes_add_check(checks, "FAIL", label, "Required field is blank")

    if setup.require_sonar and not setup.log_sonar:
        mbes_add_check(checks, "FAIL", "Sonar component", "Required sonar logging is disabled")
    elif setup.require_sonar:
        mbes_add_check(checks, "PASS", "Sonar component", setup.sonar_make_model or "Selected")
    if setup.require_positioning and not setup.log_nmea:
        mbes_add_check(checks, "FAIL", "Positioning component", "Required GNSS/NMEA logging is disabled")
    elif setup.require_positioning:
        mbes_add_check(checks, "PASS", "Positioning component", setup.positioning_model or "Selected")
    if setup.require_motion and not setup.log_motion:
        mbes_add_check(checks, "WARN", "Motion component", "Motion/MRU expected but logging is disabled")
    elif setup.require_motion:
        mbes_add_check(checks, "PASS", "Motion component", setup.motion_model or "Selected")
    if setup.require_heading and not setup.log_heading:
        mbes_add_check(checks, "WARN", "Heading component", "Heading expected but logging is disabled")
    elif setup.require_heading:
        mbes_add_check(checks, "PASS", "Heading component", setup.heading_model or "Selected")

    active_ports: dict[int, list[str]] = {}
    for name, port, enabled in mbes_enabled_sources(setup):
        if not enabled:
            mbes_add_check(checks, "INFO", f"{name} UDP", "Disabled")
            continue
        if not (1 <= int(port) <= 65535):
            mbes_add_check(checks, "FAIL", f"{name} UDP", f"Invalid port {port}")
            continue
        active_ports.setdefault(int(port), []).append(name)
        ok, detail = mbes_check_udp_port_available(int(port))
        mbes_add_check(checks, "PASS" if ok else "FAIL", f"{name} UDP {port}", detail)
    for port, names in active_ports.items():
        if len(names) > 1:
            mbes_add_check(checks, "FAIL", f"Duplicate UDP port {port}", ", ".join(names))

    route_ports: dict[tuple[str, str, int], list[str]] = {}
    for label, enabled, serial_port, baud_rate, protocol, host, out_port in [
        ("GNSS serial route", setup.gnss_route_enabled, setup.gnss_serial_port, setup.gnss_baud_rate, setup.gnss_output_protocol, setup.gnss_output_host, setup.gnss_output_port),
        ("SBES serial route", setup.sbes_route_enabled, setup.sbes_serial_port, setup.sbes_baud_rate, setup.sbes_output_protocol, setup.sbes_output_host, setup.sbes_output_port),
    ]:
        if not enabled:
            mbes_add_check(checks, "INFO", label, "Disabled")
            continue
        if not str(serial_port or "").strip():
            mbes_add_check(checks, "FAIL", label, "Serial port is blank")
        else:
            mbes_add_check(checks, "PASS", label, f"Input {serial_port} at {baud_rate} baud")
        try:
            int(baud_rate)
            if int(baud_rate) <= 0:
                raise ValueError("baud must be positive")
        except Exception:
            mbes_add_check(checks, "FAIL", label, f"Invalid baud rate {baud_rate}")
        proto = str(protocol or "").upper()
        try:
            output_port_value = int(out_port)
        except Exception:
            output_port_value = -1
        if proto not in {"UDP", "TCP"}:
            mbes_add_check(checks, "FAIL", label, f"Output protocol must be UDP or TCP, got {protocol}")
        elif not str(host or "").strip():
            mbes_add_check(checks, "FAIL", label, "Output host is blank")
        elif not (1 <= output_port_value <= 65535):
            mbes_add_check(checks, "FAIL", label, f"Invalid output port {out_port}")
        else:
            route_ports.setdefault((proto, str(host), output_port_value), []).append(label)
            mbes_add_check(checks, "PASS", f"{label} output", f"{proto} {host}:{output_port_value}")
    for (proto, host, port), labels in route_ports.items():
        if len(labels) > 1:
            mbes_add_check(checks, "WARN", f"Duplicate route output {proto} {host}:{port}", ", ".join(labels))

    for label, value in [
        ("Draft", setup.draft_m),
        ("Tide", setup.tide_m),
        ("Latency", setup.latency_s),
        ("Roll bias", setup.roll_bias_deg),
        ("Pitch bias", setup.pitch_bias_deg),
        ("Heading bias", setup.heading_bias_deg),
        ("Transducer X", setup.transducer_x_m),
        ("Transducer Y", setup.transducer_y_m),
        ("Transducer Z", setup.transducer_z_m),
        ("Antenna X", setup.antenna_x_m),
        ("Antenna Y", setup.antenna_y_m),
        ("Antenna Z", setup.antenna_z_m),
    ]:
        try:
            float(value)
            mbes_add_check(checks, "PASS", label, f"{float(value):.4f}")
        except Exception:
            mbes_add_check(checks, "FAIL", label, "Value is not numeric")

    try:
        sound_speed = float(setup.sound_speed_mps)
        if 1350.0 <= sound_speed <= 1650.0:
            mbes_add_check(checks, "PASS", "Sound speed", f"{sound_speed:.2f} m/s")
        else:
            mbes_add_check(checks, "WARN", "Sound speed", f"{sound_speed:.2f} m/s is outside the normal seawater range")
    except Exception:
        mbes_add_check(checks, "FAIL", "Sound speed", "Value is not numeric")

    if setup.require_svp:
        if setup.svp_file and Path(setup.svp_file).exists():
            mbes_add_check(checks, "PASS", "SVP file", setup.svp_file)
        else:
            mbes_add_check(checks, "WARN", "SVP file", "SVP is required but no existing file is selected")
    elif setup.svp_file and not Path(setup.svp_file).exists():
        mbes_add_check(checks, "WARN", "SVP file", "Selected SVP file does not exist")
    if setup.require_tide:
        if setup.tide_file and Path(setup.tide_file).exists():
            mbes_add_check(checks, "PASS", "Tide file", setup.tide_file)
        else:
            mbes_add_check(checks, "WARN", "Tide file", "Tide is required but no existing file is selected")
    elif setup.tide_file and not Path(setup.tide_file).exists():
        mbes_add_check(checks, "WARN", "Tide file", "Selected tide file does not exist")
    if setup.require_time_sync and not str(setup.time_sync_source or "").strip():
        mbes_add_check(checks, "WARN", "Time sync", "Select GNSS UTC, PPS, or NTP source")
    elif setup.require_time_sync:
        mbes_add_check(checks, "PASS", "Time sync", setup.time_sync_source)
    return checks


def mbes_probe_udp_connectivity(setup: MBESHardwareSetup, duration_s: float = 5.0) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    sockets: dict[socket.socket, tuple[str, int]] = {}
    try:
        for source_name, port, enabled in mbes_enabled_sources(setup):
            if not enabled:
                mbes_add_check(checks, "INFO", f"{source_name} live data", "Disabled")
                continue
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setblocking(False)
            try:
                sock.bind(("0.0.0.0", int(port)))
                sockets[sock] = (source_name, int(port))
            except Exception as exc:
                sock.close()
                mbes_add_check(checks, "FAIL", f"{source_name} UDP {port}", f"Cannot listen: {exc}")
        counts = {source_name: 0 for source_name, _port in sockets.values()}
        bytes_seen = {source_name: 0 for source_name, _port in sockets.values()}
        started = time.monotonic()
        while sockets and time.monotonic() - started < max(0.2, float(duration_s)):
            remaining = max(0.05, min(0.5, float(duration_s) - (time.monotonic() - started)))
            readable, _w, _x = select.select(list(sockets), [], [], remaining)
            for sock in readable:
                source_name, _port = sockets[sock]
                try:
                    payload, _addr = sock.recvfrom(65535)
                except BlockingIOError:
                    continue
                counts[source_name] += 1
                bytes_seen[source_name] += len(payload)
        for _sock, (source_name, port) in sockets.items():
            count = counts.get(source_name, 0)
            total_bytes = bytes_seen.get(source_name, 0)
            if count > 0:
                mbes_add_check(checks, "PASS", f"{source_name} live data", f"{count} packet(s), {total_bytes} byte(s) on UDP {port}")
            else:
                mbes_add_check(checks, "WARN", f"{source_name} live data", f"No packets seen on UDP {port} during {duration_s:.1f} s")
    finally:
        for sock in list(sockets):
            sock.close()
    return checks


class MBESRawLogger:
    """Small UDP logger that preserves native MBES packets and writes a JSONL audit index."""

    def __init__(self, setup: MBESHardwareSetup, status_callback: Optional[Any] = None, rotate_mb: int = 512) -> None:
        self.setup = setup
        self.paths = mbes_project_paths(setup.project_folder)
        self.status_callback = status_callback
        self.rotate_bytes = max(1, int(rotate_mb)) * 1024 * 1024
        self.stop_event = threading.Event()
        self.errors: queue.Queue[str] = queue.Queue()
        self.threads: list[threading.Thread] = []
        self.files: dict[str, tuple[Any, Path, int, int]] = {}
        self.lock = threading.Lock()
        self.index_path = self.paths["logs"] / f"mbes_acquisition_{mbes_file_stamp()}.jsonl"
        self.metadata_path = self.paths["logs"] / f"mbes_session_{mbes_file_stamp()}.json"
        self.index_handle: Optional[Any] = None

    def sources(self) -> list[tuple[str, int]]:
        return [(name, port) for name, port, enabled in mbes_enabled_sources(self.setup) if enabled]

    def start(self) -> None:
        self.stop_event.clear()
        self.paths = mbes_project_paths(self.setup.project_folder)
        self.metadata_path.write_text(json.dumps(self.setup.to_dict(), indent=2), encoding="utf-8")
        self.index_handle = self.index_path.open("a", encoding="utf-8")
        for source_name, port in self.sources():
            thread = threading.Thread(target=self._udp_worker, args=(source_name, port), daemon=True)
            thread.start()
            self.threads.append(thread)
        self._status(f"MBES logging started: {len(self.threads)} UDP source(s), index {self.index_path}")

    def stop(self) -> None:
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=1.5)
        self.threads.clear()
        with self.lock:
            for handle, _path, _size, _sequence in self.files.values():
                try:
                    handle.close()
                except Exception:
                    pass
            self.files.clear()
            if self.index_handle is not None:
                try:
                    self.index_handle.close()
                except Exception:
                    pass
                self.index_handle = None
        self._status("MBES logging stopped.")

    def _status(self, message: str) -> None:
        if self.status_callback is not None:
            try:
                self.status_callback(message)
            except Exception:
                pass

    def _open_source_file(self, source_name: str) -> tuple[Any, Path, int, int]:
        current = self.files.get(source_name)
        if current is not None and current[2] < self.rotate_bytes:
            return current
        if current is not None:
            try:
                current[0].close()
            except Exception:
                pass
        sequence = 1 if current is None else current[3] + 1
        source_dir = self.paths["raw"] / source_name
        source_dir.mkdir(parents=True, exist_ok=True)
        raw_path = source_dir / f"{mbes_file_stamp()}_{source_name}_{sequence:04d}.bin"
        handle = raw_path.open("ab")
        opened = (handle, raw_path, 0, sequence)
        self.files[source_name] = opened
        return opened

    def _write_packet(self, source_name: str, payload: bytes, address: str) -> None:
        if self.index_handle is None:
            return
        with self.lock:
            handle, raw_path, size, sequence = self._open_source_file(source_name)
            offset = handle.tell()
            handle.write(payload)
            handle.flush()
            record = {
                "received_utc": mbes_utc_stamp(),
                "source": source_name,
                "transport": "udp",
                "address": address,
                "raw_file": str(raw_path),
                "offset": offset,
                "length": len(payload),
                "sha256": mbes_packet_hash(payload),
                "sequence": sequence,
            }
            self.index_handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            self.index_handle.flush()
            self.files[source_name] = (handle, raw_path, size + len(payload), sequence)

    def _udp_worker(self, source_name: str, port: int) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.5)
        try:
            sock.bind(("0.0.0.0", int(port)))
            self._status(f"Listening for {source_name} on UDP {port}")
            while not self.stop_event.is_set():
                try:
                    payload, addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                self._write_packet(source_name, payload, f"{addr[0]}:{addr[1]}")
        except Exception as exc:
            self.errors.put(f"{source_name} UDP {port}: {exc}")
            self._status(f"MBES logger error: {source_name} UDP {port}: {exc}")
        finally:
            sock.close()


def mbes_float_from_row(row: dict[str, str], names: Iterable[str]) -> Optional[float]:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(str(name).lower())
        if value in (None, ""):
            continue
        try:
            return float(str(value).strip())
        except Exception:
            continue
    return None


def mbes_grid_cell(x: float, y: float, cell_size: float) -> tuple[int, int]:
    cell = max(float(cell_size), 0.001)
    return floor(x / cell), floor(y / cell)


def mbes_process_sounding_csv(
    input_path: str | Path,
    cleaned_path: str | Path,
    grid_path: str | Path,
    report_path: str | Path,
    draft_m: float = 0.0,
    tide_m: float = 0.0,
    min_depth: Optional[float] = None,
    max_depth: Optional[float] = None,
    cell_size: float = 1.0,
) -> dict[str, object]:
    input_path = Path(input_path)
    cleaned_path = Path(cleaned_path)
    grid_path = Path(grid_path)
    report_path = Path(report_path)
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    grid_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    kept = 0
    rejected = 0
    depths: list[float] = []
    grid: dict[tuple[int, int], list[float]] = {}

    with input_path.open("r", encoding="utf-8-sig", newline="") as src, cleaned_path.open("w", encoding="utf-8", newline="") as dst:
        sample = src.read(4096)
        src.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t ")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(src, dialect=dialect)
        fieldnames = ["x", "y", "raw_depth_m", "corrected_depth_m", "accepted", "rejected_reason"]
        writer = csv.DictWriter(dst, fieldnames=fieldnames)
        writer.writeheader()
        for row in reader:
            total += 1
            x = mbes_float_from_row(row, ["x", "easting", "east", "lon", "longitude"])
            y = mbes_float_from_row(row, ["y", "northing", "north", "lat", "latitude"])
            depth = mbes_float_from_row(row, ["depth", "depth_m", "z", "sounding", "sounding_m", "corrected_depth"])
            reason = ""
            corrected: Optional[float] = None
            if x is None or y is None or depth is None:
                reason = "missing_x_y_or_depth"
            else:
                corrected = float(depth) + float(draft_m) - float(tide_m)
                if min_depth is not None and corrected < float(min_depth):
                    reason = "shallower_than_min_depth"
                elif max_depth is not None and corrected > float(max_depth):
                    reason = "deeper_than_max_depth"
            accepted = not reason
            if accepted and x is not None and y is not None and corrected is not None:
                kept += 1
                depths.append(corrected)
                grid.setdefault(mbes_grid_cell(x, y, cell_size), []).append(corrected)
            else:
                rejected += 1
            writer.writerow(
                {
                    "x": "" if x is None else f"{x:.8f}",
                    "y": "" if y is None else f"{y:.8f}",
                    "raw_depth_m": "" if depth is None else f"{depth:.4f}",
                    "corrected_depth_m": "" if corrected is None else f"{corrected:.4f}",
                    "accepted": "1" if accepted else "0",
                    "rejected_reason": reason,
                }
            )

    with grid_path.open("w", encoding="utf-8", newline="") as grid_file:
        writer = csv.writer(grid_file)
        writer.writerow(["x", "y", "mean_depth_m", "minimum_depth_m", "maximum_depth_m", "count"])
        for (ix, iy), values in sorted(grid.items()):
            cx = (ix + 0.5) * float(cell_size)
            cy = (iy + 0.5) * float(cell_size)
            writer.writerow([f"{cx:.3f}", f"{cy:.3f}", f"{statistics.mean(values):.4f}", f"{min(values):.4f}", f"{max(values):.4f}", len(values)])

    report = {
        "input": str(input_path),
        "cleaned_csv": str(cleaned_path),
        "grid_csv": str(grid_path),
        "created_utc": mbes_utc_stamp(),
        "draft_m": draft_m,
        "tide_m": tide_m,
        "min_depth": min_depth,
        "max_depth": max_depth,
        "cell_size": cell_size,
        "total_soundings": total,
        "accepted_soundings": kept,
        "rejected_soundings": rejected,
        "grid_cells": len(grid),
        "minimum_depth_m": min(depths) if depths else None,
        "maximum_depth_m": max(depths) if depths else None,
        "mean_depth_m": statistics.mean(depths) if depths else None,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def mbes_export_xyz_from_cleaned(cleaned_path: str | Path, xyz_path: str | Path) -> int:
    cleaned_path = Path(cleaned_path)
    xyz_path = Path(xyz_path)
    xyz_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with cleaned_path.open("r", encoding="utf-8-sig", newline="") as src, xyz_path.open("w", encoding="utf-8", newline="\n") as dst:
        reader = csv.DictReader(src)
        for row in reader:
            if str(row.get("accepted", "1")).strip() in {"0", "False", "false"}:
                continue
            x = mbes_float_from_row(row, ["x"])
            y = mbes_float_from_row(row, ["y"])
            z = mbes_float_from_row(row, ["corrected_depth_m", "depth_m", "z"])
            if x is None or y is None or z is None:
                continue
            dst.write(f"{x:.3f} {y:.3f} {z:.3f}\n")
            count += 1
    return count


def mbes_export_geojson_from_cleaned(cleaned_path: str | Path, geojson_path: str | Path) -> int:
    cleaned_path = Path(cleaned_path)
    geojson_path = Path(geojson_path)
    geojson_path.parent.mkdir(parents=True, exist_ok=True)
    features: list[dict[str, object]] = []
    with cleaned_path.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        for row in reader:
            if str(row.get("accepted", "1")).strip() in {"0", "False", "false"}:
                continue
            x = mbes_float_from_row(row, ["x"])
            y = mbes_float_from_row(row, ["y"])
            z = mbes_float_from_row(row, ["corrected_depth_m", "depth_m", "z"])
            if x is None or y is None or z is None:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [x, y, z]},
                    "properties": {"depth_m": z},
                }
            )
    geojson_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=2), encoding="utf-8")
    return len(features)


HYDROGEOMATICS_TPU_ORDERS: dict[str, tuple[float, float]] = {
    "Special": (0.25, 0.0075),
    "1a": (0.50, 0.0130),
    "1b": (0.50, 0.0130),
    "2": (1.00, 0.0230),
    "User": (0.50, 0.0130),
}


def HYDROGEOMATICS_allowed_tvu(depth_m: float, order: str = "1a", user_a: float = 0.50, user_b: float = 0.013) -> float:
    a, b = HYDROGEOMATICS_TPU_ORDERS.get(str(order), HYDROGEOMATICS_TPU_ORDERS["1a"])
    if str(order) == "User":
        a, b = float(user_a), float(user_b)
    return math.sqrt(a * a + (b * float(depth_m)) ** 2)


def HYDROGEOMATICS_read_beam_csv(input_path: str | Path) -> list[dict[str, float | str]]:
    source = Path(input_path)
    records: list[dict[str, float | str]] = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t ")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        for row_index, row in enumerate(reader, 1):
            x = mbes_float_from_row(row, ["x", "easting", "east", "lon", "longitude"])
            y = mbes_float_from_row(row, ["y", "northing", "north", "lat", "latitude"])
            depth = mbes_float_from_row(row, ["corrected_depth_m", "depth_m", "depth", "z", "sounding", "range_depth"])
            if x is None or y is None or depth is None:
                continue
            beam_angle = mbes_float_from_row(row, ["beam_angle", "beam_angle_deg", "angle", "across_angle"]) or 0.0
            beam_number = mbes_float_from_row(row, ["beam", "beam_number", "beam_id"]) or float(row_index)
            quality = mbes_float_from_row(row, ["quality", "quality_flag", "q"]) or 1.0
            records.append(
                {
                    "x": float(x),
                    "y": float(y),
                    "depth_m": float(depth),
                    "beam_angle_deg": float(beam_angle),
                    "beam_number": float(beam_number),
                    "quality": float(quality),
                    "source_row": float(row_index),
                    "time_utc": str(row.get("time_utc", row.get("time", "")) or ""),
                }
            )
    return records


def HYDROGEOMATICS_internal_mbes_process(
    input_path: str | Path,
    output_cleaned_csv: str | Path,
    output_grid_csv: str | Path,
    output_cert_json: str | Path,
    order: str = "1a",
    user_a: float = 0.50,
    user_b: float = 0.013,
    cell_size: float = 1.0,
    density_required: int = 5,
    median_reject_m: float = 0.75,
    angle_limit_deg: float = 70.0,
    min_depth: Optional[float] = None,
    max_depth: Optional[float] = None,
) -> dict[str, object]:
    records = HYDROGEOMATICS_read_beam_csv(input_path)
    cell = max(float(cell_size), 0.001)
    cells: dict[tuple[int, int], list[dict[str, float | str]]] = {}
    for rec in records:
        cells.setdefault(mbes_grid_cell(float(rec["x"]), float(rec["y"]), cell), []).append(rec)

    medians: dict[tuple[int, int], float] = {}
    for key, values in cells.items():
        depths = [float(item["depth_m"]) for item in values]
        medians[key] = statistics.median(depths) if depths else 0.0

    cleaned_path = Path(output_cleaned_csv)
    grid_path = Path(output_grid_csv)
    cert_path = Path(output_cert_json)
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    grid_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    accepted = 0
    rejected = 0
    uncertainty_pass = 0
    density_pass_cells = 0
    accepted_cells: dict[tuple[int, int], list[dict[str, float | str]]] = {}
    rejected_reasons: dict[str, int] = {}

    with cleaned_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "x",
                "y",
                "depth_m",
                "beam_number",
                "beam_angle_deg",
                "quality",
                "tpu_m",
                "allowed_tvu_m",
                "residual_to_cell_median_m",
                "accepted",
                "rejected_reason",
                "time_utc",
            ],
        )
        writer.writeheader()
        for rec in records:
            x = float(rec["x"])
            y = float(rec["y"])
            depth = float(rec["depth_m"])
            angle = float(rec["beam_angle_deg"])
            quality = float(rec["quality"])
            key = mbes_grid_cell(x, y, cell)
            median_depth = medians.get(key, depth)
            residual = depth - median_depth
            allowed_tvu = HYDROGEOMATICS_allowed_tvu(depth, order, user_a, user_b)
            angle_component = abs(math.sin(math.radians(angle))) * max(depth, 0.0) * 0.004
            tpu = math.sqrt((0.15 + abs(depth) * 0.002) ** 2 + angle_component ** 2)
            reason = ""
            if min_depth is not None and depth < float(min_depth):
                reason = "shallower_than_min_depth"
            elif max_depth is not None and depth > float(max_depth):
                reason = "deeper_than_max_depth"
            elif abs(angle) > float(angle_limit_deg):
                reason = "beam_angle_exceeds_limit"
            elif quality <= 0:
                reason = "quality_flag_rejected"
            elif abs(residual) > float(median_reject_m):
                reason = "cell_median_residual_exceeds_limit"
            elif tpu > allowed_tvu:
                reason = "tpu_exceeds_allowed_tvu"
            if reason:
                rejected += 1
                rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
            else:
                accepted += 1
                uncertainty_pass += 1
                accepted_cells.setdefault(key, []).append(rec)
            writer.writerow(
                {
                    "x": f"{x:.8f}",
                    "y": f"{y:.8f}",
                    "depth_m": f"{depth:.4f}",
                    "beam_number": f"{float(rec['beam_number']):.0f}",
                    "beam_angle_deg": f"{angle:.3f}",
                    "quality": f"{quality:.3f}",
                    "tpu_m": f"{tpu:.4f}",
                    "allowed_tvu_m": f"{allowed_tvu:.4f}",
                    "residual_to_cell_median_m": f"{residual:.4f}",
                    "accepted": "1" if not reason else "0",
                    "rejected_reason": reason,
                    "time_utc": rec.get("time_utc", ""),
                }
            )

    with grid_path.open("w", encoding="utf-8", newline="") as grid_file:
        writer = csv.writer(grid_file)
        writer.writerow(["x", "y", "mean_depth_m", "minimum_depth_m", "maximum_depth_m", "count", "density_pass"])
        for (ix, iy), values in sorted(accepted_cells.items()):
            depths = [float(item["depth_m"]) for item in values]
            density_pass = len(depths) >= int(density_required)
            if density_pass:
                density_pass_cells += 1
            writer.writerow(
                [
                    f"{(ix + 0.5) * cell:.3f}",
                    f"{(iy + 0.5) * cell:.3f}",
                    f"{statistics.mean(depths):.4f}",
                    f"{min(depths):.4f}",
                    f"{max(depths):.4f}",
                    len(depths),
                    "1" if density_pass else "0",
                ]
            )

    total_cells = len(accepted_cells)
    certification = {
        "processor": "HYDROGEOMATICS Internal MBES Processor",
        "created_utc": mbes_utc_stamp(),
        "input": str(input_path),
        "cleaned_csv": str(cleaned_path),
        "grid_csv": str(grid_path),
        "order": order,
        "order_a": HYDROGEOMATICS_TPU_ORDERS.get(str(order), (user_a, user_b))[0] if order != "User" else user_a,
        "order_b": HYDROGEOMATICS_TPU_ORDERS.get(str(order), (user_a, user_b))[1] if order != "User" else user_b,
        "cell_size": cell,
        "density_required_per_cell": int(density_required),
        "total_soundings": len(records),
        "accepted_soundings": accepted,
        "rejected_soundings": rejected,
        "accepted_ratio": accepted / max(len(records), 1),
        "grid_cells": total_cells,
        "density_pass_cells": density_pass_cells,
        "density_pass_ratio": density_pass_cells / max(total_cells, 1),
        "uncertainty_pass_soundings": uncertainty_pass,
        "rejected_reasons": rejected_reasons,
        "certification_result": "PASS" if accepted > 0 and total_cells > 0 and density_pass_cells == total_cells else "REVIEW",
        "limitations": [
            "CSV/ASCII beam decoder implemented. Native vendor binary decoders require official packet specifications or SDK access.",
            "TPU model is configurable and deterministic; verify constants against project authority requirements before certification use.",
            "Final certification should include independent crossline, coverage, datum, metadata, and deliverable review.",
        ],
    }
    cert_path.write_text(json.dumps(certification, indent=2), encoding="utf-8")
    return certification


def wgs84_tm_to_xy(
    latitude: float,
    longitude: float,
    central_meridian: float,
    scale_factor: float = 0.9996,
    false_easting: float = 500000.0,
    false_northing: float = 0.0,
) -> tuple[float, float]:
    a = 6378137.0
    f = 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    ep2 = e2 / (1.0 - e2)
    lat = math.radians(latitude)
    lon = math.radians(longitude)
    lon0 = math.radians(central_meridian)
    n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    t = math.tan(lat) ** 2
    c = ep2 * math.cos(lat) ** 2
    aa = math.cos(lat) * (lon - lon0)
    m = a * (
        (1.0 - e2 / 4.0 - 3.0 * e2 ** 2 / 64.0 - 5.0 * e2 ** 3 / 256.0) * lat
        - (3.0 * e2 / 8.0 + 3.0 * e2 ** 2 / 32.0 + 45.0 * e2 ** 3 / 1024.0) * math.sin(2.0 * lat)
        + (15.0 * e2 ** 2 / 256.0 + 45.0 * e2 ** 3 / 1024.0) * math.sin(4.0 * lat)
        - (35.0 * e2 ** 3 / 3072.0) * math.sin(6.0 * lat)
    )
    easting = false_easting + scale_factor * n * (
        aa
        + (1.0 - t + c) * aa ** 3 / 6.0
        + (5.0 - 18.0 * t + t ** 2 + 72.0 * c - 58.0 * ep2) * aa ** 5 / 120.0
    )
    northing = false_northing + scale_factor * (
        m
        + n
        * math.tan(lat)
        * (
            aa ** 2 / 2.0
            + (5.0 - t + 9.0 * c + 4.0 * c ** 2) * aa ** 4 / 24.0
            + (61.0 - 58.0 * t + t ** 2 + 600.0 * c - 330.0 * ep2) * aa ** 6 / 720.0
        )
    )
    return easting, northing


def lambert_conformal_conic_to_xy(
    latitude: float,
    longitude: float,
    central_meridian: float,
    latitude_origin: float,
    standard_parallel_1: Optional[float] = None,
    standard_parallel_2: Optional[float] = None,
    false_easting: float = 500000.0,
    false_northing: float = 0.0,
) -> tuple[float, float]:
    a = 6378137.0
    lat = math.radians(latitude)
    lon = math.radians(longitude)
    lon0 = math.radians(central_meridian)
    lat0 = math.radians(latitude_origin)
    sp1 = math.radians(standard_parallel_1 if standard_parallel_1 is not None else latitude_origin)
    sp2 = math.radians(standard_parallel_2 if standard_parallel_2 is not None else standard_parallel_1 if standard_parallel_1 is not None else latitude_origin)
    if abs(sp1 - sp2) < 1e-12:
        n = math.sin(sp1)
    else:
        n = math.log(math.cos(sp1) / max(math.cos(sp2), 1e-12)) / math.log(
            max(math.tan(math.pi / 4.0 + sp2 / 2.0), 1e-12) / max(math.tan(math.pi / 4.0 + sp1 / 2.0), 1e-12)
        )
    f_value = math.cos(sp1) * (math.tan(math.pi / 4.0 + sp1 / 2.0) ** n) / max(n, 1e-12)
    rho = a * f_value / max(math.tan(math.pi / 4.0 + lat / 2.0) ** n, 1e-12)
    rho0 = a * f_value / max(math.tan(math.pi / 4.0 + lat0 / 2.0) ** n, 1e-12)
    theta = n * (lon - lon0)
    return false_easting + rho * math.sin(theta), false_northing + rho0 - rho * math.cos(theta)


def lambert_conformal_conic_to_latlon(
    easting: float,
    northing: float,
    central_meridian: float,
    latitude_origin: float,
    standard_parallel_1: Optional[float] = None,
    standard_parallel_2: Optional[float] = None,
    false_easting: float = 500000.0,
    false_northing: float = 0.0,
) -> tuple[float, float]:
    a = 6378137.0
    lon0 = math.radians(central_meridian)
    lat0 = math.radians(latitude_origin)
    sp1 = math.radians(standard_parallel_1 if standard_parallel_1 is not None else latitude_origin)
    sp2 = math.radians(standard_parallel_2 if standard_parallel_2 is not None else standard_parallel_1 if standard_parallel_1 is not None else latitude_origin)
    if abs(sp1 - sp2) < 1e-12:
        n = math.sin(sp1)
    else:
        n = math.log(math.cos(sp1) / max(math.cos(sp2), 1e-12)) / math.log(
            max(math.tan(math.pi / 4.0 + sp2 / 2.0), 1e-12) / max(math.tan(math.pi / 4.0 + sp1 / 2.0), 1e-12)
        )
    f_value = math.cos(sp1) * (math.tan(math.pi / 4.0 + sp1 / 2.0) ** n) / max(n, 1e-12)
    rho0 = a * f_value / max(math.tan(math.pi / 4.0 + lat0 / 2.0) ** n, 1e-12)
    x = float(easting) - false_easting
    y = rho0 - (float(northing) - false_northing)
    rho = math.copysign(math.hypot(x, y), n)
    theta = math.atan2(x, y)
    lat = 2.0 * math.atan((a * f_value / max(abs(rho), 1e-12)) ** (1.0 / max(n, 1e-12))) - math.pi / 2.0
    lon = lon0 + theta / max(n, 1e-12)
    return math.degrees(lat), math.degrees(lon)


def utm_projection_preset(zone: int, hemisphere: str = "N") -> dict[str, object]:
    """Parameters for a fixed WGS84 UTM survey grid."""
    if not 1 <= zone <= 60 or hemisphere not in ("N", "S"):
        raise ValueError("Choose UTM zone 1-60 and hemisphere N or S.")
    return {
        "use_projection": True, "use_tm_projection": True,
        "use_lcc_projection": False, "datum_name": "WGS84",
        "ellipsoid": "WGS84", "utm_zone": str(zone),
        "fixed_utm_reference_zone": True, "lcc_zone": "",
        "central_meridian": zone * 6.0 - 183.0,
        "latitude_origin": 0.0, "reference_latitude": 0.0,
        "scale_factor": 0.9996, "false_easting": 500000.0,
        "false_northing": 10000000.0 if hemisphere == "S" else 0.0,
        "grid_name": "UTM South" if hemisphere == "S" else "UTM North",
        "zone_label": f"Zone {zone}{hemisphere}",
        "projection_method": "Transverse Mercator",
    }


def selected_utm_zone(geodetic: "GeodeticSettings", longitude: Optional[float] = None) -> Optional[int]:
    text = str(getattr(geodetic, "utm_zone", "") or "").strip()
    match = re.search(r"\d+", text)
    fixed_reference = bool(getattr(geodetic, "fixed_utm_reference_zone", False))
    if match and (fixed_reference or longitude is None):
        zone = int(match.group(0))
        if 1 <= zone <= 60:
            return zone
    if longitude is not None:
        return max(1, min(60, int((float(longitude) + 180.0) // 6.0) + 1))
    if match:
        zone = int(match.group(0))
        if 1 <= zone <= 60:
            return zone
    return None


def project_lat_lon_to_selected_xy(
    latitude: Optional[float],
    longitude: Optional[float],
    geodetic: "GeodeticSettings",
) -> tuple[Optional[float], Optional[float]]:
    if latitude is None or longitude is None:
        return None, None
    if not getattr(geodetic, "use_projection", True):
        return longitude, latitude
    if getattr(geodetic, "use_tm_projection", True):
        zone = selected_utm_zone(geodetic, longitude)
        central = float(getattr(geodetic, "central_meridian", 0.0) or 0.0)
        if zone is not None:
            central = zone * 6.0 - 183.0
        false_northing = float(getattr(geodetic, "false_northing", 0.0) or 0.0)
        if latitude < 0.0 and false_northing == 0.0:
            false_northing = 10000000.0
        return wgs84_tm_to_xy(
            float(latitude),
            float(longitude),
            central,
            float(getattr(geodetic, "scale_factor", 0.9996) or 0.9996),
            float(getattr(geodetic, "false_easting", 500000.0) or 500000.0),
            false_northing,
        )
    return longitude, latitude


def ensure_projected_xy(point: "SurveyPoint", geodetic: "GeodeticSettings") -> "SurveyPoint":
    if point.easting is None or point.northing is None:
        easting, northing = project_lat_lon_to_selected_xy(point.latitude, point.longitude, geodetic)
        if point.easting is None:
            point.easting = easting
        if point.northing is None:
            point.northing = northing
    return point


KTD_NODE_CACHE: dict[str, tuple[float, list[tuple[float, float, float]]]] = {}
KTD_GRID_META_CACHE: dict[str, tuple[float, dict[str, float | int | str]]] = {}
KTD_MATRIX_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
KTD_NODATA_VALUE = 999.99


def is_ktd_nodata(value: float) -> bool:
    return abs(float(value) - KTD_NODATA_VALUE) < 0.005


def load_ktd_grid_meta(path: Path) -> Optional[dict[str, float | int | str]]:
    cache_key = str(path)
    try:
        mtime = path.stat().st_mtime
        cached = KTD_GRID_META_CACHE.get(cache_key)
        if cached and cached[0] == mtime:
            return dict(cached[1])
    except Exception:
        mtime = 0.0
    if not path.exists():
        return None
    try:
        first_line = ""
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                first_line = raw_line.strip()
                if first_line:
                    break
        all_lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        first = [part for part in re.split(r"[,\t ]+", all_lines[0] if all_lines else first_line) if part]
        values = [parse_float(part) for part in first]
        if len(values) >= 6 and all(value is not None for value in values[:6]):
            first_count = int(values[0] or 0)
            second_count = int(values[1] or 0)
            if first_count > 1 and second_count > 1:
                # HYPACK KTD matrix files commonly store:
                # row_count column_count minX minY maxX maxY.
                # Treat this as the default so wrapped matrix rows still parse correctly.
                header_order = "rows_cols"
                y_count = first_count
                x_count = second_count
                meta: dict[str, float | int | str] = {
                    "x_count": x_count,
                    "y_count": y_count,
                    "min_x": float(values[2] or 0.0),
                    "min_y": float(values[3] or 0.0),
                    "max_x": float(values[4] or 0.0),
                    "max_y": float(values[5] or 0.0),
                    "row_order": "bottom_up",
                    "header_order": header_order,
                }
                KTD_GRID_META_CACHE[cache_key] = (mtime, dict(meta))
                return meta
    except Exception:
        return None
    return None


def load_ktd_nodes(path: Path) -> list[tuple[float, float, float]]:
    nodes: list[tuple[float, float, float]] = []
    if not path.exists():
        return nodes
    cache_key = str(path)
    try:
        mtime = path.stat().st_mtime
        cached = KTD_NODE_CACHE.get(cache_key)
        if cached and cached[0] == mtime:
            return list(cached[1])
    except Exception:
        mtime = 0.0

    raw_text = path.read_text(encoding="utf-8", errors="replace")
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if lines:
        first = [part for part in re.split(r"[,\t ]+", lines[0]) if part]
        header_values = [parse_float(part) for part in first]
        if (
            len(header_values) >= 6
            and all(value is not None for value in header_values[:6])
            and int(header_values[0] or 0) > 1
            and int(header_values[1] or 0) > 1
        ):
            first_count = int(header_values[0] or 0)
            second_count = int(header_values[1] or 0)
            min_x = float(header_values[2] or 0.0)
            min_y = float(header_values[3] or 0.0)
            max_x = float(header_values[4] or min_x)
            max_y = float(header_values[5] or min_y)
            data_lines = lines[1:]
            header_order = "rows_cols"
            y_count = first_count
            x_count = second_count
            dx = (max_x - min_x) / max(x_count - 1, 1)
            dy = (max_y - min_y) / max(y_count - 1, 1)
            KTD_GRID_META_CACHE[cache_key] = (
                mtime,
                {
                    "x_count": x_count,
                    "y_count": y_count,
                    "min_x": min_x,
                    "min_y": min_y,
                    "max_x": max_x,
                    "max_y": max_y,
                    "row_order": "bottom_up",
                    "header_order": header_order,
                },
            )
            value_tokens = [token for token in re.split(r"[,\t\s]+", "\n".join(data_lines).strip()) if token]
            limit = min(len(value_tokens), x_count * y_count)
            for index in range(limit):
                value = parse_float(value_tokens[index])
                if value is None or is_ktd_nodata(value):
                    continue
                row = index // x_count
                col = index % x_count
                nodes.append((min_x + col * dx, min_y + row * dy, float(value)))
            KTD_NODE_CACHE[cache_key] = (mtime, list(nodes))
            return nodes

    for line in lines:
        if line.startswith("#") or line.startswith(";"):
            continue
        parts = [part for part in re.split(r"[,\t ]+", line) if part]
        if parts and parts[0].upper() in {"KTD", "NODE", "XYZ"}:
            parts = parts[1:]
        if len(parts) < 3:
            continue
        x = parse_float(parts[0])
        y = parse_float(parts[1])
        value = parse_float(parts[2])
        if x is not None and y is not None and value is not None and not is_ktd_nodata(value):
            nodes.append((float(x), float(y), float(value)))
    KTD_NODE_CACHE[cache_key] = (mtime, list(nodes))
    return nodes



def load_ktd_matrix(path: Path) -> Optional[dict[str, object]]:
    cache_key = str(path)
    try:
        mtime = path.stat().st_mtime
        cached = KTD_MATRIX_CACHE.get(cache_key)
        if cached and cached[0] == mtime:
            return dict(cached[1])
    except Exception:
        mtime = 0.0
    meta = load_ktd_grid_meta(path)
    if not meta:
        return None
    x_count = int(meta.get("x_count", 0) or 0)
    y_count = int(meta.get("y_count", 0) or 0)
    if x_count <= 1 or y_count <= 1:
        return None
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except Exception:
        return None
    data_lines = lines[1:]
    rows: list[list[Optional[float]]] = []
    tokens = [token for token in re.split(r"[,\t\s]+", "\n".join(data_lines).strip()) if token]
    source_rows = [" ".join(tokens[row * x_count : (row + 1) * x_count]) for row in range(y_count)]
    for raw_row in source_rows:
        row_values: list[Optional[float]] = []
        for token in [part for part in re.split(r"[,\t ]+", raw_row) if part][:x_count]:
            value = parse_float(token)
            row_values.append(None if value is None else float(value))
        while len(row_values) < x_count:
            row_values.append(None)
        rows.append(row_values)
    while len(rows) < y_count:
        rows.append([None] * x_count)
    matrix: dict[str, object] = {"meta": dict(meta), "rows": rows}
    KTD_MATRIX_CACHE[cache_key] = (mtime, matrix)
    return dict(matrix)


def ktd_cell_value_at(path_text: str, easting: Optional[float], northing: Optional[float]) -> Optional[float]:
    path_text = str(path_text or "").strip()
    if not path_text or easting is None or northing is None:
        return None
    matrix = load_ktd_matrix(Path(path_text))
    if not matrix:
        return ktd_value_at(path_text, easting, northing)
    meta = matrix.get("meta", {})
    rows = matrix.get("rows", [])
    if not isinstance(meta, dict) or not isinstance(rows, list):
        return None
    x_count = int(meta.get("x_count", 0) or 0)
    y_count = int(meta.get("y_count", 0) or 0)
    min_x = float(meta.get("min_x", 0.0) or 0.0)
    min_y = float(meta.get("min_y", 0.0) or 0.0)
    max_x = float(meta.get("max_x", min_x) or min_x)
    max_y = float(meta.get("max_y", min_y) or min_y)
    if x_count <= 1 or y_count <= 1:
        return None
    dx = (max_x - min_x) / max(x_count - 1, 1)
    dy = (max_y - min_y) / max(y_count - 1, 1)
    col = int(round((float(easting) - min_x) / max(dx, 1e-12)))
    row = int(round((float(northing) - min_y) / max(dy, 1e-12)))
    if col < 0 or col >= x_count or row < 0 or row >= y_count:
        return None
    try:
        value = rows[row][col]
        if value is None or is_ktd_nodata(float(value)):
            return None
        return float(value)
    except Exception:
        return None


def apply_ktd_rtk_postprocess_to_point(point: "SurveyPoint", geodetic: "GeodeticSettings") -> bool:
    ell_h = getattr(point, "altitude_m", None)
    if ell_h is None:
        return False
    k_value = ktd_cell_value_at(getattr(geodetic, "ktd_file_path", ""), point.x, point.y)
    if k_value is None:
        return False
    reduced_height = float(ell_h) - float(k_value)
    point.ktd_cell_k = float(k_value)
    point.rtk_reduced_height = reduced_height
    point.tide = reduced_height
    if point.raw_depth is not None:
        speed_factor = point.sound_speed / DEFAULT_SOUND_SPEED if point.sound_speed else 1.0
        point.rtk_reduced_depth = point.raw_depth * speed_factor + point.draft - reduced_height
    else:
        point.rtk_reduced_depth = None
    return True


def save_ktd_nodes(
    path: Path,
    nodes: list[tuple[float, float, float]],
    mode: str = "K",
    grid_meta: Optional[dict[str, float | int | str]] = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value_by_xy = {(round(float(x), 3), round(float(y), 3)): float(value) for x, y, value in nodes}
    if grid_meta:
        x_count = int(grid_meta.get("x_count", 0) or 0)
        y_count = int(grid_meta.get("y_count", 0) or 0)
        min_x = float(grid_meta.get("min_x", 0.0) or 0.0)
        min_y = float(grid_meta.get("min_y", 0.0) or 0.0)
        max_x = float(grid_meta.get("max_x", min_x) or min_x)
        max_y = float(grid_meta.get("max_y", min_y) or min_y)
        if x_count > 1 and y_count > 1:
            dx = (max_x - min_x) / max(x_count - 1, 1)
            dy = (max_y - min_y) / max(y_count - 1, 1)
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                if str(grid_meta.get("header_order", "rows_cols")) == "cols_rows":
                    handle.write(f"{x_count} {y_count} {min_x:.2f} {min_y:.2f} {max_x:.2f} {max_y:.2f}\n")
                else:
                    handle.write(f"{y_count} {x_count} {min_x:.2f} {min_y:.2f} {max_x:.2f} {max_y:.2f}\n")
                row_values: list[str] = []
                for row in range(y_count):
                    y = round(min_y + row * dy, 3)
                    for col in range(x_count):
                        x = round(min_x + col * dx, 3)
                        value = value_by_xy.get((x, y), KTD_NODATA_VALUE)
                        row_values.append(f"{value:.2f}")
                    handle.write(" ".join(row_values) + "\n")
                    row_values = []
            KTD_NODE_CACHE.pop(str(path), None)
            KTD_GRID_META_CACHE.pop(str(path), None)
            KTD_MATRIX_CACHE.pop(str(path), None)
            return

    unique_x = sorted({round(float(x), 3) for x, _y, _value in nodes})
    unique_y = sorted({round(float(y), 3) for _x, y, _value in nodes})
    can_write_grid = len(unique_x) >= 2 and len(unique_y) >= 2 and len(unique_x) * len(unique_y) <= max(len(nodes) * 2, len(nodes) + 20)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        if can_write_grid:
            handle.write(f"{len(unique_y)} {len(unique_x)} {unique_x[0]:.2f} {unique_y[0]:.2f} {unique_x[-1]:.2f} {unique_y[-1]:.2f}\n")
            row_values: list[str] = []
            for y in unique_y:
                for x in unique_x:
                    value = value_by_xy.get((x, y), KTD_NODATA_VALUE)
                    row_values.append(f"{value:.2f}")
                    if len(row_values) >= 12:
                        handle.write(" ".join(row_values) + "\n")
                        row_values = []
            if row_values:
                handle.write(" ".join(row_values) + "\n")
        else:
            handle.write("# HYDROGEOMATICS KTD FILE\n")
            handle.write("# Columns: EASTING NORTHING VALUE\n")
            handle.write(f"# VALUE_MODE {mode}\n")
            handle.write("# If using a geoid model, VALUE is K. Without geoid, VALUE can be K-N.\n")
            for x, y, value in nodes:
                handle.write(f"KTD {float(x):.3f} {float(y):.3f} {float(value):.4f}\n")
    try:
        KTD_NODE_CACHE.pop(str(path), None)
        KTD_GRID_META_CACHE.pop(str(path), None)
        KTD_MATRIX_CACHE.pop(str(path), None)
    except Exception:
        pass


def ktd_value_at(path_text: str, easting: Optional[float], northing: Optional[float]) -> Optional[float]:
    path_text = str(path_text or "").strip()
    if not path_text:
        return None
    nodes = load_ktd_nodes(Path(path_text))
    if not nodes:
        return None
    if easting is None or northing is None:
        return sum(value for _x, _y, value in nodes) / max(len(nodes), 1)
    x0 = float(easting)
    y0 = float(northing)
    distances: list[tuple[float, float]] = []
    for x, y, value in nodes:
        distance_sq = (x - x0) ** 2 + (y - y0) ** 2
        if distance_sq <= 1e-12:
            return value
        distances.append((distance_sq, value))
    distances.sort(key=lambda item: item[0])
    selected = distances[: min(6, len(distances))]
    weight_sum = 0.0
    value_sum = 0.0
    for distance_sq, value in selected:
        weight = 1.0 / max(distance_sq, 1e-9)
        weight_sum += weight
        value_sum += value * weight
    return value_sum / weight_sum if weight_sum else None


def rtk_tide_offset(
    geodetic: "GeodeticSettings",
    easting: Optional[float] = None,
    northing: Optional[float] = None,
) -> Optional[float]:
    method = str(getattr(geodetic, "rtk_tide_method", "Not using RTK tide") or "Not using RTK tide")
    if method == "Not using RTK tide":
        return None
    n_value = float(getattr(geodetic, "geoid_separation_n", 0.0) or 0.0)
    ktd_value = ktd_value_at(getattr(geodetic, "ktd_file_path", ""), easting, northing)
    if method == "(K-N) from user value":
        return float(getattr(geodetic, "user_k_minus_n", 0.0) or 0.0)
    if method == "(K-N) from KTD file":
        return ktd_value if ktd_value is not None else float(getattr(geodetic, "ktd_k_value", 0.0) or 0.0) - n_value
    if method == "N from geoid model, K from KTD file":
        k_value = ktd_value if ktd_value is not None else float(getattr(geodetic, "ktd_k_value", 0.0) or 0.0)
        return k_value - n_value
    if method == "N from geoid model, K from VDatum":
        return float(getattr(geodetic, "vdatum_k_value", 0.0) or 0.0) - n_value
    if method == "N from geoid model, K from user value":
        return float(getattr(geodetic, "user_k_value", 0.0) or 0.0) - n_value
    return None


def compute_rtk_tide_from_ellipsoidal_height(
    ellipsoidal_height: Optional[float],
    geodetic: "GeodeticSettings",
    easting: Optional[float] = None,
    northing: Optional[float] = None,
) -> Optional[float]:
    if ellipsoidal_height is None:
        return None
    offset = rtk_tide_offset(geodetic, easting, northing)
    if offset is None:
        return None
    reference_height = float(getattr(geodetic, "ellipsoidal_height_reference", 0.0) or 0.0)
    chart_datum = float(getattr(geodetic, "chart_datum_level", 0.0) or 0.0)
    return float(ellipsoidal_height) - reference_height - offset - chart_datum


@dataclass
class SurveyPoint:
    time: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    easting: Optional[float] = None
    northing: Optional[float] = None
    raw_depth: Optional[float] = None
    tide: float = 0.0
    draft: float = 0.0
    sound_speed: float = DEFAULT_SOUND_SPEED
    quality: str = ""
    source: str = ""
    speed_knots: Optional[float] = None
    course_degrees: Optional[float] = None
    heading_degrees: Optional[float] = None
    altitude_m: Optional[float] = None
    ktd_cell_k: Optional[float] = None
    rtk_reduced_height: Optional[float] = None
    rtk_reduced_depth: Optional[float] = None

    @property
    def corrected_depth(self) -> Optional[float]:
        if self.raw_depth is None:
            return None
        speed_factor = self.sound_speed / DEFAULT_SOUND_SPEED if self.sound_speed else 1.0
        if self.rtk_reduced_depth is not None:
            return self.rtk_reduced_depth
        return self.raw_depth * speed_factor + self.draft - self.tide

    @property
    def x(self) -> Optional[float]:
        return self.easting if self.easting is not None else self.longitude

    @property
    def y(self) -> Optional[float]:
        return self.northing if self.northing is not None else self.latitude


@dataclass
class RunLine:
    name: str
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    enabled: bool = True

    @property
    def dx(self) -> float:
        return self.end_x - self.start_x

    @property
    def dy(self) -> float:
        return self.end_y - self.start_y

    @property
    def length(self) -> float:
        return math.hypot(self.dx, self.dy)

    @property
    def bearing_degrees(self) -> float:
        return (math.degrees(math.atan2(self.dx, self.dy)) + 360.0) % 360.0

    def point_at(self, distance: float) -> tuple[float, float]:
        length = self.length
        if length == 0.0:
            return self.start_x, self.start_y
        scale = distance / length
        return self.start_x + self.dx * scale, self.start_y + self.dy * scale

    def navigation(self, x: float, y: float) -> "NavigationSolution":
        length = self.length
        if length == 0.0:
            distance = math.hypot(x - self.start_x, y - self.start_y)
            return NavigationSolution(self, 0.0, 0.0, distance, distance, "ON", self.start_x, self.start_y)
        ux = self.dx / length
        uy = self.dy / length
        vx = x - self.start_x
        vy = y - self.start_y
        along = vx * ux + vy * uy
        cross = vx * uy - vy * ux
        nearest_distance = min(max(along, 0.0), length)
        nearest_x, nearest_y = self.point_at(nearest_distance)
        distance_to_start = math.hypot(x - self.start_x, y - self.start_y)
        distance_to_end = math.hypot(x - self.end_x, y - self.end_y)
        side = "RIGHT" if cross > 0.0 else "LEFT" if cross < 0.0 else "ON"
        return NavigationSolution(
            line=self,
            along_track=along,
            cross_track=cross,
            distance_to_start=distance_to_start,
            distance_to_end=distance_to_end,
            side=side,
            nearest_x=nearest_x,
            nearest_y=nearest_y,
        )


@dataclass
class NavigationSolution:
    line: RunLine
    along_track: float
    cross_track: float
    distance_to_start: float
    distance_to_end: float
    side: str
    nearest_x: float
    nearest_y: float

    @property
    def absolute_cross_track(self) -> float:
        return abs(self.cross_track)

    @property
    def percent_complete(self) -> float:
        length = self.line.length
        if length == 0.0:
            return 0.0
        return max(0.0, min(100.0, self.along_track / length * 100.0))


class RunLineSet:
    def __init__(self) -> None:
        self.lines: list[RunLine] = []

    def clear(self) -> None:
        self.lines.clear()

    def add(self, line: RunLine) -> None:
        self.lines.append(line)

    def nearest(self, x: float, y: float) -> Optional[NavigationSolution]:
        solutions = [line.navigation(x, y) for line in self.lines if line.enabled]
        if not solutions:
            return None
        return min(solutions, key=lambda item: item.absolute_cross_track)

    def generate_parallel(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        spacing: float,
        count_each_side: int,
        prefix: str = "RL",
        include_center: bool = True,
    ) -> None:
        base_dx = end_x - start_x
        base_dy = end_y - start_y
        base_len = math.hypot(base_dx, base_dy)
        if base_len == 0.0:
            raise ValueError("Runline baseline has zero length")
        nx = -base_dy / base_len
        ny = base_dx / base_len
        offsets: list[int] = []
        if include_center:
            offsets.append(0)
        for index in range(1, count_each_side + 1):
            offsets.extend([index, -index])
        for offset_index in offsets:
            offset = offset_index * spacing
            label = f"{prefix}{offset_index:+03d}" if offset_index else f"{prefix}000"
            self.add(
                RunLine(
                    name=label,
                    start_x=start_x + nx * offset,
                    start_y=start_y + ny * offset,
                    end_x=end_x + nx * offset,
                    end_y=end_y + ny * offset,
                )
            )

    def load_csv(self, path: Path) -> int:
        before = len(self.lines)
        if path.suffix.lower() == ".lnw":
            for line in load_hypack_lnw(path):
                self.add(line)
            return len(self.lines) - before
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
            reader = csv.DictReader(handle, dialect=dialect)
            if reader.fieldnames:
                for row in reader:
                    line = runline_from_row(row)
                    if line is not None:
                        self.add(line)
                return len(self.lines) - before
            handle.seek(0)
            for row in csv.reader(handle, dialect=dialect):
                line = runline_from_sequence(row)
                if line is not None:
                    self.add(line)
        return len(self.lines) - before

    def export_csv(self, path: Path) -> int:
        if path.suffix.lower() == ".lnw":
            return write_hypack_lnw(path, self.lines)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "name",
                    "start_x",
                    "start_y",
                    "end_x",
                    "end_y",
                    "length",
                    "bearing_degrees",
                    "enabled",
                ],
            )
            writer.writeheader()
            for line in self.lines:
                writer.writerow(
                    {
                        "name": line.name,
                        "start_x": f"{line.start_x:.3f}",
                        "start_y": f"{line.start_y:.3f}",
                        "end_x": f"{line.end_x:.3f}",
                        "end_y": f"{line.end_y:.3f}",
                        "length": f"{line.length:.3f}",
                        "bearing_degrees": f"{line.bearing_degrees:.3f}",
                        "enabled": int(line.enabled),
                    }
                )
        return len(self.lines)


class SurveyModel:
    def __init__(self) -> None:
        self.points: list[SurveyPoint] = []
        self.tide = 0.0
        self.draft = 0.0
        self.sound_speed = DEFAULT_SOUND_SPEED

    def clear(self) -> None:
        self.points.clear()

    def apply_offsets(self) -> None:
        for point in self.points:
            point.tide = self.tide
            point.draft = self.draft
            point.sound_speed = self.sound_speed

    def add(self, point: SurveyPoint) -> None:
        if point.tide == 0.0:
            point.tide = self.tide
        if point.draft == 0.0:
            point.draft = self.draft
        if not point.sound_speed:
            point.sound_speed = self.sound_speed
        self.points.append(point)

    def load_file(self, path: Path) -> int:
        before = len(self.points)
        suffix = path.suffix.lower()
        preserves_hypack_corrections = False
        if is_hypack_raw_file(path):
            preserves_hypack_corrections = True
            for point in load_hypack_raw_points(path):
                self.add(point)
        elif is_hypack_edited_file(path):
            preserves_hypack_corrections = True
            for point in load_hypack_edited_points(path):
                self.add(point)
        elif suffix in {".xyz", ".txt"}:
            self._load_xyz(path)
        else:
            self._load_csv(path)
        if not preserves_hypack_corrections:
            self.apply_offsets()
        return len(self.points) - before

    def _load_xyz(self, path: Path) -> None:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.replace(",", " ").split()
                if len(parts) < 3:
                    continue
                try:
                    x, y, depth = map(float, parts[:3])
                except ValueError:
                    continue
                self.add(
                    SurveyPoint(
                        easting=x,
                        northing=y,
                        raw_depth=depth,
                        source=f"{path.name}:{line_no}",
                    )
                )

    def _load_csv(self, path: Path) -> None:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
            reader = csv.DictReader(handle, dialect=dialect)
            if reader.fieldnames:
                for row_no, row in enumerate(reader, 2):
                    point = point_from_row(row)
                    point.source = f"{path.name}:{row_no}"
                    if has_position_or_depth(point):
                        self.add(point)
                return

            handle.seek(0)
            for row_no, row in enumerate(csv.reader(handle, dialect=dialect), 1):
                point = point_from_sequence(row)
                point.source = f"{path.name}:{row_no}"
                if has_position_or_depth(point):
                    self.add(point)

    def export_xyz(self, path: Path) -> int:
        count = 0
        with path.open("w", encoding="utf-8", newline="") as handle:
            for point in self.points:
                z = point.corrected_depth
                if point.x is None or point.y is None or z is None:
                    continue
                handle.write(f"{point.x:.6f} {point.y:.6f} {z:.3f}\n")
                count += 1
        return count

    def export_csv(self, path: Path) -> int:
        fields = [
            "time",
            "latitude",
            "longitude",
            "easting",
            "northing",
            "raw_depth",
            "tide",
            "draft",
            "sound_speed",
            "corrected_depth",
            "altitude_m",
            "ktd_cell_k",
            "rtk_reduced_height",
            "rtk_reduced_depth",
            "quality",
            "source",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for point in self.points:
                writer.writerow(
                    {
                        "time": point.time,
                        "latitude": point.latitude,
                        "longitude": point.longitude,
                        "easting": point.easting,
                        "northing": point.northing,
                        "raw_depth": point.raw_depth,
                        "tide": point.tide,
                        "draft": point.draft,
                        "sound_speed": point.sound_speed,
                        "corrected_depth": point.corrected_depth,
                        "altitude_m": point.altitude_m,
                        "ktd_cell_k": point.ktd_cell_k,
                        "rtk_reduced_height": point.rtk_reduced_height,
                        "rtk_reduced_depth": point.rtk_reduced_depth,
                        "quality": point.quality,
                        "source": point.source,
                    }
                )
        return len(self.points)

    def export_hypack_raw(self, path: Path) -> int:
        count = 0
        now = datetime.now()
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("FTP NEW 2\n")
            handle.write("VER HYDROGEOMATICS-SBES-0607\n")
            handle.write('INF "" "" "" "" 1.000000 0.000000 0.000000\n')
            handle.write("ELL WGS-84 6378137.000 298.257223563\n")
            handle.write("PRO TME -63.000000 0.999600 0.000000 0.000000 0.000000 500000.0000 0.0000\n")
            handle.write("DTM 0.00 0.00 0.00 0.00000 0.00000 0.00000 0.00000\n")
            handle.write("GEO WGS84 0.000\n")
            handle.write("HVU 1.0000000000 1.0000000000\n")
            handle.write(f"TND {now:%H:%M:%S} {now:%m/%d/%Y} 300\n")
            handle.write('DEV 0 15 "GNSS" 1 ""\n')
            handle.write("OFF 0 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n")
            handle.write('DEV 1 16 "SBES" 1 ""\n')
            handle.write("OFF 1 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n")
            handle.write("EOH\n")
            for index, point in enumerate(self.points, 1):
                timestamp = display_time_to_hypack(point.time, index)
                if point.x is not None and point.y is not None:
                    handle.write(f"POS 0 {timestamp:.3f} {float(point.x):.3f} {float(point.y):.3f}\n")
                if point.tide:
                    handle.write(f"TID 0 {timestamp:.3f} {float(point.tide):.3f}\n")
                if point.raw_depth is not None:
                    handle.write(f"EC1 1 {timestamp:.3f} {float(point.raw_depth):.3f}\n")
                    count += 1
                if point.heading_degrees is not None:
                    handle.write(f"GYR 0 {timestamp:.3f} {float(point.heading_degrees):.3f}\n")
            handle.write("EOX\n")
        return count

    def stats(self) -> dict[str, float | int | None]:
        depths = [p.corrected_depth for p in self.points if p.corrected_depth is not None]
        if not depths:
            return {"count": len(self.points), "min": None, "max": None, "mean": None}
        return {
            "count": len(self.points),
            "min": min(depths),
            "max": max(depths),
            "mean": statistics.fmean(depths),
        }


class EchogramModel:
    def __init__(self, max_depth: Optional[float] = None, depth_bins: int = 160) -> None:
        self.max_depth = max_depth
        self.depth_bins = depth_bins
        self.pings: list[list[int]] = []
        self.bottom_depths: list[Optional[float]] = []

    def build_from_points(self, points: list[SurveyPoint]) -> None:
        depths = [p.corrected_depth for p in points if p.corrected_depth is not None]
        if not depths:
            self.pings = []
            self.bottom_depths = []
            return
        max_depth = self.max_depth or max(depths) * 1.15 or 1.0
        self.pings = []
        self.bottom_depths = []
        for point in points:
            depth = point.corrected_depth
            self.bottom_depths.append(depth)
            self.pings.append(synthetic_ping(depth, max_depth, self.depth_bins))

    def load_matrix_csv(self, path: Path) -> int:
        self.pings = []
        self.bottom_depths = []
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.reader(handle)
            for row in reader:
                values = [parse_float(item) for item in row]
                samples = [int(max(0, min(255, value))) for value in values if value is not None]
                if samples:
                    self.pings.append(samples)
                    self.bottom_depths.append(None)
        if self.pings:
            self.depth_bins = max(len(ping) for ping in self.pings)
        return len(self.pings)

    def export_ppm(self, path: Path) -> int:
        if not self.pings:
            raise ValueError("No echogram pings available")
        width = len(self.pings)
        height = max(len(ping) for ping in self.pings)
        with path.open("w", encoding="ascii", newline="\n") as handle:
            handle.write(f"P3\n{width} {height}\n255\n")
            for y in range(height):
                row: list[str] = []
                for ping in self.pings:
                    value = ping[y] if y < len(ping) else 0
                    row.extend(str(channel) for channel in waterfall_color(value))
                handle.write(" ".join(row) + "\n")
        return width


def synthetic_ping(depth: Optional[float], max_depth: float, bins: int) -> list[int]:
    if depth is None or max_depth <= 0.0:
        return [0] * bins
    bottom = max(0, min(bins - 1, int(depth / max_depth * (bins - 1))))
    samples: list[int] = []
    for index in range(bins):
        surface = max(0, 45 - index * 2)
        bottom_echo = int(235 * math.exp(-((index - bottom) / 2.8) ** 2))
        sub_bottom = int(90 * math.exp(-((index - bottom - 8) / 7.0) ** 2)) if index > bottom else 0
        texture = int(18 * (1.0 + math.sin(index * 0.37 + depth * 0.19)))
        samples.append(max(0, min(255, surface + bottom_echo + sub_bottom + texture)))
    return samples


def waterfall_color(value: int) -> tuple[int, int, int]:
    value = max(0, min(255, int(value)))
    if value < 64:
        return 0, value * 2, 64 + value * 2
    if value < 128:
        return 0, 128 + (value - 64) * 2, 255 - (value - 64)
    if value < 192:
        return (value - 128) * 4, 255, 64
    return 255, 255 - (value - 192) * 3, max(0, 64 - (value - 192))


def has_position_or_depth(point: SurveyPoint) -> bool:
    return point.raw_depth is not None or point.x is not None or point.y is not None


def first_value(row: dict[str, str], names: Iterable[str]) -> str:
    normalized = {clean_key(k): v for k, v in row.items() if k is not None}
    for name in names:
        value = normalized.get(clean_key(name), "")
        if value not in (None, ""):
            return str(value).strip()
    return ""


def clean_key(key: str) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def parse_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def point_from_row(row: dict[str, str]) -> SurveyPoint:
    raw_depth = parse_float(
        first_value(row, ["depth", "raw_depth", "rawdepth", "sound", "sounding", "z"])
    )
    latitude = parse_float(first_value(row, ["lat", "latitude"]))
    longitude = parse_float(first_value(row, ["lon", "long", "longitude"]))
    easting = parse_float(first_value(row, ["x", "east", "easting"]))
    northing = parse_float(first_value(row, ["y", "north", "northing"]))
    tide = parse_float(first_value(row, ["tide", "waterlevel", "water_level"]))
    draft = parse_float(first_value(row, ["draft", "draught", "transducerdraft"]))
    sv = parse_float(first_value(row, ["sv", "svp", "soundspeed", "sound_speed"]))
    return SurveyPoint(
        time=first_value(row, ["time", "date", "datetime", "timestamp"]),
        latitude=latitude,
        longitude=longitude,
        easting=easting,
        northing=northing,
        raw_depth=raw_depth,
        tide=tide if tide is not None else 0.0,
        draft=draft if draft is not None else 0.0,
        sound_speed=sv if sv is not None else DEFAULT_SOUND_SPEED,
        quality=first_value(row, ["quality", "fix", "status"]),
    )


def point_from_sequence(row: list[str]) -> SurveyPoint:
    values = [parse_float(value) for value in row]
    nums = [value for value in values if value is not None]
    if len(nums) >= 3:
        return SurveyPoint(easting=nums[0], northing=nums[1], raw_depth=nums[2])
    if len(nums) == 2:
        return SurveyPoint(easting=nums[0], northing=nums[1])
    if len(nums) == 1:
        return SurveyPoint(raw_depth=nums[0])
    return SurveyPoint()


def runline_from_row(row: dict[str, str]) -> Optional[RunLine]:
    name = first_value(row, ["name", "line", "runline", "id"]) or "RUNLINE"
    start_x = parse_float(first_value(row, ["start_x", "startx", "x1", "sx", "from_x"]))
    start_y = parse_float(first_value(row, ["start_y", "starty", "y1", "sy", "from_y"]))
    end_x = parse_float(first_value(row, ["end_x", "endx", "x2", "ex", "to_x"]))
    end_y = parse_float(first_value(row, ["end_y", "endy", "y2", "ey", "to_y"]))
    enabled_text = first_value(row, ["enabled", "active", "use"])
    enabled = enabled_text.lower() not in {"0", "false", "no", "off"}
    if None in {start_x, start_y, end_x, end_y}:
        return None
    return RunLine(name, float(start_x), float(start_y), float(end_x), float(end_y), enabled)


def runline_from_sequence(row: list[str]) -> Optional[RunLine]:
    cleaned = [item.strip() for item in row if item.strip()]
    if len(cleaned) < 4:
        return None
    if len(cleaned) >= 5:
        name = cleaned[0]
        values = [parse_float(item) for item in cleaned[1:5]]
    else:
        name = "RUNLINE"
        values = [parse_float(item) for item in cleaned[:4]]
    if any(value is None for value in values):
        return None
    return RunLine(name, float(values[0]), float(values[1]), float(values[2]), float(values[3]))



def hypack_time_to_display(value: str) -> str:
    parsed = parse_float(value)
    if parsed is None:
        return str(value).strip()
    seconds = float(parsed)
    hours = int(seconds // 3600) % 24
    minutes = int((seconds % 3600) // 60)
    secs = seconds - hours * 3600 - minutes * 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def display_time_to_hypack(value: str, fallback_index: int = 0) -> float:
    text = str(value or "").strip()
    parts = text.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600.0 + int(parts[1]) * 60.0 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60.0 + float(parts[1])
        if text:
            return float(text)
    except ValueError:
        pass
    return float(fallback_index)


def load_hypack_raw_points(path: Path) -> list[SurveyPoint]:
    points: list[SurveyPoint] = []
    last_position: Optional[SurveyPoint] = None
    last_tide = 0.0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line:
                continue
            parts = line.replace(",", " ").split()
            if not parts:
                continue
            tag = parts[0].upper()
            if tag == "TID" and len(parts) >= 4:
                tide = parse_float(parts[3])
                if tide is not None:
                    last_tide = tide
                    if last_position is not None:
                        last_position.tide = tide
                continue
            if tag == "POS" and len(parts) >= 5:
                point = SurveyPoint(
                    time=hypack_time_to_display(parts[2]),
                    easting=parse_float(parts[3]),
                    northing=parse_float(parts[4]),
                    tide=last_tide,
                    source=f"{path.name}:{line_no}",
                )
                points.append(point)
                last_position = point
                continue
            if tag in {"EC1", "EC2", "DEP", "RAW"}:
                if tag == "RAW" and "$" in line:
                    sentence = line[line.find("$"):]
                    parsed = parse_nmea_sentence(sentence)
                    if parsed is None:
                        continue
                    parsed.source = f"{path.name}:{line_no}"
                    parsed.time = parsed.time or hypack_time_to_display(parts[2] if len(parts) > 2 else "")
                    if parsed.latitude is not None and parsed.longitude is not None:
                        ensure_projected_xy(parsed, GeodeticSettings())
                        parsed.tide = last_tide
                        points.append(parsed)
                        last_position = parsed
                    elif parsed.raw_depth is not None:
                        point = parsed
                        point.tide = last_tide
                        if last_position is not None:
                            point.easting = last_position.easting
                            point.northing = last_position.northing
                            point.latitude = last_position.latitude
                            point.longitude = last_position.longitude
                            point.altitude_m = last_position.altitude_m
                            point.quality = last_position.quality
                        points.append(point)
                    continue
                if tag == "RAW" and len(parts) >= 3:
                    depth = parse_float(parts[2])
                    time_text = parts[1]
                elif len(parts) >= 4:
                    depth = parse_float(parts[3])
                    time_text = parts[2]
                else:
                    continue
                if depth is None:
                    continue
                if last_position is not None and last_position.raw_depth is None:
                    last_position.raw_depth = depth
                    last_position.time = last_position.time or hypack_time_to_display(time_text)
                    last_position.tide = last_tide
                else:
                    point = SurveyPoint(
                        time=hypack_time_to_display(time_text),
                        raw_depth=depth,
                        tide=last_tide,
                        source=f"{path.name}:{line_no}",
                    )
                    if last_position is not None:
                        point.easting = last_position.easting
                        point.northing = last_position.northing
                        point.latitude = last_position.latitude
                        point.longitude = last_position.longitude
                        point.altitude_m = last_position.altitude_m
                    points.append(point)
    return points


def load_hypack_edited_points(path: Path) -> list[SurveyPoint]:
    points: list[SurveyPoint] = []
    in_data = False
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line:
                continue
            if line.upper().startswith("EOH"):
                in_data = True
                continue
            if not in_data and re.match(r"^[A-Z]{2,4}\b", line, re.I):
                continue
            row = [item.strip() for item in line.split(",")]
            if len(row) < 5:
                continue
            x = parse_float(row[1])
            y = parse_float(row[2])
            depth = parse_float(row[4])
            if x is None or y is None or depth is None:
                continue
            tide = parse_float(row[10]) if len(row) > 10 else None
            points.append(
                SurveyPoint(
                    time=row[3],
                    easting=x,
                    northing=y,
                    raw_depth=depth,
                    tide=tide or 0.0,
                    quality=row[0],
                    source=f"{path.name}:{line_no}",
                )
            )
    return points


def load_hypack_tide_file(path: Path) -> list[tuple[int, float]]:
    """Load simple HYPACK-style tide files plus manual TID/TDX date,time,height rows."""
    values: list[tuple[int, float]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            text = line.strip()
            if not text or text.startswith("#") or text.startswith(";"):
                continue
            lowered = text.lower()
            if lowered.startswith("date") or lowered.startswith("time"):
                continue
            parts = [part for part in re.split(r"[,\t ]+", text) if part]
            if not parts:
                continue
            if parts[0].upper() == "TID" and len(parts) >= 4:
                value = parse_float(parts[3])
            else:
                numeric = [parse_float(part) for part in parts]
                numeric = [value for value in numeric if value is not None]
                value = numeric[-1] if numeric else parse_float(text)
            if value is None:
                continue
            # Legacy one-number HYPACK tide rows are commonly stored as centimeters.
            if len(parts) == 1 and abs(value) > 50:
                value = value / 100.0
            values.append((len(values), float(value)))
    return values


def load_hypack_lnw(path: Path) -> list[RunLine]:
    lines: list[RunLine] = []
    current_points: list[tuple[float, float]] = []
    current_name = ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split()
            tag = parts[0].upper()
            if tag == "LIN":
                current_points = []
                current_name = ""
            elif tag == "PTS" and len(parts) >= 3:
                x = parse_float(parts[1])
                y = parse_float(parts[2])
                if x is not None and y is not None:
                    current_points.append((x, y))
            elif tag == "LNN" and len(parts) >= 2:
                current_name = " ".join(parts[1:]).strip('"')
            elif tag == "EOL":
                if len(current_points) >= 2:
                    start = current_points[0]
                    end = current_points[-1]
                    lines.append(RunLine(current_name or str(len(lines) + 1), start[0], start[1], end[0], end[1]))
                current_points = []
                current_name = ""
    return lines


def write_hypack_lnw(path: Path, lines: list[RunLine]) -> int:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"LNS {len(lines)}\n")
        for index, line in enumerate(lines, 1):
            name = line.name or str(index)
            handle.write("LIN 2\n")
            handle.write(f"PTS {line.start_x:.2f} {line.start_y:.2f}\n")
            handle.write(f"PTS {line.end_x:.2f} {line.end_y:.2f}\n")
            handle.write(f"LNN {name}\n")
            handle.write("EOL\n")
    return len(lines)


def load_hypack_brd(path: Path) -> list[MapShape]:
    points = load_hypack_brd_points(path)
    if len(points) < 2:
        return []
    return [MapShape("line", points, text=path.stem, color="#0f766e", width=2)]



def load_hypack_brd_points(path: Path) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.replace(",", " ").split()
            if len(parts) < 2:
                continue
            x = parse_float(parts[0])
            y = parse_float(parts[1])
            if x is not None and y is not None:
                points.append((float(x), float(y)))
    return points


def save_hypack_brd_points(path: Path, points: list[tuple[float, float]]) -> int:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for x, y in points:
            handle.write(f"{float(x):010.2f} {float(y):011.2f}\n")
    return len(points)


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def clip_segment_by_polygon(
    start: tuple[float, float],
    end: tuple[float, float],
    polygon: list[tuple[float, float]],
    keep_inside: bool = True,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    if len(polygon) < 3:
        return []
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return []
    t_values = [0.0, 1.0]
    ring = polygon[:]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    for (x3, y3), (x4, y4) in zip(ring, ring[1:]):
        ex = x4 - x3
        ey = y4 - y3
        denom = _cross(dx, dy, ex, ey)
        if abs(denom) < 1e-12:
            continue
        qpx = x3 - x1
        qpy = y3 - y1
        t = _cross(qpx, qpy, ex, ey) / denom
        u = _cross(qpx, qpy, dx, dy) / denom
        if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
            t_values.append(max(0.0, min(1.0, t)))
    t_values = sorted(set(round(t, 12) for t in t_values))
    pieces: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for t0, t1 in zip(t_values, t_values[1:]):
        if t1 - t0 <= 1e-9:
            continue
        tm = (t0 + t1) / 2.0
        mx = x1 + dx * tm
        my = y1 + dy * tm
        inside = point_in_polygon(mx, my, polygon)
        if inside == keep_inside:
            pieces.append(((x1 + dx * t0, y1 + dy * t0), (x1 + dx * t1, y1 + dy * t1)))
    return pieces


def clip_runlines_to_polygon(
    lines: list[RunLine],
    polygon: list[tuple[float, float]],
    keep_inside: bool = True,
) -> list[RunLine]:
    clipped: list[RunLine] = []
    for line in lines:
        pieces = clip_segment_by_polygon(
            (line.start_x, line.start_y),
            (line.end_x, line.end_y),
            polygon,
            keep_inside=keep_inside,
        )
        for index, (start, end) in enumerate(pieces, 1):
            suffix = "" if len(pieces) == 1 else f"_{index}"
            clipped.append(RunLine(f"{line.name}{suffix}", start[0], start[1], end[0], end[1], line.enabled))
    return clipped


def clip_map_shapes_to_polygon(
    shapes: list[MapShape],
    polygon: list[tuple[float, float]],
    keep_inside: bool = True,
) -> list[MapShape]:
    clipped: list[MapShape] = []
    for shape in shapes:
        if shape.kind.lower() != "line" or len(shape.points) < 2:
            kept_points = [
                (float(x), float(y))
                for x, y in shape.points
                if point_in_polygon(float(x), float(y), polygon) == keep_inside
            ]
            if kept_points:
                clipped.append(MapShape(shape.kind, kept_points, text=shape.text, color=shape.color, width=shape.width, attrs=dict(shape.attrs)))
            continue

        pieces: list[tuple[tuple[float, float], tuple[float, float]]] = []
        for start, end in zip(shape.points, shape.points[1:]):
            pieces.extend(
                clip_segment_by_polygon(
                    (float(start[0]), float(start[1])),
                    (float(end[0]), float(end[1])),
                    polygon,
                    keep_inside=keep_inside,
                )
            )
        for index, (start, end) in enumerate(pieces, 1):
            label = shape.text if len(pieces) == 1 else f"{shape.text}_{index}"
            clipped.append(MapShape("line", [start, end], text=label, color=shape.color, width=shape.width, attrs=dict(shape.attrs)))
    return clipped


def clip_survey_points_to_polygon(
    points: list[SurveyPoint],
    polygon: list[tuple[float, float]],
    keep_inside: bool = True,
) -> list[SurveyPoint]:
    clipped: list[SurveyPoint] = []
    for point in points:
        x = point.x
        y = point.y
        if x is None or y is None:
            continue
        if point_in_polygon(float(x), float(y), polygon) == keep_inside:
            clipped.append(point)
    return clipped



DXF_COLOR_TABLE = {
    1: "#ef4444", 2: "#f59e0b", 3: "#22c55e", 4: "#06b6d4",
    5: "#2563eb", 6: "#d946ef", 7: "#e5e7eb", 8: "#64748b", 9: "#94a3b8",
}


CHART_BACKGROUND_COLOR = "#f8fbff"
CHART_GRID_COLOR = "#b8c3c9"
CHART_TEXT_COLOR = "#202020"
CHART_LAND_FILL = "#f2c46d"
CHART_BUILTUP_FILL = "#b89b57"
CHART_SHALLOW_WATER_FILL = "#9ee7f2"
CHART_VERY_SHALLOW_FILL = "#78d7e8"
CHART_DEEP_WATER_FILL = "#fbfdff"
CHART_DEPTH_CONTOUR = "#72bfe8"
CHART_COASTLINE = "#106f8b"
CHART_MAGENTA = "#ff4dff"
CHART_BROWN = "#8e7040"


ENC_LAYER_STYLE = {
    "ACHARE": (CHART_MAGENTA, 1),
    "ADMARE": (CHART_MAGENTA, 1),
    "BCNCAR": ("#111111", 2),
    "BCNISD": ("#111111", 2),
    "BCNLAT": ("#111111", 2),
    "BCNSAW": ("#111111", 2),
    "BCNSPP": ("#111111", 2),
    "BOYCAR": ("#f97316", 2),
    "BOYINB": ("#f97316", 2),
    "BOYLAT": ("#22c55e", 2),
    "BOYSAW": ("#ef4444", 2),
    "BOYSPP": ("#facc15", 2),
    "BRIDGE": ("#5f4a26", 2),
    "BUAARE": ("#8d7746", 1),
    "CBLARE": (CHART_MAGENTA, 1),
    "CBLSUB": (CHART_MAGENTA, 1),
    "COALNE": (CHART_COASTLINE, 2),
    "DEPARE": (CHART_DEPTH_CONTOUR, 1),
    "DEPCNT": (CHART_DEPTH_CONTOUR, 1),
    "DRGARE": ("#7dd3fc", 1),
    "FAIRWY": (CHART_MAGENTA, 1),
    "FERYRT": (CHART_MAGENTA, 1),
    "HRBARE": ("#7dd3fc", 1),
    "LAKARE": (CHART_SHALLOW_WATER_FILL, 1),
    "LIGHTS": (CHART_MAGENTA, 2),
    "LNDARE": (CHART_COASTLINE, 2),
    "LNDELV": (CHART_BROWN, 1),
    "LNDMRK": ("#111111", 2),
    "M_COVR": ("#999999", 1),
    "M_NSYS": (CHART_MAGENTA, 1),
    "NAVLNE": (CHART_MAGENTA, 1),
    "OBSTRN": ("#111111", 2),
    "PILPNT": ("#5f4a26", 1),
    "PIPARE": (CHART_MAGENTA, 1),
    "PIPSOL": (CHART_MAGENTA, 1),
    "RESARE": (CHART_MAGENTA, 1),
    "ROADWY": (CHART_BROWN, 1),
    "SBDARE": ("#7dd3fc", 1),
    "SEAARE": (CHART_DEPTH_CONTOUR, 1),
    "SILTNK": ("#111111", 2),
    "SLCONS": ("#5f4a26", 2),
    "SOUNDG": (CHART_TEXT_COLOR, 1),
    "TSELNE": (CHART_MAGENTA, 1),
    "TSSBND": (CHART_MAGENTA, 1),
    "TSSCRS": (CHART_MAGENTA, 1),
    "TSSLPT": (CHART_MAGENTA, 1),
    "TSSRON": (CHART_MAGENTA, 1),
    "UWTROC": ("#111111", 2),
    "WRECKS": ("#111111", 2),
}

S57_COLOUR_TABLE = {
    1: "#ffffff", 2: "#111111", 3: "#ef4444", 4: "#22c55e",
    5: "#2563eb", 6: "#facc15", 7: "#9ca3af", 8: CHART_BROWN,
    9: "#f59e0b", 10: CHART_MAGENTA, 11: "#f97316", 12: CHART_MAGENTA, 13: "#ff99ff",
}

S57_OBJECT_CLASS_BY_CODE = {
    1: "ADMARE", 4: "ACHARE", 5: "BCNCAR", 6: "BCNISD", 7: "BCNLAT", 8: "BCNSAW",
    9: "BCNSPP", 11: "BRIDGE", 13: "BUAARE", 14: "BOYCAR", 15: "BOYINB",
    17: "BOYLAT", 18: "BOYSAW", 19: "BOYSPP", 20: "CBLARE", 22: "CBLSUB",
    30: "COALNE", 42: "DEPARE", 43: "DEPCNT", 46: "DRGARE", 51: "FAIRWY",
    53: "FERYRT", 62: "HRBARE", 68: "LAKARE", 70: "LNDARE", 71: "LNDELV",
    73: "LNDMRK", 74: "LIGHTS", 83: "MORFAC", 84: "NAVLNE", 85: "OBSTRN",
    90: "PIPARE", 92: "PIPSOL", 95: "PRDARE", 108: "RESARE", 112: "ROADWY",
    114: "SBDARE", 115: "SEAARE", 117: "SLCONS", 120: "SOUNDG", 135: "TOPMAR",
    136: "TSELNE", 137: "TSSBND", 138: "TSSCRS", 139: "TSSLPT", 140: "TSSRON",
    142: "UWTROC", 143: "UNSARE", 147: "WRECKS",
}


def dxf_color_from_aci(value: object, fallback: str = "#94a3b8") -> str:
    try:
        return DXF_COLOR_TABLE.get(int(float(str(value))), fallback)
    except Exception:
        return fallback


def s57_colour_from_attr(value: object, fallback: str) -> str:
    if value in (None, ""):
        return fallback
    text = str(value).replace(",", " ").replace(";", " ")
    for token in text.split():
        parsed = parse_float(token)
        if parsed is not None:
            colour = S57_COLOUR_TABLE.get(int(parsed))
            if colour:
                return colour
    return fallback


def enc_object_class(layer_name: str, attrs: dict[str, object]) -> str:
    layer = str(layer_name or attrs.get("LAYER") or "").upper()
    if layer and not layer.isdigit() and layer not in {"ISOLATEDNODE", "CONNECTEDNODE", "EDGE", "FACE"}:
        return layer
    objl = parse_float(attrs.get("OBJL"))
    if objl is not None:
        return S57_OBJECT_CLASS_BY_CODE.get(int(objl), str(int(objl)))
    return layer or str(attrs.get("OBJL") or "").upper()


def enc_depth_area_color(attrs: dict[str, object], fallback: str = CHART_DEEP_WATER_FILL) -> str:
    shallow = parse_float(attrs.get("DRVAL1"))
    deep = parse_float(attrs.get("DRVAL2"))
    value = deep if deep is not None else shallow
    if value is None:
        return fallback
    if value <= 2.0:
        return CHART_VERY_SHALLOW_FILL
    if value <= 5.0:
        return CHART_SHALLOW_WATER_FILL
    if value <= 10.0:
        return "#c9f3f8"
    if value <= 20.0:
        return "#effbff"
    return CHART_DEEP_WATER_FILL


def enc_style_for_feature(layer_name: str, attrs: dict[str, object]) -> tuple[str, int]:
    name = enc_object_class(layer_name, attrs)
    color = "#38bdf8"
    width = 1
    for key, style in ENC_LAYER_STYLE.items():
        if name.startswith(key) or key in name:
            color, width = style
            break
    if name.startswith("DEPARE"):
        color = CHART_DEPTH_CONTOUR
    elif name.startswith(("LNDARE", "BUAARE")):
        color = CHART_COASTLINE if name.startswith("LNDARE") else "#7a663c"
        width = max(width, 2 if name.startswith("LNDARE") else 1)
    elif name.startswith(("OBSTRN", "WRECKS", "UWTROC")):
        color = "#111111"
        width = max(width, 2)
    elif name.startswith(("SOUNDG",)):
        color = CHART_TEXT_COLOR
    elif name.startswith(("ACHARE", "RESARE", "CBL", "PIP", "TSS", "NAVLNE", "M_NSYS", "FAIRWY", "FERYRT")):
        color = CHART_MAGENTA
    elif name.startswith(("DEPCNT", "SEAARE", "SBDARE")):
        color = CHART_DEPTH_CONTOUR
    color = s57_colour_from_attr(attrs.get("COLOUR") or attrs.get("COLPAT"), color)
    if attrs.get("VALSOU") not in (None, ""):
        color = CHART_TEXT_COLOR if name.startswith("SOUNDG") else color
        width = max(width, 2)
    return color, width


def enc_fill_for_feature(layer_name: str, attrs: dict[str, object], stroke_color: str) -> str:
    name = enc_object_class(layer_name, attrs)
    if name.startswith("DEPARE"):
        return enc_depth_area_color(attrs, CHART_DEEP_WATER_FILL)
    if name.startswith("LNDARE"):
        return CHART_LAND_FILL
    if name.startswith("BUAARE"):
        return CHART_BUILTUP_FILL
    if name.startswith(("LAKARE", "HRBARE")):
        return CHART_SHALLOW_WATER_FILL
    if name.startswith(("DRGARE", "SBDARE", "SEAARE")):
        return "#d7f7fb"
    if name.startswith(("M_COVR", "FAIRWY", "ACHARE", "RESARE", "CBLARE", "PIPARE")):
        return ""
    if name.startswith(("OBSTRN", "WRECKS", "UWTROC")):
        return ""
    return ""


def compact_feature_label(layer_name: str, attrs: dict[str, object]) -> str:
    parts = [str(layer_name or "").strip()]
    for key in ("OBJNAM", "LNAM", "INFORM", "NATSUR", "CATWRK", "CATOBS", "VALSOU", "DRVAL1", "DRVAL2", "COLOUR"):
        value = attrs.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return " | ".join(item for item in parts if item)

def enc_sound_display(attrs: dict[str, object]) -> str:
    for key in ("VALSOU", "DEPTH", "SOUNDING", "ELEVAT"):
        value = attrs.get(key)
        parsed = parse_float(value)
        if parsed is not None:
            text = f"{parsed:.1f}".rstrip("0").rstrip(".")
            return text
    return ""


def enc_is_magenta_overlay(shape: MapShape) -> bool:
    name = enc_object_class(str(shape.attrs.get("LAYER") or shape.text or ""), shape.attrs)
    return name.startswith(("ACHARE", "RESARE", "CBL", "PIP", "TSS", "NAVLNE", "M_NSYS", "FAIRWY", "FERYRT"))


def enc_symbol_kind(shape: MapShape) -> str:
    return enc_object_class(str(shape.attrs.get("LAYER") or shape.text or ""), shape.attrs)



def full_feature_details(shape: MapShape) -> str:
    lines = [
        f"Feature: {shape.text or shape.kind}",
        f"Type: {shape.kind}",
        f"Colour: {shape.color}",
        f"Points: {len(shape.points)}",
        "",
        "Attributes:",
    ]
    if shape.attrs:
        for key in sorted(shape.attrs):
            lines.append(f"{key}: {shape.attrs[key]}")
    else:
        lines.append("(No attributes available)")
    return "\n".join(lines)


def arc_points(cx: float, cy: float, radius: float, start_deg: float, end_deg: float, steps: int = 32) -> list[tuple[float, float]]:
    if radius <= 0:
        return []
    if end_deg < start_deg:
        end_deg += 360.0
    count = max(6, min(96, steps))
    return [
        (cx + math.cos(math.radians(start_deg + (end_deg - start_deg) * i / count)) * radius,
         cy + math.sin(math.radians(start_deg + (end_deg - start_deg) * i / count)) * radius)
        for i in range(count + 1)
    ]


def shape_bbox(shape: MapShape) -> Optional[tuple[float, float, float, float]]:
    if not shape.points:
        return None
    xs = [float(x) for x, _y in shape.points]
    ys = [float(y) for _x, y in shape.points]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])



def ogr_geometry_points(geom: Any) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if geom is None:
        return points
    name = str(geom.GetGeometryName() or "").upper()
    if name in {"POINT"}:
        return [(float(geom.GetX()), float(geom.GetY()))]
    if name in {"LINESTRING", "LINEARRING"}:
        return [(float(geom.GetX(i)), float(geom.GetY(i))) for i in range(geom.GetPointCount())]
    if name == "POLYGON":
        ring = geom.GetGeometryRef(0)
        return ogr_geometry_points(ring)
    for idx in range(geom.GetGeometryCount()):
        points.extend(ogr_geometry_points(geom.GetGeometryRef(idx)))
    return points


def load_ogr_cad_shapes(path: Path) -> list[MapShape]:
    """Load CAD vector backgrounds through GDAL/OGR, primarily DWG when supported."""
    try:
        from osgeo import gdal, ogr  # type: ignore
        ogr.UseExceptions()
        gdal.UseExceptions()
    except Exception as exc:
        raise RuntimeError(
            "DWG background loading requires GDAL/OGR Python bindings with CAD/DWG driver support."
        ) from exc

    dataset = gdal.OpenEx(str(path), gdal.OF_VECTOR)
    if dataset is None:
        dataset = ogr.Open(str(path))
    if dataset is None:
        raise RuntimeError(
            "Could not open DWG/CAD file. This GDAL build may not include a DWG driver; export/save the drawing as DXF if DWG is not supported."
        )

    shapes: list[MapShape] = []
    for layer_index in range(dataset.GetLayerCount()):
        layer = dataset.GetLayerByIndex(layer_index)
        if layer is None:
            continue
        layer_name = str(layer.GetName() or path.stem)
        try:
            layer.ResetReading()
        except Exception:
            pass
        for feature in layer:
            geom = feature.GetGeometryRef()
            pts = ogr_geometry_points(geom)
            if not pts:
                continue
            attrs: dict[str, object] = {"LAYER": layer_name, "SOURCE": path.name}
            for idx in range(feature.GetFieldCount()):
                field_def = feature.GetFieldDefnRef(idx)
                if field_def is not None:
                    name = str(field_def.GetName() or f"FIELD_{idx}")
                    attrs[name] = feature.GetField(idx)
            label = str(attrs.get("Layer") or attrs.get("LAYER") or layer_name)
            geom_name = str(geom.GetGeometryName() if geom is not None else "").upper()
            if len(pts) == 1:
                shapes.append(MapShape("point", pts, text=label, color="#94a3b8", width=1, attrs=attrs))
            elif geom_name == "POLYGON" and pts[0] != pts[-1]:
                shapes.append(MapShape("line", pts + [pts[0]], text=label, color="#94a3b8", width=1, attrs=attrs))
            else:
                shapes.append(MapShape("line", pts, text=label, color="#94a3b8", width=1, attrs=attrs))
    return shapes


def load_cad_background_shapes(path: Path) -> list[MapShape]:
    suffix = path.suffix.lower()
    if suffix == ".dwg":
        return load_ogr_cad_shapes(path)
    if suffix == ".dxf":
        try:
            return load_dxf_shapes(path)
        except Exception:
            return load_ogr_cad_shapes(path)
    return load_ogr_cad_shapes(path)


def find_oda_file_converter() -> Optional[Path]:
    """Return the ODA File Converter executable when it is available locally."""
    candidates: list[Path] = []
    path_hit = shutil.which("ODAFileConverter.exe") or shutil.which("ODAFileConverter")
    if path_hit:
        candidates.append(Path(path_hit))
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not base:
            continue
        root = Path(base) / "ODA"
        if root.exists():
            candidates.extend(root.glob("ODAFileConverter*/ODAFileConverter.exe"))
            candidates.extend(root.glob("**/ODAFileConverter.exe"))
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


def convert_dwg_to_dxf_with_oda(
    dwg_path: Path,
    output_folder: Optional[Path] = None,
    version: str = "ACAD2018",
    audit: bool = True,
) -> Path:
    """Convert one DWG to DXF through ODA, using a short temporary name for reliability."""
    converter = find_oda_file_converter()
    if converter is None:
        raise RuntimeError(
            "ODA File Converter was not found. Install ODA File Converter, then retry this conversion."
        )
    source = Path(dwg_path)
    if source.suffix.lower() != ".dwg":
        raise ValueError("Select a DWG file for ODA conversion.")
    if not source.exists():
        raise FileNotFoundError(str(source))
    target_folder = Path(output_folder) if output_folder else source.parent
    target_folder.mkdir(parents=True, exist_ok=True)
    target_path = target_folder / f"{source.stem}.dxf"
    with tempfile.TemporaryDirectory(prefix="HYDROGEOMATICS_oda_") as tmp_name:
        tmp_root = Path(tmp_name)
        tmp_in = tmp_root / "in"
        tmp_out = tmp_root / "out"
        tmp_in.mkdir()
        tmp_out.mkdir()
        safe_source = tmp_in / "source.dwg"
        shutil.copy2(source, safe_source)
        command = [
            str(converter),
            str(tmp_in),
            str(tmp_out),
            str(version or "ACAD2018"),
            "DXF",
            "0",
            "1" if audit else "0",
            "*.dwg",
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        converted = tmp_out / "source.dxf"
        if result.returncode != 0 or not converted.exists():
            detail = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
            raise RuntimeError(detail or f"ODA conversion failed for {source.name}.")
        shutil.copy2(converted, target_path)
    return target_path


def load_dxf_shapes(path: Path) -> list[MapShape]:
    try:
        import ezdxf  # type: ignore
    except Exception:
        ezdxf = None

    if ezdxf is not None:
        try:
            doc = ezdxf.readfile(str(path))
            shapes: list[MapShape] = []
            for entity in doc.modelspace():
                kind = entity.dxftype().upper()
                color = dxf_color_from_aci(getattr(entity.dxf, "color", 8))
                layer = str(getattr(entity.dxf, "layer", path.stem))
                if kind == "LINE":
                    start = entity.dxf.start
                    end = entity.dxf.end
                    shapes.append(MapShape("line", [(float(start.x), float(start.y)), (float(end.x), float(end.y))], text=layer, color=color, width=1))
                elif kind in {"LWPOLYLINE", "POLYLINE"}:
                    pts = [(float(p[0]), float(p[1])) for p in entity.get_points()] if kind == "LWPOLYLINE" else [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
                    if len(pts) >= 2:
                        if bool(getattr(entity, "closed", False)) and pts[0] != pts[-1]:
                            pts.append(pts[0])
                        shapes.append(MapShape("line", pts, text=layer, color=color, width=1))
                elif kind == "CIRCLE":
                    c = entity.dxf.center
                    pts = arc_points(float(c.x), float(c.y), float(entity.dxf.radius), 0.0, 360.0, 72)
                    shapes.append(MapShape("line", pts, text=layer, color=color, width=1))
                elif kind == "ARC":
                    c = entity.dxf.center
                    pts = arc_points(float(c.x), float(c.y), float(entity.dxf.radius), float(entity.dxf.start_angle), float(entity.dxf.end_angle), 48)
                    shapes.append(MapShape("line", pts, text=layer, color=color, width=1))
                elif kind == "POINT":
                    p = entity.dxf.location
                    shapes.append(MapShape("point", [(float(p.x), float(p.y))], text=layer, color=color, width=1))
                elif kind in {"TEXT", "MTEXT"}:
                    p = entity.dxf.insert
                    label = str(entity.plain_text() if hasattr(entity, "plain_text") else entity.dxf.text)
                    shapes.append(MapShape("text", [(float(p.x), float(p.y))], text=label, color=color, width=1))
            if shapes:
                return shapes
        except Exception:
            pass

    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    pairs: list[tuple[str, str]] = []
    for index in range(0, len(raw_lines) - 1, 2):
        pairs.append((raw_lines[index].strip(), raw_lines[index + 1].strip()))
    shapes: list[MapShape] = []
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        entity = value.upper() if code == "0" else ""
        if entity == "LINE":
            data: dict[str, float] = {}
            layer = path.stem
            color = "#94a3b8"
            i += 1
            while i < len(pairs) and pairs[i][0] != "0":
                key, item = pairs[i]
                if key == "8":
                    layer = item or layer
                elif key == "62":
                    color = dxf_color_from_aci(item, color)
                elif key in {"10", "20", "11", "21"}:
                    parsed = parse_float(item)
                    if parsed is not None:
                        data[key] = float(parsed)
                i += 1
            if {"10", "20", "11", "21"}.issubset(data):
                shapes.append(MapShape("line", [(data["10"], data["20"]), (data["11"], data["21"])], text=layer, color=color, width=1))
            continue
        if entity in {"LWPOLYLINE", "POLYLINE"}:
            pts: list[tuple[float, float]] = []
            layer = path.stem
            color = "#94a3b8"
            pending_x: Optional[float] = None
            i += 1
            while i < len(pairs):
                key, item = pairs[i]
                if key == "0" and entity == "POLYLINE" and item.upper() == "VERTEX":
                    vx = vy = None
                    i += 1
                    while i < len(pairs) and pairs[i][0] != "0":
                        vkey, vitem = pairs[i]
                        if vkey == "10":
                            vx = parse_float(vitem)
                        elif vkey == "20":
                            vy = parse_float(vitem)
                        i += 1
                    if vx is not None and vy is not None:
                        pts.append((float(vx), float(vy)))
                    continue
                if key == "0" and (entity == "LWPOLYLINE" or item.upper() == "SEQEND"):
                    if entity == "POLYLINE":
                        i += 1
                    break
                if key == "8":
                    layer = item or layer
                elif key == "62":
                    color = dxf_color_from_aci(item, color)
                elif key == "10":
                    pending_x = parse_float(item)
                elif key == "20" and pending_x is not None:
                    parsed_y = parse_float(item)
                    if parsed_y is not None:
                        pts.append((float(pending_x), float(parsed_y)))
                    pending_x = None
                i += 1
            if len(pts) >= 2:
                shapes.append(MapShape("line", pts, text=layer, color=color, width=1))
            continue
        if entity in {"CIRCLE", "ARC"}:
            data: dict[str, float] = {}
            layer = path.stem
            color = "#94a3b8"
            i += 1
            while i < len(pairs) and pairs[i][0] != "0":
                key, item = pairs[i]
                if key == "8":
                    layer = item or layer
                elif key == "62":
                    color = dxf_color_from_aci(item, color)
                elif key in {"10", "20", "40", "50", "51"}:
                    parsed = parse_float(item)
                    if parsed is not None:
                        data[key] = float(parsed)
                i += 1
            if {"10", "20", "40"}.issubset(data):
                pts = arc_points(data["10"], data["20"], data["40"], data.get("50", 0.0), data.get("51", 360.0), 72 if entity == "CIRCLE" else 48)
                shapes.append(MapShape("line", pts, text=layer, color=color, width=1))
            continue
        if entity in {"POINT", "TEXT", "MTEXT"}:
            data: dict[str, float] = {}
            layer = path.stem
            label = layer
            color = "#94a3b8"
            i += 1
            while i < len(pairs) and pairs[i][0] != "0":
                key, item = pairs[i]
                if key == "8":
                    layer = item or layer
                elif key == "1":
                    label = item or label
                elif key == "62":
                    color = dxf_color_from_aci(item, color)
                elif key in {"10", "20"}:
                    parsed = parse_float(item)
                    if parsed is not None:
                        data[key] = float(parsed)
                i += 1
            if {"10", "20"}.issubset(data):
                shapes.append(MapShape("text" if entity != "POINT" else "point", [(data["10"], data["20"])], text=label, color=color, width=1))
            continue
        i += 1
    return shapes



def load_enc_s57_shapes(path: Path) -> list[MapShape]:
    """Load ENC/S-57 features as colored plan-view background shapes."""
    try:
        from osgeo import gdal, ogr  # type: ignore
        ogr.UseExceptions()
        gdal.UseExceptions()
    except Exception as exc:
        raise RuntimeError(
            "ENC/S-57 loading requires GDAL/OGR Python bindings. Install GDAL or run this build in an environment where osgeo.ogr is available."
        ) from exc

    open_options = [
        "SPLIT_MULTIPOINT=ON",
        "ADD_SOUNDG_DEPTH=ON",
        "RETURN_PRIMITIVES=OFF",
        "RETURN_LINKAGES=ON",
        "LNAM_REFS=ON",
        "LIST_AS_STRING=ON",
        "PRESERVE_EMPTY_NUMBERS=ON",
    ]
    try:
        dataset = gdal.OpenEx(str(path), gdal.OF_VECTOR, open_options=open_options)
    except Exception:
        dataset = ogr.Open(str(path))
    if dataset is None:
        raise RuntimeError(f"Could not open ENC/S-57 file: {path.name}")

    shapes: list[MapShape] = []

    def feature_attrs(feature: Any) -> dict[str, object]:
        attrs: dict[str, object] = {}
        try:
            count = feature.GetFieldCount()
        except Exception:
            return attrs
        for index in range(count):
            try:
                name = feature.GetFieldDefnRef(index).GetNameRef()
                try:
                    value = feature.GetFieldAsString(index)
                except Exception:
                    value = feature.GetField(index)
            except Exception:
                continue
            if value not in (None, ""):
                attrs[str(name)] = value
        return attrs

    def add_geometry(geom: Any, label: str, color: str, width: int, attrs: dict[str, object]) -> None:
        if geom is None:
            return
        gtype = geom.GetGeometryName().upper()
        if gtype in {"POINT", "MULTIPOINT"}:
            if gtype == "POINT":
                shapes.append(MapShape("point", [(float(geom.GetX()), float(geom.GetY()))], text=label, color=color, width=max(1, width), attrs=dict(attrs)))
            else:
                for idx in range(geom.GetGeometryCount()):
                    add_geometry(geom.GetGeometryRef(idx), label, color, width, attrs)
            return
        if gtype in {"LINESTRING", "LINEARRING"}:
            pts = [(float(geom.GetX(i)), float(geom.GetY(i))) for i in range(geom.GetPointCount())]
            if len(pts) >= 2:
                shapes.append(MapShape("line", pts, text=label, color=color, width=max(1, width), attrs=dict(attrs)))
            return
        if gtype == "POLYGON":
            for ring_index in range(geom.GetGeometryCount()):
                ring = geom.GetGeometryRef(ring_index)
                if ring is None:
                    continue
                pts = [(float(ring.GetX(i)), float(ring.GetY(i))) for i in range(ring.GetPointCount())]
                if len(pts) >= 2:
                    shape_attrs = dict(attrs)
                    fill_color = enc_fill_for_feature(str(attrs.get("LAYER", "")), attrs, color)
                    if fill_color:
                        shape_attrs["FILL"] = fill_color
                    shapes.append(MapShape("polygon", pts, text=label, color=color, width=max(1, width), attrs=shape_attrs))
            return
        for idx in range(geom.GetGeometryCount()):
            add_geometry(geom.GetGeometryRef(idx), label, color, width, attrs)

    for layer_index in range(dataset.GetLayerCount()):
        layer = dataset.GetLayerByIndex(layer_index)
        if layer is None:
            continue
        layer_name = layer.GetName() or path.stem
        if layer_name.upper() in {"ISOLATEDNODE", "CONNECTEDNODE", "EDGE", "FACE"}:
            continue
        layer.ResetReading()
        for feature in layer:
            attrs = feature_attrs(feature)
            attrs.setdefault("LAYER", layer_name)
            attrs["OBJCLASS"] = enc_object_class(layer_name, attrs)
            color, width = enc_style_for_feature(layer_name, attrs)
            label = compact_feature_label(layer_name, attrs)
            add_geometry(feature.GetGeometryRef(), label, color, width, attrs)
    return shapes


def _kml_escape(value: object) -> str:
    return escape(str(value if value is not None else ""))


def write_kmz(path: Path, placemarks: list[str]) -> None:
    import zipfile
    kml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<kml xmlns=\"http://www.opengis.net/kml/2.2\">
<Document>
<name>HYDROGEOMATICS Export</name>
{body}
</Document>
</kml>
""".format(body="\n".join(placemarks))
    path = Path(path).with_suffix(".kmz")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", kml)

def _kml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _kml_child_text(element: object, child_name: str) -> str:
    for child in list(element):
        if _kml_local_name(child.tag) == child_name.lower() and child.text:
            return child.text.strip()
    return ""


def parse_kml_coordinates(text: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in str(text or "").replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        x = parse_float(parts[0])
        y = parse_float(parts[1])
        if x is not None and y is not None:
            points.append((float(x), float(y)))
    return points


def load_kmz_shapes(path: Path) -> list[MapShape]:
    import zipfile
    import xml.etree.ElementTree as ET

    path = Path(path)
    if path.suffix.lower() == ".kmz":
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            kml_names = [name for name in names if name.lower().endswith(".kml")]
            if not kml_names:
                raise RuntimeError("KMZ does not contain a KML file.")
            preferred = "doc.kml" if "doc.kml" in kml_names else kml_names[0]
            root = ET.fromstring(archive.read(preferred))
    else:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))

    shapes: list[MapShape] = []

    def placemark_context(node: object) -> tuple[str, dict[str, object]]:
        current = node
        # ElementTree has no parent pointer, so this is filled during traversal below.
        return path.stem, {"SOURCE": path.name}

    def walk(element: object, current_name: str = "") -> None:
        local = _kml_local_name(element.tag)
        name_here = current_name
        if local == "placemark":
            name_here = _kml_child_text(element, "name") or current_name or path.stem
        if local == "point":
            coords = []
            for child in list(element):
                if _kml_local_name(child.tag) == "coordinates":
                    coords = parse_kml_coordinates(child.text or "")
                    break
            if coords:
                shapes.append(MapShape("point", [coords[0]], text=name_here or path.stem, color="#f97316", width=2, attrs={"SOURCE": path.name, "NAME": name_here}))
        elif local == "linestring":
            coords = []
            for child in list(element):
                if _kml_local_name(child.tag) == "coordinates":
                    coords = parse_kml_coordinates(child.text or "")
                    break
            if len(coords) >= 2:
                shapes.append(MapShape("line", coords, text=name_here or path.stem, color="#f97316", width=2, attrs={"SOURCE": path.name, "NAME": name_here}))
        elif local in {"polygon", "linearring"}:
            for child in list(element):
                if _kml_local_name(child.tag) == "coordinates":
                    coords = parse_kml_coordinates(child.text or "")
                    if len(coords) >= 2:
                        shapes.append(MapShape("line", coords, text=name_here or path.stem, color="#f59e0b", width=2, attrs={"SOURCE": path.name, "NAME": name_here, "KML_TYPE": local}))
        elif local == "track":
            coords = []
            for child in list(element):
                if _kml_local_name(child.tag) == "coord" and child.text:
                    parts = child.text.split()
                    if len(parts) >= 2:
                        x = parse_float(parts[0])
                        y = parse_float(parts[1])
                        if x is not None and y is not None:
                            coords.append((float(x), float(y)))
            if len(coords) >= 2:
                shapes.append(MapShape("line", coords, text=name_here or path.stem, color="#fb923c", width=2, attrs={"SOURCE": path.name, "NAME": name_here, "KML_TYPE": "gx:Track"}))
        for child in list(element):
            walk(child, name_here)

    walk(root, path.stem)
    return shapes


def load_hypack_tgt(path: Path) -> list[MapShape]:
    shapes: list[MapShape] = []
    pattern = re.compile(r'^GPT\s+"([^"]*)"\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)', re.I)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = pattern.match(line.strip())
            if not match:
                continue
            label, x_text, y_text = match.groups()
            x = parse_float(x_text)
            y = parse_float(y_text)
            if x is not None and y is not None:
                shapes.append(MapShape("point", [(x, y)], text=label, color="#dc2626", width=2))
    return shapes



def fit_cad_shapes_to_canvas(shapes: list[MapShape], width: int, height: int, margin: int = 40) -> list[MapShape]:
    points = [point for shape in shapes for point in shape.points]
    if not points:
        return shapes
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    canvas_w = max(width - margin * 2, 1)
    canvas_h = max(height - margin * 2, 1)
    scale = min(canvas_w / span_x, canvas_h / span_y)
    fitted: list[MapShape] = []
    for shape in shapes:
        fitted_points = [
            (
                margin + (float(x) - min_x) * scale,
                height - margin - (float(y) - min_y) * scale,
            )
            for x, y in shape.points
        ]
        fitted.append(MapShape(shape.kind, fitted_points, text=shape.text, color=shape.color, width=shape.width, attrs=dict(shape.attrs)))
    return fitted


def is_hypack_raw_file(path: Path) -> bool:
    if path.suffix.lower() != ".raw":
        return False
    try:
        sample = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except Exception:
        return False
    return any(tag in sample for tag in ("\nPOS ", "\nEC1 ", "\nTID ", "\nEOH", "FTP "))


def is_hypack_edited_file(path: Path) -> bool:
    return path.suffix.lower() in {".1", ".2", ".3"} or re.match(r".*\.\d+$", path.name) is not None


def parse_nmea_lat_lon(value: str, hemisphere: str) -> Optional[float]:
    if not value:
        return None
    try:
        raw = float(value)
    except ValueError:
        return None
    degrees = int(raw // 100)
    minutes = raw - degrees * 100
    decimal = degrees + minutes / 60.0
    if hemisphere.upper() in {"S", "W"}:
        decimal = -decimal
    return decimal


def parse_nmea_sentence(sentence: str) -> Optional[SurveyPoint]:
    text = sentence.strip()
    if not text.startswith("$"):
        return None
    body = text.split("*", 1)[0]
    parts = body.split(",")
    kind = parts[0][-3:]
    if kind == "GGA" and len(parts) >= 10:
        return SurveyPoint(
            time=parts[1],
            latitude=parse_nmea_lat_lon(parts[2], parts[3]),
            longitude=parse_nmea_lat_lon(parts[4], parts[5]),
            quality=parts[6],
            altitude_m=parse_float(parts[9]),
        )
    if kind == "RMC" and len(parts) >= 10:
        return SurveyPoint(
            time=parts[1],
            latitude=parse_nmea_lat_lon(parts[3], parts[4]),
            longitude=parse_nmea_lat_lon(parts[5], parts[6]),
            quality=parts[2],
            speed_knots=parse_float(parts[7]),
            course_degrees=parse_float(parts[8]),
        )
    if kind in {"HDT", "THS"} and len(parts) >= 2:
        return SurveyPoint(heading_degrees=parse_float(parts[1]))
    if kind == "VTG" and len(parts) >= 6:
        return SurveyPoint(course_degrees=parse_float(parts[1]), speed_knots=parse_float(parts[5]))
    if kind in {"DBT", "DPT"}:
        depth = None
        if kind == "DBT" and len(parts) >= 5:
            depth = parse_float(parts[3])
        elif kind == "DPT" and len(parts) >= 2:
            depth = parse_float(parts[1])
        return SurveyPoint(raw_depth=depth)
    return None


def parse_sbes_sentence(sentence: str) -> dict[str, object]:
    text = sentence.strip()
    result: dict[str, object] = {"raw": text, "format": "", "depth": None}
    if not text.startswith("$"):
        return result
    body = text.split("*", 1)[0]
    parts = body.split(",")
    kind = parts[0][-3:]
    result["format"] = kind
    if kind == "DPT" and len(parts) >= 2:
        result["depth"] = parse_float(parts[1])
        if len(parts) >= 3:
            result["offset"] = parse_float(parts[2])
    elif kind == "DBT" and len(parts) >= 5:
        result["depth_feet"] = parse_float(parts[1])
        result["depth"] = parse_float(parts[3])
        if len(parts) >= 7:
            result["depth_fathoms"] = parse_float(parts[5])
    return result


def load_nmea(path: Path) -> list[SurveyPoint]:
    points: list[SurveyPoint] = []
    current_position: Optional[SurveyPoint] = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            parsed = parse_nmea_sentence(line)
            if parsed is None:
                continue
            if parsed.latitude is not None and parsed.longitude is not None:
                current_position = parsed
                ensure_projected_xy(current_position, GeodeticSettings())
                current_position.source = f"{path.name}:{line_no}"
                points.append(current_position)
            elif parsed.raw_depth is not None:
                if current_position is None:
                    parsed.source = f"{path.name}:{line_no}"
                    points.append(parsed)
                else:
                    current_position.raw_depth = parsed.raw_depth
    return points


def run_nmea_report_cli(args: argparse.Namespace) -> int:
    if not args.input:
        raise SystemExit("--nmea-report requires an NMEA/log input file")
    raw = Path(args.input[0]).read_text(encoding=args.encoding, errors="replace")
    parser = NMEAParser()
    parser.feed(raw)
    if args.gpx:
        export_gpx(parser.track, args.gpx, name=args.gpx_name)
    payload = {
        "last_position": gps_position_to_dict(parser.position),
        "track_points": len(parser.track),
        "satellites": [satellite.__dict__ for satellite in parser.satellites],
    }
    print(json.dumps(payload, indent=2))
    return 0


def run_checksum_cli(args: argparse.Namespace) -> int:
    print(nmea_checksum_str(args.checksum))
    if args.add_checksum:
        print(add_nmea_checksum(args.checksum, terminator=False))
    return 0


def run_project_template_cli(args: argparse.Namespace) -> int:
    project = HydroProject()
    for item in args.input:
        for point in load_points_from_any(Path(item)):
            if point.x is None or point.y is None or point.raw_depth is None:
                continue
            project.bathymetry.append(
                BathymetrySample(
                    x=float(point.x),
                    y=float(point.y),
                    depth=float(point.raw_depth),
                    time_utc=point.time or None,
                    tide=args.tide,
                    draft=args.draft,
                    sound_velocity_mps=args.sound_speed,
                    corrected_depth=point.corrected_depth,
                )
            )
    output = Path(args.output if args.output != "BS_output.xyz" else "surveyplan_project.json")
    if output.suffix.lower() != ".json":
        output = output.with_suffix(".json")
    save_project(output, project)
    print(f"Saved SurveyPlan project template to {output}")
    return 0


def run_cli(args: argparse.Namespace) -> int:
    if args.checksum:
        return run_checksum_cli(args)
    if args.nmea_report:
        return run_nmea_report_cli(args)
    if args.project_template:
        return run_project_template_cli(args)
    if args.settings_template:
        output = Path(args.output if args.output != "BS_output.xyz" else "BS_settings.json")
        AppSettings.defaults().save(output)
        print(f"Saved settings template to {output}")
        return 0
    if args.generate_runlines:
        return run_generate_runlines(args)
    if args.navigate:
        return run_navigation_cli(args)
    if args.echogram:
        return run_echogram_cli(args)

    model = SurveyModel()
    model.tide = args.tide
    model.draft = args.draft
    model.sound_speed = args.sound_speed
    loaded_hypack_corrections = False
    for item in args.input:
        path = Path(item)
        if is_hypack_raw_file(path) or is_hypack_edited_file(path):
            loaded_hypack_corrections = True
        if path.suffix.lower() in {".nmea", ".log"} and not is_hypack_raw_file(path):
            for point in load_nmea(path):
                model.add(point)
        else:
            model.load_file(path)
    user_supplied_corrections = args.tide != 0.0 or args.draft != 0.0 or args.sound_speed != DEFAULT_SOUND_SPEED
    if user_supplied_corrections or not loaded_hypack_corrections:
        model.apply_offsets()
    output = Path(args.output).with_suffix(".raw")
    count = model.export_hypack_raw(output)
    stats = model.stats()
    print(f"Saved {count} HYPACK RAW records to {output}")
    print(
        "Depth stats:",
        f"count={stats['count']}",
        f"min={format_number(stats['min'])}",
        f"max={format_number(stats['max'])}",
        f"mean={format_number(stats['mean'])}",
    )
    return 0


def run_echogram_cli(args: argparse.Namespace) -> int:
    if not args.input:
        raise SystemExit("--echogram requires input survey, XYZ, CSV, NMEA, LOG, or matrix CSV files")
    echogram = EchogramModel(max_depth=args.max_depth, depth_bins=args.depth_bins)
    if args.matrix:
        total = 0
        for item in args.input:
            total += echogram.load_matrix_csv(Path(item))
        if total == 0:
            raise SystemExit("No matrix samples found")
    else:
        points: list[SurveyPoint] = []
        for item in args.input:
            path = Path(item)
            points.extend(load_nmea(path) if path.suffix.lower() in {".nmea", ".log"} else load_points_from_any(path))
        echogram.build_from_points(points)
    output = Path(args.output)
    if output.suffix.lower() not in {".ppm", ".pnm"}:
        output = output.with_suffix(".ppm")
    count = echogram.export_ppm(output)
    print(f"Saved acoustic waterfall with {count} pings to {output}")
    return 0


def run_generate_runlines(args: argparse.Namespace) -> int:
    required = [args.start_x, args.start_y, args.end_x, args.end_y, args.spacing]
    if any(value is None for value in required):
        raise SystemExit("--generate-runlines requires --start-x --start-y --end-x --end-y --spacing")
    runlines = RunLineSet()
    runlines.generate_parallel(
        start_x=args.start_x,
        start_y=args.start_y,
        end_x=args.end_x,
        end_y=args.end_y,
        spacing=args.spacing,
        count_each_side=args.count_each_side,
        prefix=args.prefix,
        include_center=not args.no_center,
    )
    output = Path(args.output)
    count = runlines.export_csv(output)
    print(f"Saved {count} runlines to {output}")
    return 0


def run_navigation_cli(args: argparse.Namespace) -> int:
    if args.line_file:
        runlines = RunLineSet()
        runlines.load_csv(Path(args.line_file))
    else:
        required = [args.start_x, args.start_y, args.end_x, args.end_y]
        if any(value is None for value in required):
            raise SystemExit("--navigate requires --line-file or --start-x --start-y --end-x --end-y")
        runlines = RunLineSet()
        runlines.add(RunLine(args.prefix or "RUNLINE", args.start_x, args.start_y, args.end_x, args.end_y))

    if args.position:
        x, y = parse_position_pair(args.position)
        solution = runlines.nearest(x, y)
        if solution is None:
            raise SystemExit("No enabled runlines available")
        print_navigation_solution(solution)
        return 0

    if not args.input:
        raise SystemExit("--navigate requires --position or input survey/GPS files")

    rows: list[dict[str, object]] = []
    for item in args.input:
        path = Path(item)
        points = load_nmea(path) if path.suffix.lower() in {".nmea", ".log"} else load_points_from_any(path)
        for point in points:
            if point.x is None or point.y is None:
                continue
            solution = runlines.nearest(float(point.x), float(point.y))
            if solution is None:
                continue
            rows.append(navigation_row(point, solution))

    output = Path(args.output)
    with output.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "time",
            "x",
            "y",
            "line",
            "bearing_degrees",
            "along_track",
            "cross_track",
            "side",
            "distance_to_start",
            "distance_to_end",
            "percent_complete",
            "nearest_x",
            "nearest_y",
            "source",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} realtime navigation rows to {output}")
    return 0


def load_points_from_any(path: Path) -> list[SurveyPoint]:
    model = SurveyModel()
    model.load_file(path)
    return model.points


def parse_position_pair(text: str) -> tuple[float, float]:
    parts = text.replace(",", " ").split()
    if len(parts) != 2:
        raise SystemExit("--position must be 'x,y' or 'x y'")
    return float(parts[0]), float(parts[1])


def navigation_row(point: SurveyPoint, solution: NavigationSolution) -> dict[str, object]:
    return {
        "time": point.time,
        "x": format_number(point.x),
        "y": format_number(point.y),
        "line": solution.line.name,
        "bearing_degrees": f"{solution.line.bearing_degrees:.3f}",
        "along_track": f"{solution.along_track:.3f}",
        "cross_track": f"{solution.cross_track:.3f}",
        "side": solution.side,
        "distance_to_start": f"{solution.distance_to_start:.3f}",
        "distance_to_end": f"{solution.distance_to_end:.3f}",
        "percent_complete": f"{solution.percent_complete:.2f}",
        "nearest_x": f"{solution.nearest_x:.3f}",
        "nearest_y": f"{solution.nearest_y:.3f}",
        "source": point.source,
    }


def print_navigation_solution(solution: NavigationSolution) -> None:
    print(f"Line: {solution.line.name}")
    print(f"Bearing: {solution.line.bearing_degrees:.3f} deg")
    print(f"Along-track: {solution.along_track:.3f}")
    print(f"Cross-track: {solution.cross_track:.3f} ({solution.side})")
    print(f"Distance to start: {solution.distance_to_start:.3f}")
    print(f"Distance to end: {solution.distance_to_end:.3f}")
    print(f"Complete: {solution.percent_complete:.2f}%")


def format_number(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)



# ======================================================================
# BEGIN GEODETIC_TOOLS_MERGE.py
# Added from 0606finalV_surveyplan_merged.py and adapted for 0607 Tkinter UI.
# Provides LLH/XYZ, UTM, and datum transformation utilities.
# ======================================================================

GT_DEG = math.pi / 180.0
GT_ARCSEC = GT_DEG / 3600.0


class GT_Ellipsoid:
    def __init__(self, name: str, a: float, inv_f: float) -> None:
        self.name = name
        self.a = float(a)
        self.inv_f = float(inv_f)

    @property
    def f(self) -> float:
        return 1.0 / self.inv_f

    @property
    def e2(self) -> float:
        return self.f * (2.0 - self.f)

    @property
    def ep2(self) -> float:
        return self.e2 / (1.0 - self.e2)


class GT_Datum:
    def __init__(
        self,
        name: str,
        ellipsoid: GT_Ellipsoid,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
        rx_arcsec: float = 0.0,
        ry_arcsec: float = 0.0,
        rz_arcsec: float = 0.0,
        scale_ppm: float = 0.0,
    ) -> None:
        self.name = name
        self.ellipsoid = ellipsoid
        self.dx = float(dx)
        self.dy = float(dy)
        self.dz = float(dz)
        self.rx_arcsec = float(rx_arcsec)
        self.ry_arcsec = float(ry_arcsec)
        self.rz_arcsec = float(rz_arcsec)
        self.scale_ppm = float(scale_ppm)


GT_ELLIPSOIDS = {
    "WGS84": GT_Ellipsoid("WGS84", 6378137.0, 298.257223563),
    "GRS80": GT_Ellipsoid("GRS80", 6378137.0, 298.257222101),
    "Airy1830": GT_Ellipsoid("Airy1830", 6377563.396, 299.3249646),
    "Clarke1866": GT_Ellipsoid("Clarke1866", 6378206.4, 294.9786982),
    "Clarke1880": GT_Ellipsoid("Clarke1880", 6378249.145, 293.465),
    "International1924": GT_Ellipsoid("International1924", 6378388.0, 297.0),
    "Bessel1841": GT_Ellipsoid("Bessel1841", 6377397.155, 299.1528128),
    "Krassovsky1940": GT_Ellipsoid("Krassovsky1940", 6378245.0, 298.3),
}

GT_DATUMS = {
    "WGS84": GT_Datum("WGS84", GT_ELLIPSOIDS["WGS84"]),
    "NAD83": GT_Datum("NAD83", GT_ELLIPSOIDS["GRS80"]),
    "ED50": GT_Datum("ED50", GT_ELLIPSOIDS["International1924"], -87.0, -98.0, -121.0),
    "OSGB36": GT_Datum("OSGB36", GT_ELLIPSOIDS["Airy1830"], 446.448, -125.157, 542.060, 0.1502, 0.2470, 0.8421, -20.4894),
    "NAD27": GT_Datum("NAD27", GT_ELLIPSOIDS["Clarke1866"], 8.0, -160.0, -176.0),
}


def GT_parse_angle(text: object) -> float:
    s = str(text).strip().upper().replace(":", " ").replace("'", " ").replace('"', " ")
    sign = -1.0 if ("S" in s or "W" in s) else 1.0
    for ch in "NSEW":
        s = s.replace(ch, " ")
    parts = [p for p in s.split() if p]
    if not parts:
        raise ValueError("empty angle")
    if len(parts) == 1:
        return float(parts[0])
    deg = abs(float(parts[0]))
    minutes = float(parts[1]) if len(parts) > 1 else 0.0
    seconds = float(parts[2]) if len(parts) > 2 else 0.0
    if float(parts[0]) < 0:
        sign = -1.0
    return sign * (deg + minutes / 60.0 + seconds / 3600.0)


def GT_format_dms(decimal_degree: float, is_lat: bool = True) -> str:
    hemi = ("N" if decimal_degree >= 0 else "S") if is_lat else ("E" if decimal_degree >= 0 else "W")
    value = abs(decimal_degree)
    deg = int(value)
    minutes_full = (value - deg) * 60.0
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60.0
    return f"{deg:02d} {minutes:02d} {seconds:09.6f}{hemi}"


def GT_geodetic_to_ecef(lat_deg: float, lon_deg: float, h: float = 0.0, ellipsoid: Optional[GT_Ellipsoid] = None) -> tuple[float, float, float]:
    ellipsoid = ellipsoid or GT_ELLIPSOIDS["WGS84"]
    lat = lat_deg * GT_DEG
    lon = lon_deg * GT_DEG
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    n = ellipsoid.a / math.sqrt(1.0 - ellipsoid.e2 * sin_lat * sin_lat)
    x = (n + h) * cos_lat * math.cos(lon)
    y = (n + h) * cos_lat * math.sin(lon)
    z = (n * (1.0 - ellipsoid.e2) + h) * sin_lat
    return x, y, z


def GT_ecef_to_geodetic(x: float, y: float, z: float, ellipsoid: Optional[GT_Ellipsoid] = None) -> tuple[float, float, float]:
    ellipsoid = ellipsoid or GT_ELLIPSOIDS["WGS84"]
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1.0 - ellipsoid.e2))
    for _ in range(20):
        sin_lat = math.sin(lat)
        n = ellipsoid.a / math.sqrt(1.0 - ellipsoid.e2 * sin_lat * sin_lat)
        h = p / max(math.cos(lat), 1e-30) - n
        new_lat = math.atan2(z, p * (1.0 - ellipsoid.e2 * n / (n + h)))
        if abs(new_lat - lat) < 1e-14:
            lat = new_lat
            break
        lat = new_lat
    sin_lat = math.sin(lat)
    n = ellipsoid.a / math.sqrt(1.0 - ellipsoid.e2 * sin_lat * sin_lat)
    h = p / max(math.cos(lat), 1e-30) - n
    return lat / GT_DEG, lon / GT_DEG, h


def GT_meridional_arc(lat_rad: float, ellipsoid: GT_Ellipsoid) -> float:
    e2 = ellipsoid.e2
    a = ellipsoid.a
    return a * (
        (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * lat_rad)
        + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * lat_rad)
        - (35 * e2 ** 3 / 3072) * math.sin(6 * lat_rad)
    )


def GT_utm_zone_from_lon(lon_deg: float) -> int:
    return max(1, min(60, int((lon_deg + 180.0) // 6.0) + 1))


def GT_utm_central_meridian(zone: int) -> float:
    return (int(zone) - 1) * 6.0 - 180.0 + 3.0


def GT_latlon_to_utm(lat_deg: float, lon_deg: float, ellipsoid: Optional[GT_Ellipsoid] = None, zone: Optional[int] = None) -> tuple[float, float, int, str]:
    ellipsoid = ellipsoid or GT_ELLIPSOIDS["WGS84"]
    if zone is None:
        zone = GT_utm_zone_from_lon(lon_deg)
    zone = max(1, min(60, int(zone)))
    hemisphere = "N" if lat_deg >= 0 else "S"
    k0 = 0.9996
    lat = lat_deg * GT_DEG
    lon = lon_deg * GT_DEG
    lon0 = GT_utm_central_meridian(zone) * GT_DEG
    e2 = ellipsoid.e2
    ep2 = ellipsoid.ep2
    n = ellipsoid.a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    t = math.tan(lat) ** 2
    c = ep2 * math.cos(lat) ** 2
    a_term = math.cos(lat) * (lon - lon0)
    m = GT_meridional_arc(lat, ellipsoid)
    easting = k0 * n * (
        a_term
        + (1 - t + c) * a_term ** 3 / 6.0
        + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * a_term ** 5 / 120.0
    ) + 500000.0
    northing = k0 * (
        m
        + n * math.tan(lat) * (
            a_term ** 2 / 2.0
            + (5 - t + 9 * c + 4 * c * c) * a_term ** 4 / 24.0
            + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * a_term ** 6 / 720.0
        )
    )
    if lat_deg < 0:
        northing += 10000000.0
    return easting, northing, zone, hemisphere


def GT_utm_to_latlon(easting: float, northing: float, zone: int, hemisphere: str = "N", ellipsoid: Optional[GT_Ellipsoid] = None) -> tuple[float, float]:
    ellipsoid = ellipsoid or GT_ELLIPSOIDS["WGS84"]
    zone = max(1, min(60, int(zone)))
    k0 = 0.9996
    e2 = ellipsoid.e2
    ep2 = ellipsoid.ep2
    a = ellipsoid.a
    x = easting - 500000.0
    y = northing - (10000000.0 if str(hemisphere).upper().startswith("S") else 0.0)
    m = y / k0
    mu = m / (a * (1 - e2 / 4.0 - 3.0 * e2 ** 2 / 64.0 - 5.0 * e2 ** 3 / 256.0))
    e1 = (1.0 - math.sqrt(1.0 - e2)) / (1.0 + math.sqrt(1.0 - e2))
    fp = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )
    sin_fp = math.sin(fp)
    cos_fp = math.cos(fp)
    t1 = math.tan(fp) ** 2
    c1 = ep2 * cos_fp ** 2
    r1 = a * (1 - e2) / (1 - e2 * sin_fp ** 2) ** 1.5
    n1 = a / math.sqrt(1 - e2 * sin_fp ** 2)
    d = x / (n1 * k0)
    lat = fp - (n1 * math.tan(fp) / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720
    )
    lon0 = GT_utm_central_meridian(zone) * GT_DEG
    lon = lon0 + (
        d
        - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
    ) / cos_fp
    return lat / GT_DEG, lon / GT_DEG


def GT_apply_helmert7(x: float, y: float, z: float, datum: GT_Datum, inverse: bool = False) -> tuple[float, float, float]:
    dx, dy, dz = datum.dx, datum.dy, datum.dz
    rx = datum.rx_arcsec * GT_ARCSEC
    ry = datum.ry_arcsec * GT_ARCSEC
    rz = datum.rz_arcsec * GT_ARCSEC
    s = datum.scale_ppm * 1e-6
    if inverse:
        dx, dy, dz, rx, ry, rz, s = -dx, -dy, -dz, -rx, -ry, -rz, -s
    return (
        dx + (1.0 + s) * x - rz * y + ry * z,
        dy + rz * x + (1.0 + s) * y - rx * z,
        dz - ry * x + rx * y + (1.0 + s) * z,
    )


def GT_datum_transform(lat_deg: float, lon_deg: float, h: float, source: GT_Datum, target: GT_Datum) -> tuple[float, float, float]:
    x, y, z = GT_geodetic_to_ecef(lat_deg, lon_deg, h, source.ellipsoid)
    xw, yw, zw = GT_apply_helmert7(x, y, z, source, inverse=False)
    xt, yt, zt = GT_apply_helmert7(xw, yw, zw, target, inverse=True)
    return GT_ecef_to_geodetic(xt, yt, zt, target.ellipsoid)


def show_geodetic_tools_dialog(parent: Any = None) -> None:
    """Open a compact Tkinter geodetic conversion toolbox."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title("GEOCALC")
    win.geometry("920x620")

    local_settings = load_app_settings()
    current_project_folder = project_root_from_raw_folder(local_settings.raw_folder or str(default_raw_folder()))
    project_options = discover_project_options()
    if str(current_project_folder) not in project_options:
        project_options.insert(0, str(current_project_folder))
    grid_options = discover_grid_options(current_project_folder)

    notebook = ttk.Notebook(win)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)

    def show_error(exc: Exception) -> None:
        messagebox.showerror("GEOCALC", str(exc), parent=win)

    def set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    # LLH / XYZ tab
    llh_tab = ttk.Frame(notebook, padding=10)
    notebook.add(llh_tab, text="LLH / XYZ")
    lat_var = tk.StringVar(value="24.8607")
    lon_var = tk.StringVar(value="67.0011")
    h_var = tk.StringVar(value="0.0")
    x_var = tk.StringVar()
    y_var = tk.StringVar()
    z_var = tk.StringVar()
    result1 = tk.Text(llh_tab, height=12, width=42, state="disabled")
    for r, (label, var) in enumerate([("Latitude", lat_var), ("Longitude", lon_var), ("Height", h_var), ("X", x_var), ("Y", y_var), ("Z", z_var)]):
        ttk.Label(llh_tab, text=label).grid(row=r, column=0, sticky="e", padx=(0, 6), pady=3)
        ttk.Entry(llh_tab, textvariable=var, width=28).grid(row=r, column=1, sticky="ew", pady=3)
    result1.grid(row=0, column=2, rowspan=7, sticky="nsew", padx=(12, 0))
    llh_tab.columnconfigure(1, weight=1)
    llh_tab.columnconfigure(2, weight=1)

    def llh_to_xyz() -> None:
        try:
            x, y, z = GT_geodetic_to_ecef(GT_parse_angle(lat_var.get()), GT_parse_angle(lon_var.get()), float(h_var.get() or 0.0))
            x_var.set(f"{x:.4f}")
            y_var.set(f"{y:.4f}")
            z_var.set(f"{z:.4f}")
            set_text(result1, f"X: {x:.4f}\nY: {y:.4f}\nZ: {z:.4f}")
        except Exception as exc:
            show_error(exc)

    def xyz_to_llh() -> None:
        try:
            lat, lon, h = GT_ecef_to_geodetic(float(x_var.get()), float(y_var.get()), float(z_var.get()))
            lat_var.set(f"{lat:.10f}")
            lon_var.set(f"{lon:.10f}")
            h_var.set(f"{h:.4f}")
            set_text(result1, f"Latitude: {lat:.10f}\nLongitude: {lon:.10f}\nHeight: {h:.4f}\n{GT_format_dms(lat, True)}\n{GT_format_dms(lon, False)}")
        except Exception as exc:
            show_error(exc)

    ttk.Button(llh_tab, text="LLH to XYZ", command=llh_to_xyz).grid(row=6, column=0, sticky="ew", pady=(8, 0))
    ttk.Button(llh_tab, text="XYZ to LLH", command=xyz_to_llh).grid(row=6, column=1, sticky="ew", pady=(8, 0))

    # UTM tab
    utm_tab = ttk.Frame(notebook, padding=10)
    notebook.add(utm_tab, text="UTM")
    ulat_var = tk.StringVar(value="24.8607")
    ulon_var = tk.StringVar(value="67.0011")
    zone_var = tk.StringVar(value="Auto")
    hemi_var = tk.StringVar(value="N")
    east_var = tk.StringVar()
    north_var = tk.StringVar()
    result2 = tk.Text(utm_tab, height=12, width=42, state="disabled")
    for r, (label, widget) in enumerate([
        ("Latitude", ttk.Entry(utm_tab, textvariable=ulat_var, width=28)),
        ("Longitude", ttk.Entry(utm_tab, textvariable=ulon_var, width=28)),
        ("Zone", ttk.Entry(utm_tab, textvariable=zone_var, width=28)),
        ("Hemisphere", ttk.Combobox(utm_tab, textvariable=hemi_var, values=["N", "S"], width=25, state="readonly")),
        ("Easting", ttk.Entry(utm_tab, textvariable=east_var, width=28)),
        ("Northing", ttk.Entry(utm_tab, textvariable=north_var, width=28)),
    ]):
        ttk.Label(utm_tab, text=label).grid(row=r, column=0, sticky="e", padx=(0, 6), pady=3)
        widget.grid(row=r, column=1, sticky="ew", pady=3)
    result2.grid(row=0, column=2, rowspan=7, sticky="nsew", padx=(12, 0))
    utm_tab.columnconfigure(1, weight=1)
    utm_tab.columnconfigure(2, weight=1)

    def ll_to_utm() -> None:
        try:
            zone_text = zone_var.get().strip()
            zone = None if not zone_text or zone_text.lower() == "auto" else int(zone_text)
            e, n, zone_out, hemi = GT_latlon_to_utm(GT_parse_angle(ulat_var.get()), GT_parse_angle(ulon_var.get()), zone=zone)
            east_var.set(f"{e:.4f}")
            north_var.set(f"{n:.4f}")
            zone_var.set(str(zone_out))
            hemi_var.set(hemi)
            set_text(result2, f"Easting: {e:.4f}\nNorthing: {n:.4f}\nZone: {zone_out}{hemi}")
        except Exception as exc:
            show_error(exc)

    def utm_to_ll() -> None:
        try:
            lat, lon = GT_utm_to_latlon(float(east_var.get()), float(north_var.get()), int(zone_var.get()), hemi_var.get())
            ulat_var.set(f"{lat:.10f}")
            ulon_var.set(f"{lon:.10f}")
            set_text(result2, f"Latitude: {lat:.10f}\nLongitude: {lon:.10f}\n{GT_format_dms(lat, True)}\n{GT_format_dms(lon, False)}")
        except Exception as exc:
            show_error(exc)

    ttk.Button(utm_tab, text="LL to UTM", command=ll_to_utm).grid(row=6, column=0, sticky="ew", pady=(8, 0))
    ttk.Button(utm_tab, text="UTM to LL", command=utm_to_ll).grid(row=6, column=1, sticky="ew", pady=(8, 0))

    # Datum / CRS tab
    datum_tab = ttk.Frame(notebook, padding=10)
    notebook.add(datum_tab, text="Datum")
    src_var = tk.StringVar(value="WGS84")
    dst_var = tk.StringVar(value="WGS84")
    crs_options = (
        ["WGS84 Geographic Lat/Lon"]
        + [f"WGS84 / UTM Zone {zone}{hemi}" for hemi in ("N", "S") for zone in range(1, 61)]
        + ["Lambert Conical Zone IA", "Lambert Conical Zone IIA", "Local Grid Shift"]
    )
    src_crs_var = tk.StringVar(value="WGS84 Geographic Lat/Lon")
    dst_crs_var = tk.StringVar(value="WGS84 / UTM Zone 42N")
    dlat_var = tk.StringVar(value="24.8607")
    dlon_var = tk.StringVar(value="67.0011")
    dh_var = tk.StringVar(value="0.0")
    shift_e_var = tk.StringVar(value=str(getattr(local_settings.geodetic, "delta_x", 0.0)))
    shift_n_var = tk.StringVar(value=str(getattr(local_settings.geodetic, "delta_y", 0.0)))
    shift_h_var = tk.StringVar(value=str(getattr(local_settings.geodetic, "delta_z", 0.0)))
    shift_scale_var = tk.StringVar(value=str(getattr(local_settings.geodetic, "delta_scale", 0.0)))
    result3 = tk.Text(datum_tab, height=16, width=66, state="disabled")
    rows = [
        ("Source datum / CRS", ttk.Combobox(datum_tab, textvariable=src_crs_var, values=crs_options, state="readonly", width=34)),
        ("Target datum / CRS", ttk.Combobox(datum_tab, textvariable=dst_crs_var, values=crs_options, state="readonly", width=34)),
        ("Input 1: Latitude / Northing", ttk.Entry(datum_tab, textvariable=dlat_var)),
        ("Input 2: Longitude / Easting", ttk.Entry(datum_tab, textvariable=dlon_var)),
        ("Height", ttk.Entry(datum_tab, textvariable=dh_var)),
        ("Local shift Easting/X", ttk.Entry(datum_tab, textvariable=shift_e_var)),
        ("Local shift Northing/Y", ttk.Entry(datum_tab, textvariable=shift_n_var)),
        ("Local shift Height/Z", ttk.Entry(datum_tab, textvariable=shift_h_var)),
        ("Local scale ppm", ttk.Entry(datum_tab, textvariable=shift_scale_var)),
    ]
    for r, (label, widget) in enumerate(rows):
        ttk.Label(datum_tab, text=label).grid(row=r, column=0, sticky="e", padx=(0, 6), pady=3)
        widget.grid(row=r, column=1, sticky="ew", pady=3)
    result3.grid(row=0, column=2, rowspan=10, sticky="nsew", padx=(12, 0))
    datum_tab.columnconfigure(1, weight=1)
    datum_tab.columnconfigure(2, weight=1)

    def lambert_params(option: str) -> tuple[float, float, float, float, float, float]:
        text = str(option).upper()
        if "IIA" in text:
            return 70.0, 28.0, 28.0, 28.0, 500000.0, 0.0
        return 68.0, 24.0, 24.0, 24.0, 500000.0, 0.0

    def crs_to_llh(option: str, input1: float, input2: float, height: float) -> tuple[float, float, float]:
        text = str(option)
        if text == "WGS84 Geographic Lat/Lon":
            return input1, input2, height
        match = re.search(r"UTM Zone\s+(\d+)([NS])", text, re.IGNORECASE)
        if match:
            lat, lon = GT_utm_to_latlon(float(input2), float(input1), int(match.group(1)), match.group(2).upper())
            return lat, lon, height
        if "Lambert Conical" in text:
            cm, lat0, sp1, sp2, fe, fn = lambert_params(text)
            lat, lon = lambert_conformal_conic_to_latlon(float(input2), float(input1), cm, lat0, sp1, sp2, fe, fn)
            return lat, lon, height
        raise ValueError("Local Grid Shift is a grid-to-grid operation. Use it as either source or target with projected X/Y values.")

    def llh_to_crs(option: str, lat: float, lon: float, height: float) -> tuple[float, float, float, str]:
        text = str(option)
        if text == "WGS84 Geographic Lat/Lon":
            return lat, lon, height, "Latitude, Longitude, Height"
        match = re.search(r"UTM Zone\s+(\d+)([NS])", text, re.IGNORECASE)
        if match:
            easting, northing, zone_out, hemi = GT_latlon_to_utm(float(lat), float(lon), zone=int(match.group(1)))
            return northing, easting, height, f"Northing, Easting, Height | Zone {zone_out}{hemi}"
        if "Lambert Conical" in text:
            cm, lat0, sp1, sp2, fe, fn = lambert_params(text)
            easting, northing = lambert_conformal_conic_to_xy(float(lat), float(lon), cm, lat0, sp1, sp2, fe, fn)
            return northing, easting, height, "Northing, Easting, Height"
        raise ValueError(f"Unsupported target CRS: {option}")

    def apply_local_shift(input1: float, input2: float, height: float, inverse: bool = False) -> tuple[float, float, float]:
        shift_e = parse_float(shift_e_var.get()) or 0.0
        shift_n = parse_float(shift_n_var.get()) or 0.0
        shift_h = parse_float(shift_h_var.get()) or 0.0
        scale = 1.0 + ((parse_float(shift_scale_var.get()) or 0.0) * 1e-6)
        if inverse:
            return (input1 - shift_n) / scale, (input2 - shift_e) / scale, (height - shift_h) / scale
        return input1 * scale + shift_n, input2 * scale + shift_e, height * scale + shift_h

    def transform_datum() -> None:
        try:
            input1 = GT_parse_angle(dlat_var.get()) if src_crs_var.get() == "WGS84 Geographic Lat/Lon" else float(dlat_var.get())
            input2 = GT_parse_angle(dlon_var.get()) if src_crs_var.get() == "WGS84 Geographic Lat/Lon" else float(dlon_var.get())
            height = float(dh_var.get() or 0.0)
            if src_crs_var.get() == "Local Grid Shift":
                shifted_1, shifted_2, shifted_h = apply_local_shift(input1, input2, height, inverse=True)
                set_text(
                    result3,
                    "Local grid inverse shift\n"
                    f"Input Northing/Y: {input1:.4f}\nInput Easting/X: {input2:.4f}\n"
                    f"Output Northing/Y: {shifted_1:.4f}\nOutput Easting/X: {shifted_2:.4f}\nHeight/Z: {shifted_h:.4f}",
                )
                return
            lat_src, lon_src, h_src = crs_to_llh(src_crs_var.get(), input1, input2, height)
            lat_dst, lon_dst, h_dst = GT_datum_transform(lat_src, lon_src, h_src, GT_DATUMS[src_var.get()], GT_DATUMS[dst_var.get()])
            if dst_crs_var.get() == "Local Grid Shift":
                northing, easting, shifted_h = apply_local_shift(lat_dst, lon_dst, h_dst, inverse=False)
                set_text(
                    result3,
                    "Datum transform with local shift target\n"
                    f"Intermediate latitude: {lat_dst:.10f}\nIntermediate longitude: {lon_dst:.10f}\n"
                    f"Shifted value 1: {northing:.4f}\nShifted value 2: {easting:.4f}\nHeight/Z: {shifted_h:.4f}\n"
                    f"{src_crs_var.get()} -> Local Grid Shift",
                )
                return
            out1, out2, out_h, out_label = llh_to_crs(dst_crs_var.get(), lat_dst, lon_dst, h_dst)
            set_text(
                result3,
                f"Output ({out_label})\n"
                f"Value 1: {out1:.4f}\nValue 2: {out2:.4f}\nHeight: {out_h:.4f}\n\n"
                f"Latitude: {lat_dst:.10f}\nLongitude: {lon_dst:.10f}\n"
                f"{GT_format_dms(lat_dst, True)}\n{GT_format_dms(lon_dst, False)}\n"
                f"{src_crs_var.get()} -> {dst_crs_var.get()}",
            )
        except Exception as exc:
            show_error(exc)

    ttk.Button(datum_tab, text="Transform Datum / CRS", command=transform_datum).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    # Projects / Grids tab
    project_grid_tab = ttk.Frame(notebook, padding=10)
    notebook.add(project_grid_tab, text="Projects / Grids")
    project_choice_var = tk.StringVar(value=str(current_project_folder))
    grid_choice_var = tk.StringVar(value=getattr(local_settings.geodetic, "selected_grid_path", "") or (grid_options[0] if grid_options else ""))
    project_status_var = tk.StringVar(value=f"{len(project_options)} project option(s), {len(grid_options)} grid/background file option(s)")

    ttk.Label(project_grid_tab, text="Available project").grid(row=0, column=0, sticky="w", padx=6, pady=5)
    project_combo = ttk.Combobox(project_grid_tab, textvariable=project_choice_var, values=project_options, width=72)
    project_combo.grid(row=0, column=7, columnspan=2, sticky="ew", padx=6, pady=5)
    ttk.Label(project_grid_tab, text="Available grid / background").grid(row=1, column=0, sticky="w", padx=6, pady=5)
    grid_combo = ttk.Combobox(project_grid_tab, textvariable=grid_choice_var, values=grid_options, width=72)
    grid_combo.grid(row=1, column=1, columnspan=2, sticky="ew", padx=6, pady=5)
    ttk.Label(project_grid_tab, textvariable=project_status_var, foreground="#244d9a").grid(row=2, column=0, columnspan=3, sticky="ew", padx=6, pady=(0, 8))

    def refresh_project_grid_options(_event: Any = None) -> None:
        nonlocal grid_options
        grid_options = discover_grid_options(project_choice_var.get())
        grid_combo.configure(values=grid_options)
        project_status_var.set(f"{len(project_options)} project option(s), {len(grid_options)} grid/background file option(s)")
        if grid_options and not grid_choice_var.get():
            grid_choice_var.set(grid_options[0])

    def browse_geocalc_project() -> None:
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="Select project folder", initialdir=project_choice_var.get() or str(APP_DIR), parent=win)
        if folder:
            project_choice_var.set(folder)
            if folder not in project_options:
                project_options.insert(0, folder)
                project_combo.configure(values=project_options)
            refresh_project_grid_options()

    def browse_geocalc_grid() -> None:
        from tkinter import filedialog
        name = filedialog.askopenfilename(
            title="Select grid/background file",
            initialdir=project_choice_var.get() or str(APP_DIR),
            filetypes=[("Grid/background files", "*.grd *.grid *.gtx *.gsb *.ktd *.KTD *.csv *.xyz *.brd *.lnw *.dxf *.dwg *.DWG *.000 *.enc *.s57 *.kmz *.kml"), ("All files", "*.*")],
            parent=win,
        )
        if name:
            grid_choice_var.set(name)
            if name not in grid_options:
                grid_options.insert(0, name)
                grid_combo.configure(values=grid_options)

    def apply_geocalc_project_grid() -> None:
        try:
            project_folder = project_choice_var.get().strip()
            if project_folder:
                _project_folder, raw_path, _edit_path, _sort_path = ensure_project_subfolders_from_raw(project_folder)
                local_settings.raw_folder = str(raw_path)
            local_settings.geodetic.selected_grid_path = grid_choice_var.get().strip()
            local_settings.save(DEFAULT_SETTINGS_PATH)
            project_status_var.set("Project/grid selection saved to Settings.")
        except Exception as exc:
            show_error(exc)

    project_combo.bind("<<ComboboxSelected>>", refresh_project_grid_options)
    ttk.Button(project_grid_tab, text="Browse Project", command=browse_geocalc_project).grid(row=3, column=0, sticky="ew", padx=6, pady=5)
    ttk.Button(project_grid_tab, text="Browse Grid", command=browse_geocalc_grid).grid(row=3, column=1, sticky="ew", padx=6, pady=5)
    ttk.Button(project_grid_tab, text="Apply to Settings", command=apply_geocalc_project_grid).grid(row=3, column=2, sticky="ew", padx=6, pady=5)
    project_grid_tab.columnconfigure(1, weight=1)

    ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 10))

# ======================================================================
# END GEODETIC_TOOLS_MERGE.py
# ======================================================================

def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog, ttk
    except Exception as exc:
        print(f"Tkinter is not available: {exc}", file=sys.stderr)
        return 2

    model = SurveyModel()
    runlines = RunLineSet()
    settings = load_app_settings()
    _project_folder, _raw_folder, _edit_folder, _sort_folder = ensure_project_subfolders_from_raw(settings.raw_folder)
    settings.raw_folder = str(_raw_folder)
    try:
        settings.save(DEFAULT_SETTINGS_PATH)
    except Exception:
        pass
    project = HydroProject()
    border_points: list[tuple[float, float]] = []
    dxf_background_shapes: list[MapShape] = []
    enc_background_shapes: list[MapShape] = []
    kmz_background_shapes: list[MapShape] = []
    ktd_extent_shapes: list[MapShape] = []
    project_background_points: list[SurveyPoint] = []
    project_background_files: list[str] = []
    background_file_state: dict[str, str] = {}
    background_bbox_cache: dict[int, tuple[float, float, float, float]] = {}

    def simplify_background_shape(shape: MapShape, max_points: int = 700) -> MapShape:
        if shape.kind != "line" or len(shape.points) <= max_points:
            return shape
        step = max(1, len(shape.points) // max_points)
        pts = shape.points[::step]
        if pts and pts[-1] != shape.points[-1]:
            pts.append(shape.points[-1])
        return MapShape(shape.kind, pts, text=shape.text, color=shape.color, width=shape.width, attrs=dict(shape.attrs))

    def rebuild_background_cache(*shape_groups: list[MapShape]) -> None:
        background_bbox_cache.clear()
        for shapes in shape_groups:
            for shape in shapes:
                bbox = shape_bbox(shape)
                if bbox is not None:
                    background_bbox_cache[id(shape)] = bbox

    def cached_shape_bbox(shape: MapShape) -> Optional[tuple[float, float, float, float]]:
        bbox = background_bbox_cache.get(id(shape))
        if bbox is None:
            bbox = shape_bbox(shape)
            if bbox is not None:
                background_bbox_cache[id(shape)] = bbox
        return bbox

    root = tk.Tk()
    logging_active = tk.BooleanVar(value=False)
    logging_started_at = tk.StringVar(value="")
    root.title(APP_TITLE)
    root.geometry("1100x720")
    root.minsize(880, 560)
    try:
        root.state("zoomed")
    except Exception:
        try:
            root.attributes("-zoomed", True)
        except Exception:
            pass

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    THEME_SCHEMES: dict[str, dict[str, str]] = {
        "Nautical Light": {
            "window": "#f5f7f8",
            "panel": "#ffffff",
            "button": "#e8eef2",
            "button_active": "#d8e5ec",
            "text": "#111827",
            "accent": "#0f6b8d",
            "canvas": CHART_BACKGROUND_COLOR,
            "grid": CHART_GRID_COLOR,
        },
        "High Contrast": {
            "window": "#101418",
            "panel": "#18212a",
            "button": "#263442",
            "button_active": "#34506a",
            "text": "#f8fafc",
            "accent": "#facc15",
            "canvas": "#05070a",
            "grid": "#52616d",
        },
        "Soft Dark": {
            "window": "#1e242b",
            "panel": "#27313b",
            "button": "#334252",
            "button_active": "#3e556b",
            "text": "#eef4f8",
            "accent": "#7dd3fc",
            "canvas": "#101418",
            "grid": "#374754",
        },
        "Classic Gray": {
            "window": "#e5e7eb",
            "panel": "#f3f4f6",
            "button": "#d1d5db",
            "button_active": "#c3cad4",
            "text": "#111111",
            "accent": "#244d9a",
            "canvas": "#f8fafc",
            "grid": "#9ca3af",
        },
    }
    theme_colors = dict(THEME_SCHEMES.get(getattr(settings, "ui_color_scheme", "Nautical Light"), THEME_SCHEMES["Nautical Light"]))

    def bounded_font(value: object, fallback: int = 10) -> int:
        try:
            return max(8, min(30, int(float(str(value)))))
        except Exception:
            return fallback

    def apply_theme() -> None:
        nonlocal theme_colors
        scheme_name = getattr(settings, "ui_color_scheme", "Nautical Light")
        theme_colors = dict(THEME_SCHEMES.get(scheme_name, THEME_SCHEMES["Nautical Light"]))
        contrast = str(getattr(settings, "ui_contrast", "Standard") or "Standard")
        if contrast == "High":
            theme_colors["button"] = theme_colors.get("button_active", theme_colors["button"])
        elif contrast == "Low":
            theme_colors["grid"] = "#d1d5db" if scheme_name != "Soft Dark" else "#334155"
        base_font = bounded_font(getattr(settings, "ui_font_size", 10), 10)
        button_font = bounded_font(getattr(settings, "ui_button_font_size", base_font), base_font)
        menu_font = bounded_font(getattr(settings, "ui_menu_font_size", base_font), base_font)
        root.option_add("*Font", ("Segoe UI", base_font))
        root.option_add("*Menu.Font", ("Segoe UI", menu_font))
        root.option_add("*Menu.background", theme_colors["panel"])
        root.option_add("*Menu.foreground", theme_colors["text"])
        root.option_add("*Menu.activeBackground", theme_colors["button_active"])
        root.option_add("*Menu.activeForeground", theme_colors["text"])
        try:
            root.configure(bg=theme_colors["window"])
        except Exception:
            pass
        style.configure(".", font=("Segoe UI", base_font), background=theme_colors["window"], foreground=theme_colors["text"])
        style.configure("TFrame", background=theme_colors["window"])
        style.configure("TLabelframe", background=theme_colors["window"], foreground=theme_colors["text"])
        style.configure("TLabelframe.Label", background=theme_colors["window"], foreground=theme_colors["text"], font=("Segoe UI", base_font, "bold"))
        style.configure("TNotebook", background=theme_colors["window"])
        style.configure("TNotebook.Tab", font=("Segoe UI", base_font), padding=(10, 5))
        style.configure("TLabel", background=theme_colors["window"], foreground=theme_colors["text"], font=("Segoe UI", base_font))
        style.configure("TCheckbutton", background=theme_colors["window"], foreground=theme_colors["text"], font=("Segoe UI", base_font))
        style.configure("TRadiobutton", background=theme_colors["window"], foreground=theme_colors["text"], font=("Segoe UI", base_font))
        style.configure("TButton", font=("Segoe UI", button_font), padding=(8, 5), background=theme_colors["button"], foreground=theme_colors["text"])
        style.map("TButton", background=[("active", theme_colors["button_active"])])
        style.configure("SurveyPlan.TButton", font=("Segoe UI", button_font, "bold"), padding=(8, 5), background=theme_colors["button"], foreground=theme_colors["text"])
        style.map("SurveyPlan.TButton", background=[("active", theme_colors["button_active"])])
        style.configure("SurveyPlan.TLabel", font=("Segoe UI", base_font), background=theme_colors["window"], foreground=theme_colors["text"])
        style.configure("TEntry", font=("Segoe UI", base_font))
        style.configure("TCombobox", font=("Segoe UI", base_font))

    apply_theme()

    tide_var = tk.StringVar(value=f"{model.tide:.3f}")
    draft_var = tk.StringVar(value=f"{model.draft:.3f}")
    sv_var = tk.StringVar(value=str(model.sound_speed or DEFAULT_SOUND_SPEED))
    status_var = tk.StringVar(value="Ready")
    view_mode = tk.StringVar(value="plan")
    cad_tool = tk.StringVar(value="line")
    follow_vessel = tk.BooleanVar(value=False)
    nav_scale_var = tk.StringVar(value=getattr(settings, "nav_view_scale", "1:200") or "1:200")
    selected_line_index = tk.IntVar(value=-1)
    selected_line_name_var = tk.StringVar(value="No Line")
    use_selected_line = tk.BooleanVar(value=False)
    auto_select_nearest_line = tk.BooleanVar(value=False)
    line_selector_widgets: dict[str, Any] = {}
    NAV_SCALE_OPTIONS = ["Fit", "1:10", "1:20", "1:50", "1:100", "1:200", "1:500", "1:1000", "1:2000", "1:5000", "1:10000", "1:25000", "1:50000", "1:100000", "1:200000"]

    def nav_scale_to_span_m(scale_text: str) -> Optional[float]:
        text = str(scale_text or "").strip()
        if not text or text.lower() == "fit":
            return None
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return None
        value = float(match.group(1))
        return value if value > 0 else None

    def save_nav_scale_setting(*_args: Any) -> None:
        chosen = nav_scale_var.get() or "1:200"
        settings.nav_view_scale = chosen
        try:
            save_default_settings()
        except Exception:
            pass
        view_state["zoom"] = 1.0
        try:
            request_draw_view()
        except Exception:
            pass

    def enabled_line_indices() -> list[int]:
        return [index for index, line in enumerate(runlines.lines) if line.enabled]

    def refresh_line_selector_options() -> None:
        names = [line.name for line in runlines.lines if line.enabled]
        combo = line_selector_widgets.get("combo")
        try:
            if combo is not None:
                combo.configure(values=names or ["No Line"])
        except Exception:
            pass
        selected = selected_navigation_line()
        if selected is not None:
            selected_line_name_var.set(selected.name)
        elif names:
            selected_line_name_var.set(names[0])
        else:
            selected_line_name_var.set("No Line")

    def selected_navigation_line() -> Optional[RunLine]:
        index = selected_line_index.get()
        if 0 <= index < len(runlines.lines) and runlines.lines[index].enabled:
            return runlines.lines[index]
        return None

    def select_line_index(index: int, announce: bool = True, keep_auto: bool = False) -> bool:
        enabled = enabled_line_indices()
        if not enabled:
            selected_line_index.set(-1)
            if announce:
                status_var.set("No enabled runlines are available.")
            return False
        if index not in enabled:
            index = enabled[0]
        selected_line_index.set(index)
        selected_line_name_var.set(runlines.lines[index].name)
        use_selected_line.set(True)
        if not keep_auto:
            auto_select_nearest_line.set(False)
        if announce:
            status_var.set(f"Selected navigation line: {runlines.lines[index].name}")
        try:
            draw_view()
            refresh_data_display()
        except Exception:
            pass
        return True

    def select_next_line() -> None:
        enabled = enabled_line_indices()
        if not enabled:
            selected_line_index.set(-1)
            status_var.set("No enabled runlines are available for NEXT LINE.")
            return
        current = selected_line_index.get()
        if current in enabled:
            next_index = enabled[(enabled.index(current) + 1) % len(enabled)]
        else:
            next_index = enabled[0]
        select_line_index(next_index, announce=True)

    def select_previous_line() -> None:
        enabled = enabled_line_indices()
        if not enabled:
            selected_line_index.set(-1)
            status_var.set("No enabled runlines are available for PREVIOUS LINE.")
            return
        current = selected_line_index.get()
        if current in enabled:
            previous_index = enabled[(enabled.index(current) - 1) % len(enabled)]
        else:
            previous_index = enabled[-1]
        select_line_index(previous_index, announce=True)

    def swap_selected_line_direction() -> None:
        index = selected_line_index.get()
        if not (0 <= index < len(runlines.lines)):
            status_var.set("No selected line is available to swap direction.")
            return
        line = runlines.lines[index]
        line.start_x, line.end_x = line.end_x, line.start_x
        line.start_y, line.end_y = line.end_y, line.start_y
        selected_line_name_var.set(line.name)
        use_selected_line.set(True)
        auto_select_nearest_line.set(False)
        status_var.set(f"Swapped line direction: {line.name}")
        try:
            draw_view()
            refresh_data_display()
        except Exception:
            pass

    def select_nearest_line_to_position(x: float, y: float, announce: bool = False) -> Optional[NavigationSolution]:
        candidates = [
            (index, line.navigation(x, y))
            for index, line in enumerate(runlines.lines)
            if line.enabled
        ]
        if not candidates:
            selected_line_index.set(-1)
            return None
        index, solution = min(candidates, key=lambda item: item[1].absolute_cross_track)
        if selected_line_index.get() != index:
            selected_line_index.set(index)
            selected_line_name_var.set(solution.line.name)
            use_selected_line.set(True)
            if announce:
                status_var.set(f"Auto selected nearest line: {solution.line.name}")
        return solution

    def select_line_by_name(_event: Any = None) -> None:
        chosen = selected_line_name_var.get()
        for index, line in enumerate(runlines.lines):
            if line.enabled and line.name == chosen:
                select_line_index(index, announce=True)
                return
        refresh_line_selector_options()

    view_state: dict[str, Any] = {
        "zoom": 1.0,
        "pan_x": 0.0,
        "pan_y": 0.0,
        "drag": None,
        "zoom_window": False,
        "zoom_rect": None,
        "zoom_start": None,
        "last_span_x": 1.0,
        "last_span_y": 1.0,
        "last_min_x": 0.0,
        "last_max_x": 1.0,
        "last_min_y": 0.0,
        "last_max_y": 1.0,
        "full_center_x": 0.0,
        "full_center_y": 0.0,
        "full_span_x": 1.0,
        "full_span_y": 1.0,
        "last_plot_left": 32.0,
        "last_plot_top": 32.0,
        "last_plot_right": 100.0,
        "last_plot_bottom": 100.0,
        "last_plot_width": 1.0,
        "last_plot_height": 1.0,
        "draw_pending": False,
        "is_panning": False,
    }
    cad_state: dict[str, Any] = {"start": None, "preview": None}
    io_monitor_stop_event = threading.Event()
    io_monitor_threads: list[threading.Thread] = []
    io_monitor_thread_keys: set[str] = set()
    io_monitor_displays: list[dict[str, Any]] = []
    io_monitor_last_record_times: dict[str, float] = {}
    live_raw_records: list[tuple[str, str, str]] = []
    live_raw_lock = threading.Lock()
    latest_sensor_data: dict[str, Any] = {}
    live_echogram_points: list[SurveyPoint] = []
    echogram_replay_state: dict[str, Any] = {"active": False, "index": 0, "points": []}
    echogram_display_state: dict[str, Any] = {"win": None, "canvas": None, "auto": False, "after": None}
    echogram_save_state: dict[str, Any] = {"signature": None, "path": None}
    data_display_state: dict[str, Any] = {"win": None, "font_size": 18, "selected": {}, "display_order": [], "config_visible": False}
    toolbar_state: dict[str, Any] = {
        "dock": "top",
        "order": [
            "SBES",
            "Multi-Beam Sys",
            "Start Logging",
            "Stop Log&Save",
            "Zoom +",
            "Zoom -",
            "Zoom Window",
            "Extents",
        ],
    }
    DATA_DISPLAY_FIELDS = [
        ("logging", "Logging Status"),
        ("time", "Time"),
        ("easting", "Easting"),
        ("northing", "Northing"),
        ("latitude", "Latitude"),
        ("longitude", "Longitude"),
        ("heading", "HDG"),
        ("course", "COG"),
        ("speed", "Speed"),
        ("line", "Selected Line"),
        ("course_to_steer", "Course to Steer"),
        ("xte", "Cross Track"),
        ("depth", "Depth"),
        ("corrected_depth", "Corrected Depth"),
        ("altitude", "Ellipsoidal Height"),
        ("ktd_file", "KTD File"),
        ("ktd_cell_k", "KTD K at Position"),
        ("rtk_reduced_height", "RTK H-K Height"),
        ("rtk_reduced_depth", "RTK Corrected Depth"),
        ("rtk_tide", "RTK Tide"),
        ("chart_datum", "Chart Datum"),
        ("rtk_method", "RTK Tide Method"),
        ("gnss_sentence_type", "GNSS Sentence"),
        ("gnss_latest_ascii", "Latest GNSS ASCII"),
        ("quality", "Quality"),
        ("source", "Source"),
    ]
    data_display_state["display_order"] = [field_key for field_key, _field_label in DATA_DISPLAY_FIELDS]
    for field_key, _field_label in DATA_DISPLAY_FIELDS:
        data_display_state["selected"][field_key] = field_key in {
            "logging", "easting", "northing", "heading", "course_to_steer", "line", "xte", "depth",
            "ktd_cell_k", "rtk_reduced_height", "rtk_tide"
        }

    def save_default_settings() -> None:
        _project_folder, raw_path, _edit_path, _sort_path = ensure_project_subfolders_from_raw(settings.raw_folder)
        settings.raw_folder = str(raw_path)
        settings.save(DEFAULT_SETTINGS_PATH)

    def configured_devices() -> list[SerialSettings]:
        devices: list[SerialSettings] = [settings.gps, settings.sensor]
        devices.extend(settings.extra_devices)
        return [device for device in devices if getattr(device, "enabled", True)]

    def is_sbes_device(device: SerialSettings) -> bool:
        try:
            return device is settings.sensor or str(device.title).strip().upper() == str(settings.sensor.title).strip().upper() or str(device.port).strip().upper() == str(settings.sensor.port).strip().upper()
        except Exception:
            return False

    def add_live_echogram_depth(depth: float, stamp: str, source: str) -> None:
        try:
            value = float(depth)
        except Exception:
            return
        point = SurveyPoint(time=stamp, raw_depth=value, source=source)
        point.tide = model.tide
        point.draft = model.draft
        point.sound_speed = model.sound_speed
        live_echogram_points.append(point)
        if len(live_echogram_points) > 2400:
            del live_echogram_points[:-2400]
        if view_mode.get() == "echogram":
            root.after(0, request_draw_view)

    def show_live_echogram_display(auto: bool = False) -> None:
        existing = echogram_display_state.get("win")
        try:
            if existing is not None and existing.winfo_exists():
                echogram_display_state["auto"] = bool(auto)
                existing.lift()
                draw_echogram(echogram_display_state.get("canvas"))
                return
        except Exception:
            pass
        win = tk.Toplevel(root)
        win.title("LIVE SBES ECHOGRAM")
        win.geometry("900x520")
        win.minsize(520, 300)
        win.resizable(True, True)
        echogram_canvas = tk.Canvas(win, bg="#05070a", highlightthickness=0)
        echogram_canvas.pack(fill="both", expand=True)
        echogram_display_state.update({"win": win, "canvas": echogram_canvas, "auto": bool(auto)})

        def refresh_loop() -> None:
            try:
                if not win.winfo_exists():
                    return
                draw_echogram(echogram_canvas)
                echogram_display_state["after"] = win.after(350, refresh_loop)
            except Exception:
                return

        def on_close() -> None:
            after_id = echogram_display_state.get("after")
            if after_id is not None:
                try:
                    win.after_cancel(after_id)
                except Exception:
                    pass
            echogram_display_state.update({"win": None, "canvas": None, "auto": False, "after": None})
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        echogram_canvas.bind("<Configure>", lambda _event: draw_echogram(echogram_canvas))
        refresh_loop()

    def close_auto_echogram_display() -> None:
        win = echogram_display_state.get("win")
        if not bool(echogram_display_state.get("auto")):
            return
        try:
            if win is not None and win.winfo_exists():
                win.destroy()
        except Exception:
            pass
        echogram_display_state.update({"win": None, "canvas": None, "auto": False, "after": None})

    def autosave_echogram(reason: str = "stop") -> Optional[Path]:
        source_points = [point for point in live_echogram_points if point.raw_depth is not None or point.corrected_depth is not None]
        if not source_points:
            source_points = [point for point in model.points if point.raw_depth is not None or point.corrected_depth is not None]
        if not source_points:
            return None
        last_point = source_points[-1]
        signature = (
            len(source_points),
            str(last_point.time or ""),
            round(float(last_point.raw_depth if last_point.raw_depth is not None else last_point.corrected_depth or 0.0), 4),
        )
        if echogram_save_state.get("signature") == signature and echogram_save_state.get("path"):
            return Path(str(echogram_save_state["path"]))
        project_folder, _raw_path, _edit_path, _sort_path = ensure_project_subfolders_from_raw(settings.raw_folder)
        echogram_folder = project_folder / "ECHOGRAM"
        echogram_folder.mkdir(parents=True, exist_ok=True)
        safe_reason = safe_name(reason or "autosave").lower()
        output = echogram_folder / f"echogram_{safe_reason}_{datetime.now():%Y%m%d_%H%M%S}.ppm"
        echogram = EchogramModel(depth_bins=180)
        echogram.build_from_points(source_points)
        try:
            echogram.export_ppm(output)
        except Exception as exc:
            status_var.set(f"Echogram autosave failed: {exc}")
            return None
        echogram_save_state["signature"] = signature
        echogram_save_state["path"] = str(output)
        status_var.set(f"Echogram autosaved: {output}")
        return output

    def start_echogram_replay(points: list[SurveyPoint], delay_ms: int = 80) -> None:
        replay_points = [point for point in points if point.raw_depth is not None or point.corrected_depth is not None]
        if not replay_points:
            messagebox.showinfo(APP_TITLE, "No depth samples are available for echogram replay.")
            return
        echogram_replay_state["active"] = True
        echogram_replay_state["index"] = 0
        echogram_replay_state["points"] = replay_points
        live_echogram_points.clear()
        view_mode.set("echogram")

        def replay_step() -> None:
            if not echogram_replay_state.get("active"):
                return
            idx = int(echogram_replay_state.get("index", 0))
            samples = echogram_replay_state.get("points", [])
            if idx >= len(samples):
                echogram_replay_state["active"] = False
                status_var.set(f"Echogram replay completed: {len(samples)} samples")
                draw_view()
                return
            sample = samples[idx]
            depth = sample.raw_depth if sample.raw_depth is not None else sample.corrected_depth
            if depth is not None:
                add_live_echogram_depth(float(depth), str(sample.time or ""), str(sample.source or "Replay"))
            echogram_replay_state["index"] = idx + 1
            draw_view()
            root.after(delay_ms, replay_step)

        status_var.set(f"Echogram replay started: {len(replay_points)} samples")
        replay_step()

    def stop_echogram_replay() -> None:
        echogram_replay_state["active"] = False
        status_var.set("Echogram replay stopped")
        draw_view()

    def next_raw_path() -> Path:
        _project_folder, raw_path, _edit_path, _sort_path = ensure_project_subfolders_from_raw(settings.raw_folder)
        settings.raw_folder = str(raw_path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        return raw_path / f"SurveyPlan_{stamp}.raw"

    def save_existing_data_to_raw(show_message: bool = True) -> Optional[Path]:
        read_settings()
        path = next_raw_path()
        with live_raw_lock:
            raw_records = list(live_raw_records)
        try:
            if raw_records:
                count = export_live_hypack_raw(path, raw_records)
            else:
                count = model.export_hypack_raw(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not save HYPACK RAW:\n{exc}")
            return None
        if show_message:
            status_var.set(f"Saved {count} records to {path}")
        return path

    def export_live_hypack_raw(path, raw_records):
        return _acquisition.export_records(path, raw_records)

    def start_logging(_event=None):
        try:
            return _acquisition.start()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return "break"

    def stop_logging_and_save(_event=None):
        try:
            return _acquisition.stop()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return "break"

    def read_settings() -> None:
        model.tide = parse_float(tide_var.get()) or 0.0
        model.draft = parse_float(draft_var.get()) or 0.0
        model.sound_speed = parse_float(sv_var.get()) or DEFAULT_SOUND_SPEED
        model.apply_offsets()

    def apply_realtime_rtk_tide(altitude: Optional[float], stamp: str = "", source: str = "") -> None:
        if altitude is None:
            return
        read_settings()
        latest_sensor_data["altitude_m"] = altitude
        ell_h = parse_float(str(altitude))
        if ell_h is None:
            return
        easting = latest_sensor_data.get("easting")
        northing = latest_sensor_data.get("northing")
        k_value = None
        method = str(getattr(settings.geodetic, "rtk_tide_method", "") or "")
        if "KTD" in method.upper() or getattr(settings.geodetic, "ktd_file_path", ""):
            k_value = ktd_cell_value_at(
                getattr(settings.geodetic, "ktd_file_path", ""),
                parse_float(str(easting)) if easting is not None else None,
                parse_float(str(northing)) if northing is not None else None,
            )
        if k_value is not None:
            tide_value = ell_h - float(k_value)
            latest_sensor_data["ktd_cell_k"] = float(k_value)
            latest_sensor_data["rtk_reduced_height"] = tide_value
            raw_depth = parse_float(str(latest_sensor_data.get("raw_depth", "")))
            if raw_depth is not None:
                latest_sensor_data["rtk_reduced_depth"] = raw_depth * (model.sound_speed / DEFAULT_SOUND_SPEED if model.sound_speed else 1.0) + model.draft - tide_value
            status_message = f"Realtime RTK/KTD tide H-K {tide_value:.3f} m | H {ell_h:.3f} K {float(k_value):.3f}"
        else:
            latest_sensor_data["ktd_cell_k"] = None
            latest_sensor_data["rtk_reduced_height"] = None
            latest_sensor_data["rtk_reduced_depth"] = None
            tide_value = compute_rtk_tide_from_ellipsoidal_height(
                ell_h,
                settings.geodetic,
                parse_float(str(easting)) if easting is not None else None,
                parse_float(str(northing)) if northing is not None else None,
            )
            if tide_value is None:
                refresh_data_display()
                return
            status_message = f"RTK tide {tide_value:.3f} m from ellipsoidal height {ell_h:.3f} m"
        latest_sensor_data["rtk_tide"] = tide_value
        latest_sensor_data["chart_datum_level"] = getattr(settings.geodetic, "chart_datum_level", 0.0)
        latest_sensor_data["rtk_tide_method"] = getattr(settings.geodetic, "rtk_tide_method", "")
        tide_var.set(f"{tide_value:.3f}")
        model.tide = tide_value
        for point in live_echogram_points[-500:]:
            point.tide = tide_value
        refresh_data_display()
        status_var.set(status_message)

    def latest_position_point() -> Optional[SurveyPoint]:
        if latest_sensor_data.get("easting") is not None or latest_sensor_data.get("longitude") is not None:
            return ensure_projected_xy(
                SurveyPoint(
                    time=str(latest_sensor_data.get("time", "")),
                    latitude=latest_sensor_data.get("latitude"),
                    longitude=latest_sensor_data.get("longitude"),
                    easting=latest_sensor_data.get("easting"),
                    northing=latest_sensor_data.get("northing"),
                    raw_depth=latest_sensor_data.get("raw_depth"),
                    tide=float(latest_sensor_data.get("rtk_tide", model.tide) or model.tide),
                    quality=str(latest_sensor_data.get("quality", "")),
                    source=str(latest_sensor_data.get("source", "")),
                    speed_knots=latest_sensor_data.get("speed_knots"),
                    course_degrees=latest_sensor_data.get("course_degrees"),
                    heading_degrees=latest_sensor_data.get("heading_degrees"),
                    altitude_m=latest_sensor_data.get("altitude_m"),
                    ktd_cell_k=latest_sensor_data.get("ktd_cell_k"),
                    rtk_reduced_height=latest_sensor_data.get("rtk_reduced_height"),
                    rtk_reduced_depth=latest_sensor_data.get("rtk_reduced_depth"),
                ),
                settings.geodetic,
            )
        for point in reversed(model.points):
            ensure_projected_xy(point, settings.geodetic)
            if point.easting is not None and point.northing is not None:
                return point
        return None

    def latest_depth_point() -> Optional[SurveyPoint]:
        for point in reversed(model.points):
            if point.raw_depth is not None or point.corrected_depth is not None:
                return point
        return None

    def latest_navigation_solution() -> Optional[NavigationSolution]:
        point = latest_position_point()
        if point is None or point.x is None or point.y is None:
            return None
        x = float(point.x)
        y = float(point.y)
        if auto_select_nearest_line.get():
            return select_nearest_line_to_position(x, y)
        if use_selected_line.get():
            line = selected_navigation_line()
            if line is not None:
                return line.navigation(x, y)
        return runlines.nearest(x, y)

    def course_to_steer(solution: Optional[NavigationSolution]) -> Optional[float]:
        if solution is None:
            return None
        correction = max(-30.0, min(30.0, solution.cross_track * 0.08))
        return (solution.line.bearing_degrees - correction) % 360.0

    def data_display_values() -> dict[str, str]:
        position = latest_position_point()
        depth = latest_depth_point()
        solution = latest_navigation_solution()
        ktd_path_text = str(getattr(settings.geodetic, "ktd_file_path", "") or "")
        display_k_value = latest_sensor_data.get("ktd_cell_k")
        if display_k_value is None and position is not None and (position.x is not None or position.y is not None):
            display_k_value = ktd_cell_value_at(ktd_path_text, position.x, position.y)
        display_reduced_height = latest_sensor_data.get("rtk_reduced_height")
        altitude_value = latest_sensor_data.get("altitude_m", position.altitude_m if position else None)
        if display_reduced_height is None and altitude_value is not None and display_k_value is not None:
            display_reduced_height = float(altitude_value) - float(display_k_value)
        display_reduced_depth = latest_sensor_data.get("rtk_reduced_depth")
        raw_depth_value = latest_sensor_data.get("raw_depth", depth.raw_depth if depth else None)
        if display_reduced_depth is None and raw_depth_value is not None and display_reduced_height is not None:
            display_reduced_depth = float(raw_depth_value) * (model.sound_speed / DEFAULT_SOUND_SPEED if model.sound_speed else 1.0) + model.draft - float(display_reduced_height)
        values = {
            "logging": "LOGGING" if logging_active.get() else "NOT LOGGING",
            "time": latest_sensor_data.get("time", position.time if position else ""),
            "easting": format_number(position.easting if position else None),
            "northing": format_number(position.northing if position else None),
            "latitude": format_number(position.latitude if position else None),
            "longitude": format_number(position.longitude if position else None),
            "heading": format_number(latest_sensor_data.get("heading_degrees")),
            "course": format_number(latest_sensor_data.get("course_degrees")),
            "speed": format_number(latest_sensor_data.get("speed_knots")),
            "line": solution.line.name if solution else "",
            "course_to_steer": format_number(course_to_steer(solution)),
            "xte": "" if solution is None else f"{solution.cross_track:.3f} {solution.side}",
            "depth": format_number(raw_depth_value),
            "corrected_depth": format_number(depth.corrected_depth if depth else None),
            "altitude": format_number(altitude_value),
            "ktd_file": Path(ktd_path_text).name if ktd_path_text else "",
            "ktd_cell_k": format_number(display_k_value),
            "rtk_reduced_height": format_number(display_reduced_height),
            "rtk_reduced_depth": format_number(display_reduced_depth),
            "rtk_tide": format_number(latest_sensor_data.get("rtk_tide")),
            "chart_datum": format_number(getattr(settings.geodetic, "chart_datum_level", 0.0)),
            "rtk_method": str(getattr(settings.geodetic, "rtk_tide_method", "")),
            "gnss_sentence_type": latest_sensor_data.get("gnss_sentence_type", ""),
            "gnss_latest_ascii": latest_sensor_data.get("gnss_latest_ascii", ""),
            "quality": latest_sensor_data.get("quality", position.quality if position else ""),
            "source": latest_sensor_data.get("source", depth.source if depth else ""),
        }
        return values

    def refresh_data_display() -> None:
        win = data_display_state.get("win")
        value_vars = data_display_state.get("value_vars", {})
        row_frames = data_display_state.get("row_frames", {})
        if win is None or not hasattr(win, "winfo_exists"):
            return
        try:
            if not win.winfo_exists():
                return
            font_size = int(data_display_state.get("font_size", 18))
            values = data_display_values()
            for field_key, value in values.items():
                if field_key in value_vars:
                    value_vars[field_key].set(value)
            for row in row_frames.values():
                if row.winfo_ismapped():
                    row.pack_forget()
            known_fields = {field_key for field_key, _field_label in DATA_DISPLAY_FIELDS}
            order = [field_key for field_key in data_display_state.get("display_order", []) if field_key in known_fields]
            for field_key, _field_label in DATA_DISPLAY_FIELDS:
                if field_key not in order:
                    order.append(field_key)
            data_display_state["display_order"] = order
            for field_key in order:
                if field_key in row_frames and bool(data_display_state["selected"].get(field_key, True)):
                    row_frames[field_key].pack(fill="x", padx=10, pady=3)
                    for child in row_frames[field_key].winfo_children():
                        try:
                            child.configure(font=("Segoe UI", font_size, "bold" if child.winfo_class() == "TLabel" else "normal"))
                        except Exception:
                            pass
        except Exception:
            pass

    def refresh_table() -> None:
        draw_view()
        refresh_data_display()

    def load_project_background_points(path: Path) -> list[SurveyPoint]:
        temp_model = SurveyModel()
        temp_model.tide = model.tide
        temp_model.draft = model.draft
        temp_model.sound_speed = model.sound_speed
        suffix = path.suffix.lower()
        points_loaded: list[SurveyPoint] = []
        if suffix == ".ktd":
            nodes = load_ktd_nodes(path)
            points_loaded = [
                SurveyPoint(
                    easting=float(x),
                    northing=float(y),
                    raw_depth=float(value),
                    quality="KTD",
                    source=f"KTD BACKGROUND {path.name}",
                )
                for x, y, value in nodes
            ]
        elif suffix in {".nmea", ".log"} and not is_hypack_raw_file(path) and not is_hypack_edited_file(path):
            points_loaded = load_nmea(path)
            for point in points_loaded:
                point.source = f"BACKGROUND {path.name}"
        else:
            temp_model.load_file(path)
            points_loaded = list(temp_model.points)
        for point in points_loaded:
            if point.x is None or point.y is None:
                ensure_projected_xy(point, settings.geodetic)
        return [point for point in points_loaded if point.x is not None and point.y is not None]

    def load_ktd_background_path(path_obj: Path, replace_existing: bool = True) -> int:
        nonlocal ktd_extent_shapes
        if path_obj.suffix.lower() != ".ktd" or not path_obj.exists():
            return 0
        try:
            points_loaded = load_project_background_points(path_obj)
        except Exception as exc:
            status_var.set(f"KTD background load failed: {path_obj.name}: {exc}")
            return 0
        if replace_existing:
            source_tag = f"KTD BACKGROUND {path_obj.name}"
            project_background_points[:] = [
                point for point in project_background_points
                if not str(point.source).upper().startswith("KTD BACKGROUND")
                and str(point.source) != source_tag
            ]
            project_background_files[:] = [
                name for name in project_background_files
                if Path(str(name)).suffix.lower() != ".ktd"
            ]
            ktd_extent_shapes = []
        else:
            source_name = path_obj.name.upper()
            project_background_points[:] = [
                point for point in project_background_points
                if source_name not in str(point.source).upper()
            ]
            project_background_files[:] = [
                name for name in project_background_files
                if normalized_file_key(Path(str(name))) != normalized_file_key(path_obj)
            ]
            ktd_extent_shapes[:] = [
                shape for shape in ktd_extent_shapes
                if normalized_file_key(Path(str(shape.attrs.get("SOURCE", "")))) != normalized_file_key(path_obj)
            ]
        project_background_points.extend(points_loaded)
        if str(path_obj) not in project_background_files:
            project_background_files.append(str(path_obj))
        meta = load_ktd_grid_meta(path_obj)
        if meta:
            try:
                min_x = float(meta.get("min_x", 0.0) or 0.0)
                min_y = float(meta.get("min_y", 0.0) or 0.0)
                max_x = float(meta.get("max_x", min_x) or min_x)
                max_y = float(meta.get("max_y", min_y) or min_y)
                ktd_extent_shapes.append(
                    MapShape(
                        "line",
                        [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y), (min_x, min_y)],
                        text=f"KTD LIMIT {path_obj.name}",
                        color="#111827",
                        width=2,
                        attrs={
                            "SOURCE": str(path_obj),
                            "KTD_LIMIT": True,
                            "VALID_NODES": len(points_loaded),
                            "TOTAL_NODES": int(meta.get("x_count", 0) or 0) * int(meta.get("y_count", 0) or 0),
                        },
                    )
                )
                rebuild_background_cache(dxf_background_shapes, enc_background_shapes, kmz_background_shapes, ktd_extent_shapes)
            except Exception:
                pass
        draw_view()
        if meta:
            total_nodes = int(meta.get("x_count", 0) or 0) * int(meta.get("y_count", 0) or 0)
            percent = (len(points_loaded) / total_nodes * 100.0) if total_nodes else 0.0
            if points_loaded:
                status_var.set(f"Loaded KTD background: {path_obj.name} ({len(points_loaded)} valid K node(s), {percent:.1f}% of grid).")
            else:
                status_var.set(f"KTD extent loaded but no real K cells found: {path_obj.name}. All cells may be 999.99 nodata.")
        else:
            status_var.set(f"Loaded KTD background: {path_obj.name} ({len(points_loaded)} valid K value node(s))")
        return len(points_loaded)

    def add_project_background_files(file_kind: str, initial_folder: Path, patterns: list[tuple[str, str]]) -> None:
        read_settings()
        names = filedialog.askopenfilenames(
            title=f"Load {file_kind} data in background",
            initialdir=str(initial_folder),
            filetypes=patterns + [("All files", "*.*")],
        )
        if not names:
            return
        loaded = 0
        failed: list[str] = []
        for name in names:
            path_obj = Path(name)
            try:
                if path_obj.suffix.lower() == ".ktd":
                    loaded += load_ktd_background_path(path_obj, replace_existing=(loaded == 0))
                else:
                    points_loaded = load_project_background_points(path_obj)
                    project_background_points.extend(points_loaded)
                    project_background_files.append(str(path_obj))
                    loaded += len(points_loaded)
            except Exception as exc:
                failed.append(f"{path_obj.name}: {exc}")
        draw_view()
        if failed:
            messagebox.showwarning(APP_TITLE, "Some background files could not be loaded:\n" + "\n".join(failed[:6]))
        status_var.set(f"Loaded {loaded} background sounding point(s) from {file_kind} file(s)")

    def clear_project_background_data() -> None:
        project_background_points.clear()
        project_background_files.clear()
        ktd_extent_shapes.clear()
        rebuild_background_cache(dxf_background_shapes, enc_background_shapes, kmz_background_shapes, ktd_extent_shapes)
        draw_view()
        status_var.set("Cleared project RAW/EDIT/SORT/KTD background data")

    def unload_all_background_layers():
        return _acquisition.new_window.clear_layers()

    def normalized_file_key(path_obj: Path) -> str:
        try:
            return str(path_obj.resolve()).lower()
        except Exception:
            return str(path_obj).lower()

    def background_file_kind(path_obj: Path) -> str:
        suffix = path_obj.suffix.lower()
        name_lower = path_obj.name.lower()
        if suffix in {".dxf", ".dwg"}:
            return "CAD / DXF"
        if suffix in {".kmz", ".kml"}:
            return "KMZ / KML"
        if suffix == ".ktd":
            return "KTD grid"
        if suffix == ".brd":
            return "Border"
        if suffix == ".lnw":
            return "Line file"
        if suffix == ".s57" or re.fullmatch(r"\.\d{3}", suffix or ""):
            return "ENC / S-57"
        if suffix in {".raw", ".1", ".2", ".3", ".nmea", ".log", ".txt", ".xyz", ".csv", ".edt", ".edit", ".sort", ".tgt"}:
            if "line" in name_lower and suffix == ".csv":
                return "Line CSV"
            return "Sounding / target"
        if suffix in {".tif", ".tiff", ".geotiff"}:
            return "GeoTIFF"
        if suffix in {".all", ".kmall", ".s7k", ".xtf", ".gsf"}:
            return "MBES raw"
        return ""

    def is_background_file_loaded(path_obj: Path, kind: str) -> bool:
        key = normalized_file_key(path_obj)
        if key in background_file_state:
            return True
        path_text = str(path_obj)
        if kind == "CAD / DXF":
            return bool(getattr(settings, "dxf_background_path", "")) and normalized_file_key(Path(settings.dxf_background_path)) == key
        if kind == "KMZ / KML":
            return bool(getattr(settings, "kmz_background_path", "")) and normalized_file_key(Path(settings.kmz_background_path)) == key
        if kind == "ENC / S-57":
            return bool(getattr(settings, "enc_background_path", "")) and normalized_file_key(Path(settings.enc_background_path)) == key
        if kind in {"KTD grid", "Sounding / target"}:
            return any(normalized_file_key(Path(name)) == key for name in project_background_files)
        return False

    def remove_background_points_for_file(path_obj: Path) -> None:
        source_name = path_obj.name.upper()
        project_background_points[:] = [
            point for point in project_background_points
            if source_name not in str(point.source).upper()
        ]
        project_background_files[:] = [
            name for name in project_background_files
            if normalized_file_key(Path(str(name))) != normalized_file_key(path_obj)
        ]

    def show_background_file_explorer():
        return _acquisition.new_window.show_background_manager()

    def load_files() -> None:
        read_settings()
        names = filedialog.askopenfilenames(
            title="Open survey data",
            filetypes=[
                ("Survey files", "*.raw *.RAW *.csv *.txt *.xyz *.nmea *.log *.1 *.2 *.3"),
                ("All files", "*.*"),
            ],
        )
        if not names:
            return
        loaded = 0
        for name in names:
            path = Path(name)
            try:
                if path.suffix.lower() in {".nmea", ".log"}:
                    for point in load_nmea(path):
                        ensure_projected_xy(point, settings.geodetic)
                        model.add(point)
                        loaded += 1
                else:
                    loaded += model.load_file(path)
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"Could not load {path.name}:\n{exc}")
        read_settings()
        refresh_table()
        status_var.set(f"Loaded {loaded} records")

    def clear() -> None:
        model.clear()
        refresh_table()
        status_var.set("Cleared")

    def apply() -> None:
        read_settings()
        refresh_table()

    def log_hypack_raw() -> None:
        read_settings()
        name = filedialog.asksaveasfilename(
            title="Log HYPACK RAW",
            defaultextension=".raw",
            initialdir=str(Path(settings.raw_folder or str(default_raw_folder()))),
            filetypes=[("HYPACK RAW", "*.raw"), ("All files", "*.*")],
        )
        if not name:
            return
        path = Path(name).with_suffix(".raw")
        try:
            count = model.export_hypack_raw(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not write HYPACK RAW log:\n{exc}")
            return
        status_var.set(f"Logged {count} records to HYPACK RAW file {path.name}")

    def show_settings_dialog() -> None:
        if _acquisition.writer is not None:
            messagebox.showwarning(APP_TITLE, "Stop recording before changing hardware settings.")
            return
        existing = getattr(show_settings_dialog, '_win', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return existing
        if any(thread.is_alive() for thread in io_monitor_threads):
            try: _acquisition.stop()
            except Exception as exc:
                messagebox.showerror(APP_TITLE, str(exc))
                return
            status_var.set("Monitoring stopped for hardware configuration. Reopen I/O Monitor after applying settings.")
        win = tk.Toplevel(root)
        win.title("Hardware & Survey Settings")
        show_settings_dialog._win = win
        win.geometry("620x520")
        try:
            win.q.showMaximized()
        except Exception:
            win.attributes("-zoomed", True)
        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        vars_by_name: dict[str, tk.Variable] = {}

        def add_serial_tab(parent: ttk.Frame, prefix: str, serial: SerialSettings) -> None:
            enabled_var = tk.BooleanVar(value=bool(getattr(serial, "enabled", True)))
            vars_by_name[f"{prefix}.enabled"] = enabled_var
            ttk.Checkbutton(parent, text="Enable device", variable=enabled_var).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=5)
            fields = [
                ("Port No", "port", str(serial.port)),
                ("Baud Rate", "baud_rate", str(serial.baud_rate)),
                ("Data Width", "data_width", str(serial.data_width)),
                ("Stop Bits", "stop_bits", str(serial.stop_bits)),
                ("Parity", "parity", str(serial.parity)),
                ("Requested device rate (Hz; metadata only)", "recording_rate_hz", str(getattr(serial, "recording_rate_hz", 1.0))),
            ]
            if isinstance(serial, SensorSettings):
                fields.extend(
                    [
                        ("cSBESMessage", "sbes_message", serial.sbes_message),
                        ("eSBESFormat", "sbes_format", serial.sbes_format),
                    ]
                )
            for row, (label, key, value) in enumerate(fields, start=1):
                ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=5)
                var = tk.StringVar(value=value)
                vars_by_name[f"{prefix}.{key}"] = var
                choices = {'port': available_serial_ports(), 'baud_rate': ['4800','9600','19200','38400','57600','115200','230400','460800','921600'], 'data_width': ['5','6','7','8'], 'stop_bits': ['1','1.5','2'], 'parity': ['None','Even','Odd','Mark','Space']}
                if key in choices:
                    field = ttk.Combobox(parent, textvariable=var, values=choices[key], width=34)
                else:
                    field = ttk.Entry(parent, textvariable=var, width=34)
                field.grid(row=row, column=1, sticky="ew", padx=8, pady=5)
                if key == 'port':
                    ttk.Button(parent, text="Refresh ports", command=lambda f=field: f.configure(values=available_serial_ports())).grid(row=row,column=2,padx=4)

            default_options = SBES_SENTENCE_OPTIONS if isinstance(serial, SensorSettings) else GNSS_SENTENCE_OPTIONS
            options = list(dict.fromkeys(list(serial.sentence_options or default_options) + list(default_options)))
            selected = set(serial.selected_sentences)
            serial.sentence_options = options
            sentence_frame = ttk.LabelFrame(parent, text="Sentence selection - scan port to add all available NMEA / NON NMEA ASCII sentences", padding=8)
            sentence_frame.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="nsew", padx=8, pady=(10, 5))
            sentence_status_var = tk.StringVar(value="Select sentences for live decoding. RAW retains all incoming messages. SCAN reads live ASCII from the selected port.")
            ttk.Label(sentence_frame, textvariable=sentence_status_var, wraplength=760).grid(row=0, column=0, columnspan=5, sticky="ew", padx=6, pady=(0, 6))
            sentence_checks_frame = ttk.Frame(sentence_frame)
            sentence_checks_frame.grid(row=1, column=0, columnspan=5, sticky="ew")

            def rebuild_sentence_checks(new_options: list[str], new_selected: Optional[set[str]] = None) -> None:
                clean_options = [item for item in dict.fromkeys(str(option).strip().upper() for option in new_options if str(option).strip())]
                if not clean_options:
                    clean_options = list(default_options)
                selected_now = set(new_selected if new_selected is not None else selected)
                vars_by_name[f"{prefix}.sentence_options"] = tk.StringVar(value=",".join(clean_options))
                for child in sentence_checks_frame.winfo_children():
                    child.destroy()
                for key in list(vars_by_name):
                    if key.startswith(f"{prefix}.sentence."):
                        vars_by_name.pop(key, None)
                for index, sentence in enumerate(clean_options):
                    var = tk.BooleanVar(value=sentence in selected_now or normalized_sentence_type(sentence) in selected_now)
                    vars_by_name[f"{prefix}.sentence.{sentence}"] = var
                    ttk.Checkbutton(sentence_checks_frame, text=sentence, variable=var).grid(row=index // 5, column=index % 5, sticky="w", padx=8, pady=3)
                for col in range(5):
                    sentence_checks_frame.columnconfigure(col, weight=1)
                serial.sentence_options = clean_options

            def settings_serial_kwargs_from_fields() -> dict[str, object]:
                return serial_parameters(*[vars_by_name[f"{prefix}.{key}"].get() for key in ('port','baud_rate','data_width','parity','stop_bits')])

            def scan_ascii_sentences() -> None:
                if _acquisition.writer is not None or any(t.is_alive() for t in io_monitor_threads):
                    sentence_status_var.set("Stop logging / monitoring before scanning this port.")
                    return
                def finished(found, samples, error):
                    if error:
                        sentence_status_var.set("Scan failed: " + error)
                        return
                    options[:] = list(dict.fromkeys(found + options))
                    current = {s for s in options if vars_by_name.get(f"{prefix}.sentence.{s}", tk.BooleanVar(value=False)).get()}
                    rebuild_sentence_checks(options, current | set(found))
                    sentence_status_var.set(f"Found {len(found)} ASCII sentence type(s): {', '.join(found[:18])}" if found else "Port opened; no complete ASCII sentences received before timeout.")
                try:
                    scan_serial_async(win, settings_serial_kwargs_from_fields(), finished)
                    sentence_status_var.set("Scanning for 5 seconds… You can continue using the interface.")
                except ValueError as exc: sentence_status_var.set(str(exc))

            rebuild_sentence_checks(options, selected)
            ttk.Button(sentence_frame, text="SCAN ASCII SENTENCES", command=scan_ascii_sentences).grid(row=2, column=0, sticky="ew", padx=6, pady=(8, 0))
            ttk.Button(sentence_frame, text="SELECT ALL", command=lambda: rebuild_sentence_checks(options, set(options))).grid(row=2, column=1, sticky="ew", padx=6, pady=(8, 0))
            ttk.Button(sentence_frame, text="CLEAR", command=lambda: rebuild_sentence_checks(options, set())).grid(row=2, column=2, sticky="ew", padx=6, pady=(8, 0))
            for col in range(5):
                sentence_frame.columnconfigure(col, weight=1)
            parent.columnconfigure(1, weight=1)

        project_tab = ttk.Frame(notebook, padding=8)
        gps_tab = ttk.Frame(notebook, padding=8)
        sensor_tab = ttk.Frame(notebook, padding=8)
        corrections_tab = ttk.Frame(notebook, padding=8)
        geo_tab = ttk.Frame(notebook, padding=8)
        devices_tab = ttk.Frame(notebook, padding=8)
        notebook.add(project_tab, text="Project")
        notebook.add(gps_tab, text="GNSS settings")
        notebook.add(sensor_tab, text="Sensor (SBES)")
        notebook.add(corrections_tab, text="Corrections")
        notebook.add(geo_tab, text="Geodetic")
        notebook.add(devices_tab, text="Devices")

        available_projects = discover_project_options()
        current_project = str(project_root_from_raw_folder(settings.raw_folder or str(default_raw_folder())))
        if current_project not in available_projects:
            available_projects.insert(0, current_project)
        available_grids = discover_grid_options(current_project)

        ttk.Label(project_tab, text="Project folder").grid(row=0, column=0, sticky="w", padx=8, pady=5)
        project_folder_var = tk.StringVar(value=current_project)
        vars_by_name["project.folder"] = project_folder_var
        project_combo = ttk.Combobox(project_tab, textvariable=project_folder_var, values=available_projects, width=48)
        project_combo.grid(row=0, column=1, sticky="ew", padx=8, pady=5)

        ttk.Label(project_tab, text="Grid / background option").grid(row=1, column=0, sticky="w", padx=8, pady=5)
        grid_path_var = tk.StringVar(value=getattr(settings.geodetic, "selected_grid_path", ""))
        vars_by_name["geodetic.selected_grid_path"] = grid_path_var
        grid_combo = ttk.Combobox(project_tab, textvariable=grid_path_var, values=available_grids, width=48)
        grid_combo.grid(row=1, column=1, sticky="ew", padx=8, pady=5)

        def refresh_project_grids(_event: Any = None) -> None:
            grids = discover_grid_options(project_folder_var.get())
            grid_combo.configure(values=grids)
            if grids and not grid_path_var.get():
                grid_path_var.set(grids[0])

        def browse_project_folder() -> None:
            folder = filedialog.askdirectory(title="Select main project folder", initialdir=project_folder_var.get() or str(APP_DIR))
            if folder:
                project_folder_var.set(folder)
                if folder not in available_projects:
                    available_projects.insert(0, folder)
                    project_combo.configure(values=available_projects)
                refresh_project_grids()

        def browse_grid_file() -> None:
            name = filedialog.askopenfilename(
                title="Select grid/background file",
                initialdir=project_folder_var.get() or str(APP_DIR),
                filetypes=[("Grid/background files", "*.grd *.grid *.gtx *.gsb *.ktd *.KTD *.csv *.xyz *.brd *.lnw *.dxf *.dwg *.DWG *.000 *.enc *.s57 *.kmz *.kml"), ("All files", "*.*")],
            )
            if name:
                grid_path_var.set(name)

        project_combo.bind("<<ComboboxSelected>>", refresh_project_grids)
        ttk.Button(project_tab, text="Browse Project", command=browse_project_folder).grid(row=0, column=2, sticky="ew", padx=8, pady=5)
        ttk.Button(project_tab, text="Browse Grid", command=browse_grid_file).grid(row=1, column=2, sticky="ew", padx=8, pady=5)

        def project_subfolder(name: str) -> Path:
            base, raw_path, edit_path, sort_path = ensure_project_subfolders_from_raw(project_folder_var.get() or str(DEFAULT_PROJECT_FOLDER))
            mapping = {"RAW": raw_path, "EDIT": edit_path, "SORT": sort_path}
            return mapping.get(name.upper(), base)

        background_frame = ttk.LabelFrame(project_tab, text="Load recorded / edited / sorted data in background", padding=6)
        background_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 4))
        ttk.Button(
            background_frame,
            text="Load RAW Background",
            command=lambda: add_project_background_files(
                "RAW",
                project_subfolder("RAW"),
                [("HYPACK RAW", "*.raw *.RAW"), ("NMEA/log", "*.nmea *.log")],
            ),
        ).grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(
            background_frame,
            text="Load EDIT Background",
            command=lambda: add_project_background_files(
                "EDIT",
                project_subfolder("EDIT"),
                [("HYPACK edited", "*.log *.1 *.2 *.3 *.4 *.5"), ("Edited/CSV", "*.csv *.txt")],
            ),
        ).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(
            background_frame,
            text="Load SORT Background",
            command=lambda: add_project_background_files(
                "SORT",
                project_subfolder("SORT"),
                [("Sorted XYZ", "*.xyz *.XYZ"), ("Sorted text/CSV", "*.txt *.csv")],
            ),
        ).grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(
            background_frame,
            text="Load KTD Background",
            command=lambda: add_project_background_files(
                "KTD",
                project_subfolder("RAW"),
                [("KTD separation grid", "*.ktd *.KTD")],
            ),
        ).grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        ttk.Button(background_frame, text="Clear Background Data", command=clear_project_background_data).grid(row=0, column=4, sticky="ew", padx=3, pady=3)
        for col in range(5):
            background_frame.columnconfigure(col, weight=1)

        ttk.Label(project_tab, text="On save/open, RAW, EDIT, and SORT subfolders are created automatically. RAW logs are saved under RAW.", foreground="#244d9a", wraplength=620).grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 8))
        project_tab.columnconfigure(1, weight=1)

        add_serial_tab(gps_tab, "gps", settings.gps)
        add_serial_tab(sensor_tab, "sensor", settings.sensor)

        shared_input_frame = ttk.LabelFrame(project_tab, text="Shared GNSS + echosounder input port", padding=8)
        shared_input_frame.grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(10, 4))
        share_input_var = tk.BooleanVar(value=bool(getattr(settings, "share_gnss_sbes_input", False)))
        vars_by_name["share_gnss_sbes_input"] = share_input_var
        ttk.Checkbutton(
            shared_input_frame,
            text="GNSS and SBES data arrive on the same serial port",
            variable=share_input_var,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=4, pady=3)
        ttk.Label(
            shared_input_frame,
            text="Use this when one cable/COM stream contains both navigation sentences such as GGA/RMC and echosounder sentences such as DPT/DBT. Sentence selections remain separate, but the live reader opens the port only once.",
            wraplength=760,
            foreground="#244d9a",
        ).grid(row=1, column=0, columnspan=3, sticky="ew", padx=4, pady=3)

        def copy_gnss_comm_to_sbes() -> None:
            for key in ["port", "baud_rate", "data_width", "stop_bits", "parity", "recording_rate_hz"]:
                vars_by_name[f"sensor.{key}"].set(vars_by_name[f"gps.{key}"].get())
            share_input_var.set(True)
            status_var.set("Shared input enabled: GNSS communication settings copied to SBES.")

        def copy_sbes_comm_to_gnss() -> None:
            for key in ["port", "baud_rate", "data_width", "stop_bits", "parity", "recording_rate_hz"]:
                vars_by_name[f"gps.{key}"].set(vars_by_name[f"sensor.{key}"].get())
            share_input_var.set(True)
            status_var.set("Shared input enabled: SBES communication settings copied to GNSS.")

        ttk.Button(shared_input_frame, text="Use GNSS Port for SBES", command=copy_gnss_comm_to_sbes).grid(row=2, column=0, sticky="ew", padx=4, pady=(6, 3))
        ttk.Button(shared_input_frame, text="Use SBES Port for GNSS", command=copy_sbes_comm_to_gnss).grid(row=2, column=1, sticky="ew", padx=4, pady=(6, 3))
        ttk.Button(shared_input_frame, text="Open I/O Monitor", command=lambda: (apply_dialog_settings(), show_io_monitor())).grid(row=2, column=2, sticky="ew", padx=4, pady=(6, 3))
        for col in range(3):
            shared_input_frame.columnconfigure(col, weight=1)

        ttk.Label(corrections_tab, text="Tide / Draft / Sound Velocity Corrections", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 10)
        )
        correction_fields = [
            ("Tide correction (m)", tide_var),
            ("Draft correction (m)", draft_var),
            ("Sound velocity (m/s)", sv_var),
        ]
        for row, (label, var) in enumerate(correction_fields, start=1):
            ttk.Label(corrections_tab, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=5)
            ttk.Entry(corrections_tab, textvariable=var, width=18).grid(row=row, column=1, sticky="ew", padx=8, pady=5)
        ttk.Label(
            corrections_tab,
            text="These corrections are applied to loaded and logged SBES depth values when Settings are applied.",
            foreground="#244d9a",
            wraplength=520,
        ).grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=(10, 0))
        corrections_tab.columnconfigure(1, weight=1)

        ttk.Label(devices_tab, text="Selected devices used by I/O Monitor").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        settings_device_list = tk.Listbox(devices_tab, height=8, exportselection=False)
        settings_device_list.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(0, 8))
        settings_device_checks_frame = ttk.LabelFrame(devices_tab, text="Enable / disable devices", padding=6)
        settings_device_checks_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        settings_device_status = tk.StringVar(value="GNSS and SBES are enabled by default.")
        ttk.Label(devices_tab, textvariable=settings_device_status).grid(row=3, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        devices_tab.rowconfigure(1, weight=1)
        devices_tab.columnconfigure(0, weight=1)

        def settings_device_label(device: SerialSettings) -> str:
            state = "ON" if getattr(device, "enabled", True) else "OFF"
            rate = getattr(device, "recording_rate_hz", 1.0)
            return f"{state} | {device.title}: {device.port} {device.baud_rate} {device.data_width}-{device.parity}-{device.stop_bits} | {rate:g} Hz"

        def all_settings_devices() -> list[SerialSettings]:
            devices: list[SerialSettings] = [settings.gps, settings.sensor]
            devices.extend(settings.extra_devices)
            return devices

        def refresh_settings_devices() -> None:
            settings_device_list.delete(0, "end")
            for child in settings_device_checks_frame.winfo_children():
                child.destroy()
            for index, device in enumerate(all_settings_devices()):
                settings_device_list.insert("end", settings_device_label(device))
                var = vars_by_name.get(f"device_list.enabled.{index}")
                if not isinstance(var, tk.BooleanVar):
                    var = tk.BooleanVar(value=bool(getattr(device, "enabled", True)))
                    vars_by_name[f"device_list.enabled.{index}"] = var
                else:
                    var.set(bool(getattr(device, "enabled", True)))

                def set_device_enabled(item: SerialSettings = device, enabled_var: tk.BooleanVar = var) -> None:
                    item.enabled = bool(enabled_var.get())
                    if item is settings.gps and isinstance(vars_by_name.get("gps.enabled"), tk.BooleanVar):
                        vars_by_name["gps.enabled"].set(item.enabled)
                    if item is settings.sensor and isinstance(vars_by_name.get("sensor.enabled"), tk.BooleanVar):
                        vars_by_name["sensor.enabled"].set(item.enabled)
                    save_default_settings()
                    refresh_settings_devices()

                ttk.Checkbutton(
                    settings_device_checks_frame,
                    text=settings_device_label(device),
                    variable=var,
                    command=set_device_enabled,
                ).grid(row=index // 2, column=index % 2, sticky="w", padx=8, pady=3)
            for col in range(2):
                settings_device_checks_frame.columnconfigure(col, weight=1)

        def selected_settings_device() -> Optional[SerialSettings]:
            devices = all_settings_devices()
            selection = settings_device_list.curselection()
            if not selection:
                return devices[0] if devices else None
            index = int(selection[0])
            return devices[index] if 0 <= index < len(devices) else None

        def settings_serial_kwargs(device: SerialSettings) -> dict[str, object]:
            return serial_parameters(device.port,device.baud_rate,device.data_width,device.parity,device.stop_bits)

        def test_settings_device() -> None:
            if apply_dialog_settings() is False: return
            device = selected_settings_device()
            if device is None:
                settings_device_status.set("No device selected.")
                return
            def finished(found,samples,error):
                settings_device_status.set(f"{device.port} test failed: {error}" if error else next(iter(samples.values()),f"{device.port} opened successfully; no complete ASCII string before timeout."))
            try:
                scan_serial_async(win, settings_serial_kwargs(device), finished, duration=1)
                settings_device_status.set("Testing device…")
            except ValueError as exc: settings_device_status.set(str(exc))

        def add_settings_device() -> None:
            title = simpledialog.askstring(APP_TITLE, "Device name:", initialvalue=f"Device {len(settings.extra_devices) + 1}", parent=win)
            if not title:
                return
            port = simpledialog.askstring(APP_TITLE, "COM port:", initialvalue="COM3", parent=win) or "COM3"
            baud = simpledialog.askinteger(APP_TITLE, "Baud rate:", initialvalue=4800, minvalue=300, maxvalue=921600, parent=win) or 4800
            rate = simpledialog.askfloat(APP_TITLE, "Requested device rate (Hz; metadata only):", initialvalue=1.0, minvalue=0.01, parent=win) or 1.0
            settings.extra_devices.append(
                SerialSettings(
                    title=title,
                    port=port,
                    baud_rate=baud,
                    data_width=8,
                    stop_bits=1,
                    parity="N",
                    enabled=True,
                    recording_rate_hz=rate,
                    sentence_options=list(GENERIC_DEVICE_SENTENCE_OPTIONS),
                    selected_sentences=list(GENERIC_DEVICE_SENTENCE_OPTIONS),
                )
            )
            save_default_settings()
            refresh_settings_devices()
            settings_device_status.set(f"Added {title}.")

        def remove_settings_device() -> None:
            device = selected_settings_device()
            if device is None:
                return
            if device in (settings.gps, settings.sensor):
                device.enabled = False
                settings_device_status.set(f"Disabled {device.title}.")
            else:
                settings.extra_devices = [item for item in settings.extra_devices if item is not device]
                settings_device_status.set(f"Removed {device.title}.")
            save_default_settings()
            refresh_settings_devices()

        ttk.Button(devices_tab, text="TEST DEVICE", command=test_settings_device).grid(row=4, column=0, sticky="ew", padx=3)
        ttk.Button(devices_tab, text="ADD device", command=add_settings_device).grid(row=4, column=1, sticky="ew", padx=3)
        ttk.Button(devices_tab, text="REMOVE device", command=remove_settings_device).grid(row=4, column=2, sticky="ew", padx=3)
        for _col in range(3):
            devices_tab.columnconfigure(_col, weight=1)
        refresh_settings_devices()

        projection_presets: dict[str, dict[str, object]] = {
            "WGS84 / UTM Zone 42": {
                "use_projection": True,
                "use_tm_projection": True,
                "use_lcc_projection": False,
                "datum_name": "WGS84",
                "ellipsoid": "WGS84",
                "utm_zone": "42",
                "lcc_zone": "",
                "central_meridian": 69.0,
                "latitude_origin": 0.0,
                "scale_factor": 0.9996,
                "false_easting": 500000.0,
                "false_northing": 0.0,
                "grid_name": "UTM North",
                "zone_label": "Zone 42(66E-72E)",
                "projection_method": "Transverse Mercator",
                "reference_latitude": 0.0,
            },
            "Lambert Conical Zone IA": {
                "use_projection": True,
                "use_tm_projection": False,
                "use_lcc_projection": True,
                "datum_name": "WGS84",
                "ellipsoid": "WGS84",
                "utm_zone": "",
                "lcc_zone": "IA",
                "central_meridian": 68.0,
                "latitude_origin": 24.0,
                "scale_factor": 1.0,
                "false_easting": 500000.0,
                "false_northing": 0.0,
                "grid_name": "Lambert Conical",
                "zone_label": "Zone IA",
                "projection_method": "Lambert Conformal Conic",
                "reference_latitude": 24.0,
            },
            "Lambert Conical Zone IIA": {
                "use_projection": True,
                "use_tm_projection": False,
                "use_lcc_projection": True,
                "datum_name": "WGS84",
                "ellipsoid": "WGS84",
                "utm_zone": "",
                "lcc_zone": "IIA",
                "central_meridian": 70.0,
                "latitude_origin": 28.0,
                "scale_factor": 1.0,
                "false_easting": 500000.0,
                "false_northing": 0.0,
                "grid_name": "Lambert Conical",
                "zone_label": "Zone IIA",
                "projection_method": "Lambert Conformal Conic",
                "reference_latitude": 28.0,
            },
            "Custom": {},
        }

        for hemisphere in ("N", "S"):
            for zone in range(1, 61):
                projection_presets[f"WGS84 / UTM Zone {zone}{hemisphere}"] = utm_projection_preset(zone, hemisphere)
        # Keep the original preset name compatible with saved projects.
        projection_presets["WGS84 / UTM Zone 42"] = utm_projection_preset(42, "N")

        ttk.Label(geo_tab, text="Projection preset").grid(row=0, column=0, sticky="w", padx=8, pady=5)
        projection_var = tk.StringVar(value=getattr(settings.geodetic, "projection_name", "WGS84 / UTM Zone 42"))
        projection_combo = ttk.Combobox(geo_tab, textvariable=projection_var, values=list(projection_presets), state="readonly", width=32)
        projection_combo.grid(row=0, column=1, sticky="ew", padx=8, pady=5)
        vars_by_name["geodetic.projection_name"] = projection_var

        detail_text = tk.StringVar()
        ttk.Label(geo_tab, textvariable=detail_text, foreground="#244d9a").grid(row=1, column=0, columnspan=5, sticky="ew", padx=8, pady=(0, 8))

        geo_fields = [
            ("Use Projection", "use_projection", settings.geodetic.use_projection),
            ("Use TM / UTM Projection", "use_tm_projection", settings.geodetic.use_tm_projection),
            ("Use Lambert Conical", "use_lcc_projection", settings.geodetic.use_lcc_projection),
            ("Predefined Grid", "grid_name", settings.geodetic.grid_name),
            ("Zone", "zone_label", settings.geodetic.zone_label),
            ("Distance Unit", "distance_unit", settings.geodetic.distance_unit),
            ("Depth Unit", "depth_unit", settings.geodetic.depth_unit),
            ("Elevation Mode (Z positive up)", "elevation_mode_positive_up", settings.geodetic.elevation_mode_positive_up),
            ("Name of Datum", "datum_name", settings.geodetic.datum_name),
            ("Ellipsoid", "ellipsoid", settings.geodetic.ellipsoid),
            ("Semi-Major Axis", "semi_major_axis", settings.geodetic.semi_major_axis),
            ("Flattening", "flattening", settings.geodetic.flattening),
            ("UTM Zone", "utm_zone", settings.geodetic.utm_zone),
            ("Fixed UTM Reference Zone", "fixed_utm_reference_zone", settings.geodetic.fixed_utm_reference_zone),
            ("Lambert Conical Zone", "lcc_zone", settings.geodetic.lcc_zone),
            ("Projection", "projection_method", settings.geodetic.projection_method),
            ("Central Meridian", "central_meridian", settings.geodetic.central_meridian),
            ("Reference Latitude", "reference_latitude", settings.geodetic.reference_latitude),
            ("Latitude Origin", "latitude_origin", settings.geodetic.latitude_origin),
            ("Scale Factor", "scale_factor", settings.geodetic.scale_factor),
            ("False Easting", "false_easting", settings.geodetic.false_easting),
            ("False Northing", "false_northing", settings.geodetic.false_northing),
            ("Delta X", "delta_x", settings.geodetic.delta_x),
            ("Delta Y", "delta_y", settings.geodetic.delta_y),
            ("Delta Z", "delta_z", settings.geodetic.delta_z),
            ("Delta rX", "delta_rx", settings.geodetic.delta_rx),
            ("Delta rY", "delta_ry", settings.geodetic.delta_ry),
            ("Delta rZ", "delta_rz", settings.geodetic.delta_rz),
            ("Delta Scale", "delta_scale", settings.geodetic.delta_scale),
            ("Datum Shift File", "datum_shift_file", settings.geodetic.datum_shift_file),
            ("Local Grid Adjustment", "local_grid_adjustment", settings.geodetic.local_grid_adjustment),
            ("Local Grid File", "local_grid_file", settings.geodetic.local_grid_file),
            ("Ellipsoidal Height Reference", "ellipsoidal_height_reference", settings.geodetic.ellipsoidal_height_reference),
            ("Chart Datum Level", "chart_datum_level", settings.geodetic.chart_datum_level),
            ("Geoid Separation N", "geoid_separation_n", settings.geodetic.geoid_separation_n),
            ("Geoid Model File", "geoid_model_file", settings.geodetic.geoid_model_file),
            ("KTD K Value", "ktd_k_value", settings.geodetic.ktd_k_value),
            ("KTD File Path", "ktd_file_path", settings.geodetic.ktd_file_path),
            ("VDatum Zone", "vdatum_zone", settings.geodetic.vdatum_zone),
            ("VDatum Surface File", "vdatum_surface_file", settings.geodetic.vdatum_surface_file),
            ("ITRF / WGS84 Epoch", "itrf_wgs84_epoch", settings.geodetic.itrf_wgs84_epoch),
            ("VDatum K Value", "vdatum_k_value", settings.geodetic.vdatum_k_value),
            ("User K Value", "user_k_value", settings.geodetic.user_k_value),
            ("User K-N Value", "user_k_minus_n", settings.geodetic.user_k_minus_n),
        ]
        for index, (label, key, value) in enumerate(geo_fields):
            column_group = index // 20
            row = 2 + (index % 20)
            label_column = column_group * 2
            entry_column = label_column + 1
            ttk.Label(geo_tab, text=label).grid(row=row, column=label_column, sticky="w", padx=8, pady=4)
            var = tk.StringVar(value=str(value))
            vars_by_name[f"geodetic.{key}"] = var
            if key == "utm_zone":
                utm_zone_combo = ttk.Combobox(geo_tab, textvariable=var,
                    values=[str(zone) for zone in range(1, 61)], state="readonly", width=26)
                utm_zone_combo.grid(row=row, column=entry_column, sticky="ew", padx=8, pady=4)
            else:
                ttk.Entry(geo_tab, textvariable=var, width=26).grid(row=row, column=entry_column, sticky="ew", padx=8, pady=4)

        rtk_frame = ttk.LabelFrame(geo_tab, text="RTK Tide Method", padding=8)
        rtk_frame.grid(row=2, column=4, rowspan=10, sticky="nsew", padx=12, pady=4)
        rtk_method_var = tk.StringVar(value=str(getattr(settings.geodetic, "rtk_tide_method", RTK_TIDE_METHODS[0])))
        vars_by_name["geodetic.rtk_tide_method"] = rtk_method_var
        for index, method in enumerate(RTK_TIDE_METHODS):
            ttk.Radiobutton(rtk_frame, text=method, value=method, variable=rtk_method_var).grid(row=index, column=0, sticky="w", pady=2)
        ttk.Label(
            rtk_frame,
            text="Realtime and post-processing tide can use GNSS ellipsoidal height with the saved chart datum, geoid N, K, or K-N values.",
            wraplength=360,
        ).grid(row=len(RTK_TIDE_METHODS), column=0, sticky="ew", pady=(8, 0))
        geopar_tools = ttk.LabelFrame(geo_tab, text="Geodetic / KTD file tools", padding=8)
        geopar_tools.grid(row=24, column=0, columnspan=5, sticky="ew", padx=8, pady=(10, 4))

        def browse_geodetic_file(var_key: str, title: str, filetypes: list[tuple[str, str]]) -> None:
            var = vars_by_name.get(var_key)
            if var is None:
                return
            start_text = str(var.get() or settings.raw_folder or APP_DIR)
            start_path = Path(start_text)
            initialdir = str(start_path.parent if start_path.suffix else start_path)
            name = filedialog.askopenfilename(title=title, initialdir=initialdir, filetypes=filetypes + [("All files", "*.*")], parent=win)
            if name:
                var.set(name)
                refresh_geodetic_detail()

        def save_custom_grid_definition() -> None:
            name = filedialog.asksaveasfilename(
                title="Save Custom Grid",
                initialdir=str(project_root_from_raw_folder(settings.raw_folder)),
                defaultextension=".cgeo",
                filetypes=[("Custom Grid", "*.cgeo"), ("JSON", "*.json"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            payload = {key.replace("geodetic.", ""): var.get() for key, var in vars_by_name.items() if key.startswith("geodetic.")}
            Path(name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            status_var.set(f"Saved custom geodetic grid: {Path(name).name}")

        def export_vdatum_or_ktd_xyz() -> None:
            source_text = vars_by_name.get("geodetic.ktd_file_path", tk.StringVar(value="")).get().strip()
            nodes: list[tuple[float, float, float]] = []
            if source_text and Path(source_text).exists():
                nodes = load_ktd_nodes(Path(source_text))
            if not nodes:
                messagebox.showwarning(APP_TITLE, "Select a KTD file containing real nodes before exporting XYZ. No synthetic surface will be created.", parent=win)
                return
            name = filedialog.asksaveasfilename(
                title="Export VDatum / KTD Surface to XYZ",
                initialdir=str(project_root_from_raw_folder(settings.raw_folder)),
                defaultextension=".xyz",
                filetypes=[("XYZ", "*.xyz"), ("Text", "*.txt"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            with Path(name).open("w", encoding="utf-8", newline="\n") as handle:
                for x, y, value in nodes:
                    handle.write(f"{float(x):.3f} {float(y):.3f} {float(value):.4f}\n")
            status_var.set(f"Exported {len(nodes)} VDatum/KTD node(s) to {Path(name).name}")

        def load_ktd_for_realtime() -> None:
            source_text = vars_by_name.get("geodetic.ktd_file_path", tk.StringVar(value="")).get().strip()
            if not source_text:
                messagebox.showwarning(APP_TITLE, "Select a KTD file first.", parent=win)
                return
            path = Path(source_text)
            if not path.exists():
                messagebox.showwarning(APP_TITLE, f"KTD file does not exist:\n{path}", parent=win)
                return
            meta = load_ktd_grid_meta(path) or {}
            nodes = load_ktd_nodes(path)
            vars_by_name["geodetic.selected_grid_path"].set(str(path))
            load_ktd_background_path(path, replace_existing=True)
            if not nodes:
                messagebox.showwarning(
                    APP_TITLE,
                    "The KTD extent was loaded, but no real KTD cells were found.\n"
                    "Cells with 999.99 are treated as nodata. Select a KTD file containing real K values for realtime tide.",
                    parent=win,
                )
                return
            method_var = vars_by_name.get("geodetic.rtk_tide_method")
            if method_var is not None and str(method_var.get() or "") == "Not using RTK tide":
                method_var.set("N from geoid model, K from KTD file")
            refresh_geodetic_detail()
            x_count = int(meta.get("x_count", 0) or 0)
            y_count = int(meta.get("y_count", 0) or 0)
            status_var.set(
                f"KTD loaded for realtime tide: {path.name} | {x_count} x {y_count} grid | "
                f"{len(nodes)} valid cell(s) | X {format_number(meta.get('min_x'))}-{format_number(meta.get('max_x'))} "
                f"Y {format_number(meta.get('min_y'))}-{format_number(meta.get('max_y'))}"
            )

        file_buttons = [
            ("Browse KTD", "geodetic.ktd_file_path", "Select KTD File", [("KTD Files", "*.ktd *.KTD")]),
            ("Browse Geoid", "geodetic.geoid_model_file", "Select Geoid Model File", [("Geoid Files", "*.geo *.GEO")]),
            ("Browse Datum Shift", "geodetic.datum_shift_file", "Select Datum Shift File", [("Combined Shift Files", "*.lls *.LLS")]),
            ("Browse Local Grid", "geodetic.local_grid_file", "Select Local Grid File", [("Custom Grid", "*.cgeo *.CGEO"), ("Grid", "*.grd *.grid")]),
            ("Browse VDatum", "geodetic.vdatum_surface_file", "Select VDatum Surface", [("VDatum / XYZ", "*.xyz *.txt *.csv *.vdatum")]),
        ]
        for index, (label, key, title, filetypes) in enumerate(file_buttons):
            ttk.Button(geopar_tools, text=label, command=lambda k=key, t=title, ft=filetypes: browse_geodetic_file(k, t, ft)).grid(row=0, column=index, sticky="ew", padx=3, pady=3)
            geopar_tools.columnconfigure(index, weight=1)
        ttk.Button(geopar_tools, text="Save Custom Grid", command=save_custom_grid_definition).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(geopar_tools, text="Export KTD nodes to XYZ", command=export_vdatum_or_ktd_xyz).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(geopar_tools, text="Load KTD Realtime", command=load_ktd_for_realtime).grid(row=1, column=2, sticky="ew", padx=3, pady=3)
        ttk.Label(
            geopar_tools,
            text="Matches HYPACK Geodetic Parameters workflow: KTD file, geoid model, datum shift, local grid, VDatum zone/surface, chart datum level, and RTK tide method.",
            wraplength=720,
        ).grid(row=1, column=3, columnspan=2, sticky="ew", padx=3, pady=3)

        geo_tab.columnconfigure(1, weight=1)
        geo_tab.columnconfigure(3, weight=1)
        geo_tab.columnconfigure(4, weight=1)

        def refresh_geodetic_detail() -> None:
            detail_text.set(
                f"Selected: {projection_var.get()} | Datum {vars_by_name['geodetic.datum_name'].get()} | "
                f"Ellipsoid {vars_by_name['geodetic.ellipsoid'].get()} | "
                f"UTM Zone {vars_by_name['geodetic.utm_zone'].get() or '-'} "
                f"{'(fixed reference)' if str(vars_by_name.get('geodetic.fixed_utm_reference_zone', tk.StringVar(value='True')).get()).lower() not in {'0', 'false', 'no', 'off'} else '(auto by longitude)'} | "
                f"Vertical ref {vars_by_name.get('geodetic.ellipsoidal_height_reference', tk.StringVar(value='0.0')).get()} m | "
                f"RTK {vars_by_name.get('geodetic.rtk_tide_method', tk.StringVar(value='Not using RTK tide')).get()}"
            )

        def apply_projection_preset(_event: Any = None) -> None:
            preset = projection_presets.get(projection_var.get(), {})
            if not preset:
                refresh_geodetic_detail()
                return
            for key, value in preset.items():
                var = vars_by_name.get(f"geodetic.{key}")
                if var is not None:
                    var.set(str(value))
            refresh_geodetic_detail()

        def apply_utm_zone(_event: Any = None) -> None:
            zone = int(vars_by_name["geodetic.utm_zone"].get())
            hemisphere = "S" if float(vars_by_name["geodetic.false_northing"].get() or 0) == 10000000.0 else "N"
            projection_var.set(f"WGS84 / UTM Zone {zone}{hemisphere}")
            apply_projection_preset()

        utm_zone_combo.bind("<<ComboboxSelected>>", apply_utm_zone)
        projection_combo.bind("<<ComboboxSelected>>", apply_projection_preset)
        if projection_var.get() not in projection_presets:
            projection_var.set("WGS84 / UTM Zone 42")
        refresh_geodetic_detail()

        def apply_dialog_settings() -> None:
            if _acquisition.writer is not None:
                messagebox.showwarning(APP_TITLE, "Stop recording before applying settings.", parent=win)
                return False
            if getattr(win, '_serial_probe_busy', False):
                messagebox.showwarning(APP_TITLE, "Wait for the device scan to finish before applying settings.", parent=win)
                return False
            try: validate_hardware_fields(vars_by_name, settings)
            except ValueError as exc:
                messagebox.showerror(APP_TITLE, str(exc), parent=win)
                return False
            if any(thread.is_alive() for thread in io_monitor_threads):
                try: _acquisition.stop()
                except Exception as exc:
                    messagebox.showerror(APP_TITLE, str(exc), parent=win)
                    return False
            def assign_serial(prefix: str, serial: SerialSettings) -> None:
                serial.port = vars_by_name[f"{prefix}.port"].get().strip()
                serial.baud_rate = int(parse_float(vars_by_name[f"{prefix}.baud_rate"].get()) or serial.baud_rate)
                serial.data_width = int(parse_float(vars_by_name[f"{prefix}.data_width"].get()) or serial.data_width)
                serial.stop_bits = float(parse_float(vars_by_name[f"{prefix}.stop_bits"].get()) or serial.stop_bits)
                serial.parity = vars_by_name[f"{prefix}.parity"].get()
                serial.recording_rate_hz = max(0.01, parse_float(vars_by_name[f"{prefix}.recording_rate_hz"].get()) or 1.0)
                serial.enabled = bool(vars_by_name[f"{prefix}.enabled"].get())
                options = [item for item in vars_by_name[f"{prefix}.sentence_options"].get().split(",") if item]
                serial.sentence_options = options
                serial.selected_sentences = [
                    item for item in options if bool(vars_by_name.get(f"{prefix}.sentence.{item}", tk.BooleanVar(value=False)).get())
                ]
                if isinstance(serial, SensorSettings):
                    serial.sbes_message = vars_by_name[f"{prefix}.sbes_message"].get()
                    serial.sbes_format = vars_by_name[f"{prefix}.sbes_format"].get()

            assign_serial("gps", settings.gps)
            assign_serial("sensor", settings.sensor)
            settings.share_gnss_sbes_input = bool(vars_by_name.get("share_gnss_sbes_input", tk.BooleanVar(value=False)).get())
            if settings.share_gnss_sbes_input:
                settings.sensor.port = settings.gps.port
                settings.sensor.baud_rate = settings.gps.baud_rate
                settings.sensor.data_width = settings.gps.data_width
                settings.sensor.stop_bits = settings.gps.stop_bits
                settings.sensor.parity = settings.gps.parity
                settings.sensor.recording_rate_hz = settings.gps.recording_rate_hz
                for key in ["port", "baud_rate", "data_width", "stop_bits", "parity", "recording_rate_hz"]:
                    sensor_var = vars_by_name.get(f"sensor.{key}")
                    gps_var = vars_by_name.get(f"gps.{key}")
                    if sensor_var is not None and gps_var is not None:
                        sensor_var.set(gps_var.get())
            project_folder = vars_by_name["project.folder"].get().strip() or str(DEFAULT_PROJECT_FOLDER)
            _project_folder, raw_path, _edit_path, _sort_path = ensure_project_subfolders_from_raw(project_folder)
            settings.raw_folder = str(raw_path)
            for key in settings.geodetic.to_dict():
                var = vars_by_name.get(f"geodetic.{key}")
                if var is None:
                    continue
                value = var.get()
                current = getattr(settings.geodetic, key)
                if isinstance(current, bool):
                    if isinstance(value, bool):
                        parsed_bool = value
                    else:
                        parsed_bool = str(value).strip().lower() not in {"0", "false", "no", "off"}
                    setattr(settings.geodetic, key, parsed_bool)
                elif isinstance(current, float):
                    parsed_float = parse_float(str(value))
                    if parsed_float is not None:
                        setattr(settings.geodetic, key, parsed_float)
                else:
                    setattr(settings.geodetic, key, str(value))
            read_settings()
            selected_grid = Path(str(getattr(settings.geodetic, "selected_grid_path", "") or ""))
            selected_ktd = Path(str(getattr(settings.geodetic, "ktd_file_path", "") or ""))
            ktd_loaded = 0
            if selected_ktd.suffix.lower() == ".ktd" and selected_ktd.exists():
                settings.geodetic.selected_grid_path = str(selected_ktd)
                grid_var = vars_by_name.get("geodetic.selected_grid_path")
                if grid_var is not None:
                    grid_var.set(str(selected_ktd))
                ktd_loaded = load_ktd_background_path(selected_ktd, replace_existing=True)
                meta = load_ktd_grid_meta(selected_ktd) or {}
                nodes = load_ktd_nodes(selected_ktd)
                status_var.set(
                    f"Settings applied | KTD realtime loaded: {selected_ktd.name} | "
                    f"{int(meta.get('x_count', 0) or 0)} x {int(meta.get('y_count', 0) or 0)} grid | "
                    f"{len(nodes)} valid cell(s)"
                )
            elif selected_grid.suffix.lower() == ".ktd" and selected_grid.exists():
                settings.geodetic.ktd_file_path = str(selected_grid)
                ktd_loaded = load_ktd_background_path(selected_grid, replace_existing=True)
            if not ktd_loaded:
                status_var.set(
                    f"Settings applied | GNSS {settings.gps.port} @ {settings.gps.baud_rate} | "
                    f"SBES {settings.sensor.port} @ {settings.sensor.baud_rate} | "
                    f"{'shared input | ' if settings.share_gnss_sbes_input else ''}"
                    f"Tide {model.tide:.3f} Draft {model.draft:.3f} SV {model.sound_speed:.1f}"
                )
            save_default_settings()

        def save_settings() -> None:
            if apply_dialog_settings() is False: return
            save_default_settings()
            name = filedialog.asksaveasfilename(
                title="Save settings",
                defaultextension=".json",
                filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            )
            if name:
                settings.save(Path(name))
                status_var.set(f"Saved settings to {Path(name).name}")

        controls = ttk.Frame(win, padding=(10, 0, 10, 10))
        controls.pack(fill="x")
        ttk.Button(controls, text="Apply & Save", command=apply_dialog_settings).pack(side="left")
        ttk.Button(controls, text="Save As...", command=save_settings).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="Close", command=win.destroy).pack(side="right")
        win._settings_vars = vars_by_name
        win._settings_notebook = notebook
        win._settings_apply = apply_dialog_settings
        # Keep large geodetic/device pages usable on smaller displays.
        from PyQt6.QtWidgets import QScrollArea
        for index in range(notebook.q.count()):
            page = notebook.q.widget(index)
            title = notebook.q.tabText(index)
            notebook.q.removeTab(index)
            scroll = QScrollArea(notebook.q)
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            notebook.q.insertTab(index, scroll, title)
        return win

    def show_data_display() -> None:
        existing = data_display_state.get("win")
        try:
            if existing is not None and existing.winfo_exists():
                existing.lift()
                refresh_data_display()
                return
        except Exception:
            pass
        win = tk.Toplevel(root)
        win.title("DATA DISPLAY")
        win.geometry("520x680")
        win.minsize(360, 320)
        win.resizable(True, True)
        data_display_state["win"] = win

        topbar = ttk.Frame(win, padding=(10, 8, 10, 4))
        topbar.pack(fill="x")
        ttk.Label(topbar, text="DATA DISPLAY", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Label(topbar, text="X / Y / Z / Lat / Lon / status", foreground="#4b5563").pack(side="left", padx=(12, 0))
        config_button_var = tk.StringVar(value="Hide Configuration" if data_display_state.get("config_visible") else "Configure")
        ttk.Button(topbar, textvariable=config_button_var, command=lambda: toggle_config()).pack(side="right")

        config = ttk.LabelFrame(win, text="Display configuration", padding=8)
        if data_display_state.get("config_visible"):
            config.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(config, text="Font size").grid(row=0, column=0, sticky="w", padx=(0, 8))
        font_var = tk.IntVar(value=int(data_display_state.get("font_size", 18)))
        data_display_state["font_var"] = font_var
        ttk.Spinbox(config, from_=10, to=48, textvariable=font_var, width=6, command=lambda: set_data_display_font()).grid(row=0, column=1, sticky="w")

        fields_frame = ttk.Frame(config)
        fields_frame.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))

        def set_data_display_font(*_args: Any) -> None:
            try:
                data_display_state["font_size"] = int(font_var.get())
            except Exception:
                data_display_state["font_size"] = 18
            refresh_data_display()

        def toggle_config() -> None:
            visible = not bool(data_display_state.get("config_visible"))
            data_display_state["config_visible"] = visible
            config_button_var.set("Hide Configuration" if visible else "Configure")
            if visible:
                config.pack(fill="x", padx=10, pady=(0, 10), before=display_body)
            else:
                config.pack_forget()

        font_var.trace_add("write", set_data_display_font)
        data_display_state["field_vars"] = {}
        for index, (field_key, field_label) in enumerate(DATA_DISPLAY_FIELDS):
            var = tk.BooleanVar(value=bool(data_display_state["selected"].get(field_key, True)))
            data_display_state["field_vars"][field_key] = var

            def update_field(key: str = field_key, field_var: tk.BooleanVar = var) -> None:
                data_display_state["selected"][key] = bool(field_var.get())
                refresh_data_display()

            ttk.Checkbutton(fields_frame, text=field_label, variable=var, command=update_field).grid(
                row=index // 3, column=index % 3, sticky="w", padx=6, pady=2
            )
        for col in range(3):
            fields_frame.columnconfigure(col, weight=1)

        order_frame = ttk.LabelFrame(config, text="Display order", padding=6)
        order_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        order_frame.columnconfigure(0, weight=1)
        order_list = tk.Listbox(order_frame, height=6, exportselection=False)
        order_list.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 8))
        data_display_state["order_list"] = order_list

        def refresh_order_list(select_index: Optional[int] = None) -> None:
            labels = dict(DATA_DISPLAY_FIELDS)
            order_list.delete(0, "end")
            for field_key in data_display_state.get("display_order", []):
                order_list.insert("end", labels.get(field_key, field_key))
            if select_index is not None and order_list.size():
                select_index = max(0, min(select_index, order_list.size() - 1))
                order_list.selection_set(select_index)
                order_list.activate(select_index)

        def move_display_row(delta: int) -> None:
            selection = order_list.curselection()
            if not selection:
                return
            index = int(selection[0])
            new_index = max(0, min(index + delta, len(data_display_state["display_order"]) - 1))
            if new_index == index:
                return
            order = data_display_state["display_order"]
            order[index], order[new_index] = order[new_index], order[index]
            refresh_order_list(new_index)
            refresh_data_display()

        ttk.Button(order_frame, text="MOVE UP", command=lambda: move_display_row(-1)).grid(row=0, column=1, sticky="ew", pady=(0, 4))
        ttk.Button(order_frame, text="MOVE DOWN", command=lambda: move_display_row(1)).grid(row=1, column=1, sticky="ew")
        refresh_order_list()

        display_body = ttk.Frame(win, padding=(6, 0, 6, 8), relief="sunken", borderwidth=1)
        display_body.pack(fill="both", expand=True)
        data_display_state["value_vars"] = {}
        data_display_state["row_frames"] = {}
        for field_key, field_label in DATA_DISPLAY_FIELDS:
            row = ttk.Frame(display_body, padding=(4, 3), relief="groove", borderwidth=1)
            label = ttk.Label(row, text=field_label, width=18, anchor="w")
            label.pack(side="left", padx=(4, 10))
            value_var = tk.StringVar(value="")
            value = ttk.Label(row, textvariable=value_var, anchor="w")
            value.pack(side="left", fill="x", expand=True)
            data_display_state["value_vars"][field_key] = value_var
            data_display_state["row_frames"][field_key] = row

        def on_close() -> None:
            data_display_state["win"] = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        refresh_data_display()

    def show_io_monitor(auto_start: bool = False) -> None:
        win = tk.Toplevel(root)
        win.title("I/O Monitor")
        win.geometry("1100x700")
        win.minsize(640, 420)
        win.resizable(True, True)

        outer = ttk.Frame(win, padding=8)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        devices = configured_devices()
        try: configured_device_groups(devices)
        except ValueError as exc:
            win.destroy()
            messagebox.showerror(APP_TITLE, str(exc))
            return
        if not devices:
            ttk.Label(outer, text="No selected devices in Settings.").grid(row=0, column=0, sticky="nsew")

        def serial_connection_key(device: SerialSettings) -> str:
            return serial_connection_key_canonical(device)

        device_groups: dict[str, list[SerialSettings]] = {}
        for device in devices:
            device_groups.setdefault(serial_connection_key(device), []).append(device)
        grouped_devices = list(device_groups.values())

        grid = ttk.Frame(outer)
        grid.grid(row=0, column=0, sticky="nsew")
        columns = 2 if len(grouped_devices) > 1 else 1
        for col in range(columns):
            grid.columnconfigure(col, weight=1)
        for row in range(max(1, (len(grouped_devices) + columns - 1) // columns)):
            grid.rowconfigure(row, weight=1)

        def serial_kwargs(device: SerialSettings) -> dict[str, object]:
            return {
                "port": device.port,
                "baudrate": int(device.baud_rate),
                "bytesize": int(device.data_width),
                "parity": (device.parity or "N").upper()[0],
                "stopbits": float(device.stop_bits),
                "timeout": 0.6,
            }

        def raw_device_label(device: SerialSettings) -> str:
            return f"{device.title} | {device.port} {device.baud_rate} {device.data_width}-{device.parity}-{device.stop_bits}"

        def group_label(group: list[SerialSettings]) -> str:
            if not group:
                return "No device"
            titles = " + ".join(str(device.title) for device in group)
            first = group[0]
            suffix = " SHARED" if len(group) > 1 else ""
            return f"{titles} | {first.port} {first.baud_rate} {first.data_width}-{first.parity}-{first.stop_bits}{suffix}"

        def group_key(group: list[SerialSettings]) -> str:
            return serial_connection_key(group[0]) if group else "none"

        display: dict[str, Any] = {"win": win, "panes": {}, "has_data": {}}
        io_monitor_displays.append(display)

        for index, group in enumerate(grouped_devices):
            key = group_key(group)
            frame = ttk.LabelFrame(grid, text=group_label(group), padding=4)
            frame.grid(row=index // columns, column=index % columns, sticky="nsew", padx=4, pady=4)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            text_widget = tk.Text(frame, wrap="none", bg="#071018", fg="#d8dee9", insertbackground="#d8dee9")
            text_widget.insert("end", "No incoming data\n")
            text_widget.grid(row=0, column=0, sticky="nsew")
            yscroll = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
            yscroll.grid(row=0, column=1, sticky="ns")
            text_widget.configure(yscrollcommand=yscroll.set)
            display["panes"][key] = text_widget
            display["has_data"][key] = False

        def broadcast_device_raw(key: str, line: str) -> None:
            for item in list(io_monitor_displays):
                pane = item.get("panes", {}).get(key)
                win_obj = item.get("win")
                try:
                    if win_obj is None or not win_obj.winfo_exists() or pane is None or not pane.winfo_exists():
                        continue
                    if not item.get("has_data", {}).get(key, False):
                        pane.delete("1.0", "end")
                        item["has_data"][key] = True
                    pane.insert("end", line.rstrip("\n") + "\n")
                    pane.see("end")
                except Exception:
                    pass

        def schedule_device_raw(key: str, line: str) -> None:
            if not io_monitor_stop_event.is_set():
                try:
                    root.after(0, broadcast_device_raw, key, line)
                except Exception:
                    pass

        def is_gnss_device(device: SerialSettings) -> bool:
            return device is settings.gps or str(device.title).strip().upper() in {"GNSS", "GPS"}

        def device_accepts_line(device: SerialSettings, line: str, sentence_type: str, normalized_type: str) -> bool:
            accepted = set(getattr(device, "selected_sentences", []) or getattr(device, "sentence_options", []) or [])
            accepted = {str(item).strip().upper() for item in accepted if str(item).strip()}
            if not accepted:
                return True
            is_nmea_ascii = line.startswith("$")
            generic_allowed = ("NMEA" in accepted and is_nmea_ascii) or ("NON_NMEA" in accepted and not is_nmea_ascii)
            return sentence_type in accepted or normalized_type in accepted or generic_allowed

        def monitor_device_group(group):
            if group:
                _acquisition.monitor_group(group, group_key(group), broadcast_device_raw)

        io_monitor_stop_event.clear()
        for group in grouped_devices:
            key = group_key(group)
            if key in io_monitor_thread_keys:
                continue
            thread = threading.Thread(target=monitor_device_group, args=(group,), daemon=True)
            io_monitor_threads.append(thread)
            io_monitor_thread_keys.add(key)
            thread.start()

        def on_close() -> None:
            try:
                if display in io_monitor_displays:
                    io_monitor_displays.remove(display)
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", on_close)

    def show_ss510_control_panel() -> None:
        existing = getattr(show_ss510_control_panel, "_win", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.lift()
                return
        except Exception:
            pass
        try:
            import serial  # type: ignore
            from serial.tools import list_ports  # type: ignore
        except Exception:
            serial = None  # type: ignore
            list_ports = None  # type: ignore

        win = tk.Toplevel(root)
        win.title("SS510 Control Panel")
        win.geometry("1180x760")
        win.minsize(900, 560)
        show_ss510_control_panel._win = win
        serial_state: dict[str, Any] = {"port": None, "running": False}
        rx_queue: queue.Queue[tuple[str, str]] = queue.Queue()

        def checksum(sentence: str) -> str:
            body = str(sentence or "").strip()
            if body.startswith("$"):
                body = body[1:]
            if "*" in body:
                body = body.split("*", 1)[0]
            value = 0
            for char in body:
                value ^= ord(char)
            return f"{value:02X}"

        def format_ss510_command(command: str, add_checksum: bool = False) -> Optional[str]:
            line = str(command or "").strip()
            if not line:
                return None
            if not line.startswith("$"):
                line = "$" + line
            if add_checksum and "*" not in line:
                line = f"{line}*{checksum(line)}"
            return line + "\r\n"

        def depth_text(line: str) -> Optional[str]:
            parts = str(line or "").strip().split("*", 1)[0].split(",")
            if not parts or not parts[0].startswith("$"):
                return None
            if parts[0].endswith("DBT") and len(parts) >= 5 and parts[3]:
                return f"Depth: {parts[3]} m"
            if parts[0].endswith("DPT") and len(parts) >= 2 and parts[1]:
                return f"Depth: {parts[1]} m"
            return None

        outer = ttk.Frame(win, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)
        top = ttk.Frame(outer)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="SS510 Control Panel", font=("Segoe UI", 15, "bold")).pack(side="left")
        ss_status = tk.StringVar(value="Disconnected")
        ss_depth = tk.StringVar(value="Depth: --")
        ttk.Label(top, textvariable=ss_status).pack(side="left", padx=14)
        ttk.Label(top, textvariable=ss_depth, font=("Segoe UI", 11, "bold")).pack(side="right")

        left = ttk.Frame(outer)
        left.grid(row=1, column=0, sticky="ns", padx=(0, 10))
        right = ttk.Frame(outer)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        conn = ttk.LabelFrame(left, text="Connection", padding=8)
        conn.pack(fill="x", pady=(0, 8))
        port_var = tk.StringVar()
        baud_var = tk.StringVar(value="4800")
        checksum_var = tk.BooleanVar(value=False)
        timestamp_var = tk.BooleanVar(value=True)
        ttk.Label(conn, text="COM Port").grid(row=0, column=0, sticky="w", pady=3)
        port_combo = ttk.Combobox(conn, textvariable=port_var, width=15)
        port_combo.grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        ttk.Label(conn, text="Baud").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(conn, textvariable=baud_var, values=SS510_BAUD_RATES, state="readonly", width=15).grid(row=1, column=1, sticky="ew", padx=4, pady=3)
        conn.columnconfigure(1, weight=1)

        logs = ttk.PanedWindow(right, orient="vertical")
        logs.grid(row=1, column=0, sticky="nsew")
        tx_frame = ttk.LabelFrame(logs, text="Send Log", padding=4)
        rx_frame = ttk.LabelFrame(logs, text="Receive Log", padding=4)
        logs.add(tx_frame, weight=1)
        logs.add(rx_frame, weight=3)
        tx_log = tk.Text(tx_frame, height=8, wrap="none", state="disabled", font=("Consolas", 10))
        rx_log = tk.Text(rx_frame, height=24, wrap="none", state="disabled", font=("Consolas", 10))
        tx_log.pack(fill="both", expand=True)
        rx_log.pack(fill="both", expand=True)
        tx_log.tag_config("tx", foreground="#005c99")
        rx_log.tag_config("rx", foreground="#111827")
        rx_log.tag_config("err", foreground="#b00020")

        def append(widget: tk.Text, text: str, tag: str = "") -> None:
            widget.configure(state="normal")
            widget.insert("end", str(text).rstrip() + "\n", tag)
            widget.see("end")
            widget.configure(state="disabled")

        def refresh_ports() -> None:
            ports: list[str] = []
            if list_ports is not None:
                try:
                    ports = [item.device for item in list_ports.comports()]
                except Exception:
                    ports = []
            port_combo.configure(values=ports)
            if ports and not port_var.get():
                port_var.set(ports[0])

        def reader_worker() -> None:
            buffer = ""
            while serial_state.get("running"):
                port_obj = serial_state.get("port")
                try:
                    if port_obj is None or not getattr(port_obj, "is_open", False):
                        break
                    data = port_obj.read(512)
                    if not data:
                        continue
                    buffer += data.decode("ascii", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        rx_queue.put(("rx", line.rstrip("\r")))
                except Exception as exc:
                    rx_queue.put(("err", str(exc)))
                    break

        def close_port() -> None:
            serial_state["running"] = False
            port_obj = serial_state.get("port")
            try:
                if port_obj is not None and getattr(port_obj, "is_open", False):
                    port_obj.close()
            except Exception:
                pass
            serial_state["port"] = None
            connect_button.configure(text="Connect")
            ss_status.set("Disconnected")
            append(tx_log, "! Disconnected", "tx")

        def toggle_port() -> None:
            if serial_state.get("port") is not None:
                close_port()
                return
            if serial is None:
                messagebox.showerror("SS510 Control Panel", "pyserial is not installed.", parent=win)
                return
            if not port_var.get().strip():
                messagebox.showwarning("SS510 Control Panel", "Select a COM port first.", parent=win)
                return
            try:
                port_obj = serial.Serial(
                    port=port_var.get().strip(),
                    baudrate=int(baud_var.get()),
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.2,
                    write_timeout=1.0,
                )
            except Exception as exc:
                messagebox.showerror("SS510 Serial Port", str(exc), parent=win)
                return
            serial_state["port"] = port_obj
            serial_state["running"] = True
            threading.Thread(target=reader_worker, daemon=True).start()
            connect_button.configure(text="Disconnect")
            ss_status.set(f"Connected to {port_var.get()} at {baud_var.get()}")
            append(tx_log, f"! {port_var.get()},{baud_var.get()},8,N,1", "tx")

        buttons = ttk.Frame(conn)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        connect_button = ttk.Button(buttons, text="Connect", command=toggle_port)
        connect_button.pack(side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(buttons, text="Refresh", command=refresh_ports).pack(side="left", fill="x", expand=True, padx=(3, 0))
        ttk.Checkbutton(conn, text="Add NMEA checksum", variable=checksum_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Checkbutton(conn, text="Timestamp RX", variable=timestamp_var).grid(row=4, column=0, columnspan=2, sticky="w")

        def send_command(command: str) -> None:
            port_obj = serial_state.get("port")
            if port_obj is None or not getattr(port_obj, "is_open", False):
                messagebox.showwarning("SS510 Control Panel", "Open a COM port before sending commands.", parent=win)
                return
            line = format_ss510_command(command, checksum_var.get())
            if not line:
                return
            try:
                port_obj.write(line.encode("ascii", errors="replace"))
            except Exception as exc:
                messagebox.showerror("SS510 Serial Write", str(exc), parent=win)
                return
            append(tx_log, line.strip(), "tx")

        controls = ttk.LabelFrame(left, text="Output Controls", padding=8)
        controls.pack(fill="x", pady=(0, 8))
        sound_var = tk.IntVar(value=1492)
        sound_display_var = tk.StringVar(value="1492 m/s")
        rate_mode_var = tk.StringVar(value="Transmissions per second")
        rate_value_var = tk.StringVar(value="2")
        tps_var = tk.StringVar()
        tpm_var = tk.StringVar()
        seconds_var = tk.StringVar()
        ttk.Label(controls, text="Sound velocity").grid(row=0, column=0, sticky="w", pady=3)
        sound_frame = ttk.Frame(controls)
        sound_frame.grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        sound_frame.columnconfigure(0, weight=1)
        sound_scale = tk.Scale(sound_frame, from_=1400, to=1600, orient="horizontal", variable=sound_var, showvalue=False)
        sound_scale.grid(row=0, column=0, sticky="ew")
        ttk.Label(sound_frame, textvariable=sound_display_var, width=10).grid(row=0, column=1, sticky="e", padx=(6, 0))
        ttk.Label(controls, text="Rate selection").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(
            controls,
            textvariable=rate_mode_var,
            values=["Transmissions per second", "Transmissions per minute", "Seconds between transmissions"],
            state="readonly",
            width=28,
        ).grid(row=1, column=1, sticky="ew", padx=4, pady=3)
        ttk.Label(controls, text="Selected value").grid(row=2, column=0, sticky="w", pady=3)
        rate_frame = ttk.Frame(controls)
        rate_frame.grid(row=2, column=1, sticky="ew", padx=4, pady=3)
        rate_frame.columnconfigure(0, weight=1)
        rate_scale = tk.Scale(rate_frame, from_=0.1, to=10.0, resolution=0.1, orient="horizontal", showvalue=False)
        rate_scale.grid(row=0, column=0, sticky="ew")
        ttk.Entry(rate_frame, textvariable=rate_value_var, width=10).grid(row=0, column=1, sticky="e", padx=(6, 0))
        for row, (label, var) in enumerate([("Tx / second", tps_var), ("Tx / minute", tpm_var), ("Seconds between", seconds_var)], 3):
            ttk.Label(controls, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Label(controls, textvariable=var, font=("Segoe UI", 10, "bold")).grid(row=row, column=1, sticky="e", padx=4, pady=2)
        controls.columnconfigure(1, weight=1)
        rate_scale_guard = {"updating": False}

        def selected_seconds() -> float:
            value = max(parse_float(rate_value_var.get()) or 1.0, 0.001)
            if rate_mode_var.get() == "Transmissions per second":
                return 1.0 / value
            if rate_mode_var.get() == "Transmissions per minute":
                return 60.0 / value
            return value

        def update_rate_stats(*_args: Any) -> None:
            seconds = max(selected_seconds(), 0.001)
            tps_var.set(f"{1.0 / seconds:.3f}".rstrip("0").rstrip("."))
            tpm_var.set(f"{60.0 / seconds:.3f}".rstrip("0").rstrip("."))
            seconds_var.set(f"{seconds:.3f}".rstrip("0").rstrip("."))
            try:
                if not rate_scale_guard["updating"]:
                    rate_scale_guard["updating"] = True
                    rate_scale.set(float(parse_float(rate_value_var.get()) or 0.0))
                    rate_scale_guard["updating"] = False
            except Exception:
                rate_scale_guard["updating"] = False

        def update_sound_display(*_args: Any) -> None:
            sound_display_var.set(f"{int(sound_var.get())} m/s")

        def configure_rate_slider(*_args: Any) -> None:
            mode = rate_mode_var.get()
            if mode == "Transmissions per second":
                rate_scale.configure(from_=0.1, to=10.0, resolution=0.1)
                if parse_float(rate_value_var.get()) is None:
                    rate_value_var.set("2")
            elif mode == "Transmissions per minute":
                rate_scale.configure(from_=6, to=600, resolution=1)
                if parse_float(rate_value_var.get()) is None:
                    rate_value_var.set("120")
            else:
                rate_scale.configure(from_=0.1, to=10.0, resolution=0.1)
                if parse_float(rate_value_var.get()) is None:
                    rate_value_var.set("0.5")
            update_rate_stats()

        def rate_slider_changed(value: str) -> None:
            if rate_scale_guard["updating"]:
                return
            rate_scale_guard["updating"] = True
            number = parse_float(str(value)) or 0.0
            if rate_mode_var.get() == "Transmissions per minute":
                rate_value_var.set(f"{number:.0f}")
            else:
                rate_value_var.set(f"{number:.1f}".rstrip("0").rstrip("."))
            rate_scale_guard["updating"] = False
            update_rate_stats()

        def apply_sound() -> None:
            send_command(f"$PAMTC,OPTION,SET,SOSTW,{int(sound_var.get()) * 10}")

        def apply_rate() -> None:
            update_rate_stats()
            tenths = max(1, int(round(selected_seconds() * 10.0)))
            send_command(f"$PAMTC,EN,DBT,1.{tenths}")

        ttk.Button(controls, text="Set Sound Velocity", command=apply_sound).grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 3))
        ttk.Button(controls, text="Apply Output Rate", command=apply_rate).grid(row=7, column=0, columnspan=2, sticky="ew", pady=3)
        sound_var.trace_add("write", update_sound_display)
        rate_scale.configure(command=rate_slider_changed)
        rate_mode_var.trace_add("write", configure_rate_slider)
        rate_value_var.trace_add("write", update_rate_stats)
        configure_rate_slider()

        raw = ttk.LabelFrame(right, text="Raw Command", padding=8)
        raw.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        raw.columnconfigure(0, weight=1)
        raw_var = tk.StringVar(value="$PAMTC,RESET")
        ttk.Entry(raw, textvariable=raw_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(raw, text="Send", command=lambda: send_command(raw_var.get())).grid(row=0, column=1)

        bottom = ttk.Frame(right)
        bottom.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        cmd_box = ttk.LabelFrame(bottom, text="SS510 Commands", padding=6)
        cmd_box.pack(side="left", fill="both", expand=True, padx=(0, 6))
        for index, (label, command) in enumerate(SS510_COMMANDS):
            ttk.Button(cmd_box, text=label, command=lambda cmd=command: send_command(cmd)).grid(row=index // 3, column=index % 3, sticky="ew", padx=3, pady=2)
            cmd_box.columnconfigure(index % 3, weight=1)
        quick_box = ttk.LabelFrame(bottom, text="Quick Set Options", padding=6)
        quick_box.pack(side="left", fill="both", expand=True)
        for row, (label, prefix, default) in enumerate(SS510_QUICK_SET):
            var = tk.StringVar(value=default)
            ttk.Label(quick_box, text=label).grid(row=row, column=0, sticky="w", padx=3, pady=2)
            ttk.Entry(quick_box, textvariable=var, width=18).grid(row=row, column=1, sticky="ew", padx=3, pady=2)
            ttk.Button(quick_box, text="Set", command=lambda p=prefix, v=var: send_command(f"{p},{v.get()}")).grid(row=row, column=2, sticky="ew", padx=3, pady=2)
        quick_box.columnconfigure(1, weight=1)

        file_buttons = ttk.Frame(left)
        file_buttons.pack(fill="x")
        def clear_logs() -> None:
            for widget in (tx_log, rx_log):
                widget.configure(state="normal")
                widget.delete("1.0", "end")
                widget.configure(state="disabled")

        def save_log() -> None:
            name = filedialog.asksaveasfilename(
                title="Save SS510 log",
                initialdir=str(project_root_from_raw_folder(settings.raw_folder)),
                initialfile=f"ss510-log-{datetime.now():%Y%m%d-%H%M%S}.txt",
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            Path(name).write_text("[Send]\n" + tx_log.get("1.0", "end") + "\n[Receive]\n" + rx_log.get("1.0", "end"), encoding="utf-8")
            ss_status.set(f"Saved SS510 log: {Path(name).name}")

        ttk.Button(file_buttons, text="Clear Logs", command=clear_logs).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(file_buttons, text="Save Log", command=save_log).pack(side="left", fill="x", expand=True, padx=(3, 0))

        def poll_rx() -> None:
            try:
                while True:
                    kind, text = rx_queue.get_nowait()
                    if kind == "rx":
                        prefix = f"[{datetime.now():%H:%M:%S.%f}"[:-3] + "] " if timestamp_var.get() else ""
                        append(rx_log, prefix + text, "rx")
                        parsed_depth = depth_text(text)
                        if parsed_depth:
                            ss_depth.set(parsed_depth)
                    else:
                        append(rx_log, "RX error: " + text, "err")
            except queue.Empty:
                pass
            try:
                if win.winfo_exists():
                    win.after(100, poll_rx)
            except Exception:
                pass

        def on_close() -> None:
            close_port()
            show_ss510_control_panel._win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        refresh_ports()
        update_rate_stats()
        poll_rx()

    def show_project_dialog() -> None:
        win = tk.Toplevel(root)
        win.title("PROJECTS")
        win.geometry("1100x720")
        win.minsize(880, 560)
        try:
            win.state("zoomed")
        except Exception:
            try:
                win.attributes("-zoomed", True)
            except Exception:
                pass
        win.after(100, lambda: win.state("zoomed") if str(win.state()) != "zoomed" else None)
        outer = ttk.Frame(win)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        hscroll = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        frame = ttk.Frame(canvas, padding=16)
        canvas_window = canvas.create_window((0, 0), window=frame, anchor="nw")

        def update_project_scroll_region(_event: Any = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            try:
                canvas.itemconfigure(canvas_window, width=max(canvas.winfo_width(), frame.winfo_reqwidth()))
            except Exception:
                pass

        frame.bind("<Configure>", update_project_scroll_region)
        canvas.bind("<Configure>", update_project_scroll_region)

        def on_project_mousewheel(event: Any) -> str:
            try:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass
            return "break"

        canvas.bind_all("<MouseWheel>", on_project_mousewheel)
        win.bind("<Destroy>", lambda _event: canvas.unbind_all("<MouseWheel>"))
        project_name_var = tk.StringVar(value=project.name)
        current_project_folder = project_root_from_raw_folder(settings.raw_folder or str(DEFAULT_PROJECT_FOLDER))
        project_path_var = tk.StringVar(value=str(current_project_folder))
        new_folder_var = tk.StringVar(value=str(current_project_folder.parent / "New_HYDROGEOMATICS_Project"))
        copy_source_var = tk.StringVar(value=str(current_project_folder))
        copy_target_var = tk.StringVar(value=str(current_project_folder.parent / f"{current_project_folder.name}_Copy"))
        action_var = tk.StringVar(value="use_existing")
        project_dialog_status = tk.StringVar(value=f"Default: use existing project folder {current_project_folder}")

        ttk.Label(frame, text="PROJECTS", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        ttk.Label(
            frame,
            text="Select an existing HYDROGEOMATICS project folder, create a new project folder, or copy an existing project. RAW, EDIT, and SORT folders are created automatically.",
            wraplength=980,
        ).grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 12))

        action_frame = ttk.LabelFrame(frame, text="Project action", padding=10)
        action_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        actions = [
            ("Use existing project folder", "use_existing"),
            ("Create new project", "create_new"),
            ("Copy project", "copy_project"),
        ]
        for index, (label, value) in enumerate(actions):
            ttk.Radiobutton(action_frame, text=label, variable=action_var, value=value).grid(row=0, column=index, sticky="w", padx=10, pady=4)

        existing_frame = ttk.LabelFrame(frame, text="Use existing project by default", padding=10)
        existing_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        ttk.Label(existing_frame, text="Existing project folder").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        existing_combo = ttk.Combobox(existing_frame, textvariable=project_path_var, values=discover_project_options(), width=80)
        existing_combo.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        existing_frame.columnconfigure(1, weight=1)

        def browse_existing_folder() -> None:
            folder = filedialog.askdirectory(title="Use existing project folder", initialdir=project_path_var.get() or str(DEFAULT_PROJECT_FOLDER), parent=win)
            if folder:
                project_path_var.set(folder)
                action_var.set("use_existing")

        ttk.Button(existing_frame, text="Browse Existing", command=browse_existing_folder).grid(row=0, column=2, sticky="ew", padx=4, pady=4)

        new_frame = ttk.LabelFrame(frame, text="Create new project folder", padding=10)
        new_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        ttk.Label(new_frame, text="Project name").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(new_frame, textvariable=project_name_var, width=36).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(new_frame, text="New folder").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(new_frame, textvariable=new_folder_var).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        new_frame.columnconfigure(1, weight=1)

        def browse_new_folder_parent() -> None:
            folder = filedialog.askdirectory(title="Select parent/new project folder", initialdir=str(current_project_folder.parent), parent=win)
            if folder:
                chosen = Path(folder)
                if chosen.exists() and any(chosen.iterdir()):
                    new_folder_var.set(str(chosen / safe_name(project_name_var.get().strip() or "New_HYDROGEOMATICS_Project")))
                else:
                    new_folder_var.set(str(chosen))
                action_var.set("create_new")

        ttk.Button(new_frame, text="Browse / Create Folder", command=browse_new_folder_parent).grid(row=1, column=2, sticky="ew", padx=4, pady=4)

        copy_frame = ttk.LabelFrame(frame, text="Copy project", padding=10)
        copy_frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        ttk.Label(copy_frame, text="Source project").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(copy_frame, textvariable=copy_source_var, values=discover_project_options(), width=80).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(copy_frame, text="Target folder").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(copy_frame, textvariable=copy_target_var).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        copy_frame.columnconfigure(1, weight=1)

        def browse_copy_source() -> None:
            folder = filedialog.askdirectory(title="Select project to copy", initialdir=copy_source_var.get() or str(DEFAULT_PROJECT_FOLDER), parent=win)
            if folder:
                copy_source_var.set(folder)
                action_var.set("copy_project")

        def browse_copy_target() -> None:
            folder = filedialog.askdirectory(title="Select copied project target folder", initialdir=str(current_project_folder.parent), parent=win)
            if folder:
                copy_target_var.set(folder)
                action_var.set("copy_project")

        ttk.Button(copy_frame, text="Browse Source", command=browse_copy_source).grid(row=0, column=2, sticky="ew", padx=4, pady=4)
        ttk.Button(copy_frame, text="Browse Target", command=browse_copy_target).grid(row=1, column=2, sticky="ew", padx=4, pady=4)

        project_list_frame = ttk.LabelFrame(frame, text="Detected project folders", padding=10)
        project_list_frame.grid(row=6, column=0, columnspan=4, sticky="nsew", pady=(0, 10))
        project_list = tk.Listbox(project_list_frame, height=6)
        project_list_scroll = ttk.Scrollbar(project_list_frame, orient="vertical", command=project_list.yview)
        project_list.configure(yscrollcommand=project_list_scroll.set)
        project_list.grid(row=0, column=0, sticky="nsew")
        project_list_scroll.grid(row=0, column=1, sticky="ns")
        project_list_frame.rowconfigure(0, weight=1)
        project_list_frame.columnconfigure(0, weight=1)

        def refresh_project_list() -> None:
            project_list.delete(0, "end")
            for option in discover_project_options():
                project_list.insert("end", option)

        def choose_list_project(_event: Any = None) -> None:
            selection = project_list.curselection()
            if not selection:
                return
            folder = project_list.get(selection[0])
            project_path_var.set(folder)
            copy_source_var.set(folder)
            action_var.set("use_existing")

        project_list.bind("<Double-Button-1>", choose_list_project)
        refresh_project_list()

        ttk.Label(frame, textvariable=project_dialog_status, font=("Segoe UI", 10, "bold")).grid(row=7, column=0, columnspan=4, sticky="ew", pady=(0, 8))

        def apply_project_folder(folder: Path, name: str | None = None, clear_current: bool = False) -> None:
            nonlocal project
            _project_folder, raw_path, _edit_path, _sort_path = ensure_project_subfolders_from_raw(folder)
            settings.raw_folder = str(raw_path)
            if clear_current:
                project = HydroProject(name=name or folder.name or "HYDROGEOMATICS Project")
                model.clear()
                runlines.clear()
            else:
                project.name = name or project.name or folder.name
            save_default_settings()
            project_path_var.set(str(_project_folder))
            refresh_project_list()
            draw_view()
            status_var.set(f"Active project folder: {_project_folder}")
            project_dialog_status.set(f"Active project folder: {_project_folder} | RAW, EDIT, SORT ready")

        def use_existing_project() -> None:
            folder = Path(project_path_var.get().strip() or current_project_folder)
            apply_project_folder(folder, project_name_var.get().strip() or folder.name, clear_current=False)

        def create_new_project() -> None:
            folder = Path(new_folder_var.get().strip() or (current_project_folder.parent / safe_name(project_name_var.get().strip() or "New_HYDROGEOMATICS_Project")))
            if folder.exists() and any(folder.iterdir()):
                if not messagebox.askyesno(APP_TITLE, f"The folder already contains files:\n{folder}\n\nUse it as a project folder anyway?", parent=win):
                    return
            apply_project_folder(folder, project_name_var.get().strip() or folder.name, clear_current=True)

        def copy_project() -> None:
            source = Path(copy_source_var.get().strip() or current_project_folder)
            target = Path(copy_target_var.get().strip())
            if not source.exists() or not source.is_dir():
                messagebox.showerror(APP_TITLE, f"Source project folder does not exist:\n{source}", parent=win)
                return
            if not str(target):
                messagebox.showerror(APP_TITLE, "Select a target folder for the copied project.", parent=win)
                return
            if target.exists() and any(target.iterdir()):
                messagebox.showerror(APP_TITLE, f"Target folder already exists and is not empty:\n{target}", parent=win)
                return
            try:
                shutil.copytree(source, target, dirs_exist_ok=True)
                ensure_project_subfolders_from_raw(target)
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"Could not copy project:\n{exc}", parent=win)
                return
            apply_project_folder(target, project_name_var.get().strip() or target.name, clear_current=False)

        def apply_selected_action(close_after: bool = False) -> None:
            action = action_var.get()
            if action == "create_new":
                create_new_project()
            elif action == "copy_project":
                copy_project()
            else:
                use_existing_project()
            if close_after:
                win.destroy()

        def new_project() -> None:
            action_var.set("create_new")
            create_new_project()

        def open_project() -> None:
            nonlocal project
            name = filedialog.askopenfilename(
                title="Open existing project",
                initialdir=str(DEFAULT_PROJECT_FOLDER),
                filetypes=[("SurveyPlan project", "*.json"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            try:
                project = load_project(name)
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"Could not open project:\n{exc}", parent=win)
                return
            project_folder = Path(name).parent
            _project_folder, raw_path, _edit_path, _sort_path = ensure_project_subfolders_from_raw(project_folder)
            settings.raw_folder = str(raw_path)
            save_default_settings()
            project_path_var.set(str(project_folder))
            project_name_var.set(project.name)
            status_var.set(f"Opened project: {project.name} | Project folders ready: RAW, EDIT, SORT")
            draw_view()

        def save_current_project() -> None:
            name = filedialog.asksaveasfilename(
                title="Save project",
                initialdir=str(DEFAULT_PROJECT_FOLDER),
                defaultextension=".json",
                filetypes=[("SurveyPlan project", "*.json"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            project.name = project_name_var.get().strip() or project.name
            try:
                save_project(name, project)
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"Could not save project:\n{exc}", parent=win)
                return
            status_var.set(f"Saved project: {Path(name).name}")

        buttons = ttk.Frame(win, padding=(12, 8, 12, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Use Existing Project", command=lambda: (action_var.set("use_existing"), use_existing_project())).pack(side="left", padx=4)
        ttk.Button(buttons, text="Create New Project", command=lambda: (action_var.set("create_new"), create_new_project())).pack(side="left", padx=4)
        ttk.Button(buttons, text="Copy Project", command=lambda: (action_var.set("copy_project"), copy_project())).pack(side="left", padx=4)
        ttk.Button(buttons, text="Open Project File", command=open_project).pack(side="left", padx=4)
        ttk.Button(buttons, text="Save Project File", command=save_current_project).pack(side="left", padx=4)
        ttk.Button(buttons, text="Apply Selected Action", command=lambda: apply_selected_action(False)).pack(side="left", padx=4)
        ttk.Button(buttons, text="Apply && Close", command=lambda: apply_selected_action(True)).pack(side="right", padx=4)
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="right", padx=4)
        for col in range(4):
            frame.columnconfigure(col, weight=1)
        frame.rowconfigure(6, weight=1)

    def load_runlines() -> None:
        name = filedialog.askopenfilename(
            title="Open runlines",
            filetypes=[("HYPACK runlines", "*.lnw"), ("Runline CSV", "*.csv"), ("All files", "*.*")],
        )
        if not name:
            return
        try:
            count = runlines.load_csv(Path(name))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not load runlines:\n{exc}")
            return
        refresh_line_selector_options()
        draw_view()
        status_var.set(f"Loaded {count} runlines")

    def save_runlines() -> None:
        name = filedialog.asksaveasfilename(
            title="Save runlines",
            defaultextension=".lnw",
            filetypes=[("HYPACK runlines", "*.lnw"), ("Runline CSV", "*.csv"), ("All files", "*.*")],
        )
        if not name:
            return
        try:
            count = runlines.export_csv(Path(name))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not save runlines:\n{exc}")
            return
        status_var.set(f"Saved {count} runlines")

    def generate_runlines_dialog() -> None:
        view_mode.set("plan")
        win = tk.Toplevel(root)
        win.title("GENERATE RUN LINES")
        win.geometry("860x620")
        win.minsize(720, 520)
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        pick_state_var = tk.StringVar(value="Enter coordinates or pick points from the navigation screen.")

        baseline = ttk.LabelFrame(frame, text="Parallel line generator", padding=8)
        baseline.grid(row=0, column=0, sticky="ew")
        for col in range(6):
            baseline.columnconfigure(col, weight=1)

        start_x_var = tk.StringVar()
        start_y_var = tk.StringVar()
        end_x_var = tk.StringVar()
        end_y_var = tk.StringVar()
        spacing_var = tk.StringVar(value="25.000")
        count_var = tk.StringVar(value="5")
        prefix_var = tk.StringVar(value="RL")

        baseline_fields = [
            ("Start E", start_x_var),
            ("Start N", start_y_var),
            ("End E", end_x_var),
            ("End N", end_y_var),
            ("Spacing", spacing_var),
            ("Each side", count_var),
            ("Prefix", prefix_var),
        ]
        for index, (label, var) in enumerate(baseline_fields):
            ttk.Label(baseline, text=label).grid(row=index // 4 * 2, column=index % 4, sticky="w", padx=4, pady=(0, 2))
            ttk.Entry(baseline, textvariable=var, width=14).grid(row=index // 4 * 2 + 1, column=index % 4, sticky="ew", padx=4, pady=(0, 6))

        manual = ttk.LabelFrame(frame, text="Individual line start/end points", padding=8)
        manual.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        manual.columnconfigure(0, weight=1)
        manual.rowconfigure(0, weight=1)

        line_columns = ("name", "sx", "sy", "ex", "ey")
        line_tree = ttk.Treeview(manual, columns=line_columns, show="headings", height=10)
        for col, heading, width_col in [
            ("name", "Line", 90),
            ("sx", "Start E", 120),
            ("sy", "Start N", 120),
            ("ex", "End E", 120),
            ("ey", "End N", 120),
        ]:
            line_tree.heading(col, text=heading)
            line_tree.column(col, width=width_col, anchor="e" if col != "name" else "w")
        line_tree.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        line_scroll = ttk.Scrollbar(manual, orient="vertical", command=line_tree.yview)
        line_scroll.grid(row=0, column=1, sticky="ns")
        line_tree.configure(yscrollcommand=line_scroll.set)

        edit = ttk.Frame(manual)
        edit.grid(row=0, column=2, sticky="nsew")
        line_name_var = tk.StringVar(value="L1")
        line_sx_var = tk.StringVar()
        line_sy_var = tk.StringVar()
        line_ex_var = tk.StringVar()
        line_ey_var = tk.StringVar()
        edit_fields = [
            ("Line name", line_name_var),
            ("Start E", line_sx_var),
            ("Start N", line_sy_var),
            ("End E", line_ex_var),
            ("End N", line_ey_var),
        ]
        for row, (label, var) in enumerate(edit_fields):
            ttk.Label(edit, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(edit, textvariable=var, width=18).grid(row=row, column=1, sticky="ew", pady=3)
        edit.columnconfigure(1, weight=1)

        manual_rows: list[RunLine] = []

        def refresh_manual_tree(select_index: Optional[int] = None) -> None:
            line_tree.delete(*line_tree.get_children())
            for index, line in enumerate(manual_rows):
                line_tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        line.name,
                        f"{line.start_x:.3f}",
                        f"{line.start_y:.3f}",
                        f"{line.end_x:.3f}",
                        f"{line.end_y:.3f}",
                    ),
                )
            if select_index is not None and manual_rows:
                select_index = max(0, min(select_index, len(manual_rows) - 1))
                line_tree.selection_set(str(select_index))
                line_tree.focus(str(select_index))
                load_selected_line_to_fields()

        def selected_manual_index() -> Optional[int]:
            selection = line_tree.selection()
            if not selection:
                return None
            try:
                return int(selection[0])
            except ValueError:
                return None

        def load_selected_line_to_fields(_event: Any = None) -> None:
            index = selected_manual_index()
            if index is None or index >= len(manual_rows):
                return
            line = manual_rows[index]
            line_name_var.set(line.name)
            line_sx_var.set(f"{line.start_x:.3f}")
            line_sy_var.set(f"{line.start_y:.3f}")
            line_ex_var.set(f"{line.end_x:.3f}")
            line_ey_var.set(f"{line.end_y:.3f}")
            if use_selected_line.get() and index < len(runlines.lines):
                select_line_index(index, announce=False)

        def line_from_edit() -> Optional[RunLine]:
            try:
                return RunLine(
                    line_name_var.get().strip() or f"L{len(manual_rows) + 1}",
                    float(line_sx_var.get()),
                    float(line_sy_var.get()),
                    float(line_ex_var.get()),
                    float(line_ey_var.get()),
                )
            except ValueError:
                messagebox.showerror(APP_TITLE, "Enter valid numeric Start/End coordinates for the individual line.", parent=win)
                return None

        def add_manual_line() -> None:
            line = line_from_edit()
            if line is None:
                return
            manual_rows.append(line)
            line_name_var.set(f"L{len(manual_rows) + 1}")
            refresh_manual_tree(len(manual_rows) - 1)
            status_var.set(f"Added individual line {line.name}")

        def update_manual_line() -> None:
            index = selected_manual_index()
            if index is None:
                messagebox.showinfo(APP_TITLE, "Select a line row to update.", parent=win)
                return
            line = line_from_edit()
            if line is None:
                return
            manual_rows[index] = line
            refresh_manual_tree(index)
            status_var.set(f"Updated line {line.name}")

        def remove_manual_line() -> None:
            index = selected_manual_index()
            if index is None:
                return
            removed = manual_rows.pop(index)
            refresh_manual_tree(max(0, index - 1) if manual_rows else None)
            status_var.set(f"Removed line {removed.name}")

        def set_pick_target(target: str, mode: str = "baseline") -> None:
            def receive_pick(world_x: float, world_y: float) -> None:
                if mode == "manual":
                    if target == "start":
                        line_sx_var.set(f"{world_x:.3f}")
                        line_sy_var.set(f"{world_y:.3f}")
                        pick_state_var.set(f"Selected line start: E {world_x:.3f}, N {world_y:.3f}")
                    else:
                        line_ex_var.set(f"{world_x:.3f}")
                        line_ey_var.set(f"{world_y:.3f}")
                        pick_state_var.set(f"Selected line end: E {world_x:.3f}, N {world_y:.3f}")
                    index = selected_manual_index()
                    if index is not None and all(v.get().strip() for v in [line_sx_var, line_sy_var, line_ex_var, line_ey_var]):
                        line = line_from_edit()
                        if line is not None:
                            manual_rows[index] = line
                            refresh_manual_tree(index)
                elif target == "start":
                    start_x_var.set(f"{world_x:.3f}")
                    start_y_var.set(f"{world_y:.3f}")
                    pick_state_var.set(f"Baseline start selected: E {world_x:.3f}, N {world_y:.3f}")
                else:
                    end_x_var.set(f"{world_x:.3f}")
                    end_y_var.set(f"{world_y:.3f}")
                    pick_state_var.set(f"Baseline end selected: E {world_x:.3f}, N {world_y:.3f}")
                view_state["line_pick_callback"] = None
                view_state["line_pick_target"] = None
                status_var.set(pick_state_var.get())
                win.lift()

            view_state["line_pick_callback"] = receive_pick
            view_state["line_pick_target"] = f"{mode}:{target}"
            view_state["zoom_window"] = False
            status_var.set(f"Click on the navigation screen to pick {mode} line {target}.")
            pick_state_var.set(f"Waiting for map click to pick {mode} {target}.")
            win.q.hide()

        def generate_parallel_from_values() -> None:
            try:
                start_x = float(start_x_var.get())
                start_y = float(start_y_var.get())
                end_x = float(end_x_var.get())
                end_y = float(end_y_var.get())
                spacing = float(spacing_var.get())
                count = int(float(count_var.get()))
                prefix = prefix_var.get().strip() or "RL"
            except ValueError:
                messagebox.showerror(APP_TITLE, "Enter valid numeric baseline coordinates, spacing, and line count.", parent=win)
                return
            if spacing <= 0.0 or count < 0:
                messagebox.showerror(APP_TITLE, "Spacing must be positive and line count cannot be negative.", parent=win)
                return
            try:
                runlines.clear()
                runlines.generate_parallel(start_x, start_y, end_x, end_y, spacing, count, prefix)
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"Could not generate runlines:\n{exc}", parent=win)
                return
            refresh_line_selector_options()
            draw_view()
            status_var.set(f"Generated {len(runlines.lines)} parallel runlines")

        def apply_manual_lines() -> None:
            if not manual_rows:
                messagebox.showinfo(APP_TITLE, "Add at least one individual line first.", parent=win)
                return
            runlines.clear()
            for line in manual_rows:
                runlines.add(line)
            if selected_line_index.get() >= len(runlines.lines):
                selected_line_index.set(0 if runlines.lines else -1)
            refresh_line_selector_options()
            draw_view()
            status_var.set(f"Applied {len(manual_rows)} individual runlines")

        def import_current_lines() -> None:
            manual_rows.clear()
            manual_rows.extend(RunLine(line.name, line.start_x, line.start_y, line.end_x, line.end_y, line.enabled) for line in runlines.lines)
            select_index = selected_line_index.get() if 0 <= selected_line_index.get() < len(manual_rows) else (0 if manual_rows else None)
            refresh_manual_tree(select_index)
            status_var.set(f"Loaded {len(manual_rows)} current lines into manual editor")

        def on_close() -> None:
            view_state["line_pick_callback"] = None
            view_state["line_pick_target"] = None
            win.destroy()

        ttk.Button(baseline, text="Pick Baseline Start", command=lambda: set_pick_target("start", "baseline")).grid(row=0, column=10, sticky="ew", padx=4, pady=2)
        ttk.Button(baseline, text="Pick Baseline End", command=lambda: set_pick_target("end", "baseline")).grid(row=1, column=4, sticky="ew", padx=4, pady=2)
        ttk.Button(baseline, text="Generate Parallel Lines", command=generate_parallel_from_values).grid(row=2, column=4, sticky="ew", padx=4, pady=2)

        ttk.Button(edit, text="Pick Line Start", command=lambda: set_pick_target("start", "manual")).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 3))
        ttk.Button(edit, text="Pick Line End", command=lambda: set_pick_target("end", "manual")).grid(row=6, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Button(edit, text="Add Line", command=add_manual_line).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 3))
        ttk.Button(edit, text="Update Selected", command=update_manual_line).grid(row=8, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Button(edit, text="Remove Selected", command=remove_manual_line).grid(row=9, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Button(edit, text="Import Current Lines", command=import_current_lines).grid(row=10, column=0, columnspan=2, sticky="ew", pady=(8, 3))
        ttk.Button(edit, text="Apply Manual Lines", command=apply_manual_lines).grid(row=11, column=0, columnspan=2, sticky="ew", pady=3)

        nav_select = ttk.LabelFrame(edit, text="Navigation line selection", padding=6)
        nav_select.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(10, 3))
        nav_select.columnconfigure(0, weight=1)
        ttk.Checkbutton(nav_select, text="Use Selected Line", variable=use_selected_line, command=draw_view).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(nav_select, text="Auto Select Nearest Line", variable=auto_select_nearest_line, command=lambda: (use_selected_line.set(True), draw_view())).grid(row=1, column=0, sticky="w")
        ttk.Button(nav_select, text="NEXT LINE", command=select_next_line).grid(row=2, column=0, sticky="ew", pady=(5, 0))

        line_tree.bind("<<TreeviewSelect>>", load_selected_line_to_fields)
        ttk.Label(frame, textvariable=pick_state_var, wraplength=760, foreground="#244d9a").grid(row=2, column=0, sticky="ew", pady=(10, 6))
        bottom = ttk.Frame(frame)
        bottom.grid(row=3, column=0, sticky="ew")
        ttk.Button(bottom, text="Close", command=on_close).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", on_close)
        import_current_lines()

    def active_border_points() -> list[tuple[float, float]]:
        return list(border_points)

    def clip_lines_by_border_dialog() -> None:
        nonlocal border_points, dxf_background_shapes, enc_background_shapes
        polygon = active_border_points()
        if len(polygon) < 3:
            name = filedialog.askopenfilename(
                title="Select HYPACK border file for clipping",
                filetypes=[("HYPACK border", "*.brd"), ("All files", "*.*")],
            )
            if not name:
                return
            try:
                polygon = load_hypack_brd_points(Path(name))
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"Could not load border file:\n{exc}")
                return
            border_points = polygon
        if len(polygon) < 3:
            messagebox.showerror(APP_TITLE, "Border must contain at least three points.")
            return
        keep_inside = messagebox.askyesno(
            APP_TITLE,
            "Clip all available lines, DXF/ENC background, and XYZ/soundings INSIDE the border?\n\nYes = keep inside border\nNo = keep outside border",
        )
        before_lines = len(runlines.lines)
        before_dxf = len(dxf_background_shapes)
        before_enc = len(enc_background_shapes)
        before_points = len(model.points)

        clipped_lines = clip_runlines_to_polygon(runlines.lines, polygon, keep_inside=keep_inside)
        clipped_dxf = clip_map_shapes_to_polygon(dxf_background_shapes, polygon, keep_inside=keep_inside)
        clipped_enc = clip_map_shapes_to_polygon(enc_background_shapes, polygon, keep_inside=keep_inside)
        clipped_points = clip_survey_points_to_polygon(model.points, polygon, keep_inside=keep_inside)

        if before_lines and not clipped_lines:
            if not messagebox.askyesno(APP_TITLE, "No run line segments remain after clipping. Continue and clear run lines?"):
                return
        if before_points and not clipped_points:
            if not messagebox.askyesno(APP_TITLE, "No XYZ/sounding points remain after clipping. Continue and clear points?"):
                return

        if before_lines:
            runlines.clear()
            for line in clipped_lines:
                runlines.add(line)
        if before_dxf:
            dxf_background_shapes = clipped_dxf
        if before_enc:
            enc_background_shapes = clipped_enc
        if before_points:
            model.points = clipped_points
            refresh_table()
            refresh_data_display()

        draw_view()
        status_var.set(
            f"Clipped {'inside' if keep_inside else 'outside'} border: "
            f"lines {before_lines}->{len(runlines.lines)}, "
            f"DXF {before_dxf}->{len(dxf_background_shapes)}, "
            f"ENC {before_enc}->{len(enc_background_shapes)}, "
            f"XYZ {before_points}->{len(model.points)}"
        )

    def show_border_dialog() -> None:
        nonlocal border_points
        win = tk.Toplevel(root)
        win.title("BORDER")
        win.geometry("540x360")
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        info_var = tk.StringVar()

        def refresh_info() -> None:
            info_var.set(f"Border points: {len(border_points)}")
            draw_view()

        def start_border_creation() -> None:
            def receive_border_point(world_x: float, world_y: float) -> None:
                border_points.append((world_x, world_y))
                status_var.set(f"Added border point {len(border_points)}: E {world_x:.3f}, N {world_y:.3f}")
                refresh_info()

            view_mode.set("plan")
            view_state["border_pick_callback"] = receive_border_point
            view_state["zoom_window"] = False
            status_var.set("Border creation active: click the navigation screen to add border points.")
            refresh_info()

        def stop_border_creation() -> None:
            view_state["border_pick_callback"] = None
            status_var.set("Border creation stopped")

        def load_border() -> None:
            nonlocal border_points
            name = filedialog.askopenfilename(
                title="Load HYPACK border",
                filetypes=[("HYPACK border", "*.brd"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            try:
                border_points = load_hypack_brd_points(Path(name))
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"Could not load border:\n{exc}", parent=win)
                return
            status_var.set(f"Loaded border: {Path(name).name}")
            refresh_info()

        def save_border() -> None:
            if len(border_points) < 3:
                messagebox.showerror(APP_TITLE, "Create or load at least three border points before saving.", parent=win)
                return
            name = filedialog.asksaveasfilename(
                title="Save HYPACK border",
                defaultextension=".brd",
                filetypes=[("HYPACK border", "*.brd"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            points_to_save = list(border_points)
            if points_to_save[0] != points_to_save[-1]:
                points_to_save.append(points_to_save[0])
            try:
                count = save_hypack_brd_points(Path(name), points_to_save)
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"Could not save border:\n{exc}", parent=win)
                return
            status_var.set(f"Saved {count} border points to {Path(name).name}")

        def create_from_banks() -> None:
            def receive(points):
                nonlocal border_points
                border_points = list(points)
                view_state['border_pick_callback'] = None
                status_var.set('Created river-bank border. Use Save Border to write a HYPACK .brd file.')
                refresh_info()
            show_river_bank_border(receive)

        def clear_border() -> None:
            nonlocal border_points
            border_points = []
            view_state["border_pick_callback"] = None
            status_var.set("Border cleared")
            refresh_info()

        def on_close() -> None:
            view_state["border_pick_callback"] = None
            win.destroy()

        ttk.Label(frame, textvariable=info_var, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Button(frame, text="Start Border Creation", command=start_border_creation).grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Stop Creation", command=stop_border_creation).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Load Border", command=load_border).grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Save Border", command=save_border).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="New Border from East / West Bank DXF", command=create_from_banks).grid(row=5, column=0, columnspan=2, sticky="ew", padx=4, pady=8)
        ttk.Button(frame, text="Clear Border", command=clear_border).grid(row=3, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Close", command=on_close).grid(row=3, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(frame, text="Click Start Border Creation, then click points on the navigation screen. Save writes a HYPACK .brd border.", wraplength=390).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        win.protocol("WM_DELETE_WINDOW", on_close)
        refresh_info()


    def refresh_dxf_background_from_path(path_obj: Path, show_errors: bool = True) -> int:
        """Load a CAD DXF/DWG file as a plan-view background layer, not as editor content."""
        nonlocal dxf_background_shapes
        try:
            shapes = load_cad_background_shapes(path_obj)
        except Exception as exc:
            if show_errors:
                messagebox.showerror(APP_TITLE, f"Could not load CAD background:\n{exc}")
            return 0
        dxf_background_shapes = [simplify_background_shape(shape, max_points=5000) for shape in shapes]
        rebuild_background_cache(dxf_background_shapes, enc_background_shapes, kmz_background_shapes)
        settings.dxf_background_path = str(path_obj)
        save_default_settings()
        follow_vessel.set(False)
        view_mode.set("plan")
        fit_view()
        status_var.set(f"Loaded CAD background: {path_obj.name} ({len(shapes)} item(s))")
        return len(shapes)

    def show_oda_dwg_converter_dialog(parent: Any = None) -> None:
        win = tk.Toplevel(parent or root)
        win.title("ODA DWG TO DXF")
        win.geometry("620x430")
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)

        converter = find_oda_file_converter()
        converter_var = tk.StringVar(value=str(converter) if converter else "ODA File Converter not found")
        version_var = tk.StringVar(value="ACAD2018")
        load_after_var = tk.BooleanVar(value=True)
        audit_var = tk.BooleanVar(value=True)
        status = tk.StringVar(value="Select one or more DWG files to convert to DXF.")

        ttk.Label(frame, text="Optional ODA DWG to DXF Converter", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, textvariable=converter_var, wraplength=560).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 10))
        options = ttk.Frame(frame)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Label(options, text="Output version").pack(side="left", padx=(0, 6))
        ttk.Combobox(
            options,
            textvariable=version_var,
            values=("ACAD2018", "ACAD2013", "ACAD2010", "ACAD2007", "ACAD2004", "ACAD2000", "ACAD12"),
            width=12,
            state="readonly",
        ).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(options, text="Audit", variable=audit_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(options, text="Load converted DXF on map", variable=load_after_var).pack(side="left")

        log_box = tk.Text(frame, height=12, wrap="word", bg="#0b1220", fg="#d8f3ff", insertbackground="#d8f3ff")
        log_box.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(8, 8))

        def log(message: str) -> None:
            log_box.insert("end", message.rstrip() + "\n")
            log_box.see("end")
            status.set(message)
            win.update_idletasks()

        def convert_selected() -> None:
            nonlocal dxf_background_shapes
            if find_oda_file_converter() is None:
                messagebox.showwarning(
                    APP_TITLE,
                    "ODA File Converter is not installed or not visible on this computer.\n\nInstall ODA File Converter, then reopen this tool.",
                    parent=win,
                )
                return
            names = filedialog.askopenfilenames(
                title="Select DWG file(s) to convert",
                filetypes=[("DWG drawing", "*.dwg *.DWG"), ("All files", "*.*")],
                parent=win,
            )
            if not names:
                return
            converted_paths: list[Path] = []
            log(f"Converting {len(names)} DWG file(s)...")
            for name in names:
                source = Path(name)
                try:
                    dxf_path = convert_dwg_to_dxf_with_oda(source, source.parent, version_var.get(), audit_var.get())
                    converted_paths.append(dxf_path)
                    log(f"OK: {source.name} -> {dxf_path.name}")
                except Exception as exc:
                    log(f"FAILED: {source.name}: {exc}")
            if converted_paths and load_after_var.get():
                try:
                    refresh_dxf_background_from_path(converted_paths[-1], show_errors=True)
                    log(f"Loaded on map: {converted_paths[-1].name}")
                except Exception as exc:
                    log(f"Converted, but map load failed: {exc}")
            if converted_paths:
                status_var.set(f"ODA converted {len(converted_paths)} DWG file(s) to DXF")
                draw_view()

        ttk.Label(frame, textvariable=status).grid(row=3, column=0, columnspan=3, sticky="ew")
        ttk.Button(frame, text="Select DWG and Convert", command=convert_selected, style="SurveyPlan.TButton").grid(row=5, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(frame, text="Fit / Extents", command=fit_view).grid(row=5, column=1, sticky="ew", padx=4)
        ttk.Button(frame, text="Close", command=win.destroy).grid(row=5, column=2, sticky="ew", padx=(4, 0))

    def show_dxf_dialog() -> None:
        """DXF background manager. Works like Border: loads directly into the plan/background view."""
        nonlocal dxf_background_shapes
        win = tk.Toplevel(root)
        win.title("CAD BACKGROUND")
        win.geometry("460x285")
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        info_var = tk.StringVar()

        def refresh_info() -> None:
            saved_name = Path(settings.dxf_background_path).name if getattr(settings, "dxf_background_path", "") else "None"
            info_var.set(f"CAD background items: {len(dxf_background_shapes)} | Saved file: {saved_name}")
            draw_view()

        def load_dxf_background() -> None:
            name = filedialog.askopenfilename(
                title="Load CAD background",
                filetypes=[("CAD drawing", "*.dxf *.dwg *.DXF *.DWG"), ("DXF drawing", "*.dxf *.DXF"), ("DWG drawing", "*.dwg *.DWG"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            refresh_dxf_background_from_path(Path(name), show_errors=True)
            refresh_info()

        def clear_dxf_background() -> None:
            nonlocal dxf_background_shapes
            dxf_background_shapes = []
            rebuild_background_cache(dxf_background_shapes, enc_background_shapes, kmz_background_shapes)
            settings.dxf_background_path = ""
            save_default_settings()
            view_mode.set("plan")
            draw_view()
            status_var.set("CAD background cleared")
            refresh_info()

        ttk.Label(frame, textvariable=info_var, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Button(frame, text="Load CAD", command=load_dxf_background).grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Clear CAD", command=clear_dxf_background).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="ODA DWG -> DXF", command=lambda: show_oda_dwg_converter_dialog(win)).grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Fit / Extents", command=fit_view).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Close", command=win.destroy).grid(row=3, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(
            frame,
            text="DXF files load directly as background layers. DWG files can be converted through optional ODA File Converter when GDAL/OGR cannot open DWG directly.",
            wraplength=390,
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        refresh_info()


    def project_background_shapes(shapes: list[MapShape]) -> list[MapShape]:
        projected_shapes: list[MapShape] = []
        for shape in shapes:
            converted_points: list[tuple[float, float]] = []
            for x_raw, y_raw in shape.points:
                x_val = float(x_raw)
                y_val = float(y_raw)
                if -180.0 <= x_val <= 180.0 and -90.0 <= y_val <= 90.0:
                    easting, northing = project_lat_lon_to_selected_xy(y_val, x_val, settings.geodetic)
                    if easting is not None and northing is not None:
                        converted_points.append((float(easting), float(northing)))
                    else:
                        converted_points.append((x_val, y_val))
                else:
                    converted_points.append((x_val, y_val))
            projected_shapes.append(MapShape(shape.kind, converted_points, text=shape.text, color=shape.color, width=shape.width, attrs=dict(shape.attrs)))
        return projected_shapes

    def load_kmz_background_file(path_obj: Path) -> None:
        """Load KMZ/KML as a plan-view background layer."""
        nonlocal kmz_background_shapes
        try:
            shapes = load_kmz_shapes(path_obj)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not load KMZ/KML background:\n{exc}")
            return
        kmz_background_shapes = project_background_shapes(shapes)
        rebuild_background_cache(dxf_background_shapes, enc_background_shapes, kmz_background_shapes)
        settings.kmz_background_path = str(path_obj)
        save_default_settings()
        follow_vessel.set(False)
        view_mode.set("plan")
        fit_view()
        status_var.set(f"Loaded KMZ/KML background: {path_obj.name} ({len(shapes)} item(s))")

    def show_kmz_dialog() -> None:
        """KMZ/KML background manager."""
        nonlocal kmz_background_shapes
        win = tk.Toplevel(root)
        win.title("KMZ / KML")
        win.geometry("540x285")
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        info_var = tk.StringVar()

        def refresh_info() -> None:
            saved_name = Path(settings.kmz_background_path).name if getattr(settings, "kmz_background_path", "") else "None"
            info_var.set(f"KMZ/KML background items: {len(kmz_background_shapes)} | Saved file: {saved_name}")
            draw_view()

        def load_kmz() -> None:
            name = filedialog.askopenfilename(
                title="Load KMZ / KML background",
                filetypes=[("Google Earth", "*.kmz *.kml"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            load_kmz_background_file(Path(name))
            refresh_info()

        def clear_kmz() -> None:
            nonlocal kmz_background_shapes
            kmz_background_shapes = []
            rebuild_background_cache(dxf_background_shapes, enc_background_shapes, kmz_background_shapes)
            settings.kmz_background_path = ""
            save_default_settings()
            view_mode.set("plan")
            draw_view()
            status_var.set("KMZ/KML background cleared")
            refresh_info()

        ttk.Label(frame, textvariable=info_var, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Button(frame, text="Load KMZ/KML Background", command=load_kmz).grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Export KMZ / Select Files", command=export_current_kmz).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Clear KMZ/KML", command=clear_kmz).grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Fit / Extents", command=fit_view).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Close", command=win.destroy).grid(row=3, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(frame, text="Loads Google Earth KMZ/KML as a background layer and exports current or selected survey/CAD/background files to KMZ.", wraplength=440).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        refresh_info()


    def show_enc_summary() -> None:
        win = tk.Toplevel(root)
        win.title("ENC LOADED FEATURE SUMMARY")
        win.geometry("720x520")
        win.minsize(520, 320)
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        text_box = tk.Text(frame, wrap="none")
        text_box.pack(side="left", fill="both", expand=True)
        scroll_y = ttk.Scrollbar(frame, orient="vertical", command=text_box.yview)
        scroll_y.pack(side="right", fill="y")
        text_box.configure(yscrollcommand=scroll_y.set)
        counts: dict[str, int] = {}
        colors: dict[str, set[str]] = {}
        for shape in enc_background_shapes:
            cls = str(shape.attrs.get("OBJCLASS") or shape.attrs.get("LAYER") or "UNKNOWN")
            counts[cls] = counts.get(cls, 0) + 1
            colors.setdefault(cls, set()).add(shape.color)
        text_box.insert("end", f"ENC shapes loaded: {len(enc_background_shapes)}\n")
        text_box.insert("end", f"Object classes: {len(counts)}\n\n")
        for cls in sorted(counts):
            text_box.insert("end", f"{cls}: {counts[cls]}  colours={', '.join(sorted(colors.get(cls, [])))}\n")
        text_box.insert("end", "\nUse right-click > ENC Feature Details near a feature to see its full attributes.\n")
        text_box.configure(state="disabled")


    enc_display_options: dict[str, Any] = {
        "symbols": "Traditional",
        "boundaries": "Symbolized",
        "units": "Meters",
        "scheme": "S52",
        "shallow_contour": tk.StringVar(value="10.00"),
        "safety_contour": tk.StringVar(value="30.00"),
        "deep_contour": tk.StringVar(value="60.00"),
        "safety_depth": tk.StringVar(value="30.00"),
        "tide": tk.StringVar(value="0.0"),
        "keel_depth": tk.StringVar(value="0.00"),
        "safety_margin": tk.StringVar(value="1.00"),
        "ship_height": tk.StringVar(value="0.00"),
        "show_soundings": tk.BooleanVar(value=True),
        "two_depth_shades": tk.BooleanVar(value=True),
        "scale_minimum": tk.BooleanVar(value=True),
        "full_sector_lights": tk.BooleanVar(value=True),
        "information_boxes": tk.BooleanVar(value=True),
        "load_chart_updates": tk.BooleanVar(value=True),
        "shallow_pattern": tk.BooleanVar(value=False),
        "visual_quality": tk.BooleanVar(value=False),
        "hypack_sounding_display": tk.BooleanVar(value=False),
        "isolated_danger": tk.BooleanVar(value=False),
        "hide_extra_contours": tk.BooleanVar(value=False),
        "adjust_contours_to_tide": tk.BooleanVar(value=False),
        "set_shallow_to_keel": tk.BooleanVar(value=False),
    }

    def show_s57_display_options() -> None:
        win = tk.Toplevel(root)
        win.title("S57 Display Options")
        win.geometry("820x620")
        win.minsize(680, 520)
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)

        general = ttk.Frame(notebook, padding=16)
        contours = ttk.Frame(notebook, padding=16)
        notebook.add(general, text="General Display")
        notebook.add(contours, text="Contour Planning")

        symbol_var = tk.StringVar(value=str(enc_display_options.get("symbols", "Traditional")))
        boundary_var = tk.StringVar(value=str(enc_display_options.get("boundaries", "Symbolized")))
        units_var = tk.StringVar(value=str(enc_display_options.get("units", "Meters")))
        scheme_var = tk.StringVar(value=str(enc_display_options.get("scheme", "S52")))

        ttk.Label(general, text="Symbols").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Combobox(general, textvariable=symbol_var, values=["Traditional", "Simplified"], state="readonly", width=22).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Label(general, text="Units").grid(row=0, column=2, sticky="w", padx=18, pady=6)
        ttk.Combobox(general, textvariable=units_var, values=["Meters", "Feet", "Fathoms"], state="readonly", width=18).grid(row=0, column=3, sticky="ew", padx=6, pady=6)
        ttk.Label(general, text="Boundaries").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        ttk.Combobox(general, textvariable=boundary_var, values=["Symbolized", "Plain"], state="readonly", width=22).grid(row=1, column=1, sticky="ew", padx=6, pady=6)
        ttk.Label(general, text="Scheme").grid(row=1, column=2, sticky="w", padx=18, pady=6)
        ttk.Combobox(general, textvariable=scheme_var, values=["S52", "Light Chart"], state="readonly", width=18).grid(row=1, column=3, sticky="ew", padx=6, pady=6)

        for row, (label, key) in enumerate([
            ("Shallow Contour", "shallow_contour"),
            ("Safety Contour", "safety_contour"),
            ("Deep Contour", "deep_contour"),
            ("Safety Depth", "safety_depth"),
        ], start=2):
            ttk.Label(general, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=5)
            ttk.Entry(general, textvariable=enc_display_options[key], width=12).grid(row=row, column=1, sticky="w", padx=6, pady=5)

        options = ttk.LabelFrame(general, text="Options", padding=8)
        options.grid(row=6, column=0, columnspan=2, sticky="nsew", padx=6, pady=(12, 6))
        for idx, (label, key) in enumerate([
            ("Scale Minimum", "scale_minimum"),
            ("Two Depth Shades", "two_depth_shades"),
            ("Shallow Pattern", "shallow_pattern"),
            ("Full Sector Lights", "full_sector_lights"),
            ("Show Soundings", "show_soundings"),
            ("Visual Quality of Data", "visual_quality"),
            ("Information Boxes", "information_boxes"),
            ("Load Chart Updates", "load_chart_updates"),
            ("HYPACK Sounding Display", "hypack_sounding_display"),
            ("Isolated Danger in Shallow Water", "isolated_danger"),
            ("Hide Extra Contours", "hide_extra_contours"),
        ]):
            ttk.Checkbutton(options, text=label, variable=enc_display_options[key]).grid(row=idx, column=0, sticky="w", pady=2)

        text_groups = ttk.LabelFrame(general, text="Text Display Groups", padding=8)
        text_groups.grid(row=6, column=2, columnspan=2, sticky="nsew", padx=6, pady=(12, 6))
        for idx, label in enumerate([
            "1 Important e.g. Bridge clearances",
            "21 Names e.g. Buoys, Beacons & Daymarks",
            "23 Light Description",
            "24 Chart Notes e.g. INFORM or TXTDSC",
            "25 Nature of Seabed",
            "26 Geographic Names",
            "27 Magnetic Variation or Swept Depth",
            "28 Height of Land Feature",
            "29 Berthing Number",
            "31 Tidal Streams, National Language",
            "50..69 Mariners Text",
            "99 Field Notes",
        ]):
            var = tk.BooleanVar(value=idx < 11)
            ttk.Checkbutton(text_groups, text=label, variable=var).grid(row=idx, column=0, sticky="w", pady=2)

        contour_box = ttk.LabelFrame(contours, text="Contour Planning", padding=14)
        contour_box.pack(fill="both", expand=True)
        for row, (label, key, note) in enumerate([
            ("Tide", "tide", "m   (negative above gauge)"),
            ("Keel Depth", "keel_depth", "m"),
            ("Safety Margin", "safety_margin", "m"),
            ("Ship Height", "ship_height", "m   (above water line)"),
        ]):
            ttk.Label(contour_box, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=8)
            ttk.Entry(contour_box, textvariable=enc_display_options[key], width=12).grid(row=row, column=1, sticky="w", padx=8, pady=8)
            ttk.Label(contour_box, text=note).grid(row=row, column=2, sticky="w", padx=8, pady=8)
        ttk.Checkbutton(contour_box, text="Set Shallow Contour to Keel Depth and Safety Contour to Keel Depth plus Safety Margin", variable=enc_display_options["set_shallow_to_keel"]).grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=8)
        ttk.Checkbutton(contour_box, text="Adjust Contours to Tide", variable=enc_display_options["adjust_contours_to_tide"]).grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=8)
        table = ttk.LabelFrame(contour_box, text="Contour Values", padding=8)
        table.grid(row=7, column=0, columnspan=3, sticky="ew", padx=10, pady=(16, 8))
        ttk.Label(table, text="").grid(row=0, column=0, padx=12)
        ttk.Label(table, text="User Defined").grid(row=0, column=1, padx=18)
        ttk.Label(table, text="Adjusted").grid(row=0, column=2, padx=18)
        for row, (label, key) in enumerate([("Shallow Contour", "shallow_contour"), ("Safety Contour", "safety_contour"), ("Deep Contour", "deep_contour")], start=1):
            ttk.Label(table, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=4)
            ttk.Label(table, textvariable=enc_display_options[key]).grid(row=row, column=1, padx=18, pady=4)
            ttk.Label(table, textvariable=enc_display_options[key]).grid(row=row, column=2, padx=18, pady=4)

        def apply_options(close: bool = False) -> None:
            enc_display_options["symbols"] = symbol_var.get()
            enc_display_options["boundaries"] = boundary_var.get()
            enc_display_options["units"] = units_var.get()
            enc_display_options["scheme"] = scheme_var.get()
            draw_view()
            status_var.set("S57 display options applied")
            if close:
                win.destroy()

        buttons = ttk.Frame(frame, padding=(0, 10, 0, 0))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Apply", command=lambda: apply_options(False)).pack(side="left", padx=6)
        ttk.Button(buttons, text="OK", command=lambda: apply_options(True)).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="left", padx=6)


    def load_enc_background_file(path_obj: Path) -> None:
        """Load ENC/S-57 as a plan-view background layer."""
        nonlocal enc_background_shapes
        try:
            shapes = load_enc_s57_shapes(path_obj)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not load ENC/S-57 background:\n{exc}")
            return
        enc_background_shapes = project_background_shapes(shapes)
        rebuild_background_cache(dxf_background_shapes, enc_background_shapes, kmz_background_shapes)
        settings.enc_background_path = str(path_obj)
        save_default_settings()
        follow_vessel.set(False)
        view_mode.set("plan")
        fit_view()
        status_var.set(f"Loaded ENC/S-57 background: {path_obj.name} ({len(shapes)} item(s))")

    def show_enc_dialog() -> None:
        return _acquisition.new_window.show_enc_editor()
        """ENC/S-57 background manager."""
        nonlocal enc_background_shapes
        win = tk.Toplevel(root)
        win.title("ENC / S-57 BACKGROUND")
        win.geometry("500x240")
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        info_var = tk.StringVar()

        def refresh_info() -> None:
            saved_name = Path(settings.enc_background_path).name if getattr(settings, "enc_background_path", "") else "None"
            info_var.set(f"ENC/S-57 background items: {len(enc_background_shapes)} | Saved file: {saved_name}")
            draw_view()

        def load_enc() -> None:
            name = filedialog.askopenfilename(
                title="Load ENC / S-57 background",
                filetypes=[("ENC / S-57", "*.000 *.001 *.002 *.003 *.004 *.005 *.006 *.007 *.s57"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            load_enc_background_file(Path(name))
            refresh_info()

        def clear_enc() -> None:
            nonlocal enc_background_shapes
            enc_background_shapes = []
            rebuild_background_cache(dxf_background_shapes, enc_background_shapes, kmz_background_shapes)
            settings.enc_background_path = ""
            save_default_settings()
            status_var.set("ENC/S-57 background cleared")
            refresh_info()

        ttk.Label(frame, textvariable=info_var, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Button(frame, text="Load ENC / S-57", command=load_enc).grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Clear ENC", command=clear_enc).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Feature Summary", command=show_enc_summary).grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(frame, text="Close", command=win.destroy).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(
            frame,
            text="Loads ENC/S-57 feature-object layers as a plan-view background layer. Requires GDAL/OGR Python bindings for S-57 decoding.",
            wraplength=450,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        refresh_info()

    def show_geotiff_background_dialog() -> None:
        messagebox.showinfo("GeoTIFF — unavailable", "Georeferenced raster rendering is not implemented in HYDROGEOMATICS. Selecting a TIFF would not draw it. Use DXF, KML/KMZ, ENC with GDAL, or processed XYZ/CSV backgrounds.", parent=root)

    def show_background_unload_dialog():
        return _acquisition.new_window.show_background_manager()

    def show_background_upload_dialog() -> None:
        win = tk.Toplevel(root)
        win.title("BACKGROUND UPLOAD")
        win.geometry("520x360")
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Background Upload", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            frame,
            text="Use the explorer list to check or uncheck background, line, border, sounding, KTD, DXF/DWG, KMZ/KML, and ENC/S-57 files on the navigation map.",
            wraplength=470,
        ).pack(anchor="w", fill="x", pady=(0, 10))
        ttk.Button(frame, text="Explorer Load / Unload", command=show_background_file_explorer, style="SurveyPlan.TButton").pack(fill="x", pady=3)
        ttk.Button(frame, text="Unload Background", command=show_background_unload_dialog, style="SurveyPlan.TButton").pack(fill="x", pady=3)
        ttk.Button(frame, text="Fit / Extents", command=fit_view, style="SurveyPlan.TButton").pack(fill="x", pady=3)
        ttk.Button(frame, text="Close", command=win.destroy).pack(fill="x", pady=(12, 0))

    def xy_to_kml_lonlat(x_value: float, y_value: float) -> tuple[float, float]:
        """Convert displayed grid coordinates to lon/lat for Google Earth export."""
        geo = settings.geodetic
        if not getattr(geo, "use_projection", True):
            return float(x_value), float(y_value)
        zone = selected_utm_zone(geo, None) or 42
        hemi = "S" if float(y_value) >= 10000000.0 else "N"
        try:
            lat, lon = GT_utm_to_latlon(float(x_value), float(y_value), zone, hemi)
            return lon, lat
        except Exception:
            # Last-resort fallback keeps the export process from crashing.
            return float(x_value), float(y_value)

    def kml_linestring(name: str, points_xy: list[tuple[float, float]], color: str = "ff00ffff") -> Optional[str]:
        if len(points_xy) < 2:
            return None
        coords = []
        for x_value, y_value in points_xy:
            lon, lat = xy_to_kml_lonlat(float(x_value), float(y_value))
            coords.append(f"{lon:.10f},{lat:.10f},0")
        return f"""
<Placemark><name>{_kml_escape(name)}</name>
<Style><LineStyle><color>{color}</color><width>2</width></LineStyle></Style>
<LineString><tessellate>1</tessellate><coordinates>{' '.join(coords)}</coordinates></LineString>
</Placemark>"""

    def kml_point(name: str, x_value: float, y_value: float, description: str = "") -> str:
        lon, lat = xy_to_kml_lonlat(float(x_value), float(y_value))
        return f"""
<Placemark><name>{_kml_escape(name)}</name><description>{_kml_escape(description)}</description>
<Point><coordinates>{lon:.10f},{lat:.10f},0</coordinates></Point></Placemark>"""

    def export_current_kmz() -> None:
        """Export current survey/map data plus optional selected files to KMZ."""
        name = filedialog.asksaveasfilename(
            title="Export KMZ for Google Earth",
            defaultextension=".kmz",
            filetypes=[("Google Earth KMZ", "*.kmz"), ("All files", "*.*")],
        )
        if not name:
            return
        placemarks: list[str] = []
        exported_points = 0
        extra_files = filedialog.askopenfilenames(
            title="Optional: select RAW / EDIT / SORT / XYZ / BRD / LNW / DXF / DWG / KML / KMZ files to include",
            filetypes=[
                ("KMZ export sources", "*.raw *.RAW *.log *.1 *.2 *.3 *.4 *.5 *.xyz *.XYZ *.csv *.txt *.brd *.lnw *.dxf *.kml *.kmz"),
                ("Recorded RAW", "*.raw *.RAW"),
                ("Edited files", "*.log *.1 *.2 *.3 *.4 *.5"),
                ("Sorted XYZ", "*.xyz *.XYZ *.csv *.txt"),
                ("Border / lines", "*.brd *.lnw"),
                ("CAD / Earth", "*.dxf *.kml *.kmz"),
                ("All files", "*.*"),
            ],
        )

        for file_name in extra_files:
            source_path = Path(file_name)
            suffix = source_path.suffix.lower()
            try:
                if suffix == ".brd":
                    pts = load_hypack_brd_points(source_path)
                    if len(pts) >= 2:
                        if len(pts) >= 3 and pts[0] != pts[-1]:
                            pts = pts + [pts[0]]
                        item = kml_linestring(f"Border {source_path.stem}", pts, "ff00ffff")
                        if item:
                            placemarks.append(item)
                elif suffix == ".lnw":
                    for line in load_hypack_lnw(source_path):
                        item = kml_linestring(
                            f"{source_path.stem} {line.name}",
                            [(line.start_x, line.start_y), (line.end_x, line.end_y)],
                            "ff00ff00",
                        )
                        if item:
                            placemarks.append(item)
                elif suffix == ".dxf":
                    for index, shape in enumerate(load_dxf_shapes(source_path), 1):
                        pts = [(float(x), float(y)) for x, y in shape.points]
                        item = kml_linestring(f"DXF {source_path.stem} {index}", pts, "ffffaa00")
                        if item:
                            placemarks.append(item)
                elif suffix in {".kml", ".kmz"}:
                    for index, shape in enumerate(project_background_shapes(load_kmz_shapes(source_path)), 1):
                        pts = [(float(x), float(y)) for x, y in shape.points]
                        if shape.kind == "point" and pts:
                            placemarks.append(kml_point(f"{source_path.stem} {index} {shape.text}", pts[0][0], pts[0][1]))
                        else:
                            item = kml_linestring(f"{source_path.stem} {index} {shape.text}", pts, "ffff6600")
                            if item:
                                placemarks.append(item)
                else:
                    for index, point in enumerate(load_points_from_any(source_path), 1):
                        ensure_projected_xy(point, settings.geodetic)
                        if point.x is not None and point.y is not None:
                            desc = f"Depth: {format_number(point.corrected_depth)} | Raw: {format_number(point.raw_depth)} | Source: {source_path.name}"
                            placemarks.append(kml_point(f"{source_path.stem} {index}", float(point.x), float(point.y), desc))
                            exported_points += 1
            except Exception as exc:
                messagebox.showwarning(APP_TITLE, f"Skipped {source_path.name}:\n{exc}")

        for index, point in enumerate(model.points, 1):
            ensure_projected_xy(point, settings.geodetic)
            if point.x is None or point.y is None:
                continue
            desc = f"Depth: {format_number(point.corrected_depth)} | Raw: {format_number(point.raw_depth)} | Source: {point.source}"
            placemarks.append(kml_point(f"Current sounding {index}", float(point.x), float(point.y), desc))
            exported_points += 1

        for index, point in enumerate(project_background_points, 1):
            if point.x is None or point.y is None:
                continue
            desc = f"Background depth: {format_number(point.corrected_depth)} | Raw: {format_number(point.raw_depth)} | Source: {point.source}"
            placemarks.append(kml_point(f"Background sounding {index}", float(point.x), float(point.y), desc))

        if len(border_points) >= 2:
            pts = list(border_points)
            if len(pts) >= 3 and pts[0] != pts[-1]:
                pts.append(pts[0])
            item = kml_linestring("Current Border", pts, "ff00ffff")
            if item:
                placemarks.append(item)

        for line in runlines.lines:
            item = kml_linestring(f"Runline {line.name}", [(line.start_x, line.start_y), (line.end_x, line.end_y)], "ff00ff00")
            if item:
                placemarks.append(item)

        for prefix, shapes, color in [
            ("CAD", dxf_background_shapes, "ffffaa00"),
            ("ENC", enc_background_shapes, "ffff0000"),
            ("KMZ", kmz_background_shapes, "ffff6600"),
        ]:
            for index, shape in enumerate(shapes, 1):
                pts = [(float(x), float(y)) for x, y in shape.points]
                if shape.kind == "point" and pts:
                    placemarks.append(kml_point(f"{prefix} {index} {shape.text}", pts[0][0], pts[0][1]))
                else:
                    item = kml_linestring(f"{prefix} {index} {shape.text}", pts, color)
                    if item:
                        placemarks.append(item)

        if not placemarks:
            messagebox.showinfo(APP_TITLE, "No current data or selected files are available to export.")
            return
        try:
            write_kmz(Path(name), placemarks)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not export KMZ:\n{exc}")
            return
        status_var.set(f"Exported KMZ: {Path(name).name} | selected files {len(extra_files)} | points {exported_points} | placemarks {len(placemarks)}")

    def show_line_editor():
        return _acquisition.new_window.show_line_editor_panel()

    def set_view(mode: str) -> None:
        view_mode.set(mode)
        draw_view()

    def set_follow_vessel(enabled: bool) -> None:
        follow_vessel.set(enabled)
        view_mode.set("plan")
        if enabled:
            settings.nav_view_scale = nav_scale_var.get() or "1:200"
            try:
                save_default_settings()
            except Exception:
                pass
        draw_view()
        status_var.set("Follow Vessel enabled" if enabled else "Follow Vessel disabled")

    def enable_realtime_navigation_follow() -> None:
        if not follow_vessel.get():
            follow_vessel.set(True)
        view_mode.set("plan")
        settings.nav_view_scale = nav_scale_var.get() or "1:200"
        draw_view()

    def zoom_view(factor: float, center_screen: Optional[tuple[float, float]] = None) -> None:
        view_mode.set("plan")
        view_state["zoom_window"] = False
        view_state["is_panning"] = False
        follow_vessel.set(False)
        if nav_scale_var.get().lower() != "fit":
            nav_scale_var.set("Fit")
        plot_left = float(view_state.get("last_plot_left", 32.0))
        plot_right = float(view_state.get("last_plot_right", max(canvas.winfo_width() - 32, 33)))
        plot_top = float(view_state.get("last_plot_top", 32.0))
        plot_bottom = float(view_state.get("last_plot_bottom", max(canvas.winfo_height() - 32, 33)))
        if center_screen is None:
            center_screen = ((plot_left + plot_right) / 2.0, (plot_top + plot_bottom) / 2.0)
        ratio_x = max(0.0, min(1.0, (float(center_screen[0]) - plot_left) / max(plot_right - plot_left, 1.0)))
        ratio_y = max(0.0, min(1.0, (float(center_screen[1]) - plot_top) / max(plot_bottom - plot_top, 1.0)))
        focus_x, focus_y = screen_to_world(center_screen[0], center_screen[1])
        old_zoom = max(float(view_state["zoom"]), 0.05)
        new_zoom = max(0.05, min(500.0, old_zoom * factor))
        effective = max(new_zoom / old_zoom, 1e-9)
        new_span_x = max(float(view_state.get("last_span_x", 1.0)) / effective, 1e-9)
        new_span_y = max(float(view_state.get("last_span_y", 1.0)) / effective, 1e-9)
        new_center_x = float(focus_x) - (ratio_x - 0.5) * new_span_x
        new_center_y = float(focus_y) + (ratio_y - 0.5) * new_span_y
        view_state["zoom"] = new_zoom
        view_state["pan_x"] = new_center_x - float(view_state.get("full_center_x", new_center_x))
        view_state["pan_y"] = new_center_y - float(view_state.get("full_center_y", new_center_y))
        draw_view()
        status_var.set(f"Plan zoom: {float(view_state['zoom']):.2f}x")

    def pan_view(dx_fraction: float, dy_fraction: float) -> None:
        view_mode.set("plan")
        follow_vessel.set(False)
        view_state["is_panning"] = False
        view_state["pan_x"] = float(view_state["pan_x"]) + float(view_state.get("last_span_x", 1.0)) * dx_fraction
        view_state["pan_y"] = float(view_state["pan_y"]) + float(view_state.get("last_span_y", 1.0)) * dy_fraction
        draw_view()
        status_var.set("Pan view")

    def fit_view() -> None:
        view_mode.set("plan")
        view_state["zoom_window"] = False
        view_state["zoom"] = 1.0
        view_state["pan_x"] = 0.0
        view_state["pan_y"] = 0.0
        draw_view()
        status_var.set("Plan view reset to extents")

    def enable_zoom_window() -> None:
        view_mode.set("plan")
        view_state["zoom_window"] = True
        view_state["zoom_start"] = None
        if view_state.get("zoom_rect") is not None:
            try:
                canvas.delete(view_state["zoom_rect"])
            except Exception:
                pass
            view_state["zoom_rect"] = None
        status_var.set("Zoom Window: drag a rectangle on the navigation screen.")

    def screen_to_world(screen_x: float, screen_y: float) -> tuple[float, float]:
        plot_left = float(view_state.get("last_plot_left", 32.0))
        plot_right = float(view_state.get("last_plot_right", max(canvas.winfo_width() - 32, 33)))
        plot_top = float(view_state.get("last_plot_top", 32.0))
        plot_bottom = float(view_state.get("last_plot_bottom", max(canvas.winfo_height() - 32, 33)))
        width_span = max(plot_right - plot_left, 1.0)
        height_span = max(plot_bottom - plot_top, 1.0)
        ratio_x = max(0.0, min(1.0, (float(screen_x) - plot_left) / width_span))
        ratio_y = max(0.0, min(1.0, (float(screen_y) - plot_top) / height_span))
        world_x = float(view_state.get("last_min_x", 0.0)) + ratio_x * float(view_state.get("last_span_x", 1.0))
        world_y = float(view_state.get("last_max_y", 1.0)) - ratio_y * float(view_state.get("last_span_y", 1.0))
        return world_x, world_y

    def on_canvas_mouse_move(event: Any) -> None:
        if view_mode.get() != "plan":
            return
        world_x, world_y = screen_to_world(event.x, event.y)
        view_state["last_cursor_world"] = (world_x, world_y)
        try:
            lon_value, lat_value = xy_to_kml_lonlat(float(world_x), float(world_y))
            status_var.set(f"E {world_x:.3f}  N {world_y:.3f}  Lat {lat_value:.7f}  Lon {lon_value:.7f}")
        except Exception:
            status_var.set(f"E {world_x:.3f}  N {world_y:.3f}")

    def point_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
        dx = bx - ax
        dy = by - ay
        if abs(dx) < 1e-12 and abs(dy) < 1e-12:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    def nearest_enc_shape(world_x: float, world_y: float) -> Optional[MapShape]:
        if not enc_background_shapes:
            return None
        tolerance = max(float(view_state.get("last_span_x", 1.0)), float(view_state.get("last_span_y", 1.0))) * 0.025
        best_shape: Optional[MapShape] = None
        best_distance = float("inf")
        search_bbox = (world_x - tolerance, world_y - tolerance, world_x + tolerance, world_y + tolerance)
        for shape in enc_background_shapes:
            bbox = cached_shape_bbox(shape)
            if bbox is not None and not bbox_intersects(bbox, search_bbox):
                continue
            pts = [(float(x), float(y)) for x, y in shape.points]
            if not pts:
                continue
            if shape.kind == "point" or len(pts) == 1:
                dist = min(math.hypot(world_x - x, world_y - y) for x, y in pts)
            else:
                step = max(1, len(pts) // 250)
                reduced = pts[::step]
                if reduced[-1] != pts[-1]:
                    reduced.append(pts[-1])
                dist = min(
                    point_segment_distance(world_x, world_y, ax, ay, bx, by)
                    for (ax, ay), (bx, by) in zip(reduced, reduced[1:])
                )
            if dist < best_distance:
                best_distance = dist
                best_shape = shape
        return best_shape if best_distance <= tolerance else None

    def show_enc_feature_details() -> None:
        cursor = view_state.get("last_cursor_world")
        if cursor is None:
            messagebox.showinfo(APP_TITLE, "Right-click near an ENC feature first.", parent=root)
            return
        shape = nearest_enc_shape(float(cursor[0]), float(cursor[1]))
        if shape is None:
            messagebox.showinfo(APP_TITLE, "No ENC feature found near the cursor.", parent=root)
            return
        win = tk.Toplevel(root)
        win.title("ENC FEATURE DETAILS")
        win.geometry("620x520")
        win.minsize(420, 320)
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        text_box = tk.Text(frame, wrap="word")
        text_box.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text_box.yview)
        scroll.pack(side="right", fill="y")
        text_box.configure(yscrollcommand=scroll.set)
        text_box.insert("end", full_feature_details(shape))
        text_box.configure(state="disabled")

    def apply_zoom_window(x0: float, y0: float, x1: float, y1: float) -> None:
        wx0, wy0 = screen_to_world(x0, y0)
        wx1, wy1 = screen_to_world(x1, y1)
        target_span_x = abs(wx1 - wx0)
        target_span_y = abs(wy1 - wy0)
        if target_span_x <= 1e-9 or target_span_y <= 1e-9:
            return
        full_span_x = max(float(view_state.get("full_span_x", target_span_x)), 1e-9)
        full_span_y = max(float(view_state.get("full_span_y", target_span_y)), 1e-9)
        view_state["zoom"] = max(0.2, min(50.0, min(full_span_x / target_span_x, full_span_y / target_span_y)))
        view_state["pan_x"] = ((wx0 + wx1) / 2.0) - float(view_state.get("full_center_x", 0.0))
        view_state["pan_y"] = ((wy0 + wy1) / 2.0) - float(view_state.get("full_center_y", 0.0))
        follow_vessel.set(False)
        draw_view()
        status_var.set(f"Zoom Window applied: {float(view_state['zoom']):.2f}x")

    def on_canvas_mouse_down(event: Any) -> None:
        if view_mode.get() == "plan":
            border_callback = view_state.get("border_pick_callback")
            if border_callback is not None:
                world_x, world_y = screen_to_world(event.x, event.y)
                border_callback(world_x, world_y)
                return
            pick_callback = view_state.get("line_pick_callback")
            if pick_callback is not None:
                world_x, world_y = screen_to_world(event.x, event.y)
                pick_callback(world_x, world_y)
                return
            if view_state.get("zoom_window"):
                view_state["zoom_start"] = (event.x, event.y)
                view_state["drag"] = None
                if view_state.get("zoom_rect") is not None:
                    try:
                        canvas.delete(view_state["zoom_rect"])
                    except Exception:
                        pass
                view_state["zoom_rect"] = canvas.create_rectangle(
                    event.x, event.y, event.x, event.y, outline="#ffcc66", width=2, dash=(5, 3)
                )
            else:
                view_state["drag"] = (event.x, event.y)

    def on_canvas_mouse_drag(event: Any) -> None:
        if view_mode.get() != "plan":
            return
        if view_state.get("zoom_window") and view_state.get("zoom_start") is not None:
            start_x, start_y = view_state["zoom_start"]
            rect_id = view_state.get("zoom_rect")
            if rect_id is not None:
                canvas.coords(rect_id, start_x, start_y, event.x, event.y)
            return
        if view_state["drag"] is None:
            return
        follow_vessel.set(False)
        view_state["is_panning"] = True
        previous_x, previous_y = view_state["drag"]
        dx = event.x - previous_x
        dy = event.y - previous_y
        view_state["drag"] = (event.x, event.y)
        plot_width = max(float(view_state["last_plot_width"]), 1.0)
        plot_height = max(float(view_state["last_plot_height"]), 1.0)
        view_state["pan_x"] = float(view_state["pan_x"]) - dx * float(view_state["last_span_x"]) / plot_width
        view_state["pan_y"] = float(view_state["pan_y"]) + dy * float(view_state["last_span_y"]) / plot_height
        request_draw_view()

    def on_canvas_mouse_up(_event: Any) -> None:
        if view_mode.get() == "plan" and view_state.get("zoom_window") and view_state.get("zoom_start") is not None:
            start_x, start_y = view_state["zoom_start"]
            rect_id = view_state.get("zoom_rect")
            if rect_id is not None:
                try:
                    canvas.delete(rect_id)
                except Exception:
                    pass
            view_state["zoom_rect"] = None
            view_state["zoom_start"] = None
            view_state["zoom_window"] = False
            if abs(_event.x - start_x) >= 8 and abs(_event.y - start_y) >= 8:
                apply_zoom_window(start_x, start_y, _event.x, _event.y)
            else:
                status_var.set("Zoom Window cancelled: selection was too small.")
            return
        view_state["drag"] = None
        if view_state.get("is_panning"):
            view_state["is_panning"] = False
            draw_view()

    def on_canvas_mouse_wheel(event: Any) -> None:
        if view_mode.get() != "plan":
            return
        view_state["zoom_window"] = False
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            zoom_view(1.25, (event.x, event.y))
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            zoom_view(0.8, (event.x, event.y))

    def show_sb_edit() -> None:
        win = tk.Toplevel(root)
        win.title("SB EDIT")
        win.geometry("1180x720")
        win.minsize(840, 520)
        win.resizable(True, True)
        try:
            win.state("zoomed")
        except Exception:
            try:
                win.attributes("-zoomed", True)
            except Exception:
                pass
        win.after(100, lambda: win.state("zoomed") if str(win.state()) != "zoomed" else None)

        top = ttk.Frame(win, padding=8)
        top.pack(fill="x")
        status = tk.StringVar(value="Ready")
        summary = tk.StringVar(value="")
        edit_mode = tk.StringVar(value="Inspect")
        spike_threshold = tk.StringVar(value="0.75")
        smooth_window = tk.StringVar(value="5")
        bin_size = tk.StringVar(value="25.0")
        tide_adjustment = tk.StringVar(value=f"{model.tide:.3f}")
        draft_adjustment = tk.StringVar(value=f"{model.draft:.3f}")
        cursor_status = tk.StringVar(value="Graphical Linear Editor: load data, then click a sounding point to inspect/edit. Right-click a point for Accept/Reject/Smooth.")
        params_status = tk.StringVar(value="Read Parameters before loading RAW/NMEA files.")
        params_ready = tk.BooleanVar(value=False)
        sb_load_params = {
            "gnss": {"GGA", "RMC"},
            "echo": {"DPT", "DBT"},
            "allow_gnss_non_nmea": False,
            "allow_echo_non_nmea": True,
            "allow_plain_depth": False,
            "max_plain_depth": 500.0,
            "spike_limit": 0.75,
            "step_gate": 1.50,
        }
        selected_point: dict[str, Optional[SurveyPoint]] = {"point": None}
        edit_bins: list[dict[str, float | int]] = []
        point_cache: list[tuple[float, float, SurveyPoint]] = []
        graph_view_start = tk.DoubleVar(value=0.0)
        graph_view_end = tk.DoubleVar(value=100.0)
        graph_tool = tk.StringVar(value="Inspect")
        graph_drag: dict[str, object] = {"start": None, "rect": None}
        graph_scroll_guard = {"updating": False}
        table_load_job: dict[str, Optional[str]] = {"after_id": None}
        table_page_start = tk.IntVar(value=0)
        table_page_size = tk.StringVar(value="2500")
        table_page_status = tk.StringVar(value="Rows: none")
        graph_world: dict[str, float] = {
            "min_chain": 0.0,
            "max_chain": 1.0,
            "left": 62.0,
            "top": 24.0,
            "plot_w": 1.0,
            "plot_h": 1.0,
        }

        body = ttk.PanedWindow(win, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left = ttk.Frame(body, padding=6)
        right = ttk.PanedWindow(body, orient="vertical")
        body.add(left, weight=1)
        body.add(right, weight=4)

        ttk.Label(left, text="SB EDIT", font=("Segoe UI", 14, "bold")).pack(fill="x", pady=(0, 4))
        ttk.Label(left, textvariable=summary, wraplength=330, font=("Segoe UI", 11, "bold")).pack(fill="x", pady=(0, 8))
        edit_controls = ttk.LabelFrame(left, text="Linear Edit Tools", padding=6)
        edit_controls.pack(fill="x", pady=(0, 8))
        ttk.Label(edit_controls, text="Mode").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            edit_controls,
            textvariable=edit_mode,
            values=["Inspect", "Reject", "Accept", "Smooth Local"],
            width=14,
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        ttk.Label(edit_controls, text="Spike m").grid(row=1, column=0, sticky="w")
        ttk.Entry(edit_controls, textvariable=spike_threshold, width=8).grid(row=1, column=1, sticky="ew", padx=4, pady=2)
        ttk.Label(edit_controls, text="Smooth").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(edit_controls, from_=3, to=31, increment=2, textvariable=smooth_window, width=8).grid(row=2, column=1, sticky="ew", padx=4, pady=2)
        ttk.Label(edit_controls, text="Bin m").grid(row=3, column=0, sticky="w")
        ttk.Entry(edit_controls, textvariable=bin_size, width=8).grid(row=3, column=1, sticky="ew", padx=4, pady=2)
        ttk.Label(edit_controls, text="Tide m").grid(row=4, column=0, sticky="w")
        ttk.Entry(edit_controls, textvariable=tide_adjustment, width=8).grid(row=4, column=1, sticky="ew", padx=4, pady=2)
        ttk.Label(edit_controls, text="Draft m").grid(row=5, column=0, sticky="w")
        ttk.Entry(edit_controls, textvariable=draft_adjustment, width=8).grid(row=5, column=1, sticky="ew", padx=4, pady=2)
        edit_controls.columnconfigure(1, weight=1)
        ttk.Label(left, textvariable=params_status, wraplength=330, foreground="#7c2d12").pack(fill="x", pady=(0, 8))
        info = tk.Text(left, width=42, wrap="word", bg="#f7f8f3", fg="#111111", height=12)
        info.pack(fill="both", expand=True)

        plot_frame = ttk.LabelFrame(right, text="Graphical Linear Editor - Click Soundings to Edit", padding=6)
        table_frame = ttk.Frame(right)
        right.add(plot_frame, weight=3)
        right.add(table_frame, weight=2)

        plot_header = ttk.Frame(plot_frame)
        plot_header.pack(fill="x", pady=(0, 4))
        ttk.Label(
            plot_header,
            textvariable=cursor_status,
            foreground="#1f2937",
            wraplength=900,
        ).pack(side="left", fill="x", expand=True)
        ttk.Label(
            plot_header,
            text="Green=Accepted  Red=Rejected  Yellow=Selected  Cyan=Binned",
            foreground="#475569",
        ).pack(side="right", padx=(8, 0))
        plot_tools = ttk.Frame(plot_frame)
        plot_tools.pack(fill="x", pady=(0, 4))
        for label, tool_name in [("Pan", "Pan"), ("Zoom Window", "Zoom Window"), ("Inspect/Edit", "Inspect")]:
            ttk.Button(plot_tools, text=label, command=lambda name=tool_name: set_graph_tool(name)).pack(side="left", padx=2)
        ttk.Button(plot_tools, text="Zoom In", command=lambda: zoom_graph(0.55)).pack(side="left", padx=2)
        ttk.Button(plot_tools, text="Zoom Out", command=lambda: zoom_graph(1.8)).pack(side="left", padx=2)
        ttk.Button(plot_tools, text="Scroll Left", command=lambda: pan_graph(-0.20)).pack(side="left", padx=2)
        ttk.Button(plot_tools, text="Scroll Right", command=lambda: pan_graph(0.20)).pack(side="left", padx=2)
        ttk.Button(plot_tools, text="Full Extent", command=lambda: full_graph_extent()).pack(side="left", padx=2)
        ttk.Label(plot_tools, textvariable=graph_tool, foreground="#1d4ed8").pack(side="right", padx=(8, 0))
        graph_scroll = ttk.Scale(plot_frame, from_=0.0, to=100.0, variable=graph_view_start, command=lambda _value: graph_scroll_changed())
        graph_scroll.pack(fill="x", pady=(0, 4))
        plot_canvas = tk.Canvas(plot_frame, bg="#ffffff", height=430, highlightthickness=2, highlightbackground="#2563eb")
        plot_canvas.pack(fill="both", expand=True)

        table_controls = ttk.Frame(table_frame)
        table_controls.pack(fill="x", pady=(0, 4))
        ttk.Button(table_controls, text="First", command=lambda: set_table_page(0)).pack(side="left", padx=2)
        ttk.Button(table_controls, text="Previous", command=lambda: page_table(-1)).pack(side="left", padx=2)
        ttk.Button(table_controls, text="Next", command=lambda: page_table(1)).pack(side="left", padx=2)
        ttk.Button(table_controls, text="Last", command=lambda: set_table_page(max(0, len(ensure_edit_state()) - int(parse_float(table_page_size.get()) or 2500)))).pack(side="left", padx=2)
        ttk.Label(table_controls, text="Start row").pack(side="left", padx=(10, 2))
        ttk.Entry(table_controls, textvariable=table_page_start, width=8).pack(side="left", padx=2)
        ttk.Label(table_controls, text="Rows/page").pack(side="left", padx=(10, 2))
        ttk.Entry(table_controls, textvariable=table_page_size, width=7).pack(side="left", padx=2)
        ttk.Button(table_controls, text="Show Range", command=lambda: populate_sb_edit_table(ensure_edit_state())).pack(side="left", padx=2)
        ttk.Label(table_controls, textvariable=table_page_status, foreground="#475569").pack(side="right", padx=4)

        columns = ("time", "easting", "northing", "ell_h", "k", "reduced_h", "depth", "edit", "state", "raw", "draft", "sv")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        headings = {
            "time": "Time",
            "easting": "Easting",
            "northing": "Northing",
            "ell_h": "Ell H",
            "k": "K Cell",
            "reduced_h": "H-K",
            "depth": "RTK Depth",
            "edit": "Edited",
            "state": "State",
            "raw": "Raw",
            "draft": "Draft",
            "sv": "SV",
        }
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(col, width=96 if col in {"edit", "state"} else 110, anchor="e" if col != "time" else "w")
        tree.pack(side="left", fill="both", expand=True)
        sb_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        sb_hscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb_scroll.set, xscrollcommand=sb_hscroll.set)
        sb_scroll.pack(side="right", fill="y")
        sb_hscroll.pack(side="bottom", fill="x")

        def log(text: str) -> None:
            status.set(text)
            info.insert("end", text + "\n")
            info.see("end")

        def point_depth(point: SurveyPoint) -> Optional[float]:
            edited = getattr(point, "sb_edited_depth", None)
            if edited is not None:
                return float(edited)
            if point.corrected_depth is not None:
                return float(point.corrected_depth)
            if point.raw_depth is not None:
                return float(point.raw_depth)
            return None

        def sb_points() -> list[SurveyPoint]:
            return [point for point in model.points if point.raw_depth is not None or point.corrected_depth is not None or point.x is not None or point.y is not None]

        def ensure_edit_state() -> list[SurveyPoint]:
            points = sb_points()
            last_xy: Optional[tuple[float, float]] = None
            along = 0.0
            for index, point in enumerate(points, 1):
                xy = (point.x, point.y)
                if xy[0] is not None and xy[1] is not None:
                    xy_float = (float(xy[0]), float(xy[1]))
                    if last_xy is not None:
                        along += math.hypot(xy_float[0] - last_xy[0], xy_float[1] - last_xy[1])
                    last_xy = xy_float
                else:
                    along = float(index)
                setattr(point, "sb_edit_index", index)
                setattr(point, "sb_along_track", along if along > 0 else float(index))
                if not hasattr(point, "sb_accepted"):
                    setattr(point, "sb_accepted", True)
                if not hasattr(point, "sb_flag"):
                    setattr(point, "sb_flag", "RAW")
                if not hasattr(point, "sb_note"):
                    setattr(point, "sb_note", "")
                if not hasattr(point, "sb_edited_depth"):
                    setattr(point, "sb_edited_depth", point.corrected_depth if point.corrected_depth is not None else point.raw_depth)
            return points

        def sounding_points() -> list[SurveyPoint]:
            return [point for point in ensure_edit_state() if point_depth(point) is not None]

        def selected_sounding() -> Optional[SurveyPoint]:
            return selected_point.get("point")

        def set_selected(point: Optional[SurveyPoint]) -> None:
            selected_point["point"] = point

        def table_values(point: SurveyPoint) -> tuple[str, ...]:
            return (
                point.time,
                format_number(point.x),
                format_number(point.y),
                format_number(point.altitude_m),
                format_number(point.ktd_cell_k),
                format_number(point.rtk_reduced_height),
                format_number(point.corrected_depth),
                format_number(point_depth(point)),
                "ACCEPT" if bool(getattr(point, "sb_accepted", True)) else "REJECT",
                format_number(point.raw_depth),
                format_number(point.draft),
                format_number(point.sound_speed),
            )

        def normalized_table_page(total: int) -> tuple[int, int]:
            size = int(parse_float(table_page_size.get()) or 2500)
            size = max(100, min(10000, size))
            start = max(0, int(table_page_start.get() or 0))
            if total:
                start = min(start, max(0, total - 1))
            else:
                start = 0
            table_page_size.set(str(size))
            table_page_start.set(start)
            return start, min(total, start + size)

        def set_table_page(start_index: int) -> None:
            table_page_start.set(max(0, int(start_index)))
            populate_sb_edit_table(ensure_edit_state())

        def page_table(direction: int) -> None:
            size = int(parse_float(table_page_size.get()) or 2500)
            set_table_page(int(table_page_start.get() or 0) + direction * max(100, size))

        def populate_sb_edit_table(points: list[SurveyPoint]) -> None:
            after_id = table_load_job.get("after_id")
            if after_id is not None:
                try:
                    win.after_cancel(after_id)
                except Exception:
                    pass
                table_load_job["after_id"] = None
            tree.delete(*tree.get_children())
            tree.tag_configure("accepted", foreground="#111827")
            tree.tag_configure("rejected", foreground="#b91c1c")
            total = len(points)
            start, end = normalized_table_page(total)
            page_points = points[start:end]
            batch_size = 500
            table_page_status.set(f"Rows {start + 1 if total else 0}-{end} of {total}")
            status.set(f"Loading table rows {start + 1 if total else 0}-{end} of {total}...")

            def insert_batch(start_index: int = 0) -> None:
                end_index = min(start_index + batch_size, len(page_points))
                for point in page_points[start_index:end_index]:
                    tag = "rejected" if not bool(getattr(point, "sb_accepted", True)) else "accepted"
                    tree.insert(
                        "",
                        "end",
                        iid=str(id(point)),
                        values=table_values(point),
                        tags=(tag,),
                    )
                if end_index < len(page_points):
                    status.set(f"Loading table page {end_index}/{len(page_points)}...")
                    table_load_job["after_id"] = win.after(10, lambda: insert_batch(end_index))
                else:
                    table_load_job["after_id"] = None
                    status.set(f"Loaded table rows {start + 1 if total else 0}-{end} of {total}.")

            insert_batch()

        def refresh_sb_edit() -> None:
            points = ensure_edit_state()
            soundings = [point for point in points if point_depth(point) is not None]
            track = [point for point in points if point.x is not None and point.y is not None]
            accepted = [point for point in soundings if bool(getattr(point, "sb_accepted", True))]
            rejected = len(soundings) - len(accepted)
            depths = [point_depth(point) for point in accepted if point_depth(point) is not None] or [point_depth(point) for point in soundings if point_depth(point) is not None]
            if depths:
                summary.set(
                    f"Soundings: {len(soundings)} | Accepted: {len(accepted)} | Rejected: {rejected} | "
                    f"Bins: {len(edit_bins)} | Min/Max/Mean: {min(depths):.2f}/{max(depths):.2f}/{statistics.mean(depths):.2f} m | Track: {len(track)}"
                )
            else:
                summary.set(f"Soundings: {len(soundings)} | Track: {len(track)} | Total rows: {len(points)}")
            populate_sb_edit_table(points)
            redraw_sb_plot()

        def clamp_graph_view() -> None:
            start = max(0.0, min(99.0, float(graph_view_start.get())))
            end = max(start + 1.0, min(100.0, float(graph_view_end.get())))
            if end - start < 1.0:
                start = max(0.0, end - 1.0)
            graph_view_start.set(start)
            graph_view_end.set(end)

        def set_graph_view(start: float, end: float) -> None:
            start = max(0.0, min(99.0, start))
            end = max(start + 1.0, min(100.0, end))
            graph_scroll_guard["updating"] = True
            graph_view_start.set(start)
            graph_view_end.set(end)
            graph_scroll_guard["updating"] = False
            redraw_sb_plot()

        def graph_scroll_changed() -> None:
            if graph_scroll_guard["updating"]:
                return
            width_pct = max(1.0, float(graph_view_end.get()) - float(graph_view_start.get()))
            start = float(graph_view_start.get())
            set_graph_view(start, min(100.0, start + width_pct))

        def set_graph_tool(name: str) -> None:
            graph_tool.set(name)
            cursor_status.set(f"Graphical Linear Editor mode: {name}.")

        def pan_graph(fraction: float) -> None:
            width_pct = max(1.0, float(graph_view_end.get()) - float(graph_view_start.get()))
            delta = width_pct * fraction
            start = float(graph_view_start.get()) + delta
            end = float(graph_view_end.get()) + delta
            if start < 0.0:
                end -= start
                start = 0.0
            if end > 100.0:
                start -= end - 100.0
                end = 100.0
            set_graph_view(start, end)

        def zoom_graph(factor: float, center_pct: Optional[float] = None) -> None:
            start = float(graph_view_start.get())
            end = float(graph_view_end.get())
            center = center_pct if center_pct is not None else (start + end) / 2.0
            width_pct = max(1.0, min(100.0, (end - start) * factor))
            set_graph_view(center - width_pct / 2.0, center + width_pct / 2.0)

        def full_graph_extent() -> None:
            set_graph_view(0.0, 100.0)

        def chain_from_canvas_x(x_value: float) -> float:
            left = graph_world["left"]
            plot_w = max(1.0, graph_world["plot_w"])
            ratio = max(0.0, min(1.0, (float(x_value) - left) / plot_w))
            return graph_world["min_chain"] + ratio * (graph_world["max_chain"] - graph_world["min_chain"])

        def percent_from_chain(chain: float, all_min: float, all_max: float) -> float:
            span = max(1.0, all_max - all_min)
            return max(0.0, min(100.0, (chain - all_min) / span * 100.0))

        def redraw_sb_plot() -> None:
            plot_canvas.delete("all")
            width = max(plot_canvas.winfo_width(), 10)
            height = max(plot_canvas.winfo_height(), 10)
            plot_canvas.create_rectangle(0, 0, width, height, fill="#ffffff", outline="")
            all_points = sounding_points()
            if not all_points:
                plot_canvas.create_text(
                    width // 2,
                    height // 2,
                    text="Load RAW / NMEA or use the logger to populate SB EDIT.",
                    fill="#64748b",
                    font=("Segoe UI", 13),
                )
                return
            clamp_graph_view()
            margin_left, margin_top, margin_right, margin_bottom = 62, 24, 24, 52
            plot_w = max(1, width - margin_left - margin_right)
            plot_h = max(1, height - margin_top - margin_bottom)
            all_chains = [float(getattr(point, "sb_along_track", index + 1)) for index, point in enumerate(all_points)]
            all_min_chain, all_max_chain = min(all_chains), max(all_chains)
            all_span = max(1.0, all_max_chain - all_min_chain)
            view_min = all_min_chain + (float(graph_view_start.get()) / 100.0) * all_span
            view_max = all_min_chain + (float(graph_view_end.get()) / 100.0) * all_span
            points = [
                point
                for point in all_points
                if view_min <= float(getattr(point, "sb_along_track", 0.0)) <= view_max
            ]
            if not points:
                points = all_points
                view_min = all_min_chain
                view_max = all_max_chain
            if len(points) > 3500:
                step = max(1, len(points) // 3500)
                display_points = points[::step]
                if points[-1] is not display_points[-1]:
                    display_points.append(points[-1])
                cursor_status.set(f"Graph view decimated for speed: showing {len(display_points)} of {len(points)} visible soundings. Zoom in for more detail.")
            else:
                display_points = points
            chains = [float(getattr(point, "sb_along_track", index + 1)) for index, point in enumerate(points)]
            depths = [point_depth(point) for point in points if point_depth(point) is not None]
            min_chain, max_chain = min(chains), max(chains)
            min_depth, max_depth = min(depths), max(depths)
            depth_pad = max(0.5, (max_depth - min_depth) * 0.12)
            min_depth -= depth_pad
            max_depth += depth_pad
            span_chain = max(max_chain - min_chain, 1.0)
            span_depth = max(max_depth - min_depth, 0.1)
            graph_world.update(
                {
                    "min_chain": min_chain,
                    "max_chain": max_chain,
                    "all_min_chain": all_min_chain,
                    "all_max_chain": all_max_chain,
                    "left": float(margin_left),
                    "top": float(margin_top),
                    "plot_w": float(plot_w),
                    "plot_h": float(plot_h),
                }
            )
            point_cache.clear()

            def project_point(point: SurveyPoint, depth: float) -> tuple[float, float]:
                chain = float(getattr(point, "sb_along_track", 0.0))
                x = margin_left + (chain - min_chain) / span_chain * plot_w
                y = margin_top + (depth - min_depth) / span_depth * plot_h
                return x, y

            for tick in range(6):
                ratio = tick / 5
                y = margin_top + ratio * plot_h
                x = margin_left + ratio * plot_w
                plot_canvas.create_line(margin_left, y, margin_left + plot_w, y, fill="#e2e8f0")
                plot_canvas.create_line(x, margin_top, x, margin_top + plot_h, fill="#eef2f7")
                plot_canvas.create_text(margin_left - 8, y, text=f"{min_depth + ratio * span_depth:.1f}", fill="#64748b", anchor="e", font=("Consolas", 9))
                plot_canvas.create_text(x, margin_top + plot_h + 16, text=f"{min_chain + ratio * span_chain:.0f}", fill="#64748b", anchor="n", font=("Consolas", 9))
            plot_canvas.create_rectangle(margin_left, margin_top, margin_left + plot_w, margin_top + plot_h, outline="#94a3b8")
            plot_canvas.create_text(margin_left, height - 22, text="Along-track chainage (m)", fill="#475569", anchor="w")
            plot_canvas.create_text(10, margin_top, text="Depth (m)", fill="#475569", anchor="nw")

            last_raw: Optional[tuple[float, float]] = None
            last_edit: Optional[tuple[float, float]] = None
            for point in display_points:
                raw_depth = point.corrected_depth if point.corrected_depth is not None else point.raw_depth
                edit_depth = point_depth(point)
                if raw_depth is not None:
                    rx, ry = project_point(point, float(raw_depth))
                    if last_raw is not None:
                        plot_canvas.create_line(last_raw[0], last_raw[1], rx, ry, fill="#94a3b8", width=1)
                    last_raw = (rx, ry)
                if edit_depth is None:
                    continue
                ex, ey = project_point(point, float(edit_depth))
                accepted_state = bool(getattr(point, "sb_accepted", True))
                if last_edit is not None:
                    plot_canvas.create_line(last_edit[0], last_edit[1], ex, ey, fill="#16a34a" if accepted_state else "#dc2626", width=2)
                last_edit = (ex, ey)
                is_selected = selected_sounding() is point
                radius = 6 if is_selected else 3
                color = "#f59e0b" if is_selected else "#16a34a" if accepted_state else "#dc2626"
                plot_canvas.create_oval(ex - radius, ey - radius, ex + radius, ey + radius, fill=color, outline="")
                if not accepted_state:
                    plot_canvas.create_line(ex - 5, ey - 5, ex + 5, ey + 5, fill="#ffffff")
                    plot_canvas.create_line(ex + 5, ey - 5, ex - 5, ey + 5, fill="#ffffff")
                point_cache.append((ex, ey, point))

            previous_bin: Optional[tuple[float, float]] = None
            for item in edit_bins:
                chain = float(item["center_m"])
                if chain < min_chain or chain > max_chain:
                    continue
                depth = float(item["depth"])
                x = margin_left + (chain - min_chain) / span_chain * plot_w
                y = margin_top + (depth - min_depth) / span_depth * plot_h
                plot_canvas.create_rectangle(x - 4, y - 4, x + 4, y + 4, fill="#0891b2", outline="")
                if previous_bin is not None:
                    plot_canvas.create_line(previous_bin[0], previous_bin[1], x, y, fill="#0891b2", dash=(4, 4), width=2)
                previous_bin = (x, y)
            plot_canvas.create_text(
                margin_left + 8,
                margin_top + 8,
                text="Gray raw/corrected | Green accepted | Red rejected | Yellow selected | Cyan bin average",
                fill="#475569",
                anchor="nw",
            )

        def nearest_plotted_point(x: float, y: float) -> Optional[SurveyPoint]:
            best_point = None
            best_distance = 12.0
            for px, py, point in point_cache:
                distance = math.hypot(px - x, py - y)
                if distance < best_distance:
                    best_distance = distance
                    best_point = point
            return best_point

        def update_selected_detail(point: Optional[SurveyPoint]) -> None:
            if point is None:
                return
            cursor_status.set(
                f"Selected #{getattr(point, 'sb_edit_index', '-')} | "
                f"Chain {format_number(getattr(point, 'sb_along_track', None))} m | "
                f"Edited depth {format_number(point_depth(point))} m | "
                f"{'ACCEPTED' if bool(getattr(point, 'sb_accepted', True)) else 'REJECTED'} | "
                f"{getattr(point, 'sb_flag', 'RAW')}"
            )
            log(
                f"Selected #{getattr(point, 'sb_edit_index', '-')} chain {format_number(getattr(point, 'sb_along_track', None))} m, "
                f"raw {format_number(point.raw_depth)}, corrected {format_number(point.corrected_depth)}, "
                f"edited {format_number(point_depth(point))}, {getattr(point, 'sb_flag', 'RAW')}"
            )

        def accept_point(point: Optional[SurveyPoint] = None) -> None:
            point = point or selected_sounding()
            if point is None:
                return
            setattr(point, "sb_accepted", True)
            setattr(point, "sb_flag", "ACCEPTED")
            refresh_sb_edit()

        def reject_point(point: Optional[SurveyPoint] = None) -> None:
            point = point or selected_sounding()
            if point is None:
                return
            setattr(point, "sb_accepted", False)
            setattr(point, "sb_flag", "REJECTED")
            refresh_sb_edit()

        def accept_all() -> None:
            for point in sounding_points():
                setattr(point, "sb_accepted", True)
                if str(getattr(point, "sb_flag", "")).startswith("SPIKE") or getattr(point, "sb_flag", "") == "REJECTED":
                    setattr(point, "sb_flag", "ACCEPTED")
            log("Accepted all SB EDIT soundings.")
            refresh_sb_edit()

        def reset_editing() -> None:
            edit_bins.clear()
            for point in sounding_points():
                setattr(point, "sb_accepted", True)
                setattr(point, "sb_flag", "RAW")
                setattr(point, "sb_edited_depth", point.corrected_depth if point.corrected_depth is not None else point.raw_depth)
            log("Reset edited depths, flags, and bins.")
            refresh_sb_edit()

        def detect_spikes() -> None:
            points = sounding_points()
            if len(points) < 5:
                log("Need at least five soundings for spike detection.")
                return
            threshold = max(0.05, parse_float(spike_threshold.get()) or 0.75)
            rejected = 0
            for pos, point in enumerate(points):
                depth = point_depth(point)
                if depth is None:
                    continue
                lo = max(0, pos - 2)
                hi = min(len(points), pos + 3)
                window = sorted(point_depth(item) for item in points[lo:hi] if point_depth(item) is not None)
                if not window:
                    continue
                median = window[len(window) // 2]
                delta = abs(depth - median)
                if delta > threshold:
                    setattr(point, "sb_accepted", False)
                    setattr(point, "sb_flag", f"SPIKE {delta:.2f}m")
                    rejected += 1
            log(f"Spike detection rejected {rejected} sounding(s) using {threshold:.2f} m threshold.")
            refresh_sb_edit()

        def smooth_depths(local_only: bool = False) -> None:
            points = sounding_points()
            if not points:
                return
            window = max(3, int(parse_float(smooth_window.get()) or 5))
            if window % 2 == 0:
                window += 1
            targets = [selected_sounding()] if local_only else points
            half = window // 2
            updates: list[tuple[SurveyPoint, float]] = []
            for point in targets:
                if point is None or not bool(getattr(point, "sb_accepted", True)):
                    continue
                pos = points.index(point)
                lo = max(0, pos - half)
                hi = min(len(points), pos + half + 1)
                values = [point_depth(item) for item in points[lo:hi] if bool(getattr(item, "sb_accepted", True)) and point_depth(item) is not None]
                if values:
                    updates.append((point, statistics.mean(values)))
            for point, depth in updates:
                setattr(point, "sb_edited_depth", depth)
                setattr(point, "sb_accepted", True)
                setattr(point, "sb_flag", f"{'LOCAL ' if local_only else ''}SMOOTH {window}")
            log(f"Smoothed {len(updates)} sounding(s) with {window}-sample moving average.")
            refresh_sb_edit()

        def apply_tide_draft_edit() -> None:
            tide = parse_float(tide_adjustment.get())
            draft = parse_float(draft_adjustment.get())
            if tide is None or draft is None:
                messagebox.showwarning("SB EDIT", "Enter numeric tide and draft values.", parent=win)
                return
            for point in sounding_points():
                if not bool(getattr(point, "sb_accepted", True)) or point.raw_depth is None:
                    continue
                point.tide = float(tide)
                point.draft = float(draft)
                setattr(point, "sb_edited_depth", point.corrected_depth)
                setattr(point, "sb_flag", "TIDE+DRAFT")
            log(f"Applied SB EDIT tide {tide:.3f} m and draft {draft:.3f} m to accepted soundings.")
            refresh_sb_edit()

        def bin_average() -> None:
            points = [point for point in sounding_points() if bool(getattr(point, "sb_accepted", True)) and point_depth(point) is not None]
            if not points:
                log("No accepted soundings available for bin averaging.")
                return
            spacing = max(1.0, parse_float(bin_size.get()) or 25.0)
            groups: dict[int, list[SurveyPoint]] = {}
            for point in points:
                key = int(float(getattr(point, "sb_along_track", 0.0)) // spacing)
                groups.setdefault(key, []).append(point)
            edit_bins.clear()
            for key in sorted(groups):
                rows = groups[key]
                edit_bins.append(
                    {
                        "bin": key,
                        "center_m": key * spacing + spacing / 2.0,
                        "count": len(rows),
                        "depth": statistics.mean(float(point_depth(row) or 0.0) for row in rows),
                        "easting": statistics.mean(float(row.x or 0.0) for row in rows),
                        "northing": statistics.mean(float(row.y or 0.0) for row in rows),
                    }
                )
            log(f"Created {len(edit_bins)} bin-average sounding(s) at {spacing:.1f} m spacing.")
            refresh_sb_edit()

        def plot_click(event: tk.Event) -> None:
            point = nearest_plotted_point(event.x, event.y)
            if point is None:
                return
            set_selected(point)
            mode = edit_mode.get()
            if mode == "Reject":
                reject_point(point)
            elif mode == "Accept":
                accept_point(point)
            elif mode == "Smooth Local":
                smooth_depths(local_only=True)
            else:
                update_selected_detail(point)
                refresh_sb_edit()

        def plot_press(event: tk.Event) -> None:
            tool = graph_tool.get()
            if tool == "Zoom Window":
                graph_drag["start"] = (event.x, event.y)
                if graph_drag.get("rect") is not None:
                    plot_canvas.delete(graph_drag["rect"])
                graph_drag["rect"] = plot_canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#2563eb", dash=(4, 3), width=2)
                return
            if tool == "Pan":
                graph_drag["start"] = (event.x, float(graph_view_start.get()), float(graph_view_end.get()))
                cursor_status.set("Pan mode: drag left/right to move through the line.")
                return
            plot_click(event)

        def plot_drag(event: tk.Event) -> None:
            tool = graph_tool.get()
            start = graph_drag.get("start")
            if tool == "Zoom Window" and start is not None:
                x0, y0 = start  # type: ignore[misc]
                rect = graph_drag.get("rect")
                if rect is not None:
                    plot_canvas.coords(rect, x0, y0, event.x, event.y)
                return
            if tool == "Pan" and start is not None:
                x0, start_pct, end_pct = start  # type: ignore[misc]
                plot_w = max(1.0, graph_world["plot_w"])
                width_pct = max(1.0, end_pct - start_pct)
                delta_pct = -((float(event.x) - float(x0)) / plot_w) * width_pct
                set_graph_view(float(start_pct) + delta_pct, float(end_pct) + delta_pct)

        def plot_release(event: tk.Event) -> None:
            tool = graph_tool.get()
            start = graph_drag.get("start")
            if tool == "Zoom Window" and start is not None:
                x0, _y0 = start  # type: ignore[misc]
                rect = graph_drag.get("rect")
                if rect is not None:
                    plot_canvas.delete(rect)
                graph_drag["start"] = None
                graph_drag["rect"] = None
                if abs(float(event.x) - float(x0)) < 8:
                    return
                chain0 = chain_from_canvas_x(float(x0))
                chain1 = chain_from_canvas_x(float(event.x))
                all_min = graph_world.get("all_min_chain", graph_world["min_chain"])
                all_max = graph_world.get("all_max_chain", graph_world["max_chain"])
                set_graph_view(
                    percent_from_chain(min(chain0, chain1), all_min, all_max),
                    percent_from_chain(max(chain0, chain1), all_min, all_max),
                )
                return
            if tool == "Pan":
                graph_drag["start"] = None

        def plot_mousewheel(event: tk.Event) -> None:
            all_min = graph_world.get("all_min_chain", graph_world["min_chain"])
            all_max = graph_world.get("all_max_chain", graph_world["max_chain"])
            center_chain = chain_from_canvas_x(getattr(event, "x", graph_world["left"] + graph_world["plot_w"] / 2.0))
            center_pct = percent_from_chain(center_chain, all_min, all_max)
            if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
                zoom_graph(0.70, center_pct)
            else:
                zoom_graph(1.45, center_pct)

        def plot_motion(event: tk.Event) -> None:
            point = nearest_plotted_point(event.x, event.y)
            if point is None:
                cursor_status.set("Graphical Linear Editor: move over a sounding point, left-click to select, right-click for edit commands.")
                return
            cursor_status.set(
                f"Hover #{getattr(point, 'sb_edit_index', '-')} | "
                f"Chain {format_number(getattr(point, 'sb_along_track', None))} m | "
                f"Raw {format_number(point.raw_depth)} | Corrected {format_number(point.corrected_depth)} | "
                f"Edited {format_number(point_depth(point))} | {getattr(point, 'sb_flag', 'RAW')}"
            )

        def plot_right_click(event: tk.Event) -> None:
            point = nearest_plotted_point(event.x, event.y)
            if point is not None:
                set_selected(point)
                update_selected_detail(point)
                redraw_sb_plot()
            edit_menu = tk.Menu(win, tearoff=False)
            edit_menu.add_command(label="Accept selected sounding", command=accept_point)
            edit_menu.add_command(label="Reject selected sounding", command=reject_point)
            edit_menu.add_command(label="Smooth selected sounding", command=lambda: smooth_depths(True))
            edit_menu.add_separator()
            edit_menu.add_command(label="Detect visible spikes", command=detect_spikes)
            edit_menu.add_command(label="Bin average accepted soundings", command=bin_average)
            try:
                edit_menu.tk_popup(event.x_root, event.y_root)
            finally:
                edit_menu.grab_release()

        def table_selected(_event: tk.Event) -> None:
            selection = tree.selection()
            if not selection:
                return
            target_id = selection[0]
            for point in ensure_edit_state():
                if str(id(point)) == target_id:
                    set_selected(point)
                    update_selected_detail(point)
                    redraw_sb_plot()
                    return

        def save_sb_edit_json() -> None:
            name = filedialog.asksaveasfilename(
                title="Save SB EDIT JSON",
                defaultextension=".json",
                filetypes=[("JSON", "*.json"), ("All files", "*.*")],
                initialfile="sb_edit_project.json",
                parent=win,
            )
            if not name:
                return
            rows = []
            for point in ensure_edit_state():
                rows.append(
                    {
                        "index": getattr(point, "sb_edit_index", None),
                        "time": point.time,
                        "along_track": getattr(point, "sb_along_track", None),
                        "easting": point.x,
                        "northing": point.y,
                        "raw_depth": point.raw_depth,
                        "corrected_depth": point.corrected_depth,
                        "edited_depth": point_depth(point),
                        "tide": point.tide,
                        "draft": point.draft,
                        "sound_speed": point.sound_speed,
                        "accepted": bool(getattr(point, "sb_accepted", True)),
                        "flag": getattr(point, "sb_flag", "RAW"),
                        "source": point.source,
                    }
                )
            payload = {"name": "SB EDIT", "saved": datetime.now().isoformat(timespec="seconds"), "soundings": rows, "bins": edit_bins}
            Path(name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            log(f"Saved SB EDIT JSON: {Path(name).name}")

        def export_edited_csv() -> None:
            name = filedialog.asksaveasfilename(
                title="Export SB EDIT CSV",
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
                initialfile="sb_edit_soundings.csv",
                parent=win,
            )
            if not name:
                return
            fields = [
                "index",
                "time",
                "along_track",
                "easting",
                "northing",
                "raw_depth",
                "corrected_depth",
                "edited_depth",
                "tide",
                "draft",
                "sound_speed",
                "accepted",
                "flag",
                "source",
            ]
            with Path(name).open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for point in ensure_edit_state():
                    writer.writerow(
                        {
                            "index": getattr(point, "sb_edit_index", None),
                            "time": point.time,
                            "along_track": getattr(point, "sb_along_track", None),
                            "easting": point.x,
                            "northing": point.y,
                            "raw_depth": point.raw_depth,
                            "corrected_depth": point.corrected_depth,
                            "edited_depth": point_depth(point),
                            "tide": point.tide,
                            "draft": point.draft,
                            "sound_speed": point.sound_speed,
                            "accepted": int(bool(getattr(point, "sb_accepted", True))),
                            "flag": getattr(point, "sb_flag", "RAW"),
                            "source": point.source,
                        }
                    )
            log(f"Exported SB EDIT CSV: {Path(name).name}")

        def export_binned_csv() -> None:
            if not edit_bins:
                bin_average()
                if not edit_bins:
                    return
            name = filedialog.asksaveasfilename(
                title="Export SB EDIT Binned CSV",
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
                initialfile="sb_edit_binned.csv",
                parent=win,
            )
            if not name:
                return
            with Path(name).open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["bin", "center_m", "count", "depth", "easting", "northing"])
                writer.writeheader()
                writer.writerows(edit_bins)
            log(f"Exported SB EDIT binned CSV: {Path(name).name}")

        def sentence_kind(text: str) -> str:
            value = str(text or "").strip()
            if not value.startswith("$"):
                return ""
            return value.split("*", 1)[0].split(",", 1)[0][-3:].upper()

        def read_parameters() -> bool:
            dialog = tk.Toplevel(win)
            dialog.title("SB EDIT - Read Parameters")
            dialog.geometry("720x700")
            dialog.minsize(640, 620)
            dialog.transient(win)
            dialog.grab_set()
            result = {"ok": False}

            frame = ttk.Frame(dialog, padding=10)
            frame.pack(fill="both", expand=True)
            ttk.Label(
                frame,
                text="Select input sentences before loading RAW/NMEA data",
                font=("Segoe UI", 12, "bold"),
            ).pack(anchor="w", pady=(0, 8))
            ttk.Label(
                frame,
                text="GNSS sentences are used for time/position only. Echosounder sentences are used for depths only.",
                wraplength=560,
            ).pack(anchor="w", pady=(0, 8))
            top_action = ttk.Frame(frame)
            top_action.pack(fill="x", pady=(0, 10))
            ttk.Button(
                top_action,
                text="Apply Parameters and Load Selected File(s)",
                command=lambda: apply_params(),
            ).pack(side="left", fill="x", expand=True)
            ttk.Button(top_action, text="Cancel", command=dialog.destroy).pack(side="right", padx=(8, 0))

            body_params = ttk.PanedWindow(frame, orient="horizontal")
            body_params.pack(fill="both", expand=True)
            gnss_frame = ttk.LabelFrame(body_params, text="GNSS / Position Sentences", padding=8)
            echo_frame = ttk.LabelFrame(body_params, text="Echosounder / Depth Sentences", padding=8)
            body_params.add(gnss_frame, weight=1)
            body_params.add(echo_frame, weight=1)

            gnss_vars: dict[str, tk.BooleanVar] = {}
            for row, item in enumerate(["GGA", "RMC", "GLL", "VTG", "GSA", "GSV", "ZDA", "GST", "HDT", "THS", "GNS", "GBS", "BESTPOSA", "BESTPOS"]):
                var = tk.BooleanVar(value=item in sb_load_params["gnss"])
                gnss_vars[item] = var
                ttk.Checkbutton(gnss_frame, text=item, variable=var).grid(row=row, column=0, sticky="w", pady=1)

            echo_vars: dict[str, tk.BooleanVar] = {}
            for row, item in enumerate(["DPT", "DBT", "DBS", "DBK", "ECHOTRAC", "DESO25", "PLAIN_DEPTH"]):
                var = tk.BooleanVar(value=item in sb_load_params["echo"] or (item == "PLAIN_DEPTH" and bool(sb_load_params["allow_plain_depth"])))
                echo_vars[item] = var
                ttk.Checkbutton(echo_frame, text=item, variable=var).grid(row=row, column=0, sticky="w", pady=1)

            gnss_non_var = tk.BooleanVar(value=bool(sb_load_params["allow_gnss_non_nmea"]))
            echo_non_var = tk.BooleanVar(value=bool(sb_load_params["allow_echo_non_nmea"]))
            plain_var = tk.BooleanVar(value=bool(sb_load_params["allow_plain_depth"]))
            ttk.Checkbutton(gnss_frame, text="Allow GNSS NON-NMEA text", variable=gnss_non_var).grid(row=11, column=0, sticky="w", pady=(10, 1))
            ttk.Checkbutton(echo_frame, text="Allow echosounder NON-NMEA text", variable=echo_non_var).grid(row=11, column=0, sticky="w", pady=(10, 1))
            ttk.Checkbutton(echo_frame, text="Allow plain numeric depth rows", variable=plain_var).grid(row=12, column=0, sticky="w", pady=1)

            filters = ttk.LabelFrame(frame, text="Pre-Edit Depth Filters", padding=8)
            filters.pack(fill="x", pady=(10, 0))
            max_depth_var = tk.StringVar(value=str(sb_load_params["max_plain_depth"]))
            spike_limit_var = tk.StringVar(value=str(sb_load_params["spike_limit"]))
            step_gate_var = tk.StringVar(value=str(sb_load_params["step_gate"]))
            for col in range(3):
                filters.columnconfigure(col * 2 + 1, weight=1)
            ttk.Label(filters, text="Max plain depth m").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=2)
            ttk.Entry(filters, textvariable=max_depth_var, width=8).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=2)
            ttk.Label(filters, text="Spike limit m").grid(row=0, column=2, sticky="w", padx=(0, 4), pady=2)
            ttk.Entry(filters, textvariable=spike_limit_var, width=8).grid(row=0, column=3, sticky="ew", padx=(0, 10), pady=2)
            ttk.Label(filters, text="Step gate m").grid(row=0, column=4, sticky="w", padx=(0, 4), pady=2)
            ttk.Entry(filters, textvariable=step_gate_var, width=8).grid(row=0, column=5, sticky="ew", pady=2)
            ttk.Label(
                filters,
                text="Spike limit rejects local outliers. Step gate rejects sudden depth jumps between consecutive soundings.",
                wraplength=560,
            ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

            button_row = ttk.Frame(frame)
            button_row.pack(fill="x", pady=(10, 0))

            def apply_params() -> None:
                gnss = {key for key, var in gnss_vars.items() if var.get()}
                echo = {key for key, var in echo_vars.items() if var.get() and key != "PLAIN_DEPTH"}
                allow_plain = bool(plain_var.get() or echo_vars["PLAIN_DEPTH"].get())
                max_depth = parse_float(max_depth_var.get())
                spike_limit = parse_float(spike_limit_var.get())
                step_gate = parse_float(step_gate_var.get())
                if not gnss:
                    messagebox.showwarning("Read Parameters", "Select at least one GNSS sentence.", parent=dialog)
                    return
                if not echo and not allow_plain and not echo_non_var.get():
                    messagebox.showwarning("Read Parameters", "Select at least one echosounder sentence or plain/NON-NMEA depth option.", parent=dialog)
                    return
                if max_depth is None or max_depth <= 0:
                    messagebox.showwarning("Read Parameters", "Max plain depth must be positive.", parent=dialog)
                    return
                if spike_limit is None or spike_limit <= 0:
                    messagebox.showwarning("Read Parameters", "Spike limit must be positive.", parent=dialog)
                    return
                if step_gate is None or step_gate <= 0:
                    messagebox.showwarning("Read Parameters", "Step gate must be positive.", parent=dialog)
                    return
                sb_load_params.update(
                    {
                        "gnss": gnss,
                        "echo": echo,
                        "allow_gnss_non_nmea": bool(gnss_non_var.get()),
                        "allow_echo_non_nmea": bool(echo_non_var.get()),
                        "allow_plain_depth": allow_plain,
                        "max_plain_depth": float(max_depth),
                        "spike_limit": float(spike_limit),
                        "step_gate": float(step_gate),
                    }
                )
                params_ready.set(True)
                spike_threshold.set(f"{float(spike_limit):.3f}")
                params_status.set(
                    "Read Parameters ready: "
                    f"GNSS={','.join(sorted(gnss))}; Echo={','.join(sorted(echo)) or 'NONE'}; "
                    f"PlainDepth={'ON' if allow_plain else 'OFF'}; Spike={float(spike_limit):.2f}m; Step={float(step_gate):.2f}m"
                )
                result["ok"] = True
                dialog.destroy()

            ttk.Button(button_row, text="Apply Parameters and Load Selected File(s)", command=apply_params).pack(side="left", fill="x", expand=True)
            ttk.Button(button_row, text="Cancel", command=dialog.destroy).pack(side="right")
            dialog.bind("<Return>", lambda _event: apply_params())
            dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
            dialog.lift()
            dialog.focus_force()
            win.wait_window(dialog)
            return bool(result["ok"])

        def clone_with_position(depth_point: SurveyPoint, position: Optional[SurveyPoint], source: str) -> SurveyPoint:
            point = SurveyPoint(
                time=depth_point.time or (position.time if position is not None else ""),
                raw_depth=depth_point.raw_depth,
                tide=model.tide,
                draft=model.draft,
                sound_speed=model.sound_speed,
                source=source,
            )
            if position is not None:
                point.easting = position.easting
                point.northing = position.northing
                point.latitude = position.latitude
                point.longitude = position.longitude
                point.altitude_m = position.altitude_m
                point.quality = position.quality
            return point

        def parse_plain_depth_line(line: str) -> Optional[float]:
            values = [parse_float(item) for item in re.split(r"[,\t ]+", line.strip()) if item.strip()]
            nums = [value for value in values if value is not None]
            if len(nums) != 1:
                return None
            depth = float(nums[0])
            if depth <= 0 or depth > float(sb_load_params["max_plain_depth"]):
                return None
            return depth

        def load_filtered_sbes_file(path_obj: Path) -> list[SurveyPoint]:
            if is_hypack_raw_file(path_obj):
                # Typed HYPACK observations are authoritative; MSG is archival.
                # Reading both would duplicate soundings and mistake RAW navigation for depth.
                loaded = load_hypack_raw_points(path_obj)
                for point in loaded:
                    point.sound_speed = model.sound_speed
                    if not point.draft: point.draft = model.draft
                return loaded
            loaded: list[SurveyPoint] = []
            current_position: Optional[SurveyPoint] = None
            last_tide = model.tide
            with path_obj.open("r", encoding="utf-8", errors="replace") as handle:
                for line_no, raw_line in enumerate(handle, 1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    parts = line.replace(",", " ").split()
                    tag = parts[0].upper() if parts else ""
                    if tag == "TID" and len(parts) >= 4:
                        tide = parse_float(parts[3])
                        if tide is not None:
                            last_tide = float(tide)
                            if current_position is not None:
                                current_position.tide = last_tide
                        continue
                    if tag == "POS" and len(parts) >= 5:
                        point = SurveyPoint(
                            time=hypack_time_to_display(parts[2] if len(parts) > 2 else parts[1]),
                            easting=parse_float(parts[3]),
                            northing=parse_float(parts[4]),
                            tide=last_tide,
                            source=f"{path_obj.name}:{line_no}",
                        )
                        loaded.append(point)
                        current_position = point
                        continue
                    sentences = NMEA_RE.findall(line)
                    used_sentence = False
                    for sentence in sentences:
                        used_sentence = True
                        kind = sentence_kind(sentence)
                        if kind in sb_load_params["gnss"]:
                            parsed = parse_nmea_sentence(sentence)
                            if parsed is not None:
                                parsed.source = f"{path_obj.name}:{line_no}"
                                if parsed.latitude is not None and parsed.longitude is not None:
                                    ensure_projected_xy(parsed, settings.geodetic)
                                    current_position = parsed
                                    loaded.append(parsed)
                                elif current_position is not None:
                                    current_position.time = current_position.time or parsed.time
                                    current_position.speed_knots = parsed.speed_knots or current_position.speed_knots
                                    current_position.course_degrees = parsed.course_degrees or current_position.course_degrees
                                    current_position.heading_degrees = parsed.heading_degrees or current_position.heading_degrees
                        elif kind in sb_load_params["echo"]:
                            parsed_depth = parse_sbes_sentence(sentence)
                            depth = parsed_depth.get("depth")
                            if depth is None:
                                parsed = parse_nmea_sentence(sentence)
                                depth = parsed.raw_depth if parsed is not None else None
                            if depth is None:
                                continue
                            depth_point = SurveyPoint(raw_depth=float(depth), source=f"{path_obj.name}:{line_no}")
                            loaded.append(clone_with_position(depth_point, current_position, f"{path_obj.name}:{line_no}"))
                    if used_sentence:
                        continue
                    if tag in {"EC1", "EC2", "DEP", "RAW"} and bool(sb_load_params["allow_echo_non_nmea"]):
                        if tag == "RAW" and len(parts) >= 3:
                            depth = parse_float(parts[2])
                            time_text = parts[1]
                        elif len(parts) >= 4:
                            depth = parse_float(parts[3])
                            time_text = parts[2]
                        else:
                            depth = None
                            time_text = ""
                        if depth is not None and 0 < float(depth) <= float(sb_load_params["max_plain_depth"]):
                            depth_point = SurveyPoint(
                                time=hypack_time_to_display(time_text),
                                raw_depth=float(depth),
                                tide=last_tide,
                                source=f"{path_obj.name}:{line_no}",
                            )
                            loaded.append(clone_with_position(depth_point, current_position, f"{path_obj.name}:{line_no}"))
                        continue
                    if bool(sb_load_params["allow_plain_depth"]):
                        depth = parse_plain_depth_line(line)
                        if depth is not None:
                            loaded.append(clone_with_position(SurveyPoint(raw_depth=depth), current_position, f"{path_obj.name}:{line_no}"))
            return loaded

        def apply_pre_edit_filters(points: list[SurveyPoint]) -> tuple[int, int]:
            soundings = [point for point in points if point.raw_depth is not None or point.corrected_depth is not None]
            if not soundings:
                return 0, 0
            ensure_edit_state()
            spike_limit = float(sb_load_params["spike_limit"])
            step_gate = float(sb_load_params["step_gate"])
            spike_rejects = 0
            step_rejects = 0
            for pos, point in enumerate(soundings):
                depth = point_depth(point)
                if depth is None:
                    continue
                lo = max(0, pos - 2)
                hi = min(len(soundings), pos + 3)
                window = sorted(point_depth(item) for item in soundings[lo:hi] if point_depth(item) is not None)
                if window:
                    median = window[len(window) // 2]
                    delta = abs(depth - median)
                    if delta > spike_limit:
                        setattr(point, "sb_accepted", False)
                        setattr(point, "sb_flag", f"PRE SPIKE {delta:.2f}m")
                        spike_rejects += 1
                        continue
                if pos > 0:
                    previous = point_depth(soundings[pos - 1])
                    if previous is not None and abs(depth - previous) > step_gate:
                        setattr(point, "sb_accepted", False)
                        setattr(point, "sb_flag", f"STEP GATE {abs(depth - previous):.2f}m")
                        step_rejects += 1
            return spike_rejects, step_rejects

        def load_hypack_raw(path_obj):
            return load_hypack_raw_points(path_obj)

        def load_recorded() -> None:
            names = filedialog.askopenfilenames(
                title="Load One or More Recorded RAW / NMEA Files",
                initialdir=str(Path(settings.raw_folder or str(default_raw_folder()))),
                filetypes=[
                    ("Recorded RAW / NMEA", "*.raw *.RAW *.txt *.log *.nmea *.csv *.1 *.2 *.3"),
                    ("All files", "*.*"),
                ],
                parent=win,
            )
            if not names:
                return
            path_list = [Path(name) for name in names]
            if len(path_list) == 1:
                params_status.set(f"Selected {path_list[0].name}. Confirm Read Parameters before loading.")
            else:
                params_status.set(f"Selected {len(path_list)} files. Confirm Read Parameters before loading.")
            if not read_parameters():
                log("Load cancelled. Read Parameters must be confirmed after selecting RAW/NMEA file(s).")
                return
            before = len(model.points)
            total_added = 0
            loaded_points: list[SurveyPoint] = []
            file_summaries: list[str] = []
            try:
                read_settings()

                def add_loaded_point(point: SurveyPoint) -> None:
                    if point.latitude is not None and point.longitude is not None:
                        easting, northing = project_lat_lon_to_selected_xy(point.latitude, point.longitude, settings.geodetic)
                        if easting is not None and northing is not None:
                            point.easting = easting
                            point.northing = northing
                    model.add(point)
                    loaded_points.append(point)

                for path_obj in path_list:
                    added = 0
                    if is_hypack_raw_file(path_obj):
                        for point in load_filtered_sbes_file(path_obj):
                            add_loaded_point(point)
                            added += 1
                        if added == 0:
                            log(f"{path_obj.name}: no HYPACK RAW rows matched. Enable echosounder NON-NMEA for EC1/DEP/RAW depth tags.")
                    elif is_hypack_edited_file(path_obj):
                        for point in load_hypack_edited_points(path_obj):
                            add_loaded_point(point)
                            added += 1
                    elif path_obj.suffix.lower() in {".nmea", ".log", ".txt", ".raw"}:
                        for point in load_filtered_sbes_file(path_obj):
                            add_loaded_point(point)
                            added += 1
                        if added == 0:
                            log(f"{path_obj.name}: no rows matched the selected Read Parameters.")
                    else:
                        temp_model = SurveyModel()
                        temp_model.tide = model.tide
                        temp_model.draft = model.draft
                        temp_model.sound_speed = model.sound_speed
                        added = temp_model.load_file(path_obj)
                        for point in temp_model.points:
                            add_loaded_point(point)
                    total_added += added
                    file_summaries.append(f"{path_obj.name}: {added}")
            except Exception as exc:
                messagebox.showerror("SB EDIT", f"Could not load recorded file:\n{exc}", parent=win)
                return
            spike_rejects, step_rejects = apply_pre_edit_filters(loaded_points)
            log(
                f"Loaded {len(path_list)} file(s). Added {len(model.points) - before if total_added else 0} row(s). "
                f"Pre-edit filters: spike rejected {spike_rejects}, step-gate rejected {step_rejects}."
            )
            if file_summaries:
                log("File load counts: " + "; ".join(file_summaries))
            refresh_sb_edit()

        def create_tid_tdx_editor() -> None:
            editor = tk.Toplevel(win)
            editor.title("Create TID / TDX Tide File")
            editor.geometry("760x560")
            editor.minsize(620, 420)
            try:
                editor.state("zoomed")
            except Exception:
                pass
            frame = ttk.Frame(editor, padding=10)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text="Manual Tide Spreadsheet: Date, Time, Tide Height", font=("Segoe UI", 11, "bold")).pack(anchor="w")
            tide_text = tk.Text(frame, wrap="none", height=18)
            tide_text.pack(fill="both", expand=True, pady=8)
            tide_text.insert("end", "Date,Time,TideHeight\n2026-06-12,08:00:00,0.00\n2026-06-12,09:00:00,0.05\n")
            status_local = tk.StringVar(value="Enter or paste rows as Date, Time, TideHeight. CSV, tab, or space separated rows are accepted.")
            ttk.Label(frame, textvariable=status_local).pack(anchor="w", pady=(0, 6))

            def tide_rows() -> list[tuple[str, str, float]]:
                rows: list[tuple[str, str, float]] = []
                for raw_line in tide_text.get("1.0", "end").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or line.lower().startswith("date"):
                        continue
                    parts = [part.strip() for part in re.split(r"[,\\t ]+", line) if part.strip()]
                    if len(parts) < 2:
                        continue
                    height = parse_float(parts[-1])
                    if height is None:
                        continue
                    if len(parts) >= 3:
                        date_text = parts[0]
                        time_text = parts[1]
                    else:
                        date_text = ""
                        time_text = parts[0]
                    rows.append((date_text, time_text, float(height)))
                return rows

            def save_tide_file(kind: str) -> None:
                rows = tide_rows()
                if not rows:
                    messagebox.showwarning("Create TID / TDX", "No valid tide rows found.", parent=editor)
                    return
                ext = ".tdx" if kind.upper() == "TDX" else ".tid"
                name = filedialog.asksaveasfilename(
                    title=f"Save {kind.upper()} Tide File",
                    initialdir=str(project_root_from_raw_folder(settings.raw_folder)),
                    defaultextension=ext,
                    filetypes=[(f"{kind.upper()} tide", f"*{ext}"), ("All files", "*.*")],
                    parent=editor,
                )
                if not name:
                    return
                target = Path(name)
                with target.open("w", encoding="utf-8", newline="\\n") as handle:
                    if kind.upper() == "TDX":
                        handle.write("Date,Time,TideHeight\\n")
                        for date_text, time_text, height in rows:
                            handle.write(f"{date_text},{time_text},{height:.3f}\\n")
                    else:
                        handle.write("# HYDROGEOMATICS TID tide file\\n")
                        handle.write("# Date Time TideHeight_m\\n")
                        for date_text, time_text, height in rows:
                            handle.write(f"{date_text} {time_text} {height:.3f}\\n")
                status_local.set(f"Saved {len(rows)} tide row(s) to {target.name}")
                log(f"Created {target.name} with {len(rows)} tide row(s).")

            buttons = ttk.Frame(frame)
            buttons.pack(fill="x")
            ttk.Button(buttons, text="Save TID", command=lambda: save_tide_file("TID")).pack(side="left", padx=3)
            ttk.Button(buttons, text="Save TDX", command=lambda: save_tide_file("TDX")).pack(side="left", padx=3)
            ttk.Button(buttons, text="Close", command=editor.destroy).pack(side="right", padx=3)

        def tide_editor() -> None:
            value = simpledialog.askfloat("SB EDIT", "Tide correction (m):", initialvalue=model.tide, parent=win)
            if value is None:
                return
            tide_var.set(f"{value:.3f}")
            read_settings()
            model.apply_offsets()
            log(f"Tide correction set to {value:.3f} m")
            refresh_sb_edit()

        def sv_editor() -> None:
            value = simpledialog.askfloat("SB EDIT", "Sound velocity (m/s):", initialvalue=model.sound_speed, minvalue=1.0, parent=win)
            if value is None:
                return
            sv_var.set(f"{value:.3f}")
            read_settings()
            model.apply_offsets()
            log(f"Sound velocity set to {value:.3f} m/s")
            refresh_sb_edit()

        def apply_tide_sv() -> None:
            read_settings()
            model.apply_offsets()
            for point in live_echogram_points:
                point.tide = model.tide
                point.draft = model.draft
                point.sound_speed = model.sound_speed
            log("Applied tide, draft, and sound velocity corrections.")
            refresh_sb_edit()

        def interpolate_tide_by_index(tides: list[tuple[int, float]], index: int) -> Optional[float]:
            if not tides:
                return None
            tides_sorted = sorted(tides, key=lambda item: item[0])
            if index <= tides_sorted[0][0]:
                return tides_sorted[0][1]
            if index >= tides_sorted[-1][0]:
                return tides_sorted[-1][1]
            for (i0, v0), (i1, v1) in zip(tides_sorted, tides_sorted[1:]):
                if i0 <= index <= i1:
                    if i1 == i0:
                        return v0
                    ratio = (index - i0) / (i1 - i0)
                    return v0 + (v1 - v0) * ratio
            return tides_sorted[-1][1]

        def apply_observed_tide_file_to_loaded() -> None:
            if not messagebox.askyesno(
                "SB EDIT post-processing",
                "Use an observed Tide file for post-processing this loaded data?\n\n"
                "This applies observed tide values and does not recalculate RTK/KTD corrections.",
                parent=win,
            ):
                return
            name = filedialog.askopenfilename(
                title="Select observed Tide file",
                initialdir=str(project_root_from_raw_folder(settings.raw_folder)),
                filetypes=[("HYPACK Tide", "*.tid *.TID *.tide *.txt"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            try:
                tides = load_hypack_tide_file(Path(name))
            except Exception as exc:
                messagebox.showerror("SB EDIT", f"Could not load tide file:\n{exc}", parent=win)
                return
            applied = 0
            for index, point in enumerate(model.points):
                tide_value = interpolate_tide_by_index(tides, index)
                if tide_value is None:
                    continue
                point.tide = tide_value
                point.rtk_reduced_height = None
                point.rtk_reduced_depth = None
                point.ktd_cell_k = None
                applied += 1
            model.apply_offsets()
            log(f"Applied observed tide file {Path(name).name} to {applied} row(s). RTK/KTD recalculation was not used.")
            refresh_sb_edit()

        def apply_rtk_tide_to_loaded() -> None:
            if not messagebox.askyesno(
                "SB EDIT post-processing",
                "Recalculate RTK tidal corrections using the selected KTD file?\n\n"
                "This uses each raw point's ellipsoidal height H and KTD cell value K: H-K. "
                "Choose No if you want observed tide-file corrections for this file.",
                parent=win,
            ):
                return
            applied = 0
            last_reduced_height = None
            missing_alt = 0
            missing_k = 0
            read_settings()
            for point in model.points:
                if point.altitude_m is None:
                    missing_alt += 1
                    continue
                if apply_ktd_rtk_postprocess_to_point(point, settings.geodetic):
                    last_reduced_height = point.rtk_reduced_height
                    applied += 1
                else:
                    missing_k += 1
            if applied and last_reduced_height is not None:
                tide_var.set(f"{last_reduced_height:.3f}")
                log(
                    f"RTK KTD post-processing applied to {applied} row(s): "
                    "H-K reduced height and depth = raw depth + draft - (H-K). "
                    f"Skipped no ellipsoid height: {missing_alt}; no K cell: {missing_k}."
                )
            else:
                log(
                    "No rows processed. Need raw rows with ellipsoidal height and a KTD file selected in Geodetic settings. "
                    f"Skipped no ellipsoid height: {missing_alt}; no K cell: {missing_k}."
                )
            refresh_sb_edit()

        def replay_loaded_echogram() -> None:
            read_settings()
            points = current_points()
            start_echogram_replay(points)
            log(f"Replaying echogram from {len([p for p in points if p.raw_depth is not None or p.corrected_depth is not None])} depth sample(s).")

        def clear_live_echogram() -> None:
            live_echogram_points.clear()
            stop_echogram_replay()
            draw_view()
            log("Cleared live/replay echogram buffer.")

        def export_csv() -> None:
            name = filedialog.asksaveasfilename(
                title="Export SB CSV",
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            try:
                count = model.export_csv(Path(name))
            except Exception as exc:
                messagebox.showerror("SB EDIT", f"Could not export CSV:\n{exc}", parent=win)
                return
            log(f"Exported {count} rows to {Path(name).name}")

        def export_xyz() -> None:
            name = filedialog.asksaveasfilename(
                title="Export SB XYZ",
                defaultextension=".xyz",
                filetypes=[("XYZ", "*.xyz"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            try:
                count = model.export_xyz(Path(name))
            except Exception as exc:
                messagebox.showerror("SB EDIT", f"Could not export XYZ:\n{exc}", parent=win)
                return
            log(f"Exported {count} soundings to {Path(name).name}")

        actions = [
            ("Load RAW / NMEA", load_recorded),
            ("Create TID/TDX", create_tid_tdx_editor),
            ("Accept", accept_point),
            ("Reject", reject_point),
            ("Accept All", accept_all),
            ("Detect Spikes", detect_spikes),
            ("Smooth", lambda: smooth_depths(False)),
            ("Smooth Local", lambda: smooth_depths(True)),
            ("Bin Average", bin_average),
            ("Reset Edit", reset_editing),
            ("Apply Tide/Draft", apply_tide_draft_edit),
            ("Save JSON", save_sb_edit_json),
            ("Export Edited CSV", export_edited_csv),
            ("Export Binned CSV", export_binned_csv),
            ("Set Tide", tide_editor),
            ("Sound Velocity", sv_editor),
            ("Apply Tide", apply_tide_sv),
            ("Post: Tide File", apply_observed_tide_file_to_loaded),
            ("Post: RTK/KTD", apply_rtk_tide_to_loaded),
            ("Replay Echogram", replay_loaded_echogram),
            ("Stop Replay", stop_echogram_replay),
            ("Clear Echogram", clear_live_echogram),
            ("Export CSV", export_csv),
            ("Export XYZ", export_xyz),
            ("Refresh", refresh_sb_edit),
        ]
        button_bar = ttk.Frame(top)
        button_bar.pack(side="left", fill="x", expand=True)
        for index, (label, command) in enumerate(actions):
            row = index // 8
            col = index % 8
            ttk.Button(button_bar, text=label, command=command).grid(row=row, column=col, sticky="ew", padx=3, pady=2)
            button_bar.columnconfigure(col, weight=1)
        ttk.Label(top, textvariable=status).pack(side="right", padx=6)
        plot_canvas.bind("<Configure>", lambda _event: redraw_sb_plot())
        plot_canvas.bind("<Button-1>", plot_press)
        plot_canvas.bind("<B1-Motion>", plot_drag)
        plot_canvas.bind("<ButtonRelease-1>", plot_release)
        plot_canvas.bind("<Motion>", plot_motion)
        plot_canvas.bind("<Button-3>", plot_right_click)
        plot_canvas.bind("<MouseWheel>", plot_mousewheel)
        plot_canvas.bind("<Button-4>", plot_mousewheel)
        plot_canvas.bind("<Button-5>", plot_mousewheel)
        tree.bind("<<TreeviewSelect>>", table_selected)
        refresh_sb_edit()

    def draw_view() -> None:
        view_state["draw_pending"] = False
        if root.q.property("unified_navigation"):return
        if view_mode.get() == "echogram":
            draw_echogram()
        else:
            draw_plot()

    def request_draw_view(delay_ms: int = 25) -> None:
        if view_state.get("draw_pending"):
            return
        view_state["draw_pending"] = True
        root.after(delay_ms, draw_view)

    def draw_echogram(target_canvas: Optional[tk.Canvas] = None) -> None:
        target = target_canvas or canvas
        target.delete("all")
        width = max(target.winfo_width(), 10)
        height = max(target.winfo_height(), 10)
        target.create_rectangle(0, 0, width, height, fill="#05070a", outline="")
        echogram_source = live_echogram_points if live_echogram_points else model.points
        echogram = EchogramModel(depth_bins=max(40, min(220, height - 42)))
        echogram.build_from_points(echogram_source)
        if not echogram.pings:
            target.create_text(
                width // 2,
                height // 2,
                text=f"No echogram samples. Enable SBES {settings.sensor.port} or replay loaded data from SB EDIT.",
                fill="#b8c2cc",
                font=("Segoe UI", 14),
            )
            return
        plot_left = 38
        plot_top = 12
        plot_bottom = height - 28
        plot_width = max(1, width - plot_left - 8)
        plot_height = max(1, plot_bottom - plot_top)
        pings = echogram.pings[-plot_width:]
        x_scale = plot_width / max(1, len(pings))
        y_scale = plot_height / max(1, echogram.depth_bins)
        for x_index, ping in enumerate(pings):
            x1 = plot_left + x_index * x_scale
            x2 = plot_left + (x_index + 1) * x_scale + 1
            for y_index, value in enumerate(ping):
                r, g, b = waterfall_color(value)
                y1 = plot_top + y_index * y_scale
                y2 = plot_top + (y_index + 1) * y_scale + 1
                target.create_rectangle(x1, y1, x2, y2, fill=f"#{r:02x}{g:02x}{b:02x}", outline="")
        depths = [
            float(depth)
            for point in echogram_source
            for depth in [point.corrected_depth if point.corrected_depth is not None else point.raw_depth]
            if depth is not None
        ]
        max_depth = max(depths) * 1.15 if depths else 1.0
        visible_depths = echogram.bottom_depths[-len(pings):]
        last_xy: Optional[tuple[float, float]] = None
        for x_index, depth in enumerate(visible_depths):
            if depth is None:
                continue
            x = plot_left + (x_index + 0.5) * x_scale
            y = plot_top + min(max(depth / max_depth, 0.0), 1.0) * plot_height
            if last_xy is not None:
                target.create_line(last_xy[0], last_xy[1], x, y, fill="#ffffff", width=2)
            last_xy = (x, y)
        for tick in range(5):
            ratio = tick / 4
            y = plot_top + ratio * plot_height
            target.create_line(plot_left - 4, y, plot_left, y, fill="#6b7785")
            target.create_text(
                plot_left - 8,
                y,
                anchor="e",
                text=f"{max_depth * ratio:.1f}",
                fill="#b8c2cc",
                font=("Segoe UI", 8),
            )
        target.create_text(
            plot_left,
            height - 18,
            anchor="w",
            text=f"Acoustic Waterfall | {'LIVE/REPLAY' if live_echogram_points else 'MODEL'} | {len(echogram.pings)} pings | SBES {settings.sensor.port} | white trace = bottom/depth pick",
            fill="#d8dee9",
            font=("Segoe UI", 10),
        )

    def draw_plot() -> None:
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        chart_style = bool(enc_background_shapes)
        canvas_bg = CHART_BACKGROUND_COLOR if chart_style else theme_colors.get("canvas", "#101418")
        canvas.create_rectangle(0, 0, width, height, fill=canvas_bg, outline="")
        points = [p for p in model.points if p.x is not None and p.y is not None]
        background_points = [p for p in project_background_points if p.x is not None and p.y is not None]
        line_points: list[tuple[float, float]] = list(border_points)
        for shape in dxf_background_shapes + enc_background_shapes + kmz_background_shapes + ktd_extent_shapes:
            bbox = cached_shape_bbox(shape)
            if bbox is not None:
                line_points.append((bbox[0], bbox[1]))
                line_points.append((bbox[2], bbox[3]))
        for line in runlines.lines:
            line_points.append((line.start_x, line.start_y))
            line_points.append((line.end_x, line.end_y))
        if not points and not background_points and not line_points:
            canvas.create_rectangle(8, 8, width - 8, height - 8, outline="#7c8791", width=2)
            canvas.create_text(
                width // 2,
                height // 2,
                text="Load survey data or create runlines",
                fill="#b8c2cc",
                font=("Segoe UI", 14),
            )
            return
        xs = [float(p.x) for p in points if p.x is not None] + [float(p.x) for p in background_points if p.x is not None] + [p[0] for p in line_points]
        ys = [float(p.y) for p in points if p.y is not None] + [float(p.y) for p in background_points if p.y is not None] + [p[1] for p in line_points]
        depths = [p.corrected_depth for p in points if p.corrected_depth is not None]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        full_min_x, full_max_x = min_x, max_x
        full_min_y, full_max_y = min_y, max_y
        min_d = min(depths) if depths else 0.0
        max_d = max(depths) if depths else 1.0
        margin = 54
        span_x = max(max_x - min_x, 1e-9)
        span_y = max(max_y - min_y, 1e-9)
        span_d = max(max_d - min_d, 1e-9)
        selected_scale_span = nav_scale_to_span_m(nav_scale_var.get())
        if follow_vessel.get() and points:
            vessel_point = points[-1]
            center_x = float(vessel_point.x)
            center_y = float(vessel_point.y)
        else:
            if selected_scale_span is not None and float(view_state.get("last_span_x", 0.0)) > 0.0:
                center_x = (float(view_state.get("last_min_x", min_x)) + float(view_state.get("last_max_x", max_x))) / 2.0 + float(view_state["pan_x"])
                center_y = (float(view_state.get("last_min_y", min_y)) + float(view_state.get("last_max_y", max_y))) / 2.0 + float(view_state["pan_y"])
            else:
                center_x = (min_x + max_x) / 2.0 + float(view_state["pan_x"])
                center_y = (min_y + max_y) / 2.0 + float(view_state["pan_y"])
        if selected_scale_span is not None:
            aspect = max((height - margin * 2) / max(width - margin * 2, 1), 0.1)
            span_x = max(selected_scale_span, 1e-9)
            span_y = max(selected_scale_span * aspect, 1e-9)
            zoom = 1.0
        else:
            zoom = max(float(view_state["zoom"]), 0.05)
            span_x = max(span_x / zoom, 1e-9)
            span_y = max(span_y / zoom, 1e-9)
            plot_aspect = max((height - margin * 2) / max(width - margin * 2, 1), 0.1)
            current_aspect = span_y / max(span_x, 1e-9)
            if current_aspect < plot_aspect:
                span_y = span_x * plot_aspect
            elif current_aspect > plot_aspect:
                span_x = span_y / plot_aspect
        min_x = center_x - span_x / 2.0
        max_x = center_x + span_x / 2.0
        min_y = center_y - span_y / 2.0
        max_y = center_y + span_y / 2.0
        view_state["last_span_x"] = span_x
        view_state["last_span_y"] = span_y
        view_state["last_min_x"] = min_x
        view_state["last_max_x"] = max_x
        view_state["last_min_y"] = min_y
        view_state["last_max_y"] = max_y
        view_state["full_center_x"] = (full_min_x + full_max_x) / 2.0
        view_state["full_center_y"] = (full_min_y + full_max_y) / 2.0
        view_state["full_span_x"] = max(full_max_x - full_min_x, 1e-9)
        view_state["full_span_y"] = max(full_max_y - full_min_y, 1e-9)
        view_state["last_plot_width"] = max(width - margin * 2, 1)
        view_state["last_plot_height"] = max(height - margin * 2, 1)

        def project_xy(x_value: float, y_value: float) -> tuple[float, float]:
            px = margin + ((x_value - min_x) / span_x) * (width - margin * 2)
            py = height - margin - ((y_value - min_y) / span_y) * (height - margin * 2)
            return px, py

        plot_left = margin
        plot_top = margin
        plot_right = width - margin
        plot_bottom = height - margin
        view_state["last_plot_left"] = float(plot_left)
        view_state["last_plot_top"] = float(plot_top)
        view_state["last_plot_right"] = float(plot_right)
        view_state["last_plot_bottom"] = float(plot_bottom)
        frame_color = "#4b5563" if chart_style else "#d7dde4"
        grid_color = CHART_GRID_COLOR if chart_style else theme_colors.get("grid", "#24313b")
        label_color = CHART_TEXT_COLOR if chart_style else theme_colors.get("text", "#d7dde4")
        canvas.create_rectangle(plot_left, plot_top, plot_right, plot_bottom, outline=frame_color, width=2)
        tick_count = 8
        for tick in range(tick_count + 1):
            ratio = tick / tick_count
            gx = plot_left + ratio * (plot_right - plot_left)
            gy = plot_top + ratio * (plot_bottom - plot_top)
            easting = min_x + ratio * span_x
            northing = max_y - ratio * span_y
            canvas.create_line(gx, plot_top, gx, plot_bottom, fill=grid_color)
            canvas.create_line(plot_left, gy, plot_right, gy, fill=grid_color)
            canvas.create_line(gx, plot_bottom, gx, plot_bottom + 7, fill=label_color)
            canvas.create_line(gx, plot_top - 7, gx, plot_top, fill=label_color)
            canvas.create_line(plot_left, gy, plot_left + 7, gy, fill=label_color)
            canvas.create_line(plot_right - 7, gy, plot_right, gy, fill=label_color)
            canvas.create_text(gx, plot_bottom + 13, text=f"{easting:.0f}", fill=label_color, font=("Segoe UI", 8))
            canvas.create_text(plot_left + 10, gy, text=f"{northing:.0f}", fill=label_color, font=("Segoe UI", 8), anchor="w")
            try:
                lon_at_bottom, _lat_at_bottom = xy_to_kml_lonlat(float(easting), float(min_y))
                _lon_at_left, lat_at_left = xy_to_kml_lonlat(float(min_x), float(northing))
                canvas.create_text(gx, plot_top - 18, text=f"{lon_at_bottom:.5f}", fill=label_color, font=("Segoe UI", 7))
                canvas.create_text(plot_right + 6, gy, text=f"{lat_at_left:.5f}", fill=label_color, font=("Segoe UI", 7), anchor="w")
            except Exception:
                pass
        canvas.create_text((plot_left + plot_right) / 2, height - 11, text="Easting / Longitude", fill=label_color, font=("Segoe UI", 10, "bold"))
        canvas.create_text(plot_left + 24, (plot_top + plot_bottom) / 2, text="Northing", fill=label_color, font=("Segoe UI", 10, "bold"), angle=90)
        canvas.create_text(width - 12, (plot_top + plot_bottom) / 2, text="Latitude", fill=label_color, font=("Segoe UI", 9, "bold"), angle=90)
        canvas.create_text((plot_left + plot_right) / 2, plot_top - 36, text=f"Longitude along top | Latitude along right | Map scale {nav_scale_var.get()}", fill=label_color, font=("Segoe UI", 9, "bold"))

        def nice_scale_distance(target_m: float) -> float:
            if target_m <= 0:
                return 1.0
            exponent = math.floor(math.log10(target_m))
            base = 10 ** exponent
            fraction = target_m / base
            if fraction >= 5:
                nice = 5
            elif fraction >= 2:
                nice = 2
            else:
                nice = 1
            return float(nice * base)

        scale_distance = nice_scale_distance(span_x / 5.0)
        scale_pixels = min((scale_distance / max(span_x, 1e-9)) * (plot_right - plot_left), (plot_right - plot_left) * 0.32)
        scale_x0 = plot_left + 18
        scale_y = plot_bottom - 18
        canvas.create_line(scale_x0, scale_y, scale_x0 + scale_pixels, scale_y, fill=label_color, width=3)
        canvas.create_line(scale_x0, scale_y - 5, scale_x0, scale_y + 5, fill=label_color, width=2)
        canvas.create_line(scale_x0 + scale_pixels, scale_y - 5, scale_x0 + scale_pixels, scale_y + 5, fill=label_color, width=2)
        scale_label = f"{scale_distance:.0f} m" if scale_distance < 1000 else f"{scale_distance / 1000.0:g} km"
        canvas.create_text(scale_x0 + scale_pixels / 2.0, scale_y - 11, text=scale_label, fill=label_color, font=("Segoe UI", 8, "bold"))

        visible_bbox = (min_x, min_y, max_x, max_y)
        drag_fast = bool(view_state.get("is_panning"))
        bbox_pad = max(span_x, span_y) * 0.03
        query_bbox = (min_x - bbox_pad, min_y - bbox_pad, max_x + bbox_pad, max_y + bbox_pad)

        def draw_background_shapes(
            shapes: list[MapShape],
            default_color: str,
            label_limit: int = 80,
            segment_limit: int = 6000,
            shape_limit: int = 3500,
        ) -> tuple[int, int]:
            drawn = 0
            labels = 0
            visited = 0
            if drag_fast:
                label_limit = min(label_limit, 8)
                segment_limit = max(600, min(segment_limit, 3500))
                shape_limit = max(600, min(shape_limit, 2000))
            visible_items: list[tuple[float, tuple[float, float, float, float], MapShape]] = []
            for shape in shapes:
                bbox = cached_shape_bbox(shape)
                if bbox is not None and bbox_intersects(bbox, query_bbox):
                    area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 0.0)
                    visible_items.append((area, bbox, shape))
            visible_items.sort(key=lambda item: item[0])
            if len(visible_items) > shape_limit:
                visible_items = visible_items[:shape_limit]
            visible_count = len(visible_items)
            for _area, _bbox, shape in visible_items:
                visited += 1
                if drawn >= segment_limit:
                    break
                pts = [(float(x), float(y)) for x, y in shape.points]
                color_shape = shape.color or default_color
                if shape.kind == "point" and pts:
                    sx, sy = project_xy(pts[0][0], pts[0][1])
                    if plot_left - 8 <= sx <= plot_right + 8 and plot_top - 8 <= sy <= plot_bottom + 8:
                        symbol_name = enc_symbol_kind(shape)
                        sound_text = enc_sound_display(shape.attrs)
                        if symbol_name.startswith("SOUNDG") and sound_text:
                            if not bool(enc_display_options.get("show_soundings").get()):
                                continue
                            canvas.create_text(sx, sy, text=sound_text, fill=CHART_TEXT_COLOR, font=("Segoe UI", 7), anchor="center")
                        elif symbol_name.startswith(("BOY", "BCN")):
                            canvas.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, outline="#111111", fill=color_shape, width=1)
                            canvas.create_line(sx, sy + 5, sx, sy + 13, fill="#111111", width=1)
                        elif symbol_name.startswith("LIGHTS"):
                            canvas.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, outline=CHART_MAGENTA, fill="#fff8a6", width=2)
                            canvas.create_line(sx - 7, sy, sx + 7, sy, fill=CHART_MAGENTA)
                            canvas.create_line(sx, sy - 7, sx, sy + 7, fill=CHART_MAGENTA)
                        elif symbol_name.startswith(("OBSTRN", "WRECKS", "UWTROC")):
                            canvas.create_line(sx - 5, sy - 5, sx + 5, sy + 5, fill="#111111", width=2)
                            canvas.create_line(sx - 5, sy + 5, sx + 5, sy - 5, fill="#111111", width=2)
                        else:
                            canvas.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, outline=color_shape, fill=color_shape)
                        if shape.text and labels < label_limit and float(view_state.get("last_span_x", span_x)) < 2500 and not symbol_name.startswith("SOUNDG"):
                            label_text = str(shape.attrs.get("OBJNAM") or shape.attrs.get("CATLAM") or shape.text).split("|")[0][:48]
                            canvas.create_text(sx + 6, sy - 5, anchor="sw", text=label_text, fill=color_shape, font=("Segoe UI", 8))
                            labels += 1
                        drawn += 1
                elif shape.kind == "polygon" and len(pts) >= 3:
                    fill_color = str(shape.attrs.get("FILL", "") or "")
                    target_vertices = 160 if drag_fast else (900 if span_x < 10000 else 520)
                    if visible_count > 1500:
                        target_vertices = min(target_vertices, 320)
                    step = max(1, len(pts) // max(target_vertices, 1))
                    reduced = pts[::step]
                    if reduced[-1] != pts[-1]:
                        reduced.append(pts[-1])
                    flat: list[float] = []
                    for x_val, y_val in reduced:
                        sx, sy = project_xy(x_val, y_val)
                        flat.extend([sx, sy])
                    if len(flat) >= 6:
                        if fill_color:
                            try:
                                canvas.create_polygon(flat, fill=fill_color, outline=color_shape, width=max(1, int(shape.width)))
                            except Exception:
                                canvas.create_polygon(flat, fill="", outline=color_shape, width=max(1, int(shape.width)))
                        else:
                            canvas.create_polygon(flat, fill="", outline=color_shape, width=max(1, int(shape.width)))
                        drawn += max(1, len(reduced) - 1)
                    if shape.text and labels < label_limit and flat and float(view_state.get("last_span_x", span_x)) < 5000:
                        label_text = str(shape.attrs.get("OBJNAM") or shape.text).split("|")[0][:64]
                        canvas.create_text(flat[0] + 4, flat[1] - 4, anchor="sw", text=label_text, fill=color_shape, font=("Segoe UI", 8))
                        labels += 1
                elif shape.kind == "line" and len(pts) >= 2:
                    if len(pts) > 2:
                        target_vertices = 180 if drag_fast else (1400 if span_x < 10000 else 700)
                        if visible_count > 1500:
                            target_vertices = min(target_vertices, 420)
                        step = max(1, len(pts) // max(target_vertices, 1))
                        reduced = pts[::step]
                        if reduced[-1] != pts[-1]:
                            reduced.append(pts[-1])
                    else:
                        reduced = pts
                    previous = None
                    label_xy = None
                    for x_val, y_val in reduced:
                        sx, sy = project_xy(x_val, y_val)
                        if previous is not None:
                            x1d, y1d = previous
                            if not (max(x1d, sx) < plot_left - 20 or min(x1d, sx) > plot_right + 20 or max(y1d, sy) < plot_top - 20 or min(y1d, sy) > plot_bottom + 20):
                                dash = (4, 3) if enc_is_magenta_overlay(shape) else None
                                if dash:
                                    canvas.create_line(x1d, y1d, sx, sy, fill=color_shape, width=max(1, int(shape.width)), dash=dash)
                                else:
                                    canvas.create_line(x1d, y1d, sx, sy, fill=color_shape, width=max(1, int(shape.width)))
                                drawn += 1
                                if label_xy is None:
                                    label_xy = (sx, sy)
                                if drawn >= segment_limit:
                                    break
                        previous = (sx, sy)
                    if shape.text and labels < label_limit and label_xy is not None and float(view_state.get("last_span_x", span_x)) < 5000:
                        sx, sy = label_xy
                        canvas.create_text(sx + 4, sy - 4, anchor="sw", text=shape.text[:80], fill=color_shape, font=("Segoe UI", 8))
                        labels += 1
                elif shape.kind == "rectangle" and len(pts) >= 2:
                    x1d, y1d = project_xy(pts[0][0], pts[0][1])
                    x2d, y2d = project_xy(pts[1][0], pts[1][1])
                    canvas.create_rectangle(x1d, y1d, x2d, y2d, outline=color_shape, width=max(1, int(shape.width)))
                    drawn += 1
                elif shape.kind == "text" and pts and labels < label_limit and float(view_state.get("last_span_x", span_x)) < 5000:
                    sx, sy = project_xy(pts[0][0], pts[0][1])
                    if plot_left <= sx <= plot_right and plot_top <= sy <= plot_bottom:
                        canvas.create_text(sx, sy, text=shape.text[:80], anchor="nw", fill=color_shape, font=("Segoe UI", 9))
                        labels += 1
                        drawn += 1
            return drawn, visited

        drawn_dxf, _visible_dxf = draw_background_shapes(dxf_background_shapes, "#94a3b8", label_limit=80, segment_limit=22000, shape_limit=12000)
        drawn_enc, _visible_enc = draw_background_shapes(enc_background_shapes, CHART_DEPTH_CONTOUR, label_limit=160, segment_limit=14000, shape_limit=7000)
        drawn_kmz, _visible_kmz = draw_background_shapes(kmz_background_shapes, "#f97316", label_limit=60, segment_limit=4500, shape_limit=2200)
        drawn_ktd_extent, _visible_ktd_extent = draw_background_shapes(ktd_extent_shapes, "#111827", label_limit=8, segment_limit=20, shape_limit=8)

        background_drawn = 0
        ktd_drawn = 0
        ktd_labels = 0
        ktd_label_cells: set[tuple[int, int]] = set()
        ktd_values = [
            float(point.raw_depth)
            for point in background_points
            if point.raw_depth is not None
            and not is_ktd_nodata(float(point.raw_depth))
            and (str(point.source).upper().startswith("KTD BACKGROUND") or str(point.quality).upper() == "KTD")
        ]
        if not ktd_values:
            ktd_paths_for_range = [Path(str(name)) for name in project_background_files if Path(str(name)).suffix.lower() == ".ktd"]
            path_text_for_range = str(getattr(settings.geodetic, "ktd_file_path", "") or "")
            if path_text_for_range and Path(path_text_for_range) not in ktd_paths_for_range:
                ktd_paths_for_range.append(Path(path_text_for_range))
            for ktd_path in ktd_paths_for_range:
                matrix_for_range = load_ktd_matrix(ktd_path) if ktd_path.exists() else None
                rows_for_range = matrix_for_range.get("rows", []) if matrix_for_range else []
                if not isinstance(rows_for_range, list):
                    continue
                for row_values in rows_for_range:
                    if not isinstance(row_values, list):
                        continue
                    for value in row_values:
                        if value is not None and not is_ktd_nodata(float(value)):
                            ktd_values.append(float(value))
                if ktd_values:
                    break
        ktd_min = min(ktd_values) if ktd_values else 0.0
        ktd_max = max(ktd_values) if ktd_values else 1.0
        ktd_span = float(view_state.get("last_span_x", span_x))
        ktd_label_limit = 6500 if dxf_background_shapes else 2500
        ktd_label_span = 90000.0 if dxf_background_shapes else 50000.0

        def ktd_color(value: float) -> str:
            span_value = max(ktd_max - ktd_min, 0.001)
            ratio = max(0.0, min(1.0, (float(value) - ktd_min) / span_value))
            red = int(36 + 204 * ratio)
            green = int(180 - 70 * ratio)
            blue = int(235 - 150 * ratio)
            return f"#{red:02x}{green:02x}{blue:02x}"

        def ktd_display_color(value: float) -> str:
            if is_ktd_nodata(float(value)):
                return "#c084fc"
            return "#ff00ff"

        def draw_ktd_capsule(cx: float, cy: float, value: float) -> None:
            label = f"{float(value):.2f}"
            fill_color = ktd_display_color(float(value))
            radius = 3 if ktd_span < 65000 else 2
            canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline="#7e22ce", fill=fill_color, width=1)
            if ktd_span < 30000:
                canvas.create_text(cx + 6, cy - 5, text=label, anchor="sw", fill="#ff00ff", font=("Segoe UI", 8, "bold"))

        def draw_ktd_matrix_surface() -> int:
            nonlocal ktd_labels
            drawn_cells = 0
            ktd_paths = [Path(str(name)) for name in project_background_files if Path(str(name)).suffix.lower() == ".ktd"]
            if not ktd_paths:
                path_text = str(getattr(settings.geodetic, "ktd_file_path", "") or "")
                if path_text:
                    ktd_paths = [Path(path_text)]
            for ktd_path in ktd_paths:
                matrix = load_ktd_matrix(ktd_path) if ktd_path.exists() else None
                if not matrix:
                    continue
                meta = matrix.get("meta", {})
                rows = matrix.get("rows", [])
                if not isinstance(meta, dict) or not isinstance(rows, list):
                    continue
                x_count = int(meta.get("x_count", 0) or 0)
                y_count = int(meta.get("y_count", 0) or 0)
                min_grid_x = float(meta.get("min_x", 0.0) or 0.0)
                min_grid_y = float(meta.get("min_y", 0.0) or 0.0)
                max_grid_x = float(meta.get("max_x", min_grid_x) or min_grid_x)
                max_grid_y = float(meta.get("max_y", min_grid_y) or min_grid_y)
                if x_count <= 1 or y_count <= 1:
                    continue
                if max_grid_x < min_x or min_grid_x > max_x or max_grid_y < min_y or min_grid_y > max_y:
                    continue
                dx_grid = (max_grid_x - min_grid_x) / max(x_count - 1, 1)
                dy_grid = (max_grid_y - min_grid_y) / max(y_count - 1, 1)
                col_min = max(0, int(math.floor((min_x - min_grid_x) / max(dx_grid, 1e-12))) - 1)
                col_max = min(x_count - 1, int(math.ceil((max_x - min_grid_x) / max(dx_grid, 1e-12))) + 1)
                row_min = max(0, int(math.floor((min_y - min_grid_y) / max(dy_grid, 1e-12))) - 1)
                row_max = min(y_count - 1, int(math.ceil((max_y - min_grid_y) / max(dy_grid, 1e-12))) + 1)
                visible_count = max(1, (col_max - col_min + 1) * (row_max - row_min + 1))
                step = max(1, int(math.ceil(math.sqrt(visible_count / 45000.0))))
                for row in range(row_min, row_max + 1, step):
                    if row >= len(rows) or not isinstance(rows[row], list):
                        continue
                    row_values = rows[row]
                    for col in range(col_min, col_max + 1, step):
                        if col >= len(row_values):
                            continue
                        value = row_values[col]
                        if value is None:
                            continue
                        cx, cy = project_xy(min_grid_x + col * dx_grid, min_grid_y + row * dy_grid)
                        if cx < plot_left or cx > plot_right or cy < plot_top or cy > plot_bottom:
                            continue
                        radius = 2 if ktd_span < 65000 else 1
                        canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline="", fill=ktd_display_color(float(value)))
                        if ktd_span < ktd_label_span and ktd_labels < ktd_label_limit:
                            if plot_left <= cx <= plot_right and plot_top <= cy <= plot_bottom:
                                label_cell = (int(cx // 54), int(cy // 22))
                                if label_cell not in ktd_label_cells:
                                    ktd_label_cells.add(label_cell)
                                    draw_ktd_capsule(cx, cy, float(value))
                                    ktd_labels += 1
                        drawn_cells += 1
            return drawn_cells

        ktd_drawn += draw_ktd_matrix_surface()

        for point in background_points:
            try:
                bx, by = project_xy(float(point.x), float(point.y))
                if plot_left <= bx <= plot_right and plot_top <= by <= plot_bottom:
                    is_ktd = str(point.source).upper().startswith("KTD BACKGROUND") or str(point.quality).upper() == "KTD"
                    if is_ktd:
                        # Draw every visible KTD value cell. Only text labels are
                        # thinned by screen collision; the underlying KTD grid is
                        # no longer capped, so the complete file can be shown.
                        if point.raw_depth is not None and ktd_span < ktd_label_span and ktd_labels < ktd_label_limit:
                            cell = (int(bx // 54), int(by // 22))
                            if cell not in ktd_label_cells:
                                ktd_label_cells.add(cell)
                                draw_ktd_capsule(bx, by, float(point.raw_depth))
                                ktd_labels += 1
                        else:
                            radius = 2 if ktd_span < 65000 else 1
                            fill_color = ktd_display_color(float(point.raw_depth)) if point.raw_depth is not None else "#f8fafc"
                            canvas.create_oval(bx - radius, by - radius, bx + radius, by + radius, outline="", fill=fill_color)
                        ktd_drawn += 1
                    else:
                        if background_drawn >= 12000:
                            continue
                        canvas.create_oval(bx - 2, by - 2, bx + 2, by + 2, fill="#6b7280", outline="")
                        background_drawn += 1
            except Exception:
                continue

        if ktd_extent_shapes and not ktd_values:
            canvas.create_text(
                plot_left + 12,
                plot_top + 34,
                anchor="nw",
                text="KTD background loaded, but no real K cells were found. 999.99 cells are treated as nodata.",
                fill="#fee2e2",
                font=("Segoe UI", 10, "bold"),
            )

        if len(border_points) >= 2:
            border_screen = [project_xy(float(x), float(y)) for x, y in border_points]
            for (x1b, y1b), (x2b, y2b) in zip(border_screen, border_screen[1:]):
                canvas.create_line(x1b, y1b, x2b, y2b, fill="#fbbf24", width=2)
            if len(border_points) >= 3:
                x1b, y1b = border_screen[-1]
                x2b, y2b = border_screen[0]
                canvas.create_line(x1b, y1b, x2b, y2b, fill="#fbbf24", width=2, dash=(5, 3))
        active_line = selected_navigation_line() if use_selected_line.get() else None
        for line in runlines.lines:
            x1, y1 = project_xy(line.start_x, line.start_y)
            x2, y2 = project_xy(line.end_x, line.end_y)
            is_active = active_line is line
            color = "#ffdd66" if is_active else "#5bd6a6"
            width_line = 4 if is_active else 2
            canvas.create_line(x1, y1, x2, y2, fill=color, width=width_line)
            canvas.create_oval(x1 - 4, y1 - 4, x1 + 4, y1 + 4, outline=color)
            canvas.create_text(x1 + 5, y1 - 5, anchor="sw", text=line.name, fill=color if is_active else "#b9f6d7", font=("Segoe UI", 9, "bold" if is_active else "normal"))

        last: Optional[tuple[float, float]] = None
        for point in points:
            x, y = project_xy(float(point.x), float(point.y))
            if last is not None:
                canvas.create_line(last[0], last[1], x, y, fill="#334452", width=1)
            last = (x, y)
            depth = point.corrected_depth
            if depth is None:
                color = "#d8dee9"
            else:
                ratio = (depth - min_d) / span_d
                red = int(72 + ratio * 180)
                blue = int(230 - ratio * 140)
                color = f"#{red:02x}b0{blue:02x}"
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")
        if points:
            vessel_point = points[-1]
            vx, vy = project_xy(float(vessel_point.x), float(vessel_point.y))
            canvas.create_oval(vx - 8, vy - 8, vx + 8, vy + 8, outline="#ffffff", width=2)
            canvas.create_polygon(vx, vy - 12, vx - 6, vy + 4, vx + 6, vy + 4, fill="#ffcc66", outline="#6b4e00")
            canvas.create_text(vx + 11, vy - 11, anchor="sw", text="Vessel", fill="#ffdd99", font=("Segoe UI", 9))
        canvas.create_text(
            12,
            12,
            anchor="nw",
            text=(
                f"{len(points)} plotted points | BG {len(background_points)} | KTD {ktd_drawn} | {len(runlines.lines)} runlines | CAD {drawn_dxf}/{len(dxf_background_shapes)} | KTD LIMIT {drawn_ktd_extent}/{len(ktd_extent_shapes)} | ENC {drawn_enc}/{len(enc_background_shapes)} | KMZ {drawn_kmz}/{len(kmz_background_shapes)} | "
                f"scale {nav_scale_var.get()} | zoom {zoom:.2f}x | {'Follow Vessel' if follow_vessel.get() else 'No follow'}"
            ),
            fill="#d8dee9",
            font=("Segoe UI", 10),
        )
        if points and runlines.lines:
            solution = latest_navigation_solution()
            if solution is not None:
                last_point = latest_position_point() or points[-1]
                nx, ny = project_xy(solution.nearest_x, solution.nearest_y)
                px, py = project_xy(float(last_point.x), float(last_point.y))
                canvas.create_line(px, py, nx, ny, fill="#ffcc66", width=2, dash=(4, 3))
                canvas.create_text(
                    12,
                    32,
                    anchor="nw",
                    text=(
                        f"Active {solution.line.name}: XTE "
                        f"{solution.cross_track:.2f} {solution.side}, "
                        f"DTE {solution.distance_to_end:.2f}"
                    ),
                    fill="#ffdd99",
                    font=("Segoe UI", 10),
                )


    def show_ktd_editor() -> None:
        win = tk.Toplevel(root)
        win.title("KTD Editor")
        win.geometry("980x640")
        win.minsize(760, 480)
        win.resizable(True, True)

        project_folder = project_root_from_raw_folder(settings.raw_folder or str(default_raw_folder()))
        default_path = Path(getattr(settings.geodetic, "ktd_file_path", "") or str(project_folder / "rtk_separation.ktd"))
        path_var = tk.StringVar(value=str(default_path))
        mode_var = tk.StringVar(value="K")
        status = tk.StringVar(value="Ready")

        top = ttk.Frame(win, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="KTD file").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(top, textvariable=path_var).grid(row=0, column=1, columnspan=5, sticky="ew", padx=4, pady=4)
        top.columnconfigure(1, weight=1)

        grid_box = ttk.LabelFrame(win, text="Grid / node creation", padding=8)
        grid_box.pack(fill="x", padx=8, pady=(0, 8))
        x0_var = tk.StringVar(value="")
        y0_var = tk.StringVar(value="")
        xdim_var = tk.IntVar(value=3)
        ydim_var = tk.IntVar(value=3)
        xint_var = tk.StringVar(value="1000")
        yint_var = tk.StringVar(value="1000")
        value_var = tk.StringVar(value=str(getattr(settings.geodetic, "ktd_k_value", 0.0)))
        labels = [
            ("X Origin", x0_var),
            ("Y Origin", y0_var),
            ("X Dimension", xdim_var),
            ("Y Dimension", ydim_var),
            ("X Interval", xint_var),
            ("Y Interval", yint_var),
            ("Default Value", value_var),
        ]
        for index, (label, var) in enumerate(labels):
            ttk.Label(grid_box, text=label).grid(row=index // 4, column=(index % 4) * 2, sticky="w", padx=4, pady=4)
            ttk.Entry(grid_box, textvariable=var, width=14).grid(row=index // 4, column=(index % 4) * 2 + 1, sticky="ew", padx=4, pady=4)
        ttk.Label(grid_box, text="Value mode").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(grid_box, textvariable=mode_var, values=["K", "K-N"], state="readonly", width=10).grid(row=2, column=1, sticky="w", padx=4, pady=4)

        body = ttk.Frame(win, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)
        columns = ("easting", "northing", "value")
        tree = ttk.Treeview(body, columns=columns, show="headings", height=16)
        tree.heading("easting", text="Easting")
        tree.heading("northing", text="Northing")
        tree.heading("value", text="K / K-N Value")
        for col in columns:
            tree.column(col, width=150, anchor="e")
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        buttons = ttk.Frame(win, padding=(8, 0, 8, 8))
        buttons.pack(fill="x")
        ttk.Label(buttons, textvariable=status).pack(side="right", padx=6)

        def tree_nodes() -> list[tuple[float, float, float]]:
            nodes: list[tuple[float, float, float]] = []
            for item in tree.get_children():
                values = tree.item(item, "values")
                if len(values) < 3:
                    continue
                x = parse_float(values[0])
                y = parse_float(values[1])
                value = parse_float(values[2])
                if x is not None and y is not None and value is not None:
                    nodes.append((float(x), float(y), float(value)))
            return nodes

        def ktd_matrix_summary(file_path: Path, node_count: int) -> str:
            meta = load_ktd_grid_meta(file_path)
            if not meta:
                return f"{node_count} KTD node(s)"
            x_count = int(meta.get("x_count", 0) or 0)
            y_count = int(meta.get("y_count", 0) or 0)
            min_x = float(meta.get("min_x", 0.0) or 0.0)
            min_y = float(meta.get("min_y", 0.0) or 0.0)
            max_x = float(meta.get("max_x", min_x) or min_x)
            max_y = float(meta.get("max_y", min_y) or min_y)
            dx = (max_x - min_x) / max(x_count - 1, 1)
            dy = (max_y - min_y) / max(y_count - 1, 1)
            total = max(x_count * y_count, 1)
            return (
                f"{node_count} valid KTD node(s) | matrix {y_count} rows x {x_count} columns | "
                f"dx {dx:.3f} dy {dy:.3f} | valid {node_count / total * 100:.1f}%"
            )

        def refresh_tree(nodes: list[tuple[float, float, float]], source_path: Optional[Path] = None) -> None:
            tree.delete(*tree.get_children())
            for x, y, value in nodes:
                tree.insert("", "end", values=(f"{x:.3f}", f"{y:.3f}", f"{value:.4f}"))
            summary = ktd_matrix_summary(source_path, len(nodes)) if source_path else f"{len(nodes)} KTD node(s)"
            status.set(f"{summary}; all valid KTD nodes loaded.")

        def selected_or_last_world() -> tuple[float, float]:
            cursor = view_state.get("last_cursor_world")
            if cursor:
                return float(cursor[0]), float(cursor[1])
            point = latest_position_point()
            if point and point.x is not None and point.y is not None:
                return float(point.x), float(point.y)
            return 0.0, 0.0

        def add_node() -> None:
            x, y = selected_or_last_world()
            value = parse_float(value_var.get()) or 0.0
            tree.insert("", "end", values=(f"{x:.3f}", f"{y:.3f}", f"{value:.4f}"))
            status.set("Added node. Edit values by selecting a row and pressing Edit Row.")

        def edit_row() -> None:
            selection = tree.selection()
            if not selection:
                return
            item = selection[0]
            values = list(tree.item(item, "values"))
            x = simpledialog.askfloat("KTD Editor", "Easting:", initialvalue=parse_float(values[0]) or 0.0, parent=win)
            if x is None:
                return
            y = simpledialog.askfloat("KTD Editor", "Northing:", initialvalue=parse_float(values[1]) or 0.0, parent=win)
            if y is None:
                return
            value = simpledialog.askfloat("KTD Editor", "K or K-N value:", initialvalue=parse_float(values[2]) or 0.0, parent=win)
            if value is None:
                return
            tree.item(item, values=(f"{x:.3f}", f"{y:.3f}", f"{value:.4f}"))

        def remove_node() -> None:
            for item in tree.selection():
                tree.delete(item)
            status.set(f"{len(tree.get_children())} KTD node(s)")

        def generate_grid() -> None:
            x0 = parse_float(x0_var.get())
            y0 = parse_float(y0_var.get())
            if x0 is None or y0 is None:
                x0, y0 = selected_or_last_world()
                x0_var.set(f"{x0:.3f}")
                y0_var.set(f"{y0:.3f}")
            x_count = max(1, int(xdim_var.get()))
            y_count = max(1, int(ydim_var.get()))
            x_interval = parse_float(xint_var.get()) or 1.0
            y_interval = parse_float(yint_var.get()) or 1.0
            value = parse_float(value_var.get()) or 0.0
            nodes: list[tuple[float, float, float]] = []
            for row in range(y_count):
                for col in range(x_count):
                    nodes.append((float(x0) + col * x_interval, float(y0) + row * y_interval, value))
            refresh_tree(nodes)
            status.set(f"Generated {len(nodes)} node grid.")

        def browse_path() -> None:
            name = filedialog.asksaveasfilename(
                title="KTD file",
                initialdir=str(project_folder),
                initialfile=Path(path_var.get()).name or "rtk_separation.ktd",
                defaultextension=".ktd",
                filetypes=[("KTD files", "*.ktd *.KTD"), ("XYZ/text", "*.xyz *.txt *.csv"), ("All files", "*.*")],
                parent=win,
            )
            if name:
                path_var.set(name)

        def load_file() -> None:
            name = filedialog.askopenfilename(
                title="Load KTD / XYZ separation file",
                initialdir=str(Path(path_var.get()).parent if path_var.get() else project_folder),
                filetypes=[("KTD / XYZ files", "*.ktd *.KTD *.xyz *.txt *.csv"), ("All files", "*.*")],
                parent=win,
            )
            if not name:
                return
            path_var.set(name)
            file_path = Path(name)
            nodes = load_ktd_nodes(file_path)
            meta = load_ktd_grid_meta(file_path)
            if meta:
                xdim_var.set(int(meta.get("x_count", xdim_var.get()) or xdim_var.get()))
                ydim_var.set(int(meta.get("y_count", ydim_var.get()) or ydim_var.get()))
                x0_var.set(f"{float(meta.get('min_x', 0.0) or 0.0):.3f}")
                y0_var.set(f"{float(meta.get('min_y', 0.0) or 0.0):.3f}")
                x_count = max(int(meta.get("x_count", 1) or 1), 1)
                y_count = max(int(meta.get("y_count", 1) or 1), 1)
                min_x = float(meta.get("min_x", 0.0) or 0.0)
                min_y = float(meta.get("min_y", 0.0) or 0.0)
                max_x = float(meta.get("max_x", min_x) or min_x)
                max_y = float(meta.get("max_y", min_y) or min_y)
                xint_var.set(f"{(max_x - min_x) / max(x_count - 1, 1):.3f}")
                yint_var.set(f"{(max_y - min_y) / max(y_count - 1, 1):.3f}")
            refresh_tree(nodes, file_path)
            load_ktd_background_path(file_path, replace_existing=True)
            status.set(f"Loaded {Path(name).name}: {ktd_matrix_summary(file_path, len(nodes))}; displayed as KTD background.")

        def save_file(apply_settings: bool = True) -> None:
            nodes = tree_nodes()
            if not nodes:
                messagebox.showwarning("KTD Editor", "No KTD nodes to save.", parent=win)
                return
            target = Path(path_var.get() or str(default_path))
            source_meta = load_ktd_grid_meta(target)
            try:
                save_ktd_nodes(target, nodes, mode_var.get(), grid_meta=source_meta)
            except Exception as exc:
                messagebox.showerror("KTD Editor", f"Could not save KTD file:\\n{exc}", parent=win)
                return
            if apply_settings:
                settings.geodetic.ktd_file_path = str(target)
                settings.geodetic.ktd_k_value = sum(value for _x, _y, value in nodes) / len(nodes)
                load_ktd_background_path(target, replace_existing=True)
                save_default_settings()
            status.set(f"Saved {len(nodes)} node(s) to {target.name} and displayed as KTD background.")

        def clip_ktd_by_border() -> None:
            polygon = list(border_points)
            if len(polygon) < 3:
                name = filedialog.askopenfilename(
                    title="Load border file for KTD clipping",
                    initialdir=str(project_folder),
                    filetypes=[("HYPACK border", "*.brd *.BRD"), ("All files", "*.*")],
                    parent=win,
                )
                if name:
                    polygon = load_hypack_brd_points(Path(name))
            if len(polygon) < 3:
                messagebox.showwarning("KTD Editor", "Load or create a border with at least 3 points before clipping KTD.", parent=win)
                return
            source = Path(path_var.get() or str(default_path))
            if not source.exists():
                messagebox.showwarning("KTD Editor", "Load or save a source KTD file before clipping by border.", parent=win)
                return
            nodes = load_ktd_nodes(source)
            meta = load_ktd_grid_meta(source)
            if not nodes:
                messagebox.showwarning("KTD Editor", "No valid K values found in the KTD file.", parent=win)
                return
            inside_nodes = [(x, y, value) for x, y, value in nodes if point_in_polygon(float(x), float(y), polygon)]
            total_cells = None
            if meta:
                try:
                    total_cells = int(meta.get("x_count", 0) or 0) * int(meta.get("y_count", 0) or 0)
                except Exception:
                    total_cells = None
            target_name = filedialog.asksaveasfilename(
                title="Save clipped KTD file",
                initialdir=str(source.parent),
                initialfile=f"{source.stem}_border_clipped.ktd",
                defaultextension=".ktd",
                filetypes=[("KTD files", "*.ktd *.KTD"), ("All files", "*.*")],
                parent=win,
            )
            if not target_name:
                return
            target = Path(target_name)
            try:
                save_ktd_nodes(target, inside_nodes, mode_var.get(), grid_meta=meta)
            except Exception as exc:
                messagebox.showerror("KTD Editor", f"Could not save clipped KTD file:\\n{exc}", parent=win)
                return
            path_var.set(str(target))
            refresh_tree(inside_nodes, target)
            settings.geodetic.ktd_file_path = str(target)
            if inside_nodes:
                settings.geodetic.ktd_k_value = sum(value for _x, _y, value in inside_nodes) / len(inside_nodes)
            load_ktd_background_path(target, replace_existing=True)
            save_default_settings()
            outside_note = ""
            if total_cells is not None:
                outside_note = f" Outside/no-data grid cells: {max(total_cells - len(inside_nodes), 0)}."
            status.set(
                f"Saved clipped KTD: kept {len(inside_nodes)} ORIGINAL inside-border K value(s). "
                f"All outside-border cells are grey 999.99 no-data cells and are ignored for realtime K correction.{outside_note}"
            )

        def create_ktd_from_boundary() -> None:
            polygon = list(border_points)
            if len(polygon) < 3:
                name = filedialog.askopenfilename(
                    title="Load boundary for new KTD",
                    initialdir=str(project_folder),
                    filetypes=[("HYPACK border", "*.brd *.BRD"), ("All files", "*.*")],
                    parent=win,
                )
                if name:
                    polygon = load_hypack_brd_points(Path(name))
            if len(polygon) < 3:
                messagebox.showwarning("KTD Editor", "Load or select a boundary with at least 3 points before creating a boundary KTD.", parent=win)
                return
            k_value = parse_float(value_var.get())
            if k_value is None:
                messagebox.showwarning("KTD Editor", "Enter the required K value in Default Value before creating the KTD.", parent=win)
                return
            x_interval = parse_float(xint_var.get())
            y_interval = parse_float(yint_var.get())
            if x_interval is None or y_interval is None or x_interval <= 0 or y_interval <= 0:
                messagebox.showwarning("KTD Editor", "Enter positive X Interval and Y Interval values.", parent=win)
                return
            xs = [float(x) for x, _y in polygon]
            ys = [float(y) for _x, y in polygon]
            min_poly_x, max_poly_x = min(xs), max(xs)
            min_poly_y, max_poly_y = min(ys), max(ys)
            x_count = max(2, int(math.ceil((max_poly_x - min_poly_x) / float(x_interval))) + 1)
            y_count = max(2, int(math.ceil((max_poly_y - min_poly_y) / float(y_interval))) + 1)
            total_cells = x_count * y_count
            if total_cells > 1000000 and not messagebox.askyesno(
                "KTD Editor",
                f"This will create a {y_count} x {x_count} KTD grid ({total_cells:,} cells). Continue?",
                parent=win,
            ):
                return
            max_grid_x = min_poly_x + (x_count - 1) * float(x_interval)
            max_grid_y = min_poly_y + (y_count - 1) * float(y_interval)
            nodes: list[tuple[float, float, float]] = []
            for row in range(y_count):
                y = min_poly_y + row * float(y_interval)
                for col in range(x_count):
                    x = min_poly_x + col * float(x_interval)
                    if point_in_polygon(float(x), float(y), polygon):
                        nodes.append((float(x), float(y), float(k_value)))
            if not nodes:
                messagebox.showwarning("KTD Editor", "No grid cells fell inside the selected boundary. Reduce the interval or check the boundary file.", parent=win)
                return
            target_name = filedialog.asksaveasfilename(
                title="Save boundary KTD file",
                initialdir=str(Path(path_var.get()).parent if path_var.get() else project_folder),
                initialfile="boundary_constant_k.ktd",
                defaultextension=".ktd",
                filetypes=[("KTD files", "*.ktd *.KTD"), ("All files", "*.*")],
                parent=win,
            )
            if not target_name:
                return
            target = Path(target_name)
            grid_meta = {
                "x_count": x_count,
                "y_count": y_count,
                "min_x": min_poly_x,
                "min_y": min_poly_y,
                "max_x": max_grid_x,
                "max_y": max_grid_y,
                "header_order": "rows_cols",
            }
            try:
                save_ktd_nodes(target, nodes, mode_var.get(), grid_meta=grid_meta)
            except Exception as exc:
                messagebox.showerror("KTD Editor", f"Could not save boundary KTD file:\\n{exc}", parent=win)
                return
            path_var.set(str(target))
            x0_var.set(f"{min_poly_x:.3f}")
            y0_var.set(f"{min_poly_y:.3f}")
            xdim_var.set(x_count)
            ydim_var.set(y_count)
            refresh_tree(nodes, target)
            settings.geodetic.ktd_file_path = str(target)
            settings.geodetic.ktd_k_value = float(k_value)
            load_ktd_background_path(target, replace_existing=True)
            save_default_settings()
            status.set(
                f"Created {target.name}: {len(nodes)} inside-boundary K cell(s), "
                f"{max(total_cells - len(nodes), 0)} grey 999.99 outside/no-data cell(s)."
            )

        def apply_to_settings() -> None:
            nodes = tree_nodes()
            if nodes:
                settings.geodetic.ktd_k_value = sum(value for _x, _y, value in nodes) / len(nodes)
            settings.geodetic.ktd_file_path = path_var.get().strip()
            ktd_path = Path(settings.geodetic.ktd_file_path)
            if ktd_path.exists():
                load_ktd_background_path(ktd_path, replace_existing=True)
            save_default_settings()
            status.set("KTD file applied to Geodetic Settings and displayed as background.")

        ttk.Button(top, text="Browse", command=browse_path).grid(row=0, column=6, sticky="ew", padx=4)
        ttk.Button(buttons, text="Load KTD/XYZ", command=load_file).pack(side="left", padx=3)
        ttk.Button(buttons, text="Generate Grid", command=generate_grid).pack(side="left", padx=3)
        ttk.Button(buttons, text="Add Node", command=add_node).pack(side="left", padx=3)
        ttk.Button(buttons, text="Edit Row", command=edit_row).pack(side="left", padx=3)
        ttk.Button(buttons, text="Remove Node", command=remove_node).pack(side="left", padx=3)
        ttk.Button(buttons, text="Save KTD", command=save_file).pack(side="left", padx=3)
        ttk.Button(buttons, text="Clip by Border", command=clip_ktd_by_border).pack(side="left", padx=3)
        ttk.Button(buttons, text="Create KTD in Border", command=create_ktd_from_boundary).pack(side="left", padx=3)
        ttk.Button(buttons, text="Apply to Settings", command=apply_to_settings).pack(side="left", padx=3)
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="left", padx=3)

        existing = Path(path_var.get())
        if existing.exists():
            refresh_tree(load_ktd_nodes(existing))
        else:
            x, y = selected_or_last_world()
            x0_var.set(f"{x:.3f}")
            y0_var.set(f"{y:.3f}")

    def show_sbes_workflow():
        return _acquisition.new_window.show_sbes_panel()

    BUTTON_HELP: dict[str, str] = {
        "SBES": "Open the Single Beam Echosounder workflow for project setup, geodetic settings, hardware/serial communication, survey logging, background upload, and SB EDIT.",
        "Projects": "Use the current project folder, create a new project, copy a project, open an existing project file, or save the current project file. Project folders use RAW, EDIT, and SORT subfolders.",
        "Settings": "Open the maximized settings window for project folder, grid/background choice, RAW/EDIT/SORT background data loading, corrections, GNSS, SBES, device ports, recording rates, and geodetic parameters.",
        "THEME": "Change global font sizes, button and drop-down menu fonts, background contrast, and colour scheme.",
        "GEOCALC": "Open coordinate conversion tools. Includes LLH/XYZ, UTM conversion, datum transform, and selectable project/grid options.",
        "KTD Editor": "Create, load, edit, save, and display KTD separation files as HYPACK-style labelled K-value cells over CAD/DXF backgrounds.",
        "Multi-Beam Sys": "Open the combined multibeam system window for hardware setup, routing, logging, internal processing, professional handoff, visualization, and export.",
        "I/O Monitor": "Open a live monitor showing incoming raw strings from enabled selected devices. Closing this window does not stop logging.",
        "SS510 Control Panel": "Open SS510 echosounder serial controls, commands, sound velocity setup, send/receive logs, and selectable output-rate transmission checks.",
        "Start Logging": "Start HYPACK RAW logging. Shortcut: Ctrl+S. All incoming available sensor strings are preserved in the RAW file.",
        "Stop Log&Save": "Stop logging and save the active RAW file in the project RAW folder. Shortcut: Ctrl+E.",
        "Line Editor": "Open the runline editor for generating, opening, saving, selecting, picking start/end points, and clipping survey lines.",
        "Border": "Create, load, edit, and save the survey border used for display limits and clipping lines/background data.",
        "Background Upload": "Open one grouped background upload window for DXF/DWG, KMZ/KML, ENC/S-57, GeoTIFF availability information, and Fit/Extents.",
        "DXF": "Load DXF or DWG CAD files as a CAD-style background layer in the map screen.",
        "ODA DWG->DXF": "Convert DWG drawings to DXF through optional ODA File Converter, then load the converted DXF onto the map if selected.",
        "ENC": "Load an ENC/S-57 file as a chart background with feature objects, colors, and attribute inspection.",
        "KMZ": "Load KML/KMZ files as background features and export current or selected files to KMZ.",
        "SB EDIT": "Open single beam editing tools for RAW/NMEA loading, correction review, manual accept/reject, spike detection, smoothing, bin average, TID/TDX tide files, echogram replay, CSV/XYZ export, and sounding checks.",
        "Data Display": "Open a separate configurable live data display. Select visible fields, font size, and row order.",
        "Echogram": "Switch to the live SBES echogram/acoustic waterfall view. Uses the SBES port in Settings and can replay loaded soundings from SB EDIT.",
        "Zoom +": "Zoom into the current map view around the active view center.",
        "Zoom -": "Zoom out from the current map view.",
        "Zoom Window": "Drag a rectangle on the map to zoom to a selected window.",
        "Extents": "Fit all visible survey data, runlines, border, and loaded backgrounds into the map view.",
        "Follow": "Follow the live vessel position automatically as new GNSS fixes arrive.",
        "Unfollow": "Stop automatic vessel following so the map can be manually panned and zoomed.",
        "Toolbar": "Open toolbar settings to dock the toolbar top, bottom, left, or right and reorder buttons.",
        "Help": "Open this complete help guide.",
        "About": "Show program name and system summary.",
        "Clip All by Border": "Clip lines using the active border, keeping either inside or outside portions according to the chosen option.",
        "Toolbar Settings": "Open toolbar settings to change button order and docking.",
    }

    HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
        ("Main Buttons", [
            ("SBES", BUTTON_HELP["SBES"]),
            ("Projects", BUTTON_HELP["Projects"]),
            ("Settings", BUTTON_HELP["Settings"]),
            ("THEME", BUTTON_HELP["THEME"]),
            ("GEOCALC", BUTTON_HELP["GEOCALC"]),
            ("KTD Editor", BUTTON_HELP["KTD Editor"]),
            ("Multi-Beam Sys", BUTTON_HELP["Multi-Beam Sys"]),
            ("I/O Monitor", BUTTON_HELP["I/O Monitor"]),
            ("Start Logging", BUTTON_HELP["Start Logging"]),
            ("Stop Log&Save", BUTTON_HELP["Stop Log&Save"]),
            ("Data Display", BUTTON_HELP["Data Display"]),
            ("Toolbar", BUTTON_HELP["Toolbar"]),
        ]),
        ("Survey Planning And Lines", [
            ("Line Editor", BUTTON_HELP["Line Editor"]),
            ("Border", BUTTON_HELP["Border"]),
            ("Background Upload", BUTTON_HELP["Background Upload"]),
            ("Clip All by Border", BUTTON_HELP["Clip All by Border"]),
            ("Follow", BUTTON_HELP["Follow"]),
            ("Unfollow", BUTTON_HELP["Unfollow"]),
        ]),
        ("Background And Chart Files", [
            ("DXF", BUTTON_HELP["DXF"]),
            ("ODA DWG->DXF", BUTTON_HELP["ODA DWG->DXF"]),
            ("ENC", BUTTON_HELP["ENC"]),
            ("KMZ", BUTTON_HELP["KMZ"]),
        ]),
        ("Sounding And Display Tools", [
            ("SB EDIT", BUTTON_HELP["SB EDIT"]),
            ("Echogram", BUTTON_HELP["Echogram"]),
            ("Zoom +", BUTTON_HELP["Zoom +"]),
            ("Zoom -", BUTTON_HELP["Zoom -"]),
            ("Zoom Window", BUTTON_HELP["Zoom Window"]),
            ("Extents", BUTTON_HELP["Extents"]),
        ]),
        ("Mouse Controls", [
            ("Left drag", "Pan the map when no pick or zoom-window mode is active."),
            ("Mouse wheel", "Zoom in or out at the cursor position."),
            ("Right click", "Open the cursor menu for view controls, line editor actions, pick tools, and scale options."),
            ("Pick start/end", "In line generation or line editing, use PICK buttons and click the map to fill start/end coordinates."),
        ]),
        ("Settings Tabs", [
            ("Project", "Select project folder and available grid/background file. Load recorded RAW, edited EDIT, or sorted SORT data as a muted map background layer. RAW logs are saved in the selected project RAW folder."),
            ("GNSS settings", "Enable/disable GNSS, select port, baud rate, data format, NMEA sentences, and recording update rate."),
            ("Sensor (SBES)", "Enable/disable SBES, select port, baud rate, SBES sentence type, accepted sentences, and recording update rate."),
            ("Corrections", "Set Tide, Draft, and Sound Velocity corrections. These are applied when settings are applied."),
            ("Geodetic", "Select WGS84 UTM zones 1-60 (North/South), Lambert Conical IA/IIA, or custom projection parameters."),
            ("Devices", "Add, remove, test, enable, and configure additional serial devices."),
        ]),
        ("Keyboard Shortcuts", [
            ("Ctrl+S", "Start logging."),
            ("Ctrl+E", "Stop logging and save RAW data."),
            ("Ctrl+I", "Increase/select next runline number."),
            ("Ctrl+D", "Decrease/select previous runline number."),
            ("Ctrl+W", "Swap selected runline direction."),
        ]),
    ]

    class ToolTip:
        def __init__(self, widget: Any, text: str) -> None:
            self.widget = widget
            self.text = text
            self.tip: Optional[tk.Toplevel] = None
            widget.bind("<Enter>", self.show)
            widget.bind("<Leave>", self.hide)
            widget.bind("<ButtonPress>", self.hide)

        def show(self, _event: Any = None) -> None:
            if self.tip is not None or not self.text:
                return
            try:
                x = self.widget.winfo_rootx() + 18
                y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
                self.tip = tk.Toplevel(self.widget)
                self.tip.wm_overrideredirect(True)
                self.tip.wm_geometry(f"+{x}+{y}")
                label = ttk.Label(self.tip, text=self.text, wraplength=360, justify="left", padding=(8, 5), relief="solid", borderwidth=1)
                label.pack()
            except Exception:
                self.tip = None

        def hide(self, _event: Any = None) -> None:
            if self.tip is not None:
                try:
                    self.tip.destroy()
                except Exception:
                    pass
                self.tip = None

    def show_mbes_dialog(initial_tab: str = "") -> None:
        paths = mbes_project_paths(settings.raw_folder)
        setup_path = paths["logs"] / "mbes_hardware_setup.json"
        if setup_path.exists():
            try:
                mbes_setup = MBESHardwareSetup.from_dict(json.loads(setup_path.read_text(encoding="utf-8")))
            except Exception:
                mbes_setup = MBESHardwareSetup(project_folder=str(project_root_from_raw_folder(settings.raw_folder)))
        else:
            mbes_setup = MBESHardwareSetup(project_folder=str(project_root_from_raw_folder(settings.raw_folder)))
        mbes_logger_state: dict[str, Any] = {"logger": None}

        win = tk.Toplevel(root)
        win.title("MULTIBEAM SETUP / LOGGING / POST PROCESSING")
        win.geometry("1020x720")
        win.minsize(840, 560)

        status = tk.StringVar(value="Ready")
        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)
        setup_tab = ttk.Frame(notebook, padding=10)
        log_tab = ttk.Frame(notebook, padding=10)
        routing_tab = ttk.Frame(notebook, padding=10)
        process_tab = ttk.Frame(notebook, padding=10)
        processor_tab = ttk.Frame(notebook, padding=10)
        hysweep_tab = ttk.Frame(notebook, padding=10)
        export_tab = ttk.Frame(notebook, padding=10)
        visual_tab = ttk.Frame(notebook, padding=10)
        professional_tab = ttk.Frame(notebook, padding=10)
        protocol_tab = ttk.Frame(notebook, padding=10)
        notebook.add(setup_tab, text="Hardware Setup")
        notebook.add(log_tab, text="Raw Logging")
        notebook.add(routing_tab, text="Routing / Realtime")
        notebook.add(process_tab, text="Post Processing")
        notebook.add(processor_tab, text="HYDROGEOMATICS Processor")
        notebook.add(hysweep_tab, text="Multi-Beam Sys")
        notebook.add(export_tab, text="Export")
        notebook.add(visual_tab, text="Visualization")
        notebook.add(professional_tab, text="Professional Processing")
        notebook.add(protocol_tab, text="Protocol")

        def make_string(value: object = "") -> tk.StringVar:
            return tk.StringVar(value=str(value if value is not None else ""))

        vars_map: dict[str, Any] = {
            "project_folder": make_string(mbes_setup.project_folder),
            "survey_name": make_string(mbes_setup.survey_name),
            "vessel_name": make_string(mbes_setup.vessel_name),
            "operator_name": make_string(mbes_setup.operator_name),
            "hardware_raw_profile": make_string(getattr(mbes_setup, "hardware_raw_profile", "Kongsberg ALL/KMALL")),
            "sonar_make_model": make_string(mbes_setup.sonar_make_model),
            "native_raw_format": make_string(mbes_setup.native_raw_format),
            "positioning_model": make_string(mbes_setup.positioning_model),
            "motion_model": make_string(mbes_setup.motion_model),
            "heading_model": make_string(mbes_setup.heading_model),
            "svp_sensor_model": make_string(mbes_setup.svp_sensor_model),
            "tide_source": make_string(mbes_setup.tide_source),
            "time_sync_source": make_string(mbes_setup.time_sync_source),
            "horizontal_crs": make_string(mbes_setup.horizontal_crs),
            "vertical_datum": make_string(mbes_setup.vertical_datum),
            "require_sonar": tk.BooleanVar(value=bool(mbes_setup.require_sonar)),
            "require_positioning": tk.BooleanVar(value=bool(mbes_setup.require_positioning)),
            "require_motion": tk.BooleanVar(value=bool(mbes_setup.require_motion)),
            "require_heading": tk.BooleanVar(value=bool(mbes_setup.require_heading)),
            "require_svp": tk.BooleanVar(value=bool(mbes_setup.require_svp)),
            "require_tide": tk.BooleanVar(value=bool(mbes_setup.require_tide)),
            "require_time_sync": tk.BooleanVar(value=bool(mbes_setup.require_time_sync)),
            "sonar_udp_port": tk.IntVar(value=int(mbes_setup.sonar_udp_port)),
            "nmea_udp_port": tk.IntVar(value=int(mbes_setup.nmea_udp_port)),
            "motion_udp_port": tk.IntVar(value=int(mbes_setup.motion_udp_port)),
            "heading_udp_port": tk.IntVar(value=int(mbes_setup.heading_udp_port)),
            "log_sonar": tk.BooleanVar(value=bool(mbes_setup.log_sonar)),
            "log_nmea": tk.BooleanVar(value=bool(mbes_setup.log_nmea)),
            "log_motion": tk.BooleanVar(value=bool(mbes_setup.log_motion)),
            "log_heading": tk.BooleanVar(value=bool(mbes_setup.log_heading)),
            "gnss_route_enabled": tk.BooleanVar(value=bool(mbes_setup.gnss_route_enabled)),
            "gnss_serial_port": make_string(mbes_setup.gnss_serial_port),
            "gnss_baud_rate": tk.IntVar(value=int(mbes_setup.gnss_baud_rate)),
            "gnss_output_protocol": make_string(mbes_setup.gnss_output_protocol),
            "gnss_output_host": make_string(mbes_setup.gnss_output_host),
            "gnss_output_port": tk.IntVar(value=int(mbes_setup.gnss_output_port)),
            "sbes_route_enabled": tk.BooleanVar(value=bool(mbes_setup.sbes_route_enabled)),
            "sbes_serial_port": make_string(mbes_setup.sbes_serial_port),
            "sbes_baud_rate": tk.IntVar(value=int(mbes_setup.sbes_baud_rate)),
            "sbes_output_protocol": make_string(mbes_setup.sbes_output_protocol),
            "sbes_output_host": make_string(mbes_setup.sbes_output_host),
            "sbes_output_port": tk.IntVar(value=int(mbes_setup.sbes_output_port)),
            "transducer_x_m": make_string(mbes_setup.transducer_x_m),
            "transducer_y_m": make_string(mbes_setup.transducer_y_m),
            "transducer_z_m": make_string(mbes_setup.transducer_z_m),
            "antenna_x_m": make_string(mbes_setup.antenna_x_m),
            "antenna_y_m": make_string(mbes_setup.antenna_y_m),
            "antenna_z_m": make_string(mbes_setup.antenna_z_m),
            "roll_bias_deg": make_string(mbes_setup.roll_bias_deg),
            "pitch_bias_deg": make_string(mbes_setup.pitch_bias_deg),
            "heading_bias_deg": make_string(mbes_setup.heading_bias_deg),
            "latency_s": make_string(mbes_setup.latency_s),
            "draft_m": make_string(mbes_setup.draft_m),
            "tide_m": make_string(mbes_setup.tide_m),
            "sound_speed_mps": make_string(mbes_setup.sound_speed_mps),
            "svp_file": make_string(mbes_setup.svp_file),
            "tide_file": make_string(mbes_setup.tide_file),
            "notes": make_string(mbes_setup.notes),
        }
        processor_profile_var = vars_map["hardware_raw_profile"]

        def collect_setup() -> MBESHardwareSetup:
            setup = MBESHardwareSetup()
            for key, var in vars_map.items():
                value = var.get()
                current = getattr(setup, key)
                try:
                    if isinstance(current, bool):
                        setattr(setup, key, bool(value))
                    elif isinstance(current, int) and not isinstance(current, bool):
                        setattr(setup, key, int(value))
                    elif isinstance(current, float):
                        setattr(setup, key, float(value or 0.0))
                    else:
                        setattr(setup, key, str(value))
                except Exception:
                    setattr(setup, key, value)
            return setup

        def save_mbes_setup(show_message: bool = True) -> MBESHardwareSetup:
            setup = collect_setup()
            setup_paths = mbes_project_paths(setup.project_folder)
            target = setup_paths["logs"] / "mbes_hardware_setup.json"
            target.write_text(json.dumps(setup.to_dict(), indent=2), encoding="utf-8")
            if show_message:
                status.set(f"Saved MBES setup: {target}")
            return setup

        def choose_project_folder() -> None:
            folder = filedialog.askdirectory(title="Select MBES project folder", initialdir=vars_map["project_folder"].get() or str(APP_DIR), parent=win)
            if folder:
                vars_map["project_folder"].set(folder)
                status.set(f"Project folder selected: {folder}")

        def choose_file(var_name: str, title: str) -> None:
            name = filedialog.askopenfilename(title=title, initialdir=vars_map["project_folder"].get() or str(APP_DIR), parent=win)
            if name:
                vars_map[var_name].set(name)

        def apply_hardware_profile_to_setup(*_args: object) -> None:
            profile = MBES_PROCESSOR_PROFILES.get(vars_map["hardware_raw_profile"].get(), {})
            if not profile:
                return
            profile_name = vars_map["hardware_raw_profile"].get()
            vars_map["sonar_make_model"].set(str(profile.get("hardware", vars_map["sonar_make_model"].get())))
            raw_formats = [str(item) for item in profile.get("raw_formats", [])]
            if raw_formats:
                vars_map["native_raw_format"].set(" / ".join(raw_formats[:4]))
            if profile_name == "Cerulean Surveyor 240-16":
                if not vars_map["positioning_model"].get().strip():
                    vars_map["positioning_model"].set("SonarView NMEA or MAVLink2Rest vehicle position")
                if not vars_map["motion_model"].get().strip():
                    vars_map["motion_model"].set("Surveyor built-in IMU pitch/roll")
                if not vars_map["heading_model"].get().strip():
                    vars_map["heading_model"].set("SonarView NMEA heading or vehicle heading")
                if not vars_map["time_sync_source"].get().strip():
                    vars_map["time_sync_source"].set("NTP via SonarView/SonarLink or GPS NTP server")
                current_notes = vars_map["notes"].get().strip()
                cerulean_note = "Cerulean Surveyor: log with SonarView/SonarLink, preserve .svlog/.svlz, export CSV point cloud for HYDROGEOMATICS, and validate beta GSF before certification use."
                if cerulean_note not in current_notes:
                    vars_map["notes"].set((current_notes + "\n" + cerulean_note).strip())
            status.set(f"Applied MBES hardware/raw profile: {profile_name}")

        setup_grid = ttk.Frame(setup_tab)
        setup_grid.pack(fill="x")
        ttk.Label(setup_grid, text="Hardware / raw profile").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        hardware_profile_combo = ttk.Combobox(
            setup_grid,
            textvariable=vars_map["hardware_raw_profile"],
            values=list(MBES_PROCESSOR_PROFILES.keys()),
            state="readonly",
            width=38,
        )
        hardware_profile_combo.grid(row=0, column=1, columnspan=3, sticky="ew", padx=4, pady=4)
        hardware_profile_combo.bind("<<ComboboxSelected>>", apply_hardware_profile_to_setup)
        ttk.Button(setup_grid, text="Apply Profile", command=apply_hardware_profile_to_setup).grid(row=0, column=4, sticky="ew", padx=4, pady=4)
        setup_fields = [
            ("Project folder", "project_folder", 70),
            ("Survey name", "survey_name", 35),
            ("Vessel", "vessel_name", 35),
            ("Operator", "operator_name", 35),
            ("Sonar make / model", "sonar_make_model", 35),
            ("Native raw format", "native_raw_format", 35),
            ("Positioning system", "positioning_model", 35),
            ("Motion system", "motion_model", 35),
            ("Heading system", "heading_model", 35),
            ("SVP / sound speed", "svp_sensor_model", 35),
            ("Tide source", "tide_source", 35),
            ("Time sync source", "time_sync_source", 35),
            ("Horizontal CRS", "horizontal_crs", 35),
            ("Vertical datum", "vertical_datum", 35),
        ]
        for row, (label, key, width) in enumerate(setup_fields, start=1):
            ttk.Label(setup_grid, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=4)
            ttk.Entry(setup_grid, textvariable=vars_map[key], width=width).grid(row=row, column=1, columnspan=3, sticky="ew", padx=4, pady=4)
        ttk.Button(setup_grid, text="Browse", command=choose_project_folder).grid(row=1, column=4, sticky="ew", padx=4, pady=4)
        setup_grid.columnconfigure(1, weight=1)

        component_frame = ttk.LabelFrame(setup_tab, text="Required system components", padding=8)
        component_frame.pack(fill="x", pady=(10, 0))
        component_checks = [
            ("MBES sonar", "require_sonar"),
            ("GNSS / positioning", "require_positioning"),
            ("Motion / heave-pitch-roll", "require_motion"),
            ("Heading", "require_heading"),
            ("SVP / sound velocity", "require_svp"),
            ("Tide / water level", "require_tide"),
            ("Time sync UTC/PPS/NTP", "require_time_sync"),
        ]
        for index, (label, key) in enumerate(component_checks):
            ttk.Checkbutton(component_frame, text=label, variable=vars_map[key]).grid(row=index // 4, column=index % 4, sticky="w", padx=8, pady=3)

        ports_frame = ttk.LabelFrame(setup_tab, text="UDP sources", padding=8)
        ports_frame.pack(fill="x", pady=(10, 0))
        port_rows = [
            ("Native sonar packets", "log_sonar", "sonar_udp_port"),
            ("NMEA / navigation", "log_nmea", "nmea_udp_port"),
            ("Motion / MRU", "log_motion", "motion_udp_port"),
            ("Heading", "log_heading", "heading_udp_port"),
        ]
        for row, (label, enable_key, port_key) in enumerate(port_rows):
            ttk.Checkbutton(ports_frame, text=label, variable=vars_map[enable_key]).grid(row=row, column=0, sticky="w", padx=4, pady=3)
            ttk.Label(ports_frame, text="UDP port").grid(row=row, column=1, sticky="e", padx=4, pady=3)
            ttk.Entry(ports_frame, textvariable=vars_map[port_key], width=10).grid(row=row, column=2, sticky="w", padx=4, pady=3)

        calibration = ttk.LabelFrame(setup_tab, text="Offsets, patch test, and corrections", padding=8)
        calibration.pack(fill="x", pady=(10, 0))
        cal_fields = [
            ("TX X", "transducer_x_m"), ("TX Y", "transducer_y_m"), ("TX Z", "transducer_z_m"),
            ("ANT X", "antenna_x_m"), ("ANT Y", "antenna_y_m"), ("ANT Z", "antenna_z_m"),
            ("Roll bias", "roll_bias_deg"), ("Pitch bias", "pitch_bias_deg"), ("Heading bias", "heading_bias_deg"),
            ("Latency s", "latency_s"), ("Draft m", "draft_m"), ("Tide m", "tide_m"), ("Sound speed", "sound_speed_mps"),
        ]
        for index, (label, key) in enumerate(cal_fields):
            row = index // 4
            col = (index % 4) * 2
            ttk.Label(calibration, text=label).grid(row=row, column=col, sticky="w", padx=4, pady=3)
            ttk.Entry(calibration, textvariable=vars_map[key], width=12).grid(row=row, column=col + 1, sticky="ew", padx=4, pady=3)

        files_frame = ttk.LabelFrame(setup_tab, text="SVP and tide support files", padding=8)
        files_frame.pack(fill="x", pady=(10, 0))
        ttk.Label(files_frame, text="SVP file").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(files_frame, textvariable=vars_map["svp_file"]).grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(files_frame, text="Browse", command=lambda: choose_file("svp_file", "Select sound velocity profile")).grid(row=0, column=2, padx=4, pady=3)
        ttk.Label(files_frame, text="Tide file").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(files_frame, textvariable=vars_map["tide_file"]).grid(row=1, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(files_frame, text="Browse", command=lambda: choose_file("tide_file", "Select tide file")).grid(row=1, column=2, padx=4, pady=3)
        files_frame.columnconfigure(1, weight=1)
        ttk.Label(setup_tab, text="Notes").pack(anchor="w", pady=(10, 2))
        ttk.Entry(setup_tab, textvariable=vars_map["notes"]).pack(fill="x")

        check_frame = ttk.LabelFrame(setup_tab, text="Hardware selection and connectivity checks", padding=8)
        check_frame.pack(fill="both", expand=True, pady=(10, 0))
        check_buttons = ttk.Frame(check_frame)
        check_buttons.pack(fill="x", pady=(0, 6))
        probe_seconds_var = tk.StringVar(value="5")
        ttk.Label(check_buttons, text="Probe seconds").pack(side="left", padx=(0, 4))
        ttk.Entry(check_buttons, textvariable=probe_seconds_var, width=6).pack(side="left", padx=(0, 8))
        check_tree = ttk.Treeview(check_frame, columns=("status", "item", "detail"), show="headings", height=8)
        check_tree.heading("status", text="Status")
        check_tree.heading("item", text="Check")
        check_tree.heading("detail", text="Detail")
        check_tree.column("status", width=72, anchor="center")
        check_tree.column("item", width=210, anchor="w")
        check_tree.column("detail", width=620, anchor="w")
        check_scroll = ttk.Scrollbar(check_frame, orient="vertical", command=check_tree.yview)
        check_tree.configure(yscrollcommand=check_scroll.set)
        check_tree.pack(side="left", fill="both", expand=True)
        check_scroll.pack(side="right", fill="y")

        def populate_check_tree(results: list[dict[str, str]]) -> None:
            check_tree.delete(*check_tree.get_children())
            priority = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}
            for result in sorted(results, key=lambda item: priority.get(item.get("status", "INFO"), 4)):
                check_tree.insert("", "end", values=(result.get("status", ""), result.get("item", ""), result.get("detail", "")))
            fails = sum(1 for item in results if item.get("status") == "FAIL")
            warns = sum(1 for item in results if item.get("status") == "WARN")
            passes = sum(1 for item in results if item.get("status") == "PASS")
            status.set(f"MBES checks: {passes} pass, {warns} warning, {fails} fail")

        def run_static_mbes_checks() -> None:
            setup = save_mbes_setup(show_message=False)
            populate_check_tree(mbes_validate_hardware_setup(setup))

        def run_live_mbes_probe() -> None:
            setup = save_mbes_setup(show_message=False)
            seconds = parse_float(probe_seconds_var.get()) or 5.0
            status.set(f"Listening for MBES UDP packets for {seconds:.1f} seconds...")
            def worker() -> None:
                static_results = mbes_validate_hardware_setup(setup)
                live_results = mbes_probe_udp_connectivity(setup, seconds)
                root.after(0, lambda: populate_check_tree(static_results + live_results))
            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(check_buttons, text="Save Hardware Setup", command=save_mbes_setup).pack(side="left", padx=4)
        ttk.Button(check_buttons, text="Check Configuration", command=run_static_mbes_checks).pack(side="left", padx=4)
        ttk.Button(check_buttons, text="Listen for Live Data", command=run_live_mbes_probe).pack(side="left", padx=4)

        log_info = tk.Text(log_tab, height=16, wrap="word", bg="#f8fafc", fg="#111827")
        log_info.pack(fill="both", expand=True)

        def append_log(message: str) -> None:
            status.set(message)
            try:
                log_info.insert("end", f"{datetime.now().strftime('%H:%M:%S')}  {message}\n")
                log_info.see("end")
            except Exception:
                pass

        def start_mbes_logging() -> None:
            if mbes_logger_state.get("logger") is not None:
                append_log("MBES logger is already running.")
                return
            setup = save_mbes_setup(show_message=False)
            setup_checks = mbes_validate_hardware_setup(setup)
            failures = [item for item in setup_checks if item.get("status") == "FAIL"]
            if failures:
                populate_check_tree(setup_checks)
                message = "\n".join(f"{item.get('item')}: {item.get('detail')}" for item in failures[:8])
                if not messagebox.askyesno("MBES setup check", f"Required MBES setup checks failed:\n\n{message}\n\nStart logging anyway?", parent=win):
                    append_log("MBES logging cancelled because setup checks failed.")
                    return
            logger = MBESRawLogger(setup, status_callback=lambda msg: root.after(0, append_log, msg))
            if not logger.sources():
                messagebox.showwarning("MBES", "Enable at least one UDP source.", parent=win)
                return
            try:
                logger.start()
            except Exception as exc:
                messagebox.showerror("MBES", f"Could not start MBES logger:\n{exc}", parent=win)
                return
            mbes_logger_state["logger"] = logger
            append_log("Raw packet logging is active. Preserve these files as the native acquisition record.")

        def stop_mbes_logging() -> None:
            logger = mbes_logger_state.get("logger")
            if logger is None:
                append_log("MBES logger is not running.")
                return
            logger.stop()
            while not logger.errors.empty():
                append_log("ERROR: " + logger.errors.get())
            mbes_logger_state["logger"] = None

        log_buttons = ttk.Frame(log_tab)
        log_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(log_buttons, text="Start MBES Raw Logging", command=start_mbes_logging).pack(side="left", padx=4)
        ttk.Button(log_buttons, text="Stop MBES Logging", command=stop_mbes_logging).pack(side="left", padx=4)
        ttk.Button(log_buttons, text="Open Project Folder", command=lambda: os.startfile(str(Path(vars_map["project_folder"].get())))).pack(side="left", padx=4)

        route_state: dict[str, Any] = {"stops": {}, "threads": {}}

        routing_info = tk.Text(routing_tab, height=8, wrap="word", bg="#f8fafc", fg="#111827")
        routing_info.pack(fill="x", pady=(0, 8))

        def append_route(message: str) -> None:
            status.set(message)
            try:
                routing_info.insert("end", f"{datetime.now().strftime('%H:%M:%S')}  {message}\n")
                routing_info.see("end")
            except Exception:
                pass

        route_grid = ttk.LabelFrame(routing_tab, text="Serial data routing into MBES UDP/TCP feeds", padding=8)
        route_grid.pack(fill="x")
        route_headers = ["Enable", "Source", "Serial port", "Baud", "Output", "Host", "Port"]
        for col, label in enumerate(route_headers):
            ttk.Label(route_grid, text=label, font=("Segoe UI", 9, "bold")).grid(row=0, column=col, sticky="w", padx=4, pady=3)
        route_rows = [
            ("GNSS", "gnss_route_enabled", "gnss_serial_port", "gnss_baud_rate", "gnss_output_protocol", "gnss_output_host", "gnss_output_port"),
            ("SBES", "sbes_route_enabled", "sbes_serial_port", "sbes_baud_rate", "sbes_output_protocol", "sbes_output_host", "sbes_output_port"),
        ]
        for row, (label, enabled_key, serial_key, baud_key, proto_key, host_key, port_key) in enumerate(route_rows, start=1):
            ttk.Checkbutton(route_grid, variable=vars_map[enabled_key]).grid(row=row, column=0, sticky="w", padx=4, pady=3)
            ttk.Label(route_grid, text=label).grid(row=row, column=1, sticky="w", padx=4, pady=3)
            ttk.Entry(route_grid, textvariable=vars_map[serial_key], width=12).grid(row=row, column=2, sticky="ew", padx=4, pady=3)
            ttk.Entry(route_grid, textvariable=vars_map[baud_key], width=10).grid(row=row, column=3, sticky="ew", padx=4, pady=3)
            ttk.Combobox(route_grid, textvariable=vars_map[proto_key], values=["UDP", "TCP"], state="readonly", width=7).grid(row=row, column=4, sticky="ew", padx=4, pady=3)
            ttk.Entry(route_grid, textvariable=vars_map[host_key], width=16).grid(row=row, column=5, sticky="ew", padx=4, pady=3)
            ttk.Entry(route_grid, textvariable=vars_map[port_key], width=10).grid(row=row, column=6, sticky="ew", padx=4, pady=3)
        route_grid.columnconfigure(2, weight=1)
        route_grid.columnconfigure(5, weight=1)

        def send_routed_payload(protocol: str, host: str, port: int, payload: bytes, tcp_cache: dict[str, Any]) -> None:
            proto = str(protocol or "UDP").upper()
            if proto == "TCP":
                key = f"{host}:{port}"
                sock = tcp_cache.get(key)
                if sock is None:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2.0)
                    sock.connect((host, int(port)))
                    tcp_cache[key] = sock
                sock.sendall(payload)
            else:
                sock = tcp_cache.get("udp")
                if sock is None:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    tcp_cache["udp"] = sock
                sock.sendto(payload, (host, int(port)))

        def start_serial_route(route_name: str) -> None:
            if route_name in route_state["threads"]:
                append_route(f"{route_name.upper()} serial route already running.")
                return
            setup = save_mbes_setup(show_message=False)
            if route_name == "gnss":
                enabled = setup.gnss_route_enabled
                serial_port = setup.gnss_serial_port
                baud = setup.gnss_baud_rate
                protocol = setup.gnss_output_protocol
                host = setup.gnss_output_host
                out_port = setup.gnss_output_port
            else:
                enabled = setup.sbes_route_enabled
                serial_port = setup.sbes_serial_port
                baud = setup.sbes_baud_rate
                protocol = setup.sbes_output_protocol
                host = setup.sbes_output_host
                out_port = setup.sbes_output_port
            if not enabled:
                append_route(f"{route_name.upper()} route is disabled.")
                return
            stop_event = threading.Event()
            route_state["stops"][route_name] = stop_event

            def worker() -> None:
                tcp_cache: dict[str, Any] = {}
                try:
                    try:
                        import serial  # type: ignore
                    except Exception as exc:
                        root.after(0, append_route, f"{route_name.upper()} route cannot start; pyserial not available: {exc}")
                        return
                    with serial.Serial(port=serial_port, baudrate=int(baud), bytesize=8, parity="N", stopbits=1, timeout=0.35) as port_obj:
                        root.after(0, append_route, f"{route_name.upper()} serial route started: {serial_port} -> {protocol} {host}:{out_port}")
                        while not stop_event.is_set():
                            payload = port_obj.readline()
                            if not payload:
                                continue
                            try:
                                send_routed_payload(protocol, host, int(out_port), payload, tcp_cache)
                                preview = payload.decode("ascii", errors="replace").strip()
                                root.after(0, append_route, f"{route_name.upper()} routed {len(payload)} bytes: {preview[:120]}")
                            except Exception as exc:
                                root.after(0, append_route, f"{route_name.upper()} route send error: {exc}")
                                time.sleep(0.5)
                except Exception as exc:
                    root.after(0, append_route, f"{route_name.upper()} route stopped with error: {exc}")
                finally:
                    for sock in tcp_cache.values():
                        try:
                            sock.close()
                        except Exception:
                            pass
                    route_state["threads"].pop(route_name, None)
                    route_state["stops"].pop(route_name, None)
                    root.after(0, append_route, f"{route_name.upper()} serial route stopped.")

            thread = threading.Thread(target=worker, daemon=True)
            route_state["threads"][route_name] = thread
            thread.start()

        def stop_serial_route(route_name: str) -> None:
            stop_event = route_state["stops"].get(route_name)
            if stop_event is None:
                append_route(f"{route_name.upper()} route is not running.")
                return
            stop_event.set()

        def stop_all_routes() -> None:
            for stop_event in list(route_state["stops"].values()):
                stop_event.set()

        def use_existing_gnss_route_settings() -> None:
            vars_map["gnss_serial_port"].set(settings.gps.port)
            vars_map["gnss_baud_rate"].set(int(settings.gps.baud_rate))
            vars_map["gnss_route_enabled"].set(True)
            vars_map["gnss_output_protocol"].set("UDP")
            vars_map["gnss_output_port"].set(int(vars_map["nmea_udp_port"].get()))
            append_route(f"GNSS route copied from main GNSS settings: {settings.gps.port} {settings.gps.baud_rate} -> UDP {vars_map['nmea_udp_port'].get()}")

        def use_existing_sbes_route_settings() -> None:
            vars_map["sbes_serial_port"].set(settings.sensor.port)
            vars_map["sbes_baud_rate"].set(int(settings.sensor.baud_rate))
            vars_map["sbes_route_enabled"].set(True)
            vars_map["sbes_output_protocol"].set("UDP")
            append_route(f"SBES route copied from main SBES settings: {settings.sensor.port} {settings.sensor.baud_rate} -> UDP {vars_map['sbes_output_port'].get()}")

        route_buttons = ttk.Frame(routing_tab)
        route_buttons.pack(fill="x", pady=(8, 8))
        ttk.Button(route_buttons, text="Use Current GNSS Settings", command=use_existing_gnss_route_settings).pack(side="left", padx=4)
        ttk.Button(route_buttons, text="Use Current SBES Settings", command=use_existing_sbes_route_settings).pack(side="left", padx=4)
        ttk.Button(route_buttons, text="Start GNSS Route", command=lambda: start_serial_route("gnss")).pack(side="left", padx=4)
        ttk.Button(route_buttons, text="Stop GNSS Route", command=lambda: stop_serial_route("gnss")).pack(side="left", padx=4)
        ttk.Button(route_buttons, text="Start SBES Route", command=lambda: start_serial_route("sbes")).pack(side="left", padx=4)
        ttk.Button(route_buttons, text="Stop SBES Route", command=lambda: stop_serial_route("sbes")).pack(side="left", padx=4)
        ttk.Button(route_buttons, text="Stop All Routes", command=stop_all_routes).pack(side="left", padx=4)

        popup_frame = ttk.LabelFrame(routing_tab, text="Realtime hardware display popups", padding=8)
        popup_frame.pack(fill="x", pady=(4, 0))

        def open_serial_popup(title: str, port_name: str, baud_rate: int) -> None:
            popup = tk.Toplevel(win)
            popup.title(f"MBES Realtime - {title}")
            popup.geometry("760x420")
            popup_stop = threading.Event()
            text = tk.Text(popup, wrap="none", bg="#0b1220", fg="#d8f3ff", insertbackground="#d8f3ff")
            text.pack(fill="both", expand=True)
            def write_line(line: str) -> None:
                try:
                    text.insert("end", line + "\n")
                    text.see("end")
                except Exception:
                    pass
            def worker() -> None:
                try:
                    try:
                        import serial  # type: ignore
                    except Exception as exc:
                        root.after(0, write_line, f"pyserial not available: {exc}")
                        return
                    with serial.Serial(port=port_name, baudrate=int(baud_rate), bytesize=8, parity="N", stopbits=1, timeout=0.35) as port_obj:
                        root.after(0, write_line, f"Listening to {title}: {port_name} {baud_rate}")
                        while not popup_stop.is_set():
                            payload = port_obj.readline()
                            if payload:
                                root.after(0, write_line, payload.decode("ascii", errors="replace").strip())
                except Exception as exc:
                    root.after(0, write_line, f"Stopped: {exc}")
            def on_close() -> None:
                popup_stop.set()
                popup.destroy()
            popup.protocol("WM_DELETE_WINDOW", on_close)
            threading.Thread(target=worker, daemon=True).start()

        def open_udp_popup(title: str, port_number: int) -> None:
            popup = tk.Toplevel(win)
            popup.title(f"MBES Realtime - {title}")
            popup.geometry("760x420")
            popup_stop = threading.Event()
            text = tk.Text(popup, wrap="none", bg="#0b1220", fg="#d8f3ff", insertbackground="#d8f3ff")
            text.pack(fill="both", expand=True)
            def write_line(line: str) -> None:
                try:
                    text.insert("end", line + "\n")
                    text.see("end")
                except Exception:
                    pass
            def worker() -> None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.settimeout(0.35)
                try:
                    sock.bind(("0.0.0.0", int(port_number)))
                    root.after(0, write_line, f"Listening to {title} UDP {port_number}")
                    while not popup_stop.is_set():
                        try:
                            payload, addr = sock.recvfrom(65535)
                        except socket.timeout:
                            continue
                        preview = payload.decode("ascii", errors="replace").strip()
                        if not preview:
                            preview = payload[:48].hex(" ")
                        root.after(0, write_line, f"{addr[0]}:{addr[1]} | {len(payload)} bytes | {preview[:220]}")
                except Exception as exc:
                    root.after(0, write_line, f"Stopped: {exc}")
                finally:
                    sock.close()
            def on_close() -> None:
                popup_stop.set()
                popup.destroy()
            popup.protocol("WM_DELETE_WINDOW", on_close)
            threading.Thread(target=worker, daemon=True).start()

        def open_tcp_server_popup(title: str, port_number: int) -> None:
            popup = tk.Toplevel(win)
            popup.title(f"MBES Realtime - {title}")
            popup.geometry("760x420")
            popup_stop = threading.Event()
            text = tk.Text(popup, wrap="none", bg="#0b1220", fg="#d8f3ff", insertbackground="#d8f3ff")
            text.pack(fill="both", expand=True)
            def write_line(line: str) -> None:
                try:
                    text.insert("end", line + "\n")
                    text.see("end")
                except Exception:
                    pass
            def worker() -> None:
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.settimeout(0.35)
                try:
                    server.bind(("0.0.0.0", int(port_number)))
                    server.listen(5)
                    root.after(0, write_line, f"Listening to {title} TCP {port_number}")
                    clients: list[socket.socket] = []
                    while not popup_stop.is_set():
                        try:
                            client, addr = server.accept()
                            client.settimeout(0.05)
                            clients.append(client)
                            root.after(0, write_line, f"TCP client connected: {addr[0]}:{addr[1]}")
                        except socket.timeout:
                            pass
                        for client in list(clients):
                            try:
                                payload = client.recv(4096)
                                if not payload:
                                    clients.remove(client)
                                    client.close()
                                    continue
                                preview = payload.decode("ascii", errors="replace").strip()
                                root.after(0, write_line, f"{len(payload)} bytes | {preview[:220]}")
                            except socket.timeout:
                                continue
                            except Exception:
                                try:
                                    clients.remove(client)
                                    client.close()
                                except Exception:
                                    pass
                    for client in clients:
                        try:
                            client.close()
                        except Exception:
                            pass
                except Exception as exc:
                    root.after(0, write_line, f"Stopped: {exc}")
                finally:
                    server.close()
            def on_close() -> None:
                popup_stop.set()
                popup.destroy()
            popup.protocol("WM_DELETE_WINDOW", on_close)
            threading.Thread(target=worker, daemon=True).start()

        def open_component_popup(component: str) -> None:
            setup = collect_setup()
            if component == "GNSS":
                open_serial_popup("GNSS Serial Input", setup.gnss_serial_port, setup.gnss_baud_rate)
            elif component == "SBES":
                open_serial_popup("SBES Serial Input", setup.sbes_serial_port, setup.sbes_baud_rate)
            elif component == "SONAR":
                open_udp_popup("MBES Sonar Native Packets", setup.sonar_udp_port)
            elif component == "MOTION":
                open_udp_popup("Motion / MRU", setup.motion_udp_port)
            elif component == "HEADING":
                open_udp_popup("Heading", setup.heading_udp_port)
            elif component == "GNSS_ROUTE":
                if str(setup.gnss_output_protocol).upper() == "TCP":
                    open_tcp_server_popup("GNSS Routed MBES Feed", setup.gnss_output_port)
                else:
                    open_udp_popup("GNSS Routed MBES Feed", setup.gnss_output_port)
            elif component == "SBES_ROUTE":
                if str(setup.sbes_output_protocol).upper() == "TCP":
                    open_tcp_server_popup("SBES Routed MBES Feed", setup.sbes_output_port)
                else:
                    open_udp_popup("SBES Routed MBES Feed", setup.sbes_output_port)

        popup_buttons = [
            ("GNSS Serial Popup", "GNSS"),
            ("SBES Serial Popup", "SBES"),
            ("GNSS Routed UDP Popup", "GNSS_ROUTE"),
            ("SBES Routed UDP Popup", "SBES_ROUTE"),
            ("Sonar UDP Popup", "SONAR"),
            ("Motion UDP Popup", "MOTION"),
            ("Heading UDP Popup", "HEADING"),
        ]
        for index, (label, component) in enumerate(popup_buttons):
            ttk.Button(popup_frame, text=label, command=lambda c=component: open_component_popup(c)).grid(row=index // 3, column=index % 3, sticky="ew", padx=4, pady=4)
        for col in range(3):
            popup_frame.columnconfigure(col, weight=1)

        input_csv_var = tk.StringVar(value="")
        cleaned_csv_var = tk.StringVar(value=str(paths["processed"] / "mbes_cleaned_soundings.csv"))
        grid_csv_var = tk.StringVar(value=str(paths["processed"] / "mbes_grid.csv"))
        report_json_var = tk.StringVar(value=str(paths["processed"] / "mbes_qc_report.json"))
        min_depth_var = tk.StringVar(value="")
        max_depth_var = tk.StringVar(value="")
        cell_size_var = tk.StringVar(value="1.0")
        process_summary = tk.StringVar(value="Select a CSV/XYZ sounding export with x/y/depth columns.")

        def choose_process_input() -> None:
            name = filedialog.askopenfilename(
                title="Select MBES sounding CSV/XYZ",
                initialdir=vars_map["project_folder"].get() or str(APP_DIR),
                filetypes=[("Sounding CSV/XYZ", "*.csv *.xyz *.txt"), ("All files", "*.*")],
                parent=win,
            )
            if name:
                input_csv_var.set(name)

        def save_process_output(var: tk.StringVar, title: str, default_ext: str) -> None:
            name = filedialog.asksaveasfilename(title=title, defaultextension=default_ext, initialdir=vars_map["project_folder"].get() or str(APP_DIR), parent=win)
            if name:
                var.set(name)

        def run_mbes_processing() -> None:
            setup = save_mbes_setup(show_message=False)
            in_path = input_csv_var.get().strip()
            if not in_path:
                messagebox.showwarning("MBES", "Choose an input sounding file first.", parent=win)
                return
            min_depth = parse_float(min_depth_var.get()) if min_depth_var.get().strip() else None
            max_depth = parse_float(max_depth_var.get()) if max_depth_var.get().strip() else None
            try:
                report = mbes_process_sounding_csv(
                    in_path,
                    cleaned_csv_var.get(),
                    grid_csv_var.get(),
                    report_json_var.get(),
                    draft_m=parse_float(str(setup.draft_m)) or 0.0,
                    tide_m=parse_float(str(setup.tide_m)) or 0.0,
                    min_depth=min_depth,
                    max_depth=max_depth,
                    cell_size=parse_float(cell_size_var.get()) or 1.0,
                )
            except Exception as exc:
                messagebox.showerror("MBES", f"Post processing failed:\n{exc}", parent=win)
                return
            process_summary.set(
                f"Accepted {report['accepted_soundings']} of {report['total_soundings']} soundings; "
                f"grid cells {report['grid_cells']}; mean depth {report.get('mean_depth_m')}"
            )
            try:
                visual_source_var.set(cleaned_csv_var.get())
                load_visual_source()
                notebook.select(visual_tab)
            except Exception:
                pass
            status.set(f"MBES processing complete: {cleaned_csv_var.get()}")

        process_form = ttk.Frame(process_tab)
        process_form.pack(fill="x")
        process_rows = [
            ("Input soundings", input_csv_var, choose_process_input),
            ("Cleaned CSV", cleaned_csv_var, lambda: save_process_output(cleaned_csv_var, "Save cleaned MBES CSV", ".csv")),
            ("Grid CSV", grid_csv_var, lambda: save_process_output(grid_csv_var, "Save MBES grid CSV", ".csv")),
            ("QC report JSON", report_json_var, lambda: save_process_output(report_json_var, "Save MBES QC report", ".json")),
        ]
        for row, (label, var, command) in enumerate(process_rows):
            ttk.Label(process_form, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=4)
            ttk.Entry(process_form, textvariable=var).grid(row=row, column=1, sticky="ew", padx=4, pady=4)
            ttk.Button(process_form, text="Browse", command=command).grid(row=row, column=2, sticky="ew", padx=4, pady=4)
        process_form.columnconfigure(1, weight=1)
        filters = ttk.LabelFrame(process_tab, text="Corrections and filters", padding=8)
        filters.pack(fill="x", pady=(10, 0))
        ttk.Label(filters, text="Draft and tide are read from Hardware Setup.").grid(row=0, column=0, columnspan=6, sticky="w", padx=4, pady=(0, 6))
        ttk.Label(filters, text="Min depth").grid(row=1, column=0, sticky="w", padx=4)
        ttk.Entry(filters, textvariable=min_depth_var, width=12).grid(row=1, column=1, padx=4)
        ttk.Label(filters, text="Max depth").grid(row=1, column=2, sticky="w", padx=4)
        ttk.Entry(filters, textvariable=max_depth_var, width=12).grid(row=1, column=3, padx=4)
        ttk.Label(filters, text="Cell size").grid(row=1, column=4, sticky="w", padx=4)
        ttk.Entry(filters, textvariable=cell_size_var, width=12).grid(row=1, column=5, padx=4)
        ttk.Button(process_tab, text="Run MBES Post Processing", command=run_mbes_processing).pack(anchor="e", pady=(10, 0))
        ttk.Label(process_tab, textvariable=process_summary, wraplength=900, font=("Segoe UI", 10, "bold")).pack(fill="x", pady=(12, 0))

        export_source_var = tk.StringVar(value=cleaned_csv_var.get())
        export_xyz_var = tk.StringVar(value=str(paths["exports"] / "mbes_soundings.xyz"))
        export_geojson_var = tk.StringVar(value=str(paths["exports"] / "mbes_soundings.geojson"))
        export_status = tk.StringVar(value="Export cleaned accepted soundings to XYZ and GeoJSON.")

        def run_export_xyz() -> None:
            try:
                count = mbes_export_xyz_from_cleaned(export_source_var.get(), export_xyz_var.get())
            except Exception as exc:
                messagebox.showerror("MBES", f"XYZ export failed:\n{exc}", parent=win)
                return
            export_status.set(f"XYZ export wrote {count} soundings: {export_xyz_var.get()}")

        def run_export_geojson() -> None:
            try:
                count = mbes_export_geojson_from_cleaned(export_source_var.get(), export_geojson_var.get())
            except Exception as exc:
                messagebox.showerror("MBES", f"GeoJSON export failed:\n{exc}", parent=win)
                return
            export_status.set(f"GeoJSON export wrote {count} soundings: {export_geojson_var.get()}")

        export_form = ttk.Frame(export_tab)
        export_form.pack(fill="x")
        export_rows = [
            ("Cleaned CSV source", export_source_var, lambda: choose_export_source()),
            ("XYZ output", export_xyz_var, lambda: save_process_output(export_xyz_var, "Save MBES XYZ", ".xyz")),
            ("GeoJSON output", export_geojson_var, lambda: save_process_output(export_geojson_var, "Save MBES GeoJSON", ".geojson")),
        ]

        def choose_export_source() -> None:
            name = filedialog.askopenfilename(title="Select cleaned MBES CSV", initialdir=vars_map["project_folder"].get() or str(APP_DIR), filetypes=[("CSV", "*.csv"), ("All files", "*.*")], parent=win)
            if name:
                export_source_var.set(name)

        for row, (label, var, command) in enumerate(export_rows):
            ttk.Label(export_form, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=4)
            ttk.Entry(export_form, textvariable=var).grid(row=row, column=1, sticky="ew", padx=4, pady=4)
            ttk.Button(export_form, text="Browse", command=command).grid(row=row, column=2, padx=4, pady=4)
        export_form.columnconfigure(1, weight=1)
        ttk.Button(export_tab, text="Export XYZ", command=run_export_xyz).pack(anchor="e", pady=(12, 0))
        ttk.Button(export_tab, text="Export GeoJSON", command=run_export_geojson).pack(anchor="e", pady=(4, 0))
        ttk.Label(export_tab, textvariable=export_status, wraplength=900, font=("Segoe UI", 10, "bold")).pack(fill="x", pady=(12, 0))

        visual_source_var = tk.StringVar(value=cleaned_csv_var.get())
        visual_status = tk.StringVar(value="Load a cleaned MBES CSV or grid CSV to visualize soundings.")
        visual_state: dict[str, Any] = {"points": [], "zoom": 1.0, "pan_x": 0.0, "pan_y": 0.0}

        visual_top = ttk.Frame(visual_tab)
        visual_top.pack(fill="x", pady=(0, 8))
        ttk.Label(visual_top, text="Source CSV").pack(side="left", padx=(0, 4))
        ttk.Entry(visual_top, textvariable=visual_source_var).pack(side="left", fill="x", expand=True, padx=4)

        visual_canvas = tk.Canvas(visual_tab, bg="#101418", highlightthickness=0, height=420)
        visual_canvas.pack(fill="both", expand=True)

        def choose_visual_source() -> None:
            name = filedialog.askopenfilename(
                title="Select MBES cleaned/grid CSV",
                initialdir=vars_map["project_folder"].get() or str(APP_DIR),
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
                parent=win,
            )
            if name:
                visual_source_var.set(name)
                load_visual_source()

        def visual_float(row: dict[str, str], names: list[str]) -> Optional[float]:
            lowered = {str(key).strip().lower(): value for key, value in row.items()}
            for name in names:
                value = lowered.get(name.lower())
                if value in (None, ""):
                    continue
                try:
                    return float(str(value).strip())
                except Exception:
                    continue
            return None

        def read_visual_points(path: str) -> list[tuple[float, float, float]]:
            source = Path(path)
            points: list[tuple[float, float, float]] = []
            if not source.exists():
                raise FileNotFoundError(source)
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                sample = handle.read(4096)
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t ")
                except Exception:
                    dialect = csv.excel
                reader = csv.DictReader(handle, dialect=dialect)
                for row in reader:
                    accepted = str(row.get("accepted", "1")).strip().lower()
                    if accepted in {"0", "false", "no", "rejected"}:
                        continue
                    x = visual_float(row, ["x", "easting", "east", "lon", "longitude"])
                    y = visual_float(row, ["y", "northing", "north", "lat", "latitude"])
                    z = visual_float(row, ["corrected_depth_m", "mean_depth_m", "depth_m", "raw_depth_m", "depth", "z", "sounding"])
                    if x is None or y is None or z is None:
                        continue
                    points.append((float(x), float(y), float(z)))
            return points

        def depth_color(depth: float, min_depth: float, max_depth: float) -> str:
            span = max(max_depth - min_depth, 0.001)
            ratio = max(0.0, min(1.0, (depth - min_depth) / span))
            red = int(52 + 190 * ratio)
            green = int(190 - 80 * ratio)
            blue = int(235 - 170 * ratio)
            return f"#{red:02x}{green:02x}{blue:02x}"

        def draw_visualization() -> None:
            visual_canvas.delete("all")
            points: list[tuple[float, float, float]] = list(visual_state.get("points", []))
            width = max(visual_canvas.winfo_width(), 300)
            height = max(visual_canvas.winfo_height(), 260)
            visual_canvas.create_rectangle(0, 0, width, height, fill="#101418", outline="")
            if not points:
                visual_canvas.create_text(width / 2, height / 2, text="No MBES points loaded", fill="#d8dee9", font=("Segoe UI", 14, "bold"))
                return
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            ds = [point[2] for point in points]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            min_d, max_d = min(ds), max(ds)
            span_x = max(max_x - min_x, 1.0)
            span_y = max(max_y - min_y, 1.0)
            margin = 44
            scale = min((width - margin * 2) / span_x, (height - margin * 2) / span_y) * float(visual_state.get("zoom", 1.0))
            center_x = (min_x + max_x) / 2.0 + float(visual_state.get("pan_x", 0.0))
            center_y = (min_y + max_y) / 2.0 + float(visual_state.get("pan_y", 0.0))

            def project_xy(x: float, y: float) -> tuple[float, float]:
                sx = width / 2.0 + (x - center_x) * scale
                sy = height / 2.0 - (y - center_y) * scale
                return sx, sy

            for gx in range(0, width, 80):
                visual_canvas.create_line(gx, 0, gx, height, fill="#1f2937")
            for gy in range(0, height, 80):
                visual_canvas.create_line(0, gy, width, gy, fill="#1f2937")
            max_draw = 50000
            step = max(1, len(points) // max_draw)
            for x, y, depth in points[::step]:
                sx, sy = project_xy(x, y)
                if sx < -4 or sx > width + 4 or sy < -4 or sy > height + 4:
                    continue
                color = depth_color(depth, min_d, max_d)
                visual_canvas.create_oval(sx - 2, sy - 2, sx + 2, sy + 2, fill=color, outline="")
            legend_x = width - 170
            legend_y = 32
            visual_canvas.create_text(12, 12, anchor="nw", text=f"{len(points)} MBES points | depth {min_d:.3f} to {max_d:.3f} m | zoom {float(visual_state.get('zoom', 1.0)):.2f}x", fill="#e5eef7", font=("Segoe UI", 10, "bold"))
            for index in range(80):
                ratio = index / 79.0
                d = min_d + (max_d - min_d) * ratio
                visual_canvas.create_rectangle(legend_x, legend_y + index * 2, legend_x + 18, legend_y + index * 2 + 2, fill=depth_color(d, min_d, max_d), outline="")
            visual_canvas.create_text(legend_x + 26, legend_y, anchor="w", text=f"{min_d:.2f} m", fill="#e5eef7", font=("Segoe UI", 9))
            visual_canvas.create_text(legend_x + 26, legend_y + 158, anchor="w", text=f"{max_d:.2f} m", fill="#e5eef7", font=("Segoe UI", 9))

        def load_visual_source() -> None:
            try:
                points = read_visual_points(visual_source_var.get())
            except Exception as exc:
                messagebox.showerror("MBES Visualization", f"Could not load visualization data:\n{exc}", parent=win)
                return
            visual_state["points"] = points
            visual_state["zoom"] = 1.0
            visual_state["pan_x"] = 0.0
            visual_state["pan_y"] = 0.0
            visual_status.set(f"Loaded {len(points)} MBES points from {visual_source_var.get()}")
            draw_visualization()

        def visual_zoom(factor: float) -> None:
            visual_state["zoom"] = max(0.1, min(100.0, float(visual_state.get("zoom", 1.0)) * factor))
            draw_visualization()

        def visual_fit() -> None:
            visual_state["zoom"] = 1.0
            visual_state["pan_x"] = 0.0
            visual_state["pan_y"] = 0.0
            draw_visualization()

        def visual_pan(dx_ratio: float, dy_ratio: float) -> None:
            points: list[tuple[float, float, float]] = list(visual_state.get("points", []))
            if not points:
                return
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            visual_state["pan_x"] = float(visual_state.get("pan_x", 0.0)) + (max(xs) - min(xs) or 1.0) * dx_ratio
            visual_state["pan_y"] = float(visual_state.get("pan_y", 0.0)) + (max(ys) - min(ys) or 1.0) * dy_ratio
            draw_visualization()

        ttk.Button(visual_top, text="Browse / Load", command=choose_visual_source).pack(side="left", padx=4)
        ttk.Button(visual_top, text="Refresh", command=load_visual_source).pack(side="left", padx=4)
        ttk.Button(visual_top, text="Fit", command=visual_fit).pack(side="left", padx=4)
        ttk.Button(visual_top, text="Zoom +", command=lambda: visual_zoom(1.35)).pack(side="left", padx=4)
        ttk.Button(visual_top, text="Zoom -", command=lambda: visual_zoom(1 / 1.35)).pack(side="left", padx=4)
        pan_bar = ttk.Frame(visual_tab)
        pan_bar.pack(fill="x", pady=(6, 0))
        ttk.Button(pan_bar, text="West", command=lambda: visual_pan(-0.15, 0.0)).pack(side="left", padx=3)
        ttk.Button(pan_bar, text="East", command=lambda: visual_pan(0.15, 0.0)).pack(side="left", padx=3)
        ttk.Button(pan_bar, text="North", command=lambda: visual_pan(0.0, 0.15)).pack(side="left", padx=3)
        ttk.Button(pan_bar, text="South", command=lambda: visual_pan(0.0, -0.15)).pack(side="left", padx=3)
        ttk.Label(pan_bar, textvariable=visual_status).pack(side="left", padx=12)
        visual_canvas.bind("<Configure>", lambda _event: draw_visualization())

        professional_status = tk.StringVar(value="Prepare certified MBES processing workflows using CARIS, Qimera, HYPACK/HYSWEEP, PDS, EIVA, MB-System, or vendor software.")
        processor_profile_summary_var = tk.StringVar(value="")
        processor_var = tk.StringVar(value="CARIS")
        processor_path_var = tk.StringVar(value="")
        processor_project_var = tk.StringVar(value=str(paths["processed"]))
        processor_output_var = tk.StringVar(value=str(paths["exports"]))
        professional_log = tk.Text(professional_tab, height=10, wrap="word", bg="#f8fafc", fg="#111827")

        # Lowrance SL2/SL3 conversion utility. The optional sonarlight package
        # decodes the vendor's binary recording format; original files are never modified.
        lowrance_frame = ttk.LabelFrame(professional_tab, text="Lowrance recorded sonar conversion (.SL2 / .SL3)", padding=8)
        lowrance_frame.pack(fill="x", pady=(0, 8))
        lowrance_input_var = tk.StringVar(value="")
        lowrance_output_var = tk.StringVar(value=str(paths["exports"] / "lowrance_soundings.csv"))
        lowrance_channel_var = tk.StringVar(value="primary")
        lowrance_status_var = tk.StringVar(value="Select a recorded Lowrance file. Exports GPS coordinates and recorded water depth to CSV.")

        def browse_lowrance_input() -> None:
            name = filedialog.askopenfilename(
                title="Select Lowrance recorded sonar file",
                initialdir=str(paths["raw"]),
                filetypes=[("Lowrance sonar recordings", "*.sl2 *.sl3"), ("All files", "*.*")],
                parent=win,
            )
            if name:
                lowrance_input_var.set(name)
                lowrance_output_var.set(str(Path(name).with_suffix(".soundings.csv")))

        def browse_lowrance_output() -> None:
            name = filedialog.asksaveasfilename(
                title="Save converted Lowrance sounding CSV",
                defaultextension=".csv",
                initialdir=str(paths["exports"]),
                initialfile=Path(lowrance_output_var.get()).name or "lowrance_soundings.csv",
                filetypes=[("CSV sounding file", "*.csv")],
                parent=win,
            )
            if name:
                lowrance_output_var.set(name)

        def convert_lowrance_recording() -> None:
            source = Path(lowrance_input_var.get().strip())
            target = Path(lowrance_output_var.get().strip())
            if not source.is_file():
                messagebox.showwarning("Lowrance conversion", "Select an existing .SL2 or .SL3 recording first.", parent=win)
                return
            if source.suffix.lower() not in {".sl2", ".sl3"}:
                messagebox.showwarning("Lowrance conversion", "Only Lowrance .SL2 and .SL3 recordings are accepted.", parent=win)
                return
            if not str(target).strip() or target.suffix.lower() != ".csv":
                messagebox.showwarning("Lowrance conversion", "Choose a CSV output path.", parent=win)
                return
            try:
                from sonarlight import Sonar
                import math as _math
                sonar = Sonar(str(source), clean=True)
                frame = sonar.df.copy()
                if frame.empty:
                    raise ValueError("The recording decoded successfully but contains no usable records.")
                columns = {str(col).strip().lower(): col for col in frame.columns}

                def find_column(candidates: tuple[str, ...], required: bool = True):
                    for candidate in candidates:
                        if candidate in columns:
                            return columns[candidate]
                    if required:
                        raise ValueError("Required field not found in decoded data. Available fields: " + ", ".join(map(str, frame.columns)))
                    return None

                lat_col = find_column(("latitude", "lat"))
                lon_col = find_column(("longitude", "lon", "long"))
                depth_col = find_column(("water_depth", "depth", "depth_m", "waterdepth"))
                channel_col = find_column(("survey", "channel", "channel_name"), required=False)
                time_col = find_column(("time", "datetime", "timestamp", "time_utc", "utc_time", "date_time"), required=False)
                selected_channel = lowrance_channel_var.get().strip().lower()
                if channel_col is not None and selected_channel != "all channels":
                    frame = frame[frame[channel_col].astype(str).str.strip().str.lower() == selected_channel]
                if frame.empty:
                    raise ValueError(f"No records found for channel '{lowrance_channel_var.get()}'. Try All channels or another channel.")

                import pandas as _pd
                lat = _pd.to_numeric(frame[lat_col], errors="coerce")
                lon = _pd.to_numeric(frame[lon_col], errors="coerce")
                depth = _pd.to_numeric(frame[depth_col], errors="coerce")
                valid = lat.notna() & lon.notna() & depth.notna()
                valid &= lat.between(-90, 90) & lon.between(-180, 180) & (depth > 0)
                frame = frame.loc[valid].copy()
                if frame.empty:
                    raise ValueError("No valid rows with latitude, longitude and positive water depth were found.")
                lat = _pd.to_numeric(frame[lat_col], errors="coerce")
                lon = _pd.to_numeric(frame[lon_col], errors="coerce")
                depth = _pd.to_numeric(frame[depth_col], errors="coerce")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("w", newline="", encoding="utf-8-sig") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["latitude", "longitude", "depth_m", "time_utc", "channel"])
                    for idx in frame.index:
                        time_value = frame.at[idx, time_col] if time_col is not None else ""
                        channel_value = frame.at[idx, channel_col] if channel_col is not None else selected_channel
                        writer.writerow([
                            f"{float(lat.loc[idx]):.9f}",
                            f"{float(lon.loc[idx]):.9f}",
                            f"{float(depth.loc[idx]):.4f}",
                            "" if time_value is None else str(time_value),
                            str(channel_value),
                        ])
                lowrance_status_var.set(f"Converted {len(frame):,} valid sounding records to {target}")
                professional_append(f"Lowrance {source.suffix.upper()} converted: {len(frame):,} valid soundings -> {target}")
                messagebox.showinfo("Lowrance conversion complete", f"Exported {len(frame):,} valid records.\n\n{target}\n\nCoordinates remain latitude/longitude in decimal degrees; depth is in metres as decoded by the library.", parent=win)
            except ImportError as exc:
                messagebox.showerror("Lowrance conversion dependency missing", "Install the Lowrance decoder and its dependencies, then restart HYDROGEOMATICS:\n\npython -m pip install sonarlight pandas numpy\n\n" + str(exc), parent=win)
            except Exception as exc:
                lowrance_status_var.set(f"Conversion failed: {exc}")
                messagebox.showerror("Lowrance conversion failed", f"Could not convert this recording. The file may use an unsupported device variant or contain damaged records.\n\n{exc}", parent=win)

        ttk.Label(lowrance_frame, text="Recorded file").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(lowrance_frame, textvariable=lowrance_input_var).grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(lowrance_frame, text="Browse SL2/SL3", command=browse_lowrance_input).grid(row=0, column=2, padx=4, pady=3)
        ttk.Label(lowrance_frame, text="Output CSV").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(lowrance_frame, textvariable=lowrance_output_var).grid(row=1, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(lowrance_frame, text="Save As", command=browse_lowrance_output).grid(row=1, column=2, padx=4, pady=3)
        ttk.Label(lowrance_frame, text="Sonar channel").grid(row=2, column=0, sticky="w", padx=4, pady=3)
        ttk.Combobox(lowrance_frame, textvariable=lowrance_channel_var, values=["primary", "secondary", "downscan", "sidescan", "all channels"], state="readonly", width=18).grid(row=2, column=1, sticky="w", padx=4, pady=3)
        ttk.Button(lowrance_frame, text="Convert to CSV", command=convert_lowrance_recording).grid(row=2, column=2, padx=4, pady=3)
        ttk.Label(lowrance_frame, textvariable=lowrance_status_var, wraplength=980, justify="left").grid(row=3, column=0, columnspan=3, sticky="ew", padx=4, pady=(5, 2))
        lowrance_frame.columnconfigure(1, weight=1)

        internal_frame = ttk.LabelFrame(processor_tab, text="HYDROGEOMATICS internal MBES processor - beam decoding, TPU, cleaning, certification", padding=8)
        internal_frame.pack(fill="x", pady=(0, 8))
        internal_status = tk.StringVar(value="Load a beam/sounding CSV, choose TPU and cleaning settings, then run the HYDROGEOMATICS internal processor.")
        internal_input_var = tk.StringVar(value="")
        internal_cleaned_var = tk.StringVar(value=str(paths["processed"] / "HYDROGEOMATICS_internal_cleaned_beams.csv"))
        internal_grid_var = tk.StringVar(value=str(paths["processed"] / "HYDROGEOMATICS_internal_grid.csv"))
        internal_cert_var = tk.StringVar(value=str(paths["processed"] / "HYDROGEOMATICS_internal_certification.json"))
        internal_order_var = tk.StringVar(value="1a")
        internal_user_a_var = tk.StringVar(value="0.50")
        internal_user_b_var = tk.StringVar(value="0.013")
        internal_cell_var = tk.StringVar(value="1.0")
        internal_density_var = tk.StringVar(value="5")
        internal_median_var = tk.StringVar(value="0.75")
        internal_angle_var = tk.StringVar(value="70")
        internal_min_depth_var = tk.StringVar(value="")
        internal_max_depth_var = tk.StringVar(value="")

        def choose_internal_input() -> None:
            name = filedialog.askopenfilename(
                title="Select HYDROGEOMATICS beam/sounding CSV",
                initialdir=vars_map["project_folder"].get() or str(APP_DIR),
                filetypes=[("Beam / sounding CSV", "*.csv *.txt *.xyz"), ("All files", "*.*")],
                parent=win,
            )
            if name:
                internal_input_var.set(name)

        def save_internal_output(var: tk.StringVar, title: str, default_ext: str) -> None:
            name = filedialog.asksaveasfilename(
                title=title,
                defaultextension=default_ext,
                initialdir=str(paths["processed"]),
                parent=win,
            )
            if name:
                var.set(name)

        internal_rows = [
            ("Input beam/sounding CSV", internal_input_var, lambda: choose_internal_input()),
            ("Cleaned output CSV", internal_cleaned_var, lambda: save_internal_output(internal_cleaned_var, "Save HYDROGEOMATICS cleaned beam CSV", ".csv")),
            ("Grid output CSV", internal_grid_var, lambda: save_internal_output(internal_grid_var, "Save HYDROGEOMATICS grid CSV", ".csv")),
            ("Certification JSON", internal_cert_var, lambda: save_internal_output(internal_cert_var, "Save HYDROGEOMATICS certification report", ".json")),
        ]
        for row, (label, var, command) in enumerate(internal_rows):
            ttk.Label(internal_frame, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=3)
            ttk.Entry(internal_frame, textvariable=var).grid(row=row, column=1, columnspan=5, sticky="ew", padx=4, pady=3)
            ttk.Button(internal_frame, text="Browse", command=command).grid(row=row, column=6, sticky="ew", padx=4, pady=3)

        settings_row = len(internal_rows)
        controls = [
            ("Order", internal_order_var, ["Special", "1a", "1b", "2", "User"]),
            ("User a", internal_user_a_var, None),
            ("User b", internal_user_b_var, None),
            ("Cell", internal_cell_var, None),
            ("Density", internal_density_var, None),
            ("Median reject", internal_median_var, None),
            ("Angle limit", internal_angle_var, None),
            ("Min depth", internal_min_depth_var, None),
            ("Max depth", internal_max_depth_var, None),
        ]
        for index, (label, var, choices) in enumerate(controls):
            col = (index % 3) * 2
            row = settings_row + 1 + index // 3
            ttk.Label(internal_frame, text=label).grid(row=row, column=col, sticky="w", padx=4, pady=3)
            if choices:
                ttk.Combobox(internal_frame, textvariable=var, values=choices, state="readonly", width=12).grid(row=row, column=col + 1, sticky="ew", padx=4, pady=3)
            else:
                ttk.Entry(internal_frame, textvariable=var, width=12).grid(row=row, column=col + 1, sticky="ew", padx=4, pady=3)

        def run_internal_processor() -> None:
            source = internal_input_var.get().strip()
            if not source:
                messagebox.showwarning("HYDROGEOMATICS Processor", "Choose an input beam/sounding CSV first.", parent=win)
                return
            min_depth = parse_float(internal_min_depth_var.get()) if internal_min_depth_var.get().strip() else None
            max_depth = parse_float(internal_max_depth_var.get()) if internal_max_depth_var.get().strip() else None
            try:
                result = HYDROGEOMATICS_internal_mbes_process(
                    source,
                    internal_cleaned_var.get(),
                    internal_grid_var.get(),
                    internal_cert_var.get(),
                    order=internal_order_var.get(),
                    user_a=parse_float(internal_user_a_var.get()) or 0.50,
                    user_b=parse_float(internal_user_b_var.get()) or 0.013,
                    cell_size=parse_float(internal_cell_var.get()) or 1.0,
                    density_required=int(parse_float(internal_density_var.get()) or 5),
                    median_reject_m=parse_float(internal_median_var.get()) or 0.75,
                    angle_limit_deg=parse_float(internal_angle_var.get()) or 70.0,
                    min_depth=min_depth,
                    max_depth=max_depth,
                )
            except Exception as exc:
                messagebox.showerror("HYDROGEOMATICS Processor", f"Internal MBES processing failed:\n{exc}", parent=win)
                return
            visual_source_var.set(internal_cleaned_var.get())
            export_source_var.set(internal_cleaned_var.get())
            try:
                load_visual_source()
                notebook.select(visual_tab)
            except Exception:
                pass
            internal_status.set(
                f"Complete: {result['accepted_soundings']} accepted, {result['rejected_soundings']} rejected, certification {result['certification_result']}"
            )
            professional_append(
                f"HYDROGEOMATICS internal processor complete: {result['accepted_soundings']} accepted, "
                f"{result['rejected_soundings']} rejected, certification {result['certification_result']}"
            )

        ttk.Button(internal_frame, text="Run HYDROGEOMATICS Internal Processor", command=run_internal_processor).grid(
            row=settings_row + 4, column=0, columnspan=2, sticky="ew", padx=4, pady=(8, 3)
        )
        ttk.Label(
            internal_frame,
            text="Current decoder supports CSV/ASCII beam/sounding records. Native vendor binary beam decoders can be added here when official packet specifications or SDK access are available.",
            wraplength=900,
        ).grid(row=settings_row + 4, column=2, columnspan=5, sticky="ew", padx=4, pady=(8, 3))
        internal_frame.columnconfigure(1, weight=1)
        internal_frame.columnconfigure(3, weight=1)
        internal_frame.columnconfigure(5, weight=1)
        ttk.Label(processor_tab, textvariable=internal_status, wraplength=980, font=("Segoe UI", 10, "bold")).pack(fill="x", pady=(0, 8))

        def selected_processor_profile() -> dict[str, object]:
            return dict(MBES_PROCESSOR_PROFILES.get(processor_profile_var.get(), {}))

        def processor_profile_summary() -> str:
            profile_name = processor_profile_var.get()
            profile = selected_processor_profile()
            if not profile:
                return "No hardware/raw profile selected."
            raw_formats = ", ".join(str(item) for item in profile.get("raw_formats", []))
            processors = ", ".join(str(item) for item in profile.get("preferred_processors", []))
            exports = ", ".join(str(item) for item in profile.get("exports", []))
            support = str(profile.get("internal_support", ""))
            notes = " ".join(str(item) for item in profile.get("notes", []))
            return (
                f"{profile_name}: {profile.get('hardware', 'Hardware not specified')}\n"
                f"Raw/import formats: {raw_formats}\n"
                f"Recommended proprietary processors: {processors}\n"
                f"Expected exports: {exports}\n"
                f"{support}"
                + (f"\nNotes: {notes}" if notes else "")
            )

        def apply_processor_profile(*_args: object) -> None:
            profile = selected_processor_profile()
            default_processor = str(profile.get("default_processor", "")).strip()
            processor_values = {"HYDROGEOMATICS Internal", "CARIS", "Qimera", "HYPACK/HYSWEEP", "PDS", "EIVA", "MB-System", "Vendor software", "Other"}
            if default_processor:
                processor_var.set(default_processor if default_processor in processor_values else "Vendor software")
            processor_profile_summary_var.set(processor_profile_summary())
            professional_status.set(f"Selected MBES hardware/raw profile: {processor_profile_var.get()}")

        professional_top = ttk.LabelFrame(professional_tab, text="External hydrographic processor", padding=8)
        professional_top.pack(fill="x", pady=(0, 8))
        ttk.Label(professional_top, text="Hardware / raw profile").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        profile_combo = ttk.Combobox(
            professional_top,
            textvariable=processor_profile_var,
            values=list(MBES_PROCESSOR_PROFILES.keys()),
            state="readonly",
            width=28,
        )
        profile_combo.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        profile_combo.bind("<<ComboboxSelected>>", apply_processor_profile)
        ttk.Button(professional_top, text="Apply Profile", command=apply_processor_profile).grid(row=0, column=2, sticky="ew", padx=4, pady=4)
        ttk.Label(professional_top, text="Processor").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        processor_combo = ttk.Combobox(
            professional_top,
            textvariable=processor_var,
            values=["HYDROGEOMATICS Internal", "CARIS", "Qimera", "HYPACK/HYSWEEP", "PDS", "EIVA", "MB-System", "Vendor software", "Other"],
            state="readonly",
            width=20,
        )
        processor_combo.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(professional_top, text="Executable / launcher").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(professional_top, textvariable=processor_path_var).grid(row=2, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Label(professional_top, text="Processor project folder").grid(row=3, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(professional_top, textvariable=processor_project_var).grid(row=3, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Label(professional_top, text="Deliverables folder").grid(row=4, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(professional_top, textvariable=processor_output_var).grid(row=4, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Label(professional_top, textvariable=processor_profile_summary_var, wraplength=980, justify="left").grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(6, 2))
        professional_top.columnconfigure(1, weight=1)

        def professional_append(message: str) -> None:
            professional_status.set(message)
            try:
                professional_log.insert("end", f"{datetime.now().strftime('%H:%M:%S')}  {message}\n")
                professional_log.see("end")
            except Exception:
                pass

        def browse_processor_exe() -> None:
            name = filedialog.askopenfilename(
                title="Select external processor executable",
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
                parent=win,
            )
            if name:
                processor_path_var.set(name)

        def browse_processor_project() -> None:
            folder = filedialog.askdirectory(title="Select processor project folder", initialdir=processor_project_var.get() or vars_map["project_folder"].get(), parent=win)
            if folder:
                processor_project_var.set(folder)

        def browse_deliverables_folder() -> None:
            folder = filedialog.askdirectory(title="Select deliverables folder", initialdir=processor_output_var.get() or vars_map["project_folder"].get(), parent=win)
            if folder:
                processor_output_var.set(folder)

        def professional_paths() -> dict[str, Path]:
            setup = save_mbes_setup(show_message=False)
            return mbes_project_paths(setup.project_folder)

        def prepare_professional_package() -> None:
            setup = save_mbes_setup(show_message=False)
            p = mbes_project_paths(setup.project_folder)
            processor_profile = selected_processor_profile()
            profile_workflow = [str(item) for item in processor_profile.get("workflow", [])]
            processor_folder = Path(processor_project_var.get().strip() or p["processed"])
            deliverables_folder = Path(processor_output_var.get().strip() or p["exports"])
            subfolders = [
                processor_folder,
                processor_folder / "01_native_raw_import",
                processor_folder / "02_navigation_motion_heading",
                processor_folder / "03_svp_tide_offsets",
                processor_folder / "04_cleaning_qc",
                processor_folder / "05_grids_surfaces",
                deliverables_folder,
                deliverables_folder / "cleaned_soundings",
                deliverables_folder / "grids",
                deliverables_folder / "reports",
                deliverables_folder / "metadata",
            ]
            for folder in subfolders:
                folder.mkdir(parents=True, exist_ok=True)
            manifest = {
                "created_utc": mbes_utc_stamp(),
                "HYDROGEOMATICS_project": str(p["base"]),
                "processor": processor_var.get(),
                "hardware_raw_profile": processor_profile_var.get(),
                "processor_profile": processor_profile,
                "processor_executable": processor_path_var.get(),
                "processor_project_folder": str(processor_folder),
                "deliverables_folder": str(deliverables_folder),
                "native_raw_folder": str(p["raw"]),
                "logs_folder": str(p["logs"]),
                "hardware_setup": setup.to_dict(),
                "workflow": [
                    "Import native raw sonar files without deleting originals.",
                    "Import or merge GNSS, heading, motion, tide, SVP, offsets, latency, and patch-test values.",
                    "Apply sound velocity/refraction, water level/RTK tide, draft, and vessel offsets.",
                    "Clean beams, review rejected soundings, run crossline and coverage QC.",
                    "Create surfaces/grids with uncertainty/TPU if supported.",
                    "Export cleaned soundings, grid/surface, BAG/S-102/GeoTIFF where supported, metadata, and QC report.",
                ] + profile_workflow,
            }
            manifest_path = processor_folder / "HYDROGEOMATICS_professional_processing_manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            readme_path = processor_folder / "README_professional_processing.txt"
            raw_formats = ", ".join(str(item) for item in processor_profile.get("raw_formats", []))
            processors = ", ".join(str(item) for item in processor_profile.get("preferred_processors", []))
            exports = ", ".join(str(item) for item in processor_profile.get("exports", []))
            profile_steps = "\n".join(f"- {item}" for item in profile_workflow) or "- Follow the processor workflow in the manifest."
            readme_path.write_text(
                "HYDROGEOMATICS Professional MBES Processing Package\n\n"
                "Use this folder with licensed hydrographic software such as CARIS, Qimera, HYPACK/HYSWEEP, PDS, EIVA, MB-System, or vendor software.\n\n"
                "Selected hardware/raw profile:\n"
                f"{processor_profile_var.get()}\n"
                f"Hardware: {processor_profile.get('hardware', 'Not specified')}\n"
                f"Raw/import formats: {raw_formats}\n"
                f"Preferred proprietary processors: {processors}\n"
                f"Expected exports: {exports}\n"
                f"Internal HYDROGEOMATICS support: {processor_profile.get('internal_support', '')}\n\n"
                "Native raw data is preserved in:\n"
                f"{p['raw']}\n\n"
                "Recommended workflow:\n"
                "1. Import native raw files.\n"
                "2. Apply navigation, motion, heading, offsets, latency, draft, tide/water level, and SVP.\n"
                "3. Clean beams and run QC checks.\n"
                "4. Generate certified surfaces/deliverables using your licensed processor.\n"
                "5. Export cleaned CSV/XYZ/grid products back to HYDROGEOMATICS for visualization and final packaging.\n\n"
                "Profile-specific workflow notes:\n"
                f"{profile_steps}\n",
                encoding="utf-8",
            )
            professional_append(f"Prepared professional processing package: {processor_folder}")

        def launch_processor() -> None:
            exe = Path(processor_path_var.get().strip())
            if not exe.exists() or not exe.is_file():
                messagebox.showwarning("Professional Processing", "Select a valid processor executable first.", parent=win)
                return
            prepare_professional_package()
            try:
                os.startfile(str(exe))
            except Exception as exc:
                messagebox.showerror("Professional Processing", f"Could not launch processor:\n{exc}", parent=win)
                return
            professional_append(f"Launched {processor_var.get()}: {exe}")

        def open_professional_folders() -> None:
            prepare_professional_package()
            try:
                os.startfile(str(Path(processor_project_var.get())))
            except Exception as exc:
                messagebox.showerror("Professional Processing", f"Could not open folder:\n{exc}", parent=win)

        def import_professional_output() -> None:
            name = filedialog.askopenfilename(
                title="Import cleaned processor output",
                initialdir=processor_output_var.get() or str(professional_paths()["exports"]),
                filetypes=[
                    ("Cleaned soundings / grids / SonarView", "*.csv *.xyz *.txt *.gsf *.bag *.tif *.tiff *.json *.geojson *.svlog *.svlz"),
                    ("Cerulean SonarView logs", "*.svlog *.svlz"),
                    ("Generic Sensor Format", "*.gsf"),
                    ("All files", "*.*"),
                ],
                parent=win,
            )
            if not name:
                return
            suffix = Path(name).suffix.lower()
            if suffix in {".csv", ".xyz", ".txt"}:
                visual_source_var.set(name)
                export_source_var.set(name)
                try:
                    load_visual_source()
                    notebook.select(visual_tab)
                except Exception:
                    pass
                professional_append(f"Imported cleaned sounding/grid file for visualization: {name}")
            else:
                professional_append(f"Registered external deliverable: {name}")

        def create_mb_system_notes() -> None:
            p = professional_paths()
            notes_path = Path(processor_project_var.get().strip() or p["processed"]) / "mb_system_command_notes.txt"
            notes_path.parent.mkdir(parents=True, exist_ok=True)
            notes_path.write_text(
                "MB-System command notes\n\n"
                "MB-System processing is format-specific. Typical commands include:\n"
                "mbinfo -I <native_or_converted_file>\n"
                "mbprocess -I <file>\n"
                "mbgrid -I <datalist> -O <grid_name> -A2 -N -E<cell>/<cell>\n"
                "mbm_plot -I <grid_or_swath>\n\n"
                "Create a datalist that points to converted/supported MB-System files. Preserve original vendor raw files in the HYDROGEOMATICS RAW/MBES folder.\n",
                encoding="utf-8",
            )
            professional_append(f"Wrote MB-System notes: {notes_path}")

        professional_buttons = ttk.Frame(professional_top)
        professional_buttons.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(professional_buttons, text="Browse Processor", command=browse_processor_exe).pack(side="left", padx=4)
        ttk.Button(professional_buttons, text="Browse Project Folder", command=browse_processor_project).pack(side="left", padx=4)
        ttk.Button(professional_buttons, text="Browse Deliverables", command=browse_deliverables_folder).pack(side="left", padx=4)
        ttk.Button(professional_buttons, text="Prepare Package", command=prepare_professional_package).pack(side="left", padx=4)
        ttk.Button(professional_buttons, text="Launch Processor", command=launch_processor).pack(side="left", padx=4)
        ttk.Button(professional_buttons, text="Open Package Folder", command=open_professional_folders).pack(side="left", padx=4)
        apply_processor_profile()

        professional_workflow = ttk.LabelFrame(professional_tab, text="Certified processing handoff workflow", padding=8)
        professional_workflow.pack(fill="x", pady=(0, 8))
        ttk.Label(
            professional_workflow,
            text=(
                "HYDROGEOMATICS now includes an internal MBES processing engine for CSV/ASCII beam decoding, TPU, cleaning, gridding, and certification reporting, "
                "and it can also prepare external processor packages when a project requires CARIS, Qimera, HYPACK/HYSWEEP, PDS, EIVA, MB-System, or vendor tools."
            ),
            wraplength=980,
        ).pack(anchor="w")
        workflow_buttons = ttk.Frame(professional_workflow)
        workflow_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(workflow_buttons, text="Import Cleaned Output", command=import_professional_output).pack(side="left", padx=4)
        ttk.Button(workflow_buttons, text="Create MB-System Notes", command=create_mb_system_notes).pack(side="left", padx=4)
        ttk.Button(workflow_buttons, text="Open RAW Folder", command=lambda: os.startfile(str(professional_paths()["raw"]))).pack(side="left", padx=4)
        ttk.Button(workflow_buttons, text="Open Deliverables Folder", command=lambda: os.startfile(str(Path(processor_output_var.get())))).pack(side="left", padx=4)
        ttk.Label(professional_tab, textvariable=professional_status, wraplength=980, font=("Segoe UI", 10, "bold")).pack(fill="x", pady=(0, 6))
        professional_log.pack(fill="both", expand=True)

        hysweep_status = tk.StringVar(value="MBES collect and post-processing workflow loaded from the supplied HYPACK/HYSWEEP manuals.")
        hysweep_top = ttk.Frame(hysweep_tab)
        hysweep_top.pack(fill="x", pady=(0, 8))
        ttk.Label(
            hysweep_top,
            text="Use this tab as the MBES job sequence: configure, verify, collect, process, clean, certify, and export.",
            wraplength=980,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        hysweep_actions = ttk.LabelFrame(hysweep_tab, text="Workflow actions", padding=8)
        hysweep_actions.pack(fill="x", pady=(0, 8))
        action_buttons = [
            ("1 Hardware Setup", lambda: notebook.select(setup_tab)),
            ("2 Check Config", run_static_mbes_checks),
            ("3 Live Data Probe", run_live_mbes_probe),
            ("4 Routing / Realtime", lambda: notebook.select(routing_tab)),
            ("5 Raw Logging", lambda: notebook.select(log_tab)),
            ("6 Post Processing", lambda: notebook.select(process_tab)),
            ("7 Internal Processor", lambda: notebook.select(processor_tab)),
            ("8 Professional Handoff", lambda: notebook.select(professional_tab)),
            ("9 Visualization", lambda: notebook.select(visual_tab)),
            ("10 Export", lambda: notebook.select(export_tab)),
        ]
        for index, (label, command) in enumerate(action_buttons):
            ttk.Button(hysweep_actions, text=label, command=command).grid(row=index // 5, column=index % 5, sticky="ew", padx=4, pady=4)
        for col in range(5):
            hysweep_actions.columnconfigure(col, weight=1)

        hysweep_body_frame = ttk.Frame(hysweep_tab)
        hysweep_body_frame.pack(fill="both", expand=True)
        hysweep_text = tk.Text(hysweep_body_frame, wrap="word", bg="#f8fafc", fg="#111827", height=18)
        hysweep_scroll = ttk.Scrollbar(hysweep_body_frame, orient="vertical", command=hysweep_text.yview)
        hysweep_text.configure(yscrollcommand=hysweep_scroll.set)
        hysweep_text.pack(side="left", fill="both", expand=True)
        hysweep_scroll.pack(side="right", fill="y")
        hysweep_text.tag_configure("heading", font=("Segoe UI", 11, "bold"), spacing1=8, spacing3=4)

        def insert_workflow_section(title: str, items: list[str]) -> None:
            hysweep_text.insert("end", f"{title}\n", "heading")
            for number, item in enumerate(items, 1):
                hysweep_text.insert("end", f"{number}. {item}\n")
            hysweep_text.insert("end", "\n")

        hysweep_text.insert(
            "end",
            "MBES workflow\n",
            "heading",
        )
        hysweep_text.insert(
            "end",
            "This is a practical operating sequence derived from the supplied HYPACK/HYSWEEP documentation. "
            "It keeps HYDROGEOMATICS acquisition and processing aligned with the same field-to-editor rhythm: hardware setup, live windows, raw logging, corrections, cleaning, TPU/CUBE-style review, and export.\n\n",
        )
        insert_workflow_section("Data collect workflow", HYSWEEP_COLLECT_WORKFLOW)
        insert_workflow_section("Post-processing workflow", HYSWEEP_PROCESS_WORKFLOW)
        hysweep_text.insert(
            "end",
            "Important operating notes\n",
            "heading",
        )
        hysweep_text.insert(
            "end",
            "- Do not duplicate offsets or patch-test corrections in both sonar control software and HYDROGEOMATICS/HYSWEEP setup.\n"
            "- Time synchronization must be verified before survey when sonar, navigation, and motion are on separate computers or feeds.\n"
            "- Native proprietary packets remain the legal acquisition record; converted CSV/XYZ products are processing and export products.\n"
            "- Current HYDROGEOMATICS internal decoding is CSV/ASCII beam/sounding based. Native KMALL/ALL/S7K/XTF binary decoding needs official packet specifications or SDK access.\n",
        )
        hysweep_text.configure(state="disabled")

        def write_hysweep_workflow_package() -> None:
            setup = save_mbes_setup(show_message=False)
            p = mbes_project_paths(setup.project_folder)
            processor_profile = selected_processor_profile()
            checklist_path = p["logs"] / "HYDROGEOMATICS_hysweep_mbes_workflow_checklist.txt"
            manifest_path = p["logs"] / "HYDROGEOMATICS_hysweep_mbes_workflow_manifest.json"
            checklist_lines = [
                "HYDROGEOMATICS MBES Workflow Checklist",
                "",
                f"Project: {setup.project_folder}",
                f"Survey: {setup.survey_name}",
                f"Vessel: {setup.vessel_name}",
                f"Operator: {setup.operator_name}",
                f"Created UTC: {mbes_utc_stamp()}",
                "",
                "Project folders:",
            ]
            for key in ["raw", "logs", "svp", "tide", "matrix", "sort", "processed", "exports", "backup"]:
                checklist_lines.append(f"- {key.upper()}: {p[key]}")
            checklist_lines.extend(
                [
                    "",
                    "Selected proprietary processing profile:",
                    f"- Profile: {processor_profile_var.get()}",
                    f"- Hardware: {processor_profile.get('hardware', 'Not specified')}",
                    f"- Raw/import formats: {', '.join(str(item) for item in processor_profile.get('raw_formats', []))}",
                    f"- Preferred processors: {', '.join(str(item) for item in processor_profile.get('preferred_processors', []))}",
                    f"- Expected exports: {', '.join(str(item) for item in processor_profile.get('exports', []))}",
                    f"- Internal support: {processor_profile.get('internal_support', '')}",
                ]
            )
            checklist_lines.extend(["", "Data collect workflow:"])
            checklist_lines.extend(f"{index}. {item}" for index, item in enumerate(HYSWEEP_COLLECT_WORKFLOW, 1))
            checklist_lines.extend(["", "Post-processing workflow:"])
            checklist_lines.extend(f"{index}. {item}" for index, item in enumerate(HYSWEEP_PROCESS_WORKFLOW, 1))
            checklist_lines.extend(["", "Profile-specific processing notes:"])
            checklist_lines.extend(f"- {item}" for item in processor_profile.get("workflow", []))
            checklist_lines.extend(
                [
                    "",
                    "Required checks before logging:",
                    "- Hardware setup saved.",
                    "- Configuration check has no FAIL items.",
                    "- Live probe shows expected packets for each enabled stream.",
                    "- SVP, tide/water level, CRS, vertical datum, offsets, latency, and time sync are documented.",
                    "",
                    "Certification checks before final export:",
                    "- Crosslines reviewed.",
                    "- Coverage and density reviewed.",
                    "- TPU/uncertainty reviewed.",
                    "- Cleaning statistics and rejected reasons reviewed.",
                    "- Final cleaned soundings, grid/surface, metadata, and QC report exported.",
                ]
            )
            checklist_path.write_text("\n".join(checklist_lines) + "\n", encoding="utf-8")
            manifest = {
                "created_utc": mbes_utc_stamp(),
                "source_documents": [
                    r"C:\HYPACK 2018\Documentation\Hysweep Interfacing.pdf",
                    r"C:\HYPACK 2018\Documentation\HYPACK 2018 User Manual.pdf",
                ],
                "project_paths": {key: str(value) for key, value in p.items()},
                "hardware_setup": setup.to_dict(),
                "hardware_raw_profile": processor_profile_var.get(),
                "processor_profile": processor_profile,
                "collect_workflow": HYSWEEP_COLLECT_WORKFLOW,
                "post_processing_workflow": HYSWEEP_PROCESS_WORKFLOW,
                "HYDROGEOMATICS_actions": [
                    "Hardware Setup",
                    "Check Configuration",
                    "Listen for Live Data",
                    "Routing / Realtime",
                    "Start MBES Raw Logging",
                    "Run MBES Post Processing",
                    "Run HYDROGEOMATICS Internal Processor",
                    "Prepare Professional Processing Package",
                    "Visualization",
                    "Export",
                ],
            }
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            hysweep_status.set(f"Wrote workflow checklist and manifest: {checklist_path}")

        hysweep_bottom = ttk.Frame(hysweep_tab)
        hysweep_bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(hysweep_bottom, text="Write Workflow Checklist", command=write_hysweep_workflow_package).pack(side="left", padx=4)
        ttk.Button(hysweep_bottom, text="Prepare Professional Package", command=prepare_professional_package).pack(side="left", padx=4)
        ttk.Button(hysweep_bottom, text="Open Project Folder", command=lambda: os.startfile(str(Path(vars_map["project_folder"].get())))).pack(side="left", padx=4)
        ttk.Label(hysweep_bottom, textvariable=hysweep_status, wraplength=720).pack(side="left", fill="x", expand=True, padx=10)

        protocol_text = tk.Text(protocol_tab, wrap="word", bg="#f8fafc", fg="#111827")
        protocol_text.pack(fill="both", expand=True)
        protocol_text.insert(
            "1.0",
            "Recommended MBES protocol\n\n"
            "1. Preserve the manufacturer native raw data as the primary record: KMALL, ALL, S7K, XTF, or vendor binary.\n"
            "2. Select required hardware components before survey: MBES sonar, GNSS/INS position, motion/heave-pitch-roll, heading, SVP/surface sound speed, tide/water level, and time sync.\n"
            "3. Run configuration checks: required fields, duplicate UDP ports, port availability, numeric offsets, sound speed range, SVP/tide files, CRS, vertical datum, and project write access.\n"
            "4. Run live connectivity checks with systems powered and transmitting. The check listens on each selected UDP port and reports packet counts and bytes received.\n"
            "5. Log sonar, GNSS/NMEA, heading, motion, sound velocity, tide, offsets, patch-test values, CRS, and vertical datum with UTC metadata.\n"
            "6. Keep a JSONL packet index with source, receive time, byte offset, length, and SHA-256 checksum.\n"
            "7. In post processing, apply offsets, latency, patch test, draft, tide/water level, SVP/refraction, beam cleaning, uncertainty/TPU, crossline checks, and gridding.\n"
            "8. Export deliverables as cleaned CSV/XYZ, gridded bathymetry, GeoTIFF/BAG/S-102 where available, metadata, and QC report.\n\n"
            "Hardware-specific profile support in this build: Kongsberg ALL/KMALL, Teledyne RESON S7K, NORBIT S7K/WBMS, R2Sonic SONIC, EdgeTech Bathymetric, WASSP, Cerulean Surveyor 240-16, Generic GSF, HYPACK HS2/HS2X, and ASCII/XYZ. Select the profile in the Professional Processing section before preparing a package so the manifest records the correct decoder, raw format, export, and QC expectations.\n\n"
            "Professional processing support: HYDROGEOMATICS now includes an internal MBES processor for CSV/ASCII beam decoding, TPU estimation, cleaning, gridding, and certification reporting. It also prepares processor-ready folders/manifests, launches CARIS, Qimera, HYPACK/HYSWEEP, PDS, EIVA, MB-System, or vendor software, and imports cleaned outputs for visualization/export. Native vendor binary beam decoders can be added when official packet specifications or SDK access are available.\n",
        )
        protocol_text.configure(state="disabled")

        if str(initial_tab).lower() in {"processor", "HYDROGEOMATICS processor", "internal"}:
            try:
                notebook.select(processor_tab)
            except Exception:
                pass
        elif str(initial_tab).lower() in {"workflow", "mbes workflow", "hysweep", "hysweep workflow"}:
            try:
                notebook.select(hysweep_tab)
            except Exception:
                pass

        bottom = ttk.Frame(win, padding=(8, 0, 8, 8))
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=status).pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="Close", command=win.destroy).pack(side="right")
        win.bind("<Destroy>", lambda _event: stop_mbes_logging())

    def show_help_dialog() -> None:
        win = tk.Toplevel(root)
        win.title("HELP")
        win.geometry("820x640")
        win.minsize(640, 460)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="HYDROGEOMATICS SBES SURVEY Help", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 10))
        help_text = tk.Text(frame, wrap="word", height=18, bg="#f8fafc", fg="#111827")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=help_text.yview)
        help_text.configure(yscrollcommand=yscroll.set)
        help_text.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        help_text.tag_configure("section", font=("Segoe UI", 11, "bold"), spacing1=10, spacing3=4)
        help_text.tag_configure("button", font=("Segoe UI", 10, "bold"))
        help_text.insert("end", "This guide covers every main button, menu function, settings tab, mouse action, and logging shortcut. Hover over toolbar buttons for quick help.\n\n")
        for section, items in HELP_SECTIONS:
            help_text.insert("end", f"{section}\n", "section")
            for label, description in items:
                help_text.insert("end", f"- {label}: ", "button")
                help_text.insert("end", f"{description}\n")
            help_text.insert("end", "\n")
        help_text.configure(state="disabled")
        ttk.Button(frame, text="Close", command=win.destroy).pack(anchor="e", pady=(10, 0))

    def show_about_dialog() -> None:
        messagebox.showinfo(
            "ABOUT",
            f"{APP_TITLE}\n\n"
            "Single Beam Echo Sounder survey planning and logging system.\n"
            "Includes GNSS/SBES settings, realtime map navigation, HYPACK RAW logging, "
            "I/O monitor, SB EDIT, data display, echogram, border, and DXF background tools.",
            parent=root,
        )

    header = ttk.Frame(root, padding=(8, 6, 8, 2))
    header.pack(fill="x")
    ttk.Label(header, text=APP_TITLE, font=("Segoe UI", 12, "bold")).pack(side="left")
    header_actions = ttk.Frame(header)
    header_actions.pack(side="right")
    help_button = ttk.Button(header_actions, text="Help", command=show_help_dialog, style="SurveyPlan.TButton")
    help_button.pack(side="left", padx=(0, 6))
    ToolTip(help_button, BUTTON_HELP["Help"])
    about_button = ttk.Button(header_actions, text="About", command=show_about_dialog, style="SurveyPlan.TButton")
    about_button.pack(side="left")
    ToolTip(about_button, BUTTON_HELP["About"])

    controls = ttk.Frame(root, padding=(8, 8, 8, 4))

    toolbar_commands: dict[str, Any] = {}

    def toolbar_button_columns() -> int:
        return 1 if toolbar_state.get("dock") in {"left", "right"} else 8

    def render_toolbar() -> None:
        for child in controls.winfo_children():
            child.destroy()
        controls.pack_forget()
        dock = str(toolbar_state.get("dock", "top"))
        if dock == "left":
            controls.pack(side="left", fill="y", padx=(6, 2), pady=6)
        elif dock == "right":
            controls.pack(side="right", fill="y", padx=(2, 6), pady=6)
        elif dock == "bottom":
            controls.pack(side="bottom", fill="x", padx=6, pady=(2, 6))
        else:
            try:
                controls.pack(side="top", fill="x", before=correction_bar)
            except Exception:
                controls.pack(side="top", fill="x")

        for column in range(12):
            controls.columnconfigure(column, weight=0, uniform="")
        columns = toolbar_button_columns()
        known = set(toolbar_commands)
        order = [label for label in toolbar_state.get("order", []) if label in known]
        toolbar_state["order"] = order
        for index, label in enumerate(order):
            button = ttk.Button(controls, text=label, command=toolbar_commands[label], style="SurveyPlan.TButton")
            button.grid(row=index // columns, column=index % columns, sticky="ew", padx=3, pady=3)
            ToolTip(button, BUTTON_HELP.get(label, f"Open {label}."))
        for column in range(columns):
            controls.columnconfigure(column, weight=1, uniform="main_buttons")

    def show_theme_dialog() -> None:
        win = tk.Toplevel(root)
        win.title("THEME / DISPLAY")
        win.geometry("470x360")
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)

        font_var = tk.IntVar(value=bounded_font(getattr(settings, "ui_font_size", 10), 10))
        button_font_var = tk.IntVar(value=bounded_font(getattr(settings, "ui_button_font_size", 10), 10))
        menu_font_var = tk.IntVar(value=bounded_font(getattr(settings, "ui_menu_font_size", 10), 10))
        contrast_var = tk.StringVar(value=str(getattr(settings, "ui_contrast", "Standard") or "Standard"))
        scheme_var = tk.StringVar(value=str(getattr(settings, "ui_color_scheme", "Nautical Light") or "Nautical Light"))

        ttk.Label(frame, text="Font size").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Spinbox(frame, from_=8, to=30, textvariable=font_var, width=8).grid(row=0, column=1, sticky="w", padx=6, pady=6)
        ttk.Label(frame, text="Button font size").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        ttk.Spinbox(frame, from_=8, to=30, textvariable=button_font_var, width=8).grid(row=1, column=1, sticky="w", padx=6, pady=6)
        ttk.Label(frame, text="Drop-down menu font size").grid(row=2, column=0, sticky="w", padx=6, pady=6)
        ttk.Spinbox(frame, from_=8, to=30, textvariable=menu_font_var, width=8).grid(row=2, column=1, sticky="w", padx=6, pady=6)
        ttk.Label(frame, text="Background contrast").grid(row=3, column=0, sticky="w", padx=6, pady=6)
        ttk.Combobox(frame, textvariable=contrast_var, values=["Standard", "High", "Low"], state="readonly", width=18).grid(row=3, column=1, sticky="ew", padx=6, pady=6)
        ttk.Label(frame, text="Colour scheme").grid(row=4, column=0, sticky="w", padx=6, pady=6)
        ttk.Combobox(frame, textvariable=scheme_var, values=list(THEME_SCHEMES), state="readonly", width=18).grid(row=4, column=1, sticky="ew", padx=6, pady=6)

        preview = ttk.LabelFrame(frame, text="Preview", padding=8)
        preview.grid(row=5, column=0, columnspan=2, sticky="ew", padx=6, pady=(12, 8))
        ttk.Label(preview, text="Menus, buttons, settings windows, and display panels use this theme.").pack(anchor="w")
        ttk.Button(preview, text="Sample Button", style="SurveyPlan.TButton").pack(anchor="w", pady=(8, 0))

        def save_theme(close: bool = False) -> None:
            settings.ui_font_size = bounded_font(font_var.get(), 10)
            settings.ui_button_font_size = bounded_font(button_font_var.get(), settings.ui_font_size)
            settings.ui_menu_font_size = bounded_font(menu_font_var.get(), settings.ui_font_size)
            settings.ui_contrast = contrast_var.get()
            settings.ui_color_scheme = scheme_var.get()
            apply_theme()
            try:
                save_default_settings()
            except Exception:
                pass
            try:
                canvas.configure(bg=theme_colors.get("canvas", "#101418"))
            except Exception:
                pass
            draw_view()
            render_toolbar()
            try:
                build_main_menu()
            except Exception:
                pass
            status_var.set(f"Theme applied: {settings.ui_color_scheme}, font {settings.ui_font_size}")
            if close:
                win.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="Apply", command=lambda: save_theme(False), style="SurveyPlan.TButton").pack(side="left", padx=4)
        ttk.Button(buttons, text="OK", command=lambda: save_theme(True), style="SurveyPlan.TButton").pack(side="left", padx=4)
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="right", padx=4)
        frame.columnconfigure(1, weight=1)

    def show_toolbar_settings() -> None:
        win = tk.Toplevel(root)
        win.title("TOOLBAR SETTINGS")
        win.geometry("420x430")
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        dock_var = tk.StringVar(value=str(toolbar_state.get("dock", "top")))
        ttk.Label(frame, text="Dock toolbar").grid(row=0, column=0, sticky="w", pady=(0, 6))
        dock_frame = ttk.Frame(frame)
        dock_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        for index, dock_name in enumerate(["top", "bottom", "left", "right"]):
            ttk.Radiobutton(dock_frame, text=dock_name.title(), variable=dock_var, value=dock_name).grid(
                row=0, column=index, sticky="w", padx=4
            )

        ttk.Label(frame, text="Button order").grid(row=2, column=0, sticky="w", pady=(0, 6))
        order_list = tk.Listbox(frame, height=14, exportselection=False)
        order_list.grid(row=3, column=0, rowspan=4, sticky="nsew", padx=(0, 8))

        def refresh_order_list(select_index: Optional[int] = None) -> None:
            order_list.delete(0, "end")
            for label in toolbar_state.get("order", []):
                order_list.insert("end", label)
            if select_index is not None and order_list.size():
                select_index = max(0, min(select_index, order_list.size() - 1))
                order_list.selection_set(select_index)
                order_list.activate(select_index)

        def move_button(delta: int) -> None:
            selection = order_list.curselection()
            if not selection:
                return
            index = int(selection[0])
            new_index = max(0, min(index + delta, len(toolbar_state["order"]) - 1))
            if new_index == index:
                return
            order = toolbar_state["order"]
            order[index], order[new_index] = order[new_index], order[index]
            refresh_order_list(new_index)

        def restore_default_toolbar() -> None:
            toolbar_state["dock"] = "top"
            dock_var.set("top")
            toolbar_state["order"] = [
                "SBES",
                "Multi-Beam Sys",
                "Start Logging",
                "Stop Log&Save",
                "Zoom +",
                "Zoom -",
                "Zoom Window",
                "Extents",
            ]
            refresh_order_list(0)

        def apply_toolbar_settings() -> None:
            toolbar_state["dock"] = dock_var.get()
            render_toolbar()
            win.destroy()

        ttk.Button(frame, text="MOVE UP", command=lambda: move_button(-1)).grid(row=3, column=1, sticky="ew", pady=(0, 4))
        ttk.Button(frame, text="MOVE DOWN", command=lambda: move_button(1)).grid(row=4, column=1, sticky="ew", pady=(0, 4))
        ttk.Button(frame, text="Default", command=restore_default_toolbar).grid(row=5, column=1, sticky="ew", pady=(0, 4))
        ttk.Button(frame, text="Apply", command=apply_toolbar_settings).grid(row=6, column=1, sticky="ew")
        ttk.Button(frame, text="Close", command=win.destroy).grid(row=7, column=1, sticky="ew", pady=(8, 0))
        frame.rowconfigure(3, weight=1)
        frame.columnconfigure(0, weight=1)
        refresh_order_list()

    toolbar_commands.update(
        {
            "SBES": show_sbes_workflow,
            "Projects": show_project_dialog,
            "Settings": show_settings_dialog,
            "THEME": show_theme_dialog,
            "GEOCALC": lambda: show_geodetic_tools_dialog(root),
            "KTD Editor": show_ktd_editor,
            "Multi-Beam Sys": lambda: show_mbes_dialog("workflow"),
            "I/O Monitor": show_io_monitor,
            "SS510 Control Panel": show_ss510_control_panel,
            "Start Logging": start_logging,
            "Stop Log&Save": stop_logging_and_save,
            "Line Editor": show_line_editor,
            "Border": show_border_dialog,
            "Background Upload": show_background_upload_dialog,
            "DXF": show_dxf_dialog,
            "ODA DWG->DXF": lambda: show_oda_dwg_converter_dialog(root),
            "ENC": show_enc_dialog,
            "KMZ": show_kmz_dialog,
            "SB EDIT": show_sb_edit,
            "Data Display": show_data_display,
            "Zoom +": lambda: zoom_view(1.25),
            "Zoom -": lambda: zoom_view(0.8),
            "Zoom Window": enable_zoom_window,
            "Extents": fit_view,
            "Follow": lambda: set_follow_vessel(True),
            "Unfollow": lambda: set_follow_vessel(False),
            "Toolbar": show_toolbar_settings,
        }
    )

    def build_main_menu() -> None:
        menubar = tk.Menu(root)

        def add_menu(title: str, entries: list[object]) -> None:
            menu = tk.Menu(menubar, tearoff=False)
            for entry in entries:
                if entry is None:
                    menu.add_separator()
                    continue
                label, command, accelerator = entry
                if accelerator:
                    menu.add_command(label=label, command=command, accelerator=accelerator)
                else:
                    menu.add_command(label=label, command=command)
            menubar.add_cascade(label=title, menu=menu)

        add_menu(
            "Projects",
            [
                ("Projects", toolbar_commands["Projects"], ""),
                ("Settings", toolbar_commands["Settings"], ""),
                ("THEME", toolbar_commands["THEME"], ""),
                None,
                ("Exit", root.destroy, ""),
            ],
        )
        add_menu(
            "Survey",
            [
                ("Start Logging", toolbar_commands["Start Logging"], "Ctrl+S"),
                ("Stop Log&Save", toolbar_commands["Stop Log&Save"], "Ctrl+E"),
                ("I/O Monitor", toolbar_commands["I/O Monitor"], ""),
                ("Data Display", toolbar_commands["Data Display"], ""),
                ("SBES", toolbar_commands["SBES"], ""),
                ("Multi-Beam Sys", toolbar_commands["Multi-Beam Sys"], ""),
            ],
        )
        add_menu(
            "Lines",
            [
                ("Line Editor", toolbar_commands["Line Editor"], ""),
                ("Clip All by Border", clip_lines_by_border_dialog, ""),
                ("Border", toolbar_commands["Border"], ""),
                ("Background Upload", toolbar_commands["Background Upload"], ""),
                ("Follow Vessel", toolbar_commands["Follow"], ""),
                ("Unfollow Vessel", toolbar_commands["Unfollow"], ""),
            ],
        )
        add_menu(
            "View",
            [
                ("Zoom In", toolbar_commands["Zoom +"], ""),
                ("Zoom Out", toolbar_commands["Zoom -"], ""),
                ("Zoom Window", toolbar_commands["Zoom Window"], ""),
                ("Extents", toolbar_commands["Extents"], ""),
            ],
        )
        add_menu(
            "Tools",
            [
                ("THEME", toolbar_commands["THEME"], ""),
                ("GEOCALC", toolbar_commands["GEOCALC"], ""),
                ("SBES", toolbar_commands["SBES"], ""),
                ("KTD Editor", toolbar_commands["KTD Editor"], ""),
                ("SS510 Control Panel", toolbar_commands["SS510 Control Panel"], ""),
                ("Multi-Beam Sys", toolbar_commands["Multi-Beam Sys"], ""),
                None,
                ("SB EDIT", toolbar_commands["SB EDIT"], ""),
                ("Border", toolbar_commands["Border"], ""),
                ("Background Upload", toolbar_commands["Background Upload"], ""),
                ("DXF", toolbar_commands["DXF"], ""),
                ("ODA DWG->DXF", toolbar_commands["ODA DWG->DXF"], ""),
                ("ENC", toolbar_commands["ENC"], ""),
                ("KMZ / Export KMZ", toolbar_commands["KMZ"], ""),
                ("Toolbar Settings", toolbar_commands["Toolbar"], ""),
            ],
        )
        add_menu(
            "All Buttons",
            [(label, toolbar_commands[label], "") for label in toolbar_state.get("order", []) if label in toolbar_commands],
        )
        add_menu(
            "Help",
            [
                ("Help", show_help_dialog, ""),
                ("About", show_about_dialog, ""),
            ],
        )
        root.config(menu=menubar)

    build_main_menu()
    render_toolbar()

    correction_bar = ttk.Frame(root, padding=(8, 0, 8, 6))
    correction_bar.pack(fill="x")
    ttk.Button(correction_bar, text="W", command=lambda: pan_view(-0.25, 0.0), style="SurveyPlan.TButton").grid(row=0, column=0, sticky="ew", padx=(8, 2))
    ttk.Button(correction_bar, text="N", command=lambda: pan_view(0.0, 0.25), style="SurveyPlan.TButton").grid(row=0, column=1, sticky="ew", padx=2)
    ttk.Button(correction_bar, text="S", command=lambda: pan_view(0.0, -0.25), style="SurveyPlan.TButton").grid(row=0, column=2, sticky="ew", padx=2)
    ttk.Button(correction_bar, text="E", command=lambda: pan_view(0.25, 0.0), style="SurveyPlan.TButton").grid(row=0, column=3, sticky="ew", padx=(2, 8))
    ttk.Button(correction_bar, text="Z+", command=lambda: zoom_view(1.6), style="SurveyPlan.TButton").grid(row=0, column=4, sticky="ew", padx=2)
    ttk.Button(correction_bar, text="Z-", command=lambda: zoom_view(0.625), style="SurveyPlan.TButton").grid(row=0, column=5, sticky="ew", padx=(2, 10))
    ttk.Label(correction_bar, text="View Scale", style="SurveyPlan.TLabel").grid(row=0, column=6, sticky="e", padx=(8, 4))
    scale_combo = ttk.Combobox(correction_bar, textvariable=nav_scale_var, values=NAV_SCALE_OPTIONS, width=11, state="readonly")
    scale_combo.grid(row=0, column=7, sticky="ew")
    scale_combo.bind("<<ComboboxSelected>>", save_nav_scale_setting)
    nav_scale_var.trace_add("write", save_nav_scale_setting)

    ttk.Label(correction_bar, text="Line", style="SurveyPlan.TLabel").grid(row=0, column=8, sticky="e", padx=(12, 4))
    line_selector_combo = ttk.Combobox(correction_bar, textvariable=selected_line_name_var, values=["No Line"], width=16, state="readonly")
    line_selector_widgets["combo"] = line_selector_combo
    line_selector_combo.grid(row=0, column=9, sticky="ew")
    line_selector_combo.bind("<<ComboboxSelected>>", select_line_by_name)
    ttk.Button(correction_bar, text="NEXT", command=select_next_line, style="SurveyPlan.TButton").grid(row=0, column=10, sticky="ew", padx=(6, 0))
    ttk.Checkbutton(correction_bar, text="Selected", variable=use_selected_line, command=draw_view).grid(row=0, column=11, sticky="w", padx=(6, 0))
    ttk.Checkbutton(correction_bar, text="Auto nearest", variable=auto_select_nearest_line, command=lambda: (use_selected_line.set(True), draw_view())).grid(row=0, column=12, sticky="w", padx=(6, 0))
    correction_bar.columnconfigure(7, weight=1)
    correction_bar.columnconfigure(9, weight=1)
    refresh_line_selector_options()

    body = ttk.Frame(root)
    body.pack(fill="both", expand=True, padx=6, pady=(0, 6))

    canvas = tk.Canvas(body, bg="#101418", highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    def ktd_paths_for_screen_edit() -> list[Path]:
        paths = [Path(str(name)) for name in project_background_files if Path(str(name)).suffix.lower() == ".ktd"]
        path_text = str(getattr(settings.geodetic, "ktd_file_path", "") or "").strip()
        if path_text:
            ktd_path = Path(path_text)
            if ktd_path not in paths:
                paths.append(ktd_path)
        return paths

    def ktd_cell_under_world(easting: float, northing: float) -> Optional[dict[str, object]]:
        for ktd_path in ktd_paths_for_screen_edit():
            if not ktd_path.exists():
                continue
            matrix = load_ktd_matrix(ktd_path)
            if not matrix:
                continue
            meta = matrix.get("meta", {})
            rows = matrix.get("rows", [])
            if not isinstance(meta, dict) or not isinstance(rows, list):
                continue
            x_count = int(meta.get("x_count", 0) or 0)
            y_count = int(meta.get("y_count", 0) or 0)
            min_grid_x = float(meta.get("min_x", 0.0) or 0.0)
            min_grid_y = float(meta.get("min_y", 0.0) or 0.0)
            max_grid_x = float(meta.get("max_x", min_grid_x) or min_grid_x)
            max_grid_y = float(meta.get("max_y", min_grid_y) or min_grid_y)
            if x_count <= 1 or y_count <= 1:
                continue
            dx_grid = (max_grid_x - min_grid_x) / max(x_count - 1, 1)
            dy_grid = (max_grid_y - min_grid_y) / max(y_count - 1, 1)
            col = int(round((float(easting) - min_grid_x) / max(dx_grid, 1e-12)))
            row = int(round((float(northing) - min_grid_y) / max(dy_grid, 1e-12)))
            if col < 0 or col >= x_count or row < 0 or row >= y_count:
                continue
            center_x = min_grid_x + col * dx_grid
            center_y = min_grid_y + row * dy_grid
            if abs(float(easting) - center_x) > abs(dx_grid) * 0.55 or abs(float(northing) - center_y) > abs(dy_grid) * 0.55:
                continue
            value = None
            try:
                value = rows[row][col]
            except Exception:
                value = KTD_NODATA_VALUE
            return {
                "path": ktd_path,
                "meta": meta,
                "rows": rows,
                "row": row,
                "col": col,
                "x": center_x,
                "y": center_y,
                "value": KTD_NODATA_VALUE if value is None else float(value),
            }
        return None

    def edit_ktd_cell_at_cursor() -> None:
        cursor = view_state.get("last_cursor_world")
        if not cursor:
            messagebox.showinfo("KTD Screen Edit", "Right-click on a visible KTD grid cell first.", parent=root)
            return
        cell = ktd_cell_under_world(float(cursor[0]), float(cursor[1]))
        if not cell:
            messagebox.showinfo("KTD Screen Edit", "No loaded KTD cell was found at the selected map position.", parent=root)
            return
        ktd_path = cell["path"]
        old_value = float(cell.get("value", KTD_NODATA_VALUE) or KTD_NODATA_VALUE)
        row = int(cell.get("row", 0) or 0)
        col = int(cell.get("col", 0) or 0)
        x = float(cell.get("x", 0.0) or 0.0)
        y = float(cell.get("y", 0.0) or 0.0)
        new_value = simpledialog.askfloat(
            "Edit KTD Cell",
            (
                f"File: {Path(str(ktd_path)).name}\n"
                f"Cell row {row + 1}, column {col + 1}\n"
                f"Easting {x:.3f}, Northing {y:.3f}\n\n"
                "Enter K value. Use 999.99 for no-data:"
            ),
            initialvalue=old_value,
            parent=root,
        )
        if new_value is None:
            return
        matrix = load_ktd_matrix(Path(str(ktd_path)))
        if not matrix:
            messagebox.showerror("KTD Screen Edit", "Could not reload the selected KTD matrix.", parent=root)
            return
        meta = matrix.get("meta", {})
        rows = matrix.get("rows", [])
        if not isinstance(meta, dict) or not isinstance(rows, list):
            messagebox.showerror("KTD Screen Edit", "The selected KTD matrix is not editable.", parent=root)
            return
        try:
            rows[row][col] = float(new_value)
        except Exception as exc:
            messagebox.showerror("KTD Screen Edit", f"Could not update the selected KTD cell:\\n{exc}", parent=root)
            return
        x_count = int(meta.get("x_count", 0) or 0)
        y_count = int(meta.get("y_count", 0) or 0)
        min_grid_x = float(meta.get("min_x", 0.0) or 0.0)
        min_grid_y = float(meta.get("min_y", 0.0) or 0.0)
        max_grid_x = float(meta.get("max_x", min_grid_x) or min_grid_x)
        max_grid_y = float(meta.get("max_y", min_grid_y) or min_grid_y)
        dx_grid = (max_grid_x - min_grid_x) / max(x_count - 1, 1)
        dy_grid = (max_grid_y - min_grid_y) / max(y_count - 1, 1)
        nodes: list[tuple[float, float, float]] = []
        for r_index, row_values in enumerate(rows[:y_count]):
            if not isinstance(row_values, list):
                continue
            node_y = min_grid_y + r_index * dy_grid
            for c_index, value in enumerate(row_values[:x_count]):
                if value is None or is_ktd_nodata(float(value)):
                    continue
                node_x = min_grid_x + c_index * dx_grid
                nodes.append((float(node_x), float(node_y), float(value)))
        try:
            save_ktd_nodes(Path(str(ktd_path)), nodes, "K", grid_meta=meta)
        except Exception as exc:
            messagebox.showerror("KTD Screen Edit", f"Could not save the edited KTD file:\\n{exc}", parent=root)
            return
        try:
            load_ktd_background_path(Path(str(ktd_path)), replace_existing=True)
        except Exception:
            pass
        status_var.set(f"Edited KTD {Path(str(ktd_path)).name}: row {row + 1}, col {col + 1}, K={float(new_value):.2f}")
        draw_view()

    cursor_menu = tk.Menu(root, tearoff=False)
    cursor_menu.add_command(label="Zoom In", command=lambda: zoom_view(1.6))
    cursor_menu.add_command(label="Zoom Out", command=lambda: zoom_view(0.625))
    cursor_menu.add_command(label="Zoom Window", command=enable_zoom_window)
    cursor_menu.add_command(label="Extents", command=fit_view)
    pan_cursor_menu = tk.Menu(cursor_menu, tearoff=False)
    pan_cursor_menu.add_command(label="Pan North", command=lambda: pan_view(0.0, 0.25))
    pan_cursor_menu.add_command(label="Pan South", command=lambda: pan_view(0.0, -0.25))
    pan_cursor_menu.add_command(label="Pan East", command=lambda: pan_view(0.25, 0.0))
    pan_cursor_menu.add_command(label="Pan West", command=lambda: pan_view(-0.25, 0.0))
    cursor_menu.add_cascade(label="Pan", menu=pan_cursor_menu)
    cursor_menu.add_separator()
    cursor_menu.add_command(label="Follow Vessel", command=lambda: set_follow_vessel(True))
    cursor_menu.add_command(label="Unfollow Vessel", command=lambda: set_follow_vessel(False))
    scale_cursor_menu = tk.Menu(cursor_menu, tearoff=False)
    for scale_label in NAV_SCALE_OPTIONS:
        scale_cursor_menu.add_command(label=scale_label, command=lambda value=scale_label: (nav_scale_var.set(value), save_nav_scale_setting()))
    cursor_menu.add_cascade(label="View Scale", menu=scale_cursor_menu)
    cursor_menu.add_separator()
    line_cursor_menu = tk.Menu(cursor_menu, tearoff=False)
    line_cursor_menu.add_command(label="Open Lines", command=load_runlines)
    line_cursor_menu.add_command(label="Generate Lines", command=generate_runlines_dialog)
    line_cursor_menu.add_command(label="Save Lines", command=save_runlines)
    line_cursor_menu.add_command(label="Increase Line Number / Next Line", command=select_next_line, accelerator="Ctrl+I")
    line_cursor_menu.add_command(label="Decrease Line Number / Previous Line", command=select_previous_line, accelerator="Ctrl+D")
    line_cursor_menu.add_command(label="Swap Line Direction", command=swap_selected_line_direction, accelerator="Ctrl+W")
    line_cursor_menu.add_checkbutton(label="Use Selected Line", variable=use_selected_line, command=draw_view)
    line_cursor_menu.add_checkbutton(label="Auto Select Nearest Line", variable=auto_select_nearest_line, command=lambda: (use_selected_line.set(True), draw_view()))
    line_cursor_menu.add_command(label="Clip All by Border", command=clip_lines_by_border_dialog)
    line_cursor_menu.add_separator()
    line_cursor_menu.add_command(label="Line Editor Window", command=show_line_editor)
    cursor_menu.add_cascade(label="Line Editor", menu=line_cursor_menu)
    cursor_menu.add_command(label="Data Display", command=show_data_display)
    cursor_menu.add_command(label="I/O Monitor", command=show_io_monitor)
    cursor_menu.add_command(label="SB EDIT", command=show_sb_edit)
    cursor_menu.add_command(label="KTD Editor", command=show_ktd_editor)
    cursor_menu.add_command(label="Edit KTD Cell Here", command=edit_ktd_cell_at_cursor)
    cursor_menu.add_command(label="Border", command=show_border_dialog)
    cursor_menu.add_command(label="Explorer Load / Unload", command=show_background_file_explorer)
    cursor_menu.add_command(label="Background Upload", command=show_background_upload_dialog)
    cursor_menu.add_command(label="Unload Background", command=show_background_unload_dialog)
    cursor_menu.add_command(label="ENC Feature Details", command=show_enc_feature_details)
    cursor_menu.add_command(label="S57 Display Options", command=show_s57_display_options)
    cursor_menu.add_command(label="ENC Feature Summary", command=show_enc_summary)

    def show_cursor_controls(event: Any) -> str:
        try:
            view_state["last_cursor_world"] = screen_to_world(event.x, event.y)
            try:
                world_x, world_y = view_state["last_cursor_world"]
                cell = ktd_cell_under_world(float(world_x), float(world_y))
                cursor_menu.entryconfig("Edit KTD Cell Here", state=("normal" if cell else "disabled"))
            except Exception:
                cursor_menu.entryconfig("Edit KTD Cell Here", state="disabled")
            cursor_menu.tk_popup(event.x_root, event.y_root)
        finally:
            cursor_menu.grab_release()
        return "break"
    canvas.bind("<Configure>", lambda _event: draw_view())
    canvas.bind("<ButtonPress-1>", on_canvas_mouse_down)
    canvas.bind("<Motion>", on_canvas_mouse_move)
    canvas.bind("<B1-Motion>", on_canvas_mouse_drag)
    canvas.bind("<ButtonRelease-1>", on_canvas_mouse_up)
    canvas.bind("<MouseWheel>", on_canvas_mouse_wheel)
    canvas.bind("<Button-4>", on_canvas_mouse_wheel)
    canvas.bind("<Button-5>", on_canvas_mouse_wheel)
    canvas.bind("<Button-3>", show_cursor_controls)
    # Double-click background text binding removed

    shortcut_bar = ttk.Frame(root, padding=(8, 0, 8, 2))
    shortcut_bar.pack(fill="x")
    ttk.Label(
        shortcut_bar,
        text="Ctrl+S log | Ctrl+E stop/save | Ctrl+I next line | Ctrl+D previous line | Ctrl+W swap line direction",
        style="SurveyPlan.TLabel",
    ).pack(side="left")
    ttk.Label(shortcut_bar, textvariable=logging_started_at, style="SurveyPlan.TLabel").pack(side="right")
    show_data_display()

    if getattr(settings, "dxf_background_path", ""):
        saved_dxf_path = Path(settings.dxf_background_path)
        if saved_dxf_path.exists():
            refresh_dxf_background_from_path(saved_dxf_path, show_errors=False)
    if getattr(settings, "enc_background_path", ""):
        saved_enc_path = Path(settings.enc_background_path)
        if saved_enc_path.exists():
            try:
                # Avoid startup popups if GDAL is not present; user can reload from ENC button.
                shapes = load_enc_s57_shapes(saved_enc_path)
                enc_background_shapes = project_background_shapes(shapes)
                rebuild_background_cache(dxf_background_shapes, enc_background_shapes, kmz_background_shapes)
            except Exception:
                enc_background_shapes = []

    if getattr(settings, "kmz_background_path", ""):
        saved_kmz_path = Path(settings.kmz_background_path)
        if saved_kmz_path.exists():
            try:
                kmz_background_shapes = project_background_shapes(load_kmz_shapes(saved_kmz_path))
                rebuild_background_cache(dxf_background_shapes, enc_background_shapes, kmz_background_shapes)
            except Exception:
                kmz_background_shapes = []

    refresh_table()
    save_default_settings()

    def on_application_close():
        if _acquisition.ctx:
            _acquisition.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_application_close)
    root.bind_all("<Control-s>", start_logging)
    root.bind_all("<Control-S>", start_logging)
    root.bind_all("<Control-e>", stop_logging_and_save)
    root.bind_all("<Control-E>", stop_logging_and_save)
    root.bind_all("<Control-i>", lambda _event: (select_next_line(), "break")[-1])
    root.bind_all("<Control-I>", lambda _event: (select_next_line(), "break")[-1])
    root.bind_all("<Control-d>", lambda _event: (select_previous_line(), "break")[-1])
    root.bind_all("<Control-D>", lambda _event: (select_previous_line(), "break")[-1])
    root.bind_all("<Control-w>", lambda _event: (swap_selected_line_direction(), "break")[-1])
    root.bind_all("<Control-W>", lambda _event: (swap_selected_line_direction(), "break")[-1])
    model.geodetic = settings.geodetic
    state = locals()
    state['_refresh_state'] = lambda: {
        'settings': settings, 'model': model, 'runlines': runlines,
        'border_points': border_points, 'dxf_background_shapes': dxf_background_shapes,
        'enc_background_shapes': enc_background_shapes, 'kmz_background_shapes': kmz_background_shapes,
        'ktd_extent_shapes': ktd_extent_shapes, 'project_background_points': project_background_points,
        'project_background_files': project_background_files,
    }
    return state


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SurveyPlan merged bathymetry, NMEA, runline, and echogram utility.")
    parser.add_argument("input", nargs="*", help="Input HYPACK RAW/Edit, CSV, XYZ, TXT, NMEA, or LOG files")
    parser.add_argument("-o", "--output", default="0607.raw", help="Output HYPACK RAW .raw file")
    parser.add_argument("--tide", type=float, default=0.0, help="Tide/water-level correction")
    parser.add_argument("--draft", type=float, default=0.0, help="Transducer draft correction")
    parser.add_argument("--sound-speed", type=float, default=DEFAULT_SOUND_SPEED, help="Sound speed in m/s")
    parser.add_argument("--generate-runlines", action="store_true", help="Generate parallel runlines from a baseline")
    parser.add_argument("--navigate", action="store_true", help="Calculate realtime navigation against runlines")
    parser.add_argument("--echogram", action="store_true", help="Export an acoustic waterfall/echogram PPM image")
    parser.add_argument("--matrix", action="store_true", help="Treat echogram input CSV rows as intensity samples")
    parser.add_argument("--max-depth", type=float, help="Maximum echogram depth scale")
    parser.add_argument("--depth-bins", type=int, default=160, help="Echogram vertical sample bins")
    parser.add_argument("--settings-template", action="store_true", help="Write GPS/SBES/geodetic settings JSON")
    parser.add_argument("--project-template", action="store_true", help="Write a SurveyPlan project JSON from optional inputs")
    parser.add_argument("--nmea-report", action="store_true", help="Parse first input as NMEA and print GPS/satellite JSON")
    parser.add_argument("--gpx", help="Optional GPX output path for --nmea-report")
    parser.add_argument("--gpx-name", default="SurveyPlan Track", help="Track name for GPX export")
    parser.add_argument("--encoding", default="utf-8", help="Input text encoding for --nmea-report")
    parser.add_argument("--checksum", help="Calculate an NMEA checksum for a sentence")
    parser.add_argument("--add-checksum", action="store_true", help="With --checksum, print sentence with checksum added")
    parser.add_argument("--line-file", help="Runline CSV file for realtime navigation")
    parser.add_argument("--position", help="Single realtime position as 'x,y'")
    parser.add_argument("--start-x", type=float, help="Baseline start X/Easting")
    parser.add_argument("--start-y", type=float, help="Baseline start Y/Northing")
    parser.add_argument("--end-x", type=float, help="Baseline end X/Easting")
    parser.add_argument("--end-y", type=float, help="Baseline end Y/Northing")
    parser.add_argument("--spacing", type=float, help="Runline spacing")
    parser.add_argument("--count-each-side", type=int, default=5, help="Parallel lines to create on each side")
    parser.add_argument("--prefix", default="RL", help="Runline name prefix")
    parser.add_argument("--no-center", action="store_true", help="Do not include the center baseline")
    parser.add_argument("--gui", action="store_true", help="Start the merged PyQt6 GUI")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.gui or (
        not args.input
        and not args.generate_runlines
        and not args.navigate
        and not args.echogram
        and not args.settings_template
        and not args.project_template
        and not args.nmea_report
        and not args.checksum
    ):
        return run_gui()
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())



