import logging
import threading
import time
from typing import Dict, List, Tuple, Any, Optional, Callable
from metrics import METRIC_TYPE_COUNTER, METRIC_TYPE_GAUGE, MetricsPage
from rdma.helpers import MRMetadata, OneSidedReader, QueuePairPool
from prometheus_client.core import REGISTRY, CounterMetricFamily, GaugeMetricFamily
from defaults import DEFAULT_POLL_INTERVAL

class LMAP:
    """
    Local Metrics Processing Pipeline (LMAP).
    Each LMAP instance is responsible for collecting metrics from a subset of memory regions.
    Processing them with anomaly detector
    Ensuring compatibility with Prometheus format.
    """
    
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
        self.logger = logging.getLogger(f'MicroviewNIC.LMAP.{collector_id}')
    

    def _local_metrics_reading(self):
        """
        Read metrics from assigned memory regions using RDMA
        """
        # Execute one RDMA read operation from all active memory regions
        results = self.rdma.execute()
        metrics: Dict[str, List[Any]] = {}

        # Loop over memory regions
        for i in range(len(results)):
            # This is now one MR
            data_region = results[i]
            # This is the corresponding control information
            control_region = self.control_info[i]
            
            # Loop over pages in the memory region
            for j in range(len(control_region)):
                pod_id = control_region[j]["pod_id"]
                page_occupancy = control_region[j]["pages"]
                page_size_bytes = control_region[j]["page_size_bytes"]
                
                # Read page data
                page_bytes = data_region[j*page_size_bytes:(j+1)*page_size_bytes]   
                
                self.logger.debug(f"📊 LMAP {self.collector_id} reading page {j} for pod {pod_id} with occupancy {page_occupancy}")
                
                # Parse metrics from page
                mp = MetricsPage.from_bytes(page_bytes, page_occupancy)
                m = mp.get_metrics()
                
                self.logger.debug(f"Metrics for pod {pod_id}: {m}")
                
                # Add metrics to collection
                metrics.setdefault(pod_id, []).extend(m)

        return metrics
    
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
            raw_metrics = self._local_metrics_reading()
            
            # Loop over pods
            for pod_id, raw in raw_metrics.items():
                # Loop over metrics
                for metric_tuple in raw:
                    # Extract metric name, type, and value
                    metric_name, metric_type, value = metric_tuple
                    
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
            self.logger.error(f"Error collecting metrics in LMAP {self.collector_id}: {e}")
        
        return prom_metrics
    

    def start_local_scrape_loop(self):
        """
        Start a dedicated thread for local metrics reading
        """
        def scrape_loop():
            self.logger.info(f"LMAP {self.collector_id} entering local scrape loop with interval={self.scrape_interval}s")
            while self.running:
                try:
                    # Read metrics
                    metrics = self._local_metrics_reading()
                    
                    # Update cache with thread safety
                    # with self.metrics_lock:
                    #     self.metrics_cache = metrics
                    
                    # Wait for next iteration
                    time.sleep(self.scrape_interval)
                    
                except Exception as e:
                    self.logger.error(f"Error in LMAP {self.collector_id} scrape loop: {e}")
                    time.sleep(1)  # Short delay before retrying
        
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


    def cleanup(self):
        """Clean up resources"""
        self.stop_local_scrape_loop()
        if self.rdma:
            self.rdma.cleanup()
        self.logger.info(f"LMAP {self.collector_id} cleaned up")

    
    def __del__(self):
        """Destructor to ensure cleanup"""
        self.cleanup()