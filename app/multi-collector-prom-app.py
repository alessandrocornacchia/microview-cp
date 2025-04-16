""" demonstrates multiple collectors in a single Prometheus HTTP server """

import time
from prometheus_client import start_http_server, REGISTRY
from prometheus_client.core import CounterMetricFamily
from threading import Lock


class LMAPCollector1():
    def collect(self):
        # Metric logic for 1st LMAP thread
        return [CounterMetricFamily(
                                name="lmap_1",
                                documentation=f"Counter metric lmap_1",
                            )]

class LMAPCollector2():
    def collect(self):
        return [CounterMetricFamily(
                                name="lmap_2",
                                documentation=f"Counter metric lmap_2",
                            )]

# Single HTTP server with combined metrics
REGISTRY.register(LMAPCollector1())
REGISTRY.register(LMAPCollector2())

start_http_server(8000)
print("Prometheus metrics available at http://localhost:8000/metrics")

try:
    while True:
        time.sleep(10)
except KeyboardInterrupt:
    print("Exiting...")