docker:
	docker build . -t foul-play:latest --build-arg GEN=$(GEN)

clean_logs:
	rm logs/*

clean_data_cache:
	rm -rf data/pkmn_sets_cache/*
	rm -rf data/smogon_stats_cache/*

test:
	ruff check
	pytest tests

fmt:
	ruff format

lint:
	ruff check --fix

poke_engine:
	@poke_engine_version=$$(grep -oE 'poke-engine==[0-9]+\.[0-9]+\.[0-9]+' requirements.txt); \
	echo "Installing $$poke_engine_version with feature $(GEN)"; \
	pip uninstall -y poke-engine && pip install -v --force-reinstall --no-cache-dir $$poke_engine_version --config-settings="build-args=--features poke-engine/$(GEN) --no-default-features"

# This installs the vendored pmariglia/poke-engine repository checked into vendor/poke-engine
poke_engine_local:
	pip uninstall -y poke-engine && pip install -v --force-reinstall --no-cache-dir ./vendor/poke-engine/poke-engine-py --config-settings="build-args=--features poke-engine/$(GEN) --no-default-features"
