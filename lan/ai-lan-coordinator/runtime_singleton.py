"""One OS file lock per machine/role, shared across interactive and batch logons."""
import atexit
from pathlib import Path
import msvcrt

HELD={}


class AlreadyActive(RuntimeError):
    pass


def acquire_once(root,role):
    if role not in ('worker','coordinator','controller'):
        raise ValueError('unknown singleton role')
    path=Path(root).resolve()/'private'/'runtime-locks'/(role+'.lock')
    key=str(path).casefold()
    if key in HELD:
        return HELD[key]
    path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b')
    try:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
    except OSError as exc:
        stream.close()
        raise AlreadyActive('one '+role+' instance already holds machine lock') from exc
    HELD[key]=stream
    return stream


def close_all():
    for stream in HELD.values():
        stream.close()
    HELD.clear()


atexit.register(close_all)
