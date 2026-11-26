import os
from pathlib import Path
import stat
import tarfile
from tempfile import TemporaryFile
from time import monotonic

from .docker import remaining
from .evidence import CheckError


def regular_kind(path: Path):
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return None
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    if stat.S_ISREG(info.st_mode):
        return "file"
    return None


def validate_backup(backup):
    try:
        if backup.dump.suffix.lower() != ".dump" or regular_kind(backup.dump) != "file":
            raise CheckError("unsupported-dump", "A regular PostgreSQL custom-format .dump file is required.")
        with backup.dump.open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise CheckError("unsupported-dump", "The supplied dump lacks the PostgreSQL custom archive signature.")
        if regular_kind(backup.media) != "directory":
            raise CheckError("unsupported-media", "The media root must be a regular directory.")
    except OSError as exc:
        raise CheckError("backup-unreadable", "The supplied backup could not be read.") from exc
    return "backup-format", "The dump has a PostgreSQL custom archive signature and the media root is readable."


class TimedReader:
    def __init__(self, stream, deadline):
        self.stream = stream
        self.deadline = deadline

    def read(self, size):
        if not remaining(self.deadline):
            raise CheckError("media-copy-timeout", "Copying media exceeded its deadline.")
        return self.stream.read(size)


def copy_media(project, root):
    deadline = monotonic() + project.timeouts.restore
    try:
        with TemporaryFile(dir=project.work) as stream:
            with tarfile.open(fileobj=stream, mode="w") as archive:
                pending = [root]
                while pending:
                    path = pending.pop()
                    if not remaining(deadline):
                        raise CheckError("media-copy-timeout", "Copying media exceeded its deadline.")
                    kind = regular_kind(path)
                    name = path.relative_to(root).as_posix()
                    if kind == "directory":
                        member = tarfile.TarInfo(name)
                        member.type, member.mode = tarfile.DIRTYPE, 0o755
                        archive.addfile(member)
                        pending.extend(sorted(path.iterdir(), reverse=True))
                    elif kind == "file":
                        with path.open("rb") as source:
                            member = tarfile.TarInfo(name)
                            member.size = os.fstat(source.fileno()).st_size
                            member.mode = 0o644
                            archive.addfile(member, TimedReader(source, deadline))
                    else:
                        raise CheckError("unsupported-media", "The media tree contains a link or a special file.")
            stream.seek(0)
            result = project.compose("exec", "-T", "probe", "tar", "-xf", "-", "-C", "/media",
                                     timeout=remaining(deadline), stdin=stream)
    except OSError as exc:
        raise CheckError("media-copy-failed", "The backup media tree could not be copied.") from exc
    except CheckError as exc:
        if exc.code != "command-timeout":
            raise
        raise CheckError("media-copy-timeout", "Copying media exceeded its deadline.") from exc
    if result.code:
        raise CheckError("media-copy-failed", "The probe could not extract media into the project volume.")
    return "media-copied", "The source media was read into a separate project volume."


def restore_database(project, dump):
    try:
        with dump.open("rb") as stream:
            result = project.compose("exec", "-T", "database", "pg_restore", "--username=postgres", "--dbname=immich",
                                     "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl",
                                     timeout=project.timeouts.restore, stdin=stream)
    except OSError as exc:
        raise CheckError("restore-failed", "The database archive could not be opened for restoration.") from exc
    except CheckError as exc:
        if exc.code != "command-timeout":
            raise
        raise CheckError("restore-timeout", "The PostgreSQL restore exceeded its deadline.") from exc
    if result.code:
        raise CheckError("restore-failed", "pg_restore exited unsuccessfully. Restore output was withheld.")
    return "database-restored", "pg_restore committed the archive into the fresh database."
