# KVM VM Manager

A graphical manager for KVM virtual machines on Linux — built for gaming setups with **GPU passthrough**, **evdev input passthrough**, and **SPICE console** support.

> **Designed for:** Nobara / Fedora / Ubuntu / Arch · KDE Plasma · Wayland or X11 · NVIDIA/AMD GPU passthrough

![Overview](screenshots/overview.png)

---

## What it does

| Feature | Description |
|---|---|
| **GPU Passthrough** | Bind your secondary GPU to vfio-pci with one click, then pass it directly to the VM |
| **Input Passthrough** | Share keyboard & mouse with the VM via evdev — switch between host and VM with Left Ctrl + Right Ctrl |
| **SPICE Console** | Optional virtual display in a window — auto-opens when the VM starts |
| **XML Editor** | View and edit your VM's libvirt XML config directly |
| **Health Check** | Detects common setup problems (IOMMU, libvirtd, missing modules) |
| **System Monitor** | Live CPU and RAM usage at a glance |

---

## Requirements

Your system needs to have **KVM/QEMU** set up before using this app. If you're starting fresh, see [Prerequisites](#prerequisites) below.

- Python 3.10 or newer
- PyQt6
- QEMU/KVM + libvirt (`virsh`)
- `lspci` (pciutils)
- `virt-viewer` (optional — for SPICE console)

---

## Install

### One-command install (recommended)

```bash
git clone https://github.com/YOUR_USERNAME/kvm-vm-manager.git
cd kvm-vm-manager
bash install.sh
```

That's it. The installer will:
- Check for and install missing packages (supports **dnf**, **apt**, **pacman**, **zypper**)
- Install `PyQt6` via pip if needed
- Copy `VMManager.py` to `~/.local/bin/`
- Create a launcher script that handles Wayland/X11 automatically
- Put a **double-clickable shortcut** on your Desktop
- Add you to the `libvirt` group

> **After install:** Log out and back in once (for the libvirt group to activate), then double-click the icon on your Desktop.

### Manual install (no git)

1. Download `VMManager.py`, `install.sh`, and `launch-vmmanager.sh`
2. Put them all in the same folder
3. Run: `bash install.sh`

---

## First launch

Double-click **KVM VM Manager** on your Desktop. You'll be prompted for your password (required for GPU operations).

**First time setup checklist:**
1. Go to **Actions → Health Check** — fix any issues it reports
2. Read the **Overview** tab — it has a 5-step quick start guide
3. Your VMs will appear in the **VM:** dropdown at the top

---

## Usage

### Start a VM normally (no GPU passthrough)
1. Select your VM from the **VM:** dropdown
2. Click **▶ Start VM**

### Start a VM with GPU passthrough
1. **GPU Passthrough** tab → click your GPU in the list
2. Click **🔗 Bind Selected GPU to VFIO** — wait for "✓ VFIO Ready"
3. Check **Enable GPU Passthrough for this VM**
4. **Input Devices** tab → check `-event-kbd` for your keyboard, `-event-mouse` for your mouse
5. Click **▶ Start VM**

> The monitor plugged into the passed-through GPU will turn on when the VM boots.  
> Press **Left Ctrl + Right Ctrl** together to switch keyboard/mouse between your host and the VM.

### Use SPICE (no second GPU needed)
1. **Display & SPICE** tab → check **Enable SPICE Display**
2. Click **▶ Start VM** — the console window opens automatically

---

## Prerequisites

> Skip this section if you already have QEMU/KVM set up.

### Enable IOMMU in BIOS and kernel

1. **In your BIOS:** Enable Intel VT-d (Intel) or AMD-Vi (AMD)
2. **Add kernel parameter** — edit `/etc/default/grub`:
   ```
   GRUB_CMDLINE_LINUX="... intel_iommu=on"   # Intel CPU
   GRUB_CMDLINE_LINUX="... amd_iommu=on"     # AMD CPU
   ```
3. Rebuild GRUB and reboot:
   ```bash
   sudo grub2-mkconfig -o /boot/grub2/grub.cfg   # Fedora/Nobara
   sudo update-grub                               # Ubuntu/Debian
   sudo reboot
   ```

### Install QEMU/KVM

**Fedora / Nobara:**
```bash
sudo dnf install @virtualization
sudo systemctl enable --now libvirtd
```

**Ubuntu / Debian:**
```bash
sudo apt install qemu-kvm libvirt-daemon-system libvirt-clients virt-manager
sudo systemctl enable --now libvirtd
```

**Arch Linux:**
```bash
sudo pacman -S qemu libvirt virt-manager virt-viewer
sudo systemctl enable --now libvirtd
```

### Create a Windows VM

The easiest way is to use **virt-manager** to create the initial VM, then use this app to manage and launch it with GPU passthrough.

```bash
virt-manager   # GUI wizard — create your VM here first
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| App won't open / blank screen | Run `bash launch-vmmanager.sh` in a terminal to see errors. Check `/tmp/vmmanager-YYYYMMDD.log` |
| "virsh error — is libvirtd running?" | `sudo systemctl start libvirtd` |
| GPU shows "✗ Not Bound to VFIO" after binding | Wait 3 seconds — the rescan is delayed. If still failing, check `dmesg` for vfio errors |
| VM starts but GPU monitor stays black | Make sure the GPU is in the same IOMMU group as its audio companion (both must be bound) |
| Keyboard/mouse stuck in VM | Press Left Ctrl + Right Ctrl at the same time |
| "Not in libvirt group" in Health Check | `sudo usermod -aG libvirt $USER` then log out and back in |
| PyQt6 not found | `pip install PyQt6 --break-system-packages` |

---

## File layout

```
~/.local/bin/VMManager.py          ← the app
~/.local/bin/launch-vmmanager.sh   ← launcher (auto-created by install.sh)
~/Desktop/KVM VM Manager.desktop   ← desktop shortcut
~/.local/share/applications/       ← app menu entry
/tmp/vmmanager-YYYYMMDD.log        ← log file (written each launch)
```

---

## Uninstall

```bash
bash install.sh --remove
```

Your VM configurations in libvirt are not touched.

---

## Contributing

PRs welcome. The GPU name database (`GPU_NAMES` dict in `VMManager.py`) especially benefits from community additions — if your GPU isn't showing a friendly name, open a PR with its PCI ID and name.

Find your GPU's PCI ID:
```bash
lspci -nn | grep -i vga
# Example output: 01:00.0 VGA ... [10de:2782]
#                                   ^^^^^^^^^ this is the ID
```

---

## License

MIT — do whatever you want with it.
