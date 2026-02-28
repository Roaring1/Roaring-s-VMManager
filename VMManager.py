#!/usr/bin/env python3
"""
KVM Gaming VM Manager - ULTIMATE EDITION
Combines GPU passthrough, SPICE toggle, VM creation, XML editing, and all features

Run with: sudo -E python3 VMManager.py
  (-E preserves DISPLAY/XAUTHORITY so the GUI appears correctly)
"""

APP_VERSION = "2.0.0"

import sys
import os
import json
import subprocess
import re
import time
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import xml.etree.ElementTree as ET

# ──────────────────────────────────────────────────────────────────────────────
# SUDO / DISPLAY FIX
# When running "sudo python3 VMManager.py", sudo strips DISPLAY/XAUTHORITY.
# If they're missing, try to recover them from the calling user's environment.
# Always run as:  sudo -E python3 VMManager.py
# ──────────────────────────────────────────────────────────────────────────────
if not os.environ.get('DISPLAY'):
    # Try to grab from /proc of any running X session
    for pid_dir in Path('/proc').iterdir():
        if pid_dir.name.isdigit():
            env_file = pid_dir / 'environ'
            try:
                env_data = env_file.read_bytes().split(b'\x00')
                for item in env_data:
                    if item.startswith(b'DISPLAY='):
                        os.environ['DISPLAY'] = item.split(b'=', 1)[1].decode()
                        break
                if os.environ.get('DISPLAY'):
                    break
            except Exception:
                pass
    if not os.environ.get('DISPLAY'):
        os.environ['DISPLAY'] = ':0'

# Always use the system libvirt URI (required when running as root)
os.environ.setdefault('LIBVIRT_DEFAULT_URI', 'qemu:///system')

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QTabWidget, QLabel, QPushButton, QComboBox, QSpinBox, QCheckBox,
        QLineEdit, QTextEdit, QListWidget, QListWidgetItem, QTreeWidget,
        QTreeWidgetItem, QGroupBox, QSlider, QProgressBar, QSystemTrayIcon,
        QMenu, QDialog, QDialogButtonBox, QMessageBox, QFileDialog, QStatusBar,
        QScrollArea, QFrame, QSplitter
    )
    from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QSettings
    from PyQt6.QtGui import QFont, QColor, QPalette, QAction, QTextCursor, QIcon
except ImportError:
    print("\n[ERROR] PyQt6 is not installed.")
    print("        Run the installer:  bash install.sh")
    print("        Or manually:        pip install PyQt6\n")
    raise SystemExit(1)

# ──────────────────────────────────────────────────────────────────────────────
# VIRSH HELPER – always passes -c qemu:///system for reliable root operation
# ──────────────────────────────────────────────────────────────────────────────
VIRSH_URI = 'qemu:///system'

def virsh(*args, **kwargs) -> subprocess.CompletedProcess:
    """Run virsh with the system URI. Extra kwargs forwarded to subprocess.run."""
    return subprocess.run(
        ['virsh', '-c', VIRSH_URI] + list(args),
        **kwargs
    )

# ──────────────────────────────────────────────────────────────────────────────
# GPU name database for friendly display
# ──────────────────────────────────────────────────────────────────────────────
GPU_NAMES = {
    "10de:2782": "RTX 4090", "10de:2704": "RTX 4080", "10de:2786": "RTX 4070 Ti",
    "10de:2488": "RTX 3070 Ti", "10de:2484": "RTX 3070", "10de:2489": "RTX 3060 Ti",
    "10de:1e89": "RTX 2060 SUPER", "10de:1f02": "RTX 2060",
    "1002:744c": "RX 7900 XTX", "1002:73bf": "RX 6900 XT",
}

def lookup_gpu_name(device_id: str, fallback: str) -> str:
    return GPU_NAMES.get(device_id, fallback)


class GPUDevice:
    def __init__(self, pci_address: str, vendor: str, model: str, device_id: str,
                 audio_address: Optional[str] = None, vfio_bound: bool = False):
        self.pci_address = pci_address
        self.vendor = vendor
        self.model = model
        self.device_id = device_id
        self.audio_address = audio_address
        self.vfio_bound = vfio_bound
        self.audio_vfio_bound = False
        self.friendly_name = lookup_gpu_name(device_id, model)

    def __str__(self):
        status = "✓ VFIO Ready" if self.is_ready() else "✗ Not Bound to VFIO"
        return f"{self.vendor} {self.friendly_name} [{self.pci_address}] — {status}"

    def is_ready(self) -> bool:
        return self.vfio_bound and (self.audio_address is None or self.audio_vfio_bound)

    @property
    def new_id_string(self) -> str:
        """Return the vendor/device ID formatted for /sys/bus/pci/drivers/vfio-pci/new_id.
        Kernel expects space-separated hex values WITHOUT 0x prefix, e.g. '10de 2782'."""
        # device_id is stored as 'VVVV:DDDD' — convert colon to space
        return self.device_id.replace(':', ' ')


class DeviceInfo:
    def __init__(self, path: str, name: str, device_type: str):
        self.path = path
        self.name = name
        self.device_type = device_type
        self.vid_pid = self._extract_vid_pid()

    def _extract_vid_pid(self) -> str:
        match = re.search(r'usb-([0-9a-f]{4})_([0-9a-f]{4})', self.path.lower())
        return f"{match.group(1)}:{match.group(2)}" if match else "Unknown"


# ──────────────────────────────────────────────────────────────────────────────
# System monitor thread – FIXED CPU calculation (delta between two readings)
# ──────────────────────────────────────────────────────────────────────────────
class SystemMonitor(QThread):
    stats_updated = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.running = True
        self._prev_total = 0
        self._prev_idle = 0

    def _read_cpu(self):
        with open('/proc/stat') as f:
            vals = [int(x) for x in f.readline().split()[1:]]
        total = sum(vals)
        idle = vals[3]
        return total, idle

    def run(self):
        # Prime the previous-reading values
        try:
            self._prev_total, self._prev_idle = self._read_cpu()
        except Exception:
            pass

        while self.running:
            stats = {}
            time.sleep(1)

            # CPU — measure delta since last sample (actual instantaneous usage)
            try:
                total, idle = self._read_cpu()
                d_total = total - self._prev_total
                d_idle  = idle  - self._prev_idle
                self._prev_total, self._prev_idle = total, idle
                stats['cpu'] = 100.0 * (1 - d_idle / d_total) if d_total > 0 else 0.0
            except Exception:
                stats['cpu'] = 0.0

            # Memory
            try:
                with open('/proc/meminfo') as f:
                    lines = f.readlines()
                mem_total = int(lines[0].split()[1])
                mem_avail = int(lines[2].split()[1])
                stats['mem'] = 100.0 * (1 - mem_avail / mem_total)
            except Exception:
                stats['mem'] = 0.0

            self.stats_updated.emit(stats)

    def stop(self):
        self.running = False


# ──────────────────────────────────────────────────────────────────────────────
# GPU detection – FIXED: vfio_bound parsed AFTER driver line (not before)
# ──────────────────────────────────────────────────────────────────────────────
def detect_gpus() -> List[GPUDevice]:
    """Parse lspci -nnk output and return a list of discrete GPUs.

    'Kernel driver in use:' appears on a line AFTER the device description line,
    so we do a single pass tracking the current device and update vfio_bound /
    audio_vfio_bound when we encounter the driver line.
    """
    result = subprocess.run(['lspci', '-nnk'], capture_output=True, text=True)

    gpus: Dict[str, GPUDevice] = {}          # video_pci_addr -> GPUDevice
    pending_audio: Dict[str, str] = {}       # audio_pci_addr -> video_pci_addr
    current_dev: Optional[str] = None

    for line in result.stdout.splitlines():
        # ── New device block starts with its PCI address ──────────────────
        addr_match = re.match(r'^([0-9a-f]{2}:[0-9a-f]{2}\.[0-9])', line)
        if addr_match:
            current_dev = addr_match.group(1)

        if current_dev is None:
            continue

        # ── Driver line: update vfio status for whichever device owns it ──
        if 'Kernel driver in use:' in line:
            driver = line.split(':', 1)[-1].strip()
            is_vfio = (driver == 'vfio-pci')
            if current_dev in gpus:
                gpus[current_dev].vfio_bound = is_vfio
            if current_dev in pending_audio:
                video_addr = pending_audio[current_dev]
                if video_addr in gpus:
                    gpus[video_addr].audio_vfio_bound = is_vfio
            continue

        # ── VGA / Display controller → register as GPU ────────────────────
        if addr_match and ('VGA' in line or 'Display' in line):
            vendor = ('NVIDIA' if 'NVIDIA' in line
                      else 'AMD'    if ('AMD' in line or 'ATI' in line)
                      else 'GPU')
            id_m = re.search(r'\[([0-9a-f]{4}):([0-9a-f]{4})\]', line)
            dev_id = f"{id_m.group(1)}:{id_m.group(2)}" if id_m else "unknown"
            gpus[current_dev] = GPUDevice(
                current_dev, vendor, 'GPU', dev_id, vfio_bound=False
            )
            continue

        # ── GPU audio companion (same bus, .1 function) ───────────────────
        if addr_match and ('Audio' in line or 'Multimedia' in line) \
                and ('NVIDIA' in line or 'AMD' in line or 'ATI' in line):
            # Companion video is at the same bus:slot but function 0
            bus_slot = current_dev.rsplit('.', 1)[0]   # 'BB:SS'
            video_addr = f"{bus_slot}.0"
            if video_addr in gpus:
                gpus[video_addr].audio_address = current_dev
                pending_audio[current_dev] = video_addr

    return list(gpus.values())


# ──────────────────────────────────────────────────────────────────────────────
# XML helper – write to temp file and define (stdin pipe is unreliable)
# ──────────────────────────────────────────────────────────────────────────────
def virsh_define_xml(xml_str: str) -> subprocess.CompletedProcess:
    """Write XML to a temp file and run virsh define on it (more reliable than stdin)."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write(xml_str)
        tmp_path = f.name
    try:
        result = virsh('define', tmp_path, capture_output=True, text=True)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Main GUI
# ──────────────────────────────────────────────────────────────────────────────
class KVMManagerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"KVM VM Manager — ULTIMATE EDITION  v{APP_VERSION}")
        self.setMinimumSize(1100, 750)

        self.settings = QSettings('KVMManager', 'Ultimate')
        self.available_devices: List[DeviceInfo] = []
        self.selected_devices: List[str] = []
        self.available_gpus: List[GPUDevice] = []
        self.selected_gpu: Optional[GPUDevice] = None
        self.vm_running = False

        self.load_settings()
        self.setup_ui()
        self.setup_tray()

        self.monitor = SystemMonitor()
        self.monitor.stats_updated.connect(self.update_stats)
        self.monitor.start()

        self.scan_devices()
        self.scan_gpus()
        self.update_vm_status()

        QTimer.singleShot(0, self.apply_theme)
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_vm_status)
        self.status_timer.start(2000)

    # ──────────────────────────────────────────────────────────────────────────
    # UI Setup
    # ──────────────────────────────────────────────────────────────────────────
    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("VM:"))
        self.vm_combo = QComboBox()
        self.vm_combo.setMinimumWidth(200)
        self.vm_combo.currentTextChanged.connect(self.on_vm_changed)
        toolbar.addWidget(self.vm_combo)

        refresh_vm_btn = QPushButton("🔄")
        refresh_vm_btn.setToolTip("Refresh VM list")
        refresh_vm_btn.clicked.connect(self.populate_vms)
        toolbar.addWidget(refresh_vm_btn)
        toolbar.addStretch()

        self.start_btn = QPushButton("▶  Start VM")
        self.start_btn.setStyleSheet("background:#4CAF50;color:white;font-weight:bold;padding:10px 25px;")
        self.start_btn.clicked.connect(self.start_vm)
        toolbar.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⏹  Stop")
        self.stop_btn.setStyleSheet("background:#f44336;color:white;font-weight:bold;padding:10px 25px;")
        self.stop_btn.clicked.connect(self.stop_vm)
        self.stop_btn.setEnabled(False)
        toolbar.addWidget(self.stop_btn)

        actions_btn = QPushButton("⚡ Actions")
        actions_menu = QMenu()
        actions_menu.addAction("📁 Import VM from XML", self.import_vm)
        actions_menu.addAction("🔍 Find My VM", self.find_vm)
        actions_menu.addAction("🖥️  Open Console", self.open_console)
        actions_menu.addSeparator()
        actions_menu.addAction("Restart libvirtd", self.restart_libvirtd)
        actions_menu.addAction("Fix Input Permissions", self.fix_perms)
        actions_menu.addAction("Health Check", self.health_check)
        actions_btn.setMenu(actions_menu)
        toolbar.addWidget(actions_btn)

        layout.addLayout(toolbar)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_overview_tab(),  "Overview")
        self.tabs.addTab(self.create_gpu_tab(),       "GPU Passthrough")
        self.tabs.addTab(self.create_devices_tab(),   "Input Devices")
        self.tabs.addTab(self.create_display_tab(),   "Display & SPICE")
        self.tabs.addTab(self.create_xml_tab(),       "XML Viewer")
        self.tabs.addTab(self.create_logs_tab(),      "Logs")
        layout.addWidget(self.tabs)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Ready  |  Running as: " + ("root ✓" if os.geteuid() == 0 else "user (some features need sudo)"))
        self.status_bar.addPermanentWidget(self.status_label)

        # Populate VMs AFTER all widgets exist
        self.populate_vms()

    def create_overview_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        # ── Status ──
        status_grp = QGroupBox("VM Status")
        status_layout = QVBoxLayout()
        self.vm_status_label = QLabel("Status: Unknown")
        self.vm_status_label.setStyleSheet("font-size:16px;font-weight:bold;")
        status_layout.addWidget(self.vm_status_label)
        status_grp.setLayout(status_layout)
        layout.addWidget(status_grp)

        # ── System Resources ──
        stats_grp = QGroupBox("System Resources")
        stats_layout = QVBoxLayout()
        stats_layout.addWidget(QLabel("CPU Usage:"))
        self.cpu_bar = QProgressBar()
        self.cpu_bar.setTextVisible(True)
        self.cpu_bar.setFormat("%v%")
        stats_layout.addWidget(self.cpu_bar)
        stats_layout.addWidget(QLabel("Memory Usage:"))
        self.mem_bar = QProgressBar()
        self.mem_bar.setTextVisible(True)
        self.mem_bar.setFormat("%v%")
        stats_layout.addWidget(self.mem_bar)
        stats_grp.setLayout(stats_layout)
        layout.addWidget(stats_grp)

        # ── Quick-start guide ──
        guide_grp = QGroupBox("Quick Start Guide")
        guide_layout = QVBoxLayout()
        guide_text = QLabel(
            "<ol style='margin:0; padding-left:18px; line-height:1.8'>"
            "<li>Select your VM from the <b>VM:</b> dropdown at the top.</li>"
            "<li>Go to <b>GPU Passthrough</b> tab → select your GPU → click <i>Bind to VFIO</i> if not already ready.</li>"
            "<li>Go to <b>Input Devices</b> → check the <b>keyboard</b> and <b>mouse</b> event nodes to pass through.</li>"
            "<li>Go to <b>Display &amp; SPICE</b> → enable SPICE <i>or</i> connect a monitor to the passthrough GPU.</li>"
            "<li>Click <b>▶ Start VM</b> — done!</li>"
            "</ol>"
            "<p style='color:gray; margin-top:6px'>💡 Tip: Switch input between host and VM by pressing <b>Left Ctrl + Right Ctrl</b> simultaneously.</p>"
        )
        guide_text.setWordWrap(True)
        guide_layout.addWidget(guide_text)
        guide_grp.setLayout(guide_layout)
        layout.addWidget(guide_grp)

        layout.addStretch()
        return w

    def create_gpu_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        intro = QLabel(
            "Select the GPU you want to pass through to the VM. It must show <b>✓ VFIO Ready</b> before starting.<br>"
            "Your <i>host</i> GPU (the one running this screen) should stay in its normal driver — only pass through a <b>second</b> GPU."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Available GPUs:</b>"))
        header.addStretch()
        rescan = QPushButton("🔄 Rescan")
        rescan.clicked.connect(self.scan_gpus)
        header.addWidget(rescan)
        layout.addLayout(header)

        self.gpu_list = QListWidget()
        self.gpu_list.setMinimumHeight(130)
        self.gpu_list.currentItemChanged.connect(self.on_gpu_selected)
        layout.addWidget(self.gpu_list)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(160)
        self.gpu_info = QLabel("← Click a GPU above to see its details")
        self.gpu_info.setWordWrap(True)
        self.gpu_info.setStyleSheet("padding:12px;border:2px solid palette(mid);border-radius:8px;")
        scroll.setWidget(self.gpu_info)
        layout.addWidget(scroll)

        self.gpu_enabled_cb = QCheckBox("✅  Enable GPU Passthrough for this VM")
        self.gpu_enabled_cb.setStyleSheet("font-weight:bold;font-size:11pt;")
        self.gpu_enabled_cb.setToolTip("When checked, the selected GPU will be added to the VM's hardware on Start")
        layout.addWidget(self.gpu_enabled_cb)

        bind_btn = QPushButton("🔗  Bind Selected GPU to VFIO  (required before first use)")
        bind_btn.setToolTip(
            "Detaches the GPU from its NVIDIA/AMD driver and hands it to the vfio-pci driver.\n"
            "This is required once per boot (or whenever the driver resets).\n"
            "The GPU will go black on any monitor connected to it — that is normal."
        )
        bind_btn.clicked.connect(self.manual_bind_vfio)
        layout.addWidget(bind_btn)

        layout.addStretch()
        return w

    def create_devices_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        intro = QLabel(
            "Check the event nodes you want to share with the VM. Devices are grouped by physical hardware.<br>"
            "• For <b>keyboards</b>: check the <code>-event-kbd</code> entry.<br>"
            "• For <b>mice</b>: check the <code>-event-mouse</code> entry.<br>"
            "• Press <b>Left Ctrl + Right Ctrl</b> simultaneously to switch control between host and VM."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        header = QHBoxLayout()
        header.addStretch()
        refresh = QPushButton("🔄 Refresh")
        refresh.clicked.connect(self.scan_devices)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.device_tree = QTreeWidget()
        self.device_tree.setHeaderLabels(["Device / Event Node", "Path", "Type"])
        self.device_tree.setColumnWidth(0, 380)
        self.device_tree.setColumnWidth(1, 500)
        self.device_tree.setAlternatingRowColors(True)
        self.device_tree.itemChanged.connect(self.on_device_changed)
        layout.addWidget(self.device_tree)
        return w

    def create_display_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        spice_grp = QGroupBox("SPICE Virtual Display")
        spice_layout = QVBoxLayout()

        info = QLabel(
            "<b>What is SPICE?</b><br>"
            "SPICE lets you see and control your VM in a window on this PC — like a remote desktop.<br><br>"
            "• <b>Enable SPICE</b> if you don't have a second monitor/GPU for the VM.<br>"
            "• <b>Disable SPICE</b> if using GPU Passthrough — the VM will use the physical monitor plugged into the passed-through GPU."
        )
        info.setWordWrap(True)
        spice_layout.addWidget(info)
        spice_layout.addSpacing(8)

        self.spice_cb = QCheckBox("Enable SPICE Display  (console opens automatically on VM start)")
        self.spice_cb.setChecked(False)
        spice_layout.addWidget(self.spice_cb)
        spice_layout.addSpacing(8)

        video_row = QHBoxLayout()
        video_row.addWidget(QLabel("Video adapter type:"))
        self.video_combo = QComboBox()
        self.video_combo.addItems(["QXL (recommended for SPICE)", "VirtIO (faster, needs guest drivers)"])
        self.video_combo.setToolTip("QXL works out of the box. VirtIO is faster but needs virtio-win drivers installed in the VM first.")
        video_row.addWidget(self.video_combo)
        video_row.addStretch()
        spice_layout.addLayout(video_row)
        spice_layout.addSpacing(8)

        console_btn = QPushButton("🖥️  Open Console Now")
        console_btn.setToolTip("Opens virt-viewer to connect to the VM's SPICE display")
        console_btn.clicked.connect(self.open_console)
        spice_layout.addWidget(console_btn)

        spice_grp.setLayout(spice_layout)
        layout.addWidget(spice_grp)

        layout.addStretch()
        return w

    def create_xml_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        controls = QHBoxLayout()
        reload_btn = QPushButton("🔄 Reload")
        reload_btn.clicked.connect(self.reload_xml)
        controls.addWidget(reload_btn)
        export_btn = QPushButton("📤 Export")
        export_btn.clicked.connect(self.export_xml)
        controls.addWidget(export_btn)
        save_xml_btn = QPushButton("💾 Apply XML Changes")
        save_xml_btn.setToolTip("Redefines the VM with the current XML in the editor")
        save_xml_btn.clicked.connect(self.apply_xml_changes)
        controls.addWidget(save_xml_btn)

        # FIXED: stateChanged emits int in PyQt6; use explicit comparison
        self.xml_readonly = QCheckBox("Read-Only")
        self.xml_readonly.setChecked(True)
        self.xml_readonly.stateChanged.connect(self._on_readonly_changed)
        controls.addWidget(self.xml_readonly)
        controls.addStretch()
        layout.addLayout(controls)

        self.xml_editor = QTextEdit()
        self.xml_editor.setFont(QFont("Monospace", 10))
        self.xml_editor.setReadOnly(True)
        layout.addWidget(self.xml_editor)
        return w

    def _on_readonly_changed(self, state: int):
        # PyQt6 stateChanged emits an int (0=unchecked, 2=checked)
        self.xml_editor.setReadOnly(state == Qt.CheckState.Checked.value)

    def create_logs_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        controls = QHBoxLayout()
        self.autoscroll = QCheckBox("Auto-scroll")
        self.autoscroll.setChecked(True)
        controls.addWidget(self.autoscroll)
        controls.addStretch()
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.log_view.clear())
        controls.addWidget(clear)
        layout.addLayout(controls)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Courier", 9))
        layout.addWidget(self.log_view)
        return w

    # ──────────────────────────────────────────────────────────────────────────
    # Tray
    # ──────────────────────────────────────────────────────────────────────────
    def setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon.fromTheme("computer"))
        menu = QMenu()
        menu.addAction("Show", self.show)
        menu.addAction("Quit", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.show()

    # ──────────────────────────────────────────────────────────────────────────
    # VM list
    # ──────────────────────────────────────────────────────────────────────────
    def populate_vms(self):
        self.vm_combo.blockSignals(True)
        prev = self.vm_combo.currentText()
        self.vm_combo.clear()
        try:
            r = virsh('list', '--all', '--name', capture_output=True, text=True)
            vms = [v.strip() for v in r.stdout.splitlines() if v.strip()]
            if vms:
                self.vm_combo.addItems(vms)
                # Restore previous selection if still present
                idx = self.vm_combo.findText(prev)
                if idx >= 0:
                    self.vm_combo.setCurrentIndex(idx)
            else:
                self.vm_combo.setEditable(True)
                self.vm_combo.setPlaceholderText("No VMs found — type name or import XML")
        except Exception as e:
            self.vm_combo.setEditable(True)
            self.vm_combo.setPlaceholderText("virsh error — is libvirtd running?")
            self.log(f"populate_vms error: {e}", "error")
        finally:
            self.vm_combo.blockSignals(False)
            self.on_vm_changed(self.vm_combo.currentText())

    # ──────────────────────────────────────────────────────────────────────────
    # GPU scan & bind
    # ──────────────────────────────────────────────────────────────────────────
    def scan_gpus(self):
        self.log("Scanning GPUs...")
        self.available_gpus = detect_gpus()
        self.gpu_list.clear()
        for gpu in self.available_gpus:
            item = QListWidgetItem(str(gpu))
            item.setData(Qt.ItemDataRole.UserRole, gpu)
            item.setForeground(QColor("#4CAF50" if gpu.is_ready() else "#f44336"))
            self.gpu_list.addItem(item)
            self.log(f"Found GPU: {gpu}")
        if not self.available_gpus:
            self.log("No GPUs detected", "warn")

    def on_gpu_selected(self, current, previous):
        if not current:
            return
        gpu = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(gpu, GPUDevice):
            return
        self.selected_gpu = gpu
        color = "#4CAF50" if gpu.is_ready() else "#f44336"
        info = (f"<h2 style='color:{color}'>{gpu.vendor} {gpu.friendly_name}</h2>"
                f"<p>PCI: {gpu.pci_address}<br>"
                f"Device ID: {gpu.device_id}<br>"
                f"VFIO bound: {'✓ Yes' if gpu.vfio_bound else '✗ No'}<br>"
                f"Audio companion: {gpu.audio_address or 'None'}</p>")
        self.gpu_info.setText(info)

    def manual_bind_vfio(self):
        if not self.selected_gpu:
            QMessageBox.warning(self, "No GPU Selected", "Please select a GPU from the list first.")
            return
        self.bind_gpu_vfio(self.selected_gpu)
        # Delay rescan 3s — kernel needs time to update driver symlinks
        self.log("Waiting for kernel to settle before rescanning...")
        QTimer.singleShot(3000, self.scan_gpus)

    def bind_gpu_vfio(self, gpu: GPUDevice):
        """Unbind GPU (and companion audio) from current driver, bind to vfio-pci.

        The correct sysfs sequence for already-present PCI devices:
          1. modprobe vfio-pci
          2. echo ADDR > /sys/bus/pci/devices/ADDR/driver/unbind
          3. echo 'VVVV DDDD' > /sys/bus/pci/drivers/vfio-pci/new_id
             (registers the ID — triggers auto-bind for devices already unbound)
          4. If new_id says EEXIST, echo ADDR > /sys/bus/pci/drivers/vfio-pci/bind
             (device ID already known, bind manually)
        """
        try:
            self.log("Loading vfio-pci module...")
            subprocess.run(['modprobe', 'vfio-pci'], check=True)
            subprocess.run(['modprobe', 'vfio_iommu_type1'], check=False)

            def bind_device(pci_addr: str, dev_id_str: str):
                """Unbind from current driver and attach to vfio-pci."""
                full = f"0000:{pci_addr}"
                new_id_path = "/sys/bus/pci/drivers/vfio-pci/new_id"
                bind_path   = "/sys/bus/pci/drivers/vfio-pci/bind"

                # Step 1: unbind from current driver
                unbind_path = f"/sys/bus/pci/devices/{full}/driver/unbind"
                if Path(unbind_path).exists():
                    r = subprocess.run(
                        f"echo '{full}' > '{unbind_path}'",
                        shell=True, capture_output=True, text=True)
                    self.log(f"Unbound {pci_addr} from current driver")

                # Step 2: register vendor:device with vfio-pci
                # new_id format: 'VVVV DDDD' (space-separated, no 0x prefix)
                r = subprocess.run(
                    f"echo '{dev_id_str}' > '{new_id_path}'",
                    shell=True, capture_output=True, text=True)

                if r.returncode == 0:
                    self.log(f"Registered {dev_id_str} with vfio-pci via new_id")
                else:
                    # EEXIST means ID already registered — that's fine, just bind manually
                    self.log(f"new_id returned (likely already registered): {r.stderr.strip()}", "warn")
                    # Step 3: explicit bind since new_id won't auto-bind a known ID
                    rb = subprocess.run(
                        f"echo '{full}' > '{bind_path}'",
                        shell=True, capture_output=True, text=True)
                    if rb.returncode == 0:
                        self.log(f"Explicitly bound {pci_addr} to vfio-pci")
                    else:
                        self.log(f"bind error: {rb.stderr.strip()}", "warn")

            # Bind GPU video device
            self.log(f"Binding {gpu.friendly_name} [{gpu.pci_address}]...")
            bind_device(gpu.pci_address, gpu.new_id_string)

            # Bind companion audio device (must be in same IOMMU group)
            if gpu.audio_address:
                r2 = subprocess.run(['lspci', '-n', '-s', gpu.audio_address],
                                    capture_output=True, text=True)
                id_m = re.search(r'([0-9a-f]{4}):([0-9a-f]{4})', r2.stdout)
                if id_m:
                    audio_id_str = f"{id_m.group(1)} {id_m.group(2)}"
                    self.log(f"Binding audio companion [{gpu.audio_address}]...")
                    bind_device(gpu.audio_address, audio_id_str)
                    self.log(f"Audio companion {gpu.audio_address} bound to VFIO")

            self.log(f"✓ Bound {gpu.friendly_name} to VFIO — rescan in 3s", "info")
        except Exception as e:
            self.log(f"VFIO bind error: {e}", "error")

    # ──────────────────────────────────────────────────────────────────────────
    # Device scan
    # ──────────────────────────────────────────────────────────────────────────
    def scan_devices(self):
        """Scan /dev/input/by-id and group entries by physical device.

        Each physical USB device (e.g. your Ducky keyboard) can expose several
        event nodes: one for keystrokes, one for media keys, etc.  We group them
        under a collapsible parent row so the list stays clean.  The user checks
        the child entry they want to pass through — for keyboards pick the '-kbd'
        or '-event-kbd' node; for mice pick '-event-mouse'.
        """
        self.device_tree.blockSignals(True)
        self.device_tree.clear()
        self.available_devices.clear()

        input_dir = Path('/dev/input/by-id')
        if not input_dir.exists():
            self.log("/dev/input/by-id not found", "warn")
            self.device_tree.blockSignals(False)
            return

        # Group event files by their physical device base name.
        # e.g. "usb-Ducky_One_3-event-kbd" and "usb-Ducky_One_3-event-mouse"
        # both belong to "usb-Ducky_One_3".
        groups: Dict[str, List[Path]] = defaultdict(list)
        for p in sorted(input_dir.iterdir()):
            if '-event-' not in p.name:
                continue
            # Base = everything before the last '-event-...' segment
            base = re.sub(r'-event-.*$', '', p.name)
            groups[base].append(p)

        for base_name, paths in sorted(groups.items()):
            # Friendly display name: strip leading 'usb-' and replace _ with spaces
            friendly = base_name.removeprefix('usb-').replace('_', ' ')

            # Determine the dominant type for the group icon
            has_kbd   = any('kbd' in p.name.lower() or 'keyboard' in p.name.lower() for p in paths)
            has_mouse = any('mouse' in p.name.lower() for p in paths)
            group_type = 'keyboard' if has_kbd else 'mouse' if has_mouse else 'other'
            icon_char  = '⌨' if group_type == 'keyboard' else '🖱' if group_type == 'mouse' else '🎮'

            parent = QTreeWidgetItem([f"{icon_char}  {friendly}", '', group_type])
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self.device_tree.addTopLevelItem(parent)

            for p in paths:
                real_path = str(p.resolve())
                # Classify the specific event node
                name_low = p.name.lower()
                if 'kbd' in name_low or 'keyboard' in name_low:
                    node_type = 'keyboard'
                    hint = ' ← pass this for keys'
                elif 'mouse' in name_low:
                    node_type = 'mouse'
                    hint = ' ← pass this for mouse'
                else:
                    node_type = 'other'
                    hint = ''

                # Short label: just the suffix after the base name
                suffix = p.name[len(base_name):]   # e.g. '-event-kbd'
                child = QTreeWidgetItem([f"  {suffix}{hint}", str(p), node_type])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                checked = Qt.CheckState.Checked if real_path in self.selected_devices else Qt.CheckState.Unchecked
                child.setCheckState(0, checked)
                child.setData(0, Qt.ItemDataRole.UserRole, real_path)
                child.setToolTip(0, f"Full path: {real_path}")
                parent.addChild(child)

                dev = DeviceInfo(real_path, p.name, node_type)
                self.available_devices.append(dev)

            parent.setExpanded(True)

        self.device_tree.blockSignals(False)
        self.log(f"Found {len(self.available_devices)} event nodes across {len(groups)} device(s)")

    def on_device_changed(self, item, col):
        if col != 0:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return   # group header row — no path, nothing to do
        if item.checkState(0) == Qt.CheckState.Checked:
            if path not in self.selected_devices:
                self.selected_devices.append(path)
        else:
            if path in self.selected_devices:
                self.selected_devices.remove(path)

    # ──────────────────────────────────────────────────────────────────────────
    # VM Start / Stop
    # ──────────────────────────────────────────────────────────────────────────
    def start_vm(self):
        vm = self.vm_combo.currentText().strip()
        if not vm:
            QMessageBox.warning(self, "No VM", "Please select or type a VM name.")
            return

        self.log(f"Starting '{vm}'...")
        self.start_btn.setEnabled(False)

        try:
            if self.gpu_enabled_cb.isChecked() and self.selected_gpu:
                self.bind_gpu_vfio(self.selected_gpu)
                self.inject_gpu_xml(vm)

            if self.selected_devices:
                self.inject_devices_xml(vm)

            r = virsh('start', vm, capture_output=True, text=True)
            if r.returncode == 0:
                self.log(f"✓ '{vm}' started!", "info")
                self.notify("VM Started", f"{vm} is now running")
                # Auto-open console if SPICE is enabled
                if self.spice_cb.isChecked():
                    self.log("SPICE enabled — opening console in 2s...")
                    QTimer.singleShot(2000, self.open_console)
            else:
                self.log(f"virsh start failed: {r.stderr.strip()}", "error")
                QMessageBox.critical(self, "Start Failed", r.stderr.strip())
        except Exception as e:
            self.log(f"Start error: {e}", "error")
        finally:
            self.update_vm_status()

    def stop_vm(self):
        vm = self.vm_combo.currentText().strip()
        if vm:
            virsh('shutdown', vm, capture_output=True, text=True)
            self.log(f"Shutdown signal sent to '{vm}'")

    # ──────────────────────────────────────────────────────────────────────────
    # XML inject helpers – FIXED: use temp file, not /dev/stdin pipe
    # ──────────────────────────────────────────────────────────────────────────
    def inject_gpu_xml(self, vm: str):
        try:
            r = virsh('dumpxml', vm, capture_output=True, text=True, check=True)
            root = ET.fromstring(r.stdout)
            devices = root.find('devices')
            if devices is None:
                self.log("No <devices> element in XML", "error")
                return

            # Remove any existing GPU hostdev entries to avoid duplicates
            for hd in devices.findall('hostdev[@type="pci"]'):
                devices.remove(hd)

            # Add GPU PCI hostdev
            # PCI address from lspci: 'BB:SS.F'  (e.g. '01:00.0')
            pci = self.selected_gpu.pci_address      # e.g. '01:00.0'
            bus, rest = pci.split(':', 1)
            slot, func = rest.split('.', 1)

            def add_hostdev(bus_hex, slot_hex, func_hex):
                hostdev = ET.SubElement(devices, 'hostdev')
                hostdev.set('mode', 'subsystem')
                hostdev.set('type', 'pci')
                hostdev.set('managed', 'yes')
                source = ET.SubElement(hostdev, 'source')
                addr = ET.SubElement(source, 'address')
                addr.set('domain', '0x0000')
                addr.set('bus',      f'0x{bus_hex}')
                addr.set('slot',     f'0x{slot_hex}')
                addr.set('function', f'0x{func_hex}')

            add_hostdev(bus, slot, func)

            # Also add audio companion if present
            if self.selected_gpu.audio_address:
                apci = self.selected_gpu.audio_address
                abus, arest = apci.split(':', 1)
                aslot, afunc = arest.split('.', 1)
                add_hostdev(abus, aslot, afunc)

            # SPICE toggle
            if not self.spice_cb.isChecked():
                for g in devices.findall('graphics'):
                    devices.remove(g)
                for v in devices.findall('video'):
                    devices.remove(v)

            result = virsh_define_xml(ET.tostring(root, encoding='unicode'))
            if result.returncode == 0:
                self.log("GPU injected into VM XML")
            else:
                self.log(f"virsh define error: {result.stderr.strip()}", "error")
        except Exception as e:
            self.log(f"XML inject error: {e}", "error")

    def inject_devices_xml(self, vm: str):
        try:
            r = virsh('dumpxml', vm, capture_output=True, text=True, check=True)
            root = ET.fromstring(r.stdout)
            devices = root.find('devices')
            if devices is None:
                return

            # Remove existing evdev inputs
            for inp in devices.findall('input[@type="evdev"]'):
                devices.remove(inp)

            # Add selected devices
            for dev_path in self.selected_devices:
                inp = ET.SubElement(devices, 'input')
                inp.set('type', 'evdev')
                src = ET.SubElement(inp, 'source')
                src.set('dev', dev_path)
                # grab='all' and repeat='on' only on keyboard nodes (not mouse)
                # grabToggle: left+right Ctrl to switch control between host and VM
                is_kbd = 'kbd' in dev_path.lower() or 'keyboard' in dev_path.lower()
                if is_kbd:
                    src.set('grab', 'all')
                    src.set('grabToggle', 'ctrl-ctrl')
                    src.set('repeat', 'on')

            result = virsh_define_xml(ET.tostring(root, encoding='unicode'))
            if result.returncode == 0:
                self.log(f"✓ Added {len(self.selected_devices)} input device(s) to VM XML")
            else:
                self.log(f"Device inject define error: {result.stderr.strip()}", "error")
        except Exception as e:
            self.log(f"Device inject error: {e}", "error")

    # ──────────────────────────────────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────────────────────────────────
    def import_vm(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select XML", str(Path.home()), "XML (*.xml)")
        if f:
            try:
                virsh('define', f, check=True)
                self.populate_vms()
                self.log(f"VM imported from {f}")
            except Exception as e:
                QMessageBox.critical(self, "Import Error", str(e))

    def find_vm(self):
        try:
            r = virsh('list', '--all', '--name', capture_output=True, text=True)
            vms = [v.strip() for v in r.stdout.splitlines() if v.strip()]
            msg = "Found VMs:\n" + "\n".join(f"  • {v}" for v in vms) if vms else "No VMs found."
            QMessageBox.information(self, "VM List", msg)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def open_console(self):
        vm = self.vm_combo.currentText().strip()
        if not vm:
            return
        for viewer in ['virt-viewer', 'virt-manager']:
            try:
                subprocess.Popen([viewer, '--connect', VIRSH_URI, vm])
                return
            except FileNotFoundError:
                continue
        QMessageBox.critical(self, "Not Found",
                             "Install virt-viewer or virt-manager for console access.\n"
                             "  sudo apt install virt-viewer")

    def restart_libvirtd(self):
        """Restart libvirtd. We're running as root, so no pkexec needed."""
        try:
            # Running as root — call systemctl directly (pkexec is wrong for root)
            subprocess.run(['systemctl', 'restart', 'libvirtd'], check=True)
            self.log("libvirtd restarted")
            time.sleep(1)
            self.populate_vms()
        except Exception as e:
            self.log(f"libvirtd restart failed: {e}", "error")

    def fix_perms(self):
        """Fix /dev/input permissions so evdev passthrough works."""
        try:
            # Running as root already — no pkexec needed
            subprocess.run(['chmod', '666'] + list(Path('/dev/input').glob('event*')),
                           check=True)
            self.log("Input device permissions fixed (chmod 666 /dev/input/event*)")
        except Exception as e:
            self.log(f"Permission fix failed: {e}", "error")

    def health_check(self):
        issues = []
        suggestions = []

        # libvirtd running?
        r = subprocess.run(['systemctl', 'is-active', 'libvirtd'], capture_output=True, text=True)
        if r.stdout.strip() != 'active':
            issues.append("libvirtd is NOT running")
            suggestions.append("  sudo systemctl start libvirtd")

        # Running as root?
        if os.geteuid() != 0:
            issues.append("Not running as root (some operations will fail)")
            suggestions.append("  sudo -E python3 VMManager.py")

        # IOMMU enabled?
        iommu = subprocess.run(['dmesg'], capture_output=True, text=True)
        if 'IOMMU' not in iommu.stdout and 'iommu' not in iommu.stdout:
            issues.append("IOMMU may not be enabled (check BIOS + kernel params)")
            suggestions.append("  Add 'intel_iommu=on' or 'amd_iommu=on' to GRUB_CMDLINE_LINUX")

        # vfio modules?
        lsmod = subprocess.run(['lsmod'], capture_output=True, text=True)
        if 'vfio_pci' not in lsmod.stdout:
            issues.append("vfio-pci module not loaded")
            suggestions.append("  sudo modprobe vfio-pci")

        if issues:
            msg = "⚠ Issues found:\n\n"
            for i, (issue, sug) in enumerate(zip(issues, suggestions)):
                msg += f"  {i+1}. {issue}\n{sug}\n\n"
        else:
            msg = "✓ All checks passed!"

        QMessageBox.information(self, "Health Check", msg)

    # ──────────────────────────────────────────────────────────────────────────
    # XML tab
    # ──────────────────────────────────────────────────────────────────────────
    def reload_xml(self):
        vm = self.vm_combo.currentText().strip()
        if not vm:
            return
        try:
            r = virsh('dumpxml', vm, capture_output=True, text=True, check=True)
            self.xml_editor.setPlainText(r.stdout)
        except Exception as e:
            self.log(f"dumpxml error: {e}", "error")

    def export_xml(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export XML", "", "XML (*.xml)")
        if f:
            Path(f).write_text(self.xml_editor.toPlainText())
            self.log(f"XML exported to {f}")

    def apply_xml_changes(self):
        xml_text = self.xml_editor.toPlainText().strip()
        if not xml_text:
            return
        try:
            ET.fromstring(xml_text)   # Validate XML before sending
        except ET.ParseError as e:
            QMessageBox.critical(self, "XML Parse Error", f"Invalid XML:\n{e}")
            return
        result = virsh_define_xml(xml_text)
        if result.returncode == 0:
            self.log("✓ XML changes applied")
            QMessageBox.information(self, "Success", "VM configuration updated.")
        else:
            self.log(f"virsh define failed: {result.stderr.strip()}", "error")
            QMessageBox.critical(self, "Error", result.stderr.strip())

    def on_vm_changed(self, vm: str):
        if vm and vm.strip():
            self.reload_xml()

    # ──────────────────────────────────────────────────────────────────────────
    # Status updates
    # ──────────────────────────────────────────────────────────────────────────
    def update_vm_status(self):
        vm = self.vm_combo.currentText().strip()
        if not vm:
            return
        try:
            r = virsh('domstate', vm, capture_output=True, text=True)
            state = r.stdout.strip().lower()
            if state == 'running':
                self.vm_status_label.setText(f"Status: ● Running — {vm}")
                self.vm_status_label.setStyleSheet("color:#4CAF50;font-size:16px;font-weight:bold;")
                self.start_btn.setEnabled(False)
                self.stop_btn.setEnabled(True)
                self.vm_running = True
            else:
                self.vm_status_label.setText(f"Status: ⏸ {state.title()} — {vm}")
                self.vm_status_label.setStyleSheet("color:#f44336;font-size:16px;font-weight:bold;")
                self.start_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
                self.vm_running = False
        except Exception:
            pass

    def update_stats(self, stats: dict):
        self.cpu_bar.setValue(int(stats.get('cpu', 0)))
        self.mem_bar.setValue(int(stats.get('mem', 0)))

    # ──────────────────────────────────────────────────────────────────────────
    # Logging / notification
    # ──────────────────────────────────────────────────────────────────────────
    def log(self, msg: str, level: str = "info"):
        colors = {"error": "#f44336", "warn": "#FF9800", "info": "#4fc3f7"}
        color = colors.get(level, "#4fc3f7")
        ts = datetime.now().strftime("%H:%M:%S")
        fmt = (f'<span style="color:gray">[{ts}]</span> '
               f'<span style="color:{color}">[{level.upper()}]</span> {msg}')
        if hasattr(self, 'log_view'):
            self.log_view.append(fmt)
            if hasattr(self, 'autoscroll') and self.autoscroll.isChecked():
                self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        else:
            print(f"[{level.upper()}] {msg}")

    def notify(self, title: str, msg: str):
        if self.tray.isSystemTrayAvailable():
            self.tray.showMessage(title, msg, QSystemTrayIcon.MessageIcon.Information, 3000)

    # ──────────────────────────────────────────────────────────────────────────
    # Theme / Settings / Quit
    # ──────────────────────────────────────────────────────────────────────────
    def apply_theme(self):
        # Let KDE/system theme handle everything — don't override the palette.
        # This respects your global theme (Windows 12 Light, Breeze, etc.)
        app = QApplication.instance()
        app.setStyle("Breeze")
        app.setPalette(app.style().standardPalette())

    def save_settings(self):
        self.settings.setValue('vm', self.vm_combo.currentText())
        self.settings.setValue('devices', self.selected_devices)
        self.settings.sync()

    def load_settings(self):
        val = self.settings.value('devices', [])
        self.selected_devices = list(val) if val else []

    def quit_app(self):
        self.save_settings()
        self.monitor.stop()
        self.monitor.wait(3000)
        QApplication.quit()

    def closeEvent(self, event):
        self.quit_app()


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────
def main():
    # Warn if not running as root
    if os.geteuid() != 0:
        print("WARNING: Not running as root. GPU binding and some libvirt operations may fail.")
        print("         Recommended: sudo -E python3 VMManager.py\n")

    app = QApplication(sys.argv)
    app.setApplicationName("KVM VM Manager Ultimate")
    app.setOrganizationName("KVMManager")

    window = KVMManagerGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
