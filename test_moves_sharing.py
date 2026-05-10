"""
诊断脚本：检查pokémon对象是否在采样时意外共享moves列表
"""
import logging
from copy import deepcopy
from fp.battle import Pokemon
from fp.search.standard_battles import sample_pokemon
from data.pkmn_sets import TeamDatasets, SmogonSets

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# 初始化数据集
TeamDatasets.initialize("gen4ou", {"deoxysspeed", "darkrai", "kyogre"})
SmogonSets.initialize("gen4ou", {"deoxysspeed", "darkrai", "kyogre"})

# 创建三个pokémon对象
pkmns = [
    Pokemon("deoxysspeed", 80),
    Pokemon("darkrai", 80), 
    Pokemon("kyogre", 80),
]

# 跟踪moves列表对象的id
logger.warning(f"BEFORE sampling:")
for p in pkmns:
    logger.warning(f"  {p.name}: moves id={id(p.moves)}, moves={[m.name for m in p.moves]}")

# 采样每个pokémon
for p in pkmns:
    logger.warning(f"\n采样 {p.name}")
    logger.warning(f"  Before: moves id={id(p.moves)}, len={len(p.moves)}, content={[m.name for m in p.moves]}")
    sample_pokemon(p)
    logger.warning(f"  After:  moves id={id(p.moves)}, len={len(p.moves)}, content={[m.name for m in p.moves]}")

# 采样后检查
logger.warning(f"\nAFTER sampling:")
for p in pkmns:
    logger.warning(f"  {p.name}: moves id={id(p.moves)}, len={len(p.moves)}, moves={[m.name for m in p.moves]}")

# 检查是否有任何两个pokémon的moves列表是同一个对象
moves_ids = [id(p.moves) for p in pkmns]
if len(moves_ids) != len(set(moves_ids)):
    logger.error("🚨 FOUND SHARED MOVES LIST!")
    for i, p1 in enumerate(pkmns):
        for j, p2 in enumerate(pkmns):
            if i < j and id(p1.moves) == id(p2.moves):
                logger.error(f"  {p1.name} and {p2.name} share the same moves list!")
else:
    logger.info("✓ All pokémon have unique moves lists")
