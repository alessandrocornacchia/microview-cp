from prometheus_client import start_http_server, Counter, Gauge
import random
import time
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

def create_metrics(num_metrics):
    """ register metrics with prometheus client """
    counters = []
    gauges = []
    
    # Create different types of metrics
    for i in range(num_metrics):
        # Create a counter metric
        counter = Counter(f'sample_counter_{i}', f'Example counter metric #{i}')
        counter.inc(random.randint(1, 100))  # Initialize with random value
        counters.append(counter)
        
        # Create a gauge metric
        gauge = Gauge(f'sample_gauge_{i}', f'Example gauge metric #{i}')
        gauge.set(random.random() * 100)  # Initialize with random value
        gauges.append(gauge)
    
    return counters, gauges


def workload(counters, gauges, update=False):
    """ Mock some workload and update metrics """
    
    while True:
        
        if update:
            for c in counters:
                c.inc(random.random())
            for g in gauges:
                g.set(g._value.get() + random.uniform(-5, 5))

        # just sleep for a while
        time.sleep(10)


if __name__ == '__main__':
    
    import argparse
    parser = argparse.ArgumentParser(description="MicroView Client Example")
    parser.add_argument("--num-metrics", "-m", type=int, default=2, help="Number of metrics to create")
    parser.add_argument("--update-metrics", action="store_true", help="Update metrics every 10 seconds")
    args = parser.parse_args()

    # Start up the Prometheus server to expose the metrics.
    start_http_server(8000)
    logging.info("Prometheus metrics available at http://localhost:8000/metrics")
    
    # Calculate number of metrics (each type will have this many metrics)
    num_metrics = max(1, int(args.num_metrics/2))
    
    counters, gauges = create_metrics(num_metrics)
    logging.info(f"Created {num_metrics} counters and {num_metrics} gauges.")
    
    # Keep the main thread running with a mock workload
    try:
        workload(counters, gauges, args.update_metrics)
    except KeyboardInterrupt:
        logging.info("Exiting...")