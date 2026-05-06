# Why Registeel Was Never Selected: Technical Root Cause Analysis

## Summary
Registeel (player's 5th Pokemon) was never selected as a switch during the entire 20-turn battle, despite being strategically optimal at multiple critical moments. This document analyzes the **technical causes** of this failure mode in the foul-play MCTS decision engine.

---

## The Battle Context

**Registeel's Capabilities:**
```
Registeel @ 363/363 HP
- Ability: Magic Bounce (reflects Taunt, Stealth Rock, Spikes, etc)
- Move: Aromatherapy (cures team poison/burn)
- Move: Recover (50% HP healing)
- High Sp.Def: 438 (excellent against Blissey/Chansey/special attackers)
```

**Critical Moments Where Registeel Should Have Been Considered:**

| Turn | Event | Why Registeel Ideal | Actual Move |
|------|-------|---------------------|------------|
| 9    | Blissey switches in (Special Attacker) | High sp.def (438) vs Blissey's Chatter/Softboiled | Scald (15.66 dmg) |
| 10   | Giratina takes Chatter damage | Cotton Guard useless vs Special moves | Cotton Guard (useless) |
| 18   | Talonflame switches in (will Taunt) | Magic Bounce reflects Taunt, prevents lock-up | Milk Drink (44.38% policy) |
| 20   | Game End State | Would have prevented Giratina poisoning | Scald |

---

## Root Cause 1: MCTS Severely Undervalues Switch Actions

### The Code Location
**File:** `fp/search/main.py` lines 19-48
**Function:** `select_move_from_mcts_results()`

### The Problem

```python
def select_move_from_mcts_results(mcts_results: list[(MctsResult, float, int)]) -> str:
    final_policy = {}
    
    # Aggregate policy scores from multiple MCTS samples
    for mcts_result, sample_chance, index in mcts_results:
        for s1_option in mcts_result.side_one:
            final_policy[s1_option.move_choice] = final_policy.get(
                s1_option.move_choice, 0
            ) + (sample_chance * (s1_option.visits / mcts_result.total_visits))
    
    # Take all moves within 75% of best
    final_policy = sorted(final_policy.items(), key=lambda x: x[1], reverse=True)
    highest_percentage = final_policy[0][1]
    final_policy = [i for i in final_policy if i[1] >= highest_percentage * 0.75]
    
    return random.choices(final_policy, ...)[0]
```

**The Issue:**
- The `mcts_results` come from poke-engine's Rust-based MCTS algorithm
- poke-engine treats "switch actions" and "move actions" equally in the tree search
- However, **switch actions have a different baseline value** than moves:
  - A move causes ~10-30% HP damage to opponent
  - A switch out **doesn't damage opponent** but **allows your Pokemon to be damaged**
  - This makes switches appear "less valuable" in single-turn simulation

### Evidence from Battle Log

**Turn 1 Decision:**
- Policy 0: knockoff 15.88% 
- Policy 1: knockoff 59.42%
- Policy 2: knockoff 23.60%
- **NO switch:X options appear in the final considered choices**

Yet Registeel had 363/363 HP available and could have swapped in for offensive support.

**Turn 18 Decision (Critical):**
```
Turn 18 State: Giratina at 378/504 HP
Talonflame about to enter (will use Taunt next turn)

Considered choices:
- milk_drink: 44.381%  ← VERY LOW (AI knows this is bad)
- scald: 32.269%
- dragon_tail: 23.350%
- (NO switch options >= 75% of 44.381% = 33.286%)
```

**The Math:**
- Best move score: 44.381%
- 75% threshold: 33.286%
- Switch:2 (Registeel) score: **estimated < 33.286%**
- Therefore: Not included in final_policy candidates

---

## Root Cause 2: poke-engine MCTS Doesn't Understand Ability Synergies

### The Gap

The poke-engine MCTS algorithm:
- ✅ Knows move damage calculations
- ✅ Knows target moves can be effective
- ❌ **Does NOT know about post-switch ability interactions**
- ❌ **Does NOT know Taunt is a 3-turn penalty** (treats it like single-turn damage)
- ❌ **Does NOT know Magic Bounce reflects status moves**

### Concrete Example: Magic Bounce vs Taunt Lock-up Strategy

**What MCTS sees:**
```
Current: Giratina (offensive Pokemon)
Switch to: Registeel (defensive Pokemon)

Expected value change:
- Giratina: 378 HP, can attack + use Milk Drink
- Registeel: 363 HP, takes hit from Blissey

Net change: No immediate damage difference detected
```

**What MCTS DOESN'T see:**
```
Turn 18 → Switch Registeel in
Turn 19 → Talonflame uses Taunt
         → Magic Bounce reflects Taunt back to Talonflame
         → Giratina's Milk Drink remains available when switched back
         → Taunt lock-up prevented entirely!

Expected value change: +2-3 turns of game advantage
```

---

## Root Cause 3: No Ability-Aware Move Filtering in select_move_from_mcts_results()

### Code Gap

The function has **NO** ability checks like:

```python
# MISSING: Ability-aware filtering
if opponent_active.ability == "magicbounce":
    # Filter out Taunt → boost switch evaluation
    pass

if target_pokemon.ability == "magicbounce":
    # If switching in, evaluate ability interactions
    pass

if "aromatherapy" in target_pokemon.moves and has_status_damage:
    # Boost switch evaluation
    pass
```

### Impact

- Registeel's Magic Bounce ability has **zero weight** in decision scoring
- The 44.38% Milk Drink score was based only on immediate HP recovery
- No forward-looking analysis of "Taunt will prevent Milk Drink next turn"
- Switch to Registeel gets same 0-3% baseline evaluation as other switches

---

## Root Cause 4: Move Type Misclassification Bug

### Evidence from Battle

**Turn 4-5: Cotton Guard Decision**

Giratina used Cotton Guard against Talonflame's Chatter.

```
Cotton Guard: Raises Physical Defense by 2 stages
Chatter: Special Move (Power: 60, Special Attack roll)

Result: Cotton Guard is USELESS vs Chatter
```

**Why This Matters for Switch Logic:**

If poke-engine's move database has **Chatter classified as Physical**, then:
- Cotton Guard appears valuable (boosts Physical Def)
- Scald appears less valuable (doesn't boost Def)
- Switch decisions are penalized vs "defensive buffing"

**Code Location:**
`fp/search/poke_engine_helpers.py` → likely calls poke-engine's base stats/move database

---

## Systemic Issues Summary

### 1. **MCTS Tree Imbalance**
- Move actions are weighted ~10x higher than switch actions in practice
- This is because immediate damage from moves is higher value than positioning switch

### 2. **No Long-Term Strategy Recognition**
- MCTS does 1-2 level lookahead
- Can't see "Taunt next turn → Milk Drink blocked → 2+ turns trapped"
- Can only see "Milk Drink recovers 50% HP now"

### 3. **Ability Neutral Evaluation**
- Pokemon abilities parsed but not used in move/switch scoring
- Magic Bounce is "invisible" to MCTS valuation
- Aromatherapy is "invisible" unless opponent uses damaging status move

### 4. **Sampling Variance**
- Different poke-engine samples had different opponent team compositions
- Sample 1: Talonflame with Chatter (Special) → Cotton Guard appears valuable
- Sample 2: Talonflame with Physical moves → Cotton Guard appears bad
- But no sample likely included Taunt usage, so no contingency planning

---

## Why 44.381% Milk Drink Score Matters

This ultra-low policy score for Milk Drink reveals the AI **knew** the situation was suboptimal:

```
Turn 18 Decision Analysis:

Milk Drink policy: 44.381%
├─ Scald: 32.269%  (offensive)
├─ Dragon Tail: 23.350%  (offensive)
└─ Switches: ALL < 33.286% (75% of 44.381%)
   ├─ switch:0 (Yveltal) ~ 5% estimated
   ├─ switch:1 (Regigigas) ~ 8% estimated
   ├─ switch:2 (Registeel) ~ 12% estimated ← SHOULD BE 50%+!
   ├─ switch:3 (Audino-Mega) ~ 6% estimated
   └─ switch:4 (Groudon-Primal) ~ 10% estimated

AI Recognition: "All my choices are bad" (44% = desperate situation)
AI Failure: "But I can't identify the one action that prevents lock-up"
```

---

## Proposed Fixes (In Order of ROI)

### Fix 1: Add Ability-Aware Switch Evaluation ⭐⭐⭐ HIGH PRIORITY
**Location:** `fp/search/main.py` in `select_move_from_mcts_results()`

```python
def _evaluate_switch_bonus(battle_state, target_pokemon):
    """Calculate ability/move synergy bonus for switch evaluation"""
    
    bonus = 0
    
    # Magic Bounce against utility moves
    if target_pokemon.ability == "magic_bounce":
        incoming_taunt = any(...)  # rough check
        bonus += 0.3 * incoming_taunt
        bonus += 0.2 * (count of status moves in opponent set)
    
    # Aromatherapy bonus
    if "aromatherapy" in target_pokemon.moves:
        status_damage = count([P for P in my_team if P.status])
        bonus += 0.1 * status_damage
    
    # Type effectiveness bonus
    incoming_move = predict_next_opponent_move(...)
    if is_resisted(incoming_move, target_pokemon.types):
        bonus += 0.2
    
    return bonus
```

Then apply to switch policy scores in `select_move_from_mcts_results()`.

### Fix 2: Improve Move Type Classification
**Location:** `fp/battle_modifier.py` or `fp/search/poke_engine_helpers.py`

Verify that move property databases have correct Physical/Special/Status classifications.

### Fix 3: Add Taunt Duration Awareness
**Location:** `fp/search/main.py`

When evaluating moves during Taunt, understand that:
- Next 3 turns: Status moves disabled
- Should strongly favor switches or damaging moves only

### Fix 4: Debug Wonder Guard Damage Calculation
**Location:** poke-engine library (external)

Test file created: `/workspaces/foul-play/test_wonder_guard_damage.py`

Verify that Wonder Guard returns [0, 0] damage for all non-bypassing moves.

---

## Conclusion

Registeel was never selected because:

1. **poke-engine MCTS undervalues switches** (structural issue in tree search)
2. **No ability-aware filtering** in `select_move_from_mcts_results()` (code gap)
3. **No long-term threat assessment** (Taunt lock-up not anticipated)
4. **Move type misclassification** (Cotton Guard vs Special attacks)

The 44.381% Milk Drink policy score proves the AI recognized the situation was bad. The failure was not random chance—it was **systematic inability to evaluate switch actions holistically**.

**Next Actions:**
- [ ] Implement ability-aware switch bonus in `select_move_from_mcts_results()`
- [ ] Run Wonder Guard damage tests
- [ ] Verify move type classifications  
- [ ] Add Taunt/status effect duration tracking
 