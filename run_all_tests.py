#!/usr/bin/env python3
"""
Test Runner for TelethonAPI
Runs both unit and integration tests without code duplication.
"""

import subprocess
import sys
import os

def run_command(cmd, description):
    """Run a command and return success status."""
    print(f"\n{'='*60}")
    print(f"🧪 {description}")
    print(f"{'='*60}")
    
    try:
        result = subprocess.run(cmd, shell=True, check=True, text=True)
        print(result.stdout)
        if result.stderr:
            print("Warnings:", result.stderr)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ FAILED: {e}")
        print("STDOUT:", e.stdout)
        print("STDERR:", e.stderr)
        return False

def main():
    """Run all TelethonAPI tests."""
    print("🚀 Starting TelethonAPI Test Suite")
    print(f"Working Directory: {os.getcwd()}")
    
    # Check virtual environment
    if "venv" not in sys.prefix:
        print("⚠️  Warning: Virtual environment not detected")
    else:
        print("✅ Virtual environment active")
    
    success_count = 0
    total_tests = 0
    
    # Run unit tests (heavy mocking approach)
    print("\n" + "🔧 UNIT TESTS (Heavy Mocking Approach)")
    if run_command("python -m unittest tests.unit.test_telethon_api -v", 
                   "Running Unit Tests with Heavy Mocking"):
        success_count += 1
    total_tests += 1
    
    # Run integration tests (dummy classes approach)  
    print("\n" + "🔗 INTEGRATION TESTS (Dummy Classes Approach)")
    if run_command("python -m pytest tests/integration/test_telethon_api.py -v",
                   "Running Integration Tests with Dummy Classes"):
        success_count += 1
    total_tests += 1
    
    # Summary
    print(f"\n{'='*60}")
    print(f"📊 TEST SUMMARY")
    print(f"{'='*60}")
    print(f"✅ Successful test suites: {success_count}/{total_tests}")
    
    if success_count == total_tests:
        print("🎉 ALL TESTS PASSED!")
        print("\n📋 Test Coverage:")
        print("   • Unit Tests: Isolated component testing with mocks")
        print("   • Integration Tests: Workflow testing with dummy objects")
        print("   • Combined Coverage: Both approaches complement each other")
        return 0
    else:
        print("❌ Some test suites failed")
        return 1

if __name__ == "__main__":
    exit(main())
