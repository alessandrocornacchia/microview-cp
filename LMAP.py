import copy
import logging
import threading
import time
from typing import Dict, List, Tuple, Any, Optional, Callable

import numpy as np
from metrics import METRIC_TYPE_COUNTER, METRIC_TYPE_GAUGE, MetricsPage
from rdma.helpers import MRMetadata, OneSidedReader, QueuePairPool
from classifiers.enums import Models
from classifiers.classifiers import ModelBuilder, SubspaceAnomalyDetector
from prometheus_client.core import REGISTRY, CounterMetricFamily, GaugeMetricFamily
from defaults import DEFAULT_POLL_INTERVAL

class LMAP:
    """
    Local Metrics Processing Pipeline (LMAP).
    Each LMAP instance is responsible for collecting metrics from a subset of memory regions.
    Processing them with anomaly detector
    Ensuring compatibility with Prometheus format.
    """
    
    models = {
        Models.FREQUENT_DIRECTION: SubspaceAnomalyDetector,
        Models.VAE: None  # Placeholder for VAE model, will have its own builder
    }
    
    def __init__(self, collector_id: str,
                 control_info: List[List[Dict]],
                 rdma: OneSidedReader,
                 scrape_interval: int = DEFAULT_POLL_INTERVAL):
        """
        Initialize the LMAP collector
        
        Args:
            collector_id: Identifier for this collector
            control_info: Control info for all memory regions
            rdma: RDMA object for communication
        """
        self.collector_id = collector_id
        self.control_info = control_info
        self.rdma = rdma
        self.scrape_interval = scrape_interval
        self.running = False
        self.thread = None
        self.logger = logging.getLogger(f'MicroviewNIC.{collector_id}')
        self.statistics = {}
        self.classifiers: Dict[str, Any] = {}
        
        

    def set_classifier(self, model: str, **kwargs: Any):
        """
        Set the classifier for all pods
        """
        
        kwargs["model"] = model
        for mr in self.control_info:
            for ci in mr:
                pod_id = ci["pod_id"]
                kwargs["num_metrics"] = ci["num_metrics"]
                try:
                    c = LMAP.models[model].build(**kwargs)
                    self.classifiers[pod_id] = c
                except KeyError:
                    raise ValueError(f"Unknown model type: {model}")
                except Exception as e:
                    self.logger.error(f"Error building classifier for pod {pod_id}: {e}")
                    raise e
        

    def _read_metrics(self) -> Dict[str, List[MetricsPage]]:
        """
        Read metrics from assigned memory regions using RDMA
        """
        # Execute one RDMA read operation from all active memory regions
        results = self.rdma.execute()
        metrics: Dict[str, List[MetricsPage]] = {}

        # Loop over memory regions
        for i in range(len(results)):
            # This is now one MR
            data_region = results[i]
            # This is the corresponding control information
            control_region = self.control_info[i]
            
            # Loop over pages in the memory region
            for j in range(len(control_region)):
                pod_id = control_region[j]["pod_id"]
                page_occupancy = control_region[j]["num_metrics"]
                page_size_bytes = control_region[j]["page_size_bytes"]
                
                # Read page data
                raw_page = data_region[j*page_size_bytes:(j+1)*page_size_bytes]   
                
                self.logger.debug(f"📊 LMAP {self.collector_id} reading page {j} for pod {pod_id} with occupancy {page_occupancy}")
                
                # Parse metrics from page
                mp = MetricsPage.from_bytes(raw_page, page_occupancy)
                
                # Add metrics to collection
                metrics.setdefault(pod_id, []).append(mp)

        return metrics
    

    def _read_metric_values(self) -> Dict[str, np.ndarray]:
        """
        Read metrics and return only values, should avoid some Python loops
        """
        # Execute one RDMA read operation from all active memory regions
        results = self.rdma.execute()
        metrics: Dict[str, MetricsPage] = {}

        # Loop over memory regions
        for i in range(len(results)):
            # This is now one MR
            data_region = results[i]
            # This is the corresponding control information
            control_region = self.control_info[i]
            
            # Loop over pages in the memory region
            for j in range(len(control_region)):
                pod_id = control_region[j]["pod_id"]
                page_occupancy = control_region[j]["num_metrics"]
                page_size_bytes = control_region[j]["page_size_bytes"]
                
                # Read page data
                raw_page = data_region[j*page_size_bytes:(j+1)*page_size_bytes]   
                
                self.logger.debug(f"📊 LMAP {self.collector_id} reading page {j} for pod {pod_id} with occupancy {page_occupancy}")
                
                # Parse values from page
                values = MetricsPage.from_bytes(raw_page, page_occupancy).get_metrics_values()
                
                # Add metrics to collection
                metrics.setdefault(pod_id, []).append(values)

        # Perform concatenation once per pod rather than repeatedly
        values: Dict[str, np.ndarray] = {}
        for pod_id in metrics:
            if len(metrics[pod_id]) > 1:
                values[pod_id] = np.concatenate(metrics[pod_id])
            else:
                values[pod_id] = metrics[pod_id][0]

        return values
    
    
    def collect(self):
        """
        Collect metrics for Prometheus
        
        Returns:
            List of Prometheus metrics
        """
        prom_metrics = []
        
        try:
            # Read metrics using RDMA
            self.logger.debug(f"🔭 LMAP {self.collector_id} received scrape request, starting metrics reading")
            metrics_pages = self._read_metrics()
            
            # Loop over pods
            for pod_id, pages in metrics_pages.items():
                # Loop over pages
                for p in pages:
                    # Loop over metrics
                    # Get the three arrays from get_metrics()
                    names, types, values = p.get_metrics()
                    for metric_name, metric_type, value in zip(names, types, values):
                        
                        # Process raw metrics into Prometheus format
                        if metric_type == METRIC_TYPE_COUNTER:
                            metric = CounterMetricFamily(
                                name=f"{self.collector_id}_{metric_name}",  # TODO dirty trick to have same registry export same metric name
                                documentation=f"Counter metric {metric_name}",
                                labels=['pod_id', 'collector_id']
                            )
                        elif metric_type == METRIC_TYPE_GAUGE:
                            metric = GaugeMetricFamily(
                                name=f"{self.collector_id}_{metric_name}", # TODO dirty trick to have same registry export same metric name
                                documentation=f"Gauge metric {metric_name}",
                                labels=['pod_id', 'collector_id']
                            )
                        
                        # Add pod_id and collector_id as labels
                        metric.add_metric([pod_id, self.collector_id], value)
                        prom_metrics.append(metric)
                
        except Exception as e:
            self.logger.error(f"⛔️ Error collecting metrics in LMAP {self.collector_id}: {e}")
        
        return prom_metrics
    

    def start_local_scrape_loop(self):
        """
        Start a dedicated thread for local metrics reading
        """
        def scrape_loop():
            self.logger.info(f"LMAP {self.collector_id} entering local scrape loop with interval={self.scrape_interval}s")
            num_scrapes = 0
            self.statistics["num_scrapes"] = num_scrapes
            while self.running:
                try:
                    # Read metrics
                    metrics = self._read_metric_values()
                    self.logger.debug(f"📊 LMAP {self.collector_id} read metrics: {metrics}"
                                      )
                    # Assign metrics to correct classifier
                    for pod_id, pod_metrics in metrics.items():
                        
                        # Run the classifier if present
                        c = self.classifiers.get(pod_id)
                        
                        if c:
                            c.classify(pod_metrics)
                
                    num_scrapes += 1
                    # Wait for next iteration
                    time.sleep(self.scrape_interval)
                
                except Exception as e:
                    self.logger.error(f"Error in LMAP {self.collector_id} scrape loop: {e}")
                    raise e
                
            self.statistics["num_scrapes"] = num_scrapes
        
        self.running = True
        self.thread = threading.Thread(target=scrape_loop, name=f"LMAP-{self.collector_id}")
        self.thread.daemon = True  # Thread will exit when main program exits
        self.thread.start()
        self.logger.info(f"LMAP {self.collector_id} scrape thread started")


    def stop_local_scrape_loop(self):
        """Stop the local scrape loop thread"""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
            self.logger.info(f"LMAP {self.collector_id} scrape thread stopped")


    def dump_statistics(self, filename: Optional[str] = None):
        """Dump statistics to logger"""
        self.logger.info(f"LMAP {self.collector_id} statistics: {self.statistics}")
        
        for key, value in self.statistics.items():
            if isinstance(value, list):
                self.statistics["Average" + key] = np.mean(value)
                self.statistics["Max" + key] = np.max(value)
                self.statistics["Min" + key] = np.min(value)
                self.statistics["Std" + key] = np.std(value)
                del self.statistics[key]


        if filename:
            # write to csv
            with open(filename, 'w') as f:
                for key, value in self.statistics.items():
                    f.write(f"{key},{value}\n")
        
        self.logger.info("=========== Statistics ==============")
        self.logger.info("Key\tValue")
        self.logger.info("=====================================")
        for key, value in self.statistics.items():
            self.logger.info(f"{key}\t{value}")
        self.logger.info("=====================================")


    def cleanup(self):
        """Clean up resources"""
        self.stop_local_scrape_loop()
        self.dump_statistics("stats.csv")
        if self.rdma:
            self.rdma.cleanup()
        self.logger.info(f"LMAP {self.collector_id} cleaned up")

    
    def __del__(self):
        """Destructor to ensure cleanup"""
        self.cleanup()