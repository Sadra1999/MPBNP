#!/usr/bin/env python2
#-*- coding: utf-8 -*-

from __future__ import print_function
import sys, os.path
pkg_dir = os.path.dirname(os.path.realpath(__file__)) + '/./'
sys.path.append(pkg_dir)

import pyopencl.array
from collections import Counter
from BaseSampler import *
from scipy.stats import poisson

np.set_printoptions(suppress=True)

class IBPNoisyOrGibbs(BaseSampler):

    def __init__(self, cl_mode = True, inference_mode = True, cl_device = None,
                 alpha = 2.0, lam = 0.95, theta = 0.5, epislon = 0.01, init_k = 4):
        """Initialize the class.
        """
        BaseSampler.__init__(self, cl_mode, inference_mode, cl_device)

        if cl_mode:
            program_str = open(pkg_dir + './kernels/ibp_noisyor_cl.c', 'r').read()
            #utilities_str = open('kernels/utilities_cl.c', 'r').read()
            self.prg = cl.Program(self.ctx, program_str).build()
            #self.util = cl.Program(self.ctx, utilities_str).build()

        self.alpha = alpha # tendency to generate new features
        self.k = init_k    # initial number of features
        self.theta = theta # prior probability that a pixel is on in a feature image
        self.lam = lam # effecacy of a feature
        self.epislon = epislon # probability that a pixel is on by change in an actual image

    def read_csv(self, filepath, header=True):
        """Read the data from a csv file.
        """
        BaseSampler.read_csv(self, filepath, header)
        # convert the data to floats
        self.new_obs = []
        for row in self.obs:
            self.new_obs.append([int(_) for _ in row])
        self.obs = np.array(self.new_obs)
        self.d = len(self.obs[0])
        return

    def direct_read_obs(self, obs):
        BaseSampler.read_csv(self, obs)
        self.d = len(self.obs[0])
        
    def do_inference(self, init_y = None, init_z = None, output_z_file = None, output_y_file = None):
        """Perform inference on the given observations assuming data are generated by an IBP model
        with noisy-or as the likelihood function.
        @param init_y: An initial feature image matrix, where values are 0 or 1
        @param init_z: An initial feature ownership matrix, where values are 0 or 1
        """
        BaseSampler.do_inference(self, output_file=None)
        if init_y is None:
            init_y = np.random.randint(0, 2, (self.k, self.d))
        else:
            assert(type(init_y) is np.ndarray)
            assert(init_y.shape == (self.k, self.d))
        if init_z is None:
            init_z = np.random.randint(0, 2, (len(self.obs), self.k))
        else:
            assert(type(init_z) is np.ndarray)
            assert(init_z.shape == (len(self.obs), self.k))
            
        if self.cl_mode:
            return self._cl_infer_yz(init_y, init_z, output_y_file, output_z_file)
        else:
            return self._infer_yz(init_y, init_z, output_y_file, output_z_file)

    def _infer_yz(self, init_y, init_z, output_y_file = None, output_z_file = None):
        """Wrapper function to start the inference on y and z.
        This function is not supposed to directly invoked by an end user.
        @param init_y: Passed in from do_inference()
        @param init_z: Passed in from do_inference()
        """
        cur_y = init_y
        cur_z = init_z

        a_time = time()
        for i in xrange(self.niter):
            cur_y = self._infer_y(cur_y, cur_z)
            cur_y, cur_z = self._infer_z(cur_y, cur_z)
            #self._sample_lam(cur_y, cur_z)
            if output_y_file is not None and i >= self.burnin: 
                print_matrix_in_row(cur_y, output_y_file)
            if output_z_file is not None and i >= self.burnin: 
                print_matrix_in_row(cur_z, output_z_file)

        return -1, time() - a_time, None

    def _infer_y(self, cur_y, cur_z):
        """Infer feature images
        """
        # calculate the prior probability that a pixel is on
        y_on_log_prob = np.log(self.theta) * np.ones(cur_y.shape)
        y_off_log_prob = np.log(1. - self.theta) * np.ones(cur_y.shape)

        # calculate the likelihood
        on_loglik = np.empty(cur_y.shape)
        off_loglik = np.empty(cur_y.shape)
        for row in xrange(cur_y.shape[0]):
            affected_data_index = np.where(cur_z[:,row] == 1)
            for col in xrange(cur_y.shape[1]):
                old_value = cur_y[row, col]
                cur_y[row, col] = 1
                on_loglik[row, col] = self._loglik_nth(cur_y, cur_z, n = affected_data_index)
                cur_y[row, col] = 0
                off_loglik[row, col] = self._loglik_nth(cur_y, cur_z, n = affected_data_index)
                cur_y[row, col] = old_value

        # add to the prior
        y_on_log_prob += on_loglik
        y_off_log_prob += off_loglik

        ew_max = np.maximum(y_on_log_prob, y_off_log_prob)
        y_on_log_prob -= ew_max
        y_off_log_prob -= ew_max
        
        # normalize
        y_on_prob = np.exp(y_on_log_prob) / (np.exp(y_on_log_prob) + np.exp(y_off_log_prob))
        cur_y = np.random.binomial(1, y_on_prob)

        return cur_y

    def _infer_z(self, cur_y, cur_z):
        """Infer feature ownership
        """
        N = float(len(self.obs))
        z_col_sum = cur_z.sum(axis = 0)

        # calculate the IBP prior on feature ownership for existing features
        m_minus = z_col_sum - cur_z
        on_prob = m_minus / N
        off_prob = 1 - m_minus / N

        # add loglikelihood of data
        for row in xrange(cur_z.shape[0]):
            for col in xrange(cur_z.shape[1]):
                cur_z[row, col] = 1
                on_prob[row, col] = on_prob[row, col] * np.exp(self._loglik_nth(cur_y, cur_z, n = row))
                cur_z[row, col] = 0
                off_prob[row, col] = off_prob[row, col] * np.exp(self._loglik_nth(cur_y, cur_z, n = row))

        # normalize the probability
        on_prob = on_prob / (on_prob + off_prob)

        # sample the values
        cur_z = np.random.binomial(1, on_prob)

        # sample new features use importance sampling
        k_new = self._sample_k_new(cur_y, cur_z)
        if k_new:
            cur_y, cur_z = k_new

        # delete null features
        active_feat_col = np.where(cur_z.sum(axis = 0) > 0)
        cur_z = cur_z[:,active_feat_col[0]]
        cur_y = cur_y[active_feat_col[0],:]
        
        # update self.k
        self.k = cur_z.shape[1]
        
        return cur_y, cur_z

    def _sample_k_new(self, cur_y, cur_z):
        """Sample new features for all rows using Metropolis hastings.
        (This is a heuristic strategy aiming for easy parallelization in an 
        equivalent GPU implementation. We here have effectively treated the
        current Z as a snapshot frozen in time, and each new k is based on
        this frozen snapshot of Z. In a more correct procedure, we should
        go through the rows and sample k new for each row given all previously
        sampled new ks.)
        """
        N = float(len(self.obs))
        old_loglik = self._loglik(cur_y, cur_z)

        k_new_count = np.random.poisson(self.alpha / N)
        if k_new_count == 0: return False
            
        # modify the feature ownership matrix
        cur_z_new = np.hstack((cur_z, np.zeros((cur_z.shape[0], k_new_count), dtype=np.int32)))
        cur_z_new[:, [xrange(-k_new_count,0)]] = 1
        # propose feature images by sampling from the prior distribution
        cur_y_new = np.vstack((cur_y, np.random.binomial(1, self.theta, (k_new_count, self.d))))
    
        new_loglik = self._loglik(cur_y_new, cur_z_new)
        # normalization
        max_loglik = max(new_loglik, old_loglik)
        new_loglik -= max_loglik
        old_loglik -= max_loglik
        move_prob = 1 / (1 + np.exp(old_loglik - new_loglik));
        if random.random() < move_prob:
            return cur_y_new.astype(np.int32), cur_z_new.astype(np.int32)
        return False

    def _sample_lam(self, cur_y, cur_z):
        """Resample the value of lambda.
        """
        old_loglik = self._loglik(cur_y, cur_z)
        old_lam = self.lam
    
        # modify the feature ownership matrix
        self.lam = np.random.beta(1,1)
        new_loglik = self._loglik(cur_y, cur_z)
        move_prob = 1 / (1 + np.exp(old_loglik - new_loglik));
        if random.random() < move_prob:
            pass
        else:
            self.lam = old_lam

    def _sample_epislon(self, cur_y, cur_z):
        """Resample the value of epislon
        """
        old_loglik = self._loglik(cur_y, cur_z)
        old_epislon = self.epislon
    
        # modify the feature ownership matrix
        self.epislon = np.random.beta(1,1)
        new_loglik = self._loglik(cur_y, cur_z)
        move_prob = 1 / (1 + np.exp(old_loglik - new_loglik));
        if random.random() < move_prob:
            pass
        else:
            self.epislon = old_epislon

    def _loglik_nth(self, cur_y, cur_z, n):
        """Calculate the loglikelihood of the nth data point
        given Y and Z.
        """
        assert(cur_z.shape[1] == cur_y.shape[0])
                
        not_on_p = np.power(1. - self.lam, np.dot(cur_z[n], cur_y)) * (1. - self.epislon)
        loglik = np.log(np.abs(self.obs[n] - not_on_p)).sum()
        return loglik

    def _loglik(self, cur_y, cur_z):
        """Calculate the loglikelihood of data given Y and Z.
        """
        assert(cur_z.shape[1] == cur_y.shape[0])

        n_by_d = np.dot(cur_z, cur_y)
        not_on_p = np.power(1. - self.lam, n_by_d) * (1. - self.epislon)
        loglik_mat = np.log(np.abs(self.obs - not_on_p))
        return loglik_mat.sum()

    def _cl_infer_yz(self, init_y, init_z, output_y_file = None, output_z_file = None):
        """Wrapper function to start the inference on y and z.
        This function is not supposed to directly invoked by an end user.
        @param init_y: Passed in from do_inference()
        @param init_z: Passed in from do_inference()
        """
        cur_y = init_y.astype(np.int32)
        cur_z = init_z.astype(np.int32)
        d_obs = cl.Buffer(self.ctx, self.mf.READ_ONLY | self.mf.COPY_HOST_PTR, hostbuf=self.obs.astype(np.int32))

        gpu_time = 0
        total_time = 0
        for i in xrange(self.niter):
            a_time = time()
            cur_y = self._cl_infer_y(cur_y, cur_z, d_obs)
            cur_z = self._cl_infer_z(cur_y, cur_z, d_obs)
            gpu_time += time() - a_time
            cur_y, cur_z = self._cl_infer_k_new(cur_y, cur_z)
            if output_y_file is not None and i >= self.burnin: 
                print_matrix_in_row(cur_y, output_y_file)
            if output_z_file is not None and i >= self.burnin: 
                print_matrix_in_row(cur_z, output_z_file)
            total_time += time() - a_time

        return gpu_time, total_time, None

    def _cl_infer_y(self, cur_y, cur_z, d_obs):
        """Infer feature images
        """
        d_cur_y = cl.Buffer(self.ctx, self.mf.READ_WRITE | self.mf.COPY_HOST_PTR, hostbuf = cur_y.astype(np.int32))
        d_cur_z = cl.Buffer(self.ctx, self.mf.READ_WRITE | self.mf.COPY_HOST_PTR, hostbuf = cur_z.astype(np.int32))
        d_z_by_y = cl.Buffer(self.ctx, self.mf.READ_ONLY | self.mf.COPY_HOST_PTR, 
                             hostbuf = np.dot(cur_z, cur_y).astype(np.int32))
        d_rand = cl.Buffer(self.ctx, self.mf.READ_ONLY | self.mf.COPY_HOST_PTR, 
                           hostbuf=np.random.random(size = cur_y.shape).astype(np.float32))

        # calculate the prior probability that a pixel is on
        self.prg.sample_y(self.queue, cur_y.shape, None,
                          d_cur_y, d_cur_z, d_z_by_y, d_obs,
                          d_rand, #d_y_on_loglik.data, d_y_off_loglik.data,
                          np.int32(self.obs.shape[0]), np.int32(self.obs.shape[1]), np.int32(cur_y.shape[0]),
                          np.float32(self.lam), np.float32(self.epislon), np.float32(self.theta))

        cl.enqueue_copy(self.queue, cur_y, d_cur_y)
        return cur_y

    def _cl_infer_z(self, cur_y, cur_z, d_obs):
        """Infer feature ownership
        """
        d_cur_y = cl.Buffer(self.ctx, self.mf.READ_WRITE | self.mf.COPY_HOST_PTR, hostbuf = cur_y.astype(np.int32))
        d_cur_z = cl.Buffer(self.ctx, self.mf.READ_WRITE | self.mf.COPY_HOST_PTR, hostbuf = cur_z.astype(np.int32))
        d_z_by_y = cl.Buffer(self.ctx, self.mf.READ_ONLY | self.mf.COPY_HOST_PTR, 
                             hostbuf = np.dot(cur_z, cur_y).astype(np.int32))
        d_z_col_sum = cl.Buffer(self.ctx, self.mf.READ_WRITE | self.mf.COPY_HOST_PTR, 
                                hostbuf = cur_z.sum(axis = 0).astype(np.int32))
        d_rand = cl.Buffer(self.ctx, self.mf.READ_ONLY | self.mf.COPY_HOST_PTR, 
                           hostbuf=np.random.random(size = cur_z.shape).astype(np.float32))

        # calculate the prior probability that a pixel is on
        self.prg.sample_z(self.queue, cur_z.shape, None,
                          d_cur_y, d_cur_z, d_z_by_y, d_z_col_sum, d_obs,
                          d_rand, #d_z_on_loglik.data, d_z_off_loglik.data,
                          np.int32(self.obs.shape[0]), np.int32(self.obs.shape[1]), np.int32(cur_z.shape[1]),
                          np.float32(self.lam), np.float32(self.epislon), np.float32(self.theta))

        cl.enqueue_copy(self.queue, cur_z, d_cur_z)
        return cur_z
        
    def _cl_infer_k_new(self, cur_y, cur_z):

        # sample new features use importance sampling
        k_new = self._sample_k_new(cur_y, cur_z)
        if k_new:
            cur_y, cur_z = k_new

        # delete null features
        inactive_feat_col = np.where(cur_z.sum(axis = 0) == 0)
        cur_z_new = np.delete(cur_z, inactive_feat_col[0], axis=1).astype(np.int32)
        cur_y_new = np.delete(cur_y, inactive_feat_col[0], axis=0).astype(np.int32)

        z_new_s0, z_new_s1 = cur_z_new.shape
        cur_z_new = cur_z_new.reshape((z_new_s0 * z_new_s1, 1))
        cur_z_new = cur_z_new.reshape((z_new_s0, z_new_s1))

        y_new_s0, y_new_s1 = cur_y_new.shape
        cur_y_new = cur_y_new.reshape((y_new_s0 * y_new_s1, 1))
        cur_y_new = cur_y_new.reshape((y_new_s0, y_new_s1))

        # update self.k
        self.k = cur_z_new.shape[1]
        
        return cur_y_new, cur_z_new

