import numpy as np
import requests
from multiprocessing import shared_memory
from typing import Dict, Optional, Union, Any
from metrics import metric_dtype

class MicroViewMetric:
    """
    A class representing a single metric in the MicroView system.
    Abstracts away the details of shared memory access.
    """
    def __init__(self, shm_name: str, index: int, metric_name: str, metric_type: bool):
        """
        Initialize a MicroViewMetric.
        
        Args:
            shm_name: Name of the shared memory segment
            index: Index of the metric within the shared memory
            metric_name: Name of the metric
            metric_type: Type of metric (False=counter, True=gauge)
        """
        self.shm_name = shm_name
        self.index = index
        self.metric_name = metric_name
        self.metric_type = metric_type
        
        # Open the shared memory
        self.shm = shared_memory.SharedMemory(name=shm_name)
        
        # Create a numpy array view of the shared memory
        # TODO: perhaps this can be avoided by having access to the raw pointer....
        # this is a bit of an overkill, in this client we don't want to know this
        self.array = np.ndarray((self._calculate_array_size(),), dtype=metric_dtype, buffer=self.shm.buf)
        
        # Validate that we're accessing the right metric
        stored_name = self.array[index]['name'].tobytes().decode('utf-8').rstrip('\x00')
        if stored_name != metric_name:
            self.shm.close()
            raise ValueError(f"Metric name mismatch: expected '{metric_name}', found '{stored_name}'")

    def _calculate_array_size(self) -> int:
        """Calculate the number of metrics that can fit in a page"""
        return 4096 // np.dtype(metric_dtype).itemsize
        
    def update_value(self, value: float) -> None:
        """
        Update the value of this metric in shared memory.
        
        Args:
            value: New value to set
        """
        self.array[self.index]['value'] = value
        
    def get_value(self) -> float:
        """
        Get the current value of this metric.
        
        Returns:
            The current value of the metric
        """
        return float(self.array[self.index]['value'])
    
    def close(self) -> None:
        """
        Close the shared memory access.
        """
        if hasattr(self, 'shm') and self.shm:
            self.shm.close()




class MicroViewClient:
    """
    Client library for creating and managing metrics in the MicroView system.
    """
    def __init__(self, microservice_id: str, host: str = "localhost", port: int = 5000):
        """
        Initialize the MicroView client.
        
        Args:
            microservice_id: Identifier for the microservice
            host: Host where the MicroViewHostAgent is running
            port: Port where the MicroViewHostAgent API is exposed
        """
        self.microservice_id = microservice_id
        self.base_url = f"http://{host}:{port}"
        self.metrics = {}  # Store created metrics for reference
        
    def create_metric(self, name: str, metric_type: bool = False, initial_value: float = 0.0) -> MicroViewMetric:
        """
        Create a new metric in the MicroView system.
        
        Args:
            name: Name of the metric
            metric_type: Type of metric (False=counter, True=gauge)
            initial_value: Initial value for the metric
            
        Returns:
            A MicroViewMetric object for updating the metric
        """
        # Check if metric already exists locally
        if name in self.metrics:
            return self.metrics[name]
            
        # Create the payload for the API request
        payload = {
            "microservice_id": self.microservice_id,
            "name": name,
            "type": metric_type,
            "value": initial_value
        }
        
        # Make the API request to create the metric
        try:
            response = requests.post(f"{self.base_url}/metrics", json=payload)
            response.raise_for_status()  # Raise exception for HTTP errors
            
            # Parse the response
            result = response.json()
            
            # Create a MicroViewMetric object
            metric = MicroViewMetric(
                shm_name=result["shm_name"],
                index=result["index"],
                metric_name=name,
                metric_type=metric_type
            )
            
            # Store the metric for reference
            self.metrics[name] = metric
            
            return metric
            
        except requests.RequestException as e:
            raise ConnectionError(f"Failed to create metric: {e}")
            
    def delete_metric(self, name: str) -> bool:
        """
        Delete a metric from the MicroView system.
        
        Args:
            name: Name of the metric to delete
            
        Returns:
            True if metric was deleted successfully, False otherwise
        """
        # Check if metric exists locally
        if name not in self.metrics:
            return False
            
        # Create the payload for the API request
        payload = {
            "microservice_id": self.microservice_id,
            "name": name
        }
        
        # Close the shared memory access
        self.metrics[name].close()
        
        # Make the API request to delete the metric
        try:
            response = requests.post(f"{self.base_url}/delete", json=payload)
            response.raise_for_status()
            
            # Remove the metric from local storage
            del self.metrics[name]
            
            return True
            
        except requests.RequestException as e:
            print(f"Failed to delete metric: {e}")
            return False
            
    def close(self) -> None:
        """
        Close all shared memory resources.
        """
        for metric in self.metrics.values():
            metric.close()
        self.metrics.clear()
        
    def __del__(self):
        """
        Clean up resources when the client is garbage collected.
        """
        self.close()


# Example usage
if __name__ == "__main__":
    # Create a MicroView client
    client = MicroViewClient("example-service")
    
    try:
        # Create a counter metric
        requests_metric = client.create_metric("http_requests_total", False, 0)
        
        # Create a gauge metric
        latency_metric = client.create_metric("http_request_latency", True, 0.0)
        
        # Update the metrics 10 times
        for i in range(10):
            requests_metric.update_value(i)
            latency_metric.update_value(i * 0.1)
            
            print(f"Requests: {requests_metric.get_value()}, Latency: {latency_metric.get_value()}")
            
            import time
            time.sleep(10)
            
    finally:
        # Clean up
        client.close()