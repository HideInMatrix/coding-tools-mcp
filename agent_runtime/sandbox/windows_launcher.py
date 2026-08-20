from __future__ import annotations

import os
import subprocess
import sys
from typing import Sequence


INTERNAL_WINDOWS_SANDBOX_FLAG = "--internal-windows-sandbox-process"


def _last_error(prefix: str, code: int | None = None) -> OSError:
    import ctypes

    error_code = ctypes.get_last_error() if code is None else int(code)
    return OSError(
        error_code,
        f"{prefix}: {ctypes.FormatError(error_code).strip()}",
    )


def _launch_restricted(argv: Sequence[str]) -> int:
    """Launch argv with a restricted primary token and kill-on-close Job Object.

    The helper intentionally does not alter filesystem ACLs.  The restricted
    token removes privileges and constrains SID access checks, while the Job
    Object guarantees that terminating this helper also terminates the child
    process tree.  Filesystem/network isolation remains a separate capability
    and is reported as unavailable until the AppContainer backend is enabled.
    """

    if os.name != "nt":
        raise RuntimeError("Windows restricted-token launcher requires Windows")
    if not argv:
        raise ValueError("sandbox launcher requires a child command")

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_DUPLICATE = 0x0002
    TOKEN_QUERY = 0x0008
    DISABLE_MAX_PRIVILEGE = 0x00000001
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_SUSPENDED = 0x00000004
    ERROR_PRIVILEGE_NOT_HELD = 1314
    STARTF_USESTDHANDLES = 0x00000100
    STD_INPUT_HANDLE = wintypes.DWORD(-10).value
    STD_OUTPUT_HANDLE = wintypes.DWORD(-11).value
    STD_ERROR_HANDLE = wintypes.DWORD(-12).value
    WIN_BUILTIN_ADMINISTRATORS_SID = 26
    SECURITY_MAX_SID_SIZE = 68
    JOB_OBJECT_BASIC_UI_RESTRICTIONS_CLASS = 4
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_UILIMIT_HANDLES = 0x00000001
    JOB_OBJECT_UILIMIT_READCLIPBOARD = 0x00000002
    JOB_OBJECT_UILIMIT_WRITECLIPBOARD = 0x00000004
    JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS = 0x00000008
    JOB_OBJECT_UILIMIT_DISPLAYSETTINGS = 0x00000010
    JOB_OBJECT_UILIMIT_GLOBALATOMS = 0x00000020
    JOB_OBJECT_UILIMIT_DESKTOP = 0x00000040
    JOB_OBJECT_UILIMIT_EXITWINDOWS = 0x00000080
    HANDLE_FLAG_INHERIT = 0x00000001
    INFINITE = 0xFFFFFFFF
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Sid", ctypes.c_void_p),
            ("Attributes", wintypes.DWORD),
        ]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):
        _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.SetHandleInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.CreateRestrictedToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SID_AND_ATTRIBUTES),
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(SID_AND_ATTRIBUTES),
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.CreateRestrictedToken.restype = wintypes.BOOL
    advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
    advapi32.CreateProcessWithTokenW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    advapi32.CreateProcessWithTokenW.restype = wintypes.BOOL
    advapi32.CreateWellKnownSid.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.CreateWellKnownSid.restype = wintypes.BOOL

    source_token = wintypes.HANDLE()
    restricted_token = wintypes.HANDLE()
    job = wintypes.HANDLE()
    process_info = PROCESS_INFORMATION()
    handles_to_close: list[wintypes.HANDLE] = []
    try:
        access = TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            access,
            ctypes.byref(source_token),
        ):
            raise _last_error("OpenProcessToken failed")
        handles_to_close.append(source_token)

        admin_sid_buffer = ctypes.create_string_buffer(SECURITY_MAX_SID_SIZE)
        admin_sid_size = wintypes.DWORD(SECURITY_MAX_SID_SIZE)
        disabled_sids = None
        disabled_count = 0
        if advapi32.CreateWellKnownSid(
            WIN_BUILTIN_ADMINISTRATORS_SID,
            None,
            admin_sid_buffer,
            ctypes.byref(admin_sid_size),
        ):
            disabled_sids = (SID_AND_ATTRIBUTES * 1)(
                SID_AND_ATTRIBUTES(ctypes.addressof(admin_sid_buffer), 0)
            )
            disabled_count = 1

        if not advapi32.CreateRestrictedToken(
            source_token,
            DISABLE_MAX_PRIVILEGE,
            disabled_count,
            disabled_sids,
            0,
            None,
            0,
            None,
            ctypes.byref(restricted_token),
        ):
            raise _last_error("CreateRestrictedToken failed")
        handles_to_close.append(restricted_token)

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise _last_error("CreateJobObjectW failed")
        handles_to_close.append(job)
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise _last_error("SetInformationJobObject failed")
        ui_restrictions = JOBOBJECT_BASIC_UI_RESTRICTIONS()
        ui_restrictions.UIRestrictionsClass = (
            JOB_OBJECT_UILIMIT_HANDLES
            | JOB_OBJECT_UILIMIT_READCLIPBOARD
            | JOB_OBJECT_UILIMIT_WRITECLIPBOARD
            | JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS
            | JOB_OBJECT_UILIMIT_DISPLAYSETTINGS
            | JOB_OBJECT_UILIMIT_GLOBALATOMS
            | JOB_OBJECT_UILIMIT_DESKTOP
            | JOB_OBJECT_UILIMIT_EXITWINDOWS
        )
        if not kernel32.SetInformationJobObject(
            job,
            JOB_OBJECT_BASIC_UI_RESTRICTIONS_CLASS,
            ctypes.byref(ui_restrictions),
            ctypes.sizeof(ui_restrictions),
        ):
            raise _last_error("SetInformationJobObject(UI restrictions) failed")

        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = STARTF_USESTDHANDLES
        startup.hStdInput = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        startup.hStdOutput = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        startup.hStdError = kernel32.GetStdHandle(STD_ERROR_HANDLE)
        for standard_handle in (
            startup.hStdInput,
            startup.hStdOutput,
            startup.hStdError,
        ):
            if standard_handle and standard_handle != INVALID_HANDLE_VALUE:
                kernel32.SetHandleInformation(
                    standard_handle,
                    HANDLE_FLAG_INHERIT,
                    HANDLE_FLAG_INHERIT,
                )

        command_line = subprocess.list2cmdline(list(argv))
        mutable_command_line = ctypes.create_unicode_buffer(command_line)
        environment_entries = [
            f"{key}={value}"
            for key, value in sorted(
                os.environ.items(),
                key=lambda item: item[0].casefold(),
            )
        ]
        environment_text = "\0".join(environment_entries) + "\0"
        environment_buffer = ctypes.create_unicode_buffer(environment_text)
        environment_pointer = ctypes.cast(environment_buffer, ctypes.c_void_p)
        creation_flags = (
            CREATE_UNICODE_ENVIRONMENT
            | CREATE_NEW_PROCESS_GROUP
            | CREATE_SUSPENDED
        )
        created = bool(advapi32.CreateProcessAsUserW(
            restricted_token,
            str(argv[0]),
            mutable_command_line,
            None,
            None,
            True,
            creation_flags,
            environment_pointer,
            os.getcwd(),
            ctypes.byref(startup),
            ctypes.byref(process_info),
        ))
        if not created:
            as_user_error = ctypes.get_last_error()
            if as_user_error == ERROR_PRIVILEGE_NOT_HELD:
                mutable_command_line = ctypes.create_unicode_buffer(command_line)
                created = bool(advapi32.CreateProcessWithTokenW(
                    restricted_token,
                    0,
                    str(argv[0]),
                    mutable_command_line,
                    creation_flags,
                    environment_pointer,
                    os.getcwd(),
                    ctypes.byref(startup),
                    ctypes.byref(process_info),
                ))
                if not created:
                    token_error = ctypes.get_last_error()
                    raise _last_error(
                        "CreateProcessAsUserW lacked privilege and CreateProcessWithTokenW failed",
                        token_error,
                    )
            else:
                raise _last_error("CreateProcessAsUserW failed", as_user_error)
        handles_to_close.extend([process_info.hThread, process_info.hProcess])

        if not kernel32.AssignProcessToJobObject(job, process_info.hProcess):
            error_code = ctypes.get_last_error()
            kernel32.TerminateProcess(process_info.hProcess, 126)
            kernel32.WaitForSingleObject(process_info.hProcess, 5_000)
            raise _last_error("AssignProcessToJobObject failed", error_code)
        if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
            error_code = ctypes.get_last_error()
            kernel32.TerminateProcess(process_info.hProcess, 126)
            kernel32.WaitForSingleObject(process_info.hProcess, 5_000)
            raise _last_error("ResumeThread failed", error_code)

        kernel32.WaitForSingleObject(process_info.hProcess, INFINITE)
        exit_code = wintypes.DWORD(1)
        if not kernel32.GetExitCodeProcess(
            process_info.hProcess,
            ctypes.byref(exit_code),
        ):
            raise _last_error("GetExitCodeProcess failed")
        return int(exit_code.value)
    finally:
        for handle in reversed(handles_to_close):
            if handle:
                kernel32.CloseHandle(handle)


def run_internal_windows_sandbox_process(arguments: list[str]) -> int:
    argv = list(arguments)
    if argv and argv[0] == "--":
        argv = argv[1:]
    try:
        return _launch_restricted(argv)
    except Exception as exc:
        print(f"Windows sandbox launcher failed: {exc}", file=sys.stderr)
        return 126


def main() -> int:
    return run_internal_windows_sandbox_process(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
