"""
Test to verify Wonder Guard damage calculation bug.
This test checks if poke-engine correctly calculates 0 damage for moves against Wonder Guard.
"""
import pytest
from fp.search.poke_engine_helpers import get_damage_rolls


def test_knockoff_vs_wonder_guard():
    """
    KNOCKOFF should deal 0 damage to Pokemon with Wonder Guard ability.
    
    In the battle log, Turn 1:
    - Yveltal used Knock Off vs Talonflame (Wonder Guard)
    - Expected: 0 damage
    - Actual from MCTS: High value (Policy 1: 0.709 score)
    
    This test reproduces the scenario to verify if poke-engine
    correctly returns [0, 0] for damage rolls.
    """
    battle_state = (
        "YVELTAL,100,DARK,FLYING,DARK,FLYING,456,456,POISONHEAL,POISONHEAL,TOXICORB,CAREFUL,252;0;0;0;236;20,"
        "298,226,268,320,239,TOXIC,0,0,203,"
        "KNOCKOFF;false;32,WILLOWISP;false;24,KINGSSHIELD;false;16,SLACKOFF;false;16,false,TYPELESS="
        "TALONFLAME,100,FIRE,FLYING,FIRE,FLYING,318,318,WONDERGUARD,WONDERGUARD,UNKNOWNITEM,HARDY,"
        "80;84;72;72;72;128,332,242,215,199,309,NONE,0,0,10,"
        "CHATTER;false;31,SPIKES;false;31,BATONPASS;false;32,SUBSTITUTE;false;32,false,TYPELESS="
    )
    
    # Get damage rolls when Yveltal uses Knock Off against Talonflame
    damage_rolls, attacker_hp = get_damage_rolls(
        battle_state,
        "knockoff",
        "none",
        True
    )
    
    print(f"\n=== Knock Off vs Wonder Guard ===")
    print(f"Move: Knock Off (Physical)")
    print(f"Attacker: Yveltal (DARK/FLYING)")
    print(f"Defender: Talonflame (FIRE/FLYING) with Wonder Guard")
    print(f"Damage Rolls: {damage_rolls}")
    print(f"Expected: [0, 0]")
    
    # This should be [0, 0] because Wonder Guard blocks all damage
    assert damage_rolls == [0, 0], (
        f"❌ BUG: Knock Off dealt {damage_rolls} damage to Wonder Guard Talonflame!\n"
        f"Expected: [0, 0]\n"
        f"This explains why MCTS high-valued knockoff in the actual battle."
    )


def test_scald_vs_wonder_guard():
    """
    SCALD should deal 0 damage to Pokemon with Wonder Guard ability.
    Even though it's super-effective against Fire type, Wonder Guard blocks it.
    """
    battle_state = (
        "GIRATINA,100,GHOST,DRAGON,GHOST,DRAGON,504,504,POISONHEAL,POISONHEAL,TOXICORB,TIMID,"
        "252;0;100;0;0;156,212,301,236,276,280,TOXIC,0,0,750,"
        "SCALD;false;23,DRAGONTAIL;false;16,COTTONGUARD;false;15,MILKDRINK;false;16,false,TYPELESS="
        "TALONFLAME,100,FIRE,FLYING,FIRE,FLYING,318,318,WONDERGUARD,WONDERGUARD,UNKNOWNITEM,HARDY,"
        "80;84;72;72;72;128,332,242,215,199,309,NONE,0,0,10,"
        "CHATTER;false;31,SPIKES;false;31,BATONPASS;false;32,SUBSTITUTE;false;32,false,TYPELESS="
    )
    
    damage_rolls, _ = get_damage_rolls(
        battle_state,
        "scald",
        "none",
        True
    )
    
    print(f"\n=== Scald vs Wonder Guard ===")
    print(f"Move: Scald (Special, WATER type)")
    print(f"Attacker: Giratina (GHOST/DRAGON)")
    print(f"Defender: Talonflame (FIRE/FLYING) with Wonder Guard")
    print(f"Damage Rolls: {damage_rolls}")
    print(f"Expected: [0, 0]")
    
    assert damage_rolls == [0, 0], (
        f"❌ BUG: Scald dealt {damage_rolls} damage to Wonder Guard Talonflame!\n"
        f"Expected: [0, 0]"
    )


def test_status_move_vs_wonder_guard():
    """
    Status moves like Will-O-Wisp should work against Wonder Guard.
    Wonder Guard only blocks direct damage.
    """
    battle_state = (
        "YVELTAL,100,DARK,FLYING,DARK,FLYING,456,456,POISONHEAL,POISONHEAL,TOXICORB,CAREFUL,"
        "252;0;0;0;236;20,298,226,268,320,239,TOXIC,0,0,203,"
        "KNOCKOFF;false;32,WILLOWISP;false;24,KINGSSHIELD;false;16,SLACKOFF;false;16,false,TYPELESS="
        "TALONFLAME,100,FIRE,FLYING,FIRE,FLYING,318,318,WONDERGUARD,WONDERGUARD,UNKNOWNITEM,HARDY,"
        "80;84;72;72;72;128,332,242,215,199,309,NONE,0,0,10,"
        "CHATTER;false;31,SPIKES;false;31,BATONPASS;false;32,SUBSTITUTE;false;32,false,TYPELESS="
    )
    
    damage_rolls, _ = get_damage_rolls(
        battle_state,
        "willowisp",
        "none",
        True
    )
    
    print(f"\n=== Will-O-Wisp vs Wonder Guard ===")
    print(f"Move: Will-O-Wisp (Status, FIRE type, causes burn)")
    print(f"Attacker: Yveltal (DARK/FLYING)")
    print(f"Defender: Talonflame (FIRE/FLYING) with Wonder Guard")
    print(f"Damage Rolls: {damage_rolls}")
    print(f"Expected: [0, 0] (since it's a status move with no damage)")
    
    # Status moves should return [0, 0]
    assert damage_rolls == [0, 0], (
        f"Status move returned non-zero: {damage_rolls}"
    )


if __name__ == "__main__":
    print("=" * 70)
    print("WONDER GUARD DAMAGE CALCULATION TEST")
    print("=" * 70)
    
    try:
        test_knockoff_vs_wonder_guard()
        print("✓ Knock Off test passed")
    except AssertionError as e:
        print(f"✗ Knock Off test FAILED:\n{e}")
    
    try:
        test_scald_vs_wonder_guard()
        print("✓ Scald test passed")
    except AssertionError as e:
        print(f"✗ Scald test FAILED:\n{e}")
    
    try:
        test_status_move_vs_wonder_guard()
        print("✓ Will-O-Wisp test passed")
    except AssertionError as e:
        print(f"✗ Will-O-Wisp test FAILED:\n{e}")
    
    print("=" * 70)
