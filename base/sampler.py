#!/usr/bin/env python2
#-*- coding: utf-8 -*-

from __future__ import print_function
import numpy as np
import sys, copy, random, math, csv, gzip, mimetypes, os.path
from time import time

def smallest_unused_label(int_labels):
    
    if len(int_labels) == 0: return [], [], 0
    label_count = np.bincount(int_labels)
    try: 
        new_label = np.where(label_count == 0)[0][0]
    except IndexError: 
        new_label = max(int_labels) + 1
    uniq_labels = np.unique(int_labels)
    return label_count, uniq_labels, new_label

def lognormalize(x):
    # adapt it to numpypy
    x = x - np.max(x)
    xp = np.exp(x)
    return xp / xp.sum()

def sample(a, p):
    """Step sample from a discrete distribution using CDF
    """
    n = len(a)
    r = random.random()
    total = 0           # range: [0,1]
    for i in xrange(n):
        total += p[i]
        if total > r:
            return a[i]
    return a[i]

def print_matrix_in_row(npmat, file_dest):
    """Print a matrix in a row.
    """
    row, col = npmat.shape
    print(col, *npmat.reshape((1, row * col))[0], sep=',', file=file_dest)
    return True

class BaseSampler(object):

    def __init__(self, record_best, cl_mode, cl_device = None):
        """Initialize the class.
        """
        if cl_mode:
            import pyopencl as cl
            import pyopencl.array, pyopencl.tools, pyopencl.clrandom
            
            if cl_device == 'gpu':
                gpu_devices = []
                for platform in cl.get_platforms():
                    try: gpu_devices += platform.get_devices(device_type=cl.device_type.GPU)
                    except: pass
                self.ctx = cl.Context(gpu_devices)
            elif cl_device == 'cpu':
                cpu_devices = []
                for platform in cl.get_platforms():
                    try: cpu_devices += platform.get_devices(device_type=cl.device_type.CPU)
                    except: pass
                self.ctx = cl.Context([cpu_devices[0]])
            else:
                self.ctx = cl.create_some_context()

            self.queue = cl.CommandQueue(self.ctx)
            self.mem_pool = cl.tools.MemoryPool(cl.tools.ImmediateAllocator(self.queue)) 
            self.mf = cl.mem_flags
            self.device = self.ctx.get_info(cl.context_info.DEVICES)[0]
            self.device_type = self.device.type
            self.device_compute_units = self.device.max_compute_units
            
        self.cl_mode = cl_mode
        self.obs = []
        self.niter = 1000
        self.thining = 1
        self.burnin = 0
        self.N = 0 # number of data points
        self.best_sample = (None, None) # (sample, loglikelihood)
        self.record_best = record_best
        self.best_diff = []
        self.no_improv = 0
        self.gpu_time = 0
        self.total_time = 0
        
    def read_csv(self, filepath, header = True):
        """Read data from a csv file.
        """
        # determine if the type file is gzip
        filetype, encoding = mimetypes.guess_type(filepath)
        if encoding == 'gzip':
            csvfile = gzip.open(filepath, 'r')
        else:
            csvfile = open(filepath, 'r')

        #dialect = csv.Sniffer().sniff(csvfile.read(1024))
        csvfile.seek(0)
        reader = csv.reader(csvfile)#, dialect)
        if header:
            reader.next()
        for row in reader:
            self.obs.append([_ for _ in row])
            
        self.N = len(self.obs)
        return

    def direct_read_obs(self, obs):
        self.obs = obs

    def set_sampling_params(self, niter = 1000, thining = 1, burnin = 0):
        self.niter, self.thining, self.burnin = niter, thining, burnin

    def do_inference(self, output_file = None):
        """Perform inference. This method does nothing in the base class.
        """
        return

    def auto_save_sample(self, sample):
        """Save the given sample as the best sample if it yields
        a larger log-likelihood of data than the current best.
        """
        new_logprob = self._logprob(sample)
        # if there's no best sample recorded yet
        if self.best_sample[0] is None and self.best_sample[1] is None:
            self.best_sample = (sample, new_logprob)
            print('Initial sample generated, loglik: {0}'.format(new_logprob), file=sys.stderr)
            return

        # if there's a best sample
        if new_logprob > self.best_sample[1]:
            self.no_improv = 0
            self.best_diff.append(new_logprob - self.best_sample[1])
            self.best_sample = (copy.deepcopy(sample), new_logprob)
            print('New best sample found, loglik: {0}'.format(new_logprob), file=sys.stderr)
            return True
        else:
            self.no_improv += 1
            return False

    def no_improvement(self, threshold=500):
        if len(self.best_diff) == 0: return False
        if self.no_improv > threshold or np.mean(self.best_diff[-threshold:]) < 1:
            print('Too little improvement in loglikelihood - Abort searching', file=sys.stderr)
            return True
        return False
        
    def _logprob(self, sample):
        """Compute the logliklihood of data given a sample. This method
        does nothing in the base class.
        """
        return
