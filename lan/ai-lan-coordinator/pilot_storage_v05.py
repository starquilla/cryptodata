"""PC1 pilot storage accounting with one reviewed Windows cache alias."""
import os
from pathlib import Path
import shutil
import socket
import stat
from network import StoragePaused

ALIAS=Path('private/antigravity-home/AppData/Local/Microsoft/Windows/INetCache/Content.IE5')
TARGET=Path('private/antigravity-home/AppData/Local/Microsoft/Windows/INetCache/IE')

def checked_alias(root,path):
    if path.relative_to(root)!=ALIAS: raise StoragePaused('Unreviewed reparse point')
    raw=os.readlink(path)
    if raw.startswith('\\\\?\\'): raw=raw[4:]
    actual=Path(raw).resolve(); expected=root/TARGET
    if actual!=expected or not actual.is_relative_to(root) or not actual.is_dir():
        raise StoragePaused('Reviewed alias target changed or escaped project')
    current=root
    for part in TARGET.parts:
        current=current/part
        if current.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise StoragePaused('Alias target contains another reparse point')

def pc1_storage_guard(root,config):
    root=Path(root).resolve(); spec=config.get('storage',{})
    drive=spec.get('required_drive_by_host',{}).get(socket.gethostname())
    if drive and root.drive.casefold()!=drive.casefold(): raise StoragePaused('Wrong storage drive')
    limit=spec.get('project_limit_bytes',30*1024**3); high=spec.get('pause_at_bytes',27*1024**3); minimum=spec.get('minimum_disk_free_bytes',10*1024**3)
    if not 0<high<=limit or minimum<0: raise ValueError('Invalid storage budget')
    pending=[root]; total=0; aliases=0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                st=entry.stat(follow_symlinks=False)
                if st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    checked_alias(root,Path(entry.path)); aliases+=1
                    continue  # Target is counted once through its ordinary path; never traverse alias.
                if entry.is_dir(follow_symlinks=False): pending.append(Path(entry.path))
                else: total+=st.st_size
                if total>=high: raise StoragePaused('Project high-water mark reached')
    free=shutil.disk_usage(root).free
    if free<minimum: raise StoragePaused('Disk free space below reserve')
    return {'project_bytes':total,'free_bytes':free,'limit_bytes':limit,'pause_at_bytes':high,'reviewed_cache_aliases_skipped':aliases}
