from math import sqrt
from typing import Any
import numpy as np
from numpy.linalg import svd, norm
import random

from classifiers.fdsketch.frequentDirections import FastFrequentDirections, FrequentDirections
from classifiers.fdsketch.utils.syntheticDataMaker import SyntheticDataMaker

''' update storing entire history '''
class GlobalUpdate:
    def __init__(self) -> None:
        self.Nt = []

    def append(self, y):
        self.Nt.append(y)

    # return updated set of first k-left singular values
    def get_reconstruction_basis(self):
        [_,_,Vt] = svd(self.Nt, full_matrices=False)
        return Vt



class ModelBuilder:
    def build(self, num_metrics, **kwargs):
        raise NotImplementedError("ModelBuilder is an abstract class")
    

""" Anomaly detector using subspace analysis (sketch) """
class SubspaceAnomalyDetector(ModelBuilder):

    def __init__(self, k, sketch=GlobalUpdate, bootstrap = False, **kwargs) -> None:
        #self.Utk = Uo        # matrix of top-k left-singular vectors
        self.k = k
        self.model = sketch(**kwargs)
        self.Utk = None
        self.UtkT = None

        # Initialize with synthetic data if requested
        if bootstrap:
            n = kwargs.get('n', 10)       # number of samples for the random initialization, if not provided
            d = kwargs.get('d', 10)       # feature size

            self._bootstrap(n, d)
                
    @staticmethod
    def build(**kwargs):
        """ This is a builder method for the SubspaceAnomalyDetector tpyes of deetctors """
        
        num_metrics = kwargs.get('num_metrics')  # retrieve num_metrics from kwargs
        if num_metrics is None:
            raise ValueError("num_metrics must be provided")
        
        # compute default values according to guidelines
        kwargs['d'] = num_metrics  # store d in kwargs for later use
        # sketch size (ell) is the number of rows in the sketch matrix
        kwargs['ell'] = kwargs.get('ell', int(sqrt(num_metrics)))  # store ell in kwargs for later use
        # number of principal components that will be used in anomaly detection to reconstruct a sample
        kwargs['k'] = kwargs.get('k', kwargs['ell'])  # store k in kwargs for later use
        if kwargs['k'] > kwargs['ell']:
            raise ValueError("k cannot be greater than ell")
        
        print(kwargs['d'], kwargs['ell'], kwargs['k'], kwargs['model'])

        model = kwargs.get('model', "FD")  # default model for storing history
        if model == "FD":
            sketch = FrequentDirections
        elif model == "FFD":
            sketch = FastFrequentDirections
        else: 
            sketch = GlobalUpdate

        return SubspaceAnomalyDetector(sketch=sketch, **kwargs)
    

    def _bootstrap(self, n, d):
        # Generate synthetic data to initialize the model with a subspace representation
        dataMaker = SyntheticDataMaker()
        dataMaker.initBeforeMake(
            d,
            signal_dimension=self.k,
            signal_to_noise_ratio=10.0
        )
        
        # Generate training data and fit the model
        train_data = dataMaker.makeMatrix(n)
        self.fit(train_data)
                
    '''
        Classify input vector y either as anomalous or non-anomalous
            y : input column vector
            th : classification threshold, anomaly when above. 
            Deafult values are 0, which means never update classifier. This is
            useful to get the score distribution of the training set, without 
            modifying the classifier. 
    '''
    def classify(self, y, th=0, eta=0):
        # normalize input vector
        norm_ = norm(y)
        yn = y/norm_ if norm_ else y
        # optimal least-squares solution
        xi = yn @ self.Utk 
        # get anomaly score (objective value at the solution)
        score_vec = yn - xi @ self.UtkT
        score = norm(score_vec)
        # update left-singular values (use only non-anomalous data)
        if score <= th or random.random() < eta:
            self.__update__(yn)        
        # binary classification
        return (score > th, score, score_vec)

    ''' Update model with new sample '''
    def __update__(self, yn):
        self.model.append(yn)
        Vt = self.model.get_reconstruction_basis()
        # take first k rows (store both for performance)
        self.Utk = Vt[:self.k, :].T
        self.UtkT = Vt[:self.k, :]

    ''' Derive initial reconstruction basis Uk from a set of non-anomalous samples '''
    def fit(self, Y): 
        # obtain initial matrix of singular values, expects numpy array
        for y in Y:
            #y = row.to_numpy()
            norm_ = norm(y)
            yn = y/norm_ if norm_ else y
            self.__update__(yn)



if __name__ == "__main__":
    
    k = 30   # number of principal components that will be used in anomaly detection to reconstruct a sample
    m = 2000  # feature size
    n = 1000

    samples = np.random.randn(n, m)

    # Simulate a new sample for testing
    import time
    
    # here we demonstarte use of builder
    detector = SubspaceAnomalyDetector.build(model="FD", num_metrics=m, k=k, bootstrap=True)

    #---- FD sketch: ell is the sketch size, in this case we do not care about model quality, just set sketch size equal to k
    # detector = SubspaceAnomalyDetector(bootstrap=True, sketch=FrequentDirections, d=m, k=k, ell=k)
    
    start_t  = time.time()
    for i in range(n):
        is_anomalous, score, score_vector = detector.classify(samples[i], th=0.5)
    end_t = time.time()

    print(f"{detector.model} 100 vectors: {end_t - start_t} seconds")

    detector = SubspaceAnomalyDetector(bootstrap=True, sketch=FastFrequentDirections, d=m, k=k, ell=k)
    
    start_t  = time.time()
    for i in range(n):
        is_anomalous, score, score_vector = detector.classify(samples[i], th=0.5)
    end_t = time.time()

    # TODO the speedup is not visible here because with "classify" we update the model only for non-anomalous samples, not continuously..
    print(f"{detector.model} 100 vectors: {end_t - start_t} seconds")
