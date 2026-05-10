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

# Track battle count for dynamic search depth
_battle_count = 0


def calculate_dynamic_search_depth(battle_count: int) -> int:
    """
    Calculate search depth based on battle count for dynamic search.
    Early battles use shallow search, later battles use deeper search.
    
    Returns the search depth multiplier (1-4)
    """
    if not FoulPlayConfig.dynamic_search_enabled:
        return FoulPlayConfig.state_search_depth
    
    # Early game: shallow search (depth 1-2)
    if battle_count < FoulPlayConfig.dynamic_search_battle_threshold:
        return 1
    
    # Mid game: moderate search (depth 2-3)
    elif battle_count < FoulPlayConfig.dynamic_search_battle_threshold * 2:
        return 2
    
    # Late game: deep search (depth 3-4)
    else:
        return 3


def get_search_time_for_depth(base_time_ms: int, depth: int) -> int:
    """
    Calculate actual search time based on depth.
    Deeper searches get more time to explore possibilities.
    
    Depth 1: 25% of base time
    Depth 2: 50% of base time
    Depth 3: 75% of base time
    Depth 4: 100% of base time
    """
    if depth <= 1:
        return max(base_time_ms // 4, 25)
    elif depth <= 2:
        return base_time_ms // 2
    elif depth <= 3:
        return int(base_time_ms * 0.75)
    else:  # depth 4
        return base_time_ms


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


def _weather_move_multiplier(battle: Battle, move_choice: str) -> float:
    move_name = move_choice.lower()
    weather = battle.weather

    if weather is None:
        return 1.0

    if move_name == "weatherball":
        return 1.25

    if weather in (constants.RAIN, constants.HEAVY_RAIN):
        if move_name in ("thunder", "hurricane"):
            return 1.2
        if move_name in ("solarbeam", "solarblade"):
            return 0.6
    elif weather == constants.SUN:
        if move_name in ("solarbeam", "solarblade"):
            return 1.2
        if move_name in ("thunder", "hurricane"):
            return 0.6
    elif weather == constants.SAND:
        if move_name == "weatherball":
            return 1.15
    elif weather in constants.HAIL_OR_SNOW:
        if move_name == "blizzard":
            return 1.2
        if move_name == "weatherball":
            return 1.15

    return 1.0


def _weather_switch_multiplier(battle: Battle, target_pkmn) -> float:
    ability = (target_pkmn.ability or "").lower()
    if ability == "drizzle":
        desired_weather = constants.RAIN
    elif ability == "drought":
        desired_weather = constants.SUN
    elif ability == "sandstream":
        desired_weather = constants.SAND
    elif ability == "snowwarning":
        desired_weather = constants.SNOW
    elif ability == "primordialsea":
        desired_weather = constants.HEAVY_RAIN
    elif ability == "desolateland":
        desired_weather = constants.SUN
    else:
        return 1.0

    if battle.weather != desired_weather or battle.weather_turns_remaining <= 2:
        return 1.18

    return 1.0


def _weather_strategy_multiplier(battle: Battle, move_choice: str) -> float:
    move_name = _decision_move_name(move_choice)
    weather = battle.weather
    turns_remaining = battle.weather_turns_remaining

    if move_name in ("sunnyday", "raindance", "sandstorm", "snowscape"):
        if weather is None or weather == "none":
            return 1.35
        if turns_remaining >= 0 and turns_remaining <= 2:
            return 1.45
        if move_name == weather:
            return 0.7
        return 1.1

    if weather is not None and turns_remaining >= 0 and turns_remaining <= 2:
        if move_name in ("hurricane", "thunder", "weatherball", "blizzard", "solarbeam", "solarblade"):
            return 1.12

    return 1.0


def _decision_move_name(move_choice: str) -> str:
    return move_choice.lower().removesuffix("-tera").removesuffix("-mega")


def _move_data_for_choice(move_choice: str):
    move_name = _decision_move_name(move_choice)
    return all_move_json.get(move_name)


def _status_move_multiplier(battle: Battle, move_choice: str) -> float:
    if move_choice.startswith("switch "):
        return 1.0

    move_data = _move_data_for_choice(move_choice)
    if move_data is None:
        return 1.0

    move_name = _decision_move_name(move_choice)
    move_category = move_data.get(constants.CATEGORY)
    user_status = battle.user.active.status
    opponent_status = battle.opponent.active.status if battle.opponent.active else None
    user_ability = (battle.user.active.ability or "").lower()

    multiplier = 1.0

    if user_status == constants.SLEEP:
        if move_name == "sleeptalk":
            multiplier *= 1.8
        elif move_name == "rest":
            multiplier *= 1.2
        else:
            multiplier *= 0.2

    elif user_status == constants.BURN:
        if move_name == "facade":
            multiplier *= 1.6
        elif move_category == constants.PHYSICAL:
            multiplier *= 0.72
            if user_ability == "guts":
                multiplier *= 1.35

    elif user_status in (constants.POISON, constants.TOXIC):
        if move_name == "facade":
            multiplier *= 1.45
        elif move_category == constants.PHYSICAL and user_ability == "guts":
            multiplier *= 1.2

    elif user_status == constants.PARALYZED and move_category == constants.PHYSICAL:
        multiplier *= 0.95

    if opponent_status is not None:
        if move_name == "hex" and opponent_status in (
            constants.SLEEP,
            constants.BURN,
            constants.POISON,
            constants.TOXIC,
            constants.PARALYZED,
        ):
            multiplier *= 1.35
        elif move_name == "venoshock" and opponent_status in (
            constants.POISON,
            constants.TOXIC,
        ):
            multiplier *= 1.35
        elif move_name == "dreameater" and opponent_status == constants.SLEEP:
            multiplier *= 1.35

    return multiplier


def _status_switch_multiplier(battle: Battle, target_pkmn) -> float:
    user_status = battle.user.active.status
    target_status = target_pkmn.status
    target_ability = (target_pkmn.ability or "").lower()

    multiplier = 1.0

    if user_status in (constants.BURN, constants.POISON, constants.TOXIC, constants.PARALYZED):
        if target_ability in ("naturalcure", "regenerator", "magicguard"):
            multiplier *= 1.12
        if target_ability == "guts" and user_status in (constants.BURN, constants.POISON, constants.TOXIC):
            multiplier *= 1.15

    if user_status == constants.SLEEP and target_ability in ("naturalcure", "regenerator"):
        multiplier *= 1.15

    if target_status is not None and target_ability in ("guts", "marvelscale", "poisonheal", "quickfeet"):
        multiplier *= 1.15

    return multiplier


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
    
    # Log Trick Room state for awareness
    if battle.trick_room:
        logger.info(
            "Trick Room active: {} turns remaining. Speed priorities are REVERSED.".format(
                battle.trick_room_turns_remaining
            )
        )
    
    for move_choice, score in final_policy.items():
        adjusted_score = score
        try:
            # Reduce Sleep Talk usage if we've already used it multiple turns
            if "sleeptalk" in move_choice.lower():
                sleep_count = battle.user.side_conditions.get(constants.SLEEP_COUNT, 0)
                # Penalize Sleep Talk after 2 turns to avoid overuse (prevent locked into it)
                if sleep_count >= 2:
                    penalty = 0.6 if sleep_count == 2 else 0.3
                    adjusted_score = adjusted_score * penalty
                    logger.info(
                        "Sleep Talk penalty applied: sleep_count={}, penalty={}, score: {} -> {}".format(
                            sleep_count, penalty, round(score, 3), round(adjusted_score, 3)
                        )
                    )

            weather_multiplier = _weather_move_multiplier(battle, move_choice)
            if weather_multiplier != 1.0:
                adjusted_score = adjusted_score * weather_multiplier
                logger.info(
                    "Weather move boost: weather=%s move=%s multiplier=%.2f score: %s -> %s",
                    battle.weather,
                    move_choice,
                    weather_multiplier,
                    round(score, 3),
                    round(adjusted_score, 3),
                )

            status_multiplier = _status_move_multiplier(battle, move_choice)
            if status_multiplier != 1.0:
                adjusted_score = adjusted_score * status_multiplier
                logger.info(
                    "Status move boost: user_status=%s opponent_status=%s move=%s multiplier=%.2f score: %s -> %s",
                    battle.user.active.status,
                    battle.opponent.active.status if battle.opponent.active else None,
                    move_choice,
                    status_multiplier,
                    round(score, 3),
                    round(adjusted_score, 3),
                )

            weather_strategy_multiplier = _weather_strategy_multiplier(battle, move_choice)
            if weather_strategy_multiplier != 1.0:
                adjusted_score = adjusted_score * weather_strategy_multiplier
                logger.info(
                    "Weather strategy boost: weather=%s move=%s turns=%s multiplier=%.2f score: %s -> %s",
                    battle.weather,
                    move_choice,
                    battle.weather_turns_remaining,
                    weather_strategy_multiplier,
                    round(score, 3),
                    round(adjusted_score, 3),
                )
            
            # Penalize hazard moves when hazards are already at max layers on opponent's side
            hazard_move_mapping = {
                "stealthrock": (constants.STEALTH_ROCK, 1),  # (constant, max_layers)
                "spikes": (constants.SPIKES, 3),
                "toxicspikes": (constants.TOXIC_SPIKES, 2),
            }
            
            move_lower = move_choice.lower() if not move_choice.startswith("switch ") else ""
            for hazard_move, (hazard_constant, max_layers) in hazard_move_mapping.items():
                if hazard_move in move_lower:
                    opponent_hazard_layers = battle.opponent.side_conditions.get(hazard_constant, 0)
                    if opponent_hazard_layers >= max_layers:
                        # Heavy penalty for adding hazards when already maxed
                        penalty = 0.1
                        adjusted_score = adjusted_score * penalty
                        logger.info(
                            "Hazard stacking prevention: {} already at {}/{} layers, penalty={}, score: {} -> {}".format(
                                hazard_constant, opponent_hazard_layers, max_layers, 
                                penalty, round(score, 3), round(adjusted_score, 3)
                            )
                        )
                    break
            
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

                    weather_switch_multiplier = _weather_switch_multiplier(battle, target_pkmn)
                    if weather_switch_multiplier != 1.0:
                        adjusted_score = adjusted_score * weather_switch_multiplier
                        logger.info(
                            "Weather setter switch boost: %s ability=%s weather=%s multiplier=%.2f",
                            move_choice,
                            ability,
                            battle.weather,
                            weather_switch_multiplier,
                        )

                    status_switch_multiplier = _status_switch_multiplier(battle, target_pkmn)
                    if status_switch_multiplier != 1.0:
                        adjusted_score = adjusted_score * status_switch_multiplier
                        logger.info(
                            "Status switch boost: %s target_status=%s ability=%s multiplier=%.2f",
                            move_choice,
                            target_pkmn.status,
                            ability,
                            status_switch_multiplier,
                        )

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
    global _battle_count
    _battle_count += 1
    
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

    # Apply dynamic search depth adjustment if enabled
    if FoulPlayConfig.dynamic_search_enabled:
        current_depth = calculate_dynamic_search_depth(_battle_count)
        search_time_per_battle = get_search_time_for_depth(search_time_per_battle, current_depth)
        logger.info(
            "Dynamic search: Battle #{}, depth={}, adjusted_time={}ms".format(
                _battle_count, current_depth, search_time_per_battle
            )
        )
        
        # Limit number of battles at maximum depth if we have many options
        if current_depth >= 4 and len(battles) > FoulPlayConfig.dynamic_search_opts_for_max:
            logger.info(
                "Limiting battles from {} to {} at max depth {}".format(
                    len(battles), FoulPlayConfig.dynamic_search_opts_for_max, current_depth
                )
            )
            battles = battles[:FoulPlayConfig.dynamic_search_opts_for_max]

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
