# Beat Up ![umbreon](https://play.pokemonshowdown.com/sprites/xyani/umbreon.gif)
A Pokémon battle-bot that can play battles on [Pokemon Showdown](https://pokemonshowdown.com/).

Beat Up can play single battles in all generations
though currently dynamax and z-moves are not supported.

![badge](https://github.com/pmariglia/foul-play/actions/workflows/ci.yml/badge.svg)

## Python version
Requires Python 3.11+.

## Getting Started

### Configuration

Command-line arguments are used to configure Foul Play

use `python run.py --help` to see all options.

### Running Locally

**1. Clone**

Clone the repository with `git clone https://github.com/MyStudyingAccount/beat-up.git`

**2. Install Requirements**

Install the requirements with `pip install -r requirements.txt`.

Note: Requires Rust to be installed on your machine to build the engine.

**4. Run**

Run with `python run.py`

Here is a minimal example that plays a gen9randombattle on Pokemon Showdown:
```bash
python run.py \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode search_ladder \
--pokemon-format gen9randombattle
```

### Running with Docker

**1. Clone the repository**

`git clone https://github.com/MyStudyingAccount/beat-up.git`

**2. Build the Docker image**

Use the `Makefile` to build a Docker image
```shell
make docker
```

or for a specific generation:
```shell
make docker GEN=gen4
```

**3. Run the Docker Image**
```bash
docker run --rm --network host beat-up:latest \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode search_ladder \
--pokemon-format gen9randombattle
```

## Engine

This project uses [poke-engine](https://github.com/pmariglia/poke-engine) to search through battles.
See [the engine docs](https://poke-engine.readthedocs.io/en/latest/) for more information.

The engine must be built from source if installing locally so you must have rust installed on your machine.

### Re-Installing the Engine

It is common to want to re-install the engine for different generations of Pokémon.

`pip` will used cached .whl artifacts when installing packages
and cannot detect the `--config-settings` flag that was used to build the engine.

The following command will ensure that the engine is re-installed properly:
```shell
pip uninstall -y poke-engine && pip install -v --force-reinstall --no-cache-dir poke-engine --config-settings="build-args=--features poke-engine/<GENERATION> --no-default-features"
```

Or using the Makefile:
```shell
make poke_engine GEN=<generation>
```

For example, to re-install the engine for generation 4:
```shell
make poke_engine GEN=gen4
```

If you are working from the vendored copy in this repository, use:
```shell
make poke_engine_local GEN=<generation>
```

## Recent Improvements

This fork includes several enhancements over the base foul-play implementation:

### AI Improvements

- **Dynamic Search Depth**: Adjusts MCTS search depth based on battle count for better early-game speed and late-game depth
  - Use `--state-search-depth 0` to enable (default is 2)
  - Early battles (< threshold): shallow search (depth 1)
  - Late battles (> threshold): deep search (depth 3-4)
  - Configurable via `--dynamic-search-opts-for-max` and `--dynamic-search-battle-threshold`

- **Sleep/Rest Talk Awareness**: Tracks sleep turns to prevent overuse of Sleep Talk
  - Automatically reduces Sleep Talk scoring after 2-3 consecutive turns
  - Prevents getting locked into Sleep Talk for extended periods

- **Hazard Stacking Prevention**: Prevents redundant hazard placement
  - Stealth Rock: max 1 layer
  - Spikes: max 3 layers  
  - Toxic Spikes: max 2 layers
  - Heavily penalizes hazard moves when already at maximum layers

- **Trick Room Awareness**: Properly understands Trick Room mechanics
  - Logs Trick Room state and turns remaining
  - Informs move selection that speed priorities are reversed

### Configuration Options

New command-line arguments for fine-tuning:

```bash
# Search depth control
--state-search-depth 0          # Enable dynamic search depth (0=dynamic, 1-4=fixed)
--dynamic-search-opts-for-max 4 # Max options to search at max depth
--dynamic-search-battle-threshold 20  # Battles before depth increases

# Battle settings
--disable-battle-timer          # Disable 5-minute timer for deeper searches

# Format-specific
--expected-mods scalemons camomons  # Tell bot to expect stat modifications
--disable-tera-to-stellar-type      # Disallow Stellar terastallization (for Draft formats)
```

## Credits

Core improvements integrated from [Agetian/showdown-battlebot](https://github.com/Agetian/showdown-battlebot):
- Dynamic search depth algorithm
- Sleep/Rest Talk tracking and management
- Hazard stacking prevention
- Weather-aware move and switch heuristics
- Weather maintenance strategy heuristics
- Status-aware move and switch heuristics
- Enhanced generation-specific logic
- Trick Room state awareness

Base project: [pmariglia/foul-play](https://github.com/pmariglia/foul-play)
