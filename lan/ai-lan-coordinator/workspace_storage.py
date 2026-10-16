"""Use the reviewed internal cache alias only for the approved PC1 workspace."""
from pathlib import Path
import socket
from network import storage_guard
from pilot_storage_v05 import pc1_storage_guard

def workspace_storage_guard(root,config):
    root=Path(root).resolve()
    expected=Path('E:/công cụ lập trình/AI-LAN-Coordinator')
    if socket.gethostname().casefold()=='desktop-7vpam29' and root==expected:
        return pc1_storage_guard(root,config)
    return storage_guard(root,config)
