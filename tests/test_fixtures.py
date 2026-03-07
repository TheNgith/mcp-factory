"""
test_fixtures.py - Validation tests for zstd and sqlite3 DLL analysis.

Tests that the core discovery pipeline works on known fixtures,
validating PE parsing, header matching, and export deduplication.
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "discovery"))

import pytest
from schema import ExportedFunc
from pe_parse import read_pe_exports
from exports import deduplicate_exports
from headers_scan import scan_headers


class TestFixtures:
    """Test suite for fixture validation."""
    
    FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
    ZSTD_DLL = FIXTURE_DIR / "vcpkg_installed" / "x64-windows" / "bin" / "zstd.dll"
    ZSTD_HEADERS = FIXTURE_DIR / "vcpkg_installed" / "x64-windows" / "include"
    SQLITE_DLL = FIXTURE_DIR / "vcpkg_installed" / "x64-windows" / "bin" / "sqlite3.dll"
    SQLITE_HEADERS = FIXTURE_DIR / "vcpkg_installed" / "x64-windows" / "include"
    
    def test_zstd_exports_exist(self):
        """Verify zstd.dll exports can be extracted."""
        if not self.ZSTD_DLL.exists():
            pytest.skip(f"{self.ZSTD_DLL} not found (Windows-only fixture)")
        
        exports, success = read_pe_exports(self.ZSTD_DLL)
        assert success, "zstd.dll export extraction failed"
        assert len(exports) > 0, "zstd.dll has no exports"
        assert any('ZSTD_' in e.name for e in exports), "No ZSTD_ prefixed exports found"
        print(f"[OK] zstd.dll: {len(exports)} exports found")
    
    def test_zstd_header_matching(self):
        """Verify zstd.dll exports match header prototypes."""
        if not self.ZSTD_DLL.exists():
            pytest.skip(f"{self.ZSTD_DLL} not found (Windows-only fixture)")
        if not self.ZSTD_HEADERS.exists():
            pytest.skip(f"{self.ZSTD_HEADERS} not found (Windows-only fixture)")
        
        exports, success = read_pe_exports(self.ZSTD_DLL)
        assert success, "Failed to get exports"
        
        exports = deduplicate_exports(exports)
        matches = scan_headers(self.ZSTD_HEADERS, exports)
        
        match_count = sum(1 for e in exports if e.name in matches)
        match_rate = (match_count / len(exports) * 100) if exports else 0
        
        assert match_count > 0, "No header matches found for zstd.dll"
        assert match_rate >= 95, f"Header match rate too low: {match_rate:.1f}%"
        print(f"[OK] zstd.dll: {match_count}/{len(exports)} exports matched ({match_rate:.1f}%)")
    
    def test_sqlite3_exports_exist(self):
        """Verify sqlite3.dll exports can be extracted."""
        if not self.SQLITE_DLL.exists():
            pytest.skip(f"{self.SQLITE_DLL} not found (Windows-only fixture)")
        
        exports, success = read_pe_exports(self.SQLITE_DLL)
        assert success, "sqlite3.dll export extraction failed"
        assert len(exports) > 100, "sqlite3.dll has suspiciously few exports"
        assert any('sqlite3_' in e.name for e in exports), "No sqlite3_ prefixed exports found"
        print(f"[OK] sqlite3.dll: {len(exports)} exports found")
    
    def test_sqlite3_header_matching(self):
        """Verify sqlite3.dll exports match header prototypes."""
        if not self.SQLITE_DLL.exists():
            pytest.skip(f"{self.SQLITE_DLL} not found (Windows-only fixture)")
        if not self.SQLITE_HEADERS.exists():
            pytest.skip(f"{self.SQLITE_HEADERS} not found (Windows-only fixture)")
        
        exports, success = read_pe_exports(self.SQLITE_DLL)
        assert success, "Failed to get exports"
        
        exports = deduplicate_exports(exports)
        matches = scan_headers(self.SQLITE_HEADERS, exports)
        
        match_count = sum(1 for e in exports if e.name in matches)
        match_rate = (match_count / len(exports) * 100) if exports else 0
        
        assert match_count > 0, "No header matches found for sqlite3.dll"
        assert match_rate >= 90, f"Header match rate too low: {match_rate:.1f}%"
        print(f"[OK] sqlite3.dll: {match_count}/{len(exports)} exports matched ({match_rate:.1f}%)")
    
    def test_deduplication(self):
        """Verify export deduplication works correctly."""
        # Create test exports with duplicates
        exports = [
            ExportedFunc(name="func1", ordinal=1, hint="0"),
            ExportedFunc(name="func1", ordinal=2, hint="1"),  # Duplicate
            ExportedFunc(name="func2", ordinal=3, hint="2"),
        ]
        
        deduplicated = deduplicate_exports(exports)
        
        assert len(deduplicated) == 2, f"Expected 2 unique exports, got {len(deduplicated)}"
        assert all(e.ordinal for e in deduplicated), "Deduplication lost ordinal data"
        print(f"[OK] Deduplication: 3 exports -> {len(deduplicated)} unique")


def run_tests():
    """Run all tests."""
    print("[INFO] Starting fixture validation tests")
    print("")
    
    test_suite = TestFixtures()
    tests = [
        test_suite.test_zstd_exports_exist,
        test_suite.test_zstd_header_matching,
        test_suite.test_sqlite3_exports_exist,
        test_suite.test_sqlite3_header_matching,
        test_suite.test_deduplication,
    ]
    
    passed = 0
    failed = 0
    skipped = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {test.__name__}: {e}")
            failed += 1
    
    print("")
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
