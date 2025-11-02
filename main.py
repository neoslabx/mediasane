#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Import libraries
import hashlib
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid

# Import packages
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QDialogButtonBox
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QHeaderView
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QMenuBar
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QProgressBar
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QTableWidget
from PyQt6.QtWidgets import QTableWidgetItem
from PyQt6.QtWidgets import QTabWidget
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

# Define 'VERSION'
VERSION = "v1.1.3"

# Define 'APPNAME'
APPNAME = "MediaSane"

# Define 'WEBSITEURL'
WEBSITEURL = "https://neoslab.com/"

# Define 'CONFIGPATH'
CONFIGPATH = Path.home()/".config"/"mediasane"

# Define 'CONFIGFILE'
CONFIGFILE = CONFIGPATH/"config"

# Define 'ALLOWIMG'
ALLOWIMG = set("jpg jpeg png gif tif tiff bmp webp heic heif".split())

# Define 'ALLOWVID'
ALLOWVID = set("mp4 mov m4v avi mkv 3gp webm".split())


# Class 'SysUtils'
class SysUtils:
    """Low-level utilities for filesystem and metadata tasks.
    Handles file extension parsing, EXIF/mtime date extraction, and hashing.
    Designed as stateless helpers; functions may access the filesystem."""

    # Define 'lowerext'
    @staticmethod
    def lowerext(p: Path) -> str:
        """Return the lowercase file extension for a Path.
        Strips the leading dot and normalizes case for comparisons.
        Used to decide media classification and output naming."""
        ext = p.suffix[1:]
        return ext.lower()

    # Define 'cmdexists'
    @staticmethod
    def cmdexists(cmd: str) -> bool:
        """Check whether an executable is available in PATH.
        Relies on shutil.which to probe the current environment.
        Useful to conditionally use external tools like exiftool."""
        return shutil.which(cmd) is not None

    # Define 'classify'
    @staticmethod
    def classify(extlc: str, prefs: "ExecPrefs") -> str:
        """Map a lowercase extension to the appropriate naming prefix.
        Returns image or video prefix based on allowed extension sets.
        Empty string means unsupported or unknown media type."""
        if extlc in ALLOWIMG:
            return prefs.imgprefix
        if extlc in ALLOWVID:
            return prefs.vidprefix
        return ""

    # Define 'exifdate'
    @staticmethod
    def exifdate(path: Path, timeouts: int = 10) -> str:
        """Extract a YYYYMMDD date from EXIF/metadata using exiftool.
        Tries multiple date tags and formats the first valid date found.
        Returns empty string on failure, timeout, or if exiftool is absent."""
        if not SysUtils.cmdexists("exiftool"):
            return ""
        try:
            proc = subprocess.run(
                [
                    "exiftool", "-s", "-S", "-q", "-q", "-m",
                    "-api", "LargeFileSupport=1", "-fast2",
                    "-d", "%Y%m%d",
                    "-DateTimeOriginal", "-CreateDate", "-MediaCreateDate", "-FileModifyDate",
                    "--", str(path)
                ],
                capture_output=True, text=True, timeout=timeouts
            )
            if proc.returncode == 0:
                line = (proc.stdout.splitlines() or [""])[0].strip()
                if len(line) == 8 and line.isdigit():
                    return line
            return ""
        except subprocess.TimeoutExpired:
            return ""
        except (OSError, subprocess.SubprocessError, ValueError):
            return ""

    # Define 'epochdate'
    @staticmethod
    def epochdate(epoch: float) -> str:
        """Format an epoch timestamp into YYYYMMDD local date.
        Uses datetime.fromtimestamp for local time conversion.
        Returns a compact 8-digit date string suitable for filenames."""
        return datetime.fromtimestamp(epoch).strftime("%Y%m%d")

    # Define 'datename'
    @staticmethod
    def datename(name: str) -> str:
        """Parse an 8-digit leading date (YYYYMMDD) from a name.
        If the name does not start with an 8-digit date, return empty.
        Helpful to preserve existing date-based names when present."""
        if len(name) >= 8 and name[:8].isdigit():
            return name[:8]
        return ""

    # Define 'datetime'
    @staticmethod
    def datetime(path: Path) -> str:
        """Return file modification time as YYYYMMDD.
        Uses os.stat to read mtime and formats it compactly.
        Returns empty string on errors or inaccessible files."""
        try:
            return SysUtils.epochdate(path.stat().st_mtime)
        except (OSError, ValueError):
            return ""

    # Define 'datetoday'
    @staticmethod
    def datetoday() -> str:
        """Return today's local date as YYYYMMDD.
        Acts as a last-resort fallback when metadata is missing.
        Ensures deterministic naming even without file dates."""
        return datetime.now().strftime("%Y%m%d")

    # Define 'hashkey'
    @staticmethod
    def hashkey(path: Path, hash_budget_s: int = 60, quick_prefix_bytes: int = 1024 * 1024) -> Tuple[str, bool]:
        """Compute a content hash key with a time budget.
        Falls back to a weak key (size@mtime) if budget exceeded or I/O fails.
        Also includes a quick blake2b of the file prefix for robustness."""
        try:
            st = path.stat()
            size = st.st_size
            mtime = int(st.st_mtime)
        except OSError:
            size = 0
            mtime = 0

        t0 = time.monotonic()
        sha = hashlib.sha256()
        timeout = False
        try:
            with path.open("rb", buffering=1024 * 1024) as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    sha.update(chunk)
                    if (time.monotonic() - t0) > hash_budget_s:
                        timeout = True
                        break
        except (OSError, IOError):
            timeout = True

        if timeout:
            return f"weak-{size}@{mtime}", True

        quick = b""
        try:
            with path.open("rb") as fh2:
                quick = fh2.read(quick_prefix_bytes)
        except (OSError, IOError):
            quick = b""
        bl = hashlib.blake2b(quick).hexdigest()

        return f"sha256:{sha.hexdigest()}|b2b1M:{bl}", False

    # Define 'safemove'
    @staticmethod
    def safemove(src: Path, dst: Path) -> bool:
        """Move a file, falling back to copy2+unlink on cross-device errors.
        Ensures destination directories exist before moving/copying.
        Returns True on success and False if all strategies fail."""
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            return True
        except OSError:
            try:
                shutil.copy2(src, dst)
                src.unlink(missing_ok=True)
                return True
            except (OSError, IOError):
                return False


# Class 'ExecPrefs'
@dataclass
class ExecPrefs:
    """User-adjustable naming preferences for output files.
    Stores prefixes for images and videos used during renaming.
    Serializable to/from dict for config persistence."""

    # Define 'imgprefix'
    imgprefix: str = "IMG-"

    # Define 'vidprefix'
    vidprefix: str = "VID-"

    # Function 'todict'
    def todict(self) -> Dict[str, str]:
        """Serialize preferences to a plain dict.
        Intended for lightweight config storage and merging.
        Keys mirror dataclass fields for simplicity."""
        return {"imgprefix": self.imgprefix, "vidprefix": self.vidprefix}

    # Function 'fromdict'
    @staticmethod
    def fromdict(d: Dict[str, str]) -> "ExecPrefs":
        """Create an ExecPrefs instance from a dict.
        Unknown keys are ignored; defaults are applied as needed.
        Ensures safe loading from partially filled configs."""
        return ExecPrefs(
            imgprefix=str(d.get("imgprefix", "IMG-")),
            vidprefix=str(d.get("vidprefix", "VID-")),
        )


# Class 'ConfigManager'
class ConfigManager:
    """Tiny helper for reading/writing the app config file.
    Stores prefixes and last used directories under ~/.config/mediasane.
    Tolerant to missing files and I/O/permission issues."""

    # Function 'load'
    @staticmethod
    def load() -> Dict[str, str]:
        """Load key=value pairs from the config file.
        Ignores blank lines, comments, and malformed entries.
        Returns a dict with any discovered settings."""
        data: Dict[str, str] = {}
        try:
            if CONFIGFILE.is_file():
                for line in CONFIGFILE.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip()
        except (OSError, UnicodeDecodeError):
            pass
        return data

    # Function 'save'
    @staticmethod
    def save(prefs: ExecPrefs, other: Dict[str, str]):
        """Write preferences and auxiliary fields to the config file.
        Creates the config directory if necessary and ignores errors.
        Values are persisted as simple key=value lines."""
        try:
            CONFIGPATH.mkdir(parents=True, exist_ok=True)
            lines = [f"imgprefix={prefs.imgprefix}", f"vidprefix={prefs.vidprefix}"]
            for k, v in other.items():
                lines.append(f"{k}={v}")
            CONFIGFILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except (OSError, PermissionError):
            pass


# Class 'ExecOptions'
@dataclass
class ExecOptions:
    # Define 'srcdir'
    srcdir: str = ""

    # Define 'outdir'
    outdir: str = ""

    # Define 'keepdupes'
    keepdupes: bool = False

    # Define 'dryrun'
    dryrun: bool = False

    # Define 'metatimeout'
    metatimeout: int = 10

    # Define 'hashtimeout'
    hashtimeout: int = 60


# Class 'MediaRenamer'
class MediaRenamer:
    """Core planner/executor for scanning, deduping, and renaming.
    Enumerates eligible media, derives dates, and plans moves safely.
    Pushes final results to a queue for GUI consumption."""

    # Define '__init__'
    def __init__(self, opts: ExecOptions, prefs: ExecPrefs, rowsink: queue.Queue):
        """Initialize with execution options, prefs, and a result queue.
        Prepares state for duplicate detection and rename planning.
        No filesystem work occurs until run() is invoked."""
        self.opts = opts
        self.prefs = prefs
        self.rowsink = rowsink
        self.stopflag = False

        self.hashseen: Dict[str, Path] = {}
        self.actdupes: List[Tuple[Path, str, Optional[Path]]] = []
        self.actrenames: List[Tuple[Path, Path, Path]] = []
        self.results: List[Tuple[str, str]] = []

    # Define 'cancel'
    def cancel(self):
        """Signal the worker to stop at the next safe checkpoint.
        Sets an internal flag polled by long-running loops.
        Raises in checkstop() to unwind promptly."""
        self.stopflag = True

    # Define 'checkstop'
    def checkstop(self):
        """Abort processing if a stop was requested.
        Intended to be called frequently within loops.
        Raises RuntimeError to break out of the workflow."""
        if self.stopflag:
            raise RuntimeError("Cancelled")

    # Define 'enumfiles'
    @staticmethod
    def enumfiles(root: Path) -> List[Path]:
        """Walk a directory tree and collect supported media files.
        Skips the internal .duplicates directory to avoid recursion.
        Returns a list of Paths for images and videos only."""
        files: List[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != ".duplicates"]
            for fname in filenames:
                file_path = Path(dirpath) / fname
                ext = SysUtils.lowerext(file_path)
                if ext in ALLOWIMG or ext in ALLOWVID:
                    files.append(file_path)
        return files

    # Define 'resolvedate'
    def resolvedate(self, path_in: Path) -> str:
        """Resolve a best-fit date for a media file as YYYYMMDD.
        Tries name prefix, then EXIF/metadata, then mtime, then today.
        Provides a deterministic fallback chain for robust naming."""
        d = SysUtils.datename(path_in.stem)
        if not d:
            d = SysUtils.exifdate(path_in, timeouts=self.opts.metatimeout)
        if not d:
            d = SysUtils.datetime(path_in)
        if not d:
            d = SysUtils.datetoday()
        return d

    # Define 'plan'
    def plan(self):
        """Plan duplicate handling and final rename destinations.
        Computes content hash keys, tracks dupes, and sequences files.
        Populates action lists and a readable results summary."""
        src = Path(self.opts.srcdir)
        out = Path(self.opts.outdir) if self.opts.outdir else src

        candidates: List[Tuple[str, float, str, Path, str]] = []
        files = self.enumfiles(src)

        for fpath in files:
            self.checkstop()
            extlc = SysUtils.lowerext(fpath)
            prefix = SysUtils.classify(extlc, self.prefs)
            if not prefix:
                self.results.append((str(fpath), "(unsupported)"))
                continue

            hk, ho = SysUtils.hashkey(fpath, hash_budget_s=self.opts.hashtimeout)
            if hk in self.hashseen:
                if self.opts.keepdupes:
                    dupdir = out / ".duplicates"
                    base = fpath.name
                    dest = dupdir / base
                    n = 0
                    while dest.exists():
                        n += 1
                        dest = dupdir / f"{base}.{n}"
                    self.actdupes.append((fpath, "move", dest))
                    self.results.append((str(fpath), str(dest)))
                else:
                    self.actdupes.append((fpath, "delete", None))
                    self.results.append((str(fpath), "(deleted)"))
                continue
            else:
                self.hashseen[hk] = fpath

            d = self.resolvedate(fpath)
            mt = fpath.stat().st_mtime if fpath.exists() else 0.0
            candidates.append((d, mt, fpath.name, fpath, prefix))

        candidates.sort(key=lambda t: (t[0], t[1], t[2]))
        countdate: Dict[str, int] = {}

        for (d, _mt, _nm, fpath, prefix) in candidates:
            self.checkstop()
            seq = countdate.get(d, 0) + 1
            countdate[d] = seq
            enddst = (out / f"{prefix}{d}-{seq:05d}.{SysUtils.lowerext(fpath)}")
            tmpdst = enddst.with_suffix(enddst.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
            self.actrenames.append((fpath, tmpdst, enddst))
            self.results.append((str(fpath), str(enddst)))

    # Define 'execute'
    def execute(self):
        """Execute planned duplicate handling and renames.
        Performs safe moves to temporary paths before finalization.
        Emits a row (old,new) to the queue for each processed file."""
        # Announce total number of files that will be renamed
        total_renames = len(self.actrenames)
        self.rowsink.put(("__TOTAL__", str(total_renames)))

        if self.opts.dryrun:
            for old, new in self.results:
                self.checkstop()
                self.rowsink.put((old, new))
            return

        for srcpath, action, dest_path in self.actdupes:
            self.checkstop()
            if action == "move":
                assert dest_path is not None
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                SysUtils.safemove(srcpath, dest_path)
                self.rowsink.put((str(srcpath), str(dest_path)))
            elif action == "delete":
                try:
                    srcpath.unlink(missing_ok=True)
                except (OSError, PermissionError):
                    pass
                self.rowsink.put((str(srcpath), "(deleted)"))

        processed = 0
        for srcpath, tmpdst, final in self.actrenames:
            self.checkstop()

            tmpdst.parent.mkdir(parents=True, exist_ok=True)
            if srcpath.exists():
                SysUtils.safemove(srcpath, tmpdst)

            if tmpdst.exists():
                cand = final
                i = 1
                while cand.exists():
                    cand = final.with_name(final.stem + f"_{i}" + final.suffix)
                    i += 1
                try:
                    tmpdst.rename(cand)
                except OSError:
                    try:
                        shutil.copy2(tmpdst, cand)
                        tmpdst.unlink(missing_ok=True)
                    except (OSError, IOError):
                        pass

                self.rowsink.put((str(srcpath), str(cand)))
                processed += 1
                self.rowsink.put(("__COUNT__", f"{processed}"))

    # Function 'streamrun'
    def streamrun(self):
        """Stream files one-by-one with progressive UI updates.
        Enumerates, hashes, decides destination, moves, and reports.
        Preserves date-group numbering that restarts at each date."""
        src = Path(self.opts.srcdir)
        out = Path(self.opts.outdir) if self.opts.outdir else src

        files = self.enumfiles(src)
        self.rowsink.put(("__TOTAL__", str(len(files))))
        sortable: List[Tuple[str, float, str, Path, str]] = []
        for fpath in files:
            self.checkstop()
            extlc = SysUtils.lowerext(fpath)
            prefix = SysUtils.classify(extlc, self.prefs)
            if not prefix:
                self.rowsink.put((str(fpath), "(unsupported)"))
                continue
            d = self.resolvedate(fpath)
            mt = fpath.stat().st_mtime if fpath.exists() else 0.0
            sortable.append((d, mt, fpath.name, fpath, prefix))

        sortable.sort(key=lambda t: (t[0], t[1], t[2]))
        countdate: Dict[str, int] = {}
        processed = 0

        for (d, _mt, _nm, fpath, prefix) in sortable:
            self.checkstop()

            hk, ho = SysUtils.hashkey(fpath, hash_budget_s=self.opts.hashtimeout)
            if hk in self.hashseen:
                if self.opts.keepdupes:
                    dupdir = out / ".duplicates"
                    dest = dupdir / fpath.name
                    n = 0
                    while dest.exists():
                        n += 1
                        dest = dupdir / f"{fpath.name}.{n}"
                    dupdir.mkdir(parents=True, exist_ok=True)
                    SysUtils.safemove(fpath, dest)
                    self.rowsink.put((str(fpath), str(dest)))
                else:
                    try:
                        fpath.unlink(missing_ok=True)
                    except (OSError, PermissionError):
                        pass
                    self.rowsink.put((str(fpath), "(deleted)"))
                processed += 1
                self.rowsink.put(("__COUNT__", f"{processed}"))
                continue
            else:
                self.hashseen[hk] = fpath

            seq = countdate.get(d, 0) + 1
            countdate[d] = seq

            enddst = (out / f"{prefix}{d}-{seq:05d}.{SysUtils.lowerext(fpath)}")
            tmpdst = enddst.with_suffix(enddst.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
            tmpdst.parent.mkdir(parents=True, exist_ok=True)

            if not self.opts.dryrun:
                if fpath.exists():
                    SysUtils.safemove(fpath, tmpdst)

                cand = enddst
                i = 1
                while cand.exists():
                    cand = enddst.with_name(enddst.stem + f"_{i}" + enddst.suffix)
                    i += 1
                try:
                    tmpdst.rename(cand)
                except OSError:
                    try:
                        shutil.copy2(tmpdst, cand)
                        tmpdst.unlink(missing_ok=True)
                    except (OSError, IOError):
                        pass
                self.rowsink.put((str(fpath), str(cand)))
            else:
                self.rowsink.put((str(fpath), str(enddst)))

            processed += 1
            self.rowsink.put(("__COUNT__", f"{processed}"))

    # Define 'run'
    def run(self):
        """Run the full pipeline: plan then execute.
        Intended to be called from a worker thread in the GUI.
        Raises on cancellation and reports results progressively."""
        self.streamrun()


# Class 'DialogPrefs'
class DialogPrefs(QDialog):
    """Preferences dialog for naming settings.
    Allows users to edit image/video prefixes with validation.
    Changes are returned as an ExecPrefs copy on accept."""

    # Define '__init__'
    def __init__(self, parent: QWidget, prefs: ExecPrefs):
        """Build the tabbed preferences dialog UI.
        Initializes fields with current preference values.
        OK/Cancel buttons manage accept/reject lifecycle."""
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.resize(520, 240)

        self.prefs = ExecPrefs.fromdict(prefs.todict())
        tabs = QTabWidget(self)

        wnaming = QWidget()
        g = QFormLayout(wnaming)
        self.editimg = QLineEdit(self.prefs.imgprefix)
        self.editvid = QLineEdit(self.prefs.vidprefix)
        g.addRow(QLabel("Image prefix:"), self.editimg)
        g.addRow(QLabel("Video prefix:"), self.editvid)
        tabs.addTab(wnaming, "Naming")

        btns = QDialogButtonBox(parent=self)
        btnok = QPushButton("OK", self)
        btncancel = QPushButton("Cancel", self)
        btns.addButton(btnok, QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton(btncancel, QDialogButtonBox.ButtonRole.RejectRole)
        btnok.clicked.connect(self.accept)
        btncancel.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(btns)

    # Define 'values'
    def values(self) -> ExecPrefs:
        """Return sanitized ExecPrefs based on user input.
        Falls back to defaults when fields are left blank.
        Intended to be called after dialog acceptance."""
        self.prefs.imgprefix = self.editimg.text().strip() or "IMG-"
        self.prefs.vidprefix = self.editvid.text().strip() or "VID-"
        return self.prefs


# Custom 'DialogAbout'
class DialogAbout(QDialog):
    """Simple About dialog displaying branding and links.
    Shows app name, version, website and a short description.
    Uses a bundled or system icon when available."""

    # Function '__init__'
    def __init__(self, parent: Optional[QWidget], version: str, website: str):
        """Construct the About dialog UI and load logo.
        Searches multiple candidate paths for an icon/pixmap.
        Populates labels and wires the close button."""
        super().__init__(parent)
        self.setWindowTitle("About Mediasane")
        self.setModal(True)
        self.setMinimumSize(520, 360)

        logolabel = QLabel()
        logolabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cpaths = [
            Path("/usr/share/pixmaps/mediasane.png"),
            Path(__file__).resolve().parent / "logo.png",
            CONFIGPATH / "logo.png",
        ]
        pixmap: Optional[QPixmap] = None
        for cpath in cpaths:
            if cpath.is_file():
                tmp = QPixmap(str(cpath))
                if not tmp.isNull():
                    pixmap = tmp
                    break

        if pixmap:
            logolabel.setPixmap(
                pixmap.scaled(
                    128, 128,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            )
        else:
            logolabel.setText("🧹")

        title = QLabel(f"<b>Mediasane</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px;")

        ver = QLabel(f"Version: {version}")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)

        link = QLabel(f'<a href="{website}">{website}</a>')
        link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link.setTextFormat(Qt.TextFormat.RichText)
        link.setOpenExternalLinks(True)

        msg = QLabel("Media organizer and renamer\nSort by date, de-duplicate, and safely move photos/videos.")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #aaa;")

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, parent=self)
        btns.accepted.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(12)
        lay.addWidget(logolabel)
        lay.addWidget(title)
        lay.addWidget(ver)
        lay.addWidget(msg)
        lay.addWidget(link)
        lay.addStretch(1)
        lay.addWidget(btns)


# Class 'MediaSane'
class MediaSane(QWidget):
    """Main PyQt GUI for MediaSane's rename workflow.
    Provides source/output selection, options, and progress.
    Streams results into a table while a worker thread runs."""

    # Define '__init__'
    def __init__(self):
        """Create the main window, menus, widgets, and timers.
        Loads saved config, initializes preferences, and wiring.
        Sets up a background queue for incremental row updates."""
        super().__init__()
        self.setWindowTitle(f"{APPNAME} {VERSION} - Media Rename GUI")
        self.resize(1000, 700)

        self.workerthread: Optional[threading.Thread] = None
        self.worker: Optional[MediaRenamer] = None
        self.rowqueue: "queue.Queue[Tuple[str,str]]" = queue.Queue()

        # Progress counter state
        self.total_files: int = 0
        self.namecount: int = 0
        self.rowindex: Dict[str, int] = {}

        menubar = QMenuBar(self)
        mfile = menubar.addMenu("File")
        actquit = QAction("Quit", self)
        actquit.triggered.connect(QApplication.quit)
        mfile.addAction(actquit)

        medit = menubar.addMenu("Edit")
        actprefs = QAction("Preferences", self)
        actprefs.triggered.connect(self.onprefs)
        medit.addAction(actprefs)

        mhelp = menubar.addMenu("Help")
        actabout = QAction("About", self)
        actabout.triggered.connect(self.onabout)
        mhelp.addAction(actabout)

        self.srcedit = QLineEdit()
        self.srcbtn = QPushButton("Browse…")
        self.srcbtn.clicked.connect(lambda: self.pickdir(self.srcedit))
        self.outedit = QLineEdit()
        self.outbtn = QPushButton("Browse…")
        self.outbtn.clicked.connect(lambda: self.pickdir(self.outedit))

        srcrow = QHBoxLayout()
        srcrow.addWidget(QLabel("Source:"))
        srcrow.addWidget(self.srcedit, 1)
        srcrow.addWidget(self.srcbtn)

        outrow = QHBoxLayout()
        outrow.addWidget(QLabel("Output:"))
        outrow.addWidget(self.outedit, 1)
        outrow.addWidget(self.outbtn)

        self.checkdupes = QCheckBox("Keep duplicates (move to .duplicates)")
        optrow = QHBoxLayout()
        optrow.addWidget(self.checkdupes)
        optrow.addStretch()

        self.btndry = QPushButton("Dry-Run")
        self.btnrun = QPushButton("Run")
        self.btnstop = QPushButton("Stop")
        self.btnstop.setEnabled(False)
        self.btndry.clicked.connect(lambda: self.onrun(dry=True))
        self.btnrun.clicked.connect(lambda: self.onrun(dry=False))
        self.btnstop.clicked.connect(self.onstop)

        btns = QHBoxLayout()
        btns.addWidget(self.btndry)
        btns.addWidget(self.btnrun)
        btns.addWidget(self.btnstop)
        btns.addStretch()

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Original Path", "New Path / Result"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(True)

        root = QVBoxLayout()
        root.setMenuBar(menubar)
        root.addLayout(srcrow)
        root.addLayout(outrow)
        root.addLayout(optrow)
        root.addLayout(btns)
        root.addWidget(self.table, 1)
        root.addWidget(self.progress)
        self.setLayout(root)

        # Floating counter
        self.counterbox = QWidget(self)
        self.counterbox.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        v = QVBoxLayout(self.counterbox)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.countertitle = QLabel("Files", self.counterbox)
        self.countertitle.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.countertitle.setStyleSheet("font-weight: 600;")
        self.countervalue = QLabel("0/0", self.counterbox)
        self.countervalue.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.countervalue.setStyleSheet("font-weight: 600;")
        v.addWidget(self.countertitle)
        v.addWidget(self.countervalue)
        self.counterbox.adjustSize()
        self.installEventFilter(self)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.flushrows)
        self.timer.start(50)

        # Load prefs only
        cfg = ConfigManager.load()
        self.prefs = ExecPrefs.fromdict(cfg)
        self.srcedit.setText("")
        self.outedit.setText("")

        # If user pastes a path manually
        self.srcedit.editingFinished.connect(self.try_populate_from_text)

    # Function 'try_populate_from_text'
    def try_populate_from_text(self):
        """Populate table when user types a valid source path.
        Triggered when the Source field editing is finished.
        Avoids needing to re-open the directory dialog."""
        srcpath = self.srcedit.text().strip()
        if srcpath and Path(srcpath).is_dir():
            self.populate_table_with_dir(srcpath)

    # Function 'ensureposition'
    def ensureposition(self):
        """Reposition the floating counter widget.
        Places it at the top-right, just under the Output row.
        Called on resize/show events to keep it aligned."""
        right_margin = 10
        top_offset = self.outedit.geometry().bottom() + 6
        x = self.width() - self.counterbox.width() - right_margin
        y = top_offset
        self.counterbox.move(max(0, x), max(0, y))
        self.counterbox.raise_()

    # Function 'eventFilter'
    def eventFilter(self, obj, ev: QEvent):
        """Qt event filter for resize/show events.
        Keeps the counter box aligned to the top-right corner.
        Lightweight and avoids extra layout lines."""
        if obj is self and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.ensureposition()
        return super().eventFilter(obj, ev)

    # Function 'populate_table_with_dir'
    def populate_table_with_dir(self, directory: str):
        """Populate the table with files from a directory.
        Clears previous rows and lists supported media immediately.
        Initializes the counter to 0 / total files detected."""
        self.table.setRowCount(0)
        self.rowindex.clear()
        try:
            paths = MediaRenamer.enumfiles(Path(directory))
        except (OSError, PermissionError):
            paths = []
        paths.sort(key=lambda p: str(p))
        for fpath in paths:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(str(fpath)))
            self.table.setItem(r, 1, QTableWidgetItem(""))
            self.rowindex[str(fpath)] = r
        self.total_files = len(paths)
        self.namecount = 0
        self.countervalue.setText(f"{self.namecount}/{self.total_files}")
        self.counterbox.adjustSize()
        self.ensureposition()

    # Function 'pickdir'
    def pickdir(self, edit: QLineEdit):
        """Open a directory chooser and store the chosen path.
        Updates the corresponding line edit and persists paths.
        Ignores errors while saving to the config file."""
        d = QFileDialog.getExistingDirectory(self, "Choose Directory", edit.text() or str(Path.home()))
        if d:
            edit.setText(d)
            other = {
                "last_src": self.srcedit.text().strip(),
                "last_out": self.outedit.text().strip(),
            }
            ConfigManager.save(self.prefs, other)
            if edit is self.srcedit:
                self.populate_table_with_dir(d)

    # Function 'flushrows'
    def flushrows(self):
        """Drain queued result rows into the table widget.
        Called on a timer to keep the UI responsive.
        Stops when the queue is empty for this cycle."""
        try:
            while True:
                old, new = self.rowqueue.get_nowait()
                if old == "__TOTAL__":
                    try:
                        self.total_files = int(new)
                    except ValueError:
                        pass
                    self.namecount = 0
                    self.countervalue.setText(f"{self.namecount}/{self.total_files}")
                    self.counterbox.adjustSize()
                    self.ensureposition()
                    continue

                if old == "__COUNT__":
                    try:
                        self.namecount = int(new)
                    except ValueError:
                        pass
                    self.countervalue.setText(f"{self.namecount}/{self.total_files}")
                    self.counterbox.adjustSize()
                    self.ensureposition()
                    continue

                if old in self.rowindex:
                    r = self.rowindex[old]
                    self.table.setItem(r, 1, QTableWidgetItem(new))
                else:
                    r = self.table.rowCount()
                    self.table.insertRow(r)
                    self.table.setItem(r, 0, QTableWidgetItem(old))
                    self.table.setItem(r, 1, QTableWidgetItem(new))
                    self.rowindex[old] = r
        except queue.Empty:
            pass

    # Function 'onabout'
    def onabout(self):
        """Show the About dialog with version and website.
        Instantiates DialogAbout and blocks until closed.
        Pure UI action; no state changes persisted."""
        dlg = DialogAbout(self, VERSION, WEBSITEURL)
        dlg.exec()

    # Function 'onprefs'
    def onprefs(self):
        """Open the Preferences dialog and apply changes.
        Saves updated prefixes and last used directories.
        Only persists after user acceptance."""
        dlg = DialogPrefs(self, self.prefs)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.prefs = dlg.values()
            other = {
                "last_src": self.srcedit.text().strip(),
                "last_out": self.outedit.text().strip(),
            }
            ConfigManager.save(self.prefs, other)

    # Function 'onstop'
    def onstop(self):
        """Request cancellation of the active worker.
        Disables the Stop button to avoid duplicate clicks.
        Worker periodically checks and exits gracefully."""
        if self.worker:
            self.worker.cancel()
            self.btnstop.setEnabled(False)

    # Function 'onrun'
    def onrun(self, dry: bool):
        """Validate inputs and start a background run.
        Resets the table, toggles UI, and spawns the worker thread.
        Supports dry-run mode to preview planned changes."""
        src = self.srcedit.text().strip()
        out = self.outedit.text().strip()
        if not src:
            QMessageBox.warning(self, "Missing", "Please pick a source directory.")
            return
        if not Path(src).is_dir():
            QMessageBox.critical(self, "Error", "Source directory does not exist.")
            return
        if out and not Path(out).exists():
            try:
                Path(out).mkdir(parents=True, exist_ok=True)
            except (OSError, PermissionError):
                QMessageBox.critical(self, "Error", "Cannot create output directory.")
                return

        if self.table.rowCount() == 0:
            self.populate_table_with_dir(src)

        self.progress.setVisible(True)
        self.btnstop.setEnabled(True)
        self.btnrun.setEnabled(False)
        self.btndry.setEnabled(False)

        opts = ExecOptions(
            srcdir=src,
            outdir=out,
            keepdupes=self.checkdupes.isChecked(),
            dryrun=dry,
            metatimeout=10,
            hashtimeout=60,
        )
        self.worker = MediaRenamer(opts, self.prefs, self.rowqueue)

        # Function 'workload'
        def workload():
            """Run the renamer and restore UI state on finish.
            Catches common runtime errors and reports them as rows.
            Always re-enables buttons and hides the progress bar."""
            try:
                self.worker.run()
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as e:
                self.rowqueue.put(("ERROR", str(e)))
            finally:
                self.progress.setVisible(False)
                self.btnstop.setEnabled(False)
                self.btnrun.setEnabled(True)
                self.btndry.setEnabled(True)

        self.workerthread = threading.Thread(target=workload, daemon=True)
        self.workerthread.start()


# Class 'eee'
class AppEntry:
    """Thin application entry-point wrapper.
    Creates the QApplication and shows the main window.
    Exits with the Qt event loop's return code."""

    # Function 'main'
    @staticmethod
    def main():
        """Launch the Qt application and the main widget.
        Sets up QApplication, constructs MediaSane, and shows it.
        Blocks on app.exec() until the window is closed."""
        app = QApplication(sys.argv)
        win = MediaSane()
        win.show()
        sys.exit(app.exec())


# Callback
if __name__ == "__main__":
    AppEntry.main()
