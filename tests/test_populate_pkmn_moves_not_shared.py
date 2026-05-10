"""
Test to verify that populate_pkmn_from_set doesn't cause moves lists to be shared
between pokemon objects (root cause of moves mixing issue)
"""
import unittest

from fp.battle import Pokemon
from fp.search.helpers import populate_pkmn_from_set
from data.pkmn_sets import (
    PredictedPokemonSet, PokemonSet, PokemonMoveset
)


class TestPopulatePkmnMovesNotShared(unittest.TestCase):
    """
    Verify that when populate_pkmn_from_set is called on multiple pokemon sequentially,
    their moves lists don't get mixed because of shared references.
    """

    def test_moves_lists_not_shared_between_sequential_calls(self):
        """
        Reproduce the root cause: multiple pokemon being populated sequentially
        should NOT share moves list references
        """
        # Create two pokemon objects
        pkmn1 = Pokemon("deoxys", 80)
        pkmn2 = Pokemon("darkrai", 80)

        # Create move sets for each
        moves1 = ["spikes", "taunt", "superpower", "psychoboost"]
        moveset1 = PokemonMoveset(moves=moves1)
        set1 = PredictedPokemonSet(
            pkmn_set=PokemonSet(
                name="deoxys",
                ability="pressure",
                item="focussash",
                nature="jolly",
                evs=[252, 0, 0, 0, 4, 252],
                level=80,
                tera_type=None
            ),
            pkmn_moveset=moveset1
        )

        moves2 = ["darkvoid", "darkpulse", "trick", "icebeam"]
        moveset2 = PokemonMoveset(moves=moves2)
        set2 = PredictedPokemonSet(
            pkmn_set=PokemonSet(
                name="darkrai",
                ability="badreams", 
                item="lifeorb",
                nature="timid",
                evs=[4, 0, 0, 252, 0, 252],
                level=80,
                tera_type=None
            ),
            pkmn_moveset=moveset2
        )

        # Store original moves list ids to verify they don't change
        pkmn1_moves_id_before = id(pkmn1.moves)
        pkmn2_moves_id_before = id(pkmn2.moves)

        # Populate sequentially (this is how they get populated in battles)
        populate_pkmn_from_set(pkmn1, set1, source="test")
        pkmn1_moves_id_after = id(pkmn1.moves)
        
        populate_pkmn_from_set(pkmn2, set2, source="test")
        pkmn2_moves_id_after = id(pkmn2.moves)

        # Verify each pokemon got the correct moves
        pkmn1_actual_moves = [m.name for m in pkmn1.moves]
        pkmn2_actual_moves = [m.name for m in pkmn2.moves]

        self.assertEqual(moves1, pkmn1_actual_moves,
                        f"pkmn1 should have {moves1} but got {pkmn1_actual_moves}")
        self.assertEqual(moves2, pkmn2_actual_moves,
                        f"pkmn2 should have {moves2} but got {pkmn2_actual_moves}")

        # CRITICAL: verify the moves lists are NOT the same object
        self.assertNotEqual(id(pkmn1.moves), id(pkmn2.moves),
                           "pkmn1 and pkmn2 must have different moves list objects!")

        # Verify that populating one doesn't affect the other
        self.assertNotEqual(pkmn1_actual_moves, pkmn2_actual_moves,
                           "Each pokemon should have its own unique moves")

    def test_old_moves_preserved_for_pp_recovery(self):
        """
        Verify that the fix (using copy() instead of direct reference) still
        allows PP recovery from previously known moves
        """
        pkmn = Pokemon("deoxys", 80)
        
        # Add a move and modify its PP to simulate it being used in battle
        move1 = pkmn.add_move("spikes")
        if move1:
            move1.current_pp = 20  # reduced from max
        
        # Now populate with a new set that includes "spikes"
        new_moveset = PokemonMoveset(moves=["spikes", "taunt", "superpower", "psychoboost"])
        new_set = PredictedPokemonSet(
            pkmn_set=PokemonSet(
                name="deoxys",
                ability="pressure",
                item="focussash",
                nature="jolly",
                evs=[252, 0, 0, 0, 4, 252],
                level=80,
                tera_type=None
            ),
            pkmn_moveset=new_moveset
        )
        
        populate_pkmn_from_set(pkmn, new_set, source="test")
        
        # Verify PP was preserved for the move that existed before
        spikes_move = pkmn.get_move("spikes")
        self.assertIsNotNone(spikes_move)
        self.assertEqual(20, spikes_move.current_pp,
                        "PP should be preserved from previous move")
