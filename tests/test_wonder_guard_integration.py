import pytest
from fp.battle import Battle, Pokemon
from fp.search.poke_engine_helpers import (
    battle_to_poke_engine_state,
    poke_engine_get_damage_rolls,
)


def debug_battle_state(battle):
    state = battle_to_poke_engine_state(battle)
    print("\n=== DEBUG POKE-ENGINE STATE ===")
    print(state.to_string())
    print(f"User active ability: {battle.user.active.ability}")
    print(f"Opponent active ability: {battle.opponent.active.ability}")
    print("=== END DEBUG STATE ===\n")
    return state

def test_wonder_guard_knockoff_with_battle_object():
    """
    Verifies that poke-engine correctly calculates 0 damage for a non-super-effective
    move (Knock Off) against a Pokemon with the Wonder Guard ability, using a full
    Battle object to simulate a real scenario.
    """
    # 1. Setup the Battle
    battle = Battle("test-wonder-guard-integration")
    battle.generation = "gen6" # Based on the original battle log

    # 2. Setup the Attacker (Yveltal)
    attacker = Pokemon("yveltal", 100)
    attacker.add_move("knockoff")
    battle.user.active = attacker

    # 3. Setup the Defender (Talonflame with Wonder Guard)
    defender = Pokemon("talonflame", 100)
    defender.ability = "wonderguard"
    battle.opponent.active = defender

    # 4. Call the real damage calculation function
    # We expect poke-engine to handle this.
    damage_rolls, _ = poke_engine_get_damage_rolls(
        battle,
        "knockoff", # Dark type move
        "none",
        True
    )

    # 5. Assert the result
    # Knock Off (Dark) is not super-effective against Talonflame (Fire/Flying),
    # so Wonder Guard should block all damage.
    assert damage_rolls == [0, 0], (
        f"INTEGRATION TEST FAILED: poke-engine calculated {damage_rolls} for Knock Off "
        f"against Wonder Guard. Expected [0, 0]."
    )

def test_wonder_guard_scald_with_battle_object():
    """
    Verifies that poke-engine correctly calculates NON-ZERO damage for a
    super-effective move (Scald) against a Pokemon with the Wonder Guard ability.
    """
    # 1. Setup the Battle
    battle = Battle("test-wonder-guard-integration")
    battle.generation = "gen6"

    # 2. Setup the Attacker (Giratina)
    attacker = Pokemon("giratina", 100)
    attacker.add_move("scald")
    battle.user.active = attacker

    # 3. Setup the Defender (Talonflame with Wonder Guard)
    defender = Pokemon("talonflame", 100)
    defender.ability = "wonderguard"
    battle.opponent.active = defender

    debug_battle_state(battle)

    # 4. Call the real damage calculation function
    damage_rolls, _ = poke_engine_get_damage_rolls(
        battle,
        "scald", # Water type move
        "none",
        True
    )

    # 5. Assert the result
    # Scald (Water) is super-effective against Talonflame (Fire/Flying),
    # so Wonder Guard should NOT block the damage.
    assert damage_rolls != [0, 0], (
        f"INTEGRATION TEST FAILED: poke-engine calculated {damage_rolls} for Scald "
        f"against Wonder Guard. Expected non-zero damage."
    )

def test_wonder_guard_rockslide_with_battle_object():
    """
    Verifies that poke-engine correctly calculates NON-ZERO damage for a super-effective
    move (Rock Slide) against a Pokemon with the Wonder Guard ability.
    """
    # 1. Setup the Battle
    battle = Battle("test-wonder-guard-integration")
    battle.generation = "gen6"

    # 2. Setup the Attacker (Tyranitar)
    attacker = Pokemon("tyranitar", 100)
    attacker.add_move("rockslide")
    battle.user.active = attacker

    # 3. Setup the Defender (Talonflame with Wonder Guard)
    defender = Pokemon("talonflame", 100)
    defender.ability = "wonderguard"
    battle.opponent.active = defender

    # 4. Call the real damage calculation function
    damage_rolls, _ = poke_engine_get_damage_rolls(
        battle,
        "rockslide", # Rock type move
        "none",
        True
    )

    # 5. Assert the result
    # Rock Slide (Rock) IS super-effective against Talonflame (Fire/Flying),
    # so Wonder Guard should NOT block the damage.
    assert damage_rolls != [0, 0], (
        f"INTEGRATION TEST FAILED: poke-engine calculated {damage_rolls} for Rock Slide "
        f"against Wonder Guard. Expected non-zero damage."
    )
