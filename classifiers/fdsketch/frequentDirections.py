from __future__ import absolute_import
from numpy import zeros, max, sqrt, isnan, isinf, dot
from numpy import diag, count_nonzero, shape, any, abs, maximum
from numpy.linalg import svd, linalg
from scipy.linalg import svd as scipy_svd
from scipy.sparse.linalg import svds as scipy_svds
from classifiers.fdsketch.matrixSketcherBase import MatrixSketcherBase
import sys
import argparse


class FrequentDirections(MatrixSketcherBase):
    def __init__(self, d, ell, **kwargs):
        self.class_name = "FrequentDirections"
        self.d = d
        self.ell = ell
        self._sketch = zeros((self.ell, self.d))
        #self.Vt = zeros((min(self,self.d), self.d))
        self.idx = 0

    def append(self, vector):
        # last row stores the input vector
        if self.idx < self.ell:
            self._sketch[self.idx, :] = vector    
            self.idx += 1
        else:
            self._sketch[self.ell-1, :] = vector

        # decomposition
        try:
            [_, s, Vt] = svd(self._sketch, full_matrices=False)
        except:
            print(vector)
            raise ValueError()
        # shrunking all by same amount (this guarantees last row is zero)
        sShrunk = sqrt(maximum(s ** 2 - s[self.ell-1] ** 2, 0))
        
        # update new sketch and basis for reconstruction
        self._sketch = diag(sShrunk) @ Vt
        self.Vt = Vt

    def get_reconstruction_basis(self):
        return self.Vt


class FastFrequentDirections(MatrixSketcherBase):
    def __init__(self, d, ell, **kwargs):
        self.class_name = "FastFrequentDirections"
        self.d = d
        self.ell = ell
        self.m = 2 * self.ell
        self._sketch = zeros((self.m, self.d))
        self.nextZeroRow = 0
        self.Vt = zeros((self.d, self.d))

    def append(self, vector):
        if count_nonzero(vector) == 0:
            return

        if self.nextZeroRow >= self.m:
            self.__rotate__()

        self._sketch[self.nextZeroRow, :] = vector
        self.nextZeroRow += 1

    def __rotate__(self):
        try:
            [_, s, Vt] = svd(self._sketch, full_matrices=False)
        except linalg.LinAlgError as err:
            [_, s, Vt] = scipy_svd(self._sketch, full_matrices=False)
        # [_,s,Vt] = scipy_svds(self._sketch, k = self.ell)
        self.Vt = Vt

        if len(s) >= self.ell:
            sShrunk = sqrt(maximum(s ** 2 - s[self.ell - 1] ** 2, 0))
            self._sketch[: self.ell :, :] = dot(diag(sShrunk[:self.ell]), Vt[:self.ell, :])
            self._sketch[self.ell :, :] = 0
            self.nextZeroRow = self.ell
        else:
            self._sketch[: len(s), :] = dot(diag(s), Vt[: len(s), :])
            self._sketch[len(s) :, :] = 0
            self.nextZeroRow = len(s)

    def get(self):
        return self._sketch[: self.ell, :]

    def get_reconstruction_basis(self):
        return self.Vt

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', type=int, required=True, help='dimension of row vectors (number of columns in matrix).')
    parser.add_argument('-ell', type=int, required=True, help='the number of rows the sketch can keep.')
    parser.add_argument('-v', action='store_true', help='print sketch update step by step.')

    args = parser.parse_args()
    
    fd = FastFrequentDirections(args.d, args.ell)
    for line in sys.stdin:
        try:
            row = [float(s) for s in line.strip('\n\r').split(',')]
            assert(len(row) == args.d)
        except:
            continue
        fd.append(row)
        if args.v:
            print(fd)

    print(fd)
        
    