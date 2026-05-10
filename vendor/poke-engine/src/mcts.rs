use crate::engine::evaluate::evaluate;
use crate::engine::damage_calc::type_effectiveness_modifier;
use crate::engine::generate_instructions::generate_instructions_from_move_pair;
use crate::engine::state::MoveChoice;
use crate::choices::MoveCategory;
use crate::instruction::StateInstructions;
use crate::state::{Pokemon, Side, State};
use rand::distr::weighted::WeightedIndex;
use rand::prelude::*;
use rand::rng;
use std::collections::HashMap;
use std::time::Duration;

fn sigmoid(x: f32) -> f32 {
    // Tuned so that ~200 points is very close to 1.0
    1.0 / (1.0 + (-0.0125 * x).exp())
}

const PRIOR_NEUTRAL: f32 = 0.5;
const PRIOR_MIN: f32 = 0.25;
const PRIOR_MAX: f32 = 0.85;

fn best_damage_score(attacker: &Pokemon, defender: &Pokemon) -> f32 {
    let mut best_score = 0.0;
    for mv in attacker.moves.into_iter() {
        if !matches!(mv.choice.category, MoveCategory::Physical | MoveCategory::Special) {
            continue;
        }

        let effectiveness = type_effectiveness_modifier(&mv.choice.move_type, defender);
        let move_score = (mv.choice.base_power.max(1.0) / 100.0) * effectiveness;
        if move_score > best_score {
            best_score = move_score;
        }
    }

    best_score
}

fn matchup_score(attacker: &Pokemon, defender: &Pokemon) -> f32 {
    best_damage_score(attacker, defender) - best_damage_score(defender, attacker)
}

fn pivot_move_prior(side: &Side, opponent: &Side, move_choice: &MoveChoice) -> f32 {
    let active = side.get_active_immutable();
    let opponent_active = opponent.get_active_immutable();

    let move_index = match move_choice {
        MoveChoice::Move(index) | MoveChoice::MoveTera(index) | MoveChoice::MoveMega(index) => {
            index
        }
        _ => return PRIOR_NEUTRAL,
    };

    let active_move = &active.moves[move_index].choice;
    if !active_move.flags.pivot {
        return PRIOR_NEUTRAL;
    }

    let current_score = matchup_score(active, opponent_active);
    let mut best_followup_score = current_score;

    for (reserve_index, reserve) in side.pokemon.into_iter().enumerate() {
        if reserve.hp <= 0 {
            continue;
        }
        if reserve_index == side.active_index as usize {
            continue;
        }

        let reserve_score = matchup_score(reserve, opponent_active);
        if reserve_score > best_followup_score {
            best_followup_score = reserve_score;
        }
    }

    let mut prior = PRIOR_NEUTRAL;

    let followup_delta = (best_followup_score - current_score).max(0.0);
    if followup_delta > 0.0 {
        prior += (followup_delta * 0.12).min(0.25);
    }

    if active.hp * 2 <= active.maxhp {
        prior += 0.06;
    }

    if active.speed < opponent_active.speed {
        prior += 0.05;
    }

    prior.clamp(PRIOR_MIN, PRIOR_MAX)
}

#[derive(Debug)]
pub struct Node {
    pub root: bool,
    pub parent: *mut Node,
    pub children: HashMap<(usize, usize), Vec<Node>>,
    pub times_visited: u32,

    // represents the instructions & s1/s2 moves that led to this node from the parent
    pub instructions: StateInstructions,
    pub s1_choice: u8,
    pub s2_choice: u8,

    // represents the total score and number of visits for this node
    // de-coupled for s1 and s2
    pub s1_options: Option<Vec<MoveNode>>,
    pub s2_options: Option<Vec<MoveNode>>,
}

impl Node {
    fn new() -> Node {
        Node {
            root: false,
            parent: std::ptr::null_mut(),
            instructions: StateInstructions::default(),
            times_visited: 0,
            children: HashMap::new(),
            s1_choice: 0,
            s2_choice: 0,
            s1_options: None,
            s2_options: None,
        }
    }
    unsafe fn populate(
        &mut self,
        state: &State,
        s1_options: Vec<MoveChoice>,
        s2_options: Vec<MoveChoice>,
    ) {
        let s1_options_vec: Vec<MoveNode> = s1_options
            .iter()
            .map(|x| MoveNode::new(x.clone(), pivot_move_prior(&state.side_one, &state.side_two, x)))
            .collect();
        let s2_options_vec: Vec<MoveNode> = s2_options
            .iter()
            .map(|x| MoveNode::new(x.clone(), pivot_move_prior(&state.side_two, &state.side_one, x)))
            .collect();

        self.s1_options = Some(s1_options_vec);
        self.s2_options = Some(s2_options_vec);
    }

    pub fn maximize_ucb_for_side(&self, side_map: &[MoveNode]) -> usize {
        let mut choice = 0;
        let mut best_ucb1 = f32::MIN;
        for (index, node) in side_map.iter().enumerate() {
            let this_ucb1 = node.ucb1(self.times_visited);
            if this_ucb1 > best_ucb1 {
                best_ucb1 = this_ucb1;
                choice = index;
            }
        }
        choice
    }

    pub unsafe fn selection(&mut self, state: &mut State) -> (*mut Node, usize, usize) {
        let return_node = self as *mut Node;
        if self.s1_options.is_none() {
            let (s1_options, s2_options) = state.get_all_options();
            self.populate(state, s1_options, s2_options);
        }

        let s1_mc_index = self.maximize_ucb_for_side(&self.s1_options.as_ref().unwrap());
        let s2_mc_index = self.maximize_ucb_for_side(&self.s2_options.as_ref().unwrap());
        let child_vector = self.children.get_mut(&(s1_mc_index, s2_mc_index));
        match child_vector {
            Some(child_vector) => {
                let child_vec_ptr = child_vector as *mut Vec<Node>;
                let chosen_child = self.sample_node(child_vec_ptr);
                state.apply_instructions(&(*chosen_child).instructions.instruction_list);
                (*chosen_child).selection(state)
            }
            None => (return_node, s1_mc_index, s2_mc_index),
        }
    }

    unsafe fn sample_node(&self, move_vector: *mut Vec<Node>) -> *mut Node {
        let mut rng = rng();
        let weights: Vec<f64> = (*move_vector)
            .iter()
            .map(|x| x.instructions.percentage as f64)
            .collect();
        let dist = WeightedIndex::new(weights).unwrap();
        let chosen_node = &mut (&mut *move_vector)[dist.sample(&mut rng)];
        let chosen_node_ptr = chosen_node as *mut Node;
        chosen_node_ptr
    }

    pub unsafe fn expand(
        &mut self,
        state: &mut State,
        s1_move_index: usize,
        s2_move_index: usize,
    ) -> *mut Node {
        let s1_move = &self.s1_options.as_ref().unwrap()[s1_move_index].move_choice;
        let s2_move = &self.s2_options.as_ref().unwrap()[s2_move_index].move_choice;
        // if the battle is over or both moves are none there is no need to expand
        if (state.battle_is_over() != 0.0 && !self.root)
            || (s1_move == &MoveChoice::None && s2_move == &MoveChoice::None)
        {
            return self as *mut Node;
        }
        let should_branch_on_damage = self.root || (*self.parent).root;
        let mut new_instructions =
            generate_instructions_from_move_pair(state, s1_move, s2_move, should_branch_on_damage);
        let mut this_pair_vec = Vec::with_capacity(new_instructions.len());
        for state_instructions in new_instructions.drain(..) {
            let mut new_node = Node::new();
            new_node.parent = self;
            new_node.instructions = state_instructions;
            new_node.s1_choice = s1_move_index as u8;
            new_node.s2_choice = s2_move_index as u8;

            this_pair_vec.push(new_node);
        }

        // sample a node from the new instruction list.
        // this is the node that the rollout will be done on
        let new_node_ptr = self.sample_node(&mut this_pair_vec);
        state.apply_instructions(&(*new_node_ptr).instructions.instruction_list);
        self.children
            .insert((s1_move_index, s2_move_index), this_pair_vec);
        new_node_ptr
    }

    pub unsafe fn backpropagate(&mut self, score: f32, state: &mut State) {
        self.times_visited += 1;
        if self.root {
            return;
        }

        let parent_s1_movenode =
            &mut (*self.parent).s1_options.as_mut().unwrap()[self.s1_choice as usize];
        parent_s1_movenode.total_score += score;
        parent_s1_movenode.visits += 1;

        let parent_s2_movenode =
            &mut (*self.parent).s2_options.as_mut().unwrap()[self.s2_choice as usize];
        parent_s2_movenode.total_score += 1.0 - score;
        parent_s2_movenode.visits += 1;

        state.reverse_instructions(&self.instructions.instruction_list);
        (*self.parent).backpropagate(score, state);
    }

    pub fn rollout(&mut self, state: &mut State, root_eval: &f32) -> f32 {
        let battle_is_over = state.battle_is_over();
        if battle_is_over == 0.0 {
            let eval = evaluate(state);
            sigmoid(eval - root_eval)
        } else {
            if battle_is_over == -1.0 {
                0.0
            } else {
                battle_is_over
            }
        }
    }
}

#[derive(Debug)]
pub struct MoveNode {
    pub move_choice: MoveChoice,
    pub total_score: f32,
    pub visits: u32,
    pub prior: f32,
}

impl MoveNode {
    fn new(move_choice: MoveChoice, prior: f32) -> MoveNode {
        MoveNode {
            move_choice,
            total_score: 0.0,
            visits: 0,
            prior,
        }
    }

    pub fn ucb1(&self, parent_visits: u32) -> f32 {
        if self.visits == 0 {
            return 1000.0 + self.prior;
        }

        let exploration = if parent_visits <= 1 {
            0.0
        } else {
            (2.0 * (parent_visits as f32).ln() / self.visits as f32).sqrt()
        };

        let score = (self.total_score / self.visits as f32)
            + exploration
            + (self.prior - PRIOR_NEUTRAL) * 0.4;
        score
    }
    pub fn average_score(&self) -> f32 {
        let score = self.total_score / self.visits as f32;
        score
    }
}

#[derive(Clone)]
pub struct MctsSideResult {
    pub move_choice: MoveChoice,
    pub total_score: f32,
    pub visits: u32,
}

impl MctsSideResult {
    pub fn average_score(&self) -> f32 {
        if self.visits == 0 {
            return 0.0;
        }
        let score = self.total_score / self.visits as f32;
        score
    }
}

pub struct MctsResult {
    pub s1: Vec<MctsSideResult>,
    pub s2: Vec<MctsSideResult>,
    pub iteration_count: u32,
}

fn do_mcts(root_node: &mut Node, state: &mut State, root_eval: &f32) {
    let (mut new_node, s1_move, s2_move) = unsafe { root_node.selection(state) };
    new_node = unsafe { (*new_node).expand(state, s1_move, s2_move) };
    let rollout_result = unsafe { (*new_node).rollout(state, root_eval) };
    unsafe { (*new_node).backpropagate(rollout_result, state) }
}

pub fn perform_mcts(
    state: &mut State,
    side_one_options: Vec<MoveChoice>,
    side_two_options: Vec<MoveChoice>,
    max_time: Duration,
) -> MctsResult {
    let mut root_node = Node::new();
    unsafe {
        root_node.populate(state, side_one_options, side_two_options);
    }
    root_node.root = true;

    let root_eval = evaluate(state);
    let start_time = std::time::Instant::now();
    while start_time.elapsed() < max_time {
        for _ in 0..1000 {
            do_mcts(&mut root_node, state, &root_eval);
        }

        /*
        Cut off after 10 million iterations

        Under normal circumstances the bot will only run for 2.5-3.5 million iterations
        however towards the end of a battle the bot may perform tens of millions of iterations

        Beyond about 30 million iterations some floating point nonsense happens where
        MoveNode.total_score stops updating because f32 does not have enough precision

        I can push the problem farther out by using f64 but if the bot is running for 10 million iterations
        then it almost certainly sees a forced win
        */
        if root_node.times_visited == 10_000_000 {
            break;
        }
    }

    let result = MctsResult {
        s1: root_node
            .s1_options
            .as_ref()
            .unwrap()
            .iter()
            .map(|v| MctsSideResult {
                move_choice: v.move_choice.clone(),
                total_score: v.total_score,
                visits: v.visits,
            })
            .collect(),
        s2: root_node
            .s2_options
            .as_ref()
            .unwrap()
            .iter()
            .map(|v| MctsSideResult {
                move_choice: v.move_choice.clone(),
                total_score: v.total_score,
                visits: v.visits,
            })
            .collect(),
        iteration_count: root_node.times_visited,
    };

    result
}
