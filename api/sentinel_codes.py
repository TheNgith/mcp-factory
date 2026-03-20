from __future__ import annotations

# Base fallback meanings used across exploration and execution.
SENTINEL_DEFAULTS: dict[int, str] = {
    0xFFFFFFFF: "not found / invalid input",
    0xFFFFFFFE: "null argument",
    0xFFFFFFFD: "not initialized",
    0xFFFFFFFC: "account locked or suspended",
    0xFFFFFFFB: "write operation denied",
}

COMMON_WIN32_ERRORS: dict[int, str] = {
    2: "ERROR_FILE_NOT_FOUND",
    3: "ERROR_PATH_NOT_FOUND",
    5: "ERROR_ACCESS_DENIED",
    6: "ERROR_INVALID_HANDLE",
    13: "ERROR_INVALID_DATA",
    32: "ERROR_SHARING_VIOLATION",
    87: "ERROR_INVALID_PARAMETER",
    122: "ERROR_INSUFFICIENT_BUFFER",
    183: "ERROR_ALREADY_EXISTS",
    995: "ERROR_OPERATION_ABORTED",
}

COMMON_HRESULTS: dict[int, str] = {
    0x80004001: "E_NOTIMPL",
    0x80004002: "E_NOINTERFACE",
    0x80004003: "E_POINTER",
    0x80004004: "E_ABORT",
    0x80004005: "E_FAIL",
    0x80070005: "E_ACCESSDENIED (HRESULT_FROM_WIN32(ERROR_ACCESS_DENIED))",
    0x80070057: "E_INVALIDARG (HRESULT_FROM_WIN32(ERROR_INVALID_PARAMETER))",
}

COMMON_NTSTATUS: dict[int, str] = {
    0xC0000001: "STATUS_UNSUCCESSFUL",
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC0000008: "STATUS_INVALID_HANDLE",
    0xC0000022: "STATUS_ACCESS_DENIED",
    0xC0000034: "STATUS_OBJECT_NAME_NOT_FOUND",
    0xC0000035: "STATUS_OBJECT_NAME_COLLISION",
    0xC000000D: "STATUS_INVALID_PARAMETER",
}


def classify_common_result_code(code: int) -> str | None:
    """Return deterministic meaning for common Windows code families.

    Covers known sentinels, well-known HRESULT/Win32/NTSTATUS values,
    and conservative family-level labels for unknown high-bit failures.
    """
    code = int(code) & 0xFFFFFFFF

    if code in SENTINEL_DEFAULTS:
        return SENTINEL_DEFAULTS[code]

    if code in COMMON_HRESULTS:
        return f"HRESULT: {COMMON_HRESULTS[code]}"

    if code in COMMON_NTSTATUS:
        return f"NTSTATUS: {COMMON_NTSTATUS[code]}"

    if code <= 0xFFFF and code in COMMON_WIN32_ERRORS:
        return f"Win32: {COMMON_WIN32_ERRORS[code]}"

    facility = (code >> 16) & 0x1FFF
    low = code & 0xFFFF
    if facility == 7 and low in COMMON_WIN32_ERRORS:
        return (
            "HRESULT_FROM_WIN32(" + COMMON_WIN32_ERRORS[low] + ")"
            f" (0x{code:08X})"
        )

    if (code & 0xC0000000) == 0xC0000000:
        return f"NTSTATUS-like failure (0x{code:08X})"

    if (code & 0x80000000) == 0x80000000:
        return f"HRESULT-like failure (facility={facility}, code=0x{low:04X})"

    return None
