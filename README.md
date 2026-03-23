# Mount-Disks

Mount-Disks is a Linux desktop utility (PyQt6) for discovering, mounting, and unmounting partitions from a simple GUI.  
It is designed for workflows similar to CAINE, with support for standard filesystems and BitLocker volumes (via `dislocker`).

## Features

- List physical disks and partitions with mount status
- Mount and unmount selected partitions from the GUI
- Open mounted paths in the file manager
- Handle BitLocker-encrypted partitions
- Clean up mount points created by the app

## Requirements

- Linux environment (X11/Wayland desktop session)
- Python 3
- `PyQt6` Python package
- System tools: `lsblk`, `mount`, `umount`, `sudo` and/or `pkexec`
- `dislocker` (required for BitLocker volumes)

## Installation

### 1) Install system packages

Install dependencies for your distribution (package names can vary):

- Debian/Ubuntu (example):
  - `sudo apt install python3 python3-pip python3-pyqt6 util-linux sudo policykit-1 dislocker`
- Fedora (example):
  - `sudo dnf install python3 python3-pip python3-qt6 util-linux sudo polkit dislocker`
- Arch (example):
  - `sudo pacman -S python python-pip python-pyqt6 util-linux sudo polkit dislocker`

### 2) Install Python dependency (if not provided by distro package)

```bash
pip3 install PyQt6
```

## Usage

From the repository directory:

```bash
python3 "Mount Disks.py"
```

In the GUI:

1. Click **Refresh** to detect partitions.
2. Select a partition from the list.
3. For protected volumes, enter the BitLocker recovery key.
4. Enter your admin password (if needed for `sudo`) and click **Mount** or **Unmount**.

## Mount locations used by the app

- Standard mounts: `/media/mount`
- BitLocker raw/FUSE mounts: `/mnt/bitlocker_raw`

## Recommended setup

- Install filesystem support packages your environment may need (e.g., `ntfs-3g`, exFAT tools).
- Ensure your user can authenticate via `sudo` or `pkexec`.
- Run from a desktop session (not a headless shell) for GUI prompts and file-manager integration.

## Optional desktop launcher

The repository includes `Mount Disks.desktop`, but it currently contains machine-specific absolute paths.  
If you use it, update `Exec=` and `Icon=` to match your local installation paths before placing it in:

- `~/.local/share/applications/` (user)
- or `/usr/share/applications/` (system-wide)
