"""
probe_targeted.py — one-shot targeted probes for CS_CalculateInterest and CS_UnlockAccount.
Run as:  python scripts/probe_targeted.py
"""
import ctypes as ct
import os
import sys

DLL = r"C:\Users\evanw\Downloads\mcp-test-binaries\contoso_cs.dll"

_SENTINELS = {
    0xFFFFFFFF: "not_found",
    0xFFFFFFFE: "null_arg",
    0xFFFFFFFD: "not_init",
    0xFFFFFFFC: "locked",
    0xFFFFFFFB: "write_denied",
}


def note(r: int) -> str:
    r32 = r & 0xFFFFFFFF
    return _SENTINELS.get(r32, "SUCCESS" if r32 == 0 else f"0x{r32:08x}")


def free(lib):
    try:
        handle = lib._handle
        ct.windll.kernel32.FreeLibrary(ct.c_void_p(handle))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Section 1 — CS_CalculateInterest: is param_4 a float* instead of uint*?    #
# --------------------------------------------------------------------------- #
print("=== CS_CalculateInterest: uint* vs float* vs double* ===")
lib = ct.WinDLL(DLL)
lib["CS_Initialize"].restype = ct.c_uint
lib["CS_Initialize"]()

fn = lib["CS_CalculateInterest"]
fn.restype = ct.c_uint

combos = [
    (100, 5, 12),
    (1000, 50, 12),
    (25000, 500, 12),
    (25000, 10, 1),
    (10000, 1000, 12),   # 10000 principal, 10%, 12 months
    (100000, 500, 120),  # classic: $1000, 5%, 10 years
    (1, 1, 1),
    (65535, 65535, 65535),
]

for p1, p2, p3 in combos:
    # as uint
    fn.argtypes = [ct.c_uint, ct.c_uint, ct.c_ushort, ct.POINTER(ct.c_uint)]
    out_uint = ct.c_uint(0)
    r = fn(p1, p2, p3, ct.byref(out_uint))
    # as float
    fn.argtypes = [ct.c_uint, ct.c_uint, ct.c_ushort, ct.POINTER(ct.c_float)]
    out_float = ct.c_float(0.0)
    fn(p1, p2, p3, ct.byref(out_float))
    # as double
    fn.argtypes = [ct.c_uint, ct.c_uint, ct.c_ushort, ct.POINTER(ct.c_double)]
    out_double = ct.c_double(0.0)
    fn(p1, p2, p3, ct.byref(out_double))
    print(
        f"  ({p1:6}, {p2:5}, {p3:4}) -> ret={note(r):8}  "
        f"uint={out_uint.value:12}  float={out_float.value:12.4f}  double={out_double.value:12.4f}"
    )

free(lib)
print()

# --------------------------------------------------------------------------- #
# Section 2 — CS_CalculateInterest: swap param order (maybe rate/period/prin) #
# --------------------------------------------------------------------------- #
print("=== CS_CalculateInterest: swapped arg order ===")
lib = ct.WinDLL(DLL)
lib["CS_Initialize"].restype = ct.c_uint
lib["CS_Initialize"]()
fn = lib["CS_CalculateInterest"]
fn.restype = ct.c_uint
fn.argtypes = [ct.c_uint, ct.c_uint, ct.c_ushort, ct.POINTER(ct.c_uint)]
#  Try (rate, principal, period) and (period, rate, principal)
for label, a, b, c in [
    ("rate,prin,per", 500, 25000, 12),
    ("per,rate,prin", 12, 500, 25000),
    ("rate,per,prin", 500, 12, 25000),
]:
    out = ct.c_uint(0)
    r = fn(a, b, c, ct.byref(out))
    print(f"  {label}: ({a},{b},{c}) -> ret={note(r):8}  out={out.value}")
free(lib)
print()

# --------------------------------------------------------------------------- #
# Section 3 — CS_UnlockAccount: param_2 as raw integer in RDX                #
# --------------------------------------------------------------------------- #
print("=== CS_UnlockAccount: param_2 as raw int64 (not a pointer) ===")
lib = ct.WinDLL(DLL)
lib["CS_Initialize"].restype = ct.c_uint
lib["CS_Initialize"]()
fn = lib["CS_UnlockAccount"]
fn.restype = ct.c_uint
fn.argtypes = [ct.c_char_p, ct.c_int64]

pins = [0, 1, 42, 100, 1000, 1234, 4321, 5678, 9999,
        0x1337, 0xAD, 0xFF, 0x100, 0xFFFF, 0x10000]
for cust in [b"CUST-001", b"CUST-002", b"CUST-003", b"CUST-004"]:
    interesting = []
    for pin in pins:
        r = fn(cust, pin)
        n = note(r)
        if n not in ("not_found", "null_arg"):
            interesting.append(f"pin={pin}:{n}")
        if (r & 0xFFFFFFFF) == 0:
            print(f"  *** CRACKED: {cust.decode()} pin={pin} ***")
    if interesting:
        print(f"  {cust.decode()}: {interesting}")
    else:
        print(f"  {cust.decode()}: all not_found/null_arg")
free(lib)
print()

# --------------------------------------------------------------------------- #
# Section 4 — CS_UnlockAccount: param_2 as DWORD* (pointer to uint)          #
# --------------------------------------------------------------------------- #
print("=== CS_UnlockAccount: param_2 as DWORD* ===")
lib = ct.WinDLL(DLL)
lib["CS_Initialize"].restype = ct.c_uint
lib["CS_Initialize"]()
fn = lib["CS_UnlockAccount"]
fn.restype = ct.c_uint
fn.argtypes = [ct.c_char_p, ct.POINTER(ct.c_uint)]
for cust in [b"CUST-001", b"CUST-002", b"CUST-003", b"CUST-004"]:
    for pinval in [0, 1, 1234, 9999, 65535]:
        pb = ct.c_uint(pinval)
        r = fn(cust, ct.byref(pb))
        n = note(r)
        print(f"  {cust.decode()}, *pin={pinval:5} -> {n:12}  pin_after={pb.value}")
        if (r & 0xFFFFFFFF) == 0:
            print("  *** CRACKED via DWORD* ***")
free(lib)
print()

# --------------------------------------------------------------------------- #
# Section 5 — CS_UnlockAccount: what does return 0 actually do?              #
#   Old run showed CUST-001/CUST-002 return 0 sometimes — reproduce it        #
# --------------------------------------------------------------------------- #
print("=== CS_UnlockAccount: wide string sweep (wchar param_2) ===")
lib = ct.WinDLL(DLL)
lib["CS_Initialize"].restype = ct.c_uint
lib["CS_Initialize"]()
fn = lib["CS_UnlockAccount"]
fn.restype = ct.c_uint
fn.argtypes = [ct.c_char_p, ct.c_wchar_p]
tokens = ["admin", "password", "unlock", "1234", "0000", "ADMIN", "root",
          "CUST-001", "alice", "Alice", "Alice Contoso", "contoso"]
for cust in [b"CUST-001", b"CUST-003"]:
    for tok in tokens:
        r = fn(cust, tok)
        n = note(r)
        if n not in ("not_found", "null_arg"):
            print(f"  {cust.decode()}, wchar={tok!r} -> {n}")
        if (r & 0xFFFFFFFF) == 0:
            print(f"  *** CRACKED: wchar {tok!r} ***")
free(lib)
print()

print("Done.")
