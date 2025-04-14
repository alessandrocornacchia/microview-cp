""" Run experiments """

#%%
import yaml
import os
import argparse
import time
from prometheus_api_client.utils import parse_datetime, parse_timedelta
from datetime import timedelta
from prometheus_api_client import PrometheusConnect
from pytimeparse.timeparse import timeparse
import itertools

script_dir = os.path.dirname(os.path.realpath(__file__))

argparse = argparse.ArgumentParser(description="Run experiments")
argparse.add_argument("--app", "-a", type=str, help="Which app to run, choices are 'uview' or 'prometheus'", required=True)

args = argparse.parse_args()

# globals
docker_folder = args.app + '-app/docker'
prometheus_file = f"{docker_folder}/prometheus.yml"
housekeeping_interval = '1s'
cadvisor_scarping_interval = '5s'
app_scraping_intervals = ['30s'] #['1s', '10s', '30s', '1m']
num_metrics = [1] #[1, 10, 100]
update_metrics = False
experiment_duration = '2m'

monitor = {
    "cpu_sys": 'container_cpu_system_seconds_total', 
    "cpu_user": 'container_cpu_user_seconds_total', 
    "cpu": 'container_cpu_usage_seconds_total',
    "net": 'container_network_transmit_bytes_total'
}



# these can have more complex logic for kube, docker, etc.
def start_containers_cmd(sudo=False):
    cmd = f"cd {docker_folder} && docker compose up -d"
    if sudo:
        cmd = "sudo " + cmd
    return cmd

def stop_containers_cmd(sudo=False):
    cmd = f"cd {docker_folder}&& docker compose down"
    if sudo:
        cmd = "sudo " + cmd
    return cmd


def tear_up():

    # Debug line to confirm environment variables are visible
    os.system('echo "HOUSEKEEPING_INTERVAL=$HOUSEKEEPING_INTERVAL NUM_METRICS=$NUM_METRICS"')   
    
    # start monitoring containers with force-recreate option
    r1 = os.system(start_containers_cmd() + " --force-recreate cadvisor prometheus")
    
    # start replicas containers (can keep old containers if already present
    # to speed-up. i.e. don't use --force-recreate)
    r2 = os.system(start_containers_cmd() + f" {args.app}-app")
    return r1+r2



def tear_down():
    return os.system(stop_containers_cmd())



def set_scraping_interval(jobname, scrape_interval, filename= 'prometheus.yaml'):
    # customize promtheus config yaml
    with open(filename, 'r+') as stream:
        try:
            prometheus = yaml.safe_load(stream)
            for config in prometheus['scrape_configs']:
                if config['job_name'] == jobname:
                    config['scrape_interval'] = scrape_interval
                    break
        except yaml.YAMLError as exc:
            print("Cannot read from prometheus.yml")
            raise exc
    
    with open(filename, 'w') as stream:
        try:
            yaml.dump(prometheus, stream)
        except yaml.YAMLError as exc:
            print("Cannot write to prometheus.yml")
            raise exc



def deploy(generation, scraping, n):
    print(f"== Running with num_metrics={n}, housekeeping_interval={generation}, scraping_interval={scraping}")
    
    # Set environment variables programmatically
    os.environ["HOUSEKEEPING_INTERVAL"] = generation
    os.environ["NUM_METRICS"] = str(n)  # Example additional variable
    if update_metrics:
        os.environ["UPDATE_METRICS"] = "1" 
    
    # TODO if app is uview, need to start first the agent 
    
    # set cadvisor scraping interval
    set_scraping_interval('cadvisor', cadvisor_scarping_interval, prometheus_file)

    # set app scraping interval
    set_scraping_interval('app', scraping, prometheus_file)

    # TODO if app uview need to start here the NIC uview scraper
    
    return tear_up()
    



def get_resource_usage(h, s, c):
    try:
        prom = PrometheusConnect(url="http://localhost:9090", disable_ssl=True)
    except Exception as e:
        print("== Error connecting to prometheus")
        print(e)
        os.system(stop_containers_cmd(sudo=True))
        exit(1)

    start_time = parse_datetime(experiment_duration)
    end_time = parse_datetime("now")
    step = timeparse(s) # chunk size same as scraping rate
    window = str(4*timeparse(s)) + "s" # lookback window for rate computation, 4 scrape rate as recommended by prom

    print(f"== Experiment ended. Collecting data in the last {experiment_duration}. Step size: {s}")

    # collect cAdvisor resource usage data 
    measurments = dict()
    timestamps = None

    for metric, metric_query in monitor.items():

        # for each data point in the time series of the resource consumptions we collect, consider a lookback window of 4 scrapes
        # to compute the rate of change in the metric
        query = "rate(" + metric_query + "{container_label_com_docker_compose_service='cadvisor'}[" +  window  + "])"
        print(f"== Querying {query}")

        data = prom.custom_query_range(
            query,
            start_time=start_time,
            end_time=end_time,
            step=step
        )

        timestamps, v = zip(*data[0]['values'])
        measurments[metric] = v
        
    if not os.path.exists(f"{script_dir}/data"):
        os.makedirs(f"{script_dir}/data")

    # write to file
    with open(f"{script_dir}/data/{args.app}_{c}_metrics.csv", 'w') as f:
        f.write("time,{}\n".format(','.join(monitor.keys())))
        
        for i in range(len(timestamps)):
            f.write("{},{}\n".format(
                timestamps[i], 
                ','.join([str(measurments[m][i]) for m in monitor.keys()])))

    


#--- run the experiment for selected configurations
if __name__ == "__main__":


    try:
        # run experiments sequentially
        for nm in num_metrics:
            for s in app_scraping_intervals:
                
                r = deploy(housekeeping_interval, s, nm)

                if r != 0:
                    print("== Error starting docker compose, skipping this experiment")
                    os.system(stop_containers_cmd())
                    continue

                print(f"== Experiment started. Sleeping for {experiment_duration}")
                time.sleep(timeparse(experiment_duration))

                get_resource_usage(housekeeping_interval, cadvisor_scarping_interval, num_metrics)
    except KeyboardInterrupt:
        print("== Experiment interrupted by user")
    finally:
        print("== Stopping containers")
        # stop containers
        ret = tear_down()
        if ret != 0:
            print("== Error stopping docker compose")
                    
