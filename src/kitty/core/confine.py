"""Confining the calling process before it becomes an agent: Landlock plus seccomp.

Paper §13 step 1 ("closure, per process, derived from the API boundary"). The agent
launcher (kitty.agent_launch) calls `apply` on itself, exchanges its launch nonce with
rika! and then execs the agent, which inherits everything applied here; nothing can be
lifted afterwards.

Landlock is additive: a path is reachable when any rule on it or on one of its parents
grants the access. So no rule ever names a directory that holds a denied path. `plan`
expands such a directory into its children, recursively, and leaves the denied paths
out; a denied path is the resolved database file with its ``-wal``/``-shm``/
``-journal`` files, rika!'s private and data folders, sensitive configuration, resumes
and the socket folders of rika!'s herdr and Tern sessions (kitty.core.agent_sessions).
An expanded directory keeps only ``READ_DIR``: it can be listed, and because that right
reaches its whole subtree the *names* inside a denied folder can be listed too; no file
in one can be opened, executed, written or created.

What each Landlock ABI adds (the highest the kernel offers is used and reported):

====  =====================================================================
ABI   rights
====  =====================================================================
1     files and directories (read, write, execute, create, remove); a
      confined process can never ptrace a process outside its domain
2     ``REFER`` (linking/renaming across directories)
3     ``TRUNCATE``
4     TCP ``connect`` by port
5     ``IOCTL_DEV`` on devices opened after confinement
6     scopes: abstract UNIX sockets and signals to processes outside the domain
9     ``RESOLVE_UNIX``: connecting to pathname UNIX sockets (herdr, Tern, D-Bus,
      X11, Wayland, ssh-agent) created outside the domain
10    UDP ``connect``/``sendto`` by port
====  =====================================================================

Landlock's device-ioctl right covers only descriptors opened after confinement, and the
agent inherits its terminal, so a seccomp filter refuses ``TIOCSTI`` (pushing input
into the terminal, typed by whatever reads it next) and ``TIOCLINUX`` on every
descriptor with ``EPERM`` (x86-64, its i386 compat mode and arm64; x32 system calls are
refused outright).

TCP bind is not handled: agents may run local servers. Port rules are per port, not per
address, so the allowed ports (rika!'s own and the model APIs' 443) are reachable on
any host.
"""
from __future__ import annotations

import ctypes
import errno
import os
import platform
import stat
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# Landlock system calls (the same numbers on x86-64 and arm64: the generic table).
_SYS_CREATE_RULESET, _SYS_ADD_RULE, _SYS_RESTRICT_SELF = 444, 445, 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH, _RULE_NET_PORT = 1, 2
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER = 22, 2

FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13
FS_TRUNCATE = 1 << 14
FS_IOCTL_DEV = 1 << 15
FS_RESOLVE_UNIX = 1 << 16
NET_CONNECT_TCP = 1 << 1
NET_CONNECT_SEND_UDP = 1 << 3
SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0
SCOPE_SIGNAL = 1 << 1

#: Rights a rule on a file (not a directory) may carry.
FILE_RIGHTS = FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE | FS_IOCTL_DEV
#: Read-only leaves: read, list, execute.
READ = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
#: Writable leaves: everything but device nodes and connecting to outside sockets.
WRITE = (READ | FS_WRITE_FILE | FS_REMOVE_DIR | FS_REMOVE_FILE | FS_MAKE_DIR | FS_MAKE_REG | FS_MAKE_SOCK
         | FS_MAKE_FIFO | FS_MAKE_SYM | FS_REFER | FS_TRUNCATE)
#: Devices a terminal program opens again (``/dev/tty``, ptys, ``/dev/null``).
DEVICE = FS_READ_FILE | FS_WRITE_FILE | FS_READ_DIR | FS_IOCTL_DEV
#: The ABI that closes every route the paper's kernel matrix names (pathname sockets).
FULL_ABI = 9

TIOCSTI = 0x5412
TIOCLINUX = 0x541C
#: (audit arch, ioctl system call number) per machine; x32 calls carry bit 30 on x86-64.
_AUDIT_X86_64, _AUDIT_I386, _AUDIT_AARCH64 = 0xC000003E, 0x40000003, 0xC00000B7
_X32_BIT = 0x40000000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_SECCOMP_RET_ERRNO = 0x00050000


class ConfineError(OSError):
    """Confinement could not be applied; the message says which step failed."""


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def abi() -> int:
    """The highest Landlock ABI this kernel offers; 0 when Landlock is missing or disabled."""
    if platform.system() != "Linux":
        return 0
    libc = _libc()
    libc.syscall.restype = ctypes.c_long
    version = libc.syscall(ctypes.c_long(_SYS_CREATE_RULESET), None, ctypes.c_size_t(0),
                           ctypes.c_uint32(_CREATE_RULESET_VERSION))
    return int(version) if version > 0 else 0


def handled_fs(version: int) -> int:
    rights = (1 << 13) - 1  # ABI 1: execute .. make_sym
    if version >= 2:
        rights |= FS_REFER
    if version >= 3:
        rights |= FS_TRUNCATE
    if version >= 5:
        rights |= FS_IOCTL_DEV
    if version >= 9:
        rights |= FS_RESOLVE_UNIX
    return rights


def handled_net(version: int) -> int:
    return (NET_CONNECT_TCP if version >= 4 else 0) | (NET_CONNECT_SEND_UDP if version >= 10 else 0)


def scoped(version: int) -> int:
    return SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL if version >= 6 else 0


# ------------------------------------------------------------------------------ plan

@dataclass(frozen=True)
class Plan:
    """What `apply` grants: ``rules`` are ``(resolved path, rights)``; nothing else is reachable."""

    rules: tuple[tuple[str, int], ...]
    tcp: tuple[int, ...] = ()
    udp: tuple[int, ...] = ()
    #: The resolved paths no rule may reach.
    deny: tuple[str, ...] = ()
    #: Requested paths that do not exist (nothing to grant).
    missing: tuple[str, ...] = ()


def within(path: str, parent: str) -> bool:
    """`path` is `parent` or lies beneath it (both resolved)."""
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def _resolved(paths: Iterable[str]) -> list[str]:
    out: list[str] = []
    for path in paths:
        for variant in {os.path.abspath(path), os.path.realpath(path)}:
            if variant not in out:
                out.append(variant)
    return out


def plan(grants: Sequence[tuple[str, int]], deny: Iterable[str], *, readonly: Iterable[str] = (),
         exceptions: Sequence[tuple[str, int]] = (), tcp: Iterable[int] = (), udp: Iterable[int] = ()) -> Plan:
    """Leaf rules for `grants` (``(path, rights)``) that never reach a `deny` path.

    A granted directory that holds a denied (or `readonly`) path is replaced by its
    children (with ``READ_DIR`` kept on the directory itself), recursively; a symbolic
    link is followed to its resolved target, which must pass the same test. A granted
    path inside a denied one is dropped, and one inside a `readonly` path keeps only
    `READ`. `exceptions` are exact leaves granted even inside a denied folder (rika!'s
    hook assets and activity folder in ``.private/agents``), unless one holds a denied
    path itself.
    """
    blocked = _resolved(deny)
    protected = _resolved(readonly)
    special = blocked + protected
    rules: dict[str, int] = {}
    missing: list[str] = []

    def add(path: str, rights: int) -> None:
        rules[path] = rules.get(path, 0) | rights

    def expand(path: str, rights: int, seen: frozenset[str]) -> None:
        real = os.path.realpath(path)
        if real in seen:
            return
        try:
            info = os.stat(real)
        except OSError:
            missing.append(path)
            return
        if any(within(real, item) for item in blocked):
            return
        if any(within(real, item) for item in protected):
            rights &= READ
        directory = stat.S_ISDIR(info.st_mode)
        if not directory:
            add(real, rights & FILE_RIGHTS)
            return
        if not any(within(item, real) and item != real for item in special):
            add(real, rights)
            return
        if rights & FS_READ_DIR:
            add(real, FS_READ_DIR)
        try:
            children = sorted(os.scandir(real), key=lambda entry: entry.name)
        except OSError:
            return
        for child in children:
            expand(child.path, rights, seen | {real})

    for path, rights in grants:
        expand(path, rights, frozenset())
    for path, rights in exceptions:
        real = os.path.realpath(path)
        if not os.path.exists(real):
            missing.append(path)
        elif not any(within(item, real) for item in blocked if item != real):
            add(real, rights if os.path.isdir(real) else rights & FILE_RIGHTS)
    return Plan(rules=tuple(sorted(rules.items())), tcp=tuple(sorted(set(tcp))), udp=tuple(sorted(set(udp))),
                deny=tuple(blocked), missing=tuple(missing))


def reaches(plan_: Plan, path: str) -> list[str]:
    """Rules of `plan_` that grant more than listing on `path` (an empty list is the goal)."""
    real = os.path.realpath(path)
    return [rule for rule, rights in plan_.rules if within(real, rule) and rights & ~FS_READ_DIR]


# ------------------------------------------------------------------------------ apply

def _check(result: int, what: str) -> int:
    if result < 0:
        code = ctypes.get_errno()
        raise ConfineError(code, f"{what}: {os.strerror(code)}")
    return result


def _landlock(plan_: Plan, version: int) -> int:
    libc = _libc()
    libc.syscall.restype = ctypes.c_long
    fs, net, scope = handled_fs(version), handled_net(version), scoped(version)
    fields = [fs] + ([net] if version >= 4 else []) + ([scope] if version >= 6 else [])
    attr = struct.pack(f"={len(fields)}Q", *fields)
    buffer = ctypes.create_string_buffer(attr, len(attr))
    ruleset = _check(libc.syscall(ctypes.c_long(_SYS_CREATE_RULESET), buffer, ctypes.c_size_t(len(attr)),
                                  ctypes.c_uint32(0)), "landlock_create_ruleset")
    count = 0
    try:
        for path, rights in plan_.rules:
            descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
                allowed = rights & fs & (~0 if directory else FILE_RIGHTS)
                if not allowed:
                    continue
                rule = ctypes.create_string_buffer(struct.pack("=Qi", allowed, descriptor), 12)
                _check(libc.syscall(ctypes.c_long(_SYS_ADD_RULE), ctypes.c_int(ruleset), ctypes.c_int(_RULE_PATH_BENEATH),
                                    rule, ctypes.c_uint32(0)), f"landlock_add_rule {path}")
                count += 1
            finally:
                os.close(descriptor)
        for ports, right in ((plan_.tcp, NET_CONNECT_TCP), (plan_.udp, NET_CONNECT_SEND_UDP)):
            if not net & right:
                continue
            for port in ports:
                rule = ctypes.create_string_buffer(struct.pack("=QQ", right, port), 16)
                _check(libc.syscall(ctypes.c_long(_SYS_ADD_RULE), ctypes.c_int(ruleset), ctypes.c_int(_RULE_NET_PORT),
                                    rule, ctypes.c_uint32(0)), f"landlock_add_rule port {port}")
                count += 1
        _check(libc.syscall(ctypes.c_long(_SYS_RESTRICT_SELF), ctypes.c_int(ruleset), ctypes.c_uint32(0)),
               "landlock_restrict_self")
    finally:
        os.close(ruleset)
    return count


def _bpf(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("=HBBI", code, jt, jf, k)


def seccomp_program(machine: str | None = None) -> bytes:
    """The BPF filter: ``ioctl(fd, TIOCSTI|TIOCLINUX, ...)`` fails with EPERM; x32 calls too."""
    machine = (machine or platform.machine()).lower()
    load, jeq, jset, ret = 0x20, 0x15, 0x45, 0x06
    arch_word, nr_word, request_word = 4, 0, 24  # seccomp_data: nr, arch, ip, args[0], args[1] (low word)
    if machine in {"x86_64", "amd64"}:
        arches = [(_AUDIT_X86_64, 16, True), (_AUDIT_I386, 54, False)]
    elif machine in {"aarch64", "arm64"}:
        arches = [(_AUDIT_AARCH64, 29, False)]
    else:
        raise ConfineError(errno.ENOTSUP, f"no TIOCSTI filter for {machine}")
    deny = _SECCOMP_RET_ERRNO | errno.EPERM
    # Each arch block, with jumps resolved to the shared allow/deny returns at the end.
    blocks: list[list[tuple[int, int, int, int, str | None, str | None]]] = []
    for arch, ioctl, x32 in arches:
        block = [(jeq, 0, 0, arch, None, "next"), (load, 0, 0, nr_word, None, None)]
        if x32:
            block.append((jset, 0, 0, _X32_BIT, "deny", None))
        block += [(jeq, 0, 0, ioctl, None, "allow"), (load, 0, 0, request_word, None, None),
                  (jeq, 0, 0, TIOCSTI, "deny", None), (jeq, 0, 0, TIOCLINUX, "deny", "allow")]
        blocks.append(block)
    flat: list[tuple[int, int, int, int, str | None, str | None, int]] = [(load, 0, 0, arch_word, None, None, -1)]
    starts = []
    for index, block in enumerate(blocks):
        starts.append(len(flat))
        flat += [(*item, index) for item in block]
    allow_at, deny_at = len(flat), len(flat) + 1
    program = b""
    for position, (code, _jt, _jf, k, true, false, index) in enumerate(flat):
        def target(name: str | None) -> int:
            if name is None:
                return 0
            destination = {"allow": allow_at, "deny": deny_at}.get(name)
            if destination is None:  # "next": the next arch's block, or allow after the last one
                destination = starts[index + 1] if index + 1 < len(starts) else allow_at
            return destination - position - 1
        program += _bpf(code, target(true), target(false), k)
    return program + _bpf(ret, 0, 0, _SECCOMP_RET_ALLOW) + _bpf(ret, 0, 0, deny)


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]


def _seccomp() -> None:
    program = seccomp_program()
    filters = ctypes.create_string_buffer(program, len(program))
    fprog = _SockFprog(len(program) // 8, ctypes.addressof(filters))
    libc = _libc()
    _check(libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0), "seccomp")


@dataclass
class Report:
    """What was applied to this process (sent to rika! in the launch exchange)."""

    abi: int = 0
    landlock: bool = False
    rules: int = 0
    seccomp: bool = False
    no_new_privs: bool = False
    error: str | None = None
    closed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"abi": self.abi, "landlock": self.landlock, "rules": self.rules, "seccomp": self.seccomp,
                "no_new_privs": self.no_new_privs, "error": self.error, "closed": list(self.closed)}


def closures(version: int) -> list[str]:
    """The routes a Landlock domain of `version` closes (beyond files and ptrace, from ABI 1)."""
    found = ["files", "ptrace"] if version >= 1 else []
    if version >= 4:
        found.append("tcp")
    if version >= 5:
        found.append("device-ioctl")
    if version >= 6:
        found += ["abstract-unix-sockets", "signals"]
    if version >= 9:
        found.append("unix-sockets")
    if version >= 10:
        found.append("udp")
    return found


def apply(plan_: Plan, *, version: int | None = None) -> Report:
    """Confine the calling process (and everything it execs) to `plan_`; raises ConfineError."""
    report = Report(abi=abi() if version is None else version)
    libc = _libc()
    _check(libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "no_new_privs")
    report.no_new_privs = True
    if report.abi < 1:
        raise ConfineError(errno.ENOSYS, "Landlock is not available on this kernel (or is disabled)")
    report.rules = _landlock(plan_, report.abi)
    report.landlock = True
    report.closed = closures(report.abi)
    _seccomp()
    report.seccomp = True
    report.closed.append("tiocsti")
    return report


# -------------------------------------------------------------------------- guarantee

#: The guarantee levels the UI names (paper §13's kernel matrix).
FULL, ATTRIBUTION = "full", "attribution"
REQUIRED = ("files", "ptrace", "tcp", "abstract-unix-sockets", "signals", "unix-sockets", "tiocsti")
_OPEN = {
    "files": "the database, rika!'s private files, configuration and resumes can be opened directly",
    "ptrace": "rika! can be traced",
    "tcp": "any TCP port can be reached",
    "abstract-unix-sockets": "abstract UNIX sockets (X11, D-Bus) outside the tab can be reached",
    "signals": "rika! and other processes outside the tab can be signalled",
    "unix-sockets": "pathname UNIX sockets (herdr's and Tern's, which open unconfined panes; D-Bus, systemd-run, "
                    "Wayland, X11) can be reached: Landlock closes them from ABI 9 (Linux 7.1)",
    "tiocsti": "input can be pushed into the terminal (TIOCSTI/TIOCLINUX)",
}


def guarantee(closed: Iterable[str]) -> tuple[str, list[str]]:
    """``(level, routes still open)``: Full only when every route the paper enumerates is closed."""
    have = set(closed)
    open_routes = [_OPEN[item] for item in REQUIRED if item not in have]
    return (FULL if not open_routes else ATTRIBUTION), open_routes
