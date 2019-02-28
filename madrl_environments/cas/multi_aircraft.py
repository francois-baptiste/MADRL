import copy
import math
from math import pi
import sys
import matplotlib.pyplot as plt
from matplotlib import animation

import gym
import numpy as np
import random
from gym import spaces
from gym.utils import colorize, seeding
from six.moves import xrange
import Box2D
from madrl_environments import AbstractMAEnv, Agent

from rltools.util import EzPickle

# Multi-agent settings
MIN_AGENTS = 10
MAX_AGENTS = 60

DEST_THRESHOLD = 100 # in m

# Sensor model parameters
SENSING_RANGE = 1000 # in m
SENSOR_CAPACITY = 4
NMAC_RANGE = 150 # in s
TERM_PAIRWISE_OBS = [1] * 4

# Agent continuous dynamics properties
ACTION_IND_ACC = 0
ACTION_IND_TURN = 1
ACTION_DIM = 2
MIN_V = 15 # in m/s
MAX_V = 45 # in m/s
MIN_ACC = 0 # in m/s^2
MAX_ACC = 5 # in m/s^2
MIN_TURN_RATE = 0 # in rad/s
MAX_TURN_RATE = np.deg2rad(10) # in rad/s

# Training settings
DT = 1 # in s
MAX_TIME_STEPS = 2000 # in s
TRAINING_SCENARIOS = ['circle', 'annulus', 'square']

# For training scenario: on circle
MIN_CIRCLE_RADIUS = 3000 # in m 
MAX_CIRCLE_RADIUS = 4000 # in m 

# For training scenario: in annulus
INNER_RADIUS = 2000 # in m 
OUTTER_RADIUS = 4000 # in m 

# For training scenario: in square space
AIRSPACE_WIDTH = 6000 # in m 

# Angle range helper
# wrap an angle in (- pi, pi] 
def norm_angle(angle):
    return (angle + pi) % (2 * pi) - pi

# Agents sorting helper
def agent_dist(a, b):
    return np.sqrt((a.x - b.x)**2 + (a.y - b.y)**2)

def closer(a, b, ref):
    return True if agent_dist(a, ref) <= agent_dist(b, ref) else False

def partition(ref, arr, low, high): 
    i = low - 1
    pivot = arr[high]
    for j in range(low, high): 
        if closer(arr[j], pivot, ref):
            i += 1
            arr[i], arr[j] = arr[j], arr[i] 
    arr[i+1], arr[high] = arr[high], arr[i+1] 
    return i + 1

def sort_agents(ref, arr, low, high): 
    if low < high:
        parti = partition(ref, arr, low, high) 
        sort_agents(ref, arr, low, parti - 1) 
        sort_agents(ref, arr, parti + 1, high) 

# Agent definition
class Aircraft(Agent): 

    def __init__(self, env):
        self.env = env
        self.v = MIN_V
        self.turn_rate = 0
        if self.env.training_mode == 'circle':
            r = self.env.circle_radius
            init_position_angle = random.uniform(- pi, pi)
            self.x = r * np.cos(init_position_angle)
            self.y = r * np.sin(init_position_angle)
            self.dest_x = r * np.cos(init_position_angle + pi)
            self.dest_y = r * np.sin(init_position_angle + pi)

        elif self.env.training_mode == 'annulus':
            init_r = random.uniform(INNER_RADIUS, OUTTER_RADIUS)
            dest_r = random.uniform(INNER_RADIUS, OUTTER_RADIUS)
            init_position_angle = random.uniform(- pi, pi)
            self.x = init_r * np.cos(init_position_angle)
            self.y = init_r * np.sin(init_position_angle)
            self.dest_x = dest_r * np.cos(init_position_angle + pi)
            self.dest_y = dest_r * np.sin(init_position_angle + pi)

        elif self.env.training_mode == 'square':
            self.x = AIRSPACE_WIDTH * random.uniform(0, 1)
            self.y = AIRSPACE_WIDTH * random.uniform(0, 1)
            self.dest_x = AIRSPACE_WIDTH * random.uniform(0, 1)
            self.dest_y = AIRSPACE_WIDTH * random.uniform(0, 1)
            
        self.heading = norm_angle(math.atan2(self.dest_y - self.y, self.dest_x - self.x))
        self.dist_to_dest = np.sqrt((self.dest_y - self.y)**2 + (self.dest_x - self.x)**2)
        self.init_dist_to_dest = self.dist_to_dest
        self.prev_dist_to_dest = self.dist_to_dest

        # init agent obs
        self.obs = [
            (self.v - MIN_V) / MAX_V, # [0, 1]
            self.turn_rate / MAX_TURN_RATE, # [-1, 1]
            self.dist_to_dest / self.init_dist_to_dest, # [0, 1],
            norm_angle(math.atan2(self.dest_y - self.y, self.dest_x - self.x) \
                - self.heading) / pi # [-1, 1], Angle of destination wrt agent 
        ] + [1] * 4 * self.env.sensor_capacity

        # obs from intruders [dist, angle_wrt_heading, heading_diff, v_int]

        assert len(self.obs) == 4 + 4 * self.env.sensor_capacity

    def apply_action(self, action):
        # Entries of action vector in [-1, 1]
        self.v = np.clip(self.v + MAX_ACC * action[ACTION_IND_ACC] * DT, MIN_V, MAX_V) 
        self.turn_rate = MAX_TURN_RATE * action[ACTION_IND_TURN]
        self.heading = norm_angle(self.heading + self.turn_rate * DT)
        # Update coordinates
        self.x += np.cos(self.heading) * self.v * DT
        self.y += np.sin(self.heading) * self.v * DT
        self.prev_dist_to_dest = self.dist_to_dest
        self.dist_to_dest = np.sqrt((self.dest_y - self.y)**2 + (self.dest_x - self.x)**2)

    def get_pairwise_obs(self, intruder):
        intruder_pos_angle = math.atan2(intruder.y - self.y, intruder.x - self.x)
        obs = [
            agent_dist(self, intruder) / SENSING_RANGE,
            norm_angle(intruder_pos_angle - self.heading) / pi, 
            norm_angle(intruder.heading - self.heading) / pi, 
            intruder.v / MAX_V
        ]
        return obs

    def get_observation(self):
        # Update own velocity and goal info
        self.obs = [
            self.v / MAX_V, # [0, 1]
            self.turn_rate / MAX_TURN_RATE, # [-1, 1]
            self.dist_to_dest / self.init_dist_to_dest, # [0, 1],
            norm_angle(math.atan2(self.dest_y - self.y, self.dest_x - self.x) \
                - self.heading) / pi # [-1, 1], Angle of destination wrt agent 
        ]

        # Construct intruders list
        intruders = []
        for agent in self.env.aircraft:
            if agent_dist(self, agent) > 0 and agent_dist(self, agent) <= SENSING_RANGE:
                intruders.append(agent)

        if self.env.sensor_mode == 'sector':
            pass # TODO
        elif self.env.sensor_mode == 'closest':
            if len(intruders) > 0:
                # Ascending order in terms of distance from ownship
                sort_agents(self, intruders, 0, len(intruders) - 1) 
                for i in range(self.env.sensor_capacity):
                    if i < len(intruders):
                        self.obs += self.get_pairwise_obs(intruders[i])
                    else:
                        self.obs += TERM_PAIRWISE_OBS
            else: # no intruder
                self.obs += TERM_PAIRWISE_OBS * self.env.sensor_capacity

        assert len(self.obs) == 4 + 4 * self.env.sensor_capacity

        # Add Gaussian noises:


        return self.obs

    def nmac(self):
        intruder_dist_ind = [4 * (i + 1) for i in range(self.env.sensor_capacity)]
        return True if any(np.array(self.obs)[intruder_dist_ind] 
            < NMAC_RANGE / SENSING_RANGE) else False

    def arrival(self):
        return True if self.dist_to_dest < DEST_THRESHOLD else False

    def reward(self):
        reward = 0
        if self.arrival():
            reward += self.env.rew_arrival
        else:
            reward += self.env.rew_closing * (self.prev_dist_to_dest - self.dist_to_dest)
        if self.nmac():
            reward += self.env.rew_nmac
        if np.abs(self.turn_rate) > 0.7 * MAX_TURN_RATE:
            reward += self.env.rew_large_turnrate * np.abs(self.turn_rate)

        return reward

    @property
    def observation_space(self):
        return

    @property
    def action_space(self):
        if self.env.continuous_action_space:
            return spaces.Box(low=-1, high=1, shape=(ACTION_DIM,))
        else:
            pass # TODO

# Environment definition
class MultiAircraftEnv(AbstractMAEnv, EzPickle):

    def __init__(self, 
                 continuous_action_space=True,
                 n_agents=MIN_AGENTS,
                 constant_n_agents=False,
                 training_mode='circle', 
                 sensor_mode='closest',
                 sensor_capacity=SENSOR_CAPACITY,
                 max_time_steps=MAX_TIME_STEPS,
                 one_hot=False,
                 render_option=False,
                 speed_noise=1e-3,
                 turn_rate_noise=1e-3,
                 position_noise=1e-3, 
                 angle_noise=1e-3, 
                 reward_mech='local',
                 rew_arrival=15,
                 rew_closing=2.5,
                 rew_nmac=-15,
                 rew_large_turnrate=-0.1):

        self.t = 0
        self.aircraft = []
        self.n_agents = n_agents
        self.continuous_action_space = continuous_action_space
        self.constant_n_agents = constant_n_agents
        self.training_mode = training_mode
        self.sensor_mode = sensor_mode
        self.sensor_capacity = sensor_capacity
        self.max_time_steps = max_time_steps
        self.one_hot = one_hot
        self.render_option = render_option
        # Observation noises:
        self.speed_noise = 1e-3
        self.turn_rate_noise = 1e-3
        self.position_noise = 1e-3
        self.angle_noise = 1e-3
        # Reward settings:
        self._reward_mech = reward_mech
        self.rew_arrival = rew_arrival
        self.rew_closing = rew_closing
        self.rew_nmac = rew_nmac
        self.rew_large_turnrate = rew_large_turnrate
        self.seed()

    def get_param_values(self):
        return self.__dict__

    @property
    def reward_mech(self):
        return self._reward_mech

    @property
    def agents(self):
        return self.aircraft

    def seed(self, seed=None):
        self.np_random, seed_ = seeding.np_random(seed)
        return [seed_]

    def _destroy(self):
        for agent in self.aircraft:
            del agent
        self.aircraft = []

    def reset(self):
        # self._destroy()
        self.aircraft = []

        self.training_mode = random.choice(TRAINING_SCENARIOS)
        if self.training_mode == 'circle':
            self.circle_radius = random.choice(range(MIN_CIRCLE_RADIUS, MAX_CIRCLE_RADIUS))

        for _ in range(self.n_agents):
            self.aircraft.append(Aircraft(self)) # Create ref links

        # Return an obs with zero actions
        return self.step(np.array([0, 0] * self.n_agents))[0]

    def n_agents_control(self):
        if self.constant_n_agents:
            for i in range(len(self.aircraft)):
                if self.aircraft[i].arrival():
                    del self.aircraft[i]
                    self.aircraft.insert(i, Aircraft(self))
        else:
            i = 0
            while i < len(self.aircraft):
                if self.aircraft[i].arrival():
                    del self.aircraft[i]
                i += 1

    def step(self, actions):
        obs = []
        done = False
        rewards = np.zeros(self.n_agents)

        # Apply actions and update dynamics
        act_vec = np.reshape(actions, (self.n_agents, ACTION_DIM))
        for i in range(self.n_agents):
            self.aircraft[i].apply_action(act_vec[i])

        # Check if episode is done
        done = (len(self.aircraft) == 0 or self.t > MAX_TIME_STEPS)

        # Get obs (list of arrays)
        for i in range(self.n_agents):
            agent_obs = self.aircraft[i].get_observation() # obs with Gaussian noises
            obs.append(np.array(agent_obs))

            # ID necessary?
            # if self.one_hot:
            #     obs.extend(np.eye(MAX_AGENTS)[i])
            # else:
            #     obs.append(float(i) / self.n_agents)

        if self.render_option:
            plt.ion()
            self.render()

        # Get rewards
        for i in range(self.n_agents):
            rewards[i] = self.aircraft[i].reward()

        # Increment time step
        self.t += 1

        if self.reward_mech == 'local':
            return obs, rewards, done, {}
        return obs, [rewards.mean()] * self.n_agents, done, {} # Globally averaged rew

    def render(self):
        for ac in self.aircraft:
            plt.scatter(ac.x, ac.y, marker="o", color="blue", s=12)
            arrow_len = ac.v * 20
            plt.arrow(
                ac.x, ac.y,
                np.cos(ac.heading) * arrow_len,
                np.sin(ac.heading) * arrow_len,
                width=0.6,
                facecolor="black")
            plt.scatter(ac.dest_x, ac.dest_y, marker=",", color="magenta", s=12)
            plt.plot([ac.x, ac.dest_x], [ac.y, ac.dest_y], 
                linestyle="--", color="black", linewidth=0.3)
            for i in range(self.sensor_capacity):
                if ac.obs[4 + i * 4] < 1:
                    rho = ac.obs[4 + i * 4] * SENSING_RANGE
                    phi = ac.heading + ac.obs[4 + i * 4 + 1] * pi
                    plt.plot([ac.x, ac.x + rho * np.cos(phi)], [ac.y, ac.y + rho * np.sin(phi)],
                        linestyle="--", color="red", linewidth=0.3)
        
        if self.training_mode == 'circle':
            th = np.linspace(-pi, pi, 30)
            plt.plot(self.circle_radius * np.cos(th), self.circle_radius * np.sin(th), 
                linestyle="--", color="green", linewidth=0.4)
            plt.xlim((-self.circle_radius * 1.2, self.circle_radius * 1.2))
            plt.ylim((-self.circle_radius * 1.2, self.circle_radius * 1.2))
        elif self.training_mode == 'square':
            plt.xlim((0, AIRSPACE_WIDTH))
            plt.ylim((0, AIRSPACE_WIDTH))
        elif self.training_mode == 'annulus':
            th = np.linspace(-pi, pi, 30)
            plt.plot(INNER_RADIUS * np.cos(th), INNER_RADIUS * np.sin(th), 
                linestyle="--", color="green", linewidth=0.4)
            plt.plot(OUTTER_RADIUS * np.cos(th), OUTTER_RADIUS * np.sin(th), 
                linestyle="--", color="green", linewidth=0.4)
            plt.xlim((-OUTTER_RADIUS * 1.2, OUTTER_RADIUS * 1.2))
            plt.ylim((-OUTTER_RADIUS * 1.2, OUTTER_RADIUS * 1.2))

        plt.xlabel("x (m)")
        plt.ylabel("y (m)")
        plt.title(str(self.t) + ': ' + str(len(self.aircraft)))
        plt.axis("equal")
        plt.draw()
        plt.pause(0.0001)
        plt.clf()




