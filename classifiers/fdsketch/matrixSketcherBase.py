from __future__ import absolute_import
from numpy import zeros


class MatrixSketcherBase:
    def __init__(self, d, ell):
        self.d = d
        self.ell = ell
        self._sketch = zeros((self.ell, self.d))

    # Appending a row vector to sketch
    def append(self, vector):
        pass

    # Convenient looping numpy matrices row by row
    def extend(self, vectors):
        for vector in vectors:
            self.append(vector)

    # returns the sketch matrix
    def get(self):
        return self._sketch

    # Convenience support for the += operator  append
    def __iadd__(self, vector):
        self.append(vector)
        return self

    # Convenience support for printing sketch
    def show(self):
        s = ''
        for row in self._sketch:    
            s += '%s\n' % (','.join('%.2E'%x for x in row.flatten()))
        return s
    
    def __str__(self):
        return f"({self.class_name}, d={self.d}, ell={self.ell})"
    