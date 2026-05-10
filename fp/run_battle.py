import json
import asyncio
import concurrent.futures
from copy import deepcopy
import logging

from data.pkmn_sets import RandomBattleTeamDatasets, TeamDatasets
from data.pkmn_sets import SmogonSets
import constants
from constants import BattleType
from config import FoulPlayConfig, SaveReplay
from fp.battle import LastUsedMove, Pokemon, Battle
from fp.battle_modifier import async_update_battle, process_battle_updates
from fp.helpers import normalize_name
from fp.search.main import find_best_move

from fp.websocket_client import PSWebsocketClient

logger = logging.getLogger(__name__)


def _reserve_debug_snapshot(battle):
    snapshot = []
    for p in battle.user.reserve:
        snapshot.append(
            {
                "index": getattr(p, "index", None),
                "name": p.name,
                "base_name": p.base_name,
                "nickname": p.nickname,
            }
        )
    return snapshot


def _apply_request_json_if_possible(battle_obj):
    if battle_obj.team_preview or not battle_obj.request_json:
        return

    # The request JSON can refer to either p1 or p2. Update the correct battler.
    try:
        side_id = battle_obj.request_json[constants.SIDE][constants.ID]
    except Exception:
        side_id = None

    if side_id == battle_obj.user.name:
        try:
            battle_obj.user.update_from_request_json(battle_obj.request_json)
        except ValueError:
            logger.debug("User update_from_request_json raised ValueError; skipping")
    elif side_id == battle_obj.opponent.name:
        try:
            battle_obj.opponent.update_from_request_json(battle_obj.request_json)
        except ValueError:
            logger.debug(
                "Opponent update_from_request_json raised ValueError; skipping"
            )
    else:
        # Fallback: try updating user, but ignore mismatches to avoid crashing the search.
        try:
            battle_obj.user.update_from_request_json(battle_obj.request_json)
        except Exception:
            logger.debug("Could not apply request_json; continuing without applying it")


def _resolve_switch_slot(battle, switch_pokemon):
    """
    Resolves a switch target name to a team slot number.
    
    In Pokémon Showdown, sending "/switch N" causes the server to swap slot 1 (active)
    with slot N. Reserve slots are numbered 2-6:
      - slot 1: active Pokemon
      - slot 2: reserve[0]
      - slot 3: reserve[1]
      - ... 
      - slot 6: reserve[5]
    
    This function finds the target Pokemon in the reserve list and returns its slot number.
    """
    matches = [
        pkmn
        for pkmn in battle.user.reserve
        if pkmn.name == switch_pokemon
        or pkmn.base_name == switch_pokemon
        or (pkmn.nickname and normalize_name(pkmn.nickname) == switch_pokemon)
    ]

    if not matches:
        raise ValueError(
            "Could not find '{}' in reserves: {}".format(
                switch_pokemon,
                [p.name for p in battle.user.reserve]
            )
        )

    # For duplicate species, prefer the one with the highest request_json index (most recently synced).
    # If no index is available, prefer the healthier one (likely fresher).
    if len(matches) > 1:
        logger.warning(
            "Ambiguous switch target '%s' - found %d matches in reserves",
            switch_pokemon,
            len(matches),
        )
        matches_with_index = [m for m in matches if getattr(m, "index", None) is not None]
        if matches_with_index:
            chosen = max(matches_with_index, key=lambda p: getattr(p, "index", 0))
            logger.debug(
                "Selected by highest request index: %s (index=%s)",
                chosen.name,
                chosen.index,
            )
        else:
            chosen = max(matches, key=lambda p: (p.hp or 0) / (p.max_hp or 1) if p.max_hp else 0)
            logger.debug(
                "Selected by highest HP%%: %s (hp=%s/%s)",
                chosen.name,
                chosen.hp,
                chosen.max_hp,
            )
    else:
        chosen = matches[0]

    # During team preview, battle.user.active is a dummy Pokemon, so we can select any real Pokemon.
    # In normal battle, active Pokemon cannot be switched to (it's already in battle).
    if not getattr(battle, "team_preview", False) and chosen is battle.user.active:
        raise ValueError(
            "Switch target cannot be the active Pokemon. Target='{}' is already active.".format(
                switch_pokemon
            )
        )

    # Determine the slot by matching against the latest request_json.
    # Each pokemon object should have been assigned an index (1-6) by _apply_request_json_if_possible.
    slot = getattr(chosen, "index", None)
    
    if slot is None:
        raise ValueError(
            "Chosen Pokemon {} has no index field. Cannot resolve to slot. "
            "This may indicate battle state is out of sync with server.".format(
                chosen.name
            )
        )

    logger.debug(
        "Resolved switch '%s' -> slot %s (pkmn.index=%s)",
        switch_pokemon,
        slot,
        slot,
    )
    return "/switch {}".format(slot)


def _resolve_move_message(battle, decision):
    tera = False
    mega = False

    if decision.endswith("-tera"):
        decision = decision.removesuffix("-tera")
        tera = True
    elif decision.endswith("-mega"):
        decision = decision.removesuffix("-mega")
        mega = True

    message = "/choose move {}".format(decision)

    if battle.user.active.can_mega_evo and mega:
        message = "{} {}".format(message, constants.MEGA)
    elif battle.user.active.can_ultra_burst:
        message = "{} {}".format(message, constants.ULTRA_BURST)

    # only dynamax on last pokemon
    if battle.user.active.can_dynamax and all(p.hp == 0 for p in battle.user.reserve):
        message = "{} {}".format(message, constants.DYNAMAX)

    if tera and battle.generation == "gen9":
        message = "{} {}".format(message, constants.TERASTALLIZE)

    chosen_move = battle.user.active.get_move(decision)
    if chosen_move is None:
        logger.debug(
                "Could not resolve move '%s' on active '%s'; moves=%s; skipping z-move suffix",
            decision,
            battle.user.active.name,
                [m.name for m in battle.user.active.moves],
        )
    elif chosen_move.can_z:
        message = "{} {}".format(message, constants.ZMOVE)

    return message


def format_decision(battle, decision):
    # Formats a decision for communication with Pokemon-Showdown
    # If the move can be used as a Z-Move, it will be

    if decision.startswith(constants.SWITCH_STRING + " "):
        switch_pokemon = decision.split("switch ", 1)[1].strip()
        message = _resolve_switch_slot(battle, switch_pokemon)
    else:
        message = _resolve_move_message(battle, decision)

    return [message, str(battle.rqid)]


def battle_is_finished(battle_tag, msg):
    return (
        msg.startswith(">{}".format(battle_tag))
        and (constants.WIN_STRING in msg or constants.TIE_STRING in msg)
        and constants.CHAT_STRING not in msg
    )


def extract_battle_factory_tier_from_msg(msg):
    start = msg.find("Battle Factory Tier: ") + len("Battle Factory Tier: ")
    end = msg.find("</b>", start)
    tier_name = msg[start:end]

    return normalize_name(tier_name)


async def async_pick_move(battle):
    battle_copy = deepcopy(battle)
    _apply_request_json_if_possible(battle_copy)

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        best_move = await loop.run_in_executor(pool, find_best_move, battle_copy)
    battle.user.last_selected_move = LastUsedMove(
        battle.user.active.name,
        best_move.removesuffix("-tera").removesuffix("-mega"),
        battle.turn,
    )

    # Keep live battle state in sync before formatting command against it.
    # This avoids move list desyncs (e.g. Transform/Baton Pass turns).
    _apply_request_json_if_possible(battle)

    # Format the decision using the live `battle` so switch indexes map to the actual team
    result = format_decision(battle, best_move)
    logger.debug(
        "Sent command decision=%s -> formatted_result=%s active=%s reserve_snapshot=%s",
        best_move,
        result,
        battle.user.active.name,
        _reserve_debug_snapshot(battle),
    )
    return result


async def handle_team_preview(battle, ps_websocket_client):
    battle_copy = deepcopy(battle)
    battle_copy.user.active = Pokemon.get_dummy()
    battle_copy.opponent.active = Pokemon.get_dummy()
    battle_copy.team_preview = True

    # Use the preview copy here because the live battle may not have an active Pokemon yet.
    # `async_pick_move` clones internally for search and formats against the battle it receives.
    best_move = await async_pick_move(battle_copy)
    logger.debug(
        "Team preview raw choice=%s reserve_snapshot=%s",
        best_move,
        _reserve_debug_snapshot(battle),
    )

    pkmn_name = battle.user.reserve[int(best_move[0].split()[1]) - 1].name
    battle.user.last_selected_move = LastUsedMove(
        "teampreview", "switch {}".format(pkmn_name), battle.turn
    )

    size_of_team = len(battle.user.reserve) + 1
    team_list_indexes = list(range(1, size_of_team))
    choice_digit = int(best_move[0].split()[-1])

    team_list_indexes.remove(choice_digit)
    message = [
        "/team {}{}|{}".format(
            choice_digit, "".join(str(x) for x in team_list_indexes), battle.rqid
        )
    ]

    await ps_websocket_client.send_message(battle.battle_tag, message)


async def get_battle_tag_and_opponent(ps_websocket_client: PSWebsocketClient):
    while True:
        msg = await ps_websocket_client.receive_message()
        split_msg = msg.split("|")
        first_msg = split_msg[0]
        if "battle" in first_msg:
            battle_tag = first_msg.replace(">", "").strip()
            user_name = FoulPlayConfig.username
            opponent_name = (
                split_msg[4].replace(user_name, "").replace("vs.", "").strip()
            )
            logger.info("Initialized {} against: {}".format(battle_tag, opponent_name))
            return battle_tag, opponent_name


async def start_battle_common(
    ps_websocket_client: PSWebsocketClient, pokemon_battle_type
):
    battle_tag, opponent_name = await get_battle_tag_and_opponent(ps_websocket_client)
    if FoulPlayConfig.log_to_file:
        FoulPlayConfig.file_log_handler.do_rollover(
            "{}_{}.log".format(battle_tag, opponent_name)
        )

    battle = Battle(battle_tag)
    battle.opponent.account_name = opponent_name
    battle.pokemon_format = pokemon_battle_type
    battle.generation = pokemon_battle_type[:4]

    # wait until the opponent's identifier is received. This will be `p1` or `p2`.
    #
    # e.g.
    # '>battle-gen9randombattle-44733
    # |player|p1|OpponentName|2|'
    while True:
        msg = await ps_websocket_client.receive_message()
        if "|player|" in msg and battle.opponent.account_name in msg:
            battle.opponent.name = msg.split("|")[2]
            battle.user.name = constants.ID_LOOKUP[battle.opponent.name]
            break

    return battle, msg


async def get_first_request_json(
    ps_websocket_client: PSWebsocketClient, battle: Battle
):
    while True:
        msg = await ps_websocket_client.receive_message()
        msg_split = msg.split("|")
        if msg_split[1].strip() == "request" and msg_split[2].strip():
            user_json = json.loads(msg_split[2].strip("'"))
            battle.request_json = user_json
            battle.user.initialize_first_turn_user_from_json(user_json)
            battle.rqid = user_json[constants.RQID]
            return


async def start_random_battle(
    ps_websocket_client: PSWebsocketClient, pokemon_battle_type
):
    battle, msg = await start_battle_common(ps_websocket_client, pokemon_battle_type)
    battle.battle_type = BattleType.RANDOM_BATTLE
    RandomBattleTeamDatasets.initialize(battle.generation)

    while True:
        if constants.START_STRING in msg:
            battle.started = True

            # hold onto some messages to apply after we get the request JSON
            # omit the bot's switch-in message because we won't need that
            # parsing the request JSON will set the bot's active pkmn
            battle.msg_list = [
                m
                for m in msg.split(constants.START_STRING)[1].strip().split("\n")
                if not (m.startswith("|switch|{}".format(battle.user.name)))
            ]
            break
        msg = await ps_websocket_client.receive_message()

    await get_first_request_json(ps_websocket_client, battle)

    # apply the messages that were held onto
    process_battle_updates(battle)

    best_move = await async_pick_move(battle)
    await ps_websocket_client.send_message(battle.battle_tag, best_move)

    return battle


async def start_standard_battle(
    ps_websocket_client: PSWebsocketClient, pokemon_battle_type, team_dict
):
    battle, msg = await start_battle_common(ps_websocket_client, pokemon_battle_type)
    battle.user.team_dict = team_dict
    if "battlefactory" in pokemon_battle_type:
        battle.battle_type = BattleType.BATTLE_FACTORY
    else:
        battle.battle_type = BattleType.STANDARD_BATTLE

    if battle.generation in constants.NO_TEAM_PREVIEW_GENS:
        while True:
            if constants.START_STRING in msg:
                battle.started = True

                # hold onto some messages to apply after we get the request JSON
                # omit the bot's switch-in message because we won't need that
                # parsing the request JSON will set the bot's active pkmn
                battle.msg_list = [
                    m
                    for m in msg.split(constants.START_STRING)[1].strip().split("\n")
                    if not (m.startswith("|switch|{}".format(battle.user.name)))
                ]
                break
            msg = await ps_websocket_client.receive_message()

        await get_first_request_json(ps_websocket_client, battle)

        unique_pkmn_names = set(
            [p.name for p in battle.user.reserve] + [battle.user.active.name]
        )
        SmogonSets.initialize(
            FoulPlayConfig.smogon_stats or pokemon_battle_type, unique_pkmn_names
        )
        TeamDatasets.initialize(pokemon_battle_type, unique_pkmn_names)

        # apply the messages that were held onto
        process_battle_updates(battle)

        best_move = await async_pick_move(battle)
        await ps_websocket_client.send_message(battle.battle_tag, best_move)

    else:
        while constants.START_TEAM_PREVIEW not in msg:
            msg = await ps_websocket_client.receive_message()

        preview_string_lines = msg.split(constants.START_TEAM_PREVIEW)[-1].split("\n")

        opponent_pokemon = []
        for line in preview_string_lines:
            if not line:
                continue

            split_line = line.split("|")
            if (
                split_line[1] == constants.TEAM_PREVIEW_POKE
                and split_line[2].strip() == battle.opponent.name
            ):
                opponent_pokemon.append(split_line[3])

        await get_first_request_json(ps_websocket_client, battle)
        battle.initialize_team_preview(opponent_pokemon, pokemon_battle_type)
        battle.during_team_preview()

        unique_pkmn_names = set(
            p.name for p in battle.opponent.reserve + battle.user.reserve
        )

        if battle.battle_type == BattleType.BATTLE_FACTORY:
            battle.battle_type = BattleType.BATTLE_FACTORY
            tier_name = extract_battle_factory_tier_from_msg(msg)
            logger.info("Battle Factory Tier: {}".format(tier_name))
            TeamDatasets.initialize(
                pokemon_battle_type,
                unique_pkmn_names,
                battle_factory_tier_name=tier_name,
            )
        else:
            battle.battle_type = BattleType.STANDARD_BATTLE
            SmogonSets.initialize(
                FoulPlayConfig.smogon_stats or pokemon_battle_type, unique_pkmn_names
            )
            TeamDatasets.initialize(pokemon_battle_type, unique_pkmn_names)

        await handle_team_preview(battle, ps_websocket_client)

    return battle


async def start_battle(ps_websocket_client, pokemon_battle_type, team_dict):
    if "random" in pokemon_battle_type:
        battle = await start_random_battle(ps_websocket_client, pokemon_battle_type)
    else:
        battle = await start_standard_battle(
            ps_websocket_client, pokemon_battle_type, team_dict
        )

    await ps_websocket_client.send_message(battle.battle_tag, ["I am a bot, have fun and good luck!"])
    await ps_websocket_client.send_message(battle.battle_tag, ["/timer on"])

    return battle


async def pokemon_battle(ps_websocket_client, pokemon_battle_type, team_dict):
    battle = await start_battle(ps_websocket_client, pokemon_battle_type, team_dict)
    while True:
        msg = await ps_websocket_client.receive_message()
        if battle_is_finished(battle.battle_tag, msg):
            winner = (
                msg.split(constants.WIN_STRING)[-1].split("\n")[0].strip()
                if constants.WIN_STRING in msg
                else None
            )
            logger.info("Winner: {}".format(winner))
            await ps_websocket_client.send_message(battle.battle_tag, ["gg"])
            if (
                FoulPlayConfig.save_replay == SaveReplay.always
                or (
                    FoulPlayConfig.save_replay == SaveReplay.on_loss
                    and winner != FoulPlayConfig.username
                )
                or (
                    FoulPlayConfig.save_replay == SaveReplay.on_win
                    and winner == FoulPlayConfig.username
                )
            ):
                await ps_websocket_client.save_replay(battle.battle_tag)
            await ps_websocket_client.leave_battle(battle.battle_tag)
            return winner
        else:
            action_required = await async_update_battle(battle, msg)
            if action_required and not battle.wait:
                best_move = await async_pick_move(battle)
                await ps_websocket_client.send_message(battle.battle_tag, best_move)
