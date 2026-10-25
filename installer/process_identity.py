"""Read-only limited-query fallback when WMI omits process identity."""
import ctypes
from ctypes import wintypes as w


def limited_identity(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    native = ctypes.WinDLL('ntdll')
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    native.NtQueryInformationProcess.argtypes = [w.HANDLE, w.ULONG, w.LPVOID, w.ULONG, ctypes.POINTER(w.ULONG)]
    class UnicodeString(ctypes.Structure):
        _fields_ = [('Length', w.USHORT), ('MaximumLength', w.USHORT), ('Buffer', w.LPVOID)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ValueError('Cannot query process identity: '+str(pid))
    try:
        path = ctypes.create_unicode_buffer(32768)
        size = w.DWORD(len(path))
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            raise ValueError('Cannot query executable: '+str(pid))
        needed = w.ULONG()
        native.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(needed))
        if not ctypes.sizeof(UnicodeString) <= needed.value <= 131072:
            raise ValueError('Invalid command-line query length: '+str(pid))
        buffer = ctypes.create_string_buffer(needed.value)
        status = native.NtQueryInformationProcess(handle, 60, buffer, needed.value, ctypes.byref(needed))
        if status != 0:
            raise ValueError('Cannot query command line: '+str(pid))
        value = UnicodeString.from_buffer(buffer)
        lo = ctypes.addressof(buffer)
        if value.Length % 2 or not value.Buffer or not lo <= value.Buffer <= value.Buffer+value.Length <= lo+len(buffer):
            raise ValueError('Invalid command-line query result: '+str(pid))
        command = ctypes.wstring_at(value.Buffer, value.Length//2)
        if not command:
            raise ValueError('Empty command line: '+str(pid))
        return path.value, command
    finally:
        kernel.CloseHandle(handle)
