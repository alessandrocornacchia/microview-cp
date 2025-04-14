# Prometheus Monitoring Overhead Experiment

This experiment evaluates the overhead of different scraping intervals in user applications monitored by Prometheus. It measures how different scraping frequencies impact system resource usage when collecting metrics.

## Prerequisites

- Docker and Docker Compose
- Python with Conda environment
- Prometheus Python client

## Setup

1. Activate the conda environment:
   ```bash
   conda activate your-environment-name
   ```

2. Make sure you're in the experiment directory:
   ```bash
   cd /home/cornaca/microview-cp/app/prometheus-app
   ```

## Running the Experiment

Execute the experiment by running:

```bash
python ./run.py
```

This will:
1. Start containers for Prometheus, cAdvisor, and the test application
2. Configure scraping intervals based on the settings in run.py
3. Run the experiment for the specified duration (default 5 minutes)
4. Collect resource usage data and save it to CSV files in the data/ directory

## How It Works

The experiment:
- Deploys a Prometheus server, cAdvisor (for container metrics), and a test application
- Varies the number of metrics and scraping intervals based on configuration
- Collects system resource metrics including:
  - CPU system time
  - CPU user time
  - CPU total usage
  - Network transmit bytes
- Uses Prometheus rate() function to calculate resource consumption rates
- Saves results to CSV files for further analysis

## Configuration

You can modify these parameters in run.py:
- `app_scraping_intervals`: Different scraping frequencies to test
- `num_metrics`: Number of metrics the application will generate
- `experiment_duration`: How long to run each experiment iteration
- `update_metrics`: Whether metrics should be updated during the experiment

## Data Analysis

After running the experiment, check the 'data' directory for CSV files containing the collected metrics. Each file is named according to the number of metrics being monitored.

## Stopping the Experiment

The experiment can be interrupted at any time with Ctrl+C. It will automatically clean up all containers when finished or interrupted.