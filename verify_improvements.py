#!/usr/bin/env python3
"""
Quick verification script for new improvements
"""
import sys
sys.path.insert(0, '/workspaces/foul-play')

import constants
from config import FoulPlayConfig

# Test 1: Check SLEEP_COUNT constant
print("✓ Test 1: SLEEP_COUNT constant exists")
assert hasattr(constants, 'SLEEP_COUNT'), "SLEEP_COUNT not found"
assert constants.SLEEP_COUNT == "sleep_count", f"SLEEP_COUNT value wrong: {constants.SLEEP_COUNT}"
print(f"  SLEEP_COUNT = '{constants.SLEEP_COUNT}'")

# Test 2: Check new config attributes
print("\n✓ Test 2: Dynamic search config attributes")
config = FoulPlayConfig
assert hasattr(config, 'state_search_depth'), "state_search_depth not found"
assert hasattr(config, 'dynamic_search_enabled'), "dynamic_search_enabled not found"
assert hasattr(config, 'dynamic_search_battle_threshold'), "dynamic_search_battle_threshold not found"
assert hasattr(config, 'expected_mods'), "expected_mods not found"
assert hasattr(config, 'allow_tera_to_stellar_type'), "allow_tera_to_stellar_type not found"
print("  ✓ All config attributes present")

# Test 3: Verify move selection imports
print("\n✓ Test 3: Move selection module imports")
try:
    from fp.search.main import (
        calculate_dynamic_search_depth,
        get_search_time_for_depth,
        select_move_from_mcts_results,
    )
    print("  ✓ All new functions imported successfully")
except ImportError as e:
    print(f"  ✗ Import failed: {e}")
    sys.exit(1)

# Test 4: Test dynamic search depth calculation
print("\n✓ Test 4: Dynamic search depth calculation")
FoulPlayConfig.dynamic_search_enabled = True
FoulPlayConfig.dynamic_search_battle_threshold = 20

depth_100 = calculate_dynamic_search_depth(100)
depth_10 = calculate_dynamic_search_depth(10)
depth_35 = calculate_dynamic_search_depth(35)

print(f"  Battle #10 (early): depth = {depth_10} (expected: 1)")
print(f"  Battle #35 (mid): depth = {depth_35} (expected: 2)")
print(f"  Battle #100 (late): depth = {depth_100} (expected: 3)")

assert depth_10 == 1, f"Early game depth wrong: {depth_10}"
assert depth_35 == 2, f"Mid game depth wrong: {depth_35}"
assert depth_100 == 3, f"Late game depth wrong: {depth_100}"
print("  ✓ Depth calculation correct")

# Test 5: Test search time calculation
print("\n✓ Test 5: Search time calculation for depths")
base_time = 100
time_d1 = get_search_time_for_depth(base_time, 1)
time_d2 = get_search_time_for_depth(base_time, 2)
time_d3 = get_search_time_for_depth(base_time, 3)
time_d4 = get_search_time_for_depth(base_time, 4)

print(f"  Depth 1: {time_d1}ms (expected ~25)")
print(f"  Depth 2: {time_d2}ms (expected ~50)")
print(f"  Depth 3: {time_d3}ms (expected ~75)")
print(f"  Depth 4: {time_d4}ms (expected ~100)")

assert time_d1 <= 25, f"Depth 1 time too high: {time_d1}"
assert 45 <= time_d2 <= 55, f"Depth 2 time wrong: {time_d2}"
assert 70 <= time_d3 <= 80, f"Depth 3 time wrong: {time_d3}"
assert time_d4 == base_time, f"Depth 4 time should equal base: {time_d4}"
print("  ✓ Search time allocation correct")

print("\n" + "="*50)
print("✓ All verification tests passed!")
print("="*50)
print("\nIntegrated improvements summary:")
print("  • Dynamic search depth (adaptive MCTS)")
print("  • Sleep/Rest Talk tracking")
print("  • Hazard stacking prevention")
print("  • Trick Room awareness")
print("  • New config options")
print("\nTo test full functionality, run:")
print("  python run.py --help")
print("\nFor detailed info, see: IMPROVEMENTS.md")
