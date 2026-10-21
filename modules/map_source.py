#!/usr/bin/env python3
"""HYDROGEOMATICS PyQt6 conversion build with survey logger controls and fast map screen."""

from __future__ import annotations

import csv
import base64
import hashlib
import io
import json
import math
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pyqtgraph as pg


try:
    from PyQt6.QtCore import QPoint, QPointF, QRectF, QTimer, Qt
    from PyQt6.QtGui import QAction, QColor, QFont, QPainter, QPainterPath, QPen, QTransform
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QGraphicsEllipseItem,
        QGraphicsItem,
        QGraphicsItemGroup,
        QGraphicsPathItem,
        QGraphicsScene,
        QGraphicsSimpleTextItem,
        QGraphicsView,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QRubberBand,
        QSlider,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QSplitter,
        QStatusBar,
        QTabWidget,
        QToolBar,
        QVBoxLayout,
        QWidget,
    )
except Exception as exc:  # pragma: no cover - this is a user-facing fallback.
    print("PyQt6 is required for this prototype.")
    print("Install with: python -m pip install PyQt6")
    print(f"Import error: {exc}")
    raise SystemExit(1)


SOFTWARE_TERMS = "HYDROGEOMATICS — SOFTWARE DISCLAIMER & TERMS OF USE\nCommunity release: 21 October 2026\nProject code and documentation: MIT License (see LICENSE). Third-party components retain their own licenses.\nProvider: Hydrogeomatics\nWebsite: https://www.hydrogeomatics.com.pk\n\n1. SCOPE AND USE\nThese terms apply to this software, its documentation, sample and embedded data, generated files, calculations, and updates supplied by Hydrogeomatics, subject to any separate written agreement. By using the software, you acknowledge these terms and agree to them to the extent applicable law permits. If you do not agree, discontinue use. Merely opening this section does not record acceptance or send information to Hydrogeomatics.\n\n2. PROVIDED “AS IS” AND “AS AVAILABLE”\nThe software is supplied on an AS IS and AS AVAILABLE basis, with all faults. To the fullest extent permitted by applicable law, Hydrogeomatics and its authors, developers, contributors, and suppliers disclaim all express, implied, and statutory warranties, including merchantability, fitness for a particular purpose, title, non-infringement, accuracy, reliability, completeness, availability, and satisfactory quality. No guarantee is made that the software will be error-free, uninterrupted, secure, compatible with every system or instrument, or that defects will be corrected. Informal advice or support does not create a warranty.\n\n3. ASSUMPTION OF RISK AND LIMITATION OF LIABILITY\nYou use the software and rely on its outputs at your own risk. To the fullest extent permitted by applicable law, Hydrogeomatics and its authors, developers, contributors, and suppliers accept no liability arising from installation, operation, inability to use, modification, or reliance on the software or its outputs. This exclusion includes direct, indirect, incidental, consequential, special, exemplary, and punitive damages; lost profits, revenue, contracts, business opportunities, or goodwill; data loss or corruption; operational interruption; equipment or property damage; and costs of substitute services, re-survey, reprocessing, or recovery. It applies regardless of the legal theory asserted, including contract, tort, or negligence, and even if the possibility of damage has been advised. Nothing in these terms excludes or limits liability or remedies that applicable law does not permit to be excluded or limited, including mandatory consumer rights. Where a complete exclusion is prohibited, liability is limited only to the extent lawfully permitted.\n\n4. HYDROGRAPHIC, POSITIONING, AND SAFETY LIMITATIONS\nThis software is a survey and data-processing aid. It is not represented as a certified navigation system, an official nautical chart, a collision-avoidance system, or a substitute for competent professional judgment. Do not rely on it alone for vessel navigation, safe-water or under-keel-clearance decisions, dredging control, construction, boundary determination, engineering design, or other safety-critical decisions. Use suitable independently verified equipment, official sources, and qualified personnel. The operator remains responsible for safe operations and any required approvals or certifications.\n\n5. DATA AND RESULTS MUST BE VERIFIED\nYou are responsible for checking source data, coordinate reference systems, horizontal and vertical datums, units, transformations, instrument calibration, sensor offsets, latency, time synchronization, tide corrections, GNSS/RTK quality, sounding corrections, and export settings. KTD values, four-point interpolation, grids, borders, and other calculated outputs are estimates or derived data; interpolation does not establish the accuracy of unsurveyed areas. Check corner order, coverage, resolution, NoData handling, and generated boundaries. Embedded or sample datasets may be incomplete or outdated. Independently validate all outputs against suitable control and the standards required for your project before operational use, delivery, or publication.\n\n6. BACKUPS AND SYSTEM RESPONSIBILITY\nKeep independent backups of original observations, project files, configurations, and deliverables. Test the software and updates on copies before use on production data. You are responsible for suitable hardware, operating systems, dependencies, device connections, access controls, malware protection, and recovery arrangements. Failures of power, communications, sensors, third-party libraries, or external services can affect results and availability.\n\n7. PERMITTED USE AND OWNERSHIP\nUse the software only as authorized by the applicable license or written permission and in accordance with law. This disclaimer does not transfer ownership, place the software in the public domain, or grant rights beyond an applicable license. Rights in third-party components, data, trademarks, and documentation remain with their respective owners and are governed by their own terms. Preserve applicable copyright, attribution, and license notices. Do not use the software unlawfully, misrepresent its certification or results, or infringe another person's rights.\n\n8. USER DATA, CONFIDENTIALITY, AND SUPPORT SUBMISSIONS\nYou retain your rights in your own data and are responsible for having permission to process and share it. When reporting problems, remove passwords, credentials, personal information, confidential client data, and sensitive survey or location information unless sharing is authorized and necessary. Email, WhatsApp, and linked websites are external services subject to their own terms and privacy practices; their security and availability are not guaranteed by this software. This section does not itself upload project data or send support messages.\n\n9. COMMUNITY CONTRIBUTIONS AND SUPPORT\nReport bugs, propose improvements, and contribute through the public repository:\nhttps://github.com/hydrogeomatics-ui/hydrogeomatics-hydrographic-package\nRead CONTRIBUTING.md and SECURITY.md first. Use synthetic reproductions and sanitized error text. Do not publish client data, sensitive locations, personal information, or credentials. Community support has no guaranteed response time. For private vulnerability reports, use the repository Security tab.\n\n10. UPDATES AND THIRD-PARTY SERVICES\nUpdates may change features, formats, dependencies, and compatibility. Review accompanying terms and release information, retain backups, and validate updated results before use. External links are provided for convenience and do not guarantee the availability, accuracy, or practices of an external service. Third-party licenses remain applicable; this disclaimer does not override them.\n\n11. CESSATION OF USE AND SEPARATE AGREEMENTS\nYou may cease use at any time. Rights to continued use and any termination provisions are governed by the applicable software license and law. A separate written agreement signed by the relevant parties controls to the extent it expressly changes these terms. A bug report, update request, or informal conversation alone does not amend these terms or establish a service-level agreement.\n\n12. APPLICABLE LAW, SEVERABILITY, AND NO WAIVER\nThese terms operate subject to applicable law and mandatory rights; no specific exclusive court or governing jurisdiction is designated here. If a provision is invalid or unenforceable, it applies only to the extent lawfully permitted and the remaining provisions continue in effect where permitted. Failure to enforce a provision does not by itself waive it. Any revised terms should identify their version and be made available with the relevant software release; revisions do not remove accrued mandatory rights.\n"

APP_TITLE = "HYDROGEOMATICS"
KTD_NODATA_VALUE = 999.99
_EZDXF_MODULE = None
_EZDXF_IMPORT_DONE = False
_TRANSFORMER_CLASS = None
_PYPROJ_IMPORT_DONE = False
_LL_TO_UTM42_TRANSFORMER = None
_SERIAL_MODULE = None
_SERIAL_IMPORT_DONE = False
SS510_BAUD_RATES = ("4800", "9600", "19200", "38400", "57600", "115200")
SS510_COMMANDS = (
    ("Request ID", "ID\r\n"),
    ("Start Output", "START\r\n"),
    ("Stop Output", "STOP\r\n"),
    ("Ping", "PING\r\n"),
    ("Status", "STATUS\r\n"),
)

# Extensive geodetic parameter option lists for the startup CRS/KTD confirmation wizard.
# The first two CRS entries remain the only ones wired to the actual coordinate
# transform pipeline (ll_to_utm42 / pyproj EPSG:32642<->EPSG:4326); the remaining
# entries are informational/record-keeping choices that are stored with the
# project's startup geodetic settings and can be freely typed/edited as well.
STARTUP_CRS_OPTIONS = (
    "EPSG:32642 UTM 42N",
    "EPSG:4326 Lat/Lon display only",
    "EPSG:32638 UTM 38N",
    "EPSG:32639 UTM 39N",
    "EPSG:32640 UTM 40N",
    "EPSG:32641 UTM 41N",
    "EPSG:32643 UTM 43N",
    "EPSG:32644 UTM 44N",
    "EPSG:32645 UTM 45N",
    "EPSG:3857 Web Mercator (Pseudo-Mercator)",
    "Lambert Conical Zone IA",
    "Lambert Conical Zone IB",
    "Lambert Conical Zone IIA",
    "Lambert Conical Zone IIB",
    "Lambert Conical Zone III",
    "Local Grid",
)

STARTUP_SOURCE_DATUM_OPTIONS = (
    "WGS84 / GNSS",
    "WGS84 (G2139)",
    "WGS84 (G1762)",
    "WGS84 (Original)",
    "ITRF2020",
    "ITRF2014",
    "ITRF2008",
    "ITRF2005",
    "ITRF2000",
    "ETRS89",
    "NAD83",
    "Everest 1830 (Pakistan / India)",
    "Everest 1830 (1962 Definition)",
    "Everest 1830 (1975 Definition)",
    "Kalianpur 1937",
    "Kalianpur 1962",
    "Kalianpur 1975",
    "Indian 1954",
    "Indian 1960",
    "Indian 1975",
    "Local Grid",
    "Lambert Conical Zone IA",
    "Lambert Conical Zone IB",
    "Lambert Conical Zone IIA",
    "Lambert Conical Zone IIB",
    "Lambert Conical Zone III",
)

STARTUP_TARGET_DATUM_OPTIONS = (
    "WGS84 / UTM Zone 42N",
    "WGS84 / UTM Zone 41N",
    "WGS84 / UTM Zone 43N",
    "WGS84 / UTM Zone 40N",
    "WGS84 / UTM Zone 44N",
    "WGS84 / UTM Zone 45N",
    "WGS84 Geographic",
    "ITRF2014 / UTM Zone 42N",
    "ITRF2014 Geographic",
    "ETRS89 / UTM",
    "Everest 1830 / Pakistan Belt",
    "Kalianpur 1962 / Pakistan Belt",
    "Local Grid",
    "Lambert Conical Zone IA",
    "Lambert Conical Zone IB",
    "Lambert Conical Zone IIA",
    "Lambert Conical Zone IIB",
    "Lambert Conical Zone III",
)

STARTUP_PROJECTION_OPTIONS = (
    "UTM Zone 42N",
    "UTM Zone 41N",
    "UTM Zone 43N",
    "UTM Zone 40N",
    "UTM Zone 44N",
    "UTM Zone 45N",
    "Geographic Lat/Lon",
    "Transverse Mercator (custom)",
    "Lambert Conformal Conic (generic)",
    "Lambert Conical Zone IA",
    "Lambert Conical Zone IB",
    "Lambert Conical Zone IIA",
    "Lambert Conical Zone IIB",
    "Lambert Conical Zone III",
    "Web Mercator (Pseudo-Mercator)",
    "Local Grid",
    "State/Local Plane (custom)",
)

STARTUP_VERTICAL_REFERENCE_OPTIONS = (
    "RTK tide using active KTD",
    "Manual tide file",
    "Chart Datum (fixed value)",
    "Mean Sea Level (MSL)",
    "Lowest Astronomical Tide (LAT)",
    "Indian Spring Low Water (ISLW)",
    "Mean Lower Low Water (MLLW)",
    "Geoid model based (EGM2008)",
    "Geoid model based (EGM96)",
    "Local benchmark reference",
    "No KTD / ellipsoidal height only",
)


def get_serial_module():
    global _SERIAL_MODULE, _SERIAL_IMPORT_DONE
    if not _SERIAL_IMPORT_DONE:
        try:
            import serial as module  # type: ignore

            _SERIAL_MODULE = module
        except Exception:
            _SERIAL_MODULE = None
        _SERIAL_IMPORT_DONE = True
    return _SERIAL_MODULE


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports  # type: ignore

        return sorted(port.device for port in list_ports.comports())
    except Exception:
        return []


def list_serial_ports_detailed() -> list[tuple[str, str]]:
    """Return (device, description) pairs for all available serial ports."""
    try:
        from serial.tools import list_ports  # type: ignore

        return sorted(
            (port.device, f"{port.device} — {port.description}" if port.description and port.description != "n/a" else port.device)
            for port in list_ports.comports()
        )
    except Exception:
        return []


class PortState:
    """Connection state constants for serial ports."""
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"

    _LABELS = {
        "unavailable": "Unavailable",
        "available": "Available",
        "connecting": "Connecting…",
        "connected": "Connected",
        "disconnected": "Disconnected",
        "error": "Error",
    }
    _COLORS = {
        "unavailable": "#6b7280",
        "available": "#2563eb",
        "connecting": "#d97706",
        "connected": "#16a34a",
        "disconnected": "#dc2626",
        "error": "#dc2626",
    }

    @classmethod
    def label(cls, state: str) -> str:
        return cls._LABELS.get(state, state.title())

    @classmethod
    def color(cls, state: str) -> str:
        return cls._COLORS.get(state, "#6b7280")


class SerialPortMonitor:
    """Periodically scans for serial port changes and tracks connection states."""

    def __init__(self) -> None:
        self._known_ports: set[str] = set()
        self._port_states: dict[str, str] = {}  # port_name -> PortState
        self._callbacks: list[object] = []

    @property
    def known_ports(self) -> set[str]:
        return set(self._known_ports)

    def port_state(self, port: str) -> str:
        return self._port_states.get(port, PortState.UNAVAILABLE)

    def set_port_state(self, port: str, state: str) -> None:
        old = self._port_states.get(port)
        self._port_states[port] = state
        if old != state:
            for callback in self._callbacks:
                try:
                    callback(port, state, old or PortState.UNAVAILABLE)  # type: ignore[operator]
                except Exception:
                    pass

    def scan(self) -> tuple[list[str], list[str]]:
        """Scan ports. Returns (newly_appeared, newly_disappeared) lists."""
        current = set(list_serial_ports())
        appeared = sorted(current - self._known_ports)
        disappeared = sorted(self._known_ports - current)
        self._known_ports = current

        for port in appeared:
            if port not in self._port_states or self._port_states[port] in (PortState.UNAVAILABLE, PortState.DISCONNECTED):
                self.set_port_state(port, PortState.AVAILABLE)

        for port in disappeared:
            if self._port_states.get(port) in (PortState.CONNECTED, PortState.CONNECTING):
                self.set_port_state(port, PortState.DISCONNECTED)
            else:
                self.set_port_state(port, PortState.UNAVAILABLE)

        # Clean up stale states for ports that no longer exist
        for port in list(self._port_states.keys()):
            if port not in current and self._port_states[port] not in (PortState.UNAVAILABLE, PortState.DISCONNECTED, PortState.ERROR):
                self.set_port_state(port, PortState.UNAVAILABLE)

        return appeared, disappeared

    def on_state_change(self, callback: object) -> None:
        self._callbacks.append(callback)

    def is_port_available(self, port: str) -> bool:
        return port in self._known_ports


def get_ezdxf():
    global _EZDXF_MODULE, _EZDXF_IMPORT_DONE
    if not _EZDXF_IMPORT_DONE:
        try:
            import ezdxf as module  # type: ignore

            _EZDXF_MODULE = module
        except Exception:
            _EZDXF_MODULE = None
        _EZDXF_IMPORT_DONE = True
    return _EZDXF_MODULE


def get_transformer_class():
    global _TRANSFORMER_CLASS, _PYPROJ_IMPORT_DONE
    if not _PYPROJ_IMPORT_DONE:
        try:
            from pyproj import Transformer as transformer_class  # type: ignore

            _TRANSFORMER_CLASS = transformer_class
        except Exception:
            _TRANSFORMER_CLASS = None
        _PYPROJ_IMPORT_DONE = True
    return _TRANSFORMER_CLASS


def ll_to_utm42(lat: float, lon: float) -> Optional[tuple[float, float]]:
    global _LL_TO_UTM42_TRANSFORMER
    transformer_class = get_transformer_class()
    if transformer_class is None:
        return None
    try:
        if _LL_TO_UTM42_TRANSFORMER is None:
            _LL_TO_UTM42_TRANSFORMER = transformer_class.from_crs("EPSG:4326", "EPSG:32642", always_xy=True)
        x, y = _LL_TO_UTM42_TRANSFORMER.transform(float(lon), float(lat))
        return float(x), float(y)
    except Exception:
        return None


@dataclass
class Shape:
    kind: str
    points: list[tuple[float, float]]
    label: str = ""
    color: str = "#6b7280"
    value: Optional[float] = None


@dataclass
class Layer:
    name: str
    path: Optional[Path]
    shapes: list[Shape] = field(default_factory=list)
    group: Optional[Any] = None
    visible: bool = True
    hidden: bool = False
    protected: bool = False


def find_oda_file_converter() -> Optional[Path]:
    path_hit = shutil.which("ODAFileConverter.exe") or shutil.which("ODAFileConverter")
    if path_hit:
        return Path(path_hit)
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not base:
            continue
        root = Path(base) / "ODA"
        if not root.exists():
            continue
        for candidate in list(root.glob("ODAFileConverter*/ODAFileConverter.exe")) + list(root.glob("**/ODAFileConverter.exe")):
            if candidate.exists():
                return candidate
    return None


def convert_dwg_to_dxf_with_oda(dwg_path: Path, output_folder: Optional[Path] = None, version: str = "ACAD2018") -> Path:
    converter = find_oda_file_converter()
    if converter is None:
        raise RuntimeError("ODA File Converter was not found on this computer.")
    source = Path(dwg_path)
    target_folder = Path(output_folder) if output_folder else source.parent
    target_folder.mkdir(parents=True, exist_ok=True)
    target_path = target_folder / f"{source.stem}.dxf"
    with tempfile.TemporaryDirectory(prefix="HYDROGEOMATICS_pyqt_oda_") as tmp_name:
        tmp_root = Path(tmp_name)
        tmp_in = tmp_root / "in"
        tmp_out = tmp_root / "out"
        tmp_in.mkdir()
        tmp_out.mkdir()
        shutil.copy2(source, tmp_in / "source.dwg")
        result = subprocess.run(
            [str(converter), str(tmp_in), str(tmp_out), version, "DXF", "0", "1", "*.dwg"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        converted = tmp_out / "source.dxf"
        if result.returncode != 0 or not converted.exists():
            detail = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
            raise RuntimeError(detail or f"ODA conversion failed: {source.name}")
        shutil.copy2(converted, target_path)
    return target_path


def dxf_color(index: int) -> str:
    table = {
        1: "#ef4444",
        2: "#f59e0b",
        3: "#22c55e",
        4: "#06b6d4",
        5: "#3b82f6",
        6: "#d946ef",
        7: "#111827",
        8: "#6b7280",
    }
    return table.get(int(index or 8), "#6b7280")


def is_ktd_nodata(value: float) -> bool:
    return abs(float(value) - KTD_NODATA_VALUE) < 0.005


PROTECTED_KTD_MAGIC = "HYDROGEOMATICS-KTD-PROTECTED-V1"
PROTECTED_DXF_MAGIC = "HYDROGEOMATICS-DXF-PROTECTED-V1"
EMBEDDED_PROTECTED_FILES: dict[str, str] = {}


def app_resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def protected_ktd_key() -> bytes:
    seed = f"{APP_TITLE}|hydrographic-rtk-ktd|{KTD_NODATA_VALUE}".encode("utf-8")
    return hashlib.sha256(seed).digest()


def xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))


def protect_ktd_bytes(raw: bytes) -> bytes:
    packed = zlib.compress(raw, level=9)
    protected = xor_bytes(packed, protected_ktd_key())
    return (PROTECTED_KTD_MAGIC + "\n").encode("ascii") + base64.b85encode(protected)


def unprotect_ktd_bytes(payload: bytes) -> bytes:
    header, _, body = payload.partition(b"\n")
    if header.decode("ascii", errors="replace") != PROTECTED_KTD_MAGIC:
        raise RuntimeError("Protected KTD header is not valid.")
    protected = base64.b85decode(body.strip())
    return zlib.decompress(xor_bytes(protected, protected_ktd_key()))


def protect_ktd_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(protect_ktd_bytes(source.read_bytes()))


def protect_dxf_bytes(raw: bytes) -> bytes:
    packed = zlib.compress(raw, level=9)
    protected = xor_bytes(packed, protected_ktd_key())
    return (PROTECTED_DXF_MAGIC + "\n").encode("ascii") + base64.b85encode(protected)


def unprotect_dxf_bytes(payload: bytes) -> bytes:
    header, _, body = payload.partition(b"\n")
    if header.decode("ascii", errors="replace") != PROTECTED_DXF_MAGIC:
        raise RuntimeError("Protected DXF header is not valid.")
    protected = base64.b85decode(body.strip())
    return zlib.decompress(xor_bytes(protected, protected_ktd_key()))


def protect_dxf_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(protect_dxf_bytes(source.read_bytes()))


def embedded_protected_path(name: str) -> Path:
    target = Path(tempfile.gettempdir()) / "HYDROGEOMATICS_embedded_resources" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def ensure_embedded_protected_file(name: str) -> Optional[Path]:
    payload = EMBEDDED_PROTECTED_FILES.get(name)
    if not payload:
        return None
    target = embedded_protected_path(name)
    if not target.exists():
        target.write_bytes(payload.encode("ascii"))
    return target


def arc_points(cx: float, cy: float, radius: float, start_deg: float, end_deg: float, steps: int = 48) -> list[tuple[float, float]]:
    if end_deg < start_deg:
        end_deg += 360.0
    points: list[tuple[float, float]] = []
    for i in range(max(2, steps) + 1):
        angle = math.radians(start_deg + (end_deg - start_deg) * i / max(1, steps))
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def load_dxf_shapes(path: Path) -> list[Shape]:
    ezdxf = get_ezdxf()
    if ezdxf is None:
        raise RuntimeError("ezdxf is required to load DXF files in this prototype.")
    with path.open("rb") as source:
        raw_prefix = source.read(len(PROTECTED_DXF_MAGIC))
    if path.suffix.lower() == ".hpdxf" or raw_prefix == PROTECTED_DXF_MAGIC.encode("ascii"):
        raw = unprotect_dxf_bytes(path.read_bytes())
        stream = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="ignore")
        doc = ezdxf.read(stream)
    else:
        doc = ezdxf.readfile(str(path))
    shapes: list[Shape] = []
    from ezdxf.colors import aci2rgb
    from ezdxf.path import from_hatch
    for entity in doc.modelspace():
        first_shape = len(shapes)
        kind = entity.dxftype().upper()
        layer = str(getattr(entity.dxf, "layer", path.stem))
        layer_record = doc.layers.get(layer)
        aci = int(entity.dxf.get('color', 256))
        rgb = entity.dxf.get('true_color')
        if aci == 256 and rgb is None:
            rgb = layer_record.dxf.get('true_color')
            aci = abs(layer_record.dxf.get('color', 7))
        auto_color = rgb is None and aci == 7
        if rgb is not None:
            color = f'#{int(rgb):06x}'
        else:
            rgb = aci2rgb(aci if 1 <= aci <= 255 else 7)
            color = '#111827' if auto_color else '#%02x%02x%02x' % tuple(rgb)
        if kind == 'HATCH' and entity.dxf.solid_fill:
            rings = []
            for boundary in from_hatch(entity):
                ring = [(float(v.x), float(v.y)) for v in boundary.flattening(0.5)]
                if len(ring) >= 3: rings.append(ring)
            if rings:
                shape = Shape('cad_fill', rings[0], layer, color)
                shape.rings = rings
                shapes.append(shape)
        if kind == "LINE":
            s = entity.dxf.start
            e = entity.dxf.end
            shapes.append(Shape("line", [(float(s.x), float(s.y)), (float(e.x), float(e.y))], layer, color))
        elif kind == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in entity.get_points()]
            if len(pts) >= 2:
                if bool(getattr(entity, "closed", False)) and pts[0] != pts[-1]:
                    pts.append(pts[0])
                shapes.append(Shape("line", pts, layer, color))
        elif kind == "POLYLINE":
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
            if len(pts) >= 2:
                shapes.append(Shape("line", pts, layer, color))
        elif kind == "CIRCLE":
            c = entity.dxf.center
            shapes.append(Shape("line", arc_points(float(c.x), float(c.y), float(entity.dxf.radius), 0, 360, 72), layer, color))
        elif kind == "ARC":
            c = entity.dxf.center
            shapes.append(
                Shape(
                    "line",
                    arc_points(float(c.x), float(c.y), float(entity.dxf.radius), float(entity.dxf.start_angle), float(entity.dxf.end_angle)),
                    layer,
                    color,
                )
            )
        elif kind == "POINT":
            p = entity.dxf.location
            shapes.append(Shape("point", [(float(p.x), float(p.y))], layer, color))
        elif kind in {"TEXT", "MTEXT"}:
            p = entity.dxf.insert
            label = str(entity.plain_text() if hasattr(entity, "plain_text") else entity.dxf.text)
            shapes.append(Shape("text", [(float(p.x), float(p.y))], label, color))
        for shape in shapes[first_shape:]:
            shape.cad_layer = layer
            shape.cad_auto_color = auto_color
            shape.cad_visible = not (layer_record.is_off() or layer_record.is_frozen() or entity.dxf.get('invisible', 0))
    return shapes


def parse_float(value: object) -> Optional[float]:
    try:
        return float(str(value).strip())
    except Exception:
        return None


def load_ktd_grid_meta(path: Path) -> Optional[dict[str, float | int | str]]:
    if not path.exists():
        return None
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        if not lines:
            return None
        first = [part for part in re.split(r"[,\t ]+", lines[0]) if part]
        values = [parse_float(part) for part in first]
        if len(values) >= 6 and all(value is not None for value in values[:6]):
            y_count = int(values[0] or 0)
            x_count = int(values[1] or 0)
            if x_count > 1 and y_count > 1:
                return {
                    "x_count": x_count,
                    "y_count": y_count,
                    "min_x": float(values[2] or 0.0),
                    "min_y": float(values[3] or 0.0),
                    "max_x": float(values[4] or 0.0),
                    "max_y": float(values[5] or 0.0),
                    "row_order": "bottom_up",
                }
    except Exception:
        return None
    return None


def load_ktd_shapes(path: Path, include_nodata: bool = True) -> list[Shape]:
    if path.suffix.lower() in {".ktdp", ".hpktd"} or path.read_bytes()[: len(PROTECTED_KTD_MAGIC)] == PROTECTED_KTD_MAGIC.encode("ascii"):
        matrix = read_ktd_matrix(path)
        shapes: list[Shape] = []
        for index, value in enumerate(matrix.values):
            nodata = is_ktd_nodata(value)
            if nodata and not include_nodata:
                continue
            row = index // matrix.x_count
            col = index % matrix.x_count
            x = matrix.min_x + col * matrix.dx
            y = matrix.min_y + row * matrix.dy
            shapes.append(Shape("ktd", [(x, y)], "999.99" if nodata else f"{float(value):.2f}", "#9ca3af" if nodata else "#d000ff", float(value)))
        return shapes
    shapes: list[Shape] = []
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    meta = load_ktd_grid_meta(path)
    if meta:
        x_count = int(meta.get("x_count", 0) or 0)
        y_count = int(meta.get("y_count", 0) or 0)
        min_x = float(meta.get("min_x", 0.0) or 0.0)
        min_y = float(meta.get("min_y", 0.0) or 0.0)
        max_x = float(meta.get("max_x", min_x) or min_x)
        max_y = float(meta.get("max_y", min_y) or min_y)
        dx = (max_x - min_x) / max(x_count - 1, 1)
        dy = (max_y - min_y) / max(y_count - 1, 1)
        tokens = [token for token in re.split(r"[,\t\s]+", "\n".join(lines[1:]).strip()) if token]
        limit = min(len(tokens), x_count * y_count)
        for index in range(limit):
            value = parse_float(tokens[index])
            if value is None:
                continue
            row = index // x_count
            col = index % x_count
            x = min_x + col * dx
            y = min_y + row * dy
            nodata = is_ktd_nodata(value)
            if nodata and not include_nodata:
                continue
            label = "999.99" if nodata else f"{float(value):.2f}"
            color = "#9ca3af" if nodata else "#d000ff"
            shapes.append(Shape("ktd", [(x, y)], label, color, float(value)))
        return shapes

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
        if x is None or y is None or value is None:
            continue
        nodata = is_ktd_nodata(value)
        if nodata and not include_nodata:
            continue
        shapes.append(Shape("ktd", [(float(x), float(y))], "999.99" if nodata else f"{float(value):.2f}", "#9ca3af" if nodata else "#d000ff", float(value)))
    return shapes


def load_xyz_csv_shapes(path: Path) -> list[Shape]:
    shapes: list[Shape] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t ;") if sample.strip() else csv.excel
        rows = csv.reader(handle, dialect)
        for row in rows:
            values = [parse_float(cell) for cell in row]
            nums = [value for value in values if value is not None]
            if len(nums) >= 2:
                label = f"{nums[2]:.2f}" if len(nums) >= 3 else ""
                shapes.append(Shape("point", [(float(nums[0]), float(nums[1]))], label, "#2563eb"))
    return shapes


def load_xy_line_shapes(path: Path, color: str = "#dc2626", label: str = "") -> list[Shape]:
    points: list[tuple[float, float]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        values = [parse_float(part) for part in re.split(r"[,\t ]+", line) if part]
        nums = [value for value in values if value is not None]
        if len(nums) >= 2:
            points.append((float(nums[0]), float(nums[1])))
    if len(points) >= 2:
        return [Shape("line", points, label or path.stem, color)]
    if len(points) == 1:
        return [Shape("point", points, label or path.stem, color)]
    return []


def load_shapes(path: Path) -> list[Shape]:
    suffix = path.suffix.lower()
    if suffix in {".dxf", ".hpdxf"}:
        return load_dxf_shapes(path)
    if suffix == ".dwg":
        dxf_path = convert_dwg_to_dxf_with_oda(path)
        return load_dxf_shapes(dxf_path)
    if suffix in {".ktd", ".ktdp", ".hpktd"}:
        return load_ktd_shapes(path, include_nodata=False)
    if suffix in {".csv", ".xyz", ".txt"}:
        return load_xyz_csv_shapes(path)
    raise RuntimeError(f"Unsupported prototype map file: {path.suffix}")


@dataclass
class KtdMatrix:
    path: Path
    x_count: int
    y_count: int
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    values: list[float]

    @property
    def dx(self) -> float:
        return (self.max_x - self.min_x) / max(self.x_count - 1, 1)

    @property
    def dy(self) -> float:
        return (self.max_y - self.min_y) / max(self.y_count - 1, 1)


def read_ktd_matrix(path: Path) -> KtdMatrix:
    raw = path.read_bytes()
    if raw.startswith(PROTECTED_KTD_MAGIC.encode("ascii")):
        raw = unprotect_ktd_bytes(raw)
    lines = [line.strip() for line in raw.decode("utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("KTD file is empty.")
    header = [part for part in re.split(r"[,\t ]+", lines[0]) if part]
    nums = [parse_float(part) for part in header]
    if len(nums) < 6 or any(value is None for value in nums[:6]):
        raise RuntimeError("Only matrix-style KTD files can be clipped while preserving original format.")
    y_count = int(nums[0] or 0)
    x_count = int(nums[1] or 0)
    min_x = float(nums[2] or 0.0)
    min_y = float(nums[3] or 0.0)
    max_x = float(nums[4] or min_x)
    max_y = float(nums[5] or min_y)
    tokens = [token for token in re.split(r"[,\t\s]+", "\n".join(lines[1:]).strip()) if token]
    expected = x_count * y_count
    values = [KTD_NODATA_VALUE] * expected
    for index, token in enumerate(tokens[:expected]):
        value = parse_float(token)
        values[index] = KTD_NODATA_VALUE if value is None else float(value)
    return KtdMatrix(path, x_count, y_count, min_x, min_y, max_x, max_y, values)


def write_ktd_matrix(path: Path, matrix: KtdMatrix, values: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{matrix.y_count} {matrix.x_count} {matrix.min_x:.2f} {matrix.min_y:.2f} {matrix.max_x:.2f} {matrix.max_y:.2f}\n")
        for row in range(matrix.y_count):
            start = row * matrix.x_count
            row_values = values[start : start + matrix.x_count]
            handle.write(" ".join("999.99" if is_ktd_nodata(value) else f"{float(value):.2f}" for value in row_values))
            handle.write("\n")


def ktd_value_at_xy(matrix: KtdMatrix, x: float, y: float) -> Optional[float]:
    if x < matrix.min_x or x > matrix.max_x or y < matrix.min_y or y > matrix.max_y:
        return None
    col_f = (float(x) - matrix.min_x) / matrix.dx if abs(matrix.dx) > 1e-12 else 0.0
    row_f = (float(y) - matrix.min_y) / matrix.dy if abs(matrix.dy) > 1e-12 else 0.0
    col0 = int(math.floor(col_f))
    row0 = int(math.floor(row_f))
    col1 = min(col0 + 1, matrix.x_count - 1)
    row1 = min(row0 + 1, matrix.y_count - 1)
    if row0 < 0 or col0 < 0 or row0 >= matrix.y_count or col0 >= matrix.x_count:
        return None
    tx = max(0.0, min(1.0, col_f - col0))
    ty = max(0.0, min(1.0, row_f - row0))
    samples: list[tuple[float, float]] = []
    for row, wy in ((row0, 1.0 - ty), (row1, ty)):
        for col, wx in ((col0, 1.0 - tx), (col1, tx)):
            weight = wx * wy
            if weight <= 0:
                continue
            value = matrix.values[row * matrix.x_count + col]
            if not is_ktd_nodata(value):
                samples.append((float(value), float(weight)))
    if samples:
        weight_sum = sum(weight for _value, weight in samples)
        if weight_sum > 1e-12:
            return sum(value * weight for value, weight in samples) / weight_sum
    search_radius = 3
    nearest: Optional[tuple[float, float]] = None
    for rr in range(max(0, row0 - search_radius), min(matrix.y_count, row0 + search_radius + 1)):
        for cc in range(max(0, col0 - search_radius), min(matrix.x_count, col0 + search_radius + 1)):
            value = matrix.values[rr * matrix.x_count + cc]
            if is_ktd_nodata(value):
                continue
            cx = matrix.min_x + cc * matrix.dx
            cy = matrix.min_y + rr * matrix.dy
            distance = math.hypot(cx - x, cy - y)
            if nearest is None or distance < nearest[0]:
                nearest = (distance, float(value))
    return nearest[1] if nearest is not None else None


def polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(math.hypot(points[index][0] - points[index - 1][0], points[index][1] - points[index - 1][1]) for index in range(1, len(points)))


def perpendicular_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return math.hypot(px - sx, py - sy)
    t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)))
    qx = sx + t * dx
    qy = sy + t * dy
    return math.hypot(px - qx, py - qy)


def simplify_polyline(points: list[tuple[float, float]], tolerance: float = 60.0) -> list[tuple[float, float]]:
    if len(points) <= 3:
        return list(points)
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        max_distance = -1.0
        split = start
        for index in range(start + 1, end):
            distance = perpendicular_distance(points[index], points[start], points[end])
            if distance > max_distance:
                max_distance = distance
                split = index
        if max_distance > tolerance:
            keep.add(split)
            stack.append((start, split))
            stack.append((split, end))
    return [points[index] for index in sorted(keep)]


def extract_dxf_polylines(path: Path) -> list[list[tuple[float, float]]]:
    ezdxf = get_ezdxf()
    if ezdxf is None:
        raise RuntimeError("ezdxf is required for DXF bank clipping.")
    doc = ezdxf.readfile(str(path))
    polylines: list[list[tuple[float, float]]] = []
    for entity in doc.modelspace():
        kind = entity.dxftype().upper()
        points: list[tuple[float, float]] = []
        if kind == "LWPOLYLINE":
            points = [(float(p[0]), float(p[1])) for p in entity.get_points()]
        elif kind == "POLYLINE":
            points = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        if len(points) >= 3:
            polylines.append(points)
    return polylines


def nearest_endpoint_chain(start: tuple[float, float], end: tuple[float, float], candidates: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    unused = [list(points) for points in candidates if len(points) >= 2]
    chain: list[tuple[float, float]] = []
    current = start
    for _ in range(len(unused)):
        best_index = -1
        best_reverse = False
        best_distance = float("inf")
        for index, points in enumerate(unused):
            d_start = math.hypot(points[0][0] - current[0], points[0][1] - current[1])
            d_end = math.hypot(points[-1][0] - current[0], points[-1][1] - current[1])
            if d_start < best_distance:
                best_index, best_reverse, best_distance = index, False, d_start
            if d_end < best_distance:
                best_index, best_reverse, best_distance = index, True, d_end
        if best_index < 0:
            break
        points = unused.pop(best_index)
        if best_reverse:
            points = list(reversed(points))
        if chain and points and math.hypot(points[0][0] - chain[-1][0], points[0][1] - chain[-1][1]) < 1e-6:
            chain.extend(points[1:])
        else:
            chain.extend(points)
        current = chain[-1]
        if math.hypot(current[0] - end[0], current[1] - end[1]) < max(200.0, best_distance * 1.5):
            break
    return chain


def build_river_bank_polygon(path: Path) -> list[tuple[float, float]]:
    polylines = extract_dxf_polylines(path)
    closed = [points for points in polylines if math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) < 1.0]
    if closed:
        polygon = max(closed, key=polyline_length)
        if polygon[0] != polygon[-1]:
            polygon = polygon + [polygon[0]]
        simplified = simplify_polyline(polygon, 60.0)
        if simplified[0] != simplified[-1]:
            simplified.append(simplified[0])
        return simplified
    if len(polylines) < 2:
        raise RuntimeError("River bank DXF needs at least two polylines or one closed polygon.")
    bank_a = max(polylines, key=lambda points: max(y for _x, y in points) - min(y for _x, y in points))
    remaining = [points for points in polylines if points is not bank_a]
    bank_b = nearest_endpoint_chain(bank_a[-1], bank_a[0], remaining)
    if len(bank_b) < 2:
        bank_b = max(remaining, key=polyline_length)
    polygon = list(bank_a) + list(bank_b)
    if polygon[0] != polygon[-1]:
        polygon.append(polygon[0])
    simplified = simplify_polyline(polygon, 60.0)
    if simplified[0] != simplified[-1]:
        simplified.append(simplified[0])
    return simplified


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def median_value(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)


def interpolate_row_values(row_values: list[Optional[float]]) -> list[Optional[float]]:
    known = [(index, value) for index, value in enumerate(row_values) if value is not None]
    if not known:
        return list(row_values)
    result = list(row_values)
    for row, value in known:
        result[row] = value
    first_row, first_value = known[0]
    for row in range(0, first_row):
        result[row] = first_value
    last_row, last_value = known[-1]
    for row in range(last_row + 1, len(result)):
        result[row] = last_value
    for (row_a, value_a), (row_b, value_b) in zip(known, known[1:]):
        if row_b <= row_a + 1:
            continue
        for row in range(row_a + 1, row_b):
            ratio = (row - row_a) / (row_b - row_a)
            result[row] = float(value_a) + (float(value_b) - float(value_a)) * ratio
    return result


def clip_ktd_matrix_to_polygon(
    matrix: KtdMatrix,
    polygon: list[tuple[float, float]],
    buffer_m: float = 0.0,
    interpolate_gaps: bool = False,
) -> tuple[list[float], int, int, int, int]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    min_px, max_px = min(xs) - buffer_m, max(xs) + buffer_m
    min_py, max_py = min(ys) - buffer_m, max(ys) + buffer_m
    clipped = [KTD_NODATA_VALUE] * len(matrix.values)
    inside_mask = [False] * len(matrix.values)
    kept = 0
    real_total = 0
    land_nodata = 0
    row_samples: list[list[float]] = [[] for _ in range(matrix.y_count)]
    for index, value in enumerate(matrix.values):
        if is_ktd_nodata(value):
            pass
        else:
            real_total += 1
        row = index // matrix.x_count
        col = index % matrix.x_count
        x = matrix.min_x + col * matrix.dx
        y = matrix.min_y + row * matrix.dy
        if x < min_px or x > max_px or y < min_py or y > max_py:
            continue
        if point_in_polygon(x, y, polygon):
            inside_mask[index] = True
            if is_ktd_nodata(value):
                land_nodata += 1
            else:
                clipped[index] = value
                row_samples[row].append(float(value))
                kept += 1

    filled = 0
    if interpolate_gaps:
        row_medians = [median_value(samples) for samples in row_samples]
        row_model = interpolate_row_values(row_medians)
        for index, is_inside in enumerate(inside_mask):
            if not is_inside or not is_ktd_nodata(clipped[index]):
                continue
            row = index // matrix.x_count
            value = row_model[row]
            if value is not None:
                clipped[index] = round(float(value), 2)
                filled += 1
    return clipped, kept, real_total, filled, land_nodata


class SurveyMapView(QGraphicsView):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#f8fafc"))
        self.rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.origin = QPoint()
        self.mode = "Pan"
        self._panning = False
        self._last_pan = QPoint()

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        factor = 1.20 if event.angleDelta().y() > 0 else 1 / 1.20
        before = self.mapToScene(event.position().toPoint())
        self.scale(factor, factor)
        after = self.mapToScene(event.position().toPoint())
        delta = after - before
        self.translate(delta.x(), delta.y())
        self.window().update_status_from_view()  # type: ignore[attr-defined]
        event.accept()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self.mode == "Zoom Window" and event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.rubber_band.setGeometry(self.origin.x(), self.origin.y(), 1, 1)
            self.rubber_band.show()
            return
        if event.button() in {Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton}:
            self._panning = True
            self._last_pan = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        pos = event.position().toPoint()
        scene_pos = self.mapToScene(pos)
        self.window().set_coordinate_status(scene_pos.x(), -scene_pos.y())  # type: ignore[attr-defined]
        if self.rubber_band.isVisible():
            rect = QRectF(QPointF(self.origin), QPointF(pos)).normalized().toRect()
            self.rubber_band.setGeometry(rect)
            return
        if self._panning:
            before = self.mapToScene(self._last_pan)
            after = self.mapToScene(pos)
            delta = after - before
            self._last_pan = pos
            self.translate(delta.x(), delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self.rubber_band.isVisible():
            rect = self.rubber_band.geometry()
            self.rubber_band.hide()
            if rect.width() > 8 and rect.height() > 8:
                scene_rect = self.mapToScene(rect).boundingRect()
                self.fitInView(scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self.mode = "Pan"
            self.window().set_mode("Pan")  # type: ignore[attr-defined]
            return
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.window().update_status_from_view()  # type: ignore[attr-defined]
            event.accept()
            return
        super().mouseReleaseEvent(event)


class KtdLayerItem(QGraphicsItem):
    def __init__(self, shapes: list[Shape]) -> None:
        super().__init__()
        self.cells = [
            (float(shape.points[0][0]), -float(shape.points[0][1]), str(shape.label), str(shape.color), float(shape.value or KTD_NODATA_VALUE))
            for shape in shapes
            if shape.points
        ]
        if self.cells:
            xs = [cell[0] for cell in self.cells]
            ys = [cell[1] for cell in self.cells]
            self.min_x = min(xs)
            self.min_y = min(ys)
            self.max_x = max(xs)
            self.max_y = max(ys)
            self._rect = QRectF(self.min_x - 5.0, self.min_y - 5.0, self.max_x - self.min_x + 10.0, self.max_y - self.min_y + 10.0)
            span = max(self.max_x - self.min_x, self.max_y - self.min_y, 1.0)
            self.tile_size = max(50.0, span / 140.0)
        else:
            self.min_x = self.min_y = self.max_x = self.max_y = 0.0
            self.tile_size = 100.0
            self._rect = QRectF()
        self.tiles: dict[tuple[int, int], list[tuple[float, float, str, str, float]]] = {}
        for cell in self.cells:
            tile_key = self.tile_for_point(cell[0], cell[1])
            self.tiles.setdefault(tile_key, []).append(cell)

    def tile_for_point(self, x: float, y: float) -> tuple[int, int]:
        return (math.floor((x - self.min_x) / self.tile_size), math.floor((y - self.min_y) / self.tile_size))

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        return self._rect

    def paint(self, painter: QPainter, option, widget=None) -> None:  # type: ignore[override]
        exposed = option.exposedRect.adjusted(-10, -10, 10, 10)
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        radius = 2.6 / max(lod, 0.25)
        radius = max(1.1, min(radius, 4.5))
        valid_pen = QPen(QColor("#d000ff"), 0)
        valid_brush = QColor("#d000ff")
        nodata_pen = QPen(QColor("#9ca3af"), 0)
        nodata_brush = QColor("#9ca3af")
        label_limit = 9000 if lod > 0.04 else 4500 if lod > 0.015 else 1200
        visible_cells: list[tuple[float, float, str, str, float]] = []
        min_tx = math.floor((exposed.left() - self.min_x) / self.tile_size)
        max_tx = math.floor((exposed.right() - self.min_x) / self.tile_size)
        min_ty = math.floor((exposed.top() - self.min_y) / self.tile_size)
        max_ty = math.floor((exposed.bottom() - self.min_y) / self.tile_size)
        for tx in range(min_tx, max_tx + 1):
            for ty in range(min_ty, max_ty + 1):
                visible_cells.extend(self.tiles.get((tx, ty), []))
        label_stride = max(1, len(visible_cells) // max(label_limit, 1)) if label_limit else len(visible_cells) + 1
        point_limit = 90000 if lod < 0.02 else 180000 if lod < 0.05 else 500000
        point_stride = max(1, len(visible_cells) // point_limit)
        for index, (x, y, label, _color, value) in enumerate(visible_cells):
            if index % point_stride:
                continue
            if not exposed.contains(QPointF(x, y)):
                continue
            nodata = is_ktd_nodata(value)
            painter.setPen(nodata_pen if nodata else valid_pen)
            painter.setBrush(nodata_brush if nodata else valid_brush)
            painter.drawEllipse(QPointF(x, y), radius, radius)

        labels_drawn = 0
        font = QFont("Arial", 9)
        font.setPointSizeF(max(8.0, min(42.0, 8.0 / max(lod, 0.08))))
        font.setBold(True)
        painter.setFont(font)
        label_offset = max(4.0, min(55.0, 7.5 / max(lod, 0.08)))
        for index, (x, y, label, _color, value) in enumerate(visible_cells):
            if labels_drawn >= label_limit or index % label_stride:
                continue
            if not exposed.contains(QPointF(x, y)):
                continue
            nodata = is_ktd_nodata(value)
            painter.setPen(QPen(QColor("#4b5563" if nodata else "#d000ff"), 0))
            painter.drawText(QPointF(x + label_offset, y - label_offset), label)
            labels_drawn += 1


class LiveSurveyItem(QGraphicsItem):
    def __init__(self) -> None:
        super().__init__()
        self.track_points: list[tuple[float, float]] = []
        self.soundings: list[tuple[float, float, float]] = []
        self._rect = QRectF(-100.0, -100.0, 200.0, 200.0)

    def update_data(self, track_points: list[tuple[float, float]], soundings: list[tuple[float, float, float]]) -> None:
        self.prepareGeometryChange()
        self.track_points = list(track_points[-20000:])
        self.soundings = list(soundings[-20000:])
        xs = [p[0] for p in self.track_points] + [p[0] for p in self.soundings]
        ys = [-p[1] for p in self.track_points] + [-p[1] for p in self.soundings]
        if xs and ys:
            self._rect = QRectF(min(xs) - 25.0, min(ys) - 25.0, max(xs) - min(xs) + 50.0, max(ys) - min(ys) + 50.0)
        else:
            self._rect = QRectF(-100.0, -100.0, 200.0, 200.0)
        self.update()

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        return self._rect

    def paint(self, painter: QPainter, option, widget=None) -> None:  # type: ignore[override]
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        exposed = option.exposedRect.adjusted(-20, -20, 20, 20)
        dot_radius = max(0.8, min(2.2, 1.7 / max(lod, 0.25)))
        painter.setPen(QPen(QColor("#0f766e"), 0))
        painter.setBrush(QColor("#0f766e"))
        stride = max(1, len(self.track_points) // 8000)
        for index, (x, y) in enumerate(self.track_points):
            if index % stride:
                continue
            scene_y = -y
            if exposed.contains(QPointF(x, scene_y)):
                painter.drawEllipse(QPointF(x, scene_y), dot_radius, dot_radius)

        label_limit = 3500 if lod > 0.03 else 0
        label_stride = max(1, len(self.soundings) // max(label_limit, 1)) if label_limit else len(self.soundings) + 1
        painter.setFont(QFont("Times New Roman", 8))
        painter.setPen(QPen(QColor("#111827"), 0))
        for index, (x, y, depth) in enumerate(self.soundings):
            if not label_limit or index % label_stride:
                continue
            scene_y = -y
            if not exposed.contains(QPointF(x, scene_y)):
                continue
            label = f"{depth:.1f}"
            painter.drawText(QPointF(x + 1.5 / max(lod, 0.25), scene_y - 1.5 / max(lod, 0.25)), label)


class UTMCoordinateAxis(pg.AxisItem):
    """Absolute project-grid metres, matching the cursor coordinate display."""
    def tickStrings(self, values, scale, spacing):
        return [f"{float(value):,.3f}" if math.isfinite(float(value)) else "" for value in values]


class PyQtGraphSurveyMap(pg.PlotWidget):
    def __init__(self, owner: Any = None) -> None:
        super().__init__(axisItems={"left": UTMCoordinateAxis(orientation="left"), "bottom": UTMCoordinateAxis(orientation="bottom")})
        for name in ("left", "bottom"):
            self.getAxis(name).enableAutoSIPrefix(False)
        self.getAxis("left").setWidth(140)
        self.owner = owner
        self.mode = "Pan"
        self._editing_vertex = False
        self._measuring = False
        self.measure_start: Optional[tuple[float, float]] = None
        self.measure_line = pg.PlotDataItem([], [], pen=pg.mkPen("#ef4444", width=2))
        self.measure_label = pg.TextItem("", color=(239, 68, 68), anchor=(0, 1))
        self.setBackground("w")
        self.showGrid(x=True, y=True, alpha=0.22)
        self.setAspectLocked(True)
        self.setLabel("bottom", "Easting")
        self.setLabel("left", "Northing")
        self.plotItem.vb.setMouseMode(pg.ViewBox.PanMode)
        self.live_track_item = pg.ScatterPlotItem(pxMode=True, pen=None, brush=pg.mkBrush(15, 118, 110, 190), size=5)
        self.live_sounding_items: list[pg.TextItem] = []
        self.addItem(self.live_track_item)
        self.addItem(self.measure_line)
        self.addItem(self.measure_label)
        self.measure_line.setVisible(False)
        self.measure_label.setVisible(False)
        self.scene().sigMouseMoved.connect(self._on_mouse_moved)

    def _on_mouse_moved(self, pos: QPointF) -> None:
        if self.owner is None:
            return
        if self.sceneBoundingRect().contains(pos):
            point = self.plotItem.vb.mapSceneToView(pos)
            try:
                self.owner.set_coordinate_status(float(point.x()), float(point.y()))
            except Exception:
                pass

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.plotItem.vb.setMouseMode(pg.ViewBox.RectMode if mode == "Zoom Window" else pg.ViewBox.PanMode)
        if mode != "Measure":
            self._measuring = False

    def event_to_map_xy(self, event) -> tuple[float, float]:
        scene_pos = self.mapToScene(event.position().toPoint())
        point = self.plotItem.vb.mapSceneToView(scene_pos)
        return float(point.x()), float(point.y())

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        owner = self.owner
        if owner is not None and getattr(owner, "_consume_planning_click", None):
            if owner._consume_planning_click(event, self):
                event.accept()
                return
        picker = getattr(self, "corner_picker", None)
        if picker is not None:
            if event.button() == Qt.MouseButton.RightButton:
                picker.cancel_pick()
            elif event.button() == Qt.MouseButton.LeftButton:
                scene_pos = self.mapToScene(event.position().toPoint())
                if self.plotItem.vb.sceneBoundingRect().contains(scene_pos):
                    picker.map_click(*self.event_to_map_xy(event))
            event.accept()
            return
        if self.mode == "Measure" and event.button() == Qt.MouseButton.LeftButton:
            self.measure_start = self.event_to_map_xy(event)
            self._measuring = True
            self.update_measure_overlay(*self.measure_start)
            event.accept()
            return
        if self.mode == "Edit DXF" and event.button() == Qt.MouseButton.LeftButton and self.owner is not None:
            x, y = self.event_to_map_xy(event)
            try:
                self._editing_vertex = bool(self.owner.select_edit_vertex(x, y))
            except Exception:
                self._editing_vertex = False
            if self._editing_vertex:
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.owner is not None:
            cancel = getattr(self.owner, 'context', {}).get('view_state', {}).get('line_pick_cancel')
            if cancel:
                cancel();event.accept();return
        picker = getattr(self, "corner_picker", None)
        if picker is not None and event.key() == Qt.Key.Key_Escape:
            picker.cancel_pick()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.mode == "Measure" and self._measuring and self.measure_start is not None:
            x, y = self.event_to_map_xy(event)
            self.update_measure_overlay(x, y)
            event.accept()
            return
        if self.mode == "Edit DXF" and self._editing_vertex and self.owner is not None:
            x, y = self.event_to_map_xy(event)
            try:
                self.owner.move_selected_vertex(x, y)
            except Exception:
                pass
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self.mode == "Measure" and self._measuring:
            x, y = self.event_to_map_xy(event)
            self.update_measure_overlay(x, y)
            self._measuring = False
            event.accept()
            return
        if self.mode == "Edit DXF" and self._editing_vertex and self.owner is not None:
            try:
                self.owner.finish_edit_vertex()
            except Exception:
                pass
            self._editing_vertex = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def update_measure_overlay(self, x: float, y: float) -> None:
        if self.measure_start is None:
            return
        x0, y0 = self.measure_start
        dx = float(x) - x0
        dy = float(y) - y0
        distance = math.hypot(dx, dy)
        bearing = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
        self.measure_line.setData([x0, float(x)], [y0, float(y)])
        self.measure_line.setVisible(True)
        self.measure_label.setText(f"{distance:,.2f} m  Brg {bearing:06.2f}")
        self.measure_label.setPos(float(x), float(y))
        self.measure_label.setVisible(True)
        if self.owner is not None:
            try:
                self.owner.status.showMessage(f"Measure: {distance:,.2f} m | Bearing {bearing:06.2f}", 4000)
            except Exception:
                pass

    def set_map_background(self, color: str) -> None:
        self.setBackground(color)
        self._apply_cad_contrast(self.scene().items())

    def zoom_by(self, factor: float) -> None:
        scale = 1.0 / max(float(factor), 0.0001)
        self.plotItem.vb.scaleBy((scale, scale))

    def pan_fraction(self, dx_fraction: float, dy_fraction: float) -> None:
        x_range, y_range = self.plotItem.vb.viewRange()
        dx = (x_range[1] - x_range[0]) * float(dx_fraction)
        dy = (y_range[1] - y_range[0]) * float(dy_fraction)
        self.plotItem.vb.setXRange(x_range[0] + dx, x_range[1] + dx, padding=0)
        self.plotItem.vb.setYRange(y_range[0] + dy, y_range[1] + dy, padding=0)

    def add_shapes_layer(self, shapes: list[Shape]) -> list[Any]:
        cad = [s for s in shapes if hasattr(s, 'cad_layer')]
        items = self._add_non_cad_shapes([s for s in shapes if not hasattr(s, 'cad_layer')])
        lines = {}
        for s in cad:
            if not s.points: continue
            item = None
            if s.kind == 'cad_fill':
                path = QPainterPath()
                path.setFillRule(Qt.FillRule.OddEvenFill)
                for ring in s.rings:
                    path.moveTo(*ring[0])
                    for p in ring[1:]: path.lineTo(*p)
                    path.closeSubpath()
                item = QGraphicsPathItem(path)
                item.setPen(QPen(Qt.PenStyle.NoPen))
                item.setBrush(QColor(s.color))
                item._cad_kind = 0
            elif s.kind == 'line':
                points = lines.setdefault((s.cad_layer, s.color, s.cad_visible, s.cad_auto_color), [])
                points.extend(s.points); points.append((float('nan'), float('nan')))
            elif s.kind == 'text':
                item = pg.TextItem(s.label, color=s.color, anchor=(0, 1))
                item.setFont(QFont('Arial', 12))
                item.setPos(*s.points[0]); item._cad_kind = 2
            elif s.kind == 'point':
                item = pg.ScatterPlotItem([s.points[0][0]], [s.points[0][1]], brush=s.color, pen=None, size=5)
                item._cad_kind = 1
            if item is not None:
                item._cad_layer = s.cad_layer
                item._cad_auto_color = s.cad_auto_color
                item._entity_visible = s.cad_visible
                self.addItem(item); items.append(item)
        for (name, color, visible, auto_color), points in lines.items():
            arr = np.asarray(points)
            item = pg.PlotDataItem(arr[:,0], arr[:,1], connect='finite', pen=pg.mkPen(color, width=1))
            item._cad_layer = name; item._cad_kind = 1; item._entity_visible = visible
            item._cad_auto_color = auto_color
            self.addItem(item); items.append(item)
        self._apply_cad_contrast(items)
        return items

    def _apply_cad_contrast(self, items):
        color = '#111827' if self.backgroundBrush().color().lightness() > 128 else '#ffffff'
        for item in items:
            if not getattr(item, '_cad_auto_color', False): continue
            if isinstance(item, pg.TextItem): item.setColor(color)
            elif isinstance(item, pg.PlotDataItem): item.setPen(pg.mkPen(color, width=1))
            elif isinstance(item, pg.ScatterPlotItem): item.setBrush(pg.mkBrush(color))
            elif isinstance(item, QGraphicsPathItem): item.setBrush(QColor(color))

    def _add_non_cad_shapes(self, shapes: list[Shape]) -> list[Any]:
        items: list[Any] = []
        ktd_x: list[float] = []
        ktd_y: list[float] = []
        ktd_v: list[float] = []
        point_x: list[float] = []
        point_y: list[float] = []
        point_labels: list[str] = []
        line_groups = {}

        for shape in shapes:
            if not shape.points:
                continue
            if shape.kind == "ktd":
                value = float(shape.value if shape.value is not None else KTD_NODATA_VALUE)
                if is_ktd_nodata(value):
                    continue
                ktd_x.append(float(shape.points[0][0]))
                ktd_y.append(float(shape.points[0][1]))
                ktd_v.append(value)
            elif shape.kind == "point":
                point_x.append(float(shape.points[0][0]))
                point_y.append(float(shape.points[0][1]))
                point_labels.append(str(shape.label or ""))
            elif shape.kind == "line" and len(shape.points) >= 2:
                points = line_groups.setdefault(shape.color or "#64748b", [])
                points.extend(shape.points)
                points.append((float("nan"), float("nan")))
            elif shape.kind == "text":
                label = pg.TextItem(str(shape.label)[:80], color=shape.color or "#111827", anchor=(0, 1))
                label.setPos(float(shape.points[0][0]), float(shape.points[0][1]))
                self.addItem(label)
                items.append(label)

        for color, points in line_groups.items():
            arr = np.asarray(points, dtype=float)
            item = pg.PlotDataItem(arr[:, 0], arr[:, 1], connect="finite", pen=pg.mkPen(color, width=1))
            self.addItem(item)
            items.append(item)

        if ktd_x:
            x_arr = np.asarray(ktd_x, dtype=float)
            y_arr = np.asarray(ktd_y, dtype=float)
            v_arr = np.asarray(ktd_v, dtype=float)
            min_x, max_x = float(np.min(x_arr)), float(np.max(x_arr))
            min_y, max_y = float(np.min(y_arr)), float(np.max(y_arr))
            grid_x: list[float] = []
            grid_y: list[float] = []
            start_x = math.floor(min_x / 100.0) * 100.0
            end_x = math.ceil(max_x / 100.0) * 100.0
            start_y = math.floor(min_y / 100.0) * 100.0
            end_y = math.ceil(max_y / 100.0) * 100.0
            value = start_x
            while value <= end_x:
                grid_x.extend([value, value, float("nan")])
                grid_y.extend([start_y, end_y, float("nan")])
                value += 100.0
            value = start_y
            while value <= end_y:
                grid_x.extend([start_x, end_x, float("nan")])
                grid_y.extend([value, value, float("nan")])
                value += 100.0
            if grid_x:
                grid_item = pg.PlotDataItem(np.asarray(grid_x), np.asarray(grid_y), pen=pg.mkPen((210, 0, 255, 45), width=0.6))
                self.addItem(grid_item)
                items.append(grid_item)
            scatter = pg.ScatterPlotItem(x=x_arr, y=y_arr, pxMode=True, pen=None, brush=pg.mkBrush(210, 0, 255, 190), size=5)
            self.addItem(scatter)
            items.append(scatter)
            label_stride = 1 if len(x_arr) <= 10000 else max(1, len(x_arr) // 10000)
            for xi, yi, vi in zip(x_arr[::label_stride], y_arr[::label_stride], v_arr[::label_stride]):
                label = pg.TextItem(f"{vi:.2f}", color=(210, 0, 255), anchor=(0, 1))
                label.setPos(float(xi), float(yi))
                self.addItem(label)
                items.append(label)

        if point_x:
            scatter = pg.ScatterPlotItem(x=np.asarray(point_x), y=np.asarray(point_y), pxMode=True, pen=None, brush=pg.mkBrush(37, 99, 235, 190), size=6)
            self.addItem(scatter)
            items.append(scatter)
            label_stride = max(1, len(point_x) // 500)
            for xi, yi, label_text in zip(point_x[::label_stride], point_y[::label_stride], point_labels[::label_stride]):
                if label_text:
                    label = pg.TextItem(label_text[:32], color=(17, 24, 39), anchor=(0, 1))
                    label.setPos(float(xi), float(yi))
                    self.addItem(label)
                    items.append(label)
        return items

    def remove_layer_items(self, items: Optional[list[Any]]) -> None:
        for item in list(items or []):
            try:
                self.removeItem(item)
            except Exception:
                pass

    def clear_background_items(self, layer_items: list[list[Any]]) -> None:
        for items in layer_items:
            self.remove_layer_items(items)

    def update_live(self, track_points: list[tuple[float, float]], soundings: list[tuple[float, float, float]]) -> None:
        if track_points:
            arr = np.asarray(track_points[-30000:], dtype=float)
            self.live_track_item.setData(x=arr[:, 0], y=arr[:, 1])
        else:
            self.live_track_item.setData([], [])
        for item in self.live_sounding_items:
            self.removeItem(item)
        self.live_sounding_items.clear()
        if soundings:
            stride = max(1, len(soundings) // 3500)
            for x, y, depth in soundings[-30000::stride]:
                label = pg.TextItem(f"{depth:.1f}", color=(17, 24, 39), anchor=(0, 1))
                label.setPos(float(x), float(y))
                self.addItem(label)
                self.live_sounding_items.append(label)

    def visible_bounds(self) -> QRectF:
        x_range, y_range = self.plotItem.vb.viewRange()
        return QRectF(float(x_range[0]), float(y_range[0]), float(x_range[1] - x_range[0]), float(y_range[1] - y_range[0]))

    def fit_bounds(self, bounds: Optional[tuple[float, float, float, float]]) -> None:
        if not bounds:
            self.autoRange()
            return
        min_x, min_y, max_x, max_y = bounds
        if max_x <= min_x or max_y <= min_y:
            return
        self.setXRange(min_x, max_x, padding=0.05)
        self.setYRange(min_y, max_y, padding=0.05)


def validate_four_corners(points):
    p = np.asarray(points, dtype=float)
    if p.shape != (4, 2) or not np.isfinite(p).all():
        raise ValueError("Enter finite Easting and Northing coordinates for all four corners.")
    edges = np.roll(p, -1, axis=0) - p
    cross = edges[:, 0] * np.roll(edges, -1, axis=0)[:, 1] - edges[:, 1] * np.roll(edges, -1, axis=0)[:, 0]
    tolerance = max(float(np.max(np.abs(edges))) ** 2 * 1e-12, 1e-16)
    if not (np.all(cross > tolerance) or np.all(cross < -tolerance)):
        raise ValueError("Corners must form a convex area in clockwise or counterclockwise order, without duplicates or crossing edges.")
    return p


def interpolate_four_corners(points, values, queries):
    """Piecewise linear interpolation on triangles 1-2-3 and 1-3-4."""
    p = validate_four_corners(points)
    k = np.asarray(values, dtype=float)
    if k.shape != (4,) or not np.isfinite(k).all() or any(is_ktd_nodata(v) for v in k):
        raise ValueError("Enter four finite K values; 999.99 is reserved for NoData.")
    q = np.asarray(queries, dtype=float).reshape(-1, 2)
    result = np.full(len(q), KTD_NODATA_VALUE)
    inside = np.zeros(len(q), dtype=bool)
    for indices in ((0, 1, 2), (0, 2, 3)):
        a, b, c = p[list(indices)]
        uv = np.linalg.solve(np.column_stack((b - a, c - a)), (q - a).T).T
        weights = np.column_stack((1 - uv.sum(axis=1), uv))
        mask = np.all(weights >= -1e-10, axis=1)
        result[mask] = weights[mask] @ k[list(indices)]
        inside |= mask
    if any(is_ktd_nodata(v) for v in result[inside]):
        raise ValueError("Interpolation produces reserved NoData value 999.99; adjust the K values.")
    return result, inside


class FourCornerDialog(QDialog):
    def __init__(self, owner, with_values=False):
        super().__init__(owner)
        self.owner = owner
        self.with_values = with_values
        self.setWindowTitle("Four corners — map coordinates")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Enter corners around the perimeter (clockwise or counterclockwise).\nCoordinates use the map's Easting/Northing system. K values are in metres."))
        self.table = QTableWidget(4, 3 if with_values else 2)
        self.table.setHorizontalHeaderLabels(["Easting", "Northing", "K"] if with_values else ["Easting", "Northing"])
        for row in range(4):
            for col in range(self.table.columnCount()):
                self.table.setItem(row, col, QTableWidgetItem(""))
        layout.addWidget(self.table)
        self.message = QLabel("Pick all four corners on the main map, or type coordinates above.")
        layout.addWidget(self.message)
        for text, callback in (("Pick four corners on map", self.pick), ("Use these corners", self.confirm), ("Cancel", self.reject)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            layout.addWidget(button)
        self.finished.connect(self.cleanup)
        self.resize(590, 370)

    def pick(self):
        view = self.owner.view
        self.cleanup()
        previous = getattr(view, "corner_picker", None)
        if previous is not None and previous != self:
            previous.cancel_pick()
        self.picked = []
        view.corner_picker = self
        self.overlay = pg.PlotDataItem([], [], pen=pg.mkPen("#dc2626", width=2), symbol="o", symbolSize=9)
        view.addItem(self.overlay)
        self.hide()
        self.owner.raise_()
        view.setFocus()
        self.owner.status.showMessage("Select corner 1 of 4. Right-click or Escape cancels selection.")

    def map_click(self, x, y):
        row = len(self.picked)
        self.picked.append((x, y))
        for col, value in enumerate((x, y)):
            self.table.item(row, col).setText(f"{value:.8f}")
        self.overlay.setData([p[0] for p in self.picked], [p[1] for p in self.picked])
        if len(self.picked) == 4:
            self.cancel_pick()
            self.message.setText("Four corners selected. Review coordinates and enter K values before continuing.")
        else:
            self.owner.status.showMessage(f"Select corner {len(self.picked) + 1} of 4. Right-click or Escape cancels.")

    def cleanup(self, *_):
        if getattr(self.owner.view, "corner_picker", None) is self:
            self.owner.view.corner_picker = None
        if getattr(self, "overlay", None) is not None:
            self.owner.view.removeItem(self.overlay)
            self.overlay = None
        self.owner.status.clearMessage()

    def cancel_pick(self):
        self.cleanup()
        self.show()
        self.raise_()
        self.activateWindow()

    def confirm(self):
        try:
            rows = [[float(self.table.item(r, c).text()) for c in range(self.table.columnCount())] for r in range(4)]
            self.points = validate_four_corners([r[:2] for r in rows]).tolist()
            self.values = [r[2] for r in rows] if self.with_values else []
            if self.with_values:
                interpolate_four_corners(self.points, self.values, self.points)
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, "Four corners", str(exc))


class MatrixKtdEditor(QMainWindow):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.matrix = None
        self.setWindowTitle("KTD Matrix Editor — four corners")
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(QLabel("Create a regular KTD grid or edit an existing matrix. Interpolation uses\ntriangles 1–2–3 and 1–3–4; values outside the selected area remain unchanged."))
        options = QHBoxLayout()
        self.nx, self.ny = QSpinBox(), QSpinBox()
        for label, spin in (("Columns", self.nx), ("Rows", self.ny)):
            spin.setRange(2, 500)
            spin.setValue(21)
            options.addWidget(QLabel(label))
            options.addWidget(spin)
        layout.addLayout(options)
        buttons = QHBoxLayout()
        for label, callback in (("New from four corners", self.new_grid), ("Open matrix KTD", self.open_grid), ("Interpolate from four points", self.interpolate), ("Save KTD as…", self.save_grid)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Easting", "Northing", "K (editable)"])
        layout.addWidget(self.table)
        self.status = QLabel("Create or open a matrix KTD. Maximum 50,000 cells in this editor.")
        layout.addWidget(self.status)
        self.setCentralWidget(root)
        self.resize(950, 650)

    def corners(self, callback):
        self.corner_dialog = FourCornerDialog(self.owner, True)
        self.corner_dialog.accepted.connect(lambda: callback(self.corner_dialog.points, self.corner_dialog.values))
        self.corner_dialog.show()

    def queries(self):
        m = self.matrix
        xx, yy = np.meshgrid(np.linspace(m.min_x, m.max_x, m.x_count), np.linspace(m.min_y, m.max_y, m.y_count))
        return np.column_stack((xx.ravel(), yy.ravel()))

    def refresh(self):
        self.table.setRowCount(len(self.matrix.values))
        for row, ((x, y), k) in enumerate(zip(self.queries(), self.matrix.values)):
            for col, value in enumerate((x, y, k)):
                item = QTableWidgetItem(f"{value:.8f}")
                if col < 2:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.status.setText(f"{self.matrix.x_count} columns × {self.matrix.y_count} rows. 999.99 = NoData. Save to write changes.")

    def collect(self):
        if self.matrix is None:
            raise ValueError("Create or open a matrix first.")
        values = []
        for row in range(self.table.rowCount()):
            try:
                value = float(self.table.item(row, 2).text())
                if not math.isfinite(value):
                    raise ValueError()
            except ValueError:
                raise ValueError(f"Enter a finite K value at row {row + 1}.")
            values.append(value)
        self.matrix.values = values

    def new_grid(self):
        if self.nx.value() * self.ny.value() > 50000:
            QMessageBox.warning(self, "Grid size", "Use at most 50,000 cells.")
            return
        self.corners(self.create_grid)

    def create_grid(self, points, values):
        if self.nx.value() * self.ny.value() > 50000:
            QMessageBox.warning(self, "Grid size", "Use at most 50,000 cells.")
            return
        p = validate_four_corners(points)
        m = KtdMatrix(Path("new.ktd"), self.nx.value(), self.ny.value(), *p.min(axis=0), *p.max(axis=0), [])
        previous = self.matrix
        try:
            self.matrix = m
            result, mask = interpolate_four_corners(points, values, self.queries())
            if not mask.any():
                raise ValueError("No grid nodes fall inside the area. Increase grid resolution.")
            m.values = result.tolist()
            self.refresh()
        except ValueError as exc:
            self.matrix = previous
            QMessageBox.warning(self, "Create KTD", str(exc))

    def open_grid(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open matrix KTD", str(self.owner.project_folder), "KTD (*.ktd *.KTD *.hpktd *.ktdp)")
        if not name:
            return
        try:
            matrix = read_ktd_matrix(Path(name))
            if matrix.x_count < 2 or matrix.y_count < 2 or len(matrix.values) > 50000 or matrix.dx <= 0 or matrix.dy <= 0:
                raise ValueError("Use a regular matrix with 2 or more rows/columns and at most 50,000 cells.")
            self.matrix = matrix
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Open KTD", str(exc))

    def interpolate(self):
        try:
            self.collect()
        except ValueError as exc:
            QMessageBox.warning(self, "Interpolate", str(exc))
            return
        self.corners(self.apply_interpolation)

    def apply_interpolation(self, points, values):
        try:
            self.collect()
            result, mask = interpolate_four_corners(points, values, self.queries())
            if not mask.any():
                raise ValueError("No existing grid nodes are inside the selected area.")
            data = np.asarray(self.matrix.values)
            data[mask] = result[mask]
            self.matrix.values = data.tolist()
            self.refresh()
            self.status.setText(f"Interpolated {int(mask.sum())} cells. Save to write changes.")
        except ValueError as exc:
            QMessageBox.warning(self, "Interpolate", str(exc))

    def save_grid(self):
        try:
            self.collect()
            name, _ = QFileDialog.getSaveFileName(self, "Save matrix KTD", str(self.owner.project_folder / "KTD" / "four_corner.ktd"), "KTD (*.ktd)")
            if not name:
                return
            target = Path(name).with_suffix(".ktd")
            # Preserve coordinate and K precision in newly edited matrices.
            target.parent.mkdir(parents=True, exist_ok=True)
            m = self.matrix
            with target.open("w", encoding="utf-8") as handle:
                handle.write(f"{m.y_count} {m.x_count} {m.min_x:.8f} {m.min_y:.8f} {m.max_x:.8f} {m.max_y:.8f}\n")
                for row in range(m.y_count):
                    handle.write(" ".join(f"{v:.8f}" for v in m.values[row*m.x_count:(row+1)*m.x_count]) + "\n")
            m.path = target
            self.owner.add_layer(Layer(target.name, target, load_ktd_shapes(target, False)))
            self.owner.fit_extents()
            self.status.setText(f"Saved and displayed {target}. Load in the original KTD Editor to apply as RTK tide KTD.")
        except Exception as exc:
            QMessageBox.warning(self, "Save KTD", str(exc))


def show_four_corner_border(owner):
    dialog = FourCornerDialog(owner)
    owner.four_corner_border_dialog = dialog
    def save():
        name, _ = QFileDialog.getSaveFileName(owner, "Save four-corner border", str(owner.project_folder / "EXPORT" / "four_corner.brd"), "Border (*.brd)")
        if not name:
            return
        try:
            target = Path(name).with_suffix(".brd")
            target.parent.mkdir(parents=True, exist_ok=True)
            points = [tuple(p) for p in dialog.points]
            points.append(points[0])
            target.write_text("".join(f"{x:.8f} {y:.8f}\n" for x, y in points), encoding="utf-8")
            owner.add_layer(Layer(f"BORDER {target.name}", target, [Shape("line", points, target.stem, "#dc2626")]))
            owner.fit_extents()
            owner.status.showMessage(f"Saved four-corner border: {target}", 10000)
        except Exception as exc:
            QMessageBox.warning(owner, "Save border", str(exc))
    dialog.accepted.connect(save)
    dialog.show()

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 820)
        self.layers: list[Layer] = []
        self.project_folder = Path(globals().get("MERGED_PROJECT_FOLDER", Path(__file__).resolve().parent / "SurveyPlan_Project"))
        self.logging_active = False
        self.log_file_path: Optional[Path] = None
        self.log_file_handle = None
        self.gnss_port = ""
        self.gnss_baud = 9600
        self.sbes_port = ""
        self.sbes_baud = 9600
        self.shared_gnss_sbes_port = False
        self.sound_velocity = 1500.0
        self.antenna_offset_x = 0.0
        self.antenna_offset_y = 0.0
        self.transducer_offset_z = 0.0
        self.ktd_file_path = ""
        self.use_rtk_tide_method = False
        self.chart_datum_level = 0.00
        self.enabled_nmea_sentences: dict[str, bool] = {
            "GGA": True, "RMC": True, "GSA": True, "GSV": True, "GLL": True,
            "VTG": True, "HDT": True, "GST": True, "GNS": True, "GBS": False,
            "ZDA": True, "DTM": False, "THS": False, "ROT": False,
            "PTNL,GGK": False, "PTNL,QA": False, "BESTPOSA": True, "BESTPOS": True,
        }
        self.accuracy_monitor_window: Optional[QMainWindow] = None
        self.latest_accuracy: dict[str, Optional[float]] = {"x": None, "y": None, "z": None}
        self.project_folder.mkdir(parents=True, exist_ok=True)
        self.load_active_ktd_reference()
        self.mbes_setup = {
            "sonar_model": "Cerulean Surveyor 240-16 / Generic MBES",
            "gnss_model": "",
            "motion_model": "",
            "heading_model": "",
            "svp_model": "",
            "tide_source": "",
            "time_sync": "GNSS PPS / NMEA ZDA",
            "sonar_udp": "4001",
            "gnss_udp": "4002",
            "motion_udp": "4003",
            "heading_udp": "4004",
        }
        self.device_queue: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self.device_stop = threading.Event()
        self.device_threads: list[threading.Thread] = []
        self.device_thread_states: dict[str, str] = {}  # "GNSS"/"SBES" -> PortState
        self.monitor_windows: list[QPlainTextEdit] = []
        self.latest_values: dict[str, str] = {"GNSS": "", "SBES": "", "DEPTH": "", "POSITION": "", "EASTING": "", "NORTHING": "", "LAT": "", "LON": ""}
        self.raw_record_count = 0
        self.last_flush_time = time.monotonic()
        self.port_monitor = SerialPortMonitor()
        self.port_monitor.on_state_change(self._on_port_state_change)
        self.current_xy: Optional[tuple[float, float]] = None
        self.current_lat_lon: Optional[tuple[float, float]] = None
        self.live_track_points: list[tuple[float, float]] = []
        self.live_soundings: list[tuple[float, float, float]] = []
        self.edit_selection: Optional[tuple[Layer, Shape, int]] = None
        self.edit_marker: Optional[Any] = None
        self.view_history: list[tuple[float, float, float, float]] = []
        self.suppress_background_state_save = False
        self.view = PyQtGraphSurveyMap(self)
        self.layer_list = QListWidget()
        self.mode_label = QLabel("Pan")
        self.coord_label = QLabel("E: --  N: --")
        self.scale_label = QLabel("Scale: --")
        self.project_label = QLabel()
        self.logging_badge = QLabel("LOGGING: OFF")
        self.event_log = QPlainTextEdit()
        self.main_toolbar: Optional[QToolBar] = None
        self.crs_box = QComboBox()
        self.crs_box.addItems(STARTUP_CRS_OPTIONS)
        self.crs_box.setEditable(True)
        self.startup_geodetic_settings = self.load_startup_geodetic_settings()
        saved_crs = str(self.startup_geodetic_settings.get("crs", ""))
        if saved_crs:
            index = self.crs_box.findText(saved_crs)
            if index >= 0:
                self.crs_box.setCurrentIndex(index)
        if self.startup_geodetic_settings.get("ktd_path"):
            self.ktd_file_path = str(self.startup_geodetic_settings.get("ktd_path", "")).strip()
        self.use_rtk_tide_method = bool(self.startup_geodetic_settings.get("use_rtk_tide", self.use_rtk_tide_method))
        self.transformer = None
        self.device_timer = QTimer(self)
        self.device_timer.timeout.connect(self.process_device_queue)
        self.device_timer.start(100)
        self.port_scan_timer = QTimer(self)
        self.port_scan_timer.timeout.connect(self._periodic_port_scan)
        self.port_scan_timer.start(2000)
        self.gnss_status_label = QLabel("GNSS: —")
        self.sbes_status_label = QLabel("SBES: —")
        self._build_ui()
        self.port_monitor.scan()  # initial scan
        self._update_connection_status_display()
        

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 6, 8, 6)
        root_layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("MAP window")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch(1)
        for status_label in (self.gnss_status_label, self.sbes_status_label):
            status_label.setStyleSheet("font-size:11px; padding:3px 10px; border-radius:3px; background:#f3f4f6; color:#6b7280;")
            status_label.setToolTip("Click Settings to configure serial ports")
            header.addWidget(status_label)
        self.logging_badge.setStyleSheet("font-weight:700; color:#991b1b; padding:4px 8px;")
        header.addWidget(self.logging_badge)
        root_layout.addLayout(header)

        survey_controls = QHBoxLayout()
        for label, slot in [
            ("Projects", self.show_projects_panel),
            ("Settings", self.show_settings_panel),
            ("THEME", self.show_theme_panel),
            ("SBES", self.show_sbes_panel),
            ("Multi-Beam Sys", self.show_mbes_panel),
            ("GEOCALC", self.show_geocalc_panel),
            ("KTD Editor", self.show_ktd_editor_panel),
            ("Start Logging", self.start_logging),
            ("Stop Log&Save", self.stop_logging_and_save),
            ("Data Display", self.show_data_display),
            ("I/O Monitor", self.show_io_monitor),
            ("SB EDIT", self.show_sb_edit_panel),
        ]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            survey_controls.addWidget(button)
        root_layout.addLayout(survey_controls)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.project_label.setWordWrap(True)
        left_layout.addWidget(self.project_label)
        left_layout.addWidget(QLabel("Map Layers"))
        left_layout.addWidget(self.layer_list)
        load_btn = QPushButton("Load DXF / DWG / KTD / XYZ")
        unload_btn = QPushButton("Unload Selected")
        fit_btn = QPushButton("Fit Extents")
        zoom_in_btn = QPushButton("Zoom +")
        zoom_out_btn = QPushButton("Zoom -")
        zoom_prev_btn = QPushButton("Zoom Previous")
        zoom_btn = QPushButton("Zoom Window")
        measure_btn = QPushButton("Measure")
        pan_btn = QPushButton("Pan")
        edit_dxf_btn = QPushButton("Edit DXF")
        edit_window_btn = QPushButton("DXF Edit Window")
        save_dxf_btn = QPushButton("Save Edited DXF")
        convert_btn = QPushButton("ODA DWG -> DXF")
        left_layout.addWidget(load_btn)
        left_layout.addWidget(convert_btn)
        left_layout.addWidget(zoom_in_btn)
        left_layout.addWidget(zoom_out_btn)
        left_layout.addWidget(zoom_prev_btn)
        left_layout.addWidget(zoom_btn)
        left_layout.addWidget(measure_btn)
        left_layout.addWidget(pan_btn)
        left_layout.addWidget(edit_dxf_btn)
        left_layout.addWidget(edit_window_btn)
        left_layout.addWidget(save_dxf_btn)
        left_layout.addWidget(fit_btn)
        left_layout.addWidget(unload_btn)
        left_layout.addWidget(QLabel("Coordinate mode"))
        left_layout.addWidget(self.crs_box)
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumHeight(170)
        self.event_log.setPlaceholderText("Survey events, logging messages, and hardware status will appear here.")
        left_layout.addWidget(QLabel("Survey Logger Events"))
        left_layout.addWidget(self.event_log)
        splitter.addWidget(left)
        splitter.addWidget(self.view)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)
        self.status = QStatusBar()
        self.status.addWidget(self.mode_label)
        self.status.addPermanentWidget(self.coord_label)
        self.status.addPermanentWidget(self.scale_label)
        self.setStatusBar(self.status)
        toolbar = QToolBar("Map")
        self.main_toolbar = toolbar
        self.addToolBar(toolbar)
        for label, slot in [
            ("Load", self.load_files),
            ("ODA Convert", self.convert_dwg_files),
            ("Pan", lambda: self.set_mode("Pan")),
            ("Zoom +", lambda: self.zoom_view(1.35)),
            ("Zoom -", lambda: self.zoom_view(1 / 1.35)),
            ("Zoom Previous", self.zoom_previous),
            ("Zoom Window", lambda: self.set_mode("Zoom Window")),
            ("Measure", lambda: self.set_mode("Measure")),
            ("DXF Edit Window", self.show_dxf_edit_window),
            ("Fit", self.fit_extents),
            ("W", lambda: self.pan_view(-0.20, 0.0)),
            ("E", lambda: self.pan_view(0.20, 0.0)),
            ("N", lambda: self.pan_view(0.0, 0.20)),
            ("S", lambda: self.pan_view(0.0, -0.20)),
            ("Clear", self.clear_layers),
            ("All Tools", self.show_all_tools_panel),
        ]:
            action = QAction(label, self)
            action.triggered.connect(slot)
            toolbar.addAction(action)
        load_btn.clicked.connect(self.load_files)
        convert_btn.clicked.connect(self.convert_dwg_files)
        unload_btn.clicked.connect(self.unload_selected)
        fit_btn.clicked.connect(self.fit_extents)
        zoom_in_btn.clicked.connect(lambda: self.zoom_view(1.35))
        zoom_out_btn.clicked.connect(lambda: self.zoom_view(1 / 1.35))
        zoom_prev_btn.clicked.connect(self.zoom_previous)
        zoom_btn.clicked.connect(lambda: self.set_mode("Zoom Window"))
        measure_btn.clicked.connect(lambda: self.set_mode("Measure"))
        pan_btn.clicked.connect(lambda: self.set_mode("Pan"))
        edit_dxf_btn.clicked.connect(lambda: self.set_mode("Edit DXF"))
        edit_window_btn.clicked.connect(self.show_dxf_edit_window)
        save_dxf_btn.clicked.connect(self.save_edited_linework)
        self.layer_list.itemChanged.connect(self.layer_visibility_changed)
        self.project_folder.mkdir(parents=True, exist_ok=True)
        self.build_menus()
        self.refresh_project_label()

    def log_event(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.event_log.appendPlainText(f"[{timestamp}] {message}")
        self.status.showMessage(message, 5000)

    def build_menus(self) -> None:
        menubar = self.menuBar()
        menubar.clear()

        def add_menu(title: str, entries: list[tuple[str, object] | None]) -> None:
            menu = menubar.addMenu(title)
            for entry in entries:
                if entry is None:
                    menu.addSeparator()
                    continue
                label, slot = entry
                action = QAction(label, self)
                action.triggered.connect(slot)  # type: ignore[arg-type]
                menu.addAction(action)

        add_menu(
            "Projects",
            [
                ("Projects", self.show_projects_panel),
                ("Settings", self.show_settings_panel),
                ("Theme", self.show_theme_panel),
                ("Classic Full 0701 Tools", self.launch_classic_0701),
                None,
                ("Exit", self.close),
            ],
        )
        add_menu(
            "Survey",
            [
                ("SBES", self.show_sbes_panel),
                ("Multi-Beam Sys", self.show_mbes_panel),
                ("Start Logging", self.start_logging),
                ("Stop Log&Save", self.stop_logging_and_save),
                ("Data Display", self.show_data_display),
                ("I/O Monitor", self.show_io_monitor),
                ("Echogram", self.show_echogram_panel),
            ],
        )
        add_menu(
            "Lines",
            [
                ("Line Editor", self.show_line_editor_panel),
                ("Border Editor", self.show_border_panel),
                ("Clip All by Border", self.show_clip_by_border_panel),
                ("Follow Vessel", self.follow_vessel),
                ("Unfollow Vessel", self.unfollow_vessel),
                ("Zoom Window", lambda: self.set_mode("Zoom Window")),
                ("Fit Extents", self.fit_extents),
            ],
        )
        add_menu(
            "Background",
            [
                ("Load DXF/DWG/KTD/XYZ", self.load_files),
                ("Background Upload", self.show_background_panel),
                ("ODA DWG -> DXF", self.convert_dwg_files),
                ("ENC / S-57", self.show_enc_panel),
                ("KMZ / KML", self.show_kmz_panel),
                ("Unload Selected", self.unload_selected),
                ("Unload All Backgrounds", self.clear_layers),
            ],
        )
        add_menu(
            "Tools",
            [
                ("GEOCALC", self.show_geocalc_panel),
                ("KTD Editor", self.show_ktd_editor_panel),
                ("SB EDIT", self.show_sb_edit_panel),
                ("SS510 Control Panel", self.show_ss510_panel),
                ("Toolbar Settings", self.show_toolbar_panel),
                ("Classic Full 0701 Tools", self.launch_classic_0701),
                ("Theme", self.show_theme_panel),
                ("Help", self.show_help_panel),
                ("Disclaimer & Terms of Use", self.show_disclaimer_panel),
                ("About", self.show_about_panel),
            ],
        )

    def show_feature_panel(self, title: str, tabs: list[tuple[str, str]]) -> None:
        win = QMainWindow(self)
        win.setWindowTitle(title)
        tab_widget = QTabWidget()
        for tab_title, text in tabs:
            editor = QPlainTextEdit()
            editor.setReadOnly(True)
            editor.setPlainText(text)
            tab_widget.addTab(editor, tab_title)
        win.setCentralWidget(tab_widget)
        win.resize(900, 600)
        win.show()

    def refresh_project_label(self) -> None:
        self.project_label.setText(f"Project: {self.project_folder}")

    def active_ktd_reference_path(self) -> Path:
        return self.project_folder / "KTD" / "active_rtk_ktd_path.txt"

    def startup_geodetic_settings_path(self) -> Path:
        return self.project_folder / "Settings" / "startup_geodetic.json"

    def load_startup_geodetic_settings(self) -> dict[str, object]:
        path = self.startup_geodetic_settings_path()
        defaults: dict[str, object] = {
            "crs": "EPSG:32642 UTM 42N",
            "source_datum": "WGS84 / GNSS",
            "target_datum": "WGS84 / UTM Zone 42N",
            "projection": "UTM Zone 42N",
            "vertical_reference": "RTK tide using active KTD",
            "ktd_path": self.ktd_file_path,
            "use_rtk_tide": self.use_rtk_tide_method,
        }
        if not path.exists():
            return defaults
        try:
            saved = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(saved, dict):
                defaults.update(saved)
        except Exception:
            pass
        return defaults

    def save_startup_geodetic_settings(self, settings: dict[str, object]) -> None:
        path = self.startup_geodetic_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(settings)
        payload["saved"] = datetime.now().isoformat(timespec="seconds")
        payload["project"] = str(self.project_folder)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def apply_startup_geodetic_settings(self, settings: dict[str, object], save: bool) -> None:
        crs = str(settings.get("crs", self.crs_box.currentText()))
        index = self.crs_box.findText(crs)
        if index >= 0:
            self.crs_box.setCurrentIndex(index)
        self.ktd_file_path = str(settings.get("ktd_path", "")).strip()
        self.use_rtk_tide_method = bool(settings.get("use_rtk_tide", True))
        self.startup_geodetic_settings = dict(settings)
        if save:
            self.save_active_ktd_reference()
            self.save_startup_geodetic_settings(settings)
        self.refresh_project_label()
        self.log_event(
            "Startup geodetic setup confirmed: "
            f"{crs}; KTD={Path(self.ktd_file_path).name if self.ktd_file_path else 'not selected'}; "
            f"RTK tide={'on' if self.use_rtk_tide_method else 'off'}."
        )

    def confirm_startup_geodetic_setup(self) -> None:
        settings = self.load_startup_geodetic_settings()
        dialog = QDialog(self)
        dialog.setWindowTitle("Startup Geodetic / KTD Confirmation")
        dialog.resize(760, 430)
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Confirm the project geodetic parameters and default KTD file before survey startup. "
            "These values are saved for future use, but this confirmation will appear every time the application starts."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        crs_box = QComboBox()
        crs_box.addItems(STARTUP_CRS_OPTIONS)
        crs_box.setEditable(True)
        crs_index = crs_box.findText(str(settings.get("crs", self.crs_box.currentText())))
        if crs_index >= 0:
            crs_box.setCurrentIndex(crs_index)
        else:
            crs_box.setCurrentText(str(settings.get("crs", self.crs_box.currentText())))
        source_box = QComboBox()
        source_box.addItems(STARTUP_SOURCE_DATUM_OPTIONS)
        source_box.setEditable(True)
        source_box.setCurrentText(str(settings.get("source_datum", "WGS84 / GNSS")))
        target_box = QComboBox()
        target_box.addItems(STARTUP_TARGET_DATUM_OPTIONS)
        target_box.setEditable(True)
        target_box.setCurrentText(str(settings.get("target_datum", "WGS84 / UTM Zone 42N")))
        projection_box = QComboBox()
        projection_box.addItems(STARTUP_PROJECTION_OPTIONS)
        projection_box.setEditable(True)
        projection_box.setCurrentText(str(settings.get("projection", "UTM Zone 42N")))
        vertical_box = QComboBox()
        vertical_box.addItems(STARTUP_VERTICAL_REFERENCE_OPTIONS)
        vertical_box.setEditable(True)
        vertical_box.setCurrentText(str(settings.get("vertical_reference", "RTK tide using active KTD")))
        use_rtk_check = QCheckBox("Use selected KTD for realtime and post-processing RTK tide")
        use_rtk_check.setChecked(bool(settings.get("use_rtk_tide", False)))
        # Restore the user's project default; Browse can select any supported KTD.
        ktd_edit = QLineEdit(str(settings.get("ktd_path", "")))
        browse_btn = QPushButton("Browse KTD")
        clear_btn = QPushButton("Clear")
        ktd_row = QHBoxLayout()
        ktd_row.addWidget(ktd_edit, 1)
        ktd_row.addWidget(browse_btn)
        ktd_row.addWidget(clear_btn)
        form.addRow("Project CRS", crs_box)
        form.addRow("Source datum", source_box)
        form.addRow("Target datum", target_box)
        form.addRow("Projection", projection_box)
        form.addRow("Vertical reference", vertical_box)
        form.addRow("Default KTD file", ktd_row)
        form.addRow("", use_rtk_check)
        layout.addLayout(form)

        status = QLabel()
        status.setWordWrap(True)
        layout.addWidget(status)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Save and Continue")
        continue_btn = QPushButton("Continue This Time")
        cancel_btn = QPushButton("Cancel Startup")
        buttons.addStretch(1)
        buttons.addWidget(save_btn)
        buttons.addWidget(continue_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def update_status() -> None:
            path_text = ktd_edit.text().strip()
            if use_rtk_check.isChecked() and not path_text:
                status.setText("Warning: RTK tide is enabled but no KTD file is selected. Browse for a KTD file or disable RTK tide to continue.")
            elif path_text and not Path(path_text).exists():
                status.setText("Warning: selected KTD path does not exist. Please browse again or clear it.")
            else:
                status.setText("Ready. Press Save and Continue to store these defaults, or Continue This Time to use them for this run only.")

        def collect_settings() -> dict[str, object]:
            return {
                "crs": crs_box.currentText(),
                "source_datum": source_box.currentText().strip(),
                "target_datum": target_box.currentText().strip(),
                "projection": projection_box.currentText().strip(),
                "vertical_reference": vertical_box.currentText().strip(),
                "ktd_path": ktd_edit.text().strip(),
                "use_rtk_tide": use_rtk_check.isChecked(),
            }

        def browse_ktd() -> None:
            name, _ = QFileDialog.getOpenFileName(dialog, "Select default startup KTD file", str(Path.home() / "Desktop"), "KTD files (*.ktd *.KTD *.hpktd *.ktdp);;All files (*.*)")
            if name:
                ktd_edit.setText(name)
                use_rtk_check.setChecked(True)
                update_status()

        def validate_selection() -> bool:
            path_text = ktd_edit.text().strip()
            if not path_text:
                if use_rtk_check.isChecked():
                    QMessageBox.warning(dialog, "Select KTD", "Select a KTD file or disable RTK tide.")
                    return False
                return True
            try:
                path = Path(path_text).expanduser().resolve()
                if not path.is_file():
                    raise ValueError("The selected file does not exist.")
                if path.suffix.lower() not in {".ktd", ".hpktd", ".ktdp"}:
                    raise ValueError("Select a .ktd, .hpktd, or .ktdp file.")
                if not load_ktd_shapes(path, include_nodata=False):
                    raise ValueError("The selected KTD contains no usable data.")
                ktd_edit.setText(str(path))
                return True
            except Exception as exc:
                QMessageBox.warning(dialog, "Invalid KTD", str(exc))
                return False

        def accept_saved() -> None:
            if not validate_selection():
                return
            self.apply_startup_geodetic_settings(collect_settings(), save=True)
            dialog.accept()

        def accept_once() -> None:
            if not validate_selection():
                return
            self.apply_startup_geodetic_settings(collect_settings(), save=False)
            dialog.accept()

        browse_btn.clicked.connect(browse_ktd)
        clear_btn.clicked.connect(lambda: (ktd_edit.clear(), use_rtk_check.setChecked(False), update_status()))
        use_rtk_check.stateChanged.connect(update_status)
        ktd_edit.textChanged.connect(update_status)
        save_btn.clicked.connect(accept_saved)
        continue_btn.clicked.connect(accept_once)
        cancel_btn.clicked.connect(dialog.reject)
        update_status()

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.autoload_project_layers()
        else:
            self.log_event("Startup geodetic confirmation cancelled by user.")
            self.close()

    def load_active_ktd_reference(self) -> None:
        ref = self.active_ktd_reference_path()
        if not ref.exists():
            return
        try:
            lines = [line.strip() for line in ref.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
            if lines:
                self.ktd_file_path = lines[0]
            if len(lines) > 1:
                self.use_rtk_tide_method = lines[1].split("=", 1)[-1].strip().lower() not in {"0", "false", "no", "off"}
        except Exception:
            return

    def save_active_ktd_reference(self) -> None:
        ref = self.active_ktd_reference_path()
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(f"{self.ktd_file_path}\nuse_rtk_tide={int(self.use_rtk_tide_method)}\n", encoding="utf-8")

    def background_state_path(self) -> Path:
        return self.project_folder / "CAD" / "background_state.json"

    def default_dxf_source_names(self) -> set[str]:
        return {
            "river banks updated 19 Mar 2018.dxf",
            "Channel with chainage.dxf",
            "Complete c.l.RPDK to attock 19 Mar 2018.dxf",
            "river_banks_default.hpdxf",
            "channel_chainage_default.hpdxf",
            "centerline_default.hpdxf",
        }

    def embedded_default_dxf_sources(self) -> list[Path]:
        embedded_paths = [
            path
            for path in (ensure_embedded_protected_file(name) for name in EMBEDDED_PROTECTED_FILES)
            if path is not None and path.suffix.lower() == ".hpdxf"
        ]
        resource_paths = [
            app_resource_path("river_banks_default.hpdxf"),
            app_resource_path("channel_chainage_default.hpdxf"),
            app_resource_path("centerline_default.hpdxf"),
        ]
        return [path for path in embedded_paths + resource_paths if path.exists()]

    def default_background_sources(self) -> list[Path]:
        cad = Path.home() / "Desktop" / "CAD"
        names = [
            "river banks updated 19 Mar 2018.dxf",
            "Channel with chainage.dxf",
            "Complete c.l.RPDK to attock 19 Mar 2018.dxf",
        ]
        paths = self.embedded_default_dxf_sources()
        if not paths:
            paths.extend([cad / name for name in names])
        if self.ktd_file_path:
            paths.append(Path(self.ktd_file_path))
        return [path for path in paths if path.exists()]

    def project_copy_for_background(self, source: Path) -> Path:
        if source.suffix.lower() == ".hpdxf":
            return source
        target_folder = self.project_folder / ("KTD" if source.suffix.lower() in {".ktd", ".ktdp", ".hpktd"} else "CAD")
        target_folder.mkdir(parents=True, exist_ok=True)
        target = target_folder / source.name
        try:
            if source.resolve() == target.resolve():
                return target
        except Exception:
            pass
        try:
            if not target.exists() or source.stat().st_mtime > target.stat().st_mtime or source.stat().st_size != target.stat().st_size:
                shutil.copy2(source, target)
        except Exception:
            if source.exists() and not target.exists():
                shutil.copy2(source, target)
        return target

    def save_background_state(self) -> None:
        if self.suppress_background_state_save:
            return
        state_path = self.background_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved": datetime.now().isoformat(timespec="seconds"),
            "project": str(self.project_folder),
            "layers": [
                {"name": layer.name, "path": str(layer.path or ""), "visible": bool(layer.visible), "cad_order": getattr(layer, "cad_order", []), "cad_visibility": getattr(layer, "cad_visibility", {})}
                for layer in self.layers
                if layer.path is not None and not layer.hidden and not layer.protected
            ],
        }
        state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def autoload_project_layers(self) -> None:
        state_path = self.background_state_path()
        layer_specs: list[dict[str, object]] = []
        if state_path.exists():
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8", errors="replace"))
                layer_specs = [dict(item) for item in payload.get("layers", []) if isinstance(item, dict)]
            except Exception as exc:
                self.log_event(f"Could not read background state: {exc}")
        embedded_default_sources = self.embedded_default_dxf_sources()
        if embedded_default_sources:
            default_names = self.default_dxf_source_names()
            layer_specs = [
                spec
                for spec in layer_specs
                if Path(str(spec.get("path", ""))).name not in default_names
                and str(spec.get("name", "")) not in default_names
            ]
            existing = {Path(str(spec.get("path", ""))).name for spec in layer_specs}
            for source in embedded_default_sources:
                if source.name not in existing:
                    layer_specs.append({"name": source.name, "path": str(source), "visible": True, "hidden": True, "protected": True})
        if not layer_specs:
            for source in self.default_background_sources():
                project_path = self.project_copy_for_background(source)
                is_protected_dxf = project_path.suffix.lower() == ".hpdxf"
                layer_specs.append({"name": project_path.name, "path": str(project_path), "visible": True, "hidden": is_protected_dxf, "protected": is_protected_dxf})
            if layer_specs:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                visible_specs = [spec for spec in layer_specs if not bool(spec.get("hidden", False)) and not bool(spec.get("protected", False))]
                state_path.write_text(json.dumps({"saved": datetime.now().isoformat(timespec="seconds"), "project": str(self.project_folder), "layers": visible_specs}, indent=2), encoding="utf-8")

        self.suppress_background_state_save = True
        loaded = 0
        try:
            for spec in layer_specs:
                path = Path(str(spec.get("path", "")))
                if path.suffix.lower() in {".ktd", ".ktdp", ".hpktd"}:
                    # KTD startup loading follows only the user's selected default.
                    continue
                if not path.exists() or any(layer.path == path for layer in self.layers):
                    continue
                try:
                    layer = Layer(
                        str(spec.get("name") or path.name),
                        path,
                        load_shapes(path),
                        visible=bool(spec.get("visible", True)),
                        hidden=bool(spec.get("hidden", False)) or path.suffix.lower() == ".hpdxf",
                        protected=bool(spec.get("protected", False)) or path.suffix.lower() == ".hpdxf",
                    )
                    layer.cad_order = list(spec.get("cad_order", []))
                    layer.cad_visibility = dict(spec.get("cad_visibility", {}))
                    self.add_layer(layer)
                    if not layer.visible and self.layer_list.count() > 0:
                        self.layer_list.item(self.layer_list.count() - 1).setCheckState(Qt.CheckState.Unchecked)
                    loaded += 1
                except Exception as exc:
                    self.log_event(f"Could not autoload background {path.name}: {exc}")
        finally:
            self.suppress_background_state_save = False
        self.autoload_active_ktd_layer()
        if loaded:
            self.fit_extents()
            self.log_event(f"Autoloaded {loaded} project background layer(s).")

    def autoload_active_ktd_layer(self) -> None:
        if not self.ktd_file_path:
            return
        path = Path(self.ktd_file_path)
        if not path.exists():
            return
        if any(layer.path == path for layer in self.layers):
            return
        try:
            self.add_layer(Layer(f"ACTIVE RTK KTD {path.name}", path, load_shapes(path)))
            self.fit_extents()
            self.log_event(f"Autoloaded active RTK KTD: {path.name}")
        except Exception as exc:
            self.log_event(f"Active RTK KTD autoload failed: {exc}")

    def show_projects_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("Projects")
        root = QWidget()
        layout = QVBoxLayout(root)
        current = QLabel(f"Current project: {self.project_folder}")
        current.setWordWrap(True)
        layout.addWidget(current)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Folder", "Path", "Exists"])
        layout.addWidget(table)
        buttons = QHBoxLayout()
        use_btn = QPushButton("Use Existing Project")
        create_btn = QPushButton("Create New Project")
        copy_btn = QPushButton("Copy Project")
        open_btn = QPushButton("Open Current Folder")
        buttons.addWidget(use_btn)
        buttons.addWidget(create_btn)
        buttons.addWidget(copy_btn)
        buttons.addWidget(open_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setPlainText("Default project structure: RAW, EDIT, SORT, EXPORT, KTD, CAD, LOGS, BACKUP. Select an existing project or create/copy one before survey logging.")
        layout.addWidget(info)
        win.setCentralWidget(root)

        def ensure_project(folder: Path) -> None:
            for sub in ("RAW", "EDIT", "SORT", "EXPORT", "KTD", "CAD", "LOGS", "BACKUP"):
                (folder / sub).mkdir(parents=True, exist_ok=True)
            self.project_folder = folder
            self.ktd_file_path = ""
            self.load_active_ktd_reference()
            self.refresh_project_label()
            current.setText(f"Current project: {self.project_folder}")
            refresh()
            self.suppress_background_state_save = True
            try:
                self.clear_layers()
            finally:
                self.suppress_background_state_save = False
            self.autoload_project_layers()
            self.log_event(f"Project selected: {folder}")

        def refresh() -> None:
            folders = ["RAW", "EDIT", "SORT", "EXPORT", "KTD", "CAD", "LOGS", "BACKUP"]
            table.setRowCount(len(folders))
            for row, name in enumerate(folders):
                path = self.project_folder / name
                table.setItem(row, 0, QTableWidgetItem(name))
                table.setItem(row, 1, QTableWidgetItem(str(path)))
                table.setItem(row, 2, QTableWidgetItem("Yes" if path.exists() else "No"))

        def use_existing() -> None:
            folder = QFileDialog.getExistingDirectory(self, "Use existing project folder", str(self.project_folder))
            if folder:
                ensure_project(Path(folder))

        def create_new() -> None:
            folder = QFileDialog.getExistingDirectory(self, "Select or create new project folder", str(Path.home() / "Desktop"))
            if folder:
                ensure_project(Path(folder))

        def copy_project() -> None:
            source = QFileDialog.getExistingDirectory(self, "Select source project folder", str(self.project_folder))
            if not source:
                return
            dest = QFileDialog.getExistingDirectory(self, "Select destination folder for copied project", str(Path.home() / "Desktop"))
            if not dest:
                return
            target = Path(dest) / Path(source).name
            if target.exists():
                QMessageBox.warning(self, "Projects", f"Destination already exists:\n{target}")
                return
            shutil.copytree(source, target)
            ensure_project(target)

        def open_current() -> None:
            try:
                os.startfile(str(self.project_folder))  # type: ignore[attr-defined]
            except Exception as exc:
                QMessageBox.warning(self, "Projects", str(exc))

        use_btn.clicked.connect(use_existing)
        create_btn.clicked.connect(create_new)
        copy_btn.clicked.connect(copy_project)
        open_btn.clicked.connect(open_current)
        refresh()
        win.showMaximized()

    def select_project_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select HYDROGEOMATICS project folder", str(self.project_folder))
        if not folder:
            return
        self.project_folder = Path(folder)
        for sub in ("RAW", "EDIT", "SORT", "EXPORT", "KTD", "CAD"):
            (self.project_folder / sub).mkdir(parents=True, exist_ok=True)
        self.refresh_project_label()
        self.log_event(f"Project selected: {self.project_folder}")

    def start_logging(self) -> None:
        if self.logging_active:
            self.log_event("Logging is already active.")
            return
        raw_folder = self.project_folder / "RAW"
        raw_folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.log_file_path = raw_folder / f"HYDROGEOMATICS_{stamp}.RAW"
        self.log_file_handle = self.log_file_path.open("w", encoding="utf-8", newline="")
        self.log_file_handle.write("HYDROGEOMATICS PYQT6 RAW LOG\n")
        self.log_file_handle.write(f"START,{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.log_file_handle.write(f"PROJECT,{self.project_folder}\n")
        self.log_file_handle.flush()
        self.logging_active = True
        self.logging_badge.setText("LOGGING: ON")
        self.logging_badge.setStyleSheet("font-weight:700; color:#166534; padding:4px 8px;")
        self.log_event(f"Started RAW logging: {self.log_file_path}")
        self.start_device_readers()
        # Change 4: Automatically open Accuracy Monitor when logging starts
        QTimer.singleShot(200, self.show_accuracy_monitor)

    def stop_logging_and_save(self) -> None:
        if not self.logging_active:
            self.log_event("No active logging session to stop.")
            return
        self.stop_device_readers()
        # Drain all pending records before closing the recording.
        while not self.device_queue.empty():
            self.process_device_queue()
        if self.log_file_handle is not None:
            self.log_file_handle.write(f"STOP,{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            self.log_file_handle.close()
        saved = self.log_file_path
        self.log_file_handle = None
        self.log_file_path = None
        self.logging_active = False
        self.logging_badge.setText("LOGGING: OFF")
        self.logging_badge.setStyleSheet("font-weight:700; color:#991b1b; padding:4px 8px;")
        self.log_event(f"Stopped logging and saved: {saved}")

    def show_text_window(self, title: str, text: str) -> None:
        win = QMainWindow(self)
        win.setWindowTitle(title)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        win.setCentralWidget(editor)
        win.resize(760, 520)
        win.show()

    def _settings_profiles_dir(self) -> Path:
        return self.project_folder / "Settings" / "profiles"

    def _save_settings_profile(self, name: str, data: dict[str, object]) -> Path:
        folder = self._settings_profiles_dir()
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[^\w\-. ]', '_', name.strip()) or "default"
        path = folder / f"{safe}.json"
        payload = dict(data)
        payload["profile_name"] = name
        payload["saved"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def _list_settings_profiles(self) -> list[Path]:
        folder = self._settings_profiles_dir()
        if not folder.exists():
            return []
        return sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    def _load_settings_profile(self, path: Path) -> Optional[dict[str, object]]:
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None

    def show_settings_panel(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.resize(820, 620)
        outer_layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        outer_layout.addWidget(tabs)

        # ── Profile management bar ──
        profile_bar = QHBoxLayout()
        profile_combo = QComboBox()
        profile_combo.setMinimumWidth(220)
        load_profile_btn = QPushButton("Load Profile")
        save_profile_btn = QPushButton("Save as Profile")
        delete_profile_btn = QPushButton("Delete")
        profile_bar.addWidget(QLabel("Parameter profile:"))
        profile_bar.addWidget(profile_combo, 1)
        profile_bar.addWidget(load_profile_btn)
        profile_bar.addWidget(save_profile_btn)
        profile_bar.addWidget(delete_profile_btn)
        outer_layout.addLayout(profile_bar)

        def refresh_profiles() -> None:
            profile_combo.clear()
            profile_combo.addItem("(current unsaved)")
            for path in self._list_settings_profiles():
                data = self._load_settings_profile(path)
                label = str(data.get("profile_name", path.stem)) if data else path.stem
                profile_combo.addItem(label, str(path))

        refresh_profiles()

        # ════════════════════════════════════════════════
        #  TAB 1: Serial Hardware
        # ════════════════════════════════════════════════
        hw_tab = QWidget()
        hw_layout = QVBoxLayout(hw_tab)
        form = QFormLayout()
        baud_values = ["4800", "9600", "19200", "38400", "57600", "115200"]

        def populate_port_combo(combo: QComboBox, current_text: str) -> None:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("")
            detailed = list_serial_ports_detailed()
            for device, description in detailed:
                combo.addItem(description, device)
            if current_text:
                idx = combo.findData(current_text)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                else:
                    combo.setCurrentText(current_text)
            combo.blockSignals(False)

        def get_port_value(combo: QComboBox) -> str:
            data = combo.currentData()
            if data:
                return str(data)
            text = combo.currentText().strip()
            if " — " in text:
                return text.split(" — ")[0].strip()
            return text

        gnss_port_box = QComboBox()
        gnss_port_box.setEditable(True)
        populate_port_combo(gnss_port_box, self.gnss_port)
        gnss_baud_box = QComboBox()
        gnss_baud_box.addItems(baud_values)
        gnss_baud_box.setCurrentText(str(self.gnss_baud))
        sbes_port_box = QComboBox()
        sbes_port_box.setEditable(True)
        populate_port_combo(sbes_port_box, self.sbes_port)
        sbes_baud_box = QComboBox()
        sbes_baud_box.addItems(baud_values)
        sbes_baud_box.setCurrentText(str(self.sbes_baud))
        shared_box = QCheckBox("Use same serial input for GNSS and SBES when both sentences arrive on one port")
        shared_box.setChecked(self.shared_gnss_sbes_port)
        sv_slider = QSlider(Qt.Orientation.Horizontal)
        sv_slider.setRange(1350, 1650)
        sv_slider.setValue(int(self.sound_velocity))
        sv_label = QLabel(f"{self.sound_velocity:.0f} m/s")
        ox_edit = QLineEdit(f"{self.antenna_offset_x:.3f}")
        oy_edit = QLineEdit(f"{self.antenna_offset_y:.3f}")
        tz_edit = QLineEdit(f"{self.transducer_offset_z:.3f}")
        form.addRow("GNSS port", gnss_port_box)
        form.addRow("GNSS baud", gnss_baud_box)
        form.addRow("SBES port", sbes_port_box)
        form.addRow("SBES baud", sbes_baud_box)
        form.addRow("Shared input", shared_box)
        sv_row = QHBoxLayout()
        sv_row.addWidget(sv_slider)
        sv_row.addWidget(sv_label)
        form.addRow("Sound velocity", sv_row)
        form.addRow("Antenna offset X", ox_edit)
        form.addRow("Antenna offset Y", oy_edit)
        form.addRow("Transducer offset Z", tz_edit)

        # ── RTK Tide Method / Chart Datum / KTD (Change 1) ──
        rtk_method_box = QComboBox()
        rtk_method_box.addItems(["Not using RTK method", "KTD file"])
        rtk_method_box.setCurrentIndex(1 if self.use_rtk_tide_method else 0)
        chart_datum_edit = QLineEdit(f"{self.chart_datum_level:.2f}")
        chart_datum_edit.setToolTip("Chart Datum Level (m) — used when RTK tide method is disabled")
        ktd_edit = QLineEdit(self.ktd_file_path)
        browse_ktd_btn = QPushButton("Browse KTD")
        clear_ktd_btn = QPushButton("Clear")
        ktd_row = QHBoxLayout()
        ktd_row.addWidget(ktd_edit, 1)
        ktd_row.addWidget(browse_ktd_btn)
        ktd_row.addWidget(clear_ktd_btn)
        chart_datum_label = QLabel("Chart Datum Level (m)")
        ktd_file_label = QLabel("Realtime KTD file")
        form.addRow("RTK Tide Method", rtk_method_box)
        form.addRow(chart_datum_label, chart_datum_edit)
        form.addRow(ktd_file_label, ktd_row)

        def update_rtk_visibility() -> None:
            use_ktd = rtk_method_box.currentIndex() == 1
            chart_datum_edit.setVisible(not use_ktd)
            chart_datum_label.setVisible(not use_ktd)
            ktd_edit.setVisible(use_ktd)
            browse_ktd_btn.setVisible(use_ktd)
            clear_ktd_btn.setVisible(use_ktd)
            ktd_file_label.setVisible(use_ktd)

        rtk_method_box.currentIndexChanged.connect(lambda _idx: update_rtk_visibility())
        update_rtk_visibility()

        hw_layout.addLayout(form)
        scan_btn = QPushButton("Scan Ports")
        hw_scan_row = QHBoxLayout()
        hw_scan_row.addWidget(scan_btn)
        hw_scan_row.addStretch(1)
        hw_layout.addLayout(hw_scan_row)
        tabs.addTab(hw_tab, "Serial / Hardware")

        # ════════════════════════════════════════════════
        #  TAB 2: Advanced GNSS Settings (Change 3)
        # ════════════════════════════════════════════════
        gnss_tab = QWidget()
        gnss_layout = QVBoxLayout(gnss_tab)
        gnss_layout.addWidget(QLabel("Select sentences for live processing. ALL received sentences are always saved to RAW, including unchecked and unrecognized sentences."))
        sentence_checks: dict[str, QCheckBox] = {}
        sentence_descriptions = {
            "GGA": "GGA — Global Positioning System Fix Data (position, quality, altitude)",
            "RMC": "RMC — Recommended Minimum (position, velocity, time)",
            "GSA": "GSA — GNSS DOP and Active Satellites",
            "GSV": "GSV — GNSS Satellites in View",
            "GLL": "GLL — Geographic Position (Lat/Lon)",
            "VTG": "VTG — Course Over Ground and Ground Speed",
            "HDT": "HDT — Heading True (from dual-antenna)",
            "GST": "GST — GNSS Pseudorange Noise Statistics (accuracy)",
            "GNS": "GNS — Multi-constellation Fix Data",
            "GBS": "GBS — GNSS Satellite Fault Detection",
            "ZDA": "ZDA — UTC Time and Date",
            "DTM": "DTM — Datum Reference",
            "THS": "THS — True Heading and Status (IMU heading)",
            "ROT": "ROT — Rate of Turn",
            "PTNL,GGK": "PTNL,GGK — Trimble proprietary position fix",
            "PTNL,QA": "PTNL,QA — Trimble proprietary quality assurance",
            "BESTPOSA": "BESTPOSA — NovAtel Best Available Position (ASCII)",
            "BESTPOS": "BESTPOS — NovAtel Best Position (abbreviated)",
        }
        # layout in a grid: 3 columns
        grid = QGridLayout()
        for idx, (key, desc) in enumerate(sentence_descriptions.items()):
            cb = QCheckBox(desc)
            cb.setChecked(self.enabled_nmea_sentences.get(key, False))
            sentence_checks[key] = cb
            grid.addWidget(cb, idx // 3, idx % 3)
        gnss_layout.addLayout(grid)
        sel_all_btn = QPushButton("Select All")
        desel_all_btn = QPushButton("Deselect All")
        gnss_btn_row = QHBoxLayout()
        gnss_btn_row.addWidget(sel_all_btn)
        gnss_btn_row.addWidget(desel_all_btn)
        gnss_btn_row.addStretch(1)
        gnss_layout.addLayout(gnss_btn_row)
        gnss_layout.addStretch(1)
        sel_all_btn.clicked.connect(lambda: [cb.setChecked(True) for cb in sentence_checks.values()])
        desel_all_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in sentence_checks.values()])
        tabs.addTab(gnss_tab, "Advanced GNSS")

        # ── Bottom buttons ──
        bottom = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        close_btn = QPushButton("Close")
        bottom.addStretch(1)
        bottom.addWidget(apply_btn)
        bottom.addWidget(close_btn)
        outer_layout.addLayout(bottom)

        # ── Helpers ──
        def refresh_ports() -> None:
            self.port_monitor.scan()
            current_gnss = get_port_value(gnss_port_box)
            current_sbes = get_port_value(sbes_port_box)
            populate_port_combo(gnss_port_box, current_gnss)
            populate_port_combo(sbes_port_box, current_sbes)
            port_count = len(list_serial_ports())
            self.log_event(f"Serial scan found {port_count} COM port(s).")
            self._update_connection_status_display()

        def browse_ktd() -> None:
            name, _ = QFileDialog.getOpenFileName(dialog, "Select realtime KTD file", str(Path.home() / "Desktop"), "KTD files (*.ktd *.KTD *.hpktd *.ktdp);;All files (*.*)")
            if name:
                ktd_edit.setText(name)

        def sync_shared_port() -> None:
            if shared_box.isChecked():
                gnss_val = get_port_value(gnss_port_box)
                idx = sbes_port_box.findData(gnss_val)
                if idx >= 0:
                    sbes_port_box.setCurrentIndex(idx)
                else:
                    sbes_port_box.setCurrentText(gnss_port_box.currentText())
                sbes_baud_box.setCurrentText(gnss_baud_box.currentText())

        sv_slider.valueChanged.connect(lambda value: sv_label.setText(f"{value} m/s"))
        shared_box.toggled.connect(lambda _checked: sync_shared_port())
        gnss_port_box.currentTextChanged.connect(lambda _text: sync_shared_port())
        gnss_baud_box.currentTextChanged.connect(lambda _text: sync_shared_port())
        scan_btn.clicked.connect(refresh_ports)
        browse_ktd_btn.clicked.connect(browse_ktd)
        clear_ktd_btn.clicked.connect(lambda: ktd_edit.clear())

        def collect_all_settings() -> dict[str, object]:
            return {
                "gnss_port": get_port_value(gnss_port_box),
                "gnss_baud": gnss_baud_box.currentText(),
                "sbes_port": get_port_value(sbes_port_box),
                "sbes_baud": sbes_baud_box.currentText(),
                "shared": shared_box.isChecked(),
                "sound_velocity": sv_slider.value(),
                "antenna_offset_x": ox_edit.text(),
                "antenna_offset_y": oy_edit.text(),
                "transducer_offset_z": tz_edit.text(),
                "rtk_tide_method": rtk_method_box.currentIndex(),
                "chart_datum_level": chart_datum_edit.text(),
                "ktd_file_path": ktd_edit.text().strip(),
                "enabled_sentences": {k: cb.isChecked() for k, cb in sentence_checks.items()},
            }

        def populate_from_data(data: dict[str, object]) -> None:
            if "gnss_port" in data:
                populate_port_combo(gnss_port_box, str(data.get("gnss_port", "")))
            if "gnss_baud" in data:
                gnss_baud_box.setCurrentText(str(data.get("gnss_baud", "9600")))
            if "sbes_port" in data:
                populate_port_combo(sbes_port_box, str(data.get("sbes_port", "")))
            if "sbes_baud" in data:
                sbes_baud_box.setCurrentText(str(data.get("sbes_baud", "9600")))
            if "shared" in data:
                shared_box.setChecked(bool(data.get("shared")))
            if "sound_velocity" in data:
                sv_slider.setValue(int(float(data.get("sound_velocity", 1500))))
            if "antenna_offset_x" in data:
                ox_edit.setText(str(data.get("antenna_offset_x", "0.000")))
            if "antenna_offset_y" in data:
                oy_edit.setText(str(data.get("antenna_offset_y", "0.000")))
            if "transducer_offset_z" in data:
                tz_edit.setText(str(data.get("transducer_offset_z", "0.000")))
            if "rtk_tide_method" in data:
                rtk_method_box.setCurrentIndex(int(data.get("rtk_tide_method", 1)))
            if "chart_datum_level" in data:
                chart_datum_edit.setText(str(data.get("chart_datum_level", "0.00")))
            if "ktd_file_path" in data:
                ktd_edit.setText(str(data.get("ktd_file_path", "")))
            if "enabled_sentences" in data:
                saved_sentences = data.get("enabled_sentences")
                if isinstance(saved_sentences, dict):
                    for k, cb in sentence_checks.items():
                        if k in saved_sentences:
                            cb.setChecked(bool(saved_sentences[k]))
            update_rtk_visibility()

        def load_selected_profile() -> None:
            idx = profile_combo.currentIndex()
            if idx <= 0:
                return
            path_str = profile_combo.currentData()
            if not path_str:
                return
            data = self._load_settings_profile(Path(path_str))
            if data:
                populate_from_data(data)
                self.log_event(f"Loaded settings profile: {data.get('profile_name', Path(path_str).stem)}")

        def save_new_profile() -> None:
            name = profile_combo.currentText().strip()
            if not name or name == "(current unsaved)":
                name, ok = QFileDialog.getSaveFileName(dialog, "Save profile name", str(self._settings_profiles_dir() / "my_profile.json"), "JSON (*.json)")
                if not ok or not name:
                    return
                name = Path(name).stem
            path = self._save_settings_profile(name, collect_all_settings())
            self.log_event(f"Saved settings profile: {name} -> {path}")
            refresh_profiles()

        def delete_selected_profile() -> None:
            idx = profile_combo.currentIndex()
            if idx <= 0:
                return
            path_str = profile_combo.currentData()
            if path_str and Path(path_str).exists():
                Path(path_str).unlink(missing_ok=True)
                self.log_event(f"Deleted profile: {Path(path_str).stem}")
                refresh_profiles()

        load_profile_btn.clicked.connect(load_selected_profile)
        save_profile_btn.clicked.connect(save_new_profile)
        delete_profile_btn.clicked.connect(delete_selected_profile)

        def apply_settings() -> None:
            self.gnss_port = get_port_value(gnss_port_box)
            self.gnss_baud = int(parse_float(gnss_baud_box.currentText()) or 9600)
            self.sbes_port = get_port_value(sbes_port_box)
            self.sbes_baud = int(parse_float(sbes_baud_box.currentText()) or 9600)
            self.shared_gnss_sbes_port = shared_box.isChecked()
            self.sound_velocity = float(sv_slider.value())
            self.antenna_offset_x = float(parse_float(ox_edit.text()) or 0.0)
            self.antenna_offset_y = float(parse_float(oy_edit.text()) or 0.0)
            self.transducer_offset_z = float(parse_float(tz_edit.text()) or 0.0)
            self.use_rtk_tide_method = rtk_method_box.currentIndex() == 1
            self.chart_datum_level = float(parse_float(chart_datum_edit.text()) or 0.0)
            self.ktd_file_path = ktd_edit.text().strip()
            for k, cb in sentence_checks.items():
                self.enabled_nmea_sentences[k] = cb.isChecked()
            self.save_active_ktd_reference()
            if self.shared_gnss_sbes_port and self.gnss_port:
                self.sbes_port = self.gnss_port
                self.sbes_baud = self.gnss_baud
            rtk_status = "KTD" if self.use_rtk_tide_method else f"Chart Datum {self.chart_datum_level:.2f}m"
            self.log_event(
                f"Settings applied: GNSS {self.gnss_port or 'OFF'} @ {self.gnss_baud}, "
                f"SBES {self.sbes_port or 'OFF'} @ {self.sbes_baud}, SV {self.sound_velocity:.0f} m/s, RTK: {rtk_status}"
            )
            dialog.accept()

        apply_btn.clicked.connect(apply_settings)
        close_btn.clicked.connect(dialog.reject)
        dialog.exec()
        self._update_connection_status_display()

    def show_sbes_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("SBES")
        tabs = QTabWidget()

        startup = QWidget()
        startup_layout = QVBoxLayout(startup)
        startup_text = QPlainTextEdit()
        startup_text.setReadOnly(True)
        startup_text.setPlainText(
            f"Project folder:\n{self.project_folder}\n\n"
            "Prepared folders: RAW, EDIT, SORT, EXPORT, KTD, CAD.\n"
            "Use Projects to open/create/copy a survey project, then set geodetic and hardware parameters."
        )
        startup_buttons = QHBoxLayout()
        for label, slot in (
            ("Projects", self.show_projects_panel),
            ("GEOCALC", self.show_geocalc_panel),
            ("KTD Editor", self.show_ktd_editor_panel),
            ("Settings", self.show_settings_panel),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            startup_buttons.addWidget(btn)
        startup_buttons.addStretch(1)
        startup_layout.addLayout(startup_buttons)
        startup_layout.addWidget(startup_text)
        tabs.addTab(startup, "Project / Geodetic")

        hardware = QWidget()
        hardware_layout = QVBoxLayout(hardware)
        hardware_text = QPlainTextEdit()
        hardware_text.setReadOnly(True)
        hardware_text.setPlainText(
            f"GNSS: {self.gnss_port or 'OFF'} @ {self.gnss_baud}\n"
            f"SBES: {self.sbes_port or 'OFF'} @ {self.sbes_baud}\n"
            f"Shared GNSS/SBES input: {'YES' if self.shared_gnss_sbes_port else 'NO'}\n"
            f"Sound velocity: {self.sound_velocity:.0f} m/s\n"
            f"Offsets X/Y/Z: {self.antenna_offset_x:.3f}, {self.antenna_offset_y:.3f}, {self.transducer_offset_z:.3f}\n"
            f"KTD: {self.ktd_file_path or 'not selected'}\n\n"
            "GNSS parser accepts NMEA GGA/RMC/GLL/GNS and NovAtel BESTPOS/BESTPOSA. "
            "SBES parser accepts DPT/DBT and plain numeric depth. All raw strings are preserved."
        )
        hw_buttons = QHBoxLayout()
        settings_btn = QPushButton("Serial / Offsets Setup")
        monitor_btn = QPushButton("I/O Monitor")
        settings_btn.clicked.connect(self.show_settings_panel)
        monitor_btn.clicked.connect(self.show_io_monitor)
        hw_buttons.addWidget(settings_btn)
        hw_buttons.addWidget(monitor_btn)
        hw_buttons.addStretch(1)
        hardware_layout.addLayout(hw_buttons)
        hardware_layout.addWidget(hardware_text)
        tabs.addTab(hardware, "Hardware")

        survey = QWidget()
        survey_layout = QVBoxLayout(survey)
        survey_buttons = QHBoxLayout()
        for label, slot in (
            ("Start Logging", self.start_logging),
            ("Stop Log&Save", self.stop_logging_and_save),
            ("Data Display", self.show_data_display),
            ("Echogram", self.show_echogram_panel),
            ("SB EDIT", self.show_sb_edit_panel),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            survey_buttons.addWidget(btn)
        survey_buttons.addStretch(1)
        survey_text = QPlainTextEdit()
        survey_text.setReadOnly(True)
        survey_text.setPlainText(
            "Navigation map uses PyQtGraph for fast pan/zoom.\n"
            "Live position is drawn as a dotted track. Depths are displayed as cartographic sounding labels.\n"
            "Background layers can include DXF/DWG, KTD, XYZ/CSV, line, border, and sounding files."
        )
        survey_layout.addLayout(survey_buttons)
        survey_layout.addWidget(survey_text)
        tabs.addTab(survey, "Navigation / Survey")

        win.setCentralWidget(tabs)
        win.showMaximized()

    def show_mbes_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("Multi-Beam Sys")
        tabs = QTabWidget()

        setup_tab = QWidget()
        setup_layout = QVBoxLayout(setup_tab)
        form = QFormLayout()
        fields: dict[str, QLineEdit] = {}
        for key, label in (
            ("sonar_model", "MBES sonar"),
            ("gnss_model", "GNSS / positioning"),
            ("motion_model", "Motion / MRU"),
            ("heading_model", "Heading"),
            ("svp_model", "SVP / sound velocity"),
            ("tide_source", "Tide / water level"),
            ("time_sync", "Time sync"),
            ("sonar_udp", "Sonar UDP port"),
            ("gnss_udp", "GNSS UDP port"),
            ("motion_udp", "Motion UDP port"),
            ("heading_udp", "Heading UDP port"),
        ):
            edit = QLineEdit(str(self.mbes_setup.get(key, "")))
            fields[key] = edit
            form.addRow(label, edit)
        required_checks: dict[str, QCheckBox] = {}
        checks_row = QHBoxLayout()
        for key, label in (
            ("sonar_model", "Sonar"),
            ("gnss_model", "GNSS"),
            ("motion_model", "Motion"),
            ("heading_model", "Heading"),
            ("svp_model", "SVP"),
            ("tide_source", "Tide"),
            ("time_sync", "Time"),
        ):
            box = QCheckBox(label)
            box.setChecked(True)
            required_checks[key] = box
            checks_row.addWidget(box)
        setup_layout.addLayout(form)
        setup_layout.addWidget(QLabel("Required components"))
        setup_layout.addLayout(checks_row)
        result_log = QPlainTextEdit()
        result_log.setReadOnly(True)
        buttons = QHBoxLayout()
        save_btn = QPushButton("Save Setup")
        check_btn = QPushButton("Check Configuration")
        listen_btn = QPushButton("Listen for Live Data")
        seconds_spin = QSpinBox()
        seconds_spin.setRange(1, 60)
        seconds_spin.setValue(5)
        buttons.addWidget(save_btn)
        buttons.addWidget(check_btn)
        buttons.addWidget(QLabel("Seconds"))
        buttons.addWidget(seconds_spin)
        buttons.addWidget(listen_btn)
        buttons.addStretch(1)
        setup_layout.addLayout(buttons)
        setup_layout.addWidget(result_log)

        def collect_mbes_setup() -> dict[str, str]:
            for key, edit in fields.items():
                self.mbes_setup[key] = edit.text().strip()
            return {key: str(value) for key, value in self.mbes_setup.items()}

        def validate_mbes_setup() -> list[str]:
            setup = collect_mbes_setup()
            messages: list[str] = []
            for key, box in required_checks.items():
                if box.isChecked() and not setup.get(key, "").strip():
                    messages.append(f"FAIL: required {box.text()} field is empty.")
            ports: list[int] = []
            for key in ("sonar_udp", "gnss_udp", "motion_udp", "heading_udp"):
                text = setup.get(key, "").strip()
                value = int(parse_float(text) or 0)
                if value < 1 or value > 65535:
                    messages.append(f"FAIL: {key} must be a UDP port from 1 to 65535.")
                    continue
                ports.append(value)
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    sock.bind(("0.0.0.0", value))
                    messages.append(f"PASS: UDP {value} can be opened.")
                except OSError as exc:
                    messages.append(f"WARN: UDP {value} could not be opened now: {exc}")
                finally:
                    sock.close()
            if len(ports) != len(set(ports)):
                messages.append("FAIL: duplicate UDP ports are configured.")
            for folder in ("RAW", "EXPORT", "KTD", "CAD"):
                target = self.project_folder / folder
                try:
                    target.mkdir(parents=True, exist_ok=True)
                    test = target / ".HYDROGEOMATICS_write_test"
                    test.write_text("ok", encoding="utf-8")
                    test.unlink(missing_ok=True)
                    messages.append(f"PASS: {folder} folder writable.")
                except Exception as exc:
                    messages.append(f"FAIL: {folder} folder is not writable: {exc}")
            if not messages:
                messages.append("PASS: MBES setup check completed.")
            return messages

        def save_setup() -> None:
            setup = collect_mbes_setup()
            target = self.project_folder / "MBES_Setup.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(f"{key}={value}" for key, value in setup.items()), encoding="utf-8")
            result_log.setPlainText(f"Saved MBES setup:\n{target}")
            self.log_event(f"Saved MBES setup: {target}")

        def check_setup() -> None:
            result_log.setPlainText("\n".join(validate_mbes_setup()))

        def listen_udp() -> None:
            setup = collect_mbes_setup()
            seconds = float(seconds_spin.value())
            ports = [(name, int(parse_float(setup.get(name, "")) or 0)) for name in ("sonar_udp", "gnss_udp", "motion_udp", "heading_udp")]
            lines = [f"Listening {seconds:.0f} second(s) on enabled UDP ports..."]
            for name, port in ports:
                if port < 1 or port > 65535:
                    lines.append(f"{name}: skipped invalid port")
                    continue
                count = 0
                byte_count = 0
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(0.2)
                try:
                    sock.bind(("0.0.0.0", port))
                    end_time = time.time() + seconds
                    while time.time() < end_time:
                        try:
                            data, _addr = sock.recvfrom(65535)
                            count += 1
                            byte_count += len(data)
                        except socket.timeout:
                            QApplication.processEvents()
                    lines.append(f"{name}: {count} packet(s), {byte_count} byte(s) on UDP {port}")
                except OSError as exc:
                    lines.append(f"{name}: could not listen on UDP {port}: {exc}")
                finally:
                    sock.close()
            result_log.setPlainText("\n".join(lines))

        save_btn.clicked.connect(save_setup)
        check_btn.clicked.connect(check_setup)
        listen_btn.clicked.connect(listen_udp)
        tabs.addTab(setup_tab, "Hardware Setup")

        routing_tab = QWidget()
        routing_layout = QVBoxLayout(routing_tab)
        routing_text = QPlainTextEdit()
        routing_text.setPlainText(
            "Serial-to-network routing plan\n\n"
            f"GNSS serial: {self.gnss_port or 'OFF'} @ {self.gnss_baud} -> UDP {self.mbes_setup.get('gnss_udp')}\n"
            f"SBES serial: {self.sbes_port or 'OFF'} @ {self.sbes_baud} -> UDP {self.mbes_setup.get('sonar_udp')}\n\n"
            "When shared input is enabled in Settings, the same serial stream is parsed for both GNSS and echosounder sentences."
        )
        routing_layout.addWidget(routing_text)
        tabs.addTab(routing_tab, "Routing")

        processing_tab = QWidget()
        processing_layout = QVBoxLayout(processing_tab)
        processor_text = QPlainTextEdit()
        processor_text.setPlainText(
            "Internal processing target:\n"
            "- preserve vendor/raw UDP packets\n"
            "- decode supported ASCII/CSV beams\n"
            "- TPU and cleaning workflow\n"
            "- export XYZ/CSV/GeoJSON\n"
            "- prepare handoff folders for CARIS, Qimera, HYPACK/HYSWEEP, PDS, EIVA, MB-System, and vendor software\n\n"
            "Native proprietary binary beam decoding requires the vendor packet specification or SDK."
        )
        processing_layout.addWidget(processor_text)
        tabs.addTab(processing_tab, "Processing / Export")

        win.setCentralWidget(tabs)
        win.showMaximized()

    def show_geocalc_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("GEOCALC")
        tabs = QTabWidget()

        ll_tab = QWidget()
        ll_layout = QFormLayout(ll_tab)
        lat_edit = QLineEdit()
        lon_edit = QLineEdit()
        east_edit = QLineEdit()
        north_edit = QLineEdit()
        result = QPlainTextEdit()
        result.setReadOnly(True)
        ll_layout.addRow("Latitude", lat_edit)
        ll_layout.addRow("Longitude", lon_edit)
        ll_layout.addRow("Easting", east_edit)
        ll_layout.addRow("Northing", north_edit)
        buttons = QHBoxLayout()
        ll_to_utm_btn = QPushButton("LL -> UTM 42N")
        utm_to_ll_btn = QPushButton("UTM 42N -> LL")
        buttons.addWidget(ll_to_utm_btn)
        buttons.addWidget(utm_to_ll_btn)
        ll_layout.addRow(buttons)
        ll_layout.addRow("Result", result)

        def do_ll_to_utm() -> None:
            lat = parse_float(lat_edit.text())
            lon = parse_float(lon_edit.text())
            if lat is None or lon is None:
                result.setPlainText("Enter numeric latitude and longitude.")
                return
            xy = ll_to_utm42(float(lat), float(lon))
            if not xy:
                result.setPlainText("pyproj transformer is not available.")
                return
            east_edit.setText(f"{xy[0]:.3f}")
            north_edit.setText(f"{xy[1]:.3f}")
            result.setPlainText(f"UTM Zone 42N\nEasting: {xy[0]:.3f}\nNorthing: {xy[1]:.3f}")

        def do_utm_to_ll() -> None:
            east = parse_float(east_edit.text())
            north = parse_float(north_edit.text())
            transformer_class = get_transformer_class()
            if east is None or north is None:
                result.setPlainText("Enter numeric easting and northing.")
                return
            if transformer_class is None:
                result.setPlainText("pyproj transformer is not available.")
                return
            transformer = transformer_class.from_crs("EPSG:32642", "EPSG:4326", always_xy=True)
            lon, lat = transformer.transform(float(east), float(north))
            lat_edit.setText(f"{lat:.8f}")
            lon_edit.setText(f"{lon:.8f}")
            result.setPlainText(f"WGS84\nLatitude: {lat:.8f}\nLongitude: {lon:.8f}")

        ll_to_utm_btn.clicked.connect(do_ll_to_utm)
        utm_to_ll_btn.clicked.connect(do_utm_to_ll)
        tabs.addTab(ll_tab, "LL / UTM")

        datum_tab = QPlainTextEdit()
        datum_tab.setReadOnly(True)
        datum_tab.setPlainText(
            "Active PyQt conversion: WGS84 EPSG:4326 <-> UTM Zone 42N EPSG:32642.\n\n"
            "Lambert IA/IIA and local grid shift forms will be wired here as custom CRS definitions are finalized."
        )
        tabs.addTab(datum_tab, "Datum / CRS")
        win.setCentralWidget(tabs)
        win.resize(680, 470)
        win.show()

    def show_ktd_editor_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("KTD Editor")
        root = QWidget()
        layout = QVBoxLayout(root)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Easting", "Northing", "K"])
        status = QLabel("Load a KTD/XYZ file.")
        current_path: dict[str, Optional[Path]] = {"path": None}
        current_nodes: list[tuple[float, float, float]] = []
        default_cad = Path.home() / "Desktop" / "CAD"
        bank_path_edit = QLineEdit(str(default_cad / "river banks updated 19 Mar 2018.dxf"))
        channel_path_edit = QLineEdit(str(default_cad / "Channel with chainage.dxf"))
        border_path_edit = QLineEdit("")
        buffer_spin = QSpinBox()
        buffer_spin.setRange(0, 5000)
        buffer_spin.setSingleStep(50)
        buffer_spin.setValue(250)
        fill_gaps_box = QCheckBox("Interpolate missing K cells inside river water area")
        fill_gaps_box.setChecked(True)
        buttons = QHBoxLayout()
        load_btn = QPushButton("Load KTD/XYZ")
        load_active_btn = QPushButton("Load Active KTD")
        save_btn = QPushButton("Save KTD Nodes")
        map_btn = QPushButton("Display on Map")
        apply_active_btn = QPushButton("Apply as RTK Tide KTD")
        clip_btn = QPushButton("Clip KTD by River Banks")
        protect_btn = QPushButton("Create Protected KTD")
        load_channel_btn = QPushButton("Load Channel DXF")
        load_border_btn = QPushButton("Load Border on Map")
        buttons.addWidget(load_btn)
        buttons.addWidget(load_active_btn)
        buttons.addWidget(save_btn)
        buttons.addWidget(map_btn)
        buttons.addWidget(apply_active_btn)
        buttons.addWidget(clip_btn)
        buttons.addWidget(protect_btn)
        buttons.addWidget(load_channel_btn)
        buttons.addWidget(load_border_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        creation_row = QHBoxLayout()
        matrix_btn = QPushButton("New / Edit Matrix KTD — Four Corners")
        border_create_btn = QPushButton("Create Border — Four Corners")
        def open_matrix_editor():
            self.matrix_ktd_editor = MatrixKtdEditor(self)
            self.matrix_ktd_editor.show()
        matrix_btn.clicked.connect(open_matrix_editor)
        border_create_btn.clicked.connect(lambda: show_four_corner_border(self))
        creation_row.addWidget(matrix_btn)
        creation_row.addWidget(border_create_btn)
        layout.addLayout(creation_row)
        bank_row = QHBoxLayout()
        bank_browse_btn = QPushButton("Browse Banks")
        bank_row.addWidget(QLabel("River bank DXF"))
        bank_row.addWidget(bank_path_edit)
        bank_row.addWidget(bank_browse_btn)
        layout.addLayout(bank_row)
        channel_row = QHBoxLayout()
        channel_browse_btn = QPushButton("Browse Channel")
        channel_row.addWidget(QLabel("Channel DXF"))
        channel_row.addWidget(channel_path_edit)
        channel_row.addWidget(channel_browse_btn)
        layout.addLayout(channel_row)
        border_row = QHBoxLayout()
        border_browse_btn = QPushButton("Browse Border")
        border_row.addWidget(QLabel("Border file"))
        border_row.addWidget(border_path_edit)
        border_row.addWidget(border_browse_btn)
        layout.addLayout(border_row)
        clip_options = QHBoxLayout()
        clip_options.addWidget(QLabel("Outside bank margin"))
        clip_options.addWidget(buffer_spin)
        clip_options.addWidget(QLabel("m"))
        clip_options.addWidget(fill_gaps_box)
        clip_options.addStretch(1)
        layout.addLayout(clip_options)
        layout.addWidget(table)
        layout.addWidget(status)
        win.setCentralWidget(root)

        def fill_table(nodes: list[tuple[float, float, float]]) -> None:
            display_nodes = nodes[:50000]
            table.setRowCount(len(display_nodes))
            for row, (x, y, value) in enumerate(display_nodes):
                table.setItem(row, 0, QTableWidgetItem(f"{x:.3f}"))
                table.setItem(row, 1, QTableWidgetItem(f"{y:.3f}"))
                table.setItem(row, 2, QTableWidgetItem(f"{value:.4f}"))
            suffix = "" if len(nodes) <= len(display_nodes) else f" | showing first {len(display_nodes):,} editable rows"
            status.setText(f"{len(nodes):,} valid/non-nodata node(s) loaded{suffix}.")

        def table_nodes() -> list[tuple[float, float, float]]:
            nodes: list[tuple[float, float, float]] = []
            for row in range(table.rowCount()):
                x = parse_float(table.item(row, 0).text() if table.item(row, 0) else "")
                y = parse_float(table.item(row, 1).text() if table.item(row, 1) else "")
                value = parse_float(table.item(row, 2).text() if table.item(row, 2) else "")
                if x is not None and y is not None and value is not None:
                    nodes.append((float(x), float(y), float(value)))
            return nodes

        def load_file() -> None:
            name, _ = QFileDialog.getOpenFileName(self, "Load KTD / XYZ", str(Path.home() / "Desktop"), "KTD / XYZ (*.ktd *.KTD *.hpktd *.ktdp *.xyz *.csv *.txt);;All files (*.*)")
            if not name:
                return
            path = Path(name)
            shapes = load_ktd_shapes(path, include_nodata=False) if path.suffix.lower() in {".ktd", ".hpktd", ".ktdp"} else load_xyz_csv_shapes(path)
            current_nodes.clear()
            current_nodes.extend([(s.points[0][0], s.points[0][1], float(s.value if s.value is not None else parse_float(s.label) or 0.0)) for s in shapes if s.points])
            current_path["path"] = path
            fill_table(current_nodes)
            self.add_layer(Layer(path.name, path, load_shapes(path)))
            self.fit_extents()

        def load_active_ktd() -> None:
            if not self.ktd_file_path or not Path(self.ktd_file_path).exists():
                QMessageBox.warning(self, "KTD Editor", "No active KTD is available.")
                return
            path = Path(self.ktd_file_path)
            shapes = load_ktd_shapes(path, include_nodata=False)
            current_nodes.clear()
            current_nodes.extend([(s.points[0][0], s.points[0][1], float(s.value if s.value is not None else parse_float(s.label) or 0.0)) for s in shapes if s.points])
            current_path["path"] = path
            fill_table(current_nodes)
            status.setText(f"Loaded active KTD for editing: {path.name}. Save writes a new user KTD file.")

        def save_file() -> None:
            nodes = table_nodes()
            if not nodes:
                QMessageBox.warning(self, "KTD Editor", "No table nodes to save.")
                return
            name, _ = QFileDialog.getSaveFileName(self, "Save KTD node file", str((current_path["path"] or Path.home() / "Desktop" / "edited.ktd").with_suffix(".ktd")), "KTD files (*.ktd);;All files (*.*)")
            if not name:
                return
            target = Path(name)
            with target.open("w", encoding="utf-8", newline="") as handle:
                handle.write("# HYDROGEOMATICS PyQt6 KTD node file\n")
                for x, y, value in nodes:
                    handle.write(f"KTD {x:.3f} {y:.3f} {value:.4f}\n")
            status.setText(f"Saved {len(nodes):,} node(s) to {target.name}.")

        def display_map() -> None:
            path = current_path.get("path")
            if not path:
                QMessageBox.warning(self, "KTD Editor", "Load a KTD file first.")
                return
            self.add_layer(Layer(path.name, path, load_shapes(path)))
            self.fit_extents()

        def browse_banks() -> None:
            name, _ = QFileDialog.getOpenFileName(self, "Select river bank DXF", str(default_cad), "DXF files (*.dxf *.DXF);;All files (*.*)")
            if name:
                bank_path_edit.setText(name)

        def browse_channel() -> None:
            name, _ = QFileDialog.getOpenFileName(self, "Select channel DXF", str(default_cad), "DXF files (*.dxf *.DXF);;All files (*.*)")
            if name:
                channel_path_edit.setText(name)

        def browse_border() -> None:
            start_dir = str(self.project_folder) if self.project_folder and self.project_folder.exists() else str(Path.home() / "Desktop")
            name, _ = QFileDialog.getOpenFileName(self, "Select HYPACK border file", start_dir, "Border files (*.txt *.brd *.csv *.xyz);;All files (*.*)")
            if name:
                border_path_edit.setText(name)

        def load_border() -> None:
            border_text = border_path_edit.text().strip()
            if not border_text:
                QMessageBox.warning(self, "KTD Editor", "No border file selected. Use 'Browse Border' to pick a HYPACK border file.")
                return
            path = Path(border_text)
            if not path.exists():
                QMessageBox.warning(self, "KTD Editor", f"Border file does not exist:\n{path}")
                return
            shapes = load_xy_line_shapes(path, "#dc2626", path.stem)
            if not shapes:
                QMessageBox.warning(self, "KTD Editor", f"No valid X Y coordinates found in:\n{path.name}")
                return
            # Close the polygon if not already closed
            if shapes[0].points and shapes[0].points[0] != shapes[0].points[-1]:
                shapes[0].points.append(shapes[0].points[0])
            self.add_layer(Layer(f"BORDER {path.name}", path, shapes))
            self.fit_extents()
            status.setText(f"Loaded border: {path.name} ({len(shapes[0].points)} vertices)")

        def load_channel() -> None:
            path = Path(channel_path_edit.text().strip())
            if not path.exists():
                QMessageBox.warning(self, "KTD Editor", f"Channel DXF does not exist:\n{path}")
                return
            self.add_layer(Layer(f"CHANNEL {path.name}", path, load_shapes(path)))
            self.fit_extents()
            status.setText(f"Loaded channel DXF: {path.name}")

        def apply_active() -> None:
            path = current_path.get("path")
            if not path:
                QMessageBox.warning(self, "KTD Editor", "Load or create a KTD file first.")
                return
            self.ktd_file_path = str(path)
            self.use_rtk_tide_method = True
            self.save_active_ktd_reference()
            self.autoload_active_ktd_layer()
            status.setText(f"Active RTK tide KTD saved for this project: {path.name}")

        def clip_by_banks() -> None:
            source = current_path.get("path")
            if not source or source.suffix.lower() != ".ktd":
                QMessageBox.warning(self, "KTD Editor", "Load the original matrix KTD file before clipping.")
                return
            bank_path = Path(bank_path_edit.text().strip())
            if not bank_path.exists():
                QMessageBox.warning(self, "KTD Editor", f"River bank DXF does not exist:\n{bank_path}")
                return
            target_default = self.project_folder / "KTD" / f"{source.stem}_riverbank_clipped.ktd"
            name, _ = QFileDialog.getSaveFileName(self, "Save river-bank clipped KTD", str(target_default), "KTD files (*.ktd);;All files (*.*)")
            if not name:
                return
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                matrix = read_ktd_matrix(source)
                polygon = build_river_bank_polygon(bank_path)
                clipped, kept, real_total, filled, land_nodata = clip_ktd_matrix_to_polygon(
                    matrix,
                    polygon,
                    buffer_m=float(buffer_spin.value()),
                    interpolate_gaps=fill_gaps_box.isChecked(),
                )
                target = Path(name)
                write_ktd_matrix(target, matrix, clipped)
                current_path["path"] = target
                shapes = load_ktd_shapes(target, include_nodata=False)
                current_nodes.clear()
                current_nodes.extend([(s.points[0][0], s.points[0][1], float(s.value if s.value is not None else parse_float(s.label) or 0.0)) for s in shapes if s.points])
                fill_table(current_nodes)
                self.ktd_file_path = str(target)
                self.use_rtk_tide_method = True
                self.save_active_ktd_reference()
                self.add_layer(Layer(f"RTK KTD {target.name}", target, shapes))
                self.add_layer(Layer(f"BANKS {bank_path.name}", bank_path, load_shapes(bank_path)))
                channel_path = Path(channel_path_edit.text().strip())
                if channel_path.exists():
                    self.add_layer(Layer(f"CHANNEL {channel_path.name}", channel_path, load_shapes(channel_path)))
                self.fit_extents()
                values = [value for value in clipped if not is_ktd_nodata(value)]
                value_range = f"{min(values):.2f} to {max(values):.2f} m" if values else "no real values"
                status.setText(
                    f"Saved clipped KTD: {target.name} | kept {kept:,} of {real_total:,} real cells | "
                    f"interpolated {filled:,} gap cell(s) | land/nodata cells inside banks {land_nodata:,} | K range {value_range}. "
                    "This KTD is now active for RTK tide and will autoload with the project."
                )
            except Exception as exc:
                QMessageBox.warning(self, "KTD Editor", f"Could not clip KTD by river banks:\n{exc}")
            finally:
                QApplication.restoreOverrideCursor()

        def create_protected_ktd() -> None:
            source = current_path.get("path")
            if not source or not source.exists() or source.suffix.lower() not in {".ktd", ".ktdp", ".hpktd"}:
                QMessageBox.warning(self, "KTD Editor", "Load or create a KTD file first.")
                return
            target_default = self.project_folder / "KTD" / f"{source.stem}_protected.hpktd"
            name, _ = QFileDialog.getSaveFileName(self, "Create protected KTD for EXE build", str(target_default), "Protected KTD (*.hpktd *.ktdp);;All files (*.*)")
            if not name:
                return
            try:
                matrix = read_ktd_matrix(source)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", suffix=".ktd", delete=False) as temp_handle:
                    temp_path = Path(temp_handle.name)
                try:
                    write_ktd_matrix(temp_path, matrix, matrix.values)
                    protect_ktd_file(temp_path, Path(name))
                finally:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                status.setText(f"Created protected KTD: {Path(name).name}. Select it with Browse KTD at startup and Save and Continue to make it the default.")
            except Exception as exc:
                QMessageBox.warning(self, "KTD Editor", f"Could not create protected KTD:\n{exc}")

        load_btn.clicked.connect(load_file)
        load_active_btn.clicked.connect(load_active_ktd)
        save_btn.clicked.connect(save_file)
        map_btn.clicked.connect(display_map)
        apply_active_btn.clicked.connect(apply_active)
        clip_btn.clicked.connect(clip_by_banks)
        protect_btn.clicked.connect(create_protected_ktd)
        bank_browse_btn.clicked.connect(browse_banks)
        channel_browse_btn.clicked.connect(browse_channel)
        border_browse_btn.clicked.connect(browse_border)
        load_channel_btn.clicked.connect(load_channel)
        load_border_btn.clicked.connect(load_border)
        win.resize(840, 620)
        win.show()

    def show_line_editor_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("Line Editor")
        root = QWidget()
        layout = QVBoxLayout(root)
        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setPlainText("Open line files containing X Y coordinate rows. Loaded lines are displayed on the map in blue.")
        buttons = QHBoxLayout()
        open_btn = QPushButton("Open Lines")
        save_btn = QPushButton("Save Visible Track as Line")
        next_btn = QPushButton("NEXT LINE")
        buttons.addWidget(open_btn)
        buttons.addWidget(save_btn)
        buttons.addWidget(next_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(info)
        win.setCentralWidget(root)

        def open_lines() -> None:
            names, _ = QFileDialog.getOpenFileNames(self, "Open line file(s)", str(Path.home() / "Desktop"), "Line files (*.lnw *.brd *.txt *.csv *.xyz);;All files (*.*)")
            for name in names:
                path = Path(name)
                shapes = load_xy_line_shapes(path, "#2563eb", path.stem)
                if shapes:
                    self.add_layer(Layer(f"LINE {path.name}", path, shapes))
                    info.appendPlainText(f"Loaded line: {path.name}")
            if names:
                self.fit_extents()

        def save_track_line() -> None:
            if not self.live_track_points:
                QMessageBox.warning(self, "Line Editor", "No live GNSS track is available to save as a line.")
                return
            name, _ = QFileDialog.getSaveFileName(self, "Save track as line", str(self.project_folder / "EXPORT" / "track_line.txt"), "Text files (*.txt);;All files (*.*)")
            if not name:
                return
            target = Path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as handle:
                for x, y in self.live_track_points:
                    handle.write(f"{x:.3f} {y:.3f}\n")
            info.appendPlainText(f"Saved track line: {target}")

        open_btn.clicked.connect(open_lines)
        save_btn.clicked.connect(save_track_line)
        next_btn.clicked.connect(lambda: self.log_event("NEXT LINE selected. Full runline selection logic is being migrated."))
        win.resize(760, 420)
        win.show()

    def show_border_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("Border Editor")
        root = QWidget()
        layout = QVBoxLayout(root)
        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setPlainText("Open border files containing X Y coordinate rows. Loaded borders are displayed on the map in red.")
        buttons = QHBoxLayout()
        open_btn = QPushButton("Load Border")
        save_btn = QPushButton("Save View Bounds Border")
        corners_btn = QPushButton("Create Border — Four Corners")
        corners_btn.clicked.connect(lambda: show_four_corner_border(self))
        buttons.addWidget(corners_btn)
        buttons.addWidget(open_btn)
        buttons.addWidget(save_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(info)
        win.setCentralWidget(root)

        def open_border() -> None:
            name, _ = QFileDialog.getOpenFileName(self, "Open border file", str(Path.home() / "Desktop"), "Border files (*.brd *.txt *.csv *.xyz);;All files (*.*)")
            if not name:
                return
            path = Path(name)
            shapes = load_xy_line_shapes(path, "#dc2626", path.stem)
            if shapes and shapes[0].points and shapes[0].points[0] != shapes[0].points[-1]:
                shapes[0].points.append(shapes[0].points[0])
            if shapes:
                self.add_layer(Layer(f"BORDER {path.name}", path, shapes))
                self.fit_extents()
                info.appendPlainText(f"Loaded border: {path.name}")

        def save_view_border() -> None:
            rect = self.view.visible_bounds()
            pts = [(rect.left(), rect.top()), (rect.right(), rect.top()), (rect.right(), rect.bottom()), (rect.left(), rect.bottom()), (rect.left(), rect.top())]
            name, _ = QFileDialog.getSaveFileName(self, "Save view border", str(self.project_folder / "EXPORT" / "view_border.brd"), "Border files (*.brd);;All files (*.*)")
            if not name:
                return
            target = Path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as handle:
                for x, y in pts:
                    handle.write(f"{x:.3f} {y:.3f}\n")
            self.add_layer(Layer(f"BORDER {target.name}", target, [Shape("line", pts, target.stem, "#dc2626")]))
            info.appendPlainText(f"Saved view border: {target}")

        open_btn.clicked.connect(open_border)
        save_btn.clicked.connect(save_view_border)
        win.resize(760, 420)
        win.show()

    def show_background_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("Background Upload / Layer Manager")
        root = QWidget()
        layout = QVBoxLayout(root)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Visible", "Name", "Path", "Items"])
        buttons = QHBoxLayout()
        load_btn = QPushButton("Load Files")
        unload_btn = QPushButton("Unload Selected")
        fit_btn = QPushButton("Fit")
        refresh_btn = QPushButton("Refresh")
        buttons.addWidget(load_btn)
        buttons.addWidget(unload_btn)
        buttons.addWidget(fit_btn)
        buttons.addWidget(refresh_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(table)
        win.setCentralWidget(root)

        def refresh() -> None:
            visible_layers = [layer for layer in self.layers if not layer.hidden]
            table.setRowCount(len(visible_layers))
            for row, layer in enumerate(visible_layers):
                visible = QTableWidgetItem("Yes" if layer.visible else "No")
                name = QTableWidgetItem(layer.name)
                path = QTableWidgetItem(str(layer.path or ""))
                count = QTableWidgetItem(str(len(layer.shapes)))
                table.setItem(row, 0, visible)
                table.setItem(row, 1, name)
                table.setItem(row, 2, path)
                table.setItem(row, 3, count)

        def unload_selected_layer() -> None:
            row = table.currentRow()
            if 0 <= row < self.layer_list.count():
                self.layer_list.setCurrentRow(row)
                self.unload_selected()
                refresh()

        load_btn.clicked.connect(lambda: (self.load_files(), refresh()))
        unload_btn.clicked.connect(unload_selected_layer)
        fit_btn.clicked.connect(self.fit_extents)
        refresh_btn.clicked.connect(refresh)
        refresh()
        win.resize(980, 520)
        win.show()

    def show_enc_panel(self) -> None:
        self.show_feature_panel("ENC / S-57", [("ENC", "Migration target: load S-57 ENC features, chart colors, object inspection, and display options.")])

    def show_kmz_panel(self) -> None:
        self.show_feature_panel("KMZ / KML", [("KMZ / KML", "Migration target: load KML/KMZ features and export survey/CAD/background layers to KMZ.")])

    def show_sb_edit_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("SB EDIT")
        root = QWidget()
        layout = QVBoxLayout(root)

        controls = QHBoxLayout()
        gnss_mode = QComboBox()
        gnss_mode.addItems(["NMEA GGA/RMC/GLL/GNS + BESTPOSA", "NMEA only", "BESTPOSA only", "Ignore GNSS"])
        echo_mode = QComboBox()
        echo_mode.addItems(["NMEA DPT/DBT + Numeric", "NMEA DPT/DBT only", "Numeric only"])
        spike_edit = QLineEdit("2.0")
        step_edit = QLineEdit("1.0")
        load_btn = QPushButton("Load RAW Files")
        apply_btn = QPushButton("Apply Parameters / Filters")
        rtk_btn = QPushButton("Apply RTK/KTD")
        export_btn = QPushButton("Export Accepted CSV")
        controls.addWidget(QLabel("GNSS"))
        controls.addWidget(gnss_mode)
        controls.addWidget(QLabel("Echosounder"))
        controls.addWidget(echo_mode)
        controls.addWidget(QLabel("Spike m"))
        controls.addWidget(spike_edit)
        controls.addWidget(QLabel("Step gate m"))
        controls.addWidget(step_edit)
        controls.addWidget(load_btn)
        controls.addWidget(apply_btn)
        controls.addWidget(rtk_btn)
        controls.addWidget(export_btn)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Vertical)
        plot = pg.PlotWidget()
        plot.setBackground("#ffffff")
        plot.showGrid(x=True, y=True, alpha=0.25)
        plot.setLabel("bottom", "Sample")
        plot.setLabel("left", "Depth", units="m")
        table = QTableWidget(0, 11)
        table.setHorizontalHeaderLabels(["Use", "File", "Time", "Easting", "Northing", "Depth", "H", "K", "RTK Tide", "Corrected", "Sentence"])
        splitter.addWidget(plot)
        splitter.addWidget(table)
        splitter.setSizes([420, 360])
        status = QLabel("Load RAW files, then apply parameters.")
        layout.addWidget(splitter)
        layout.addWidget(status)
        win.setCentralWidget(root)

        raw_lines: list[tuple[str, str]] = []
        rows: list[dict[str, object]] = []

        def parse_raw_record(line: str) -> tuple[str, str, str]:
            parts = line.strip().split(",", 2)
            if len(parts) == 3 and parts[0].upper() in {"GNSS", "SBES", "MBES"}:
                return parts[0].upper(), parts[1], parts[2]
            # HYPACK native message envelope: MSG device seconds sentence.
            hypack = line.strip().split(maxsplit=3)
            if len(hypack) == 4 and hypack[0].upper() == "MSG":
                return "AUTO", hypack[2], hypack[3]
            return "AUTO", "", line.strip()

        def sentence_allowed_for_gnss(sentence: str) -> bool:
            upper = sentence.upper()
            mode = gnss_mode.currentText()
            if mode == "Ignore GNSS":
                return False
            if mode == "NMEA only":
                return "GGA" in upper or "RMC" in upper or "GLL" in upper or "GNS" in upper
            if mode == "BESTPOSA only":
                return "BESTPOS" in upper
            return "GGA" in upper or "RMC" in upper or "GLL" in upper or "GNS" in upper or "BESTPOS" in upper

        def depth_allowed(sentence: str) -> bool:
            upper = sentence.upper().strip()
            numeric = parse_float(sentence) is not None
            nmea = upper.startswith(("$SDDPT", "$INDPT", "$IIDPT", "$YXDPT", "$SDDBT", "$INDBT", "$IIDBT"))
            mode = echo_mode.currentText()
            if mode == "NMEA DPT/DBT only":
                return nmea
            if mode == "Numeric only":
                return numeric
            return nmea or numeric

        def draw_rows() -> None:
            plot.clear()
            accepted = [row for row in rows if row.get("use")]
            if accepted:
                x = np.arange(len(accepted), dtype=float)
                y = np.asarray([float(row["depth"]) for row in accepted], dtype=float)
                plot.plot(x, y, pen=pg.mkPen("#2563eb", width=1), symbol="o", symbolBrush="#2563eb", symbolSize=4)
                plot.invertY(True)
            table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                use_item = QTableWidgetItem("Yes" if row.get("use") else "No")
                for col, value in enumerate(
                    [
                        use_item.text(),
                        row.get("file", ""),
                        row.get("time", ""),
                        f"{float(row.get('x', 0.0)):.3f}" if row.get("x") is not None else "",
                        f"{float(row.get('y', 0.0)):.3f}" if row.get("y") is not None else "",
                        f"{float(row.get('depth', 0.0)):.3f}",
                        f"{float(row.get('height', 0.0)):.3f}" if row.get("height") is not None else "",
                        f"{float(row.get('ktd_k', 0.0)):.3f}" if row.get("ktd_k") is not None else "",
                        f"{float(row.get('rtk_tide', 0.0)):.3f}" if row.get("rtk_tide") is not None else "",
                        f"{float(row.get('corrected_depth', row.get('depth', 0.0))):.3f}",
                        str(row.get("sentence", ""))[:160],
                    ]
                ):
                    table.setItem(index, col, QTableWidgetItem(str(value)))

        def load_files() -> None:
            names, _ = QFileDialog.getOpenFileNames(self, "Load RAW / NMEA files", str(self.project_folder / "RAW"), "RAW/NMEA/Text (*.raw *.RAW *.txt *.log *.000 *.csv);;All files (*.*)")
            if not names:
                return
            raw_lines.clear()
            for name in names:
                path = Path(name)
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.strip():
                        raw_lines.append((path.name, line))
            status.setText(f"Loaded {len(raw_lines):,} raw line(s) from {len(names)} file(s). Apply parameters to parse.")

        def apply_parameters() -> None:
            rows.clear()
            current_xy: Optional[tuple[float, float]] = None
            current_height: Optional[float] = None
            last_depth: Optional[float] = None
            spike_limit = abs(float(parse_float(spike_edit.text()) or 0.0))
            step_gate = abs(float(parse_float(step_edit.text()) or 0.0))
            for index, (file_name, line) in enumerate(raw_lines):
                source, timestamp, sentence = parse_raw_record(line)
                if source in {"GNSS", "AUTO"} and sentence_allowed_for_gnss(sentence):
                    lat_lon_h = self.parse_gnss_position_height(sentence)
                    if lat_lon_h:
                        xy = ll_to_utm42(lat_lon_h[0], lat_lon_h[1])
                        if xy:
                            current_xy = xy
                            current_height = lat_lon_h[2]
                if source in {"SBES", "AUTO"} and depth_allowed(sentence):
                    depth = self.extract_depth_value(sentence)
                    if depth is None:
                        continue
                    use = True
                    if last_depth is not None:
                        diff = abs(float(depth) - float(last_depth))
                        if spike_limit and diff > spike_limit:
                            use = False
                        if step_gate and diff > step_gate:
                            use = False
                    if use:
                        last_depth = float(depth)
                    rows.append(
                        {
                            "use": use,
                            "file": file_name,
                            "time": timestamp,
                            "x": current_xy[0] if current_xy else None,
                            "y": current_xy[1] if current_xy else None,
                            "height": current_height,
                            "depth": float(depth),
                            "ktd_k": None,
                            "rtk_tide": None,
                            "corrected_depth": float(depth),
                            "sentence": sentence,
                        }
                    )
                if index % 5000 == 0:
                    QApplication.processEvents()
            draw_rows()
            accepted = sum(1 for row in rows if row.get("use"))
            status.setText(f"Parsed {len(rows):,} sounding row(s); {accepted:,} accepted after filters.")

        def apply_rtk_ktd() -> None:
            if not rows:
                QMessageBox.warning(self, "SB EDIT", "Load and apply RAW read parameters first.")
                return
            if not self.ktd_file_path or not Path(self.ktd_file_path).exists():
                QMessageBox.warning(self, "SB EDIT", "No active RTK KTD file is selected in Settings / KTD Editor.")
                return
            try:
                matrix = read_ktd_matrix(Path(self.ktd_file_path))
            except Exception as exc:
                QMessageBox.warning(self, "SB EDIT", f"Could not read active KTD file:\n{exc}")
                return
            applied = 0
            missing_height = 0
            missing_k = 0
            for row in rows:
                if not row.get("use"):
                    continue
                x = row.get("x")
                y = row.get("y")
                height = row.get("height")
                if x is None or y is None:
                    missing_k += 1
                    continue
                k_value = ktd_value_at_xy(matrix, float(x), float(y))
                if k_value is None:
                    missing_k += 1
                    continue
                row["ktd_k"] = float(k_value)
                if height is None:
                    missing_height += 1
                    continue
                tide = float(height) - float(k_value)
                row["rtk_tide"] = tide
                row["corrected_depth"] = float(row.get("depth", 0.0)) + tide
                applied += 1
            draw_rows()
            status.setText(
                f"Applied RTK/KTD post-processing to {applied:,} sounding row(s) using {Path(self.ktd_file_path).name}. "
                f"Missing H: {missing_height:,}, missing K/position: {missing_k:,}."
            )

        def export_csv() -> None:
            accepted = [row for row in rows if row.get("use")]
            if not accepted:
                QMessageBox.warning(self, "SB EDIT", "No accepted rows to export.")
                return
            name, _ = QFileDialog.getSaveFileName(self, "Export SB EDIT CSV", str(self.project_folder / "EXPORT" / "sb_edit_export.csv"), "CSV files (*.csv);;All files (*.*)")
            if not name:
                return
            target = Path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Easting", "Northing", "RawDepth", "EllipsoidH", "KTD_K", "RTK_Tide_H_minus_K", "CorrectedDepth", "Time", "File", "Sentence"])
                for row in accepted:
                    writer.writerow(
                        [
                            row.get("x", ""),
                            row.get("y", ""),
                            row.get("depth", ""),
                            row.get("height", ""),
                            row.get("ktd_k", ""),
                            row.get("rtk_tide", ""),
                            row.get("corrected_depth", row.get("depth", "")),
                            row.get("time", ""),
                            row.get("file", ""),
                            row.get("sentence", ""),
                        ]
                    )
            status.setText(f"Exported {len(accepted):,} accepted sounding row(s): {target}")

        load_btn.clicked.connect(load_files)
        apply_btn.clicked.connect(apply_parameters)
        rtk_btn.clicked.connect(apply_rtk_ktd)
        export_btn.clicked.connect(export_csv)
        win.showMaximized()

    def show_ss510_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("SS510 Control Panel")
        root = QWidget()
        layout = QVBoxLayout(root)
        form = QFormLayout()
        port_box = QComboBox()
        port_box.setEditable(True)
        port_box.addItem("")
        for device, description in list_serial_ports_detailed():
            port_box.addItem(description, device)
        baud_box = QComboBox()
        baud_box.addItems(list(SS510_BAUD_RATES))
        baud_box.setCurrentText("9600")
        sv_slider = QSlider(Qt.Orientation.Horizontal)
        sv_slider.setRange(1350, 1650)
        sv_slider.setValue(int(self.sound_velocity))
        rate_slider = QSlider(Qt.Orientation.Horizontal)
        rate_slider.setRange(1, 20)
        rate_slider.setValue(1)
        sv_label = QLabel(f"{sv_slider.value()} m/s")
        rate_label = QLabel("1.00 tx/s | 60.0 tx/min | 1.000 s/tx")
        form.addRow("COM port", port_box)
        form.addRow("Baud", baud_box)
        sv_row = QHBoxLayout()
        sv_row.addWidget(sv_slider)
        sv_row.addWidget(sv_label)
        form.addRow("Sound velocity", sv_row)
        rate_row = QHBoxLayout()
        rate_row.addWidget(rate_slider)
        rate_row.addWidget(rate_label)
        form.addRow("Output rate", rate_row)
        layout.addLayout(form)

        command_row = QHBoxLayout()
        open_btn = QPushButton("Open")
        close_btn = QPushButton("Close")
        send_sv_btn = QPushButton("Send SV")
        send_rate_btn = QPushButton("Send Rate")
        command_row.addWidget(open_btn)
        command_row.addWidget(close_btn)
        command_row.addWidget(send_sv_btn)
        command_row.addWidget(send_rate_btn)
        for label, command in SS510_COMMANDS:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _checked=False, cmd=command: send_command(cmd))
            command_row.addWidget(btn)
        command_row.addStretch(1)
        layout.addLayout(command_row)
        log = QPlainTextEdit()
        log.setReadOnly(True)
        layout.addWidget(log)
        win.setCentralWidget(root)
        serial_state: dict[str, Any] = {"ser": None}

        def update_rate_label(value: int) -> None:
            hz = max(0.01, float(value))
            rate_label.setText(f"{hz:.2f} tx/s | {hz * 60.0:.1f} tx/min | {1.0 / hz:.3f} s/tx")

        def append(message: str) -> None:
            log.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')} {message}")

        def open_port() -> None:
            serial_module = get_serial_module()
            if serial_module is None:
                QMessageBox.warning(self, "SS510", "pyserial is not installed.")
                return
            if serial_state.get("ser") is not None:
                append("Port is already open.")
                return
            port = port_box.currentData() or port_box.currentText().strip()
            if " — " in port:
                port = port.split(" — ")[0].strip()
            if not port:
                QMessageBox.warning(self, "SS510", "Select a COM port.")
                return
            try:
                serial_state["ser"] = serial_module.Serial(port, int(baud_box.currentText()), timeout=0.2)
                append(f"Opened {port} @ {baud_box.currentText()}")
            except Exception as exc:
                QMessageBox.warning(self, "SS510", str(exc))

        def close_port() -> None:
            ser = serial_state.get("ser")
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
                serial_state["ser"] = None
                append("Closed port.")

        def send_command(command: str) -> None:
            ser = serial_state.get("ser")
            if ser is None:
                append(f"DRY RUN: {command.strip()}")
                return
            try:
                ser.write(command.encode("ascii", errors="replace"))
                append(f"TX: {command.strip()}")
            except Exception as exc:
                append(f"TX ERROR: {exc}")

        sv_slider.valueChanged.connect(lambda value: sv_label.setText(f"{value} m/s"))
        rate_slider.valueChanged.connect(update_rate_label)
        open_btn.clicked.connect(open_port)
        close_btn.clicked.connect(close_port)
        send_sv_btn.clicked.connect(lambda: send_command(f"SV,{sv_slider.value()}\r\n"))
        send_rate_btn.clicked.connect(lambda: send_command(f"RATE,{rate_slider.value()}\r\n"))
        win.destroyed.connect(lambda _obj=None: close_port())
        win.resize(1180, 680)
        win.show()

    def show_echogram_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("Echogram")
        root = QWidget()
        layout = QVBoxLayout(root)
        plot = pg.PlotWidget()
        plot.setBackground("#07111f")
        plot.showGrid(x=True, y=True, alpha=0.25)
        plot.setLabel("bottom", "Sample")
        plot.setLabel("left", "Depth", units="m")
        status = QLabel("Live SBES echogram from accepted depth stream.")
        buttons = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        save_btn = QPushButton("Save Echogram CSV")
        buttons.addWidget(refresh_btn)
        buttons.addWidget(save_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(plot)
        layout.addWidget(status)
        win.setCentralWidget(root)

        def refresh() -> None:
            plot.clear()
            if not self.live_soundings:
                status.setText("No SBES depth samples available yet.")
                return
            depths = np.asarray([p[2] for p in self.live_soundings[-30000:]], dtype=float)
            x = np.arange(len(depths), dtype=float)
            plot.plot(x, depths, pen=pg.mkPen("#38bdf8", width=1), symbol="o", symbolSize=3, symbolBrush="#facc15")
            plot.invertY(True)
            status.setText(f"{len(depths):,} depth sample(s), latest {depths[-1]:.3f} m")

        def save_csv() -> None:
            if not self.live_soundings:
                QMessageBox.warning(self, "Echogram", "No echogram samples to save.")
                return
            name, _ = QFileDialog.getSaveFileName(self, "Save echogram CSV", str(self.project_folder / "EXPORT" / "echogram.csv"), "CSV files (*.csv);;All files (*.*)")
            if not name:
                return
            target = Path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Sample", "Easting", "Northing", "Depth"])
                for index, (x, y, depth) in enumerate(self.live_soundings):
                    writer.writerow([index, x, y, depth])
            status.setText(f"Saved echogram CSV: {target}")

        refresh_btn.clicked.connect(refresh)
        save_btn.clicked.connect(save_csv)
        refresh()
        win.resize(1000, 620)
        win.show()

    def show_clip_by_border_panel(self) -> None:
        self.show_feature_panel(
            "Clip All by Border",
            [
                ("Clip", "Migration target: clip survey lines, KTD, DXF/ENC/KMZ backgrounds, and sounding data inside/outside the active border."),
            ],
        )

    def show_toolbar_panel(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Toolbar Settings")
        layout = QVBoxLayout(dialog)
        dock_box = QComboBox()
        dock_box.addItems(["Top", "Bottom", "Left", "Right"])
        visible_btn = QPushButton("Show / Hide Toolbar")
        apply_btn = QPushButton("Apply Dock")
        layout.addWidget(QLabel("Dock toolbar"))
        layout.addWidget(dock_box)
        layout.addWidget(apply_btn)
        layout.addWidget(visible_btn)

        def apply_dock() -> None:
            if self.main_toolbar is None:
                return
            area_map = {
                "Top": Qt.ToolBarArea.TopToolBarArea,
                "Bottom": Qt.ToolBarArea.BottomToolBarArea,
                "Left": Qt.ToolBarArea.LeftToolBarArea,
                "Right": Qt.ToolBarArea.RightToolBarArea,
            }
            self.removeToolBar(self.main_toolbar)
            self.addToolBar(area_map[dock_box.currentText()], self.main_toolbar)

        visible_btn.clicked.connect(lambda: self.main_toolbar.setVisible(not self.main_toolbar.isVisible()) if self.main_toolbar else None)
        apply_btn.clicked.connect(apply_dock)
        dialog.resize(320, 180)
        dialog.exec()

    def show_all_tools_panel(self) -> None:
        self.show_feature_panel(
            "All Tools",
            [
                ("Main", "Projects, Settings, THEME, GEOCALC, KTD Editor, SBES, Multi-Beam Sys, Start Logging, Stop Log&Save, Data Display, I/O Monitor."),
                ("Survey Planning", "Line Editor, Border Editor, Clip All by Border, Follow Vessel, Unfollow Vessel, Zoom Window, Extents."),
                ("Background", "Background Upload, DXF/DWG/KTD/XYZ, ODA DWG->DXF, ENC, KMZ/KML, Unload Selected, Unload All."),
                ("Sounding / Tools", "SB EDIT, Echogram, SS510 Control Panel, Toolbar Settings, Help, About."),
            ],
        )

    def follow_vessel(self) -> None:
        if self.current_xy is None:
            self.log_event("Follow Vessel requested, but no GNSS position has been decoded yet.")
            return
        x, y = self.current_xy
        self.push_view_history()
        self.view.setXRange(x - 50.0, x + 50.0, padding=0)
        self.view.setYRange(y - 50.0, y + 50.0, padding=0)
        self.log_event("Following current vessel position.")

    def unfollow_vessel(self) -> None:
        self.log_event("Unfollow Vessel selected. Manual pan/zoom is active.")

    def launch_classic_0701(self) -> None:
        classic_path = Path(__file__).with_name("0701.py")
        if not classic_path.exists():
            desktop_path = Path.home() / "Desktop" / "0701.py"
            classic_path = desktop_path if desktop_path.exists() else classic_path
        if not classic_path.exists():
            QMessageBox.warning(self, APP_TITLE, "Could not find 0701.py for the classic full tools bridge.")
            return
        try:
            subprocess.Popen([sys.executable, str(classic_path)], cwd=str(classic_path.parent))
            self.log_event(f"Launched classic full tools: {classic_path}")
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Could not launch classic 0701.py:\n{exc}")

    def show_theme_panel(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Theme")
        layout = QVBoxLayout(dialog)
        bg_box = QComboBox()
        bg_box.addItems(["Light", "White", "Dark", "Chart"])
        apply_btn = QPushButton("Apply")
        layout.addWidget(QLabel("Map background"))
        layout.addWidget(bg_box)
        layout.addWidget(apply_btn)

        def apply_theme() -> None:
            colors = {
                "Light": "#f8fafc",
                "White": "#ffffff",
                "Dark": "#111827",
                "Chart": "#e0f2fe",
            }
            self.view.set_map_background(colors.get(bg_box.currentText(), "#f8fafc"))
            self.log_event(f"Theme applied: {bg_box.currentText()}")

        apply_btn.clicked.connect(apply_theme)
        dialog.resize(320, 180)
        dialog.exec()

    def show_help_panel(self) -> None:
        self.show_feature_panel(
            "Help",
            [
                ("Overview", "HYDROGEOMATICS PyQt6 conversion build: survey logger controls, PyQt map, CAD/KTD loading, live GNSS/SBES logging, dotted track, and cartographic depths."),
                ("Disclaimer & Terms", SOFTWARE_TERMS),
                ("Navigation", "Mouse wheel zooms around cursor. Left-drag pans in Pan mode. Zoom Window lets you drag a rectangle. Toolbar provides Z+/Z-/N/S/E/W/Fit."),
            ],
        )

    def show_disclaimer_panel(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("HYDROGEOMATICS — Disclaimer & Terms of Use")
        root = QWidget()
        layout = QVBoxLayout(root)
        heading = QLabel("Software supplied AS IS — please read the terms below.")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(SOFTWARE_TERMS)
        layout.addWidget(editor)
        contacts = QLabel(
            'Bug reports and update requests: '
            ''
            ''
            '<a href="https://www.hydrogeomatics.com.pk">Hydrogeomatics website</a>'
        )
        contacts.setWordWrap(True)
        contacts.setOpenExternalLinks(True)
        layout.addWidget(contacts)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(win.close)
        layout.addWidget(close_btn)
        win.setCentralWidget(root)
        win.resize(960, 720)
        win.show()

    def show_about_panel(self) -> None:
        QMessageBox.information(self, "About", "HYDROGEOMATICS\nPyQt6 survey logger and hydrographic map conversion build.\n\nSupplied AS IS. See Tools > Disclaimer & Terms of Use.\nwww.hydrogeomatics.com.pk")

    def show_data_display(self) -> None:
        self.show_text_window(
            "Data Display",
            "Live Data Display\n\n"
            f"Logging: {'ON' if self.logging_active else 'OFF'}\n"
            f"Project: {self.project_folder}\n"
            f"Layers loaded: {len(self.layers)}\n\n"
            f"RAW records written: {self.raw_record_count}\n"
            f"Input queue backlog: {self.device_queue.qsize()}\n\n"
            f"Latest GNSS: {self.latest_values.get('GNSS') or '--'}\n"
            f"Latest SBES: {self.latest_values.get('SBES') or '--'}\n"
            f"Latest depth: {self.latest_values.get('DEPTH') or '--'}\n"
            f"Easting: {self.latest_values.get('EASTING') or '--'}\n"
            f"Northing: {self.latest_values.get('NORTHING') or '--'}\n"
            f"Latitude: {self.latest_values.get('LAT') or '--'}\n"
            f"Longitude: {self.latest_values.get('LON') or '--'}\n"
            f"Latest position sentence: {self.latest_values.get('POSITION') or '--'}\n\n"
            "Corrected depth, KTD K value, and line XTE fields are the next display expansion.",
        )

    def show_io_monitor(self) -> None:
        win = QMainWindow(self)
        win.setWindowTitle("I/O Monitor")
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(
            "I/O Monitor\n\n"
            f"GNSS: {self.gnss_port or 'OFF'} @ {self.gnss_baud}\n"
            f"SBES: {self.sbes_port or 'OFF'} @ {self.sbes_baud}\n\n"
        )
        win.setCentralWidget(editor)
        win.resize(860, 540)
        self.monitor_windows.append(editor)
        win.destroyed.connect(lambda _obj=None, ed=editor: self.monitor_windows.remove(ed) if ed in self.monitor_windows else None)
        win.show()

    def start_device_readers(self) -> None:
        self.stop_device_readers(join_timeout=0.2)
        serial_module = get_serial_module()
        if serial_module is None:
            self.log_event("pyserial is not installed; RAW container opened without live serial device data.")
            return
        self.device_stop.clear()
        self.device_thread_states.clear()
        configs = [("GNSS", self.gnss_port, self.gnss_baud), ("SBES", self.sbes_port, self.sbes_baud)]
        if self.shared_gnss_sbes_port and self.gnss_port and self.gnss_port == self.sbes_port:
            configs = [("GNSS", self.gnss_port, self.gnss_baud)]
        for name, port, baud in configs:
            if not port:
                continue
            self.device_thread_states[name] = PortState.CONNECTING
            thread = threading.Thread(target=self.serial_reader_worker, args=(name, port, baud), daemon=True, name=f"HYDROGEOMATICS-{name}-{port}")
            thread.start()
            self.device_threads.append(thread)
            self.log_event(f"Started {name} reader on {port} @ {baud}")
        if not self.device_threads:
            self.log_event("No GNSS/SBES serial ports selected. Use Settings to choose COM ports.")
        self._update_connection_status_display()

    def stop_device_readers(self, join_timeout: float = 1.0) -> None:
        self.device_stop.set()
        for thread in list(self.device_threads):
            thread.join(timeout=join_timeout)
        self.device_threads.clear()
        self.device_thread_states.clear()
        self._update_connection_status_display()

    def serial_reader_worker(self, name: str, port: str, baud: int) -> None:
        serial_module = get_serial_module()
        if serial_module is None:
            return
        reconnect_delay = 0.5
        max_reconnect_delay = 8.0
        consecutive_errors = 0

        while not self.device_stop.is_set():
            # Check if port is physically present before trying to open
            if not self.port_monitor.is_port_available(port):
                self.device_thread_states[name] = PortState.DISCONNECTED
                self.device_queue.put((name, datetime.now().strftime("%H:%M:%S.%f")[:-3],
                                      f"WAIT {port}: port not detected, waiting for reconnection…"))
                # Wait until port reappears or stop is signalled
                for _ in range(int(max_reconnect_delay / 0.5)):
                    if self.device_stop.is_set():
                        return
                    if self.port_monitor.is_port_available(port):
                        break
                    self.device_stop.wait(0.5)
                continue

            ser = None
            try:
                self.device_thread_states[name] = PortState.CONNECTING
                ser = serial_module.Serial(port=port, baudrate=int(baud), timeout=0.3)
                self.device_thread_states[name] = PortState.CONNECTED
                self.port_monitor.set_port_state(port, PortState.CONNECTED)
                consecutive_errors = 0
                reconnect_delay = 0.5
                self.device_queue.put((name, datetime.now().strftime("%H:%M:%S.%f")[:-3],
                                      f"CONNECTED {port} @ {baud}"))

                while not self.device_stop.is_set():
                    try:
                        raw = ser.readline()
                    except Exception as read_exc:
                        self.device_queue.put((name, datetime.now().strftime("%H:%M:%S.%f")[:-3],
                                              f"READ_ERROR {port}: {read_exc}"))
                        break  # exit inner loop to trigger reconnect
                    if not raw:
                        continue
                    text = raw.decode("ascii", errors="replace").strip()
                    if text:
                        self.device_queue.put((name, datetime.now().strftime("%H:%M:%S.%f")[:-3], text))

            except Exception as exc:
                consecutive_errors += 1
                self.device_thread_states[name] = PortState.ERROR
                self.port_monitor.set_port_state(port, PortState.ERROR)
                self.device_queue.put((name, datetime.now().strftime("%H:%M:%S.%f")[:-3],
                                      f"ERROR {port}: {exc} (attempt {consecutive_errors})"))
            finally:
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass

            if self.device_stop.is_set():
                return

            # Backoff before reconnect
            self.device_thread_states[name] = PortState.DISCONNECTED
            self.port_monitor.set_port_state(port, PortState.DISCONNECTED)
            self.device_queue.put((name, datetime.now().strftime("%H:%M:%S.%f")[:-3],
                                  f"RECONNECT {port}: retrying in {reconnect_delay:.1f}s…"))
            self.device_stop.wait(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

        self.device_thread_states[name] = PortState.UNAVAILABLE

    def _periodic_port_scan(self) -> None:
        """Called by port_scan_timer every 2 seconds to detect port changes."""
        appeared, disappeared = self.port_monitor.scan()
        if appeared:
            self.log_event(f"Serial port(s) detected: {', '.join(appeared)}")
        if disappeared:
            self.log_event(f"Serial port(s) removed: {', '.join(disappeared)}")
        if appeared or disappeared:
            self._update_connection_status_display()

    def _on_port_state_change(self, port: str, new_state: str, old_state: str) -> None:
        """Callback from SerialPortMonitor when a port's state changes."""
        pass  # state is tracked; UI update happens in _periodic_port_scan and process_device_queue

    def _update_connection_status_display(self) -> None:
        """Update the GNSS/SBES status labels in the header bar."""
        for name, port_attr, label in [
            ("GNSS", "gnss_port", self.gnss_status_label),
            ("SBES", "sbes_port", self.sbes_status_label),
        ]:
            port = getattr(self, port_attr, "")
            if not port:
                label.setText(f"{name}: not configured")
                label.setStyleSheet("font-size:11px; padding:3px 10px; border-radius:3px; background:#f3f4f6; color:#6b7280;")
                continue

            # Determine effective state
            thread_state = self.device_thread_states.get(name)
            if thread_state:
                state = thread_state
            elif self.port_monitor.is_port_available(port):
                state = PortState.AVAILABLE
            else:
                state = PortState.UNAVAILABLE

            color = PortState.color(state)
            state_text = PortState.label(state)
            label.setText(f"{name}: {port} · {state_text}")

            if state == PortState.CONNECTED:
                label.setStyleSheet(f"font-size:11px; font-weight:600; padding:3px 10px; border-radius:3px; background:#dcfce7; color:{color};")
            elif state in (PortState.DISCONNECTED, PortState.ERROR):
                label.setStyleSheet(f"font-size:11px; font-weight:600; padding:3px 10px; border-radius:3px; background:#fef2f2; color:{color};")
            elif state == PortState.CONNECTING:
                label.setStyleSheet(f"font-size:11px; padding:3px 10px; border-radius:3px; background:#fefce8; color:{color};")
            else:
                label.setStyleSheet(f"font-size:11px; padding:3px 10px; border-radius:3px; background:#f3f4f6; color:{color};")

    def process_device_queue(self) -> None:
        processed = 0
        status_changed = False
        while processed < 2000:
            try:
                name, timestamp, text = self.device_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            # Detect connection lifecycle messages (not raw NMEA data)
            upper_text = text.upper()
            is_status_msg = upper_text.startswith(("ERROR ", "CONNECTED ", "RECONNECT ", "WAIT ", "READ_ERROR "))
            if is_status_msg:
                status_changed = True
                self.log_event(f"{name}: {text}")
                for editor in list(self.monitor_windows):
                    editor.appendPlainText(f"{timestamp} {name}: {text}")
                continue  # don't write status messages to RAW log or update sentence values

            self.latest_values[name] = text
            if self.log_file_handle is not None and self.logging_active:
                safe_text = text.replace("\r", " ").replace("\n", " ")
                self.log_file_handle.write(f"{name},{timestamp},{safe_text}\n")
                self.raw_record_count += 1
                now = time.monotonic()
                if now - self.last_flush_time >= 1.0:
                    self.log_file_handle.flush()
                    self.last_flush_time = now
            # Archive first: processing preferences must never suppress raw data.
            self.update_latest_from_sentence(name, text)
            if self.shared_gnss_sbes_port:
                self.update_latest_from_sentence("SBES" if name == "GNSS" else "GNSS", text)
            for editor in list(self.monitor_windows):
                editor.appendPlainText(f"{timestamp} {name}: {text}")
        if self.log_file_handle is not None and processed:
            self.log_file_handle.flush()
        if status_changed:
            self._update_connection_status_display()

    def _is_sentence_enabled(self, text: str) -> bool:
        """Check if a GNSS/NMEA sentence is enabled in the Advanced GNSS Settings."""
        header = text.strip().upper().split("*", 1)[0].split(";", 1)[0].split(",")
        token = header[0].lstrip("$!#")
        key = token[-3:] if len(token) == 5 and not token.startswith("P") else token
        if token == "PTNL" and len(header) > 1:
            key = token + "," + header[1]
        return self.enabled_nmea_sentences.get(key, True)

    def update_latest_from_sentence(self, name: str, text: str) -> None:
        upper = text.upper()
        if name == "GNSS" and ("GGA" in upper or "RMC" in upper or "GLL" in upper or "GNS" in upper or "BESTPOS" in upper):
            if not self._is_sentence_enabled(text):
                return
            self.latest_values["POSITION"] = text
            # Extract accuracy from GST/BESTPOS for accuracy monitor
            self._extract_accuracy_from_sentence(text)
            lat_lon = self.parse_gnss_position(text)
            if lat_lon:
                lat, lon = lat_lon
                self.current_lat_lon = (lat, lon)
                self.latest_values["LAT"] = f"{lat:.8f}"
                self.latest_values["LON"] = f"{lon:.8f}"
                xy = ll_to_utm42(lat, lon)
                if xy:
                    self.current_xy = xy
                    self.latest_values["EASTING"] = f"{xy[0]:.3f}"
                    self.latest_values["NORTHING"] = f"{xy[1]:.3f}"
                    self.live_track_points.append(xy)
                    if len(self.live_track_points) > 20000:
                        self.live_track_points = self.live_track_points[-20000:]
                    self.refresh_live_survey_layer()
        if name == "GNSS" and "GST" in upper:
            if self._is_sentence_enabled(text):
                self._extract_accuracy_from_sentence(text)
        if name == "SBES":
            depth_value = self.extract_depth_value(text)
            if depth_value is not None:
                self.latest_values["DEPTH"] = f"{depth_value:.3f} m"
                if self.current_xy is not None:
                    x, y = self.current_xy
                    self.live_soundings.append((x, y, float(depth_value)))
                    if len(self.live_soundings) > 20000:
                        self.live_soundings = self.live_soundings[-20000:]
                    self.refresh_live_survey_layer()

    def _extract_accuracy_from_sentence(self, text: str) -> None:
        """Extract X, Y, Z accuracy from GST or BESTPOSA sentences."""
        upper = text.upper().strip()
        try:
            if "GST" in upper and self.enabled_nmea_sentences.get("GST", True):
                # $xxGST,time,rms,smaj,smin,orient,lat_err,lon_err,alt_err*cs
                body = text.split("*")[0] if "*" in text else text
                parts = body.split(",")
                if len(parts) >= 8:
                    lat_sigma = parse_float(parts[6])
                    lon_sigma = parse_float(parts[7])
                    alt_sigma = parse_float(parts[8]) if len(parts) > 8 else None
                    if lat_sigma is not None:
                        self.latest_accuracy["y"] = float(lat_sigma)
                    if lon_sigma is not None:
                        self.latest_accuracy["x"] = float(lon_sigma)
                    if alt_sigma is not None:
                        self.latest_accuracy["z"] = float(alt_sigma)
            elif "BESTPOS" in upper:
                body = text.split("*")[0] if "*" in text else text
                if ";" in body:
                    data = body.split(";", 1)[1]
                    fields = [p.strip() for p in data.split(",")]
                    # BESTPOSA fields: ..., lat_sigma(idx 7), lon_sigma(idx 8), hgt_sigma(idx 9)
                    if len(fields) >= 10:
                        lat_sigma = parse_float(fields[7])
                        lon_sigma = parse_float(fields[8])
                        hgt_sigma = parse_float(fields[9])
                        if lat_sigma is not None:
                            self.latest_accuracy["y"] = float(lat_sigma)
                        if lon_sigma is not None:
                            self.latest_accuracy["x"] = float(lon_sigma)
                        if hgt_sigma is not None:
                            self.latest_accuracy["z"] = float(hgt_sigma)
        except Exception:
            pass

    def show_accuracy_monitor(self) -> None:
        """Open (or bring to front) the Accuracy Monitor showing X, Y, Z accuracy values."""
        if self.accuracy_monitor_window is not None:
            try:
                if self.accuracy_monitor_window.isVisible():
                    self.accuracy_monitor_window.raise_()
                    return
            except Exception:
                pass

        win = QMainWindow(self)
        win.setWindowTitle("Accuracy Monitor")
        win.resize(340, 220)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(QLabel("GNSS Position Accuracy"))
        x_label = QLabel("X (Easting) σ:  —")
        y_label = QLabel("Y (Northing) σ:  —")
        z_label = QLabel("Z (Height) σ:  —")
        for lbl in (x_label, y_label, z_label):
            lbl.setFont(QFont("Consolas", 13, QFont.Weight.Bold))
            lbl.setStyleSheet("padding: 6px; border: 1px solid #d1d5db; border-radius: 4px; background: #f9fafb;")
            layout.addWidget(lbl)
        status = QLabel("Waiting for accuracy data from GNSS (GST / BESTPOSA)…")
        status.setWordWrap(True)
        layout.addWidget(status)
        win.setCentralWidget(root)

        def update_accuracy() -> None:
            x = self.latest_accuracy.get("x")
            y = self.latest_accuracy.get("y")
            z = self.latest_accuracy.get("z")
            x_label.setText(f"X (Easting) σ:  {x:.4f} m" if x is not None else "X (Easting) σ:  —")
            y_label.setText(f"Y (Northing) σ:  {y:.4f} m" if y is not None else "Y (Northing) σ:  —")
            z_label.setText(f"Z (Height) σ:  {z:.4f} m" if z is not None else "Z (Height) σ:  —")
            any_value = x is not None or y is not None or z is not None
            if any_value:
                status.setText("Accuracy values are live from the GNSS stream.")
                # Color code: green if < 0.05m, yellow < 0.1, red otherwise
                for val, lbl in [(x, x_label), (y, y_label), (z, z_label)]:
                    if val is not None:
                        color = "#166534" if val < 0.05 else "#92400e" if val < 0.10 else "#991b1b"
                        lbl.setStyleSheet(f"padding: 6px; border: 1px solid #d1d5db; border-radius: 4px; background: #f9fafb; color: {color};")

        timer = QTimer(win)
        timer.timeout.connect(update_accuracy)
        timer.start(500)
        win.destroyed.connect(lambda _obj=None: setattr(self, 'accuracy_monitor_window', None))
        self.accuracy_monitor_window = win
        win.show()

    def extract_depth(self, text: str) -> str:
        value = self.extract_depth_value(text)
        return f"{value:.3f} m" if value is not None else ""

    def extract_depth_value(self, text: str) -> Optional[float]:
        upper = text.upper().strip()
        parts = upper.split(",")
        if upper.startswith(("$SDDPT", "$INDPT", "$IIDPT", "$YXDPT")) and len(parts) > 1:
            value = parse_float(parts[1])
            return float(value) if value is not None else None
        if upper.startswith(("$SDDBT", "$INDBT", "$IIDBT")) and len(parts) > 3:
            value = parse_float(parts[3])
            return float(value) if value is not None else None
        value = parse_float(text)
        return float(value) if value is not None else None

    def parse_gnss_position(self, text: str) -> Optional[tuple[float, float]]:
        upper = text.upper()
        if "BESTPOS" in upper:
            bestpos_height = self.parse_bestpos_position_height(text)
            bestpos = (bestpos_height[0], bestpos_height[1]) if bestpos_height else None
            if bestpos:
                return bestpos
        return self.parse_nmea_position(text)

    def parse_gnss_position_height(self, text: str) -> Optional[tuple[float, float, Optional[float]]]:
        upper = text.upper()
        if "BESTPOS" in upper:
            return self.parse_bestpos_position_height(text)
        pos = self.parse_nmea_position(text)
        return (pos[0], pos[1], None) if pos else None

    def parse_nmea_position(self, text: str) -> Optional[tuple[float, float]]:
        body = text.strip()
        if "*" in body:
            body = body.split("*", 1)[0]
        parts = body.split(",")
        if not parts:
            return None
        sentence = parts[0].upper()
        if sentence.endswith(("GGA", "GNS")) and len(parts) >= 7:
            if sentence.endswith("GGA") and parts[6] in {"", "0"}:
                return None
            if sentence.endswith("GNS") and not parts[6].strip("N"):
                return None
            lat = self.nmea_coord_to_decimal(parts[2], parts[3])
            lon = self.nmea_coord_to_decimal(parts[4], parts[5])
            if lat is not None and lon is not None:
                return lat, lon
        if sentence.endswith("GLL") and len(parts) >= 7 and parts[6] == "A":
            lat = self.nmea_coord_to_decimal(parts[1], parts[2])
            lon = self.nmea_coord_to_decimal(parts[3], parts[4])
            if lat is not None and lon is not None:
                return lat, lon
        if sentence.endswith("RMC") and len(parts) >= 7 and parts[2] == "A":
            lat = self.nmea_coord_to_decimal(parts[3], parts[4])
            lon = self.nmea_coord_to_decimal(parts[5], parts[6])
            if lat is not None and lon is not None:
                return lat, lon
        return None

    def parse_bestpos_position(self, text: str) -> Optional[tuple[float, float]]:
        parsed = self.parse_bestpos_position_height(text)
        return (parsed[0], parsed[1]) if parsed else None

    def parse_bestpos_position_height(self, text: str) -> Optional[tuple[float, float, Optional[float]]]:
        body = text.strip()
        if not body:
            return None
        if "*" in body:
            body = body.split("*", 1)[0]
        if ";" in body:
            data = body.split(";", 1)[1]
            fields = [part.strip() for part in data.split(",")]
            # NovAtel BESTPOS(A): sol status, pos type, lat, lon, height, ...
            if len(fields) >= 4:
                lat = parse_float(fields[2])
                lon = parse_float(fields[3])
                height = parse_float(fields[4]) if len(fields) >= 5 else None
                if lat is not None and lon is not None:
                    return float(lat), float(lon), float(height) if height is not None else None
        fields = [part.strip() for part in body.lstrip("#").split(",")]
        for index, field in enumerate(fields):
            if field.upper() in {"SOL_COMPUTED", "INSUFFICIENT_OBS", "NO_CONVERGENCE"} and index + 3 < len(fields):
                lat = parse_float(fields[index + 2])
                lon = parse_float(fields[index + 3])
                height = parse_float(fields[index + 4]) if index + 4 < len(fields) else None
                if lat is not None and lon is not None:
                    return float(lat), float(lon), float(height) if height is not None else None
        # Fallback for simple text: BESTPOSA,lat,lon,height or BESTPOS,lat,lon,height.
        if fields and fields[0].upper().startswith("BESTPOS") and len(fields) >= 3:
            lat = parse_float(fields[1])
            lon = parse_float(fields[2])
            height = parse_float(fields[3]) if len(fields) >= 4 else None
            if lat is not None and lon is not None and abs(float(lat)) <= 90 and abs(float(lon)) <= 180:
                return float(lat), float(lon), float(height) if height is not None else None
        return None

    def nmea_coord_to_decimal(self, value: str, hemisphere: str) -> Optional[float]:
        raw = parse_float(value)
        if raw is None:
            return None
        degrees_part = int(raw // 100)
        minutes = raw - degrees_part * 100
        decimal = degrees_part + minutes / 60.0
        if str(hemisphere).upper() in {"S", "W"}:
            decimal *= -1.0
        return float(decimal)

    def refresh_live_survey_layer(self) -> None:
        self.view.update_live(self.live_track_points, self.live_soundings)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.logging_active:
            self.stop_logging_and_save()
        else:
            self.stop_device_readers(join_timeout=0.2)
        event.accept()

    def zoom_view(self, factor: float) -> None:
        self.push_view_history()
        self.view.zoom_by(float(factor))
        self.update_status_from_view()

    def pan_view(self, dx_fraction: float, dy_fraction: float) -> None:
        self.push_view_history()
        self.view.pan_fraction(float(dx_fraction), float(dy_fraction))
        self.update_status_from_view()

    def set_mode(self, mode: str) -> None:
        self.view.set_mode(mode)
        self.mode_label.setText(mode)
        if mode == "Edit DXF":
            self.status.showMessage("Edit DXF mode: click and drag a river bank/channel line vertex, then use Save Edited DXF.", 8000)
        elif mode == "Measure":
            self.status.showMessage("Measure mode: click and drag on the map to measure distance and bearing.", 8000)
        else:
            self.clear_edit_marker()

    def clear_edit_marker(self) -> None:
        self.edit_selection = None
        if self.edit_marker is not None:
            try:
                self.view.removeItem(self.edit_marker)
            except Exception:
                pass
            self.edit_marker = None

    def edit_pick_tolerance(self) -> float:
        rect = self.view.visible_bounds()
        return max(5.0, min(150.0, max(rect.width(), rect.height()) / 120.0))

    def select_edit_vertex(self, x: float, y: float) -> bool:
        tolerance = self.edit_pick_tolerance()
        best: Optional[tuple[float, Layer, Shape, int]] = None
        for layer in self.layers:
            if not layer.visible:
                continue
            for shape in layer.shapes:
                if shape.kind != "line" or not shape.points:
                    continue
                for index, (px, py) in enumerate(shape.points):
                    distance = math.hypot(float(px) - x, float(py) - y)
                    if distance <= tolerance and (best is None or distance < best[0]):
                        best = (distance, layer, shape, index)
        if best is None:
            self.status.showMessage(f"No editable DXF/line vertex within {tolerance:.1f} m.", 4000)
            return False
        _distance, layer, shape, index = best
        self.edit_selection = (layer, shape, index)
        self.show_edit_marker(float(shape.points[index][0]), float(shape.points[index][1]))
        self.status.showMessage(f"Selected vertex {index + 1} in {layer.name}. Drag to adjust.", 6000)
        return True

    def show_edit_marker(self, x: float, y: float) -> None:
        if self.edit_marker is None:
            self.edit_marker = pg.ScatterPlotItem(pxMode=True, pen=pg.mkPen("#ffffff", width=2), brush=pg.mkBrush(239, 68, 68, 230), size=14)
            self.view.addItem(self.edit_marker)
        self.edit_marker.setData(x=[x], y=[y])

    def move_selected_vertex(self, x: float, y: float) -> None:
        if self.edit_selection is None:
            return
        layer, shape, index = self.edit_selection
        if index < 0 or index >= len(shape.points):
            return
        old_point = shape.points[index]
        shape.points[index] = (float(x), float(y))
        if len(shape.points) > 2:
            if index == 0 and old_point == shape.points[-1]:
                shape.points[-1] = (float(x), float(y))
            elif index == len(shape.points) - 1 and old_point == shape.points[0]:
                shape.points[0] = (float(x), float(y))
        self.redraw_layer(layer)
        self.show_edit_marker(float(x), float(y))
        self.set_coordinate_status(float(x), float(y))

    def finish_edit_vertex(self) -> None:
        if self.edit_selection is None:
            return
        layer, _shape, index = self.edit_selection
        self.status.showMessage(f"Adjusted vertex {index + 1} in {layer.name}. Save Edited DXF to write changes.", 7000)

    def redraw_layer(self, layer: Layer) -> None:
        self.view.remove_layer_items(layer.group)
        layer.group = self.view.add_shapes_layer(layer.shapes)

    def line_layers(self) -> list[Layer]:
        return [layer for layer in self.layers if any(shape.kind == "line" and len(shape.points) >= 2 for shape in layer.shapes)]

    def write_line_layers_to_dxf(self, layers: list[Layer], target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        ezdxf = get_ezdxf()
        if ezdxf is None:
            raise RuntimeError("ezdxf is required to save edited DXF files.")
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        for layer in layers:
            layer_name = re.sub(r"[^A-Za-z0-9_ -]+", "_", layer.name)[:80] or "EDITED"
            if layer_name not in doc.layers:
                doc.layers.add(layer_name)
            for shape in layer.shapes:
                if shape.kind == "line" and len(shape.points) >= 2:
                    msp.add_lwpolyline([(float(x), float(y)) for x, y in shape.points], dxfattribs={"layer": layer_name})
        doc.saveas(str(target))

    def show_dxf_edit_window(self) -> None:
        existing = getattr(self, '_cad_edit_window', None)
        if existing is not None:
            existing.show(); existing.raise_(); existing.activateWindow()
            return existing
        line_layers = self.line_layers()
        win = QMainWindow(self)
        self._cad_edit_window = win
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        win.destroyed.connect(lambda: setattr(self, '_cad_edit_window', None))
        win.setWindowTitle("DXF On-Screen Edit Window")
        root = QWidget()
        layout = QVBoxLayout(root)
        top = QHBoxLayout()
        layer_box = QComboBox()
        for layer in line_layers:
            layer_box.addItem(layer.name)
        select_btn = QPushButton("Select Vertex")
        pan_btn = QPushButton("Pan")
        zoom_window_btn = QPushButton("Zoom Window")
        fit_btn = QPushButton("Fit")
        save_btn = QPushButton("Save DXF")
        close_btn = QPushButton("Close")
        status = QLabel("Select a layer, then click and drag a vertex.")
        top.addWidget(QLabel("Layer"))
        top.addWidget(layer_box)
        top.addWidget(select_btn)
        top.addWidget(pan_btn)
        top.addWidget(zoom_window_btn)
        top.addWidget(fit_btn)
        top.addWidget(save_btn)
        top.addWidget(close_btn)
        load_btn = QPushButton("Load backgrounds")
        load_btn.clicked.connect(lambda: self.show_background_manager())
        top.addWidget(load_btn)
        top.addStretch(1)
        layout.addLayout(top)
        plot = pg.PlotWidget()
        plot.setBackground("#ffffff")
        plot.showGrid(x=True, y=True, alpha=0.2)
        plot.setAspectLocked(True)
        layout.addWidget(plot, 1)
        layout.addWidget(status)
        win.setCentralWidget(root)

        state: dict[str, Any] = {"layer": None, "items": [], "vertex_item": None, "marker": None, "selected": None, "edit_enabled": True}

        def current_layer() -> Layer:
            index = layer_box.currentIndex()
            return line_layers[index] if 0 <= index < len(line_layers) else None

        def clear_plot() -> None:
            plot.clear()
            state["items"] = []
            state["vertex_item"] = None
            state["marker"] = None

        def draw_layer() -> None:
            clear_plot()
            layer = current_layer()
            state["layer"] = layer
            state["selected"] = None
            for button in (select_btn, pan_btn, zoom_window_btn, fit_btn, save_btn):button.setEnabled(layer is not None)
            if layer is None:
                status.setText("No editable linework loaded. Use Load backgrounds to add a DXF file.")
                return
            xs_all: list[float] = []
            ys_all: list[float] = []
            for shape_index, shape in enumerate(layer.shapes):
                if shape.kind != "line" or len(shape.points) < 2:
                    continue
                arr = np.asarray(shape.points, dtype=float)
                state["items"].append((shape, None))
                xs_all.extend(arr[:, 0].tolist())
                ys_all.extend(arr[:, 1].tolist())
            state["batch_items"] = PyQtGraphSurveyMap.add_shapes_layer(plot, [shape for shape, _ in state["items"]])
            if xs_all:
                vertex_item = pg.ScatterPlotItem(x=np.asarray(xs_all), y=np.asarray(ys_all), pxMode=True, pen=None, brush=pg.mkBrush(239, 68, 68, 140), size=4)
                plot.addItem(vertex_item)
                state["vertex_item"] = vertex_item
                plot.setRange(xRange=(min(xs_all), max(xs_all)), yRange=(min(ys_all), max(ys_all)), padding=0.08)
            marker = pg.ScatterPlotItem(pxMode=True, pen=pg.mkPen("#ffffff", width=2), brush=pg.mkBrush(220, 38, 38, 230), size=14)
            plot.addItem(marker)
            marker.setVisible(False)
            state["marker"] = marker
            status.setText(f"Loaded {layer.name}. Click-drag a red vertex marker.")

        def fit_edit_layer() -> None:
            xs: list[float] = []
            ys: list[float] = []
            for shape, _item in state["items"]:
                for x, y in shape.points:
                    xs.append(float(x))
                    ys.append(float(y))
            if xs and ys:
                plot.setRange(xRange=(min(xs), max(xs)), yRange=(min(ys), max(ys)), padding=0.08)

        def map_xy(event) -> tuple[float, float]:
            scene_pos = plot.mapToScene(event.position().toPoint())
            point = plot.plotItem.vb.mapSceneToView(scene_pos)
            return float(point.x()), float(point.y())

        def tolerance() -> float:
            x_range, y_range = plot.plotItem.vb.viewRange()
            return max(2.0, min(100.0, max(x_range[1] - x_range[0], y_range[1] - y_range[0]) / 160.0))

        def refresh_vertices() -> None:
            xs: list[float] = []
            ys: list[float] = []
            for shape, _item in state["items"]:
                for x, y in shape.points:
                    xs.append(float(x))
                    ys.append(float(y))
            vertex_item = state.get("vertex_item")
            if vertex_item is not None:
                vertex_item.setData(x=np.asarray(xs), y=np.asarray(ys))

        def mouse_press(event) -> None:
            if not state.get("edit_enabled", True):
                pg.PlotWidget.mousePressEvent(plot, event)
                return
            if event.button() != Qt.MouseButton.LeftButton:
                pg.PlotWidget.mousePressEvent(plot, event)
                return
            x, y = map_xy(event)
            best: Optional[tuple[float, Shape, int, Any]] = None
            tol = tolerance()
            for shape, item in state["items"]:
                for index, (px, py) in enumerate(shape.points):
                    dist = math.hypot(float(px) - x, float(py) - y)
                    if dist <= tol and (best is None or dist < best[0]):
                        best = (dist, shape, index, item)
            if best is None:
                status.setText(f"No vertex within {tol:.1f} m.")
                return
            _dist, shape, index, item = best
            item = pg.PlotDataItem([p[0] for p in shape.points], [p[1] for p in shape.points], pen=pg.mkPen("#dc2626", width=2))
            plot.addItem(item)
            state["selected"] = (shape, index, item)
            marker = state.get("marker")
            if marker is not None:
                marker.setData(x=[shape.points[index][0]], y=[shape.points[index][1]])
                marker.setVisible(True)
            status.setText(f"Selected vertex {index + 1}. Drag to adjust.")

        def mouse_move(event) -> None:
            if not state.get("edit_enabled", True):
                pg.PlotWidget.mouseMoveEvent(plot, event)
                return
            selected = state.get("selected")
            if selected is None:
                pg.PlotWidget.mouseMoveEvent(plot, event)
                return
            x, y = map_xy(event)
            shape, index, item = selected
            old = shape.points[index]
            shape.points[index] = (float(x), float(y))
            if len(shape.points) > 2:
                if index == 0 and old == shape.points[-1]:
                    shape.points[-1] = (float(x), float(y))
                elif index == len(shape.points) - 1 and old == shape.points[0]:
                    shape.points[0] = (float(x), float(y))
            arr = np.asarray(shape.points, dtype=float)
            item.setData(arr[:, 0], arr[:, 1])
            marker = state.get("marker")
            if marker is not None:
                marker.setData(x=[float(x)], y=[float(y)])
            status.setText(f"E {x:,.3f}  N {y:,.3f}")

        def mouse_release(event) -> None:
            if not state.get("edit_enabled", True):
                pg.PlotWidget.mouseReleaseEvent(plot, event)
                return
            if state.get("selected") is not None:
                state["selected"] = None
                bounds = plot.viewRange()
                draw_layer()
                plot.setRange(xRange=bounds[0], yRange=bounds[1], padding=0)
                layer = state.get("layer")
                if isinstance(layer, Layer):
                    self.redraw_layer(layer)
                    if hasattr(self, '_send_layers_to_survey'):self._send_layers_to_survey()
                status.setText("Vertex adjusted. Continue editing or Save DXF.")
                return
            pg.PlotWidget.mouseReleaseEvent(plot, event)

        def save_layer() -> None:
            layer = current_layer()
            if layer is None:return
            name, _ = QFileDialog.getSaveFileName(win, "Save edited DXF", str(self.project_folder / "CAD" / f"{layer.name}_edited.dxf"), "DXF files (*.dxf);;All files (*.*)")
            if not name:
                return
            try:
                self.write_line_layers_to_dxf([layer], Path(name))
                status.setText(f"Saved edited DXF: {name}")
                self.log_event(f"Saved edited DXF: {name}")
            except Exception as exc:
                QMessageBox.warning(win, APP_TITLE, f"Could not save edited DXF:\n{exc}")

        plot.mousePressEvent = mouse_press  # type: ignore[method-assign]
        plot.mouseMoveEvent = mouse_move  # type: ignore[method-assign]
        plot.mouseReleaseEvent = mouse_release  # type: ignore[method-assign]
        layer_box.currentIndexChanged.connect(draw_layer)
        select_btn.clicked.connect(lambda: (state.update({"edit_enabled": True}), plot.plotItem.vb.setMouseMode(pg.ViewBox.PanMode), status.setText("Select Vertex mode: click-drag a vertex.")))
        pan_btn.clicked.connect(lambda: (state.update({"edit_enabled": False}), plot.plotItem.vb.setMouseMode(pg.ViewBox.PanMode), status.setText("Pan mode.")))
        zoom_window_btn.clicked.connect(lambda: (state.update({"edit_enabled": False}), plot.plotItem.vb.setMouseMode(pg.ViewBox.RectMode), status.setText("Zoom Window mode: drag a rectangle.")))
        fit_btn.clicked.connect(fit_edit_layer)
        save_btn.clicked.connect(save_layer)
        close_btn.clicked.connect(win.close)
        def refresh_layers():
            nonlocal line_layers
            latest = self.line_layers()
            if [id(l) for l in latest] == [id(l) for l in line_layers]:return
            current = current_layer()
            line_layers = latest
            layer_box.blockSignals(True);layer_box.clear()
            for layer in line_layers:layer_box.addItem(layer.name)
            layer_box.setCurrentIndex(next((i for i,l in enumerate(line_layers) if l is current), 0 if line_layers else -1))
            layer_box.blockSignals(False);draw_layer()
        timer = QTimer(win);timer.timeout.connect(refresh_layers);timer.start(200)
        win._plot = plot;win._state = state;win._layers = layer_box;win._status = status
        win._refresh_layers = refresh_layers
        available = win.screen().availableGeometry()
        win.resize(min(1100, int(available.width()*.9)), min(760, int(available.height()*.85)))
        win.show()
        draw_layer()
        return win

    def save_edited_linework(self) -> None:
        line_layers = self.line_layers()
        if not line_layers:
            QMessageBox.warning(self, APP_TITLE, "No editable DXF/line layers are loaded.")
            return
        name, _ = QFileDialog.getSaveFileName(self, "Save edited linework DXF", str(self.project_folder / "CAD" / "edited_river_banks_channel.dxf"), "DXF files (*.dxf);;All files (*.*)")
        if not name:
            return
        try:
            target = Path(name)
            self.write_line_layers_to_dxf(line_layers, target)
            self.log_event(f"Saved edited linework DXF: {target}")
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Could not save edited DXF:\n{exc}")

    def set_coordinate_status(self, x: float, y: float) -> None:
        text = f"E: {x:,.3f}  N: {y:,.3f}"
        transformer_class = get_transformer_class() if self.crs_box.currentText().startswith("EPSG:32642") else None
        if transformer_class is not None:
            try:
                if self.transformer is None:
                    self.transformer = transformer_class.from_crs("EPSG:32642", "EPSG:4326", always_xy=True)
                lon, lat = self.transformer.transform(x, y)
                text += f"   Lat: {lat:.8f}  Lon: {lon:.8f}"
            except Exception:
                pass
        self.coord_label.setText(text)

    def update_status_from_view(self) -> None:
        rect = self.view.visible_bounds()
        self.scale_label.setText(f"View: {rect.width():,.1f} x {rect.height():,.1f} m")

    def push_view_history(self) -> None:
        rect = self.view.visible_bounds()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        entry = (float(rect.left()), float(rect.top()), float(rect.right()), float(rect.bottom()))
        if self.view_history and all(abs(a - b) < 1e-6 for a, b in zip(self.view_history[-1], entry)):
            return
        self.view_history.append(entry)
        if len(self.view_history) > 30:
            self.view_history = self.view_history[-30:]

    def zoom_previous(self) -> None:
        if not self.view_history:
            self.status.showMessage("No previous zoom view is available.", 4000)
            return
        left, top, right, bottom = self.view_history.pop()
        self.view.fit_bounds((left, top, right, bottom))
        self.update_status_from_view()

    def add_layer(self, layer: Layer) -> None:
        if layer.path is not None and layer.path.exists():
            try:
                layer.path = self.project_copy_for_background(layer.path)
            except Exception as exc:
                self.log_event(f"Could not copy layer into project folder: {exc}")
        layer.group = self.view.add_shapes_layer(layer.shapes)
        self.layers.append(layer)
        if not layer.hidden:
            item = QListWidgetItem(layer.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.layer_list.addItem(item)
        self.status.showMessage(f"Loaded {layer.name}: {len(layer.shapes)} item(s)", 5000)
        if not layer.hidden:
            self.save_background_state()

    def layer_for_list_row(self, row: int) -> Optional[Layer]:
        visible_layers = [layer for layer in self.layers if not layer.hidden]
        if 0 <= row < len(visible_layers):
            return visible_layers[row]
        return None

    def load_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "Load map files",
            str(Path.home() / "Desktop"),
            "Map files (*.dxf *.dwg *.ktd *.KTD *.hpktd *.ktdp *.xyz *.csv *.txt);;All files (*.*)",
        )
        for name in names:
            path = Path(name)
            try:
                shapes = load_shapes(path)
                self.add_layer(Layer(path.name, path, shapes))
            except Exception as exc:
                QMessageBox.warning(self, APP_TITLE, f"Could not load {path.name}:\n{exc}")
        if names:
            self.fit_extents()

    def convert_dwg_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(self, "Convert DWG to DXF", str(Path.home() / "Desktop"), "DWG files (*.dwg *.DWG)")
        converted: list[Path] = []
        for name in names:
            path = Path(name)
            try:
                converted.append(convert_dwg_to_dxf_with_oda(path))
            except Exception as exc:
                QMessageBox.warning(self, APP_TITLE, f"Could not convert {path.name}:\n{exc}")
        if converted:
            answer = QMessageBox.question(self, APP_TITLE, f"Converted {len(converted)} file(s). Load converted DXF layer(s) now?")
            if answer == QMessageBox.StandardButton.Yes:
                for path in converted:
                    try:
                        self.add_layer(Layer(path.name, path, load_dxf_shapes(path)))
                    except Exception as exc:
                        QMessageBox.warning(self, APP_TITLE, f"Converted but could not load {path.name}:\n{exc}")
                self.fit_extents()

    def layer_visibility_changed(self, item: QListWidgetItem) -> None:
        row = self.layer_list.row(item)
        layer = self.layer_for_list_row(row)
        if layer is not None:
            layer.visible = item.checkState() == Qt.CheckState.Checked
            for map_item in list(layer.group or []):
                try:
                    map_item.setVisible(layer.visible)
                except Exception:
                    pass
            self.save_background_state()

    def unload_selected(self) -> None:
        row = self.layer_list.currentRow()
        layer = self.layer_for_list_row(row)
        if layer is None:
            return
        self.layers.remove(layer)
        self.view.remove_layer_items(layer.group)
        self.layer_list.takeItem(row)
        self.save_background_state()

    def clear_layers(self) -> None:
        removable = [layer for layer in self.layers if not layer.hidden]
        self.view.clear_background_items([list(layer.group or []) for layer in removable])
        self.layers = [layer for layer in self.layers if layer.hidden]
        self.layer_list.clear()
        self.refresh_live_survey_layer()
        self.save_background_state()

    def fit_extents(self) -> None:
        self.push_view_history()
        xs: list[float] = []
        ys: list[float] = []
        for layer in self.layers:
            if not layer.visible:
                continue
            for shape in layer.shapes:
                for x, y in shape.points:
                    xs.append(float(x))
                    ys.append(float(y))
        xs.extend([x for x, _y in self.live_track_points])
        ys.extend([y for _x, y in self.live_track_points])
        xs.extend([x for x, _y, _d in self.live_soundings])
        ys.extend([y for _x, y, _d in self.live_soundings])
        if xs and ys:
            self.view.fit_bounds((min(xs), min(ys), max(xs), max(ys)))
        else:
            self.view.fit_bounds(None)
        self.update_status_from_view()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    win = MainWindow()
    win.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


def river_bank_border(east_segments, west_segments):
    """Join contiguous bank segments, orient end caps, reject invalid polygons."""
    import math
    from shapely.geometry import LineString, Polygon
    from shapely.ops import linemerge
    from shapely.validation import explain_validity
    def bank(segments, name):
        cleaned=[]
        for segment in segments:
            points=[]
            for xy in segment:
                p=tuple(float(v) for v in xy[:2])
                if len(p)!=2 or not all(math.isfinite(v) for v in p):raise ValueError(name+' contains invalid coordinates.')
                if not points or p!=points[-1]:points.append(p)
            if len(points)>=2:cleaned.append(points)
        if not cleaned:raise ValueError('Select at least one line for the '+name+'.')
        line=LineString(cleaned[0]) if len(cleaned)==1 else linemerge(cleaned)
        if line.geom_type!='LineString':raise ValueError(name+' must form one connected, unbranched line. Select adjoining segments with matching endpoints.')
        if line.is_ring or not line.is_simple:raise ValueError(name+' must be an open line without crossings.')
        return list(line.coords)
    east=bank(east_segments,'east bank');west=bank(west_segments,'west bank')
    candidates=[]
    for side in (west,list(reversed(west))):
        ring=east+side+[east[0]];polygon=Polygon(ring)
        if polygon.is_valid and polygon.area>0:
            distance=math.dist(east[-1],side[0])+math.dist(side[-1],east[0])
            candidates.append((distance,ring,polygon.area))
    if not candidates:raise ValueError('The banks cross, overlap, or their end connections create an invalid border. Check the selected lines.')
    _,ring,area=min(candidates,key=lambda item:item[0])
    return ring,area


def show_river_bank_border(owner, apply_border):
    from PyQt6 import QtWidgets as W, QtCore as C
    from concurrent.futures import ThreadPoolExecutor
    dialog=W.QDialog(owner);dialog.setWindowTitle('New border from east and west river banks');dialog.resize(1000,700)
    owner.river_bank_border_dialog=dialog
    layout=W.QVBoxLayout(dialog)
    hint=W.QLabel('Select the DXF lines for each bank (Ctrl/Shift for multiple adjoining segments). Both banks must use the map coordinate system. Endpoints are joined automatically; preview before applying.')
    hint.setWordWrap(True);layout.addWidget(hint)
    row=W.QHBoxLayout();layout.addLayout(row);lists=[];sources=[[],[]];jobs={}
    executor=ThreadPoolExecutor(max_workers=2)
    status=W.QLabel('Load a DXF for each bank, or choose lines from loaded DXF backgrounds.');status.setWordWrap(True)
    preview=pg.PlotWidget();preview.setBackground('w');preview.setAspectLocked(True);preview.showGrid(x=True,y=True);preview.setLabel('bottom','Easting');preview.setLabel('left','Northing')
    buttons=W.QHBoxLayout();preview_btn=W.QPushButton('Preview border');apply_btn=W.QPushButton('Use this border');apply_btn.setEnabled(False)
    cancel=W.QPushButton('Close');buttons.addWidget(preview_btn);buttons.addWidget(apply_btn);buttons.addWidget(cancel)
    current={'points':None}
    def invalidate():current['points']=None;apply_btn.setEnabled(False);preview.clear()
    def populate(index,shapes):
        invalidate();sources[index]=[s for s in shapes if s.kind=='line' and len(s.points)>=2]
        box=lists[index];box.clear()
        for i,shape in enumerate(sources[index]):
            a,b=shape.points[0],shape.points[-1]
            box.addItem(f'{i+1}: {shape.label or "Line"} | {len(shape.points)} vertices | {a[0]:.3f}, {a[1]:.3f} to {b[0]:.3f}, {b[1]:.3f}')
        if box.count()==1:box.item(0).setSelected(True)
        status.setText(f'Loaded {box.count()} candidate lines. Select only the required bank segments.')
    def load(index):
        if index in jobs:return
        name,_=W.QFileDialog.getOpenFileName(dialog,'Load river-bank DXF',str(owner.project_folder),'DXF (*.dxf *.DXF)')
        if name:
            invalidate();jobs[index]=executor.submit(load_dxf_shapes,Path(name));status.setText('Reading DXF…')
    def poll():
        for index,future in list(jobs.items()):
            if future.done():
                del jobs[index]
                try:populate(index,future.result())
                except Exception as exc:status.setText('DXF load failed: '+str(exc))
    for index,title in enumerate(('East river bank','West river bank')):
        group=W.QGroupBox(title);column=W.QVBoxLayout(group);row.addWidget(group)
        load_btn=W.QPushButton('Load DXF…');loaded_btn=W.QPushButton('Use loaded DXF backgrounds');column.addWidget(load_btn);column.addWidget(loaded_btn)
        box=W.QListWidget();box.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection);column.addWidget(box);lists.append(box)
        box.itemSelectionChanged.connect(invalidate)
        load_btn.clicked.connect(lambda checked=False,i=index:load(i))
        loaded_btn.clicked.connect(lambda checked=False,i=index:populate(i,[s for layer in owner.layers if layer.path and Path(layer.path).suffix.lower() in ('.dxf','.hpdxf') for s in layer.shapes]))
    layout.addWidget(preview,1);layout.addWidget(status);layout.addLayout(buttons)
    def make_preview():
        try:
            if jobs:raise ValueError('Wait for DXF loading to finish.')
            selected=[[sources[i][lists[i].row(item)].points for item in lists[i].selectedItems()] for i in range(2)]
            points,area=river_bank_border(*selected);preview.clear()
            for segments,color in zip(selected,('#2563eb','#16a34a')):
                for pts in segments:preview.plot([p[0] for p in pts],[p[1] for p in pts],pen=pg.mkPen(color,width=3))
            preview.plot([p[0] for p in points],[p[1] for p in points],pen=pg.mkPen('#dc2626',width=1))
            current['points']=points;apply_btn.setEnabled(True)
            status.setText(f'Valid closed border: {len(points)-1} vertices; area {area:,.3f} square coordinate units. Use this border replaces the active survey border. Save it using Save Border in the border editor.')
        except Exception as exc:invalidate();status.setText(str(exc))
    def apply():
        if current['points'] is not None:
            apply_border(list(current['points']));dialog.accept()
    preview_btn.clicked.connect(make_preview);apply_btn.clicked.connect(apply);cancel.clicked.connect(dialog.reject)
    timer=C.QTimer(dialog);timer.timeout.connect(poll);timer.start(100)
    def finish(*args):timer.stop();executor.shutdown(wait=False,cancel_futures=True)
    dialog.finished.connect(finish)
    dialog._bank_lists=lists;dialog._populate_bank=populate;dialog._preview_border=make_preview;dialog._apply_border=apply;dialog._bank_status=status
    dialog.show();return dialog
