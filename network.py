''' stuff related to neural networks '''

# TODO
# pre-extract np stuff
# use in-place arithmetic when possible
# take care with rng seeding, etc.
# compare sparse array to regular numpy array
# figure out how to vectorize the recurrent training step (no for loop over plastic units)

import numpy as np
from scipy.sparse import csr_matrix
from scipy.stats import norm

prng_seed = 1234
prng = np.random.RandomState(prng_seed)


class Generator(object):
    ''' a randomly-connected recurrent neural network '''

    def __init__(self, n_units, p_connect, syn_strength, p_plastic):
        self.n_units = n_units
        self.p_connect = p_connect
        self.syn_strength = syn_strength
        self.p_plastic = p_plastic

        self.n_plastic = int(np.round(n_units * p_plastic))
        self.scale_recurr = syn_strength / np.sqrt(p_connect * n_units)


class Trial(object):
    ''' a single trial and its (timing) characteristics '''
    # defaults not currently designed to be configured
    extra_train_ms = 150
    extra_end_ms = 200
    plot_points = 500

    def __init__(self, length_ms, spacing, time_step, start_train_ms, end_train_ms):
        self.length_ms = length_ms
        self.spacing = spacing
        self.time_step = time_step
        self.start_train_ms = start_train_ms
        self.end_train_ms = end_train_ms

        self.start_train_n = int(np.round(start_train_ms / time_step))
        self.end_train_n = int(np.round(end_train_ms / time_step))

        self.max_ms = self.end_train_ms + self.extra_end_ms
        self.n_steps = int(np.floor(self.max_ms / self.time_step))
        self.time_ms = np.arange(0, self.max_ms, self.time_step)

        # plotting
        self.plot_skip = np.ceil(self.n_steps / self.plot_points)
        if self.plot_skip % 2 == 0:
            self.plot_skip += 1


class Input(object):
    ''' an input to a network '''

    def __init__(self, trial_obj, n_units, value, start_ms, duration_ms):
        self.n_units = n_units
        self.value = value
        self.start_ms = start_ms
        self.duration_ms = duration_ms

        # making input time series
        startpulse_idx = int(np.round(start_ms / trial_obj.time_step))
        pulsedur_samps = int(np.round(duration_ms / trial_obj.time_step))

        input_series = np.zeros((n_units, trial_obj.n_steps))
        input_series[0, startpulse_idx:startpulse_idx + pulsedur_samps - 1] = value
        self.series = input_series


class Output(object):
    ''' a (desired) output from a network '''

    def __init__(self, trial_obj, n_units, value, center_ms, width_ms, baseline_val):
        self.n_units = n_units
        self.value = value
        self.center_ms = center_ms
        self.width_ms = width_ms
        self.baseline_val = baseline_val

        # making output time series
        bell = norm.pdf(trial_obj.time_ms, center_ms, width_ms).reshape(1, -1)
        bell /= np.max(bell)  # by the way, this is a fast way to normalize a vector to 1
        self.series = bell * (value - baseline_val) + baseline_val


class Trainer(object):
    ''' training object.
        consumes network, input, output, and trial objects.
        defines some parameters relevant to training.
        simulates a neural network and trains its weights.
        stages are innate, recurrent, readout, and test '''

    def __init__(self, generator_obj, input_obj, output_obj, trial_obj,
                 tau_ms, sigmoid, noise_harvest, noise_train,
                 n_trials_recurrent, n_trials_readout, n_trials_test):
        # other objs
        self.gen = generator_obj
        self.inp = input_obj
        self.out = output_obj
        self.tr = trial_obj

        # training params
        self.tau_ms = tau_ms
        self.sigmoid = sigmoid
        self.noise_train = noise_train
        self.noise_harvest = noise_harvest

        # number of trials
        self.n_trials_recurrent = n_trials_recurrent
        self.n_trials_readout = n_trials_readout
        self.n_trials_test = n_trials_test

        # constant for dividing change in firing rate
        self.time_div = tau_ms / self.tr.time_step

    def initialize_weights(self):
        # generator recurrent weights (wxx)
        wxx_mask = prng.rand(self.gen.n_units, self.gen.n_units)  # uniform distribution!
        wxx_mask[wxx_mask <= self.gen.p_connect] = 1
        wxx_mask[wxx_mask < 1] = 0
        wxx_vals = prng.normal(scale=self.gen.scale_recurr,
                               size=(self.gen.n_units, self.gen.n_units))
        wxx_nonsparse = wxx_vals * wxx_mask
        np.fill_diagonal(wxx_nonsparse, 0)
        self.gen.wxx_ini = csr_matrix(wxx_nonsparse)

        # input => RRN (winputx)
        self.inp.winputx_ini = prng.normal(scale=1,
                                           size=(self.gen.n_units, self.inp.n_units))

        # RRN => output (wxout)
        self.out.wxout_ini = prng.normal(scale=1 / np.sqrt(self.gen.n_units),
                                         size=(self.out.n_units, self.gen.n_units))

    def harvest_innate(self):
        # assigning recurrent and input weights to workspace names
        wxx = self.gen.wxx_ini
        winputx = self.inp.winputx_ini

        # creating all noise ahead of time
        all_noise = self.noise_harvest * prng.normal(scale=np.sqrt(self.tr.time_step),
                                                     size=(self.gen.n_units, self.tr.n_steps))

        # what we are really interested in: the innate trajectory
        x_history = np.empty((self.gen.n_units, self.tr.n_steps))

        # creating initial conditions for firing rate & activation level
        x_lvl = 2 * prng.rand(self.gen.n_units, 1) - 1
        x_fr = self.sigmoid(x_lvl)

        for t in range(self.tr.n_steps):
            x_lvl_update = wxx * x_fr + winputx * self.inp.series[:, t] + all_noise[:, [t]]
            x_lvl += (-x_lvl + x_lvl_update) / self.time_div
            x_fr = self.sigmoid(x_lvl)
            x_history[:, [t]] = x_fr

        self.gen.innate = x_history  # save the innate trajectory

    def harvest_innate2(self):
        ''' version which uses the @ operator instead '''

        # assigning recurrent and input weights to workspace names
        wxx = self.gen.wxx_ini
        winputx = self.inp.winputx_ini

        # creating all noise ahead of time
        all_noise = self.noise_harvest * prng.normal(scale=np.sqrt(self.tr.time_step),
                                                     size=(self.gen.n_units, self.tr.n_steps))

        # what we are really interested in: the innate trajectory
        x_history = np.empty((self.gen.n_units, self.tr.n_steps))

        # creating initial conditions for firing rate & activation level
        x_lvl = 2 * prng.rand(self.gen.n_units) - 1
        x_fr = self.sigmoid(x_lvl)

        for t in range(self.tr.n_steps):
            x_lvl_update = wxx @ x_fr + winputx @ self.inp.series[:, t] + all_noise[:, t]
            x_lvl += (-x_lvl + x_lvl_update) / self.time_div
            x_fr = self.sigmoid(x_lvl)
            x_history[:, t] = x_fr

        self.gen.innate = x_history  # save the innate trajectory

    def train_recurrent(self):
        # assigning recurrent and input weights to workspace names
        wxx = self.gen.wxx_ini
        winputx = self.inp.winputx_ini

        # creating all noise ahead of time
        all_noise = self.noise_train * prng.normal(scale=np.sqrt(self.tr.time_step),
                                                   size=(self.gen.n_units, self.n_trials_recurrent, self.tr.n_steps))

        # creating all initial condition for firing rate & activation level ahead of time
        x_lvl_init = 2 * prng.rand(self.gen.n_units, self.n_trials_recurrent) - 1
        x_fr_init = self.sigmoid(x_lvl_init)

        # trials are non-parallel
        for trial in range(self.n_trials_recurrent):

            x_lvl = x_lvl_init[:, [trial]]
            x_fr = x_fr_init[:, [trial]]

            # time steps are non-parallel
            for t in range(self.tr.n_steps):
                x_lvl_update = wxx * x_fr + winputx * self.inp.series[:, t] + all_noise[:, trial, [t]]
                x_lvl += (-x_lvl + x_lvl_update) / self.time_div
                x_fr = self.sigmoid(x_lvl)

                # updating plastic units will eventually be IN parallel (hopefully)




    def train_readout(self):
        pass

    def test(selfs):
        pass
