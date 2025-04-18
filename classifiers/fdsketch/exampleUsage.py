from __future__ import absolute_import
import sys
from numpy.linalg import norm
from numpy import dot, cov
from time import sleep
from numpy import random

from utils.syntheticDataMaker import SyntheticDataMaker
from frequentDirections import FastFrequentDirections, FrequentDirections
from bruteForce import BruteForce

n = 500     # stream size
m = 10      # feature size (signal + noise ?)
ell = 4     # sketch size
k = 5       # signal dimension

# this is only needed for generating input vectors
dataMaker = SyntheticDataMaker()
dataMaker.initBeforeMake(
    m, 
    signal_dimension=k, 
    signal_to_noise_ratio=10.0
)

# This is where the sketching actually happens
ffd = FastFrequentDirections(m, ell)
fd = FrequentDirections(m, ell)
bf = BruteForce(m, ell)
A = dataMaker.makeMatrix(n)
for i in range(n):
    #row = dataMaker.makeRow()
    row = A[i,:]
    #print(row)
#    sleep(1)
    ffd.append(row)
    bf.append(row)
    fd.append(row)
ffd_sketch = ffd.get()
fd_sketch = fd.get()
bf_sketch = bf.get()

#print(fd_sketch)
#print(bf_sketch)

# Here is where you do something with the sketch.
# The sketch is an ell by d matrix
# For example, you can compute an approximate covariance of the input
# matrix like this:

# approxCovarianceMatrix = dot(sketch.transpose(), sketch)

# should be similar ...
ATA = A.T @ A #bugfix: not covariance
squared_frob_A = norm(A, "fro") ** 2

diff = ATA - dot(ffd_sketch.transpose(), ffd_sketch)
relative_cov_err = norm(diff, 2) / squared_frob_A
print('FFD sketch covariance error: ', relative_cov_err)

diff = ATA - dot(fd_sketch.transpose(), fd_sketch)
relative_cov_err = norm(diff, 2) / squared_frob_A
print('FD sketch covariance error: ', relative_cov_err)

diff = ATA - dot(bf_sketch.transpose(), bf_sketch)
relative_cov_err = norm(diff, 2) / squared_frob_A
print('BF sketch covariance error: ', relative_cov_err)