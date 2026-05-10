import logging
import random
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy

import constants
from data import all_move_json
from constants import BattleType
from fp.battle import Battle, Pokemon
from fp.helpers import type_effectiveness_modifier
from config import FoulPlayConfig
from .standard_battles import prepare_battles
from .random_battles import prepare_random_battles

from poke_engine import State as PokeEngineState, monte_carlo_tree_search, MctsResult

from fp.search.poke_engine_helpers import battle_to_poke_engine_state

logger = logging.getLogger(__name__)


def _max_incoming_effectiveness(attacker_moves, defender_types):
    max_eff = 0
    for mv in attacker_moves:
        move_name = mv.name
        if move_name not in all_move_json:
            continue
        move_data = all_move_json[move_name]
        if move_data.get(constants.CATEGORY) not in constants.DAMAGING_CATEGORIES:
            continue
        move_type = move_data.get(constants.TYPE)
        if not move_type:
            continue
        try:
            eff = type_effectiveness_modifier(move_type, defender_types)
        except Exception:
            continue
        max_eff = max(max_eff, eff)
    return max_eff


def _impostor_switch_multiplier(battle: Battle, target_pkmn):
    opp = battle.opponent.active
    user_active = battle.user.active
    if opp is None or user_active is None:
        return 1.0

    opp_is_transformed = (
        constants.TRANSFORM in opp.volatile_statuses
        or (opp.original_ability or "").lower() == "impostor"
    )
    if not opp_is_transformed:
        return 1.0

    opp_moves = opp.moves or []
    if len(opp_moves) == 0:
        return 1.0

    stay_eff = _max_incoming_effectiveness(opp_moves, user_active.types)
    switch_eff = _max_incoming_effectiveness(opp_moves, target_pkmn.types)

    if switch_eff == 0:
        return 1.8
    if stay_eff >= 2 and switch_eff <= 1:
        return 1.6
    if switch_eff < stay_eff:
        return 1.35
    return 1.0


def _switch_out_move_multiplier(battle: Battle):
    active = battle.user.active
    opponent = battle.opponent.active
    if active is None or opponent is None:
        return 1.0

    if not any(m.name in constants.SWITCH_OUT_MOVES for m in (active.moves or [])):
        return 1.0

    try:
        active_speed = active.calculate_boosted_stats()[constants.SPEED]
        opponent_speed = opponent.calculate_boosted_stats()[constants.SPEED]
    except Exception:
        active_speed = None
        opponent_speed = None

    incoming_eff = _max_incoming_effectiveness(opponent.moves or [], active.types)
    outgoing_eff = _max_incoming_effectiveness(active.moves or [], opponent.types)

    hp_fraction = (active.hp or 0) / max(active.max_hp or 1, 1)
    multiplier = 1.0

    # Low-health pivoters should be more willing to grab momentum with a switch-out move.
    if hp_fraction <= 0.5:
        multiplier *= 1.12
    if hp_fraction <= 0.33:
        multiplier *= 1.08

    # If we are slower, pivoting is usually safer than trying to trade hits.
    if active_speed is not None and opponent_speed is not None and active_speed < opponent_speed:
        multiplier *= 1.15

    # When the opponent has a clearly dangerous attack into our current typing,
    # prefer pivoting into a better answer.
    if incoming_eff >= 2:
        multiplier *= 1.2

    # If our current mon is not threatening much damage, momentum moves become more attractive.
    if outgoing_eff <= 1:
        multiplier *= 1.08

    return min(multiplier, 1.5)


def select_move_from_mcts_results(mcts_results: list[(MctsResult, float, int)], battle: Battle) -> str:
    final_policy = {}
    for mcts_result, sample_chance, index in mcts_results:
        this_policy = max(mcts_result.side_one, key=lambda x: x.visits)
        logger.info(
            "Policy {}: {} visited {}% avg_score={} sample_chance_multiplier={}".format(
                index,
                this_policy.move_choice,
                round(100 * this_policy.visits / mcts_result.total_visits, 2),
                round(this_policy.total_score / this_policy.visits, 3),
                round(sample_chance, 3),
            )
        )
        for s1_option in mcts_result.side_one:
            final_policy[s1_option.move_choice] = final_policy.get(
                s1_option.move_choice, 0
            ) + (sample_chance * (s1_option.visits / mcts_result.total_visits))

    # Apply simple ability-aware adjustments (e.g., preferring switches to Magic Bounce)
    adjusted_policy = {}
    for move_choice, score in final_policy.items():
        adjusted_score = score
        try:
            if move_choice.startswith("switch "):
                target_raw = move_choice.split(" ", 1)[1].strip()
                target_pkmn = None

                # If the engine returned a numeric slot (e.g., "switch 2"), try by index
                if target_raw.isdigit():
                    try:
                        idx = int(target_raw)
                        target_pkmn = battle.user.find_reserve_pokemon_by_index(idx)
                    except Exception:
                        target_pkmn = None

                # If it's a PS-style slot or nickname (e.g., "p1a: Nickname"), extract nickname
                if target_pkmn is None and ":" in target_raw:
                    try:
                        nickname = Pokemon.extract_nickname_from_pokemonshowdown_string(
                            target_raw
                        )
                        target_pkmn = battle.user.find_reserve_pokemon_by_nickname(nickname)
                    except Exception:
                        target_pkmn = None

                # Fallbacks: by nickname or species/base_name
                if target_pkmn is None:
                    target_pkmn = battle.user.find_reserve_pokemon_by_nickname(target_raw)
                if target_pkmn is None:
                    target_pkmn = battle.user.find_pokemon_in_reserves(target_raw)

                if target_pkmn is not None:
                    ability = (target_pkmn.ability or "").lower()
                    # boost switches into Magic Bounce users when opponent can use status/hazard moves
                    if ability in ("magicbounce", "magic_bounce"):
                        opp_moves = [m.name for m in (battle.opponent.active.moves or [])]
                        dangerous = any(
                            m in ("taunt", "stealthrock", "spikes", "toxicspikes", "whirlwind", "rapidspin", "defog")
                            for m in opp_moves
                        )
                        if dangerous:
                            adjusted_score = adjusted_score * 1.5

                    impostor_multiplier = _impostor_switch_multiplier(battle, target_pkmn)
                    if impostor_multiplier > 1.0:
                        try:
                            opp_moves = battle.opponent.active.moves or []
                            stay_eff = _max_incoming_effectiveness(opp_moves, battle.user.active.types)
                            switch_eff = _max_incoming_effectiveness(opp_moves, target_pkmn.types)
                            logger.info(
                                "Impostor/Transform switch boost: %s -> x%.2f (stay_eff=%.2f switch_eff=%.2f)",
                                move_choice,
                                impostor_multiplier,
                                stay_eff,
                                switch_eff,
                            )
                        except Exception:
                            logger.info(
                                "Impostor/Transform switch boost: %s -> x%.2f",
                                move_choice,
                                impostor_multiplier,
                            )
                        adjusted_score = adjusted_score * impostor_multiplier

                    if move_choice in constants.SWITCH_OUT_MOVES:
                        switch_out_multiplier = _switch_out_move_multiplier(battle)
                        if switch_out_multiplier > 1.0:
                            logger.info(
                                "Switch-out move bonus: %s -> x%.2f",
                                move_choice,
                                switch_out_multiplier,
                            )
                        adjusted_score = adjusted_score * switch_out_multiplier
        except Exception:
            # be conservative on any unexpected error
            adjusted_score = score
        adjusted_policy[move_choice] = adjusted_score

    final_policy = sorted(adjusted_policy.items(), key=lambda x: x[1], reverse=True)

    # Consider all moves that are close to the best move (after adjustment)
    highest_percentage = final_policy[0][1]
    final_policy = [i for i in final_policy if i[1] >= highest_percentage * 0.75]
    logger.debug("Considered Choices:")
    for i, policy in enumerate(final_policy):
        logger.debug(f"\t{round(policy[1] * 100, 3)}%: {policy[0]}")

    choice = random.choices(final_policy, weights=[p[1] for p in final_policy])[0]
    return choice[0]


def get_result_from_mcts(state: str, search_time_ms: int, index: int) -> MctsResult:
    logger.debug("Calling with {} state: {}".format(index, state))
    poke_engine_state = PokeEngineState.from_string(state)

    res = monte_carlo_tree_search(poke_engine_state, search_time_ms)
    logger.info("Iterations {}: {}".format(index, res.total_visits))
    return res


def search_time_num_battles_randombattles(battle):
    revealed_pkmn = len(battle.opponent.reserve)
    if battle.opponent.active is not None:
        revealed_pkmn += 1

    opponent_active_num_moves = len(battle.opponent.active.moves)
    in_time_pressure = battle.time_remaining is not None and battle.time_remaining <= 60

    # it is still quite early in the battle and the pkmn in front of us
    # hasn't revealed any moves: search a lot of battles shallowly
    if (
        revealed_pkmn <= 3
        and battle.opponent.active.hp > 0
        and opponent_active_num_moves == 0
    ):
        num_battles_multiplier = 2 if in_time_pressure else 4
        return FoulPlayConfig.parallelism * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms // 2
        )

    else:
        num_battles_multiplier = 1 if in_time_pressure else 2
        return FoulPlayConfig.parallelism * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms
        )


def search_time_num_battles_standard_battle(battle):
    opponent_active_num_moves = len(battle.opponent.active.moves)
    in_time_pressure = battle.time_remaining is not None and battle.time_remaining <= 60

    if (
        battle.team_preview
        or (battle.opponent.active.hp > 0 and opponent_active_num_moves == 0)
        or opponent_active_num_moves < 3
    ):
        num_battles_multiplier = 1 if in_time_pressure else 2
        return FoulPlayConfig.parallelism * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms
        )
    else:
        return FoulPlayConfig.parallelism, FoulPlayConfig.search_time_ms


def find_best_move(battle: Battle) -> str:
    battle = deepcopy(battle)
    if battle.team_preview:
        battle.user.active = battle.user.reserve.pop(0)
        battle.opponent.active = battle.opponent.reserve.pop(0)

    if battle.battle_type == BattleType.RANDOM_BATTLE:
        num_battles, search_time_per_battle = search_time_num_battles_randombattles(
            battle
        )
        battles = prepare_random_battles(battle, num_battles)
    elif battle.battle_type == BattleType.BATTLE_FACTORY:
        num_battles, search_time_per_battle = search_time_num_battles_standard_battle(
            battle
        )
        battles = prepare_random_battles(battle, num_battles)
    elif battle.battle_type == BattleType.STANDARD_BATTLE:
        num_battles, search_time_per_battle = search_time_num_battles_standard_battle(
            battle
        )
        battles = prepare_battles(battle, num_battles)
    else:
        raise ValueError("Unsupported battle type: {}".format(battle.battle_type))

    logger.debug("Searching for a move using MCTS...")
    logger.debug(
        "Sampling {} battles at {}ms each".format(num_battles, search_time_per_battle)
    )
    with ProcessPoolExecutor(max_workers=FoulPlayConfig.parallelism) as executor:
        futures = []
        for index, (b, chance) in enumerate(battles):
            fut = executor.submit(
                get_result_from_mcts,
                battle_to_poke_engine_state(b).to_string(),
                search_time_per_battle,
                index,
            )
            futures.append((fut, chance, index))

    mcts_results = [(fut.result(), chance, index) for (fut, chance, index) in futures]
    choice = select_move_from_mcts_results(mcts_results, battle)
    logger.debug("Choice: {}".format(choice))
    return choice
